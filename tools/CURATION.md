# Regarnir la file d’attente (`queue.json`)

L’action du dimanche prend les premières œuvres de `queue.json` : 50 par semaine tant que le jeu compte moins de 100 vrais enregistrements en jeu, puis 25. La file doit donc toujours contenir **au moins 60 œuvres**. Ce travail de sélection est fait chaque samedi par une tâche Claude, ou à la main en suivant ces règles.

Aucun téléchargement n’est nécessaire pour regarnir la file : l’action GitHub télécharge et découpe les extraits elle-même.

## 1. Trouver des enregistrements libres

```sh
pip install -r tools/requirements.txt
# Internet Archive, collection Musopen par défaut
python3 tools/renew.py discover --source ia --catalog catalog.json --out cand_ia.json
# autres collections libres, par exemple :
python3 tools/renew.py discover --source ia --catalog catalog.json --out cand_ia2.json \
  --ia-query 'collection:opensource_audio AND subject:classical AND licenseurl:*publicdomain*'
# Wikimedia Commons (catégories Musopen par défaut, ou --search "…")
python3 tools/renew.py discover --catalog catalog.json --out cand_wm.json
```

Les fichiers de candidats contiennent les champs `file`, `url`, `page`, `artist`, `licence`, `licence_url`, `title`, `description`, `kb`. Si Commons répond 429, attendre le délai demandé ou s’en tenir à Internet Archive : ne jamais contourner une limite.

## 2. Choisir

- D’abord les œuvres célèbres et reconnaissables (thèmes entendus au concert, au cinéma, dans la publicité), puis de belles pièces moins connues.
- Varier les époques (baroque, classique, romantique, XXe siècle), les formations (orchestre, piano, musique de chambre, voix) et les compositeurs : au plus 3 nouvelles œuvres par compositeur à chaque fois, au plus 2 mouvements d’une même œuvre dans tout le catalogue et la file réunis.
- Écarter : ce qui est déjà dans `catalog.json` ou dans la file (même œuvre et même mouvement, ou même fichier), les échantillons d’instruments et fichiers de test, la vidéo, les titres peu clairs, la voix parlée, les arrangements éloignés de l’original, les fichiers de moins de 400 Ko.
- Pour un même enregistrement, préférer le MP3 ou l’OGG au FLAC.
- Écarter toute œuvre que l’on ne peut pas identifier avec certitude.
- Ranger la file par ordre de priorité : les œuvres les plus célèbres en tête, en alternant compositeurs et époques.

## 3. Écrire chaque entrée

```json
{
  "id": "beethoven-symphonie-7-allegretto",
  "file": "<copié du candidat>", "url": "<copié>", "page": "<copié>",
  "artist": "Czech National Symphony Orchestra",
  "licence": "Domaine public (Public Domain Mark)", "licence_url": "<copié>",
  "start": 0,
  "replaces": "beethoven_7",
  "meta": {"c": "beethoven", "t": "Symphonie n°7, Allegretto", "y": 1812, "approx": false,
           "cat": "Op. 92", "form": "orchestre",
           "fact": "Une ou deux phrases vérifiables en français.", "stat": null}
}
```

- `id` : slug ASCII en minuscules, `compositeur-oeuvre`, inédit dans le catalogue et la file.
- `artist` : l’interprète, jamais le compositeur. « Musopen » si l’interprète n’est pas connu.
- `licence` : libellé en français : « Domaine public », « Domaine public (Public Domain Mark) », « CC0 », « CC BY 4.0 », « CC BY-SA 3.0 »… Seules ces licences sont acceptées (pas de NC ni de ND).
- `start` (facultatif) : `0` quand le thème le plus connu se fait entendre dès le début du fichier. Sinon, ne pas le mettre : le script choisit seul un passage qui revient dans l’œuvre.
- `replaces` (facultatif) : l’`id` d’une version synthétisée (`"synth": true`) de la même œuvre encore en jeu ; elle sera retirée à l’arrivée du vrai enregistrement.
- `meta.c` : clé d’un compositeur de `catalog.json`, ou à ajouter dans `composers_add` : `"clé": ["Nom complet", "Nom court", naissance, mort, "B|C|R|M"]` (B baroque, surtout avant 1750 ; C classique, 1750-1820 ; R romantique, 1820-1900 ; M XXe siècle).
- `meta.t` : le titre français usuel, court. Ajouter le mouvement seulement s’il faut distinguer deux entrées (« Symphonie n°5, Finale »).
- `meta.y` : année d’achèvement, ou de première publication si c’est ce que l’on sait ; `approx: true` si elle est incertaine ; `null` si inconnue.
- `meta.cat` : numéro de catalogue (« Op. 67 », « K. 550 », « BWV 1007 », « RV 269 », « D. 960 », « Hob. I:94 », « HWV 56 ») ou `""`.
- `meta.form` : seulement quand toute la pièce est clairement jouée par cet instrument ou cet ensemble : violon, alto, violoncelle, contrebasse, guitare, harpe, flute, hautbois, coranglais, clarinette, basson, trompette, cor, piano, clavecin, orgue, celesta, voix, choeur, orchestre (symphonies, ouvertures), cordes, quatuor, trio, violonpiano, pianoorch, violonorch, celloorch, vents. `null` pour les concertos, les numéros d’opéra et tout cas douteux.
- `meta.fact` : une ou deux phrases simples, factuelles et vérifiables sur l’œuvre ou son histoire. Vérifier en cas de doute ; ne jamais inventer d’anecdote. Pas de HTML.
- `meta.stat` : `null`, sauf classement Bachtrack connu avec certitude.
- Typographie française : « guillemets », apostrophe ’, n°.

## 4. Valider et publier

```sh
python3 tools/renew.py checkq   # doit afficher 0 errors
git add queue.json && git commit -m "File d’attente : +N œuvres" && git pull --rebase && git push
```
