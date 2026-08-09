"""Estimation du boisement d'une maille via BD Forêt V2 (IGN).

Pour une maille (géométrie WGS84), on interroge la BD Forêt V2 sur la bbox
de la maille, on ne garde que les polygones forestiers (pondérés par classe),
on calcule la surface pondérée réellement DANS la maille (intersection
shapely), et on en déduit :

  - taux_boise  : surface forestière pondérée / surface maille (0..1)
  - coef_foret  : multiplicateur de l'indice, rampe saturée (cf. config)

Le taux est un ratio de surfaces calculé en WGS84 : la légère distorsion en
longitude s'annule au numérateur/dénominateur (même maille), donc inutile de
reprojeter en Lambert-93 pour un simple rapport.
"""
import time

import requests
from shapely.geometry import shape

from config import (
    BDFORET_WFS_URL, BDFORET_WFS_TYPENAME,
    FORET_POIDS, FORET_SATURATION, FORET_COEF_MIN,
)


def _bbox_maille(geom):
    """(lat_min, lon_min, lat_max, lon_max) de la géométrie de la maille."""
    lon_min, lat_min, lon_max, lat_max = geom.bounds
    return lat_min, lon_min, lat_max, lon_max


def fetch_bdforet(geom, timeout=120, count=6000):
    """Renvoie les features BD Forêt V2 intersectant la bbox de la maille."""
    lat_min, lon_min, lat_max, lon_max = _bbox_maille(geom)
    params = {
        "SERVICE": "WFS", "REQUEST": "GetFeature", "VERSION": "2.0.0",
        "TYPENAMES": BDFORET_WFS_TYPENAME,
        "SRSNAME": "urn:ogc:def:crs:EPSG::4326",
        "OUTPUTFORMAT": "application/json",
        "COUNT": str(count),
        "bbox": f"{lat_min},{lon_min},{lat_max},{lon_max},"
                f"urn:ogc:def:crs:EPSG::4326",
    }
    r = requests.get(BDFORET_WFS_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json().get("features", [])


def taux_boisement(geometry):
    """Calcule taux_boise, coef_foret et une répartition par classe.

    `geometry` : dict GeoJSON (WGS84) de la maille (Polygon/MultiPolygon).
    Retourne un dict, ou des valeurs neutres si la requête échoue (on ne
    pénalise pas une maille faute de donnée).
    """
    maille = shape(geometry)
    aire_maille = maille.area
    if aire_maille <= 0:
        return _resultat_neutre("aire maille nulle")

    try:
        feats = fetch_bdforet(maille)
    except Exception as e:  # réseau/serveur : neutre plutôt que faux zéro
        return _resultat_neutre(f"BD Forêt échec : {e}")

    aire_ponderee = 0.0
    repartition = {}
    for f in feats:
        classe = (f.get("properties") or {}).get("tfv_g11")
        poids = FORET_POIDS.get(classe, 0.0)
        if poids <= 0:
            continue
        try:
            poly = shape(f["geometry"])
        except Exception:
            continue
        inter = poly.intersection(maille).area  # part réellement dans la maille
        if inter <= 0:
            continue
        aire_ponderee += inter * poids
        repartition[classe] = repartition.get(classe, 0.0) + inter / aire_maille

    taux = min(aire_ponderee / aire_maille, 1.0)
    coef = max(min(taux / FORET_SATURATION, 1.0), FORET_COEF_MIN)
    return {
        "taux_boise": round(taux, 3),
        "coef_foret": round(coef, 3),
        "foret_repartition": {k: round(v, 3) for k, v in repartition.items()},
        "foret_note": None,
    }


def _resultat_neutre(note):
    """Valeurs neutres (coef 1) : on ne pénalise pas sans donnée fiable."""
    return {"taux_boise": None, "coef_foret": 1.0,
            "foret_repartition": {}, "foret_note": note}


def enrichir_cellules(cellules, pause=0.4, log=print):
    """Ajoute taux_boise / coef_foret à chaque cellule (modifie en place)."""
    n = len(cellules)
    for i, cell in enumerate(cellules, 1):
        res = taux_boisement(cell["geometry"])
        cell.update(res)
        if log and (i % 5 == 0 or i == n):
            log(f"    {i}/{n} mailles boisement calculé")
        time.sleep(pause)
    return cellules
