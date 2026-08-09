"""Estimation du boisement ET de l'essence d'une maille via BD Forêt V2 (IGN).

Pour une maille (géométrie WGS84), on interroge la BD Forêt V2 sur la bbox
de la maille, on ne garde que les polygones forestiers (pondérés par classe
de structure `tfv_g11`), on calcule la surface pondérée réellement DANS la
maille (intersection shapely), et on en déduit :

  - taux_boise   : surface forestière pondérée / surface maille (0..1)
  - coef_foret   : multiplicateur de l'indice lié à la COUVERTURE (rampe saturée)
  - coef_essence : multiplicateur de l'indice lié à l'ESPÈCE hôte du cèpe
                   (chêne/hêtre/châtaignier/épicéa/pin = 1 ; peuplier... = bas)
  - essence_repartition : part de surface boisée par groupe d'essence (stat)

L'essence sert aussi au rendu : `emprises_par_groupe` renvoie la géométrie
forestière dissoute PAR GROUPE d'essence, ce qui permet à la carte de filtrer
(cases à cocher) et de teinter plus fort les essences principales.

Les taux sont des ratios de surfaces calculés en WGS84 : la légère distorsion
en longitude s'annule au numérateur/dénominateur (même maille).
"""
import time
import unicodedata

import requests
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

from config import (
    BDFORET_WFS_URL, BDFORET_WFS_TYPENAME,
    FORET_POIDS, FORET_SATURATION, FORET_COEF_MIN,
    FORET_SIMPLIFY_TOL, FORET_COORD_DECIMALES,
    ESSENCE_GROUPES, ESSENCE_VERS_GROUPE, ESSENCE_COEF_MIN,
)


def _norm(s):
    """minuscule + sans accent, pour matcher les valeurs `essence`."""
    s = (s or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def groupe_essence(props):
    """Clé de groupe d'essence d'un polygone BD Forêt (défaut : 'reste')."""
    return ESSENCE_VERS_GROUPE.get(_norm(props.get("essence")), "reste")


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
    """Calcule taux_boise, coef_foret, coef_essence et la répartition d'essence.

    `geometry` : dict GeoJSON (WGS84) de la maille (Polygon/MultiPolygon).
    Retourne un dict, ou des valeurs neutres si la requête échoue (on ne
    pénalise pas une maille faute de donnée).

    coef_essence = moyenne du poids hôte des polygones boisés, pondérée par
    leur surface DANS la maille × leur poids de structure (une grande forêt
    fermée de chênes pèse plus qu'un petit bosquet ouvert de peupliers).
    """
    maille = shape(geometry)
    aire_maille = maille.area
    if aire_maille <= 0:
        return _resultat_neutre("aire maille nulle")

    try:
        feats = fetch_bdforet(maille)
    except Exception as e:  # réseau/serveur : neutre plutôt que faux zéro
        return _resultat_neutre(f"BD Forêt échec : {e}")

    aire_ponderee = 0.0        # pour taux_boise (structure)
    aire_structuree = 0.0      # dénominateur du coef_essence
    aire_hote = 0.0            # numérateur du coef_essence
    repartition = {}           # part de surface boisée brute par groupe
    for f in feats:
        props = f.get("properties") or {}
        poids_struct = FORET_POIDS.get(props.get("tfv_g11"), 0.0)
        if poids_struct <= 0:
            continue
        try:
            poly = shape(f["geometry"])
        except Exception:
            continue
        inter = poly.intersection(maille).area  # part réellement dans la maille
        if inter <= 0:
            continue
        grp = groupe_essence(props)
        poids_hote = ESSENCE_GROUPES[grp]["poids"]
        aire_ponderee += inter * poids_struct
        aire_structuree += inter * poids_struct
        aire_hote += inter * poids_struct * poids_hote
        repartition[grp] = repartition.get(grp, 0.0) + inter / aire_maille

    taux = min(aire_ponderee / aire_maille, 1.0)
    coef = max(min(taux / FORET_SATURATION, 1.0), FORET_COEF_MIN)
    if aire_structuree > 0:
        coef_ess = max(aire_hote / aire_structuree, ESSENCE_COEF_MIN)
    else:
        coef_ess = 1.0  # pas de forêt exploitable : essence neutre
    # essence dominante (plus grande part de surface boisée)
    dominante = max(repartition, key=repartition.get) if repartition else None
    return {
        "taux_boise": round(taux, 3),
        "coef_foret": round(coef, 3),
        "coef_essence": round(coef_ess, 3),
        "essence_repartition": {k: round(v, 3) for k, v in repartition.items()},
        "essence_dominante": dominante,
        "foret_note": None,
    }


def _resultat_neutre(note):
    """Valeurs neutres (coef 1) : on ne pénalise pas sans donnée fiable."""
    return {"taux_boise": None, "coef_foret": 1.0, "coef_essence": 1.0,
            "essence_repartition": {}, "essence_dominante": None,
            "foret_note": note}


def _polygones(geom):
    """Extrait les parties surfaciques (Polygon) d'une géométrie shapely.

    L'intersection polygone∩maille peut renvoyer des lignes/points (contacts)
    ou une GeometryCollection ; on ne garde que le surfacique.
    """
    if geom.is_empty:
        return []
    gt = geom.geom_type
    if gt == "Polygon":
        return [geom]
    if gt == "MultiPolygon":
        return list(geom.geoms)
    if gt == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_polygones(g))
        return out
    return []  # LineString, Point, etc.


def _arrondir(o, nd):
    """Arrondit récursivement les coordonnées d'une géométrie GeoJSON."""
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)):
            return [round(o[0], nd), round(o[1], nd)]
        return [_arrondir(x, nd) for x in o]
    return o


def _dissoudre(polys):
    """Union + simplification + arrondi d'une liste de polygones. None si vide."""
    if not polys:
        return None
    union = unary_union(polys).simplify(FORET_SIMPLIFY_TOL, preserve_topology=True)
    union = union.buffer(0)  # répare d'éventuelles auto-intersections
    if union.is_empty:
        return None
    g = mapping(union)
    if "coordinates" not in g:  # GeometryCollection résiduelle : on filtre
        union = unary_union(_polygones(union))
        g = mapping(union)
        if "coordinates" not in g:
            return None
    g["coordinates"] = _arrondir(g["coordinates"], FORET_COORD_DECIMALES)
    return g


def emprises_par_groupe(geometry, timeout=120):
    """Renvoie {groupe_essence: géométrie GeoJSON} des forêts DANS la maille.

    Chaque polygone boisé (poids de structure > 0) est classé dans son groupe
    d'essence, puis on dissout par groupe. Dictionnaire vide si la maille ne
    contient aucune forêt exploitable (causse, ville, champs...).
    """
    maille = shape(geometry)
    try:
        feats = fetch_bdforet(maille, timeout=timeout)
    except Exception as e:
        print(f"  ! BD Forêt échec emprise : {e}")
        return {}

    par_groupe = {}
    for f in feats:
        props = f.get("properties") or {}
        if FORET_POIDS.get(props.get("tfv_g11"), 0.0) <= 0:
            continue
        try:
            inter = shape(f["geometry"]).intersection(maille)
        except Exception:
            continue
        parts = _polygones(inter)
        if not parts:
            continue
        par_groupe.setdefault(groupe_essence(props), []).extend(parts)

    out = {}
    for grp, polys in par_groupe.items():
        g = _dissoudre(polys)
        if g is not None:
            out[grp] = g
    return out


def enrichir_cellules(cellules, pause=0.4, log=print):
    """Ajoute taux_boise / coef_foret / coef_essence à chaque cellule (en place)."""
    n = len(cellules)
    for i, cell in enumerate(cellules, 1):
        res = taux_boisement(cell["geometry"])
        cell.update(res)
        if log and (i % 5 == 0 or i == n):
            log(f"    {i}/{n} mailles boisement+essence calculé")
        time.sleep(pause)
    return cellules
