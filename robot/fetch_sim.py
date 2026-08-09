"""Récupération des données SIM (SAFRAN-ISBA) via le WFS de la DREAL Bretagne.

Chaque maille de 8 km porte, pour chaque jour disponible (~15 j d'historique) :
  - swi_courant            : indice d'humidité des sols (0 sec -> ~1 saturé)
  - anomalie_swi           : écart à la normale 1991-2020
  - etr_courante           : évapotranspiration réelle (mm)
  - cumul_precipitations_15j : pluie cumulée sur 15 jours (mm)
  - date + géométrie (Lambert-93)

ATTENTION : les champs `lat`/`lon` renvoyés par ce WFS sont ERRONÉS
(coordonnées compressées). On recalcule donc systématiquement le centroïde
à partir de la géométrie (Lambert-93 -> WGS84 via pyproj). Ne pas utiliser
les propriétés lat/lon brutes.

Accès libre (licence ouverte), sans clé.
"""
import requests
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform
from pyproj import Transformer

from config import SIM_WFS_URL, SIM_WFS_TYPENAME, LOZERE_BBOX_L93

# Transformateur Lambert-93 (EPSG:2154) -> WGS84 (EPSG:4326), ordre lon/lat.
_TO_WGS84 = Transformer.from_crs(2154, 4326, always_xy=True)


def _l93_to_wgs84_geom(geom_l93):
    """Reprojette une géométrie GeoJSON de Lambert-93 vers WGS84 (lon/lat)."""
    g = shape(geom_l93)
    g_wgs = shp_transform(lambda x, y, z=None: _TO_WGS84.transform(x, y), g)
    return mapping(g_wgs), g_wgs.centroid


def fetch_sim_features(bbox_l93=LOZERE_BBOX_L93, timeout=120):
    """Renvoie la liste brute des features GeoJSON du WFS pour l'emprise donnée."""
    minx, miny, maxx, maxy = bbox_l93
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": SIM_WFS_TYPENAME,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "urn:ogc:def:crs:EPSG::2154",
        "BBOX": f"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::2154",
        "COUNT": "50000",
    }
    r = requests.get(SIM_WFS_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json().get("features", [])


def _clean_date(d):
    """'2026-08-08Z' -> '2026-08-08'."""
    return (d or "").replace("Z", "").strip()


def organiser_par_maille(features):
    """Regroupe les features par maille, avec coordonnées recalculées.

    Renvoie un dict : maille_id -> {
        'lat', 'lon'          : centroïde WGS84 (recalculé, fiable),
        'geometry'            : polygone WGS84 (lon/lat),
        'historique'          : [ {date, swi, anomalie_swi, etr, pluie_15j}, ... ]
    }
    """
    mailles = {}
    for f in features:
        p = f.get("properties", {})
        mid = str(p.get("id_maille_historique"))
        if mid not in mailles:
            geom_wgs, centroid = _l93_to_wgs84_geom(f.get("geometry"))
            mailles[mid] = {
                "lat": round(centroid.y, 5),
                "lon": round(centroid.x, 5),
                "geometry": geom_wgs,
                "historique": [],
            }
        mailles[mid]["historique"].append({
            "date": _clean_date(p.get("date")),
            "swi": p.get("swi_courant"),
            "anomalie_swi": p.get("anomalie_swi"),
            "etr": p.get("etr_courante"),
            "pluie_15j": p.get("cumul_precipitations_15j"),
        })
    for m in mailles.values():
        m["historique"].sort(key=lambda h: h["date"])
    return mailles


if __name__ == "__main__":
    feats = fetch_sim_features()
    mailles = organiser_par_maille(feats)
    lats = [m["lat"] for m in mailles.values()]
    print(f"{len(feats)} features -> {len(mailles)} mailles")
    print(f"latitudes {min(lats):.2f} -> {max(lats):.2f}")
    exemple = next(iter(mailles.values()))
    print("Exemple :", exemple["lat"], exemple["lon"],
          "| jours :", len(exemple["historique"]), "| dernier :",
          exemple["historique"][-1])
