# Champipi — changelog du 8 au 14 septembre 2026

Sept commits, tous poussés (`main`, dernier : `65ae15b`). Les dates sont celles des commits ; plusieurs
lots travaillés le 13/09 ont été committés d'un coup dans la nuit du 13 au 14.

## 14/09 · `65ae15b` — Secteurs prévus : pastilles ①②③ toujours affichées

Correctif du calque « secteurs favorables » livré la veille. La synthèse liste les 3 meilleures mailles
sans condition, alors que le calque ne dessinait que celles ≥ 55 : avec un pic à 60, seule la ①
apparaissait. Désormais les 3 pastilles sont toujours posées (grisées sous le seuil, infobulle
« sous le seuil 55 »), le contour bleu reste réservé aux mailles ≥ 55, et la synthèse affiche
l'indice prévu de chaque secteur entre parenthèses. *Fichier : `site/template.html`.*

## 14/09 · `5c63bca` — PWA Android : écran de lancement figé

Gabarit StatiCrypt maison (`site/staticrypt_template.html`) : écran d'ouverture Champipi, textes en
français, et surtout remplacement du document sans `document.write`, qui laissait l'écran de lancement
de la PWA Android bloqué. `daily.yml` passe les options `-t` et `--template-*` à StatiCrypt.

## 14/09 · `cc9ba64` — Prévision : secteurs favorables visibles sur la carte

Réponse au constat « la synthèse cite des secteurs qu'on ne retrouve pas sur la carte ». Trois briques :

- **Timeline légère dès l'ouverture.** Le curseur Rejeu se construit à partir des séries inline
  (~30 j) et de la prévision, sans télécharger l'historique long ; un bouton « Historique complet… »
  remonte à 2022 comme avant, en conservant la date courante.
- **Synthèse cliquable.** Les dates placent le curseur sur le jour voulu (la carte se recolore comme
  elle le fera ce jour-là) ; les secteurs centrent la carte sur la maille et ouvrent sa fiche ;
  bouton « Voir les secteurs sur la carte ».
- **Calque secteurs favorables.** Sur une date de prévision : contour bleu pointillé des mailles ≥ 55
  (emprise des parcelles de la maille, ~8 km) et pastilles ①②③ sur les meilleures.

Côté robot, la série courte inline était calculée **sans lag** alors que l'indice du jour et la
prévision le sont : `run.py` sert maintenant la queue de l'historique laggé (`SERIE_COURTE_JOURS = 30`).
Effet collatéral : le petit graphe de la fiche maille coïncide enfin avec la carte du jour.
*Fichiers : `robot/run.py`, `robot/config.py`, `site/template.html`.*

## 14/09 (00:18) · `fa5a6d0` — Robustesse : dates externes blindées + fraîcheur des sources

Suite de la panne du 12/09. Nouveau module `robot/fraicheur.py` : `parse_iso` / `iso_ok` / `iso_ymd`
ne lèvent jamais d'exception ; toute date de source externe (SIM, stations, historique, prévision)
passe par là. SIM vide → erreur explicite et département sauté ; Open-Meteo en échec → indice calculé
sans température au lieu de perdre le département. `evaluer()` classe chaque source
ok / retard / périmé / absent (seuils par source) et l'écrit dans le payload et `version.json`.
Sur la page : encadré `#fraich-box` sous « Indice du jour » (orange retard, rouge périmé), ⚠ sur la
date d'en-tête, et contrôle côté client de l'âge des données (couvre le cas PWA en cache ou action
GitHub en panne). *Fichiers : `robot/emit.py`, `fetch_sim.py`, `fetch_stations.py`, `fraicheur.py`,
`run.py`, `stations.py`, `site/template.html`.*

## 14/09 (00:11) · `23136d3` — PWA : vérification de mise à jour au lancement

Motif : l'app Android reste des jours en mémoire et, quand le réseau rame, le service worker sert la
page de la veille sans le dire. `emit.py` écrit `site/version.json` (généré le, date des données,
fichiers de données versionnés) ; `sw.js` le récupère en `no-store`, et un message `MAJ` télécharge
la page et les seuls fichiers de données déjà en cache. Bandeau `#majband` : vérification / à jour /
téléchargement / avertissement, rechargement automatique si l'utilisateur n'a rien touché,
re-vérification au retour au premier plan (> 10 min) et au retour du réseau. Anti-boucle quand le
CDN GitHub Pages est en retard. *Fichiers : `daily.yml`, `robot/emit.py`, `site/sw.js`, `site/template.html`.*

## 14/09 (00:01) · `a3b8b26` — SIM : ignorer les lignes WFS sans date

Panne du 12/09 : le WFS `qry_sorties_sim` a servi pour chaque maille une ligne à `date: null` →
`fromisoformat('')` plantait, les 3 départements étaient ignorés. `fetch_sim.py` filtre ces lignes
et journalise « ⚠ N ligne(s) SIM sans date ». Au 14/09 la source amont n'a toujours pas publié de
point après le 11/09 : la page est bien la dernière génération, c'est la source qui n'avance plus.

## 09/09 · `b12c809` — Rejeu : jour manquant + navigation ±1 par index

Le service worker pouvait servir un `hist_XX.json` périmé (un jour de moins que la page), d'où un
jour absent dans le Rejeu ; `sw.js` corrigé. Les boutons ‹ › avancent maintenant d'un index (jamais
bloqués par une date absente), les « » restent par date ±30 j. *Fichiers : `site/sw.js`, `site/template.html`.*

## Points ouverts

- Vérifier au prochain run CI réussi que la série courte laggée a bien 30 points et que son dernier
  point égale l'indice du jour.
- La source SIM est bloquée au 11/09 ; le seuil « retard » (> 3 j) déclenchera l'encadré orange le 15/09.
  Piste non implémentée : réattribuer au lot à date nulle la date max + 1 j quand il forme exactement
  un lot journalier.
