"""Configuration centrale de Champipi.

Toutes les constantes du projet (zone géographique, sources de données,
seuils de l'indice cèpe) sont regroupées ici pour être faciles à ajuster.
"""

# --- Zone d'étude : département de la Lozère (48) ---------------------------
# Emprise (bounding box) en WGS84 [lon_min, lat_min, lon_max, lat_max].
# Généreuse : les mailles seront ensuite clippées au contour réel du 48.
LOZERE_BBOX_WGS84 = (2.95, 44.05, 4.00, 45.00)

# Emprise équivalente en Lambert-93 (EPSG:2154), utilisée par le WFS SIM.
LOZERE_BBOX_L93 = (680000, 6330000, 795000, 6445000)

DEPARTEMENT_CODE = "48"

# --- Sources de données (toutes en accès libre, sans clé) ------------------

# Météo-France / SAFRAN-ISBA, moissonné par la DREAL Bretagne (data.gouv).
# Fournit par maille de 8 km : SWI, évapotranspiration, cumuls de pluie 15 j.
SIM_WFS_URL = "https://geobretagne.fr/geoserver/dreal_b/wfs"
SIM_WFS_TYPENAME = "dreal_b:qry_sorties_sim"

# BRGM : lithologie simplifiée (pour classer acide / calcaire).
BRGM_WFS_URL = "https://geoservices.brgm.fr/geologie"
BRGM_WFS_TYPENAME = "ms:LITHO_1M_SIMPLIFIEE"

# Open-Meteo : température quotidienne par point, gratuit et sans clé.
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# BD Forêt V2 (IGN), via la Géoplateforme : occupation forestière détaillée.
# Sert à estimer le taux de boisement de chaque maille (filtre forêt/non-forêt)
# et, plus tard, l'essence (chêne, hêtre, conifère...). Gratuit, sans clé.
BDFORET_WFS_URL = "https://data.geopf.fr/wfs/ows"
BDFORET_WFS_TYPENAME = "LANDCOVER.FORESTINVENTORY.V2:formation_vegetale"

# Contour du département (GeoJSON communautaire, stable).
DEPT48_GEOJSON_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
    "departements/48-lozere/departement-48-lozere.geojson"
)

# --- Fichiers produits ------------------------------------------------------
TERRAIN_FILE = "data/terrain.json"        # statique, calculé une fois
FORET_GEOM_FILE = "data/foret_geom.json"  # emprise forestière par maille (statique)
SITE_TEMPLATE = "site/template.html"      # gabarit de la page
SITE_OUTPUT = "site/index.html"           # page auto-portée (données embarquées)

# --- Classification géologique → coefficient terrain -----------------------
# Le cèpe est mycorhizien sur sols acides. On pénalise fortement le calcaire.
# Coefficient multiplicatif appliqué à l'indice (0 = nul, 1 = optimal).
GEOLOGIE_COEFFICIENT = {
    "acide": 1.0,      # granites, schistes, grès, gneiss...
    "neutre": 0.7,     # basaltes, alluvions, formations mixtes
    "calcaire": 0.15,  # calcaires, marnes, dolomies
    "inconnu": 0.6,    # par défaut si non classé
}

# Mots-clés de la description BRGM (DESCR) → classe géologique.
GEOLOGIE_MOTS_CLES = {
    "acide": ["granit", "schiste", "gneiss", "grès", "gres", "micaschiste",
              "quartz", "rhyolit", "leucogranit", "migmatit", "arène", "arene"],
    "calcaire": ["calcaire", "marne", "dolomie", "gypse", "craie", "calcair"],
    "neutre": ["basalt", "alluv", "argile", "sable", "colluv", "molasse",
               "cendre", "scorie", "tuf"],
}

# --- Filtre forêt (BD Forêt V2) → coefficient de boisement -----------------
# Le cèpe est mycorhizien : sans arbre hôte, aucune pousse, quelle que soit
# la météo. On pondère chaque classe forestière (tfv_g11) par sa "valeur
# boisée", puis on somme la surface pondérée des polygones DANS la maille,
# divisée par la surface de la maille, pour obtenir un taux boisé pondéré.
#
# Poids par classe (0 = pas d'arbre exploitable, 1 = couvert forestier plein).
# - forêt fermée : couvert dense, hôtes potentiels -> 1.0
# - forêt ouverte : arbres épars, encore favorable -> 0.6
# - peupleraie : ce sont des arbres mais le peuplier n'est PAS un hôte du
#   cèpe -> faible (0.3), sera affiné quand on gèrera l'essence.
# Tout le reste (lande, pelouse, coupe rase "sans couvert arboré") = 0.
FORET_POIDS = {
    "Forêt fermée feuillus": 1.0,
    "Forêt fermée conifères": 1.0,
    "Forêt fermée mixte": 1.0,
    "Forêt fermée sans couvert arboré": 0.0,   # coupe rase : pas d'arbre
    "Forêt ouverte feuillus": 0.6,
    "Forêt ouverte conifères": 0.6,
    "Forêt ouverte mixte": 0.6,
    "Peupleraie": 0.3,
    "Lande": 0.0,
    "Formation herbacée": 0.0,
}

# Conversion taux boisé -> coefficient multiplicateur de l'indice.
# Rampe SATURÉE : le rôle du filtre est d'écarter les mailles quasi nues,
# pas de pénaliser les paysages mixtes. Dès que la maille atteint
# FORET_SATURATION de boisement, il y a largement de quoi chercher -> coef 1.
# coef_foret = min(1, taux_boise / FORET_SATURATION).
#   taux 0 %  -> 0    | 15 % -> 0.5 | >= 30 % -> 1.0
FORET_SATURATION = 0.30

# Plancher : on n'annule jamais totalement une maille (donnée BD Forêt
# imparfaite, lisières hors polygone...). Coef minimal appliqué.
FORET_COEF_MIN = 0.05

# Rendu carte "emprise forestière" : la géométrie forêt est simplifiée pour
# alléger la page autoportée. Tolérance en degrés (~0.0015° ≈ 150 m) et
# arrondi des coordonnées, invisibles à l'échelle départementale.
FORET_SIMPLIFY_TOL = 0.0015
FORET_COORD_DECIMALES = 5

# --- Essence hôte (BD Forêt V2, champ `essence`) ---------------------------
# Le cèpe est mycorhizien : l'ESPÈCE d'arbre compte, pas seulement la présence
# de forêt. On regroupe les valeurs brutes du champ `essence` en groupes qui
# servent à la fois (a) à pondérer l'indice (poids hôte) et (b) au filtre
# d'affichage de la carte (cases à cocher). Trois étages :
#   - principale : hôte nommé de premier ordre du cèpe (case individuelle) ;
#   - secondaire : forêt hôte PROBABLE mais espèce non précisée par l'IGN
#                  (« Feuillus / Conifères / Mixte » génériques) — majoritaire
#                  en Lozère, donc surtout PAS à jeter ;
#   - reste      : non-hôte ou indéterminé (peuplier, mélèze, landes...).
#
# poids : multiplicateur hôte 0..1 injecté dans coef_essence (1 = hôte idéal).
# alpha : opacité relative sur la carte (dominantes = principales teintent plus).
# defaut: case cochée à l'ouverture de la carte.
ESSENCE_GROUPES = {
    "chene":       {"label": "Chêne",                 "etage": "principale", "poids": 1.00, "alpha": 1.00, "defaut": True},
    "hetre":       {"label": "Hêtre",                 "etage": "principale", "poids": 1.00, "alpha": 1.00, "defaut": True},
    "chataignier": {"label": "Châtaignier",           "etage": "principale", "poids": 1.00, "alpha": 1.00, "defaut": True},
    "epicea":      {"label": "Épicéa / sapin",        "etage": "principale", "poids": 0.95, "alpha": 1.00, "defaut": True},
    "pin":         {"label": "Pin",                   "etage": "principale", "poids": 0.90, "alpha": 1.00, "defaut": True},
    "feuillus":    {"label": "Feuillus indéterminés", "etage": "secondaire", "poids": 0.85, "alpha": 0.62, "defaut": True},
    "coniferes":   {"label": "Conifères indéterminés","etage": "secondaire", "poids": 0.85, "alpha": 0.62, "defaut": True},
    "mixte":       {"label": "Forêt mixte",           "etage": "secondaire", "poids": 0.85, "alpha": 0.62, "defaut": True},
    "reste":       {"label": "Autres / non hôtes",    "etage": "reste",      "poids": 0.20, "alpha": 0.32, "defaut": False},
}

# Ordre d'affichage des cases à cocher (principales d'abord, reste en dernier).
ESSENCE_ORDRE = ["chene", "hetre", "chataignier", "epicea", "pin",
                 "feuillus", "coniferes", "mixte", "reste"]

# Valeur brute `essence` (BD Forêt V2) -> clé de groupe. Comparaison en
# minuscules et sans accent. Toute valeur absente tombe dans "reste"
# (peuplier, mélèze, robinier, NC, NR, None...).
ESSENCE_VERS_GROUPE = {
    "chenes decidus": "chene", "chenes sempervirents": "chene",
    "chene vert": "chene", "chene pubescent": "chene", "chene": "chene",
    "chenes": "chene",
    "hetre": "hetre",
    "chataignier": "chataignier",
    "sapin, epicea": "epicea", "epicea": "epicea", "sapin": "epicea",
    "douglas": "epicea",
    "pin sylvestre": "pin", "pin laricio, pin noir": "pin",
    "pins melanges": "pin", "pin a crochets, pin cembro": "pin",
    "pin maritime": "pin", "pin d'alep": "pin",
    "pin d'alep, pin parasol": "pin", "pins": "pin",
    "feuillus": "feuillus",
    "coniferes": "coniferes",
    "mixte": "mixte", "melanges": "mixte",
    "peuplier": "reste", "meleze": "reste", "robinier": "reste",
}

# Plancher du coef essence : la donnée d'essence est imparfaite (générique,
# lisières...), on ne met jamais une maille tout à fait à zéro par l'essence.
ESSENCE_COEF_MIN = 0.15

# --- Paramètres de l'indice cèpe -------------------------------------------
# L'indice combine humidité du sol, pluie récente et température, sur 0-100.
# Ces seuils sont un point de départ raisonnable, à recaler avec le terrain.

# SWI (indice d'humidité des sols, ~0 très sec à ~1 saturé).
SWI_MIN = 0.30      # en dessous : sol trop sec, pas de pousse
SWI_OPTIMAL = 0.65  # humidité idéale (humide sans excès)
SWI_EXCES = 0.95    # au-dessus : sol gorgé, on plafonne

# Cumul de pluie sur 15 jours (mm) : le déclencheur.
PLUIE_15J_MIN = 20      # seuil de déclenchement
PLUIE_15J_OPTIMAL = 60  # apport idéal

# Température moyenne (°C) : fenêtre de fructification du cèpe.
TEMP_MIN = 8        # trop froid en dessous
TEMP_OPT_BAS = 12   # début de plage optimale
TEMP_OPT_HAUT = 20  # fin de plage optimale
TEMP_MAX = 27       # trop chaud au-dessus

# Pondération des trois composantes (doit sommer à 1).
POIDS_HUMIDITE = 0.50   # SWI
POIDS_PLUIE = 0.30      # cumul 15 j
POIDS_TEMPERATURE = 0.20

# Décalage (lag) entre conditions favorables et pousse visible, en jours.
# Affiché à titre indicatif ; l'indice du jour anticipe déjà cette échéance.
LAG_JOURS_PLAINE = 10
LAG_JOURS_ALTITUDE = 15
ALTITUDE_SEUIL = 1000  # au-dessus, on applique le lag "altitude"

# --- Choc thermique (modulation dynamique par le REFROIDISSEMENT récent) ----
# Ce n'est pas le NIVEAU de température (déjà géré par la cloche score_temperature)
# mais sa DYNAMIQUE : un refroidissement net sur quelques jours est un déclencheur
# reconnu de la fructification (les premières nuits fraîches d'arrière-saison).
# On mesure le refroidissement R (°C) = moyenne de la température de la fenêtre de
# RÉFÉRENCE (jours -10..-4) MOINS celle de la fenêtre RÉCENTE (jours -3..0) :
#   R > 0  -> il a refroidi (favorable)     R < 0 -> il a réchauffé (défavorable)
# puis on module l'indice de la maille :  indice *= 1 + CHOC_K * score_choc(R),
# où score_choc ∈ [-1, +1] (rampe sur |R| entre CHOC_MIN et CHOC_OPT, signée).
# La modulation étant MULTIPLICATIVE, elle ne fait rien quand l'indice de base est
# déjà ~0 (sol sec) : un coup de froid sur sol sec ne crée pas de pousse.
CHOC_FENETRE_RECENTE = 4   # nb de jours "récents" (dont le jour courant) moyennés
CHOC_FENETRE_REF = 7       # nb de jours de la fenêtre de référence, juste avant
CHOC_MIN = 2.0             # °C : en dessous, refroidissement non significatif -> 0
CHOC_OPT = 6.0             # °C : refroidissement pleinement "déclencheur" -> 1
# Force de la modulation (± sur l'indice). Modérée : le choc AFFINE, il ne pilote
# pas. Le côté réchauffement pénalise plus doucement (évite de double-compter la
# cloche de température). À CALIBRER sur les observations de terrain.
CHOC_K = 0.20              # gain côté refroidissement (favorable)
CHOC_K_CHAUD = 0.10        # gain côté réchauffement (défavorable, plus doux)

# --- Versant / exposition (modulation dynamique de l'indice) ---------------
# Le versant (ubac/adret) n'est PAS un coefficient statique comme la géologie :
# son signe s'inverse avec le déficit du moment. Un versant nord (frais,
# humide) aide quand il fait sec/chaud ; un versant sud (chaud) aide quand il
# fait froid. On module donc l'indice de la maille par face de forêt :
#     mod = 1 + VERSANT_K * expo * stress          (indice_face = indice_maille * mod)
# où `expo` (statique, ~[-1,+1], + = nord) vient du MNT (cf. build_versant.py)
# et `stress` (dynamique, [-1,+1], + = sec/chaud) vient du SWI et de la
# température de la maille (cf. versant.py). Fichier de géométrie découpée :
VERSANT_GEOM_FILE = "data/versant_geom.json"   # statique (3 classes/maille/groupe)

# Force de la modulation. Volontairement modérée : le versant AFFINE l'indice,
# il ne le pilote pas. À CALIBRER sur les observations de terrain.
VERSANT_K = 0.40

# Classement d'un pixel du MNT en versant (seuils utilisés à la construction).
VERSANT_PENTE_MIN = 6.0      # en dessous : trop plat, versant sans effet -> neutre
VERSANT_NORTHNESS_SEUIL = 0.34  # |cos(orientation)| au-delà : nord (+) ou sud (-)

# Étiquettes des 3 classes de versant (clé -> libellé lisible).
VERSANT_CLASSES = {"nord": "nord (ubac)", "neutre": "neutre", "sud": "sud (adret)"}

# --- Construction du "stress" hydro-thermique de la maille ------------------
# stress = clamp( P_HYDRIQUE * dryness + P_THERMIQUE * chaleur , -1, +1 )
#   dryness = (SWI_OPTIMAL - swi) / (SWI_OPTIMAL - SWI_MIN)   (+ = sol sec)
#   chaleur = (temp - TEMP_MID) / TEMP_DEMI                   (+ = chaud)
VERSANT_TEMP_MID = 16.0    # centre de la plage optimale (≈ (12+20)/2)
VERSANT_TEMP_DEMI = 8.0    # demi-amplitude thermique de référence
VERSANT_POIDS_HYDRIQUE = 0.60
VERSANT_POIDS_THERMIQUE = 0.40

# Allègement de la géométrie versant (contours pixel 10 m -> page légère).
# Les contours issus du raster sont en "marches d'escalier" : on simplifie
# fortement (tolérance en mètres, L93) et on arrondit les coordonnées WGS84,
# invisibles à l'échelle départementale mais décisifs pour le poids du fichier.
VERSANT_SIMPLIFY_M = 120         # tolérance de simplification (m)
VERSANT_COORD_DECIMALES = 5      # arrondi des coordonnées WGS84 (~1 m)
VERSANT_AIRE_MIN_HA = 0.5        # on jette les faces < 0.5 ha (bruit)
