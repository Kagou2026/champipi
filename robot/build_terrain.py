"""Construction du masque terrain STATIQUE de la Lozère (à lancer une seule fois).

Pour chaque maille SIM située dans le département 48 :
  - géométrie et centroïde (depuis le WFS SIM) ;
  - classe géologique (acide / neutre / calcaire) via le BRGM ;
  - altitude (via Open-Meteo, sans clé) ;
  - coefficient terrain déduit de la géologie.

Résultat : data/terrain.json, réutilisé chaque jour par le robot.
Relancer uniquement si l'on veut affiner la classification.
"""
import json
import re
import time

import requests
from shapely.geometry import shape, Point

from config import (
    BRGM_WFS_URL, BRGM_WFS_TYPENAME, OPENMETEO_URL, DEPT48_GEOJSON_URL,
    TERRAIN_FILE, GEOLOGIE_COEFFICIENT, GEOLOGIE_MOTS_CLES,
)
from fetch_sim import fetch_sim_features, organiser_par_maille


def charger_contour_48():
    r = requests.get(DEPT48_GEOJSON_URL, timeout=60)
    r.raise_for_status()
    gj = r.json()
    # Le fichier est un Feature ou FeatureCollection ; on prend la géométrie.
    if gj.get("type") == "FeatureCollection":
        geom = gj["features"][0]["geometry"]
    elif gj.get("type") == "Feature":
        geom = gj["geometry"]
    else:
        geom = gj
    return shape(geom)


def classer_geologie(descr):
    """Classe une description lithologique BRGM en acide / neutre / calcaire."""
    d = (descr or "").lower()
    for classe in ("calcaire", "acide", "neutre"):
        for mot in GEOLOGIE_MOTS_CLES[classe]:
            if mot in d:
                return classe
    return "inconnu"


def geologie_au_point(lat, lon, timeout=60):
    """Interroge le BRGM (lithologie simplifiée) au point donné."""
    d = 0.005
    bbox = f"{lat - d},{lon - d},{lat + d},{lon + d},urn:ogc:def:crs:EPSG::4326"
    params = {
        "service": "wfs", "version": "2.0.0", "request": "GetFeature",
        "typeNames": BRGM_WFS_TYPENAME, "count": "1",
        "srsName": "urn:ogc:def:crs:EPSG::4326", "bbox": bbox,
    }
    try:
        r = requests.get(BRGM_WFS_URL, params=params, timeout=timeout)
        r.raise_for_status()
        m = re.search(r"<ms:DESCR>([^<]+)</ms:DESCR>", r.text)
        return m.group(1) if m else None
    except Exception as e:
        print(f"  ! BRGM échec ({lat},{lon}) : {e}")
        return None


def altitudes_par_lots(coords, lot=90):
    """Renvoie la liste des altitudes (m) pour une liste de (lat, lon)."""
    resultats = []
    for i in range(0, len(coords), lot):
        chunk = coords[i:i + lot]
        lats = ",".join(str(c[0]) for c in chunk)
        lons = ",".join(str(c[1]) for c in chunk)
        r = requests.get(OPENMETEO_URL.replace("/forecast", "/elevation"),
                         params={"latitude": lats, "longitude": lons}, timeout=60)
        r.raise_for_status()
        resultats.extend(r.json().get("elevation", []))
        time.sleep(0.5)
    return resultats


def main():
    print("1/4 Récupération de la grille SIM...")
    mailles = organiser_par_maille(fetch_sim_features())
    print(f"    {len(mailles)} mailles dans l'emprise brute")

    print("2/4 Clip au contour du département 48...")
    contour = charger_contour_48()
    dedans = {}
    for mid, m in mailles.items():
        if m["lat"] is None or m["lon"] is None:
            continue
        if contour.contains(Point(m["lon"], m["lat"])):
            dedans[mid] = m
    print(f"    {len(dedans)} mailles en Lozère")

    print("3/4 Altitudes (Open-Meteo)...")
    ids = list(dedans.keys())
    coords = [(dedans[i]["lat"], dedans[i]["lon"]) for i in ids]
    alts = altitudes_par_lots(coords)
    for i, mid in enumerate(ids):
        dedans[mid]["altitude"] = alts[i] if i < len(alts) else None

    print("4/4 Géologie (BRGM), une requête par maille...")
    cellules = []
    for n, mid in enumerate(ids, 1):
        m = dedans[mid]
        descr = geologie_au_point(m["lat"], m["lon"])
        classe = classer_geologie(descr)
        cellules.append({
            "maille_id": mid,
            "lat": m["lat"],
            "lon": m["lon"],
            "altitude": m["altitude"],
            "geologie_descr": descr,
            "geologie_classe": classe,
            "coef_terrain": GEOLOGIE_COEFFICIENT[classe],
            "geometry": m["geometry"],
        })
        if n % 10 == 0:
            print(f"    {n}/{len(ids)} mailles classées")
        time.sleep(0.3)

    with open(TERRAIN_FILE, "w", encoding="utf-8") as fp:
        json.dump({"cellules": cellules}, fp, ensure_ascii=False)

    # Petit récapitulatif
    from collections import Counter
    c = Counter(x["geologie_classe"] for x in cellules)
    print(f"\nTerrain écrit dans {TERRAIN_FILE} : {len(cellules)} mailles")
    print("Répartition géologique :", dict(c))


if __name__ == "__main__":
    main()
