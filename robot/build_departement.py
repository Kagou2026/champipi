"""Génération des données statiques d'un département (hors versant).

Réutilise les fonctions du pipeline Lozère (build_terrain / foret /
build_foret_geom / backfill_hist) en les paramétrant par le registre
`config.DEPARTEMENTS`. Produit, pour un code donné :
  - data/terrain_<code>.json      (mailles + géologie + altitude + forêt/essence)
  - data/safran_grille_<code>.json (maille -> point grille SAFRAN Lambert-II)
  - data/foret_geom_<code>.json   (emprises forestières par maille/essence)
  - data/historique_<code>.json   (rejeu long SAFRAN 2022->)

Le VERSANT (data/versant_geom_<code>.json) n'est PAS produit ici : il demande le
pipeline MNT IGN (cache ~1 Go). Sans lui, run.py rend le département en repli
« forêt » (polygones colorés par l'indice de la maille, sans ubac/adret).

Usage :
    python robot/build_departement.py terrain 30
    python robot/build_departement.py grille 30
    python robot/build_departement.py foret 30
    python robot/build_departement.py hist 30 07      # moisson SAFRAN commune
    python robot/build_departement.py all 30          # terrain+grille+foret (pas hist)
"""
import json
import os
import sys
import time

import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(__file__))
from config import (DEPARTEMENTS, GEOLOGIE_COEFFICIENT)
from fetch_sim import fetch_sim_features, organiser_par_maille
from build_terrain import (classer_geologie, geologie_au_point, altitudes_par_lots)
from foret import enrichir_cellules, _dissoudre
from build_foret_geom import collecter_polygones, maille_dominante
import backfill_hist as bh

_TO_2154 = Transformer.from_crs(4326, 2154, always_xy=True).transform
_TO_27572 = Transformer.from_crs(4326, 27572, always_xy=True).transform


def _ctx(code):
    ctx = DEPARTEMENTS.get(code)
    if not ctx:
        raise SystemExit(f"Département inconnu : {code}")
    return ctx


def charger_contour(url):
    r = requests.get(url, timeout=60); r.raise_for_status()
    gj = r.json()
    if gj.get("type") == "FeatureCollection":
        geom = gj["features"][0]["geometry"]
    elif gj.get("type") == "Feature":
        geom = gj["geometry"]
    else:
        geom = gj
    return shape(geom)


# --------------------------------------------------------------------------- #
def build_terrain(code):
    ctx = _ctx(code)
    print(f"[{code} {ctx['nom']}] TERRAIN")
    print("  1/5 grille SIM (WFS)…")
    mailles = organiser_par_maille(fetch_sim_features(bbox_l93=ctx["bbox_l93"]))
    print(f"      {len(mailles)} mailles dans l'emprise brute")
    print("  2/5 clip au contour…")
    contour = charger_contour(ctx["contour"])
    dedans = {mid: m for mid, m in mailles.items()
              if m["lat"] is not None and contour.contains(Point(m["lon"], m["lat"]))}
    print(f"      {len(dedans)} mailles dans le {ctx['nom']}")
    ids = list(dedans.keys())
    print("  3/5 altitudes (Open-Meteo)…")
    alts = altitudes_par_lots([(dedans[i]["lat"], dedans[i]["lon"]) for i in ids])
    for i, mid in enumerate(ids):
        dedans[mid]["altitude"] = alts[i] if i < len(alts) else None
    print("  4/5 géologie (BRGM)…")
    cellules = []
    for n, mid in enumerate(ids, 1):
        m = dedans[mid]
        descr = geologie_au_point(m["lat"], m["lon"])
        classe = classer_geologie(descr)
        cellules.append({
            "maille_id": mid, "lat": m["lat"], "lon": m["lon"],
            "altitude": m["altitude"], "geologie_descr": descr,
            "geologie_classe": classe, "coef_terrain": GEOLOGIE_COEFFICIENT[classe],
            "geometry": m["geometry"]})
        if n % 20 == 0:
            print(f"      {n}/{len(ids)}")
        time.sleep(0.25)
    print("  5/5 boisement + essence (BD Forêt)…")
    enrichir_cellules(cellules)
    json.dump({"cellules": cellules}, open(ctx["terrain"], "w", encoding="utf-8"),
              ensure_ascii=False)
    from collections import Counter
    print(f"  -> {ctx['terrain']} : {len(cellules)} mailles, "
          f"géol {dict(Counter(c['geologie_classe'] for c in cellules))}")


def build_grille(code):
    """maille -> [lambx, lamby] (point grille SAFRAN, Lambert-II étendu hm).

    Le centroïde de la maille SIM reprojeté en EPSG:27572 puis divisé par 100
    tombe EXACTEMENT sur le point de grille SAFRAN (vérifié : écart nul)."""
    ctx = _ctx(code)
    cells = json.load(open(ctx["terrain"], encoding="utf-8"))["cellules"]
    grille = {}
    for c in cells:
        g = shape(c["geometry"])
        x, y = _TO_27572(g.centroid.x, g.centroid.y)
        grille[c["maille_id"]] = [int(round(x / 100.0)), int(round(y / 100.0))]
    json.dump(grille, open(ctx["grille"], "w", encoding="utf-8"))
    print(f"[{code}] GRILLE -> {ctx['grille']} : {len(grille)} points")


def build_foret(code):
    ctx = _ctx(code)
    print(f"[{code} {ctx['nom']}] FORÊT (emprises)")
    cells = json.load(open(ctx["terrain"], encoding="utf-8"))["cellules"]
    ids = [c["maille_id"] for c in cells]
    geoms = [shape(c["geometry"]).buffer(0) for c in cells]
    tree = STRtree(geoms)
    par_id = collecter_polygones(cells)
    print(f"  {len(par_id)} polygones forêt uniques")
    par_maille = {}
    orph = 0
    for grp, geom in par_id.values():
        mid = maille_dominante(geom, ids, geoms, tree)
        if mid is None:
            orph += 1; continue
        par_maille.setdefault(mid, {}).setdefault(grp, []).append(geom)
    resultat = {}
    for mid, groupes in par_maille.items():
        out = {}
        for grp, polys in groupes.items():
            g = _dissoudre(polys)
            if g is not None:
                out[grp] = g
        if out:
            resultat[mid] = out
    json.dump(resultat, open(ctx["foret_geom"], "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    mo = os.path.getsize(ctx["foret_geom"]) / 1e6
    print(f"  -> {ctx['foret_geom']} : {len(resultat)} mailles, {mo:.2f} Mo, "
          f"{orph} polygones hors mailles")


def build_hist(codes):
    """Moisson SAFRAN COMMUNE à plusieurs départements (évite de re-streamer les
    gros CSV nationaux), puis écriture d'un historique par département."""
    ctxs = {c: _ctx(c) for c in codes}
    # rev commun : (lambx,lamby) -> liste de (code, maille_id)
    rev = {}
    coef = {c: {} for c in codes}
    for c in codes:
        grille = json.load(open(ctxs[c]["grille"], encoding="utf-8"))
        cells = json.load(open(ctxs[c]["terrain"], encoding="utf-8"))["cellules"]
        for cell in cells:
            coef[c][cell["maille_id"]] = (cell["coef_terrain"]
                * cell.get("coef_foret", 1.0) * cell.get("coef_essence", 1.0))
        for mid, (lx, ly) in grille.items():
            rev.setdefault((lx, ly), []).append((c, mid))
    print(f"[hist {'+'.join(codes)}] moisson SAFRAN commune "
          f"({len(rev)} points grille)…")
    # rev_simple pour backfill.moissonner : (lambx,lamby) -> un id fictif ; on
    # récupère les données par point puis on redistribue à chaque (code, maille).
    rev_simple = {k: k for k in rev}          # clé = (lx,ly), valeur = (lx,ly)
    data_pt = bh.moissonner(bh.lister_sources(), rev_simple)   # {(lx,ly): {date:...}}
    for c in codes:
        ctx = ctxs[c]
        # reconstruire data par maille de ce département
        data_m = {}
        grille = json.load(open(ctx["grille"], encoding="utf-8"))
        for mid, (lx, ly) in grille.items():
            d = data_pt.get((lx, ly))
            if d:
                data_m[mid] = d
        par = bh.series_par_maille(data_m, coef[c], bh.DEBUT)
        dates, mailles = bh.aligner(par)
        payload = {"debut": bh.DEBUT, "fin": dates[-1] if dates else None,
                   "n_jours": len(dates), "dates": dates, "mailles": mailles}
        json.dump(payload, open(ctx["hist"], "w", encoding="utf-8"),
                  separators=(",", ":"))
        mo = os.path.getsize(ctx["hist"]) / 1e6
        print(f"  -> {ctx['hist']} : {len(dates)} j, {len(mailles)} mailles, {mo:.1f} Mo")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); raise SystemExit(1)
    cmd = sys.argv[1]; codes = sys.argv[2:]
    if cmd == "terrain":
        for c in codes: build_terrain(c)
    elif cmd == "grille":
        for c in codes: build_grille(c)
    elif cmd == "foret":
        for c in codes: build_foret(c)
    elif cmd == "hist":
        build_hist(codes)
    elif cmd == "all":
        for c in codes:
            build_terrain(c); build_grille(c); build_foret(c)
    else:
        print(__doc__); raise SystemExit(1)
