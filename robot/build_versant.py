"""Construction du masque VERSANT statique de la Lozère (à lancer une fois).

Produit `data/versant_geom.json` : pour chaque maille et chaque groupe
d'essence, la forêt est découpée en 3 classes de versant (nord / sud / neutre)
d'après le MNT IGN 10 m, agrégées (dissolve) et accompagnées d'une exposition
représentative `expo` (~[-1,+1], + = nord). Le robot quotidien module ensuite
l'indice de la maille par cet `expo` (cf. versant.py, run.py).

Structure du fichier :
    { maille_id: { groupe: { "nord": {"expo": x, "geom": <geojson WGS84>},
                              "sud":  {...}, "neutre": {...} }, ... }, ... }

Entrées (cache, dossier data/_versant_cache/ par défaut, non versionné) :
  - slope10.tif, north10.tif : pente (deg) et northness (cos orientation) en
    Lambert-93, 10 m, sur l'emprise LOZERE (grille X0/Y_TOP/RES ci-dessous),
    dérivés du MNT IGN RGE ALTI (WMS GetMap image/geotiff, cf. mémoire projet).
  - foret_lz.pkl : {id: (groupe, essence, geom_L93)} des forêts hôtes BD Forêt.
Ces caches sont volumineux (~1 Go) et volontairement hors dépôt : seul le
résultat léger `data/versant_geom.json` est committé.

Traitement en BANDES (RAM limitée) puis assemblage :
    python build_versant.py bands 0 4
    python build_versant.py bands 4 8
    python build_versant.py bands 8 12
    python build_versant.py assemble
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
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform as shp_transform, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    TERRAIN_FILE, VERSANT_GEOM_FILE, ESSENCE_ORDRE,
    VERSANT_PENTE_MIN, VERSANT_NORTHNESS_SEUIL,
    VERSANT_SIMPLIFY_M, VERSANT_COORD_DECIMALES, VERSANT_AIRE_MIN_HA,
)

# Grille du MNT (doit correspondre à la fabrication de slope10/north10).
X0, Y_TOP, RES = 680000.0, 6445000.0, 10.0
W = H = 12288
SB, HALO = 1024, 640          # coeur de bande / halo (px)
CACHE = os.environ.get("VERSANT_CACHE",
                       os.path.join(os.path.dirname(__file__), "..", "data", "_versant_cache"))
BAND_DIR = os.path.join(CACHE, "_bands")
CLASS_KEY = {1: "nord", 2: "sud", 3: "neutre"}
GROUP_IDX = {g: i for i, g in enumerate(ESSENCE_ORDRE)}  # 0..8 (<16)

to84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True).transform


def _round(o, nd):
    """Arrondit récursivement les coordonnées d'un mapping GeoJSON."""
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, (list, tuple)):
        return [_round(x, nd) for x in o]
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    return o


def boxmean(a, r=5):
    """Moyenne glissante (fenêtre 2r+1) via image intégrale — lisse le MNT."""
    a = a.astype("float64"); Hh, Ww = a.shape
    S = np.zeros((Hh + 1, Ww + 1)); S[1:, 1:] = a.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(Hh) - r, 0, Hh); y1 = np.clip(np.arange(Hh) + r + 1, 0, Hh)
    x0 = np.clip(np.arange(Ww) - r, 0, Ww); x1 = np.clip(np.arange(Ww) + r + 1, 0, Ww)
    Y0, Y1 = y0[:, None], y1[:, None]; X0_, X1 = x0[None, :], x1[None, :]
    tot = S[Y1, X1] - S[Y0, X1] - S[Y1, X0_] + S[Y0, X0_]
    return (tot / ((Y1 - Y0) * (X1 - X0_))).astype("float32")


def charger_forets_taggees():
    """Forêts hôtes taggées (maille dominante, groupe) -> mg_plus, + centroïdes.

    mg_plus = maille_idx*16 + group_idx + 1  (valeur de gravure, jamais 0).
    """
    cells = json.load(open(TERRAIN_FILE, encoding="utf-8"))["cellules"]
    maille_ids = [c["maille_id"] for c in cells]
    mgeoms = [shape(c["geometry"]) for c in cells]  # WGS84
    to93 = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform
    mgeoms93 = [shp_transform(to93, g).buffer(0) for g in mgeoms]
    tree = STRtree(mgeoms93)

    ford = pickle.load(open(os.path.join(CACHE, "foret_lz.pkl"), "rb"))
    forets = []  # (mg_plus, geom93, centroid_y)
    for grp, _ess, g93 in ford.values():
        gi = GROUP_IDX.get(grp)
        if gi is None:
            continue
        cand = tree.query(g93)
        best_idx, best_area = None, 0.0
        for j in cand:
            j = int(j)
            inter = g93.intersection(mgeoms93[j]).area
            if inter > best_area:
                best_area, best_idx = inter, j
        if best_idx is None:
            continue  # forêt hors des mailles retenues
        mg_plus = best_idx * 16 + gi + 1
        forets.append((mg_plus, g93, g93.centroid.y))
    return maille_ids, forets


def traiter_bandes(s0, s1):
    os.makedirs(BAND_DIR, exist_ok=True)
    maille_ids, forets = charger_forets_taggees()
    cys = np.array([f[2] for f in forets])
    slp = rasterio.open(os.path.join(CACHE, "slope10.tif"))
    nor = rasterio.open(os.path.join(CACHE, "north10.tif"))
    for s in range(s0, s1):
        yc = s * SB
        if yc >= H:
            break
        hc = min(SB, H - yc); yr0 = max(0, yc - HALO); yr1 = min(H, yc + hc + HALO); wh = yr1 - yr0
        ytop_m = Y_TOP - yc * RES; ybot_m = Y_TOP - (yc + hc) * RES
        sel = [i for i in range(len(forets)) if ybot_m < cys[i] <= ytop_m]
        if not sel:
            pickle.dump(({}, {}), open(f"{BAND_DIR}/b{s}.pkl", "wb")); print(f"  bande {s}: 0 forêt"); continue
        tr = from_origin(X0, Y_TOP - yr0 * RES, RES, RES)
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
            pickle.dump(({}, {}), open(f"{BAND_DIR}/b{s}.pkl", "wb")); print(f"  bande {s}: vide"); continue
        lab = flat[mk]
        sn = np.bincount(lab, weights=(north * np.sin(np.radians(slope))).ravel()[mk])
        cc = np.bincount(lab, weights=np.ones(mk.sum()))
        geoms = {}    # label -> [geom L93]
        expo = {}     # label -> [sn, cc]
        for v in np.unique(lab):
            v = int(v); expo[v] = [float(sn[v]), float(cc[v])]
        for geom, val in shapes(label.astype("int32"), mask=(label > 0), transform=tr, connectivity=8):
            v = int(val)
            if cc[v] < 25:      # ignore éclats < 0.25 ha
                continue
            g = shape(geom).simplify(15, preserve_topology=True)
            if not g.is_empty:
                geoms.setdefault(v, []).append(g)
        pickle.dump((geoms, expo), open(f"{BAND_DIR}/b{s}.pkl", "wb"))
        print(f"  bande {s}: {len(sel)} forêts, {len(geoms)} labels", flush=True)
    print(f"bandes {s0}-{s1} OK")


def assembler():
    maille_ids, _ = charger_forets_taggees()
    geoms_all = {}   # label -> [geom]
    sn_all = {}; cc_all = {}
    for fp in sorted(glob.glob(f"{BAND_DIR}/b*.pkl")):
        geoms, expo = pickle.load(open(fp, "rb"))
        for v, gs in geoms.items():
            geoms_all.setdefault(v, []).extend(gs)
        for v, (sn, cc) in expo.items():
            sn_all[v] = sn_all.get(v, 0.0) + sn; cc_all[v] = cc_all.get(v, 0.0) + cc
    out = {}
    nfeat = 0
    for v, gs in geoms_all.items():
        mg_plus = v // 4; cl = v % 4
        maille_idx = (mg_plus - 1) // 16; group_idx = (mg_plus - 1) % 16
        mid = maille_ids[maille_idx]; grp = ESSENCE_ORDRE[group_idx]; classe = CLASS_KEY[cl]
        g = unary_union(gs)
        # Jette les fragments individuels trop petits (le "confetti" forestier
        # qui gonfle le nombre de sommets sans rien apporter à la lecture).
        seuil = VERSANT_AIRE_MIN_HA * 1e4
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
    json.dump(out, open(VERSANT_GEOM_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(VERSANT_GEOM_FILE) / 1e6
    print(f"{VERSANT_GEOM_FILE} écrit : {len(out)} mailles, {nfeat} faces, {sz:.2f} Mo")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "assemble":
        assembler()
    elif len(sys.argv) >= 4 and sys.argv[1] == "bands":
        traiter_bandes(int(sys.argv[2]), int(sys.argv[3]))
    else:
        print(__doc__)
