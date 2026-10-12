"""Contours simplifiés des départements du registre -> data/contours_depts.json.

Pourquoi : la page affiche en léger le contour des départements NON
sélectionnés (cliquer dessus les active) et doit le faire hors-ligne, en forêt.
Les contours sont donc EMBARQUÉS dans le signal inline (emit.py), pas récupérés
à l'exécution. Ce script tourne une fois (ou quand on ajoute un département) :
    python robot/build_contours.py            # tous les DEPARTEMENTS du registre
    python robot/build_contours.py 48 30      # seulement ceux-là (fusionné)

Source : france-geojson (gregoiredavid), même URL que `config.DEPARTEMENTS[..]["contour"]`.
Simplification : Douglas-Peucker (tolérance TOL degrés ≈ 300 m) + arrondi 3
décimales (~100 m). ~100-250 points par département -> ~5-10 Ko le tout.
Format : {code: [[ [lon,lat], ... ], ...]}   (une liste d'anneaux extérieurs ;
les multipolygones donnent plusieurs anneaux).
"""
import json, os, sys, time
import requests

from config import DEPARTEMENTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "contours_depts.json")
TOL = 0.003


def _dp(points, tol):
    """Douglas-Peucker itératif sur une liste de (x, y)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]; bx, by = points[b]
        dx, dy = bx - ax, by - ay
        n2 = dx * dx + dy * dy
        dmax, imax = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if n2 == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n2))
                d = ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5
            if d > dmax:
                dmax, imax = d, i
        if dmax > tol and imax > 0:
            keep[imax] = True
            stack.append((a, imax)); stack.append((imax, b))
    return [p for p, k in zip(points, keep) if k]


def anneaux(geom):
    """Anneaux extérieurs d'un Polygon / MultiPolygon GeoJSON."""
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        raise ValueError(geom["type"])
    return [p[0] for p in polys]


def contour_simplifie(url):
    for essai in range(5):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:  # réseau capricieux : on réessaie
            if essai == 4:
                raise
            time.sleep(3)
    gj = r.json()
    geom = gj["geometry"] if gj.get("type") == "Feature" else gj["features"][0]["geometry"]
    out = []
    for ring in anneaux(geom):
        pts = [(float(x), float(y)) for x, y in ring]
        simp = _dp(pts, TOL)
        if len(simp) < 4:
            continue
        out.append([[round(x, 3), round(y, 3)] for x, y in simp])
    return out


def main(codes):
    data = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fp:
            data = json.load(fp)
    for code in codes:
        d = DEPARTEMENTS[code]
        rings = contour_simplifie(d["contour"])
        data[code] = rings
        print(f"{code} {d['nom']}: {len(rings)} anneau(x), {sum(len(r) for r in rings)} points")
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(data, fp, separators=(",", ":"))
    print(f"-> {OUT} ({os.path.getsize(OUT)} octets)")


if __name__ == "__main__":
    main(sys.argv[1:] or list(DEPARTEMENTS))
