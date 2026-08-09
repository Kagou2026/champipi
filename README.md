# 🍄 Champipi — indice de pousse des cèpes en Lozère

Site privé qui estime, chaque jour et par maille de 8 km, les conditions
favorables à la pousse des cèpes/bolets en **Lozère (48)**. Il combine
l'humidité des sols, la pluie récente et la température, pondérés par la
géologie (le calcaire, défavorable au cèpe, est fortement pénalisé).

Tout est **gratuit** et **sans clé d'API**. Le site est **protégé par un mot
de passe** (contenu chiffré, cookie de 3 jours).

---

## Comment ça marche

```
                data.gouv (SIM/SAFRAN)  ─┐
                BRGM (géologie)          ├─►  robot Python  ─►  site/index.html
                Open-Meteo (température) ─┘     (indice)          (auto-porté)
                                                                     │
                          GitHub Actions (chaque matin) ────────────┤
                                                                     ▼
                                              StatiCrypt (chiffrement + mot de passe)
                                                                     │
                                                                     ▼
                                                       GitHub Pages (site en ligne)
```

- **`robot/`** : le calcul.
  - `config.py` — tous les réglages (emprise Lozère, **seuils de l'indice**, coefficients géologie).
  - `fetch_sim.py` — humidité du sol (SWI), pluie 15 j, évapotranspiration (WFS Météo-France/SAFRAN). *Recalcule les coordonnées depuis la géométrie car le champ lat/lon du flux est erroné.*
  - `fetch_temp.py` — température par maille (Open-Meteo).
  - `compute_index.py` — la formule de l'indice cèpe (0–100).
  - `build_terrain.py` — **à lancer une seule fois** : géologie + altitude + contour du 48 → `data/terrain.json`.
  - `run.py` — orchestrateur quotidien : génère `site/index.html`.
- **`site/template.html`** : la carte (Leaflet) + le graphe 15 j (Chart.js). `index.html` en est la version remplie de données, régénérée chaque jour.
- **`data/terrain.json`** : le masque terrain statique (déjà calculé, 82 mailles).
- **`.github/workflows/daily.yml`** : lance le robot, chiffre la page, publie.

---

## Mise en route (une seule fois)

### 1. Créer le dépôt GitHub
Crée un dépôt (privé ou public — le contenu est chiffré de toute façon) et
pousse le contenu de ce dossier dedans.

```bash
git init
git add .
git commit -m "Champipi — version initiale"
git branch -M main
git remote add origin https://github.com/<ton-compte>/champipi.git
git push -u origin main
```

### 2. Définir le mot de passe du site
Dans le dépôt GitHub : **Settings → Secrets and variables → Actions → New
repository secret**.
- **Name** : `SITE_PASSWORD`
- **Secret** : le mot de passe de ton choix.

> Le mot de passe n'est jamais écrit dans le code. Il reste dans ce secret,
> utilisé seulement au moment du chiffrement. Pour le changer, modifie le
> secret puis relance le workflow (étape 4).

### 3. Activer GitHub Pages
**Settings → Pages → Build and deployment → Source : GitHub Actions.**

### 4. Lancer une première fois
**Onglet Actions → « Champipi — mise à jour quotidienne » → Run workflow.**
Au bout d'une minute, l'URL du site apparaît dans le job *deploy*
(`https://<ton-compte>.github.io/champipi/`).

Ensuite, le site se met à jour **tout seul chaque matin**.

---

## Lancer / tester en local (facultatif)

```bash
pip install -r requirements.txt
python robot/run.py          # génère site/index.html avec les données du jour
# ouvre ensuite site/index.html dans un navigateur (version NON chiffrée)
```

Régénérer le masque terrain (si tu affines la géologie ou l'emprise) :

```bash
python robot/build_terrain.py   # réécrit data/terrain.json (~1 min)
```

---

## Régler l'indice

Tout est dans **`robot/config.py`**, en haut de fichier, avec des commentaires :

- `SWI_MIN`, `SWI_OPTIMAL` — seuils d'humidité du sol.
- `PLUIE_15J_MIN`, `PLUIE_15J_OPTIMAL` — cumul de pluie déclencheur.
- `TEMP_MIN … TEMP_MAX` — fenêtre de température du cèpe.
- `POIDS_HUMIDITE / POIDS_PLUIE / POIDS_TEMPERATURE` — importance relative.
- `GEOLOGIE_COEFFICIENT` — pénalité calcaire (0.15) vs sol acide (1.0).

Ces valeurs sont un point de départ raisonnable. La **prochaine grande
amélioration** sera de les recaler avec tes observations de terrain.

---

## Limites (à garder en tête)

- L'indice est **indicatif** : il modélise des conditions favorables, pas la
  présence certaine de champignons.
- La maille fait **8 km** : elle ne distingue pas un versant nord d'un versant sud.
- La géologie vient de la carte BRGM au 1:1 000 000 (généralisée).
- **Il ne dit rien sur la comestibilité.** Ne jamais consommer un champignon
  sans identification sûre.

Sources : Météo-France/SAFRAN (via data.gouv, licence ouverte), BRGM, Open-Meteo, IGN.
