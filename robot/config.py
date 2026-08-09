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

# Contour du département (GeoJSON communautaire, stable).
DEPT48_GEOJSON_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
    "departements/48-lozere/departement-48-lozere.geojson"
)

# --- Fichiers produits ------------------------------------------------------
TERRAIN_FILE = "data/terrain.json"        # statique, calculé une fois
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
