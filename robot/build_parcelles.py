"""Découpe des faces versant en PARCELLES individuelles + altitude propre (IGN).

Entrée  : data/versant_geom_<code>.json   {mid:{grp:{classe:{expo,geom}}}}
Sortie  : data/parcelles_geom_<code>.json  {genere_le, mailles:{mid:{grp:{classe:[
              {expo, alt, amin, amax, geom(Polygon)}, ...]}}}}

Chaque face (maille×essence×versant) est un MultiPolygon = plusieurs parcelles
dispersées cousues ensemble. On les ÉCLATE en polygones individuels ; pour
chacun on échantillonne quelques points intérieurs et on interroge l'API
altimétrique IGN (RGE ALTI) pour obtenir son altitude moyenne / mini / maxi.

Usage : python robot/build_parcelles.py 30   (ou 07, 48)
"""
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from shapely.geometry import shape, Point, Polygon, MultiPolygon

IGN = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
RESSOURCE = "ign_rge_alti_wld"
AIRE_MIN_HA = 3.0          # on ne garde/échantillonne que les parcelles servies (≥3 ha)
POINTS_MAX = 12            # points d'échantillonnage max par parcelle
BATCH = 150               # points par requête IGN
THREADS = 8
NODATA = -1000.0          # sentinelle IGN (mer/hors couverture) → ignorée


def parts_polygones(geom):
    """Liste de shapely.Polygon (extérieur, trous ignorés pour l'échantillonnage)."""
    g = shape(geom)
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return []


def echantillon(poly, f2ha):
    """Points (lon,lat) intérieurs représentatifs d'un polygone."""
    rp = poly.representative_point()
    pts = [(rp.x, rp.y)]
    minx, miny, maxx, maxy = poly.bounds
    # pas de grille visant ~POINTS_MAX points dans l'aire
    aire = poly.area
    if aire <= 0:
        return pts
    step = math.sqrt(aire / POINTS_MAX)
    if step <= 0:
        return pts
    y = miny + step / 2
    while y <= maxy and len(pts) < POINTS_MAX:
        x = minx + step / 2
        while x <= maxx and len(pts) < POINTS_MAX:
            if poly.contains(Point(x, y)):
                pts.append((round(x, 6), round(y, 6)))
            x += step
        y += step
    return pts


def ign_batch(points):
    """Renvoie la liste d'altitudes (même ordre) pour une liste de (lon,lat)."""
    lon = "|".join(f"{p[0]:.6f}" for p in points)
    lat = "|".join(f"{p[1]:.6f}" for p in points)
    params = {"resource": RESSOURCE, "delimiter": "|",
              "lon": lon, "lat": lat, "zonly": "true", "indent": "false"}
    for essai in range(4):
        try:
            r = requests.get(IGN, params=params, timeout=40)
            if r.status_code == 200:
                return r.json().get("elevations", [])
        except Exception:
            pass
        time.sleep(1.0 + essai)
    return [None] * len(points)


def build(code):
    src = f"data/versant_geom_{'' if code == '48' else code + '_'}".replace("_48_", "")
    # chemins explicites (le 48 n'a pas de suffixe)
    src = "data/versant_geom.json" if code == "48" else f"data/versant_geom_{code}.json"
    out = "data/parcelles_geom.json" if code == "48" else f"data/parcelles_geom_{code}.json"
    d = json.load(open(src, encoding="utf-8"))

    # échelle aire : lat moyenne du département (approx via 1er point trouvé)
    lat0 = 44.3
    for gr in d.values():
        for cls in gr.values():
            for o in cls.values():
                pl = parts_polygones(o["geom"])
                if pl:
                    lat0 = pl[0].representative_point().y
                    break
            break
        break
    f2ha = (111000 * math.cos(math.radians(lat0)) * 111000) / 1e4

    # 1) éclatement + échantillonnage (points à interroger)
    parcelles = []           # {mid,grp,classe,expo,geom, pt_slice}
    tous_points = []
    n_faces = 0
    for mid, gr in d.items():
        for grp, cls in gr.items():
            for classe, o in cls.items():
                n_faces += 1
                expo = o.get("expo")
                for poly in parts_polygones(o["geom"]):
                    if poly.area * f2ha < AIRE_MIN_HA:
                        continue
                    pts = echantillon(poly, f2ha)
                    i0 = len(tous_points)
                    tous_points.extend(pts)
                    parcelles.append({
                        "mid": mid, "grp": grp, "classe": classe, "expo": expo,
                        "geom": poly, "sl": (i0, i0 + len(pts))})
    print(f"[{code}] {n_faces} faces → {len(parcelles)} parcelles ≥{AIRE_MIN_HA} ha, "
          f"{len(tous_points)} points IGN", flush=True)

    # 2) requêtes IGN par lots, en parallèle
    lots = [tous_points[i:i + BATCH] for i in range(0, len(tous_points), BATCH)]
    alts = [None] * len(tous_points)
    fait = [0]

    def run_lot(idx):
        lot = lots[idx]
        res = ign_batch(lot)
        base = idx * BATCH
        for j, v in enumerate(res):
            alts[base + j] = v
        fait[0] += 1
        if fait[0] % 20 == 0:
            print(f"    IGN {fait[0]}/{len(lots)} lots", flush=True)

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        list(ex.map(run_lot, range(len(lots))))

    # 3) agrégation alt par parcelle + structure de sortie
    res = {}
    nd = 5   # décimales coords (≈1 m), aligné VERSANT_COORD_DECIMALES
    gardees = 0
    for pc in parcelles:
        i0, i1 = pc["sl"]
        vals = [v for v in alts[i0:i1] if v is not None and v > NODATA]
        if not vals:
            continue
        amoy = round(sum(vals) / len(vals))
        amin = round(min(vals))
        amax = round(max(vals))
        geo = pc["geom"].__geo_interface__
        # arrondi coords
        coords = [[[round(x, nd), round(y, nd)] for x, y in ring]
                  for ring in geo["coordinates"]]
        part = {"expo": pc["expo"], "alt": amoy, "amin": amin, "amax": amax,
                "geom": {"type": "Polygon", "coordinates": coords}}
        res.setdefault(pc["mid"], {}).setdefault(pc["grp"], {}) \
           .setdefault(pc["classe"], []).append(part)
        gardees += 1

    payload = {"genere_le": time.strftime("%Y-%m-%d %H:%M"),
               "aire_min_ha": AIRE_MIN_HA, "mailles": res}
    json.dump(payload, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    import os
    mo = os.path.getsize(out) / 1e6
    print(f"[{code}] -> {out} : {gardees} parcelles avec altitude, {mo:.2f} Mo", flush=True)


if __name__ == "__main__":
    for c in (sys.argv[1:] or ["30"]):
        build(c)
