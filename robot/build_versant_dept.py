"""Calcul du versant (ubac/adret) d'un département — version paramétrée.

Même algorithme que build_versant.py (Lozère) mais la grille, le cache, le
terrain et la sortie sont ceux du département passé en argument (registre
config.DEPARTEMENTS + data/_versant_cache_<code>/grid.json produit par build_mnt).

Entrées (data/_versant_cache_<code>/) : grid.json, slope10.tif, north10.tif,
foret.pkl. Sortie : data/versant_geom_<code>.json.

Usage :
    python robot/build_versant_dept.py 07 bands 0 20
    python robot/build_versant_dept.py 07 assemble
"""
import os
import sys
import json
import glob
import pickle

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes, sieve
from rasterio.windows import Window
from rasterio.transform import from_origin
from shapely.geometry import shape, mapping, MultiPolygon
from shapely.ops import transform as shp_transform, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    DEPARTEMENTS, ESSENCE_ORDRE,
    VERSANT_PENTE_MIN, VERSANT_NORTHNESS_SEUIL,
    VERSANT_SIMPLIFY_M, VERSANT_COORD_DECIMALES, VERSANT_AIRE_MIN_HA,
)

CLASS_KEY = {1: "nord", 2: "sud", 3: "neutre"}
GROUP_IDX = {g: i for i, g in enumerate(ESSENCE_ORDRE)}
to84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True).transform
to93 = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform


class Ctx:
    def __init__(self, code):
        self.code = code
        self.d = DEPARTEMENTS[code]
        self.cache = f"data/_versant_cache_{code}"
        self.band_dir = f"{self.cache}/_bands"
        g = json.load(open(f"{self.cache}/grid.json"))
        self.X0 = g["X0"]; self.Y_TOP = g["Y_TOP"]
        self.W = g["W"]; self.H = g["H"]; self.RES = g["RES"]
        self.SB = 1024; self.HALO = 640


def _round(o, nd):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, (list, tuple)):
        return [_round(x, nd) for x in o]
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    return o


def boxmean(a, r=5):
    a = a.astype("float64"); Hh, Ww = a.shape
    S = np.zeros((Hh + 1, Ww + 1)); S[1:, 1:] = a.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(Hh) - r, 0, Hh); y1 = np.clip(np.arange(Hh) + r + 1, 0, Hh)
    x0 = np.clip(np.arange(Ww) - r, 0, Ww); x1 = np.clip(np.arange(Ww) + r + 1, 0, Ww)
    Y0, Y1 = y0[:, None], y1[:, None]; X0_, X1 = x0[None, :], x1[None, :]
    tot = S[Y1, X1] - S[Y0, X1] - S[Y1, X0_] + S[Y0, X0_]
    return (tot / ((Y1 - Y0) * (X1 - X0_))).astype("float32")


def charger_forets_taggees(ctx):
    cells = json.load(open(ctx.d["terrain"], encoding="utf-8"))["cellules"]
    maille_ids = [c["maille_id"] for c in cells]
    mgeoms93 = [shp_transform(to93, shape(c["geometry"])).buffer(0) for c in cells]
    tree = STRtree(mgeoms93)
    ford = pickle.load(open(f"{ctx.cache}/foret.pkl", "rb"))
    forets = []
    for grp, _ess, g93 in ford.values():
        gi = GROUP_IDX.get(grp)
        if gi is None:
            continue
        best_idx, best_area = None, 0.0
        for j in tree.query(g93):
            j = int(j)
            inter = g93.intersection(mgeoms93[j]).area
            if inter > best_area:
                best_area, best_idx = inter, j
        if best_idx is None:
            continue
        forets.append((best_idx * 16 + gi + 1, g93, g93.centroid.y))
    return maille_ids, forets


def traiter_bandes(ctx, s0, s1):
    os.makedirs(ctx.band_dir, exist_ok=True)
    _, forets = charger_forets_taggees(ctx)
    cys = np.array([f[2] for f in forets])
    slp = rasterio.open(f"{ctx.cache}/slope10.tif")
    nor = rasterio.open(f"{ctx.cache}/north10.tif")
    W, H, RES, SB, HALO = ctx.W, ctx.H, ctx.RES, ctx.SB, ctx.HALO
    for s in range(s0, s1):
        yc = s * SB
        if yc >= H:
            break
        hc = min(SB, H - yc); yr0 = max(0, yc - HALO); yr1 = min(H, yc + hc + HALO)
        wh = yr1 - yr0
        ytop_m = ctx.Y_TOP - yc * RES; ybot_m = ctx.Y_TOP - (yc + hc) * RES
        sel = [i for i in range(len(forets)) if ybot_m < cys[i] <= ytop_m]
        if not sel:
            pickle.dump(({}, {}), open(f"{ctx.band_dir}/b{s}.pkl", "wb"))
            print(f"  bande {s}: 0 forêt"); continue
        tr = from_origin(ctx.X0, ctx.Y_TOP - yr0 * RES, RES, RES)
        slope = slp.read(1, window=Window(0, yr0, W, wh))
        north = nor.read(1, window=Window(0, yr0, W, wh))
        nsm = boxmean(north); slsm = boxmean(slope); steep = slsm >= VERSANT_PENTE_MIN
        mg = rasterize([(forets[i][1], forets[i][0]) for i in sel],
                       out_shape=(wh, W), transform=tr, fill=0, dtype="int32")
        inf = mg > 0
        cls = np.zeros((wh, W), "uint8"); cls[inf] = 3
        cls[inf & steep & (nsm > VERSANT_NORTHNESS_SEUIL)] = 1
        cls[inf & steep & (nsm < -VERSANT_NORTHNESS_SEUIL)] = 2
        cls = sieve(cls, size=6, connectivity=8); cls[~inf] = 0
        label = np.where(inf & (cls > 0), mg.astype("int64") * 4 + cls, 0)
        flat = label.ravel(); mk = flat > 0
        if mk.sum() == 0:
            pickle.dump(({}, {}), open(f"{ctx.band_dir}/b{s}.pkl", "wb"))
            print(f"  bande {s}: vide"); continue
        lab = flat[mk]
        sn = np.bincount(lab, weights=(north * np.sin(np.radians(slope))).ravel()[mk])
        cc = np.bincount(lab, weights=np.ones(mk.sum()))
        geoms = {}; expo = {}
        for v in np.unique(lab):
            v = int(v); expo[v] = [float(sn[v]), float(cc[v])]
        for geom, val in shapes(label.astype("int32"), mask=(label > 0),
                                transform=tr, connectivity=8):
            v = int(val)
            if cc[v] < 25:
                continue
            g = shape(geom).simplify(15, preserve_topology=True)
            if not g.is_empty:
                geoms.setdefault(v, []).append(g)
        pickle.dump((geoms, expo), open(f"{ctx.band_dir}/b{s}.pkl", "wb"))
        print(f"  bande {s}: {len(sel)} forêts, {len(geoms)} labels", flush=True)
    print(f"bandes {s0}-{s1} OK")


def assembler(ctx):
    maille_ids, _ = charger_forets_taggees(ctx)
    geoms_all = {}; sn_all = {}; cc_all = {}
    for fp in sorted(glob.glob(f"{ctx.band_dir}/b*.pkl")):
        geoms, expo = pickle.load(open(fp, "rb"))
        for v, gs in geoms.items():
            geoms_all.setdefault(v, []).extend(gs)
        for v, (sn, cc) in expo.items():
            sn_all[v] = sn_all.get(v, 0.0) + sn; cc_all[v] = cc_all.get(v, 0.0) + cc
    out = {}; nfeat = 0
    seuil = VERSANT_AIRE_MIN_HA * 1e4
    for v, gs in geoms_all.items():
        mg_plus = v // 4; cl = v % 4
        maille_idx = (mg_plus - 1) // 16; group_idx = (mg_plus - 1) % 16
        mid = maille_ids[maille_idx]; grp = ESSENCE_ORDRE[group_idx]
        classe = CLASS_KEY[cl]
        g = unary_union(gs)
        if isinstance(g, MultiPolygon):
            parts = [p for p in g.geoms if p.area >= seuil]
            if not parts:
                continue
            g = unary_union(parts)
        elif g.area < seuil:
            continue
        g = g.simplify(VERSANT_SIMPLIFY_M, preserve_topology=True)
        if g.is_empty:
            continue
        g84 = shp_transform(to84, g)
        expo = round(sn_all[v] / max(cc_all[v], 1), 3)
        out.setdefault(mid, {}).setdefault(grp, {})[classe] = {
            "expo": expo, "geom": _round(mapping(g84), VERSANT_COORD_DECIMALES)}
        nfeat += 1
    json.dump(out, open(ctx.d["versant_geom"], "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(ctx.d["versant_geom"]) / 1e6
    print(f"{ctx.d['versant_geom']} : {len(out)} mailles, {nfeat} faces, {sz:.2f} Mo")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); raise SystemExit(1)
    ctx = Ctx(sys.argv[1])
    if sys.argv[2] == "assemble":
        assembler(ctx)
    elif sys.argv[2] == "bands":
        traiter_bandes(ctx, int(sys.argv[3]), int(sys.argv[4]))
    else:
        print(__doc__); raise SystemExit(1)
