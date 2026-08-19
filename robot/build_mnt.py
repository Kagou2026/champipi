"""Reconstruction du cache MNT d'un département pour le calcul du versant.

Produit dans data/_versant_cache_<code>/ :
  - grid.json               : grille commune (X0, Y_TOP, W, H, RES=10) en L93
  - dem.tif                 : mosaïque altitude RGE ALTI 10 m (IGN WMS, BigTIFF)
  - slope10.tif             : pente (degrés)
  - north10.tif             : northness = cos(orientation)  (+1 = nord/ubac)
  - foret.pkl               : {id: (groupe, None, geom_L93)} forêts hôtes

Source altitude : IGN Géoplateforme WMS (sans clé), couche RGE ALTI HIGHRES,
FORMAT=image/geotiff, EPSG:2154, tuiles 2048 px (20,48 km à 10 m).

Grille dérivée de l'emprise réelle des mailles (terrain_<code>.json) + 2 km de
marge → on ne télécharge pas plus que nécessaire.

Usage :
    python robot/build_mnt.py dem 07      # mosaïque altitude
    python robot/build_mnt.py derive 07   # pente + northness
    python robot/build_mnt.py foret 07    # masque forêt (pkl)
    python robot/build_mnt.py all 07
"""
import json
import math
import os
import sys
import time

import numpy as np
import requests
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.windows import Window
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(__file__))
from config import DEPARTEMENTS

RES = 10.0
TILE = 2048
MARGE = 2000.0
WMS = "https://data.geopf.fr/wms-r/wms"
_to93 = Transformer.from_crs(4326, 2154, always_xy=True).transform


def cache_dir(code):
    return f"data/_versant_cache_{code}"


def grille(code):
    """(X0, Y_TOP, W, H) en L93 couvrant les mailles du département + marge."""
    ctx = DEPARTEMENTS[code]
    cells = json.load(open(ctx["terrain"], encoding="utf-8"))["cellules"]
    minx = miny = 1e18; maxx = maxy = -1e18
    for c in cells:
        a, b, cc, d = shp_transform(_to93, shape(c["geometry"])).bounds
        minx = min(minx, a); miny = min(miny, b)
        maxx = max(maxx, cc); maxy = max(maxy, d)
    minx -= MARGE; miny -= MARGE; maxx += MARGE; maxy += MARGE
    X0 = math.floor(minx / RES) * RES
    Y_TOP = math.ceil(maxy / RES) * RES
    W = int(math.ceil((maxx - X0) / RES))
    H = int(math.ceil((Y_TOP - miny) / RES))
    return X0, Y_TOP, W, H


def _fetch_tile(bbox, w, h):
    p = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
         "LAYERS": "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES", "STYLES": "",
         "CRS": "EPSG:2154", "FORMAT": "image/geotiff",
         "WIDTH": str(w), "HEIGHT": str(h),
         "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"}
    for att in range(4):
        try:
            r = requests.get(WMS, params=p, timeout=180)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image"):
                raise RuntimeError(r.text[:200])
            with MemoryFile(r.content) as mf, mf.open() as ds:
                return ds.read(1).astype("float32")
        except Exception as e:
            if att == 3:
                raise
            print(f"    tuile échec ({e}) — retry"); time.sleep(3)


def build_dem(code):
    X0, Y_TOP, W, H = grille(code)
    cd = cache_dir(code); os.makedirs(cd, exist_ok=True)
    json.dump({"X0": X0, "Y_TOP": Y_TOP, "W": W, "H": H, "RES": RES},
              open(f"{cd}/grid.json", "w"))
    print(f"[{code}] grille {W}x{H} px ({W*RES/1000:.0f}x{H*RES/1000:.0f} km)")
    prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="float32",
                crs="EPSG:2154", transform=from_origin(X0, Y_TOP, RES, RES),
                tiled=True, blockxsize=512, blockysize=512, compress="deflate",
                BIGTIFF="YES")
    ntx = math.ceil(W / TILE); nty = math.ceil(H / TILE)
    done = 0; total = ntx * nty
    with rasterio.open(f"{cd}/dem.tif", "w", **prof) as dst:
        for ty in range(nty):
            for tx in range(ntx):
                col = tx * TILE; row = ty * TILE
                tw = min(TILE, W - col); th = min(TILE, H - row)
                bx0 = X0 + col * RES; by1 = Y_TOP - row * RES
                bx1 = bx0 + tw * RES; by0 = by1 - th * RES
                arr = _fetch_tile((bx0, by0, bx1, by1), tw, th)
                arr = arr[:th, :tw]
                dst.write(arr, 1, window=Window(col, row, tw, th))
                done += 1
                if done % 5 == 0 or done == total:
                    print(f"  tuile {done}/{total}", flush=True)
    print(f"[{code}] dem.tif OK")


def derive(code):
    cd = cache_dir(code)
    g = json.load(open(f"{cd}/grid.json"))
    W, H, res = g["W"], g["H"], g["RES"]
    prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="float32",
                crs="EPSG:2154", transform=from_origin(g["X0"], g["Y_TOP"], res, res),
                tiled=True, blockxsize=512, blockysize=512, compress="deflate",
                BIGTIFF="YES")
    dem = rasterio.open(f"{cd}/dem.tif")
    slp = rasterio.open(f"{cd}/slope10.tif", "w", **prof)
    nor = rasterio.open(f"{cd}/north10.tif", "w", **prof)
    BAND = 2048
    for y0 in range(0, H, BAND):
        r0 = max(0, y0 - 1); r1 = min(H, y0 + BAND + 1); hh = r1 - r0
        z = dem.read(1, window=Window(0, r0, W, hh)).astype("float64")
        gx = np.zeros_like(z); gy = np.zeros_like(z)
        # Horn 3x3 : gx = dz/dEst, gy = dz/dSud (la ligne augmente vers le sud)
        gx[1:-1, 1:-1] = ((z[:-2, 2:] + 2*z[1:-1, 2:] + z[2:, 2:])
                          - (z[:-2, :-2] + 2*z[1:-1, :-2] + z[2:, :-2])) / (8*res)
        gy[1:-1, 1:-1] = ((z[2:, :-2] + 2*z[2:, 1:-1] + z[2:, 2:])
                          - (z[:-2, :-2] + 2*z[:-2, 1:-1] + z[:-2, 2:])) / (8*res)
        slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
        # aspect = atan2(-gx, gy) (0=N,90=E,180=S) ; northness = cos(aspect)
        north = np.cos(np.arctan2(-gx, gy)).astype("float32")
        top = y0 - r0
        sub_h = min(BAND, H - y0)
        slp.write(slope[top:top+sub_h, :], 1, window=Window(0, y0, W, sub_h))
        nor.write(north[top:top+sub_h, :], 1, window=Window(0, y0, W, sub_h))
        print(f"  dérivées {y0}/{H}", flush=True)
    dem.close(); slp.close(); nor.close()
    print(f"[{code}] slope10.tif + north10.tif OK")


def build_foret(code):
    """Masque forêt hôte pour le versant, tiré de foret_geom_<code>.json
    (déjà les forêts par maille×groupe) → pas de nouvel appel BD Forêt."""
    import pickle
    ctx = DEPARTEMENTS[code]
    fg = json.load(open(ctx["foret_geom"], encoding="utf-8"))
    ford = {}; k = 0
    for mid, groupes in fg.items():
        for grp, geom in groupes.items():
            g93 = shp_transform(_to93, shape(geom)).buffer(0)
            if g93.is_empty:
                continue
            ford[f"{mid}_{grp}_{k}"] = (grp, None, g93); k += 1
    pickle.dump(ford, open(f"{cache_dir(code)}/foret.pkl", "wb"))
    print(f"[{code}] foret.pkl : {len(ford)} polygones")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); raise SystemExit(1)
    cmd, code = sys.argv[1], sys.argv[2]
    if cmd == "dem":
        build_dem(code)
    elif cmd == "derive":
        derive(code)
    elif cmd == "foret":
        build_foret(code)
    elif cmd == "all":
        build_dem(code); derive(code); build_foret(code)
    else:
        print(__doc__); raise SystemExit(1)
