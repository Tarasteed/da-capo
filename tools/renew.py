#!/usr/bin/env python3
"""Da capo: find free classical recordings on Wikimedia Commons and cut blind-test excerpts.

Two steps, both run by the weekly maintenance session:

  discover  List audio files from Wikimedia Commons (Musopen categories by default, or a
            search query) that are public domain or under a free licence, skipping those
            already in the catalogue. Writes candidates.json for review.

  fetch     Download the chosen recordings, pick a representative ~25 s passage
            automatically (recurring, well-lit material near the start of the movement),
            and write MP3 excerpts plus their technical metadata.

Requirements: pip install librosa imageio-ffmpeg requests lameenc numpy
Network: commons.wikimedia.org and upload.wikimedia.org.
"""
import argparse, html, json, os, re, subprocess, sys, tempfile, time, unicodedata, urllib.parse
import numpy as np

API = "https://commons.wikimedia.org/w/api.php"
UA = os.environ.get("DACAPO_UA", "DaCapoBlindTest/1.0 (https://github.com/Tarasteed/da-capo; classical music blind test, weekly)")
SR = 44100
EXCERPT = 25.0
DEFAULT_CATEGORIES = ["Category:Musopen", "Category:Musopen Kickstarter Project", "Category:Set Chopin Free"]
AUDIO_MIME = {"audio/ogg", "application/ogg", "audio/flac", "audio/x-flac", "audio/wav", "audio/x-wav",
              "audio/mpeg", "audio/webm", "audio/opus"}


# ------------------------------------------------------------------ helpers
def session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s

class Throttled(Exception):
    """A host keeps answering 429: stop asking it for the rest of this run."""

THROTTLE = {}

def polite_get(s, url, attempts=6, **kw):
    """GET that honours Retry-After. Shared outgoing addresses often get HTTP 429 from Wikimedia;
    after three refusals from the same host we give up on that host for this run."""
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(attempts):
        if THROTTLE.get(host, 0) >= 3:
            raise Throttled(f"{host} refuses downloads for now (HTTP 429)")
        r = s.get(url, timeout=120, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            if r.status_code == 429: THROTTLE[host] = THROTTLE.get(host, 0) + 1
            wait = int(r.headers.get("retry-after", "0") or 0) or (5 + attempt * 10)
            print(f"  waiting {min(wait + 2, 120)}s (HTTP {r.status_code} from {host})", file=sys.stderr)
            r.close(); time.sleep(min(wait + 2, 120)); continue
        r.raise_for_status()
        return r
    r.raise_for_status()

def api(s, **params):
    params.update(format="json", formatversion=2)
    r = polite_get(s, API, params=params)
    time.sleep(1.5)
    return r.json()

def strip_html(x):
    x = re.sub(r"<[^>]+>", " ", x or "")
    return re.sub(r"\s+", " ", html.unescape(x)).strip()

def slug(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\.(ogg|oga|flac|wav|mp3|opus|webm)$", "", t)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:60].rstrip("-")

def licence_ok(short, url):
    s = (short or "").lower()
    u = (url or "").lower()
    if "nc" in re.split(r"[\s\-_/]+", s) or "-nc" in u or "nd" in re.split(r"[\s\-_/]+", s) or "-nd" in u:
        return False
    return any(k in s for k in ("public domain", "pd", "cc0", "cc by", "cc-by")) or "publicdomain" in u


# ------------------------------------------------------------------ discover
IIPROPS = "url|size|mime|mediatype|extmetadata"
EXT = "LicenseShortName|LicenseUrl|Artist|Credit|ImageDescription|ObjectName|AttributionRequired|DateTimeOriginal"

def _collect(s, gen_params):
    out, cont = [], {}
    while True:
        data = api(s, action="query", prop="imageinfo", iiprop=IIPROPS, iiextmetadatafilter=EXT, **gen_params, **cont)
        for page in data.get("query", {}).get("pages", []):
            ii = (page.get("imageinfo") or [None])[0]
            if ii: out.append((page["title"], ii))
        if "continue" not in data: break
        cont = {k: v for k, v in data["continue"].items()}
        time.sleep(0.3)
    return out

IA_AUDIO = {"VBR MP3": "audio/mpeg", "MP3": "audio/mpeg", "128Kbps MP3": "audio/mpeg", "64Kbps MP3": "audio/mpeg",
            "Ogg Vorbis": "audio/ogg", "Flac": "audio/flac", "24bit Flac": "audio/flac", "WAVE": "audio/wav"}
IA_PREF = ["VBR MP3", "MP3", "128Kbps MP3", "Ogg Vorbis", "Flac", "24bit Flac", "64Kbps MP3", "WAVE"]

def ia_licence(md):
    url = (md.get("licenseurl") or "").strip()
    rights = strip_html(" ".join(md.get("rights", [])) if isinstance(md.get("rights"), list) else md.get("rights", ""))
    u = url.lower()
    if "publicdomain/mark" in u: return "Domaine public (Public Domain Mark)", url
    if "publicdomain/zero" in u: return "CC0", url
    m = re.search(r"licenses/(by(?:-sa)?)/([\d.]+)", u)
    if m: return f"CC {m.group(1).upper()} {m.group(2)}", url
    if "public domain" in rights.lower(): return "Domaine public", url
    return None, url

def discover_ia(s, args, known):
    """Internet Archive: list audio files of items in a collection (default: musopen) or given items."""
    items = list(args.ia_item or [])
    if not items:
        q = args.ia_query or "collection:musopen AND mediatype:audio"
        r = polite_get(s, "https://archive.org/advancedsearch.php",
                       params={"q": q, "fl[]": "identifier", "rows": 1000, "output": "json"})
        items = [d["identifier"] for d in r.json()["response"]["docs"]]
        time.sleep(1)
    cands = []
    for ident in items:
        try:
            md = polite_get(s, f"https://archive.org/metadata/{ident}").json()
        except Exception as e:
            print(f"  skip {ident}: {e}", file=sys.stderr); continue
        time.sleep(0.8)
        meta = md.get("metadata", {})
        lic, lic_url = ia_licence(meta)
        if not lic:
            print(f"  skip {ident}: no free licence stated", file=sys.stderr); continue
        files = md.get("files", [])
        groups = {}
        for f in files:
            fmt = f.get("format")
            if fmt not in IA_AUDIO: continue
            key = f.get("original") if f.get("source") == "derivative" and f.get("original") else f["name"]
            groups.setdefault(key, []).append(f)
        creator = meta.get("creator", "")
        creator = ", ".join(creator) if isinstance(creator, list) else creator
        for key, fs in groups.items():
            fs.sort(key=lambda f: IA_PREF.index(f["format"]))
            f = fs[0]
            fid = f"ia:{ident}/{key}"
            if fid in known: continue
            size = int(f.get("size") or 0)
            if size < args.min_kb * 1024 // 4: continue   # MP3 derivatives are small; keep a lower bar
            name = f.get("title") or re.sub(r"\.[a-z0-9]+$", "", key.split("/")[-1])
            cands.append({
                "file": fid, "url": f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}",
                "page": f"https://archive.org/details/{ident}", "mime": IA_AUDIO[f["format"]], "kb": round(size / 1024),
                "licence": lic, "licence_url": lic_url, "artist": strip_html(f.get("artist") or f.get("creator") or creator)[:200],
                "credit": "Internet Archive", "description": strip_html(f"{meta.get('title', '')} — {name}")[:400],
                "title": name, "duration": f.get("length"),
            })
    return cands

def discover(args):
    s = session()
    catalogue = json.load(open(args.catalog, encoding="utf-8")) if args.catalog and os.path.exists(args.catalog) else {"pieces": []}
    known = {(p.get("src") or {}).get("file") for p in catalogue.get("pieces", [])}
    if args.source == "ia":
        cands = discover_ia(s, args, known)
        cands.sort(key=lambda c: c["file"].lower())
        json.dump(cands, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{len(cands)} candidates written to {args.out} (Internet Archive)")
        return
    rows = []
    if args.search:
        rows += _collect(s, dict(generator="search", gsrsearch=f"{args.search} filetype:audio", gsrnamespace=6, gsrlimit=50))
    else:
        for cat in (args.category or DEFAULT_CATEGORIES):
            rows += _collect(s, dict(generator="categorymembers", gcmtitle=cat, gcmtype="file", gcmlimit=500))
    seen, cands = set(), []
    for title, ii in rows:
        if title in seen or title in known: continue
        seen.add(title)
        em = ii.get("extmetadata", {})
        g = lambda k: (em.get(k) or {}).get("value", "")
        mime = ii.get("mime", "")
        if ii.get("mediatype") != "AUDIO" and mime not in AUDIO_MIME: continue
        if ii.get("size", 0) < args.min_kb * 1024: continue
        lic, lic_url = strip_html(g("LicenseShortName")), g("LicenseUrl")
        if not licence_ok(lic, lic_url): continue
        cands.append({
            "file": title, "url": ii["url"], "page": ii.get("descriptionurl"), "mime": mime,
            "kb": round(ii.get("size", 0) / 1024), "licence": lic, "licence_url": lic_url,
            "artist": strip_html(g("Artist"))[:200], "credit": strip_html(g("Credit"))[:200],
            "description": strip_html(g("ImageDescription"))[:400],
        })
    cands.sort(key=lambda c: c["file"].lower())
    json.dump(cands, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(cands)} candidates written to {args.out} ({len(rows)} files scanned, {len(known)} already in catalogue)")


# ------------------------------------------------------------------ fetch & excerpt
def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def decode(path, sr=SR):
    cmd = [ffmpeg(), "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()

def choose_start(y, sr, L=EXCERPT, search_max=240.0):
    """Pick a passage of length L: recurring material (the main theme usually returns),
    clearly audible, with some activity, and preferably early in the movement."""
    import librosa
    mono = librosa.to_mono(y.T) if y.ndim == 2 else y
    a = librosa.resample(mono, orig_sr=sr, target_sr=22050) if sr != 22050 else mono
    asr = 22050
    a = a[: int(480 * asr)]                               # the first 8 minutes are enough to find the main theme
    dur = len(a) / asr
    # trim silence at both ends
    rms_full = librosa.feature.rms(y=a, frame_length=2048, hop_length=512)[0]
    db = 20 * np.log10(rms_full + 1e-9)
    loud = np.where(db > db.max() - 38)[0]
    t0 = loud[0] * 512 / asr if len(loud) else 0.0
    t1 = (loud[-1] + 1) * 512 / asr if len(loud) else dur
    if t1 - t0 <= L + 2:
        return t0, min(L, t1 - t0)
    hop = 4096
    fps = asr / hop                                       # ~5.4 frames/s
    chroma = librosa.feature.chroma_cqt(y=a, sr=asr, hop_length=hop)
    chroma = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    F = chroma.T                                          # frames x 12
    n = F.shape[0]
    rms = librosa.feature.rms(y=a, frame_length=hop * 2, hop_length=hop)[0][:n]
    onset = librosa.onset.onset_strength(y=a, sr=asr, hop_length=hop)[:n]
    w = int(round(L * fps))
    starts = np.arange(int(t0 * fps), max(int(t0 * fps) + 1, min(int((t1 - L) * fps), int((t0 + search_max) * fps))))
    if len(starts) == 0:
        return t0, L
    # recurrence: best aligned similarity of the window with any non-overlapping window
    S = F @ F.T
    rep = np.zeros(n)
    for lag in range(w, n - w):
        d = np.diagonal(S, lag)
        if len(d) < w: break
        c = np.convolve(d, np.ones(w) / w, mode="valid")  # c[i] = sim(window i, window i+lag)
        m = len(c)
        rep[:m] = np.maximum(rep[:m], c)
        rep[lag:lag + m] = np.maximum(rep[lag:lag + m], c)
    ref = np.percentile(rms[rms > 0], 90) + 1e-9 if np.any(rms > 0) else 1.0
    loud_db = 20 * np.log10(rms / ref + 1e-9)
    cs = lambda v: np.convolve(v, np.ones(w) / w, mode="valid")
    loud_w, on_w = cs(loud_db), cs(onset)
    S_rep = rep[starts]
    S_loud = np.clip(loud_w[np.minimum(starts, len(loud_w) - 1)], -30, 0)
    S_on = on_w[np.minimum(starts, len(on_w) - 1)]
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    minutes = (starts / fps - t0) / 60.0
    score = 1.0 * z(S_rep) + 0.7 * z(S_loud) + 0.35 * z(S_on) - 0.35 * minutes
    score[S_loud < -16] -= 3                              # avoid near-silent passages
    best = int(np.argmax(score))
    # Famous themes usually open the movement: the opening wins unless it is quiet or clearly weaker.
    short = (t1 - t0) < 480
    margin = 1.0 if (short and S_loud[0] > -10) else 0.45
    if score[0] >= score[best] - margin:
        best = 0
    else:
        # otherwise take the first statement of the chosen material, not a later repeat
        b = starts[best]
        for i, a0 in enumerate(starts[:best]):
            if S_loud[i] < -12: continue
            sim = float(np.mean(np.sum(F[a0:a0 + w] * F[b:b + w], axis=1)))
            if sim >= 0.88:
                best = i; break
    start = starts[best] / fps
    # snap to a note onset at or just before the chosen start
    on_full = librosa.onset.onset_detect(y=a, sr=asr, hop_length=256, units="time", backtrack=True)
    near = on_full[(on_full <= start + 0.3) & (on_full >= start - 2.0)]
    if len(near): start = float(near[-1])
    return max(t0, start), L

def encode_mp3(y, path, kbps=112):
    import lameenc
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    enc = lameenc.Encoder(); enc.set_bit_rate(kbps); enc.set_in_sample_rate(SR); enc.set_channels(2); enc.set_quality(2)
    data = enc.encode(pcm.tobytes()) + enc.flush()
    open(path, "wb").write(data)
    return len(data)

def fetch_one(s, p, out_dir):
    """Download one recording, cut the excerpt, write <out_dir>/<id>.mp3. Returns a result dict."""
    pid = p.get("id") or slug(p["file"].replace("File:", ""))
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            with polite_get(s, p["url"], stream=True) as r:
                with open(src, "wb") as fh:
                    for chunk in r.iter_content(1 << 16): fh.write(chunk)
            y = decode(src)
        total = len(y) / SR
        if "start" in p:
            start, L = float(p["start"]), float(p.get("excerpt_length", EXCERPT))
        else:
            start, L = choose_start(y, SR)
        # never begin on silence: move to the first audible sound within 8 s
        head = np.abs(y[int(start * SR): int((start + 8) * SR)]).max(axis=1)
        ref = np.abs(y[int(start * SR): int((start + 40) * SR)]).max() + 1e-9
        loud = np.nonzero(head > 0.02 * ref)[0]
        if len(loud) and loud[0] / SR > 0.3:
            start += max(0.0, loud[0] / SR - 0.1)
        seg = y[int(start * SR): int((start + L) * SR)].copy()
        if len(seg) < 8 * SR:
            raise ValueError(f"recording too short ({total:.0f}s)")
        fi, fo = int(0.25 * SR), int(1.6 * SR)
        seg[:fi] *= np.linspace(0, 1, fi)[:, None]
        seg[-fo:] *= np.linspace(1, 0, fo)[:, None]
        peak = np.abs(seg).max() + 1e-9
        rms = np.sqrt((seg ** 2).mean()) + 1e-9
        seg *= min(0.89 / peak, 0.13 / rms)
        kb = encode_mp3(seg, os.path.join(out_dir, pid + ".mp3")) / 1024
        print(f"ok   {pid}: {start:6.1f}s / {total:6.1f}s  ({kb:.0f} KB)")
        return {"id": pid, "file": p["file"], "start": round(start, 2), "length": round(len(seg) / SR, 2),
                "source_duration": round(total, 1), "kb": round(kb)}
    except Exception as e:  # keep going; failures are reported
        print(f"FAIL {pid}: {e}", file=sys.stderr)
        return {"id": pid, "file": p.get("file"), "error": str(e)[:300], "throttled": isinstance(e, Throttled)}

def fetch(args):
    s = session()
    picks = json.load(open(args.picks, encoding="utf-8"))
    os.makedirs(args.out_dir, exist_ok=True)
    results = []
    for p in picks:
        results.append(fetch_one(s, p, args.out_dir))
        time.sleep(0.5)
    json.dump(results, open(os.path.join(args.out_dir, "fetched.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{sum('error' not in r for r in results)}/{len(results)} excerpts written to {args.out_dir}")

def catalog_entry(pid, m, p, r, added):
    return {
        "id": pid, "c": m["c"], "t": m["t"], "y": m.get("y"), "approx": bool(m.get("approx", False)),
        "cat": m.get("cat", ""), "form": m.get("form"), "audio": f"audio/{pid}.mp3", "loop": None,
        "fact": m.get("fact", ""), "stat": m.get("stat"), "added": added, "retired": None,
        "src": {"file": p["file"], "page": p.get("page"), "artist": p.get("artist") or "", "licence": p.get("licence", ""),
                "licence_url": p.get("licence_url", ""), "start": r.get("start")},
        "synth": False}


# ------------------------------------------------------------------ weekly release (GitHub Actions)
def paris_today():
    from zoneinfo import ZoneInfo
    import datetime as dt
    return dt.datetime.now(ZoneInfo("Europe/Paris")).date()

def release(args):
    """Weekly job: take the next items of queue.json (curated works with French metadata), download and cut
    them, add them to the catalogue from tomorrow, then rotate: retire synthesized stand-ins, cap the pool,
    and delete audio files of pieces retired more than a week ago.
    queue.json = {"composers_add": {...}, "items": [{id, file, url, page, artist, licence, licence_url,
                  start?, replaces?, tries?, meta: {c, t, y, approx, cat, form, fact, stat}}]}"""
    import datetime as dt
    today = paris_today(); tomorrow = (today + dt.timedelta(days=1)).isoformat()
    week_ago = (today - dt.timedelta(days=7)).isoformat()
    cat = json.load(open(args.catalog, encoding="utf-8"))
    q = json.load(open(args.queue, encoding="utf-8"))
    for k, v in q.get("composers_add", {}).items():
        cat["composers"].setdefault(k, v)
    have = {p["id"] for p in cat["pieces"]}
    items = [it for it in q.get("items", []) if it["id"] not in have]
    s = session(); os.makedirs(args.audio_dir, exist_ok=True)
    done, kept, failed, skipped_hosts = [], [], [], set()
    live = lambda p: p["added"] <= tomorrow and not (p.get("retired") and p["retired"] <= tomorrow)
    count = args.count or (50 if sum(1 for p in cat["pieces"] if live(p) and p.get("src")) < 100 else 25)
    rest = []
    for i, it in enumerate(items):
        if len(done) >= count or len(failed) >= max(10, count // 2):
            rest = items[i:]; break
        host = urllib.parse.urlparse(it["url"]).netloc
        if THROTTLE.get(host, 0) >= 3:
            skipped_hosts.add(host); kept.append(it); continue
        m = it.get("meta") or {}
        if m.get("c") not in cat["composers"]:
            failed.append(f"{it['id']}: unknown composer {m.get('c')}"); continue
        r = fetch_one(s, it, args.audio_dir)
        if "error" in r:
            if r.get("throttled"):
                skipped_hosts.add(host); kept.append(it); continue
            it["tries"] = it.get("tries", 0) + 1
            if it["tries"] < 3: kept.append(it)
            failed.append(f"{it['id']}: {r['error'][:120]}")
            continue
        cat["pieces"].append(catalog_entry(it["id"], m, it, r, tomorrow))
        if it.get("replaces"):
            for p in cat["pieces"]:
                if p["id"] == it["replaces"] and not p.get("retired"): p["retired"] = tomorrow
        done.append(f"{m['t']} ({cat['composers'][m['c']][1]})")
        time.sleep(0.5)
    q["items"] = kept + rest
    # rotation
    active = lambda p: p["added"] <= tomorrow and not (p.get("retired") and p["retired"] <= tomorrow)
    real = [p for p in cat["pieces"] if active(p) and p.get("src")]
    retired = []
    if len(real) >= 100:
        for p in cat["pieces"]:
            if p.get("synth") and active(p): p["retired"] = tomorrow; retired.append(p["id"])
    act = sorted([p for p in cat["pieces"] if active(p) and p.get("src")], key=lambda p: p["added"])
    for p in act[: max(0, len(act) - args.cap)]:
        p["retired"] = tomorrow; retired.append(p["id"])
    removed = []
    for p in cat["pieces"]:
        if p.get("retired") and p["retired"] <= week_ago:
            f = os.path.join(args.audio_dir, os.path.basename(p["audio"]))
            if os.path.exists(f): os.remove(f); removed.append(p["id"])
    cat["updated"] = today.isoformat()
    json.dump(cat, open(args.catalog, "w", encoding="utf-8"), ensure_ascii=False)
    fmt_catalog(args.catalog)
    json.dump(q, open(args.queue, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_active = sum(1 for p in cat["pieces"] if active(p))
    pl = lambda n, one, many: f"{n} {one if n < 2 else many}"
    lines = [f"### Da capo, renouvellement du {today.isoformat()}",
             f"- {pl(len(done), 'nouvel extrait', 'nouveaux extraits')}, en jeu à partir du {tomorrow}" + (" : " + ", ".join(done[:8]) + ("…" if len(done) > 8 else "") if done else ""),
             f"- {pl(len(retired), 'extrait retiré', 'extraits retirés')}, {pl(len(removed), 'fichier audio supprimé', 'fichiers audio supprimés')}",
             f"- {pl(n_active, 'extrait', 'extraits')} en jeu demain, {pl(len(q['items']), 'œuvre', 'œuvres')} en attente dans la file"]
    if skipped_hosts: lines.append(f"- Reporté à la semaine prochaine (serveur saturé) : {', '.join(sorted(skipped_hosts))}")
    if failed: lines += ["- Échecs :"] + [f"  - {f}" for f in failed]
    if len(q["items"]) < 30: lines.append("- La file d’attente baisse : elle sera regarnie par la tâche Claude du samedi.")
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text + "\n")


# ------------------------------------------------------------------ merge a batch
def merge(args):
    """Add fetched excerpts to the catalogue, using French metadata written by the curator.
    meta.json = {"composers_add": {key: [full, short, birth, death, era]}, "pieces": {id: {c, t, y, approx?, cat, form, fact, stat}}}"""
    cat = json.load(open(args.catalog, encoding="utf-8"))
    picks = {p["id"]: p for p in json.load(open(args.picks, encoding="utf-8"))}
    fetched = json.load(open(args.fetched, encoding="utf-8"))
    meta = json.load(open(args.meta, encoding="utf-8"))
    for k, v in meta.get("composers_add", {}).items():
        cat["composers"].setdefault(k, v)
    have = {p["id"] for p in cat["pieces"]}
    added, skipped = 0, []
    for r in fetched:
        pid = r.get("id")
        if "error" in r: skipped.append(f"{pid}: download/excerpt failed"); continue
        if pid in have: skipped.append(f"{pid}: already in catalogue"); continue
        m, p = meta["pieces"].get(pid), picks.get(pid)
        if not m or not p: skipped.append(f"{pid}: no metadata"); continue
        cat["pieces"].append(catalog_entry(pid, m, p, r, args.added))
        added += 1
    cat["updated"] = args.added
    json.dump(cat, open(args.catalog, "w", encoding="utf-8"), ensure_ascii=False)
    fmt_catalog(args.catalog)
    print(f"{added} pieces added (available from {args.added}); {len(skipped)} skipped")
    for s in skipped: print("  skipped:", s)


# ------------------------------------------------------------------ catalogue tools
FORMS = {"violon", "alto", "violoncelle", "contrebasse", "guitare", "harpe", "flute", "hautbois", "coranglais", "clarinette",
         "basson", "trompette", "cor", "piano", "clavecin", "orgue", "celesta", "voix", "choeur", "orchestre", "cordes",
         "quatuor", "trio", "violonpiano", "pianoorch", "violonorch", "celloorch", "vents"}

def fmt_catalog(path):
    c = json.load(open(path, encoding="utf-8"))
    L = ["{", f' "updated": {json.dumps(c["updated"])},', f' "epoch": {json.dumps(c["epoch"])},', ' "composers": {']
    ks = list(c["composers"])
    L += [f'  {json.dumps(k)}: {json.dumps(c["composers"][k], ensure_ascii=False)}' + ("," if i < len(ks) - 1 else "") for i, k in enumerate(ks)]
    L += [" },", ' "pieces": [']
    L += ["  " + json.dumps(x, ensure_ascii=False) + ("," if i < len(c["pieces"]) - 1 else "") for i, x in enumerate(c["pieces"])]
    L += [" ]", "}"]
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")

def check(args):
    """Validate catalog.json; exit 1 on any error so a broken catalogue is never published."""
    c = json.load(open(args.catalog, encoding="utf-8"))
    errs, warn = [], []
    comps = c.get("composers", {})
    for k, v in comps.items():
        if not (isinstance(v, list) and len(v) == 5 and isinstance(v[2], int) and isinstance(v[3], int) and v[4] in "BCRM"):
            errs.append(f"composer {k}: expected [full name, short name, birth, death, era B/C/R/M]")
    ids, date = set(), re.compile(r"^\d{4}-\d{2}-\d{2}$")
    today = time.strftime("%Y-%m-%d")
    active = 0
    for p in c.get("pieces", []):
        pid = p.get("id")
        if not pid or pid in ids: errs.append(f"missing or duplicate id: {pid}"); continue
        ids.add(pid)
        for f in ("c", "t", "audio", "added"):
            if not p.get(f): errs.append(f"{pid}: missing {f}")
        if p.get("c") not in comps: errs.append(f"{pid}: unknown composer {p.get('c')}")
        if p.get("form") is not None and p["form"] not in FORMS: errs.append(f"{pid}: unknown form {p['form']}")
        if p.get("y") is not None and not (1100 <= int(p["y"]) <= 1990): errs.append(f"{pid}: implausible year {p['y']}")
        for f in ("added", "retired"):
            if p.get(f) and not date.match(p[f]): errs.append(f"{pid}: bad date {f}={p[f]}")
        if not p.get("retired") or p["retired"] > today: active += 1
        if p.get("src") is None and not p.get("synth"): warn.append(f"{pid}: no src and not marked synth")
        if args.audio_dir and not p.get("retired") and not os.path.exists(os.path.join(args.audio_dir, os.path.basename(p.get("audio", "")))):
            warn.append(f"{pid}: audio file not found locally ({p.get('audio')})")
    print(f"{len(ids)} pieces ({active} active), {len(comps)} composers, {len(errs)} errors, {len(warn)} warnings")
    for w in warn[:30]: print("warning:", w)
    for e in errs: print("ERROR:", e)
    if not errs: fmt_catalog(args.catalog)
    sys.exit(1 if errs else 0)


def checkq(args):
    """Validate queue.json against the catalogue before committing it; exit 1 on any error."""
    c = json.load(open(args.catalog, encoding="utf-8"))
    q = json.load(open(args.queue, encoding="utf-8"))
    comps = {**c["composers"], **q.get("composers_add", {})}
    cat_ids = {p["id"] for p in c["pieces"]}
    cat_files = {(p.get("src") or {}).get("file") for p in c["pieces"]}
    synth = {p["id"] for p in c["pieces"] if p.get("synth")}
    errs, seen, files = [], set(), set()
    for k, v in q.get("composers_add", {}).items():
        if not (isinstance(v, list) and len(v) == 5 and isinstance(v[2], int) and isinstance(v[3], int) and v[4] in ("B", "C", "R", "M")):
            errs.append(f"composers_add {k}: expected [full name, short name, birth, death, era B/C/R/M]")
    for it in q.get("items", []):
        pid = it.get("id", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", pid): errs.append(f"{pid!r}: id must be a lowercase ascii slug")
        if pid in seen: errs.append(f"{pid}: duplicate id in queue")
        if pid in cat_ids: errs.append(f"{pid}: id already used in catalog.json")
        seen.add(pid)
        for f in ("file", "url", "page", "licence", "licence_url"):
            if not it.get(f): errs.append(f"{pid}: missing {f}")
        if it.get("file") in cat_files: errs.append(f"{pid}: recording already in the catalogue ({it.get('file')})")
        if it.get("file") in files: errs.append(f"{pid}: recording listed twice in the queue")
        files.add(it.get("file"))
        if not str(it.get("url", "")).startswith("https://"): errs.append(f"{pid}: url must be https")
        if it.get("start") is not None and not isinstance(it["start"], (int, float)): errs.append(f"{pid}: start must be a number of seconds")
        if it.get("replaces") and it["replaces"] not in synth: errs.append(f"{pid}: replaces {it['replaces']}, which is not a synthesized piece")
        m = it.get("meta") or {}
        for f in ("c", "t", "fact"):
            if not m.get(f): errs.append(f"{pid}: meta.{f} missing")
        if m.get("c") and m["c"] not in comps: errs.append(f"{pid}: unknown composer {m['c']} (add it to composers_add)")
        if m.get("form") is not None and m["form"] not in FORMS: errs.append(f"{pid}: unknown form {m['form']}")
        if m.get("y") is not None and not (isinstance(m["y"], int) and 1100 <= m["y"] <= 1990): errs.append(f"{pid}: implausible year {m.get('y')}")
        if "<" in m.get("fact", "") or "<" in m.get("t", ""): errs.append(f"{pid}: no HTML in title or fact")
    print(f"{len(q.get('items', []))} items in queue, {len(errs)} errors")
    for e in errs: print("ERROR:", e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("--catalog"); d.add_argument("--out", default="candidates.json")
    d.add_argument("--category", action="append"); d.add_argument("--search"); d.add_argument("--min-kb", type=int, default=600)
    d.add_argument("--source", choices=["commons", "ia"], default="commons", help="Wikimedia Commons (default) or Internet Archive")
    d.add_argument("--ia-item", action="append", help="Internet Archive item identifier (repeatable)")
    d.add_argument("--ia-query", help="Internet Archive search query (default: collection:musopen AND mediatype:audio)")
    f = sub.add_parser("fetch"); f.add_argument("--picks", required=True); f.add_argument("--out-dir", default="excerpts")
    k = sub.add_parser("check"); k.add_argument("--catalog", default="catalog.json"); k.add_argument("--audio-dir")
    m = sub.add_parser("merge"); m.add_argument("--catalog", default="catalog.json"); m.add_argument("--picks", required=True)
    m.add_argument("--fetched", required=True); m.add_argument("--meta", required=True); m.add_argument("--added", required=True)
    r = sub.add_parser("release", help="weekly job: publish the next items of queue.json")
    r.add_argument("--catalog", default="catalog.json"); r.add_argument("--queue", default="queue.json")
    r.add_argument("--audio-dir", default="audio")
    r.add_argument("--count", type=int, default=0, help="new excerpts per run (0 = 50 while under 100 real recordings, then 25)")
    r.add_argument("--cap", type=int, default=400)
    cq = sub.add_parser("checkq", help="validate queue.json before committing it")
    cq.add_argument("--catalog", default="catalog.json"); cq.add_argument("--queue", default="queue.json")
    a = ap.parse_args()
    {"discover": discover, "fetch": fetch, "check": check, "merge": merge, "release": release, "checkq": checkq}[a.cmd](a)
