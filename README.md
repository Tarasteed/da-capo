# Da capo

Un blind test de musique classique, en français : on écoute 25 secondes d’une grande œuvre et l’on devine le compositeur, le titre, l’année ou l’instrument. Chaque jour, un défi de cinq extraits, le même pour tout le monde.

**Jouer : https://tarasteed.github.io/da-capo/**

## Les modes de jeu

- **Blind test** : huit extraits, trouver le compositeur puis l’œuvre. Répondre vite rapporte un bonus, mais rien n’est révélé tant qu’on n’a pas validé ou passé.
- **Défi du jour** : cinq extraits tirés chaque jour du catalogue, avec un score à partager.
- **L’année** : situer l’œuvre sur un cadran de 1600 à 1950.
- **L’instrument** : reconnaître l’instrument ou la formation.
- **Discothèque** : les œuvres déjà découvertes, avec leur histoire, l’interprète et la licence de l’enregistrement.

## Le renouvellement automatique

Le site se renouvelle seul, sans rien mettre à jour à la main :

1. **`queue.json`** est une file d’attente d’œuvres déjà choisies, avec leurs informations en français (titre, année, anecdote) et l’adresse d’un enregistrement libre de droits.
2. **Chaque dimanche**, l’action GitHub [`site.yml`](.github/workflows/site.yml) prend les œuvres suivantes de la file : 50 par semaine tant que le jeu compte moins de 100 vrais enregistrements, puis 25. Elle les télécharge, choisit automatiquement un passage de 25 secondes (en privilégiant le thème le plus reconnaissable), les ajoute à `catalog.json` à partir du lundi et remet le site en ligne.
3. **Rotation** : les versions synthétisées du début sont retirées dès qu’un vrai enregistrement de la même œuvre arrive, puis toutes une fois passé le cap des 100 enregistrements. Au-delà de 400 extraits en jeu, les plus anciens sont retirés. Les entrées retirées restent dans le catalogue, pour que les défis passés gardent leur contenu.
4. **Une tâche Claude**, programmée le dimanche avant l’action, regarnit la file quand elle descend sous une cinquantaine d’œuvres.

Le résumé de chaque renouvellement s’affiche dans l’onglet **Actions** du dépôt. Pour en lancer un à la main : Actions → Da capo → *Run workflow*.

## Organisation du dépôt

| Fichier | Rôle |
| --- | --- |
| `index.html` | Le jeu : une seule page, sans dépendance hormis les polices Google Fonts. |
| `catalog.json` | Toutes les œuvres, en jeu ou retirées, avec leurs dates d’entrée et de sortie. |
| `queue.json` | Les prochaines œuvres à ajouter. |
| `tools/renew.py` | Le script d’entretien : `discover` (chercher des enregistrements libres), `fetch` (découper des extraits), `merge`, `check` (valider le catalogue) et `release` (le renouvellement hebdomadaire). |
| branche `audio` | Les extraits MP3 en jeu. Elle est réécrite en un seul commit à chaque renouvellement, pour que le dépôt ne grossisse pas avec le temps. |

Pour tester en local : récupérer la branche `audio` dans un dossier `audio/` (`git worktree add audio audio`), puis lancer `python3 -m http.server` et ouvrir http://localhost:8000.

## Enregistrements et licences

Les extraits proviennent d’enregistrements libres de droits, pour la plupart réalisés par [Musopen](https://musopen.org) et diffusés par [Internet Archive](https://archive.org) et [Wikimedia Commons](https://commons.wikimedia.org) : domaine public, CC0, CC BY ou CC BY-SA. L’interprète, la source et la licence de chaque extrait sont indiqués dans le jeu, après chaque réponse et dans la discothèque. Les extraits sous licence CC BY-SA sont redistribués sous la même licence.

Les quelques versions « synthétisées » du lancement sont des arrangements simplifiés des thèmes, rendus avec la banque de sons [GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS). Elles disparaissent au fil des semaines.
