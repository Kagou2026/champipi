"""Écriture des sorties de Champipi — architecture (b) « signal léger + fichiers
statiques externes ».

Au lieu d'une unique page auto-portée qui ré-embarque chaque jour ~10 Mo de
géométrie + historique (re-chiffrés, jamais cachés), on sépare :

  * la GÉOMÉTRIE versant (statique, open-data public) -> ``geom_<code>.json``,
    servie en clair et cachée à vie par le navigateur (nom + hash de contenu) ;
  * l'HISTORIQUE long (rejeu) -> ``hist_<code>.json``, chargé À LA DEMANDE ;
  * le SIGNAL DU JOUR (léger : indice du jour, série 15 j, top, prévision,
    stations) reste INLINE dans la page (chiffré StatiCrypt).

Résultat : la page à ouvrir passe de ~10 Mo à ~0,3 Mo, et « ajouter un
département » ne l'alourdit plus que de quelques centaines de Ko — la géométrie
et l'historique d'un département ne se chargent que quand on le regarde.

La géométrie ne porte PLUS l'indice du jour : la couleur de chaque face est
recalculée côté page (``moduleFace``) depuis l'indice de la maille + l'expo +
le stress. Le fichier geom est donc 100 % statique.
"""
import json
import hashlib
import math
import os

try:
    import config as _cfg
except Exception:                      # emit peut servir hors du package robot
    _cfg = None


def _cget(name, default):
    return getattr(_cfg, name, default) if _cfg else default


def _compact(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# (a) Allègement de la géométrie SERVIE (pas de la source, gardée pleine).
# Deux leviers, en pur Python (aucune dépendance en CI) :
#   1. jeter les MORCEAUX de forêt < GEOM_MIN_PART_HA (le « confetti » : la
#      majorité des polygones pour une fraction infime de la surface) ;
#   2. arrondir les coordonnées à GEOM_COORD_DECIMALES (~11 m à 4 déc.,
#      invisible à l'échelle 8 km) et dédupliquer les points ainsi confondus.
# Chaque face garde au moins son plus gros morceau (aucune ne disparaît).
# Niveau « équilibré » : ~2,5–3 Mo/dépt contre ~7, message visuel identique.
# ---------------------------------------------------------------------------
def _ring_area_ha(ring, f2ha):
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]; x2, y2 = ring[i + 1]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5 * f2ha


def _round_ring(ring, nd):
    out = []; last = None
    for pt in ring:
        p = [round(pt[0], nd), round(pt[1], nd)]
        if p != last:
            out.append(p); last = p
    if out and out[0] != out[-1]:
        out.append(out[0][:])
    return out if len(out) >= 4 else None


def _clean_poly(poly, nd, min_ha, f2ha):
    if _ring_area_ha(poly[0], f2ha) < min_ha:
        return None
    rings = []
    for r in poly:
        rr = _round_ring(r, nd)
        if rr and _ring_area_ha(rr, f2ha) > 0:
            rings.append(rr)
    return rings or None


def _slim_geom(geom, nd, min_ha, f2ha):
    t = geom.get("type"); c = geom.get("coordinates")
    if t == "Polygon":
        polys = [c]
    elif t == "MultiPolygon":
        polys = c
    else:
        return geom
    kept = [cp for cp in (_clean_poly(p, nd, min_ha, f2ha) for p in polys) if cp]
    if not kept and polys:
        biggest = max(polys, key=lambda p: _ring_area_ha(p[0], f2ha))
        cp = _clean_poly(biggest, nd, 0, f2ha)
        if cp:
            kept = [cp]
    if not kept:
        return None
    if len(kept) == 1:
        return {"type": "Polygon", "coordinates": kept[0]}
    return {"type": "MultiPolygon", "coordinates": kept}


def _slim_features(feats, lat_center):
    min_ha = _cget("GEOM_MIN_PART_HA", 3.0)
    nd = _cget("GEOM_COORD_DECIMALES", 4)
    if not min_ha and nd is None:
        return feats
    f2ha = (111000 * math.cos(math.radians(lat_center)) * 111000) / 1e4
    out = []
    for f in feats:
        g = _slim_geom(f["geometry"], nd, min_ha or 0, f2ha)
        if g:
            out.append({"type": "Feature", "geometry": g, "properties": f["properties"]})
    return out


def _short_hash(b):
    return hashlib.sha1(b).hexdigest()[:8]


def _bbox_from_features(feats):
    """Emprise [[lat_min,lon_min],[lat_max,lon_max]] d'une liste de features."""
    latmin = lonmin = 1e9
    latmax = lonmax = -1e9

    def walk(coords):
        nonlocal latmin, lonmin, latmax, lonmax
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            lon, lat = coords[0], coords[1]
            latmin = min(latmin, lat); latmax = max(latmax, lat)
            lonmin = min(lonmin, lon); lonmax = max(lonmax, lon)
        else:
            for c in coords:
                walk(c)

    for f in feats:
        walk(f["geometry"]["coordinates"])
    if latmin > latmax:
        return None
    return [[round(latmin, 4), round(lonmin, 4)],
            [round(latmax, 4), round(lonmax, 4)]]


def decouper_departement(code, nom, payload, out_dir):
    """Écrit geom_<code>.json et hist_<code>.json pour un département et renvoie
    (dept_leger, entree_registre).

    - ``payload`` : le dict complet produit par run.py pour ce département
      (mailles, geojson, historique, prevision, stations...).
    - la géométrie est écrite SANS l'indice (recalculé côté page) ;
    - l'historique est écrit tel quel s'il existe.
    """
    # -- geom_<code>.json : géométrie versant, sans indice ------------------
    feats = []
    for f in (payload.get("geojson", {}).get("features", []) or []):
        p = f.get("properties", {})
        feats.append({
            "type": "Feature", "geometry": f["geometry"],
            "properties": {"maille_id": p.get("maille_id"),
                           "groupe": p.get("groupe"),
                           "versant": p.get("versant"),
                           "expo": p.get("expo")},
        })
    # (a) allègement : jeter les confettis + arrondir les coordonnées.
    bbox_raw = _bbox_from_features(feats)
    lat_center = ((bbox_raw[0][0] + bbox_raw[1][0]) / 2) if bbox_raw else 45.0
    feats = _slim_features(feats, lat_center)
    geom = {"features": feats}
    geom_bytes = _compact(geom).encode("utf-8")
    geom_hash = _short_hash(geom_bytes)
    with open(os.path.join(out_dir, f"geom_{code}.json"), "wb") as fp:
        fp.write(geom_bytes)

    # -- hist_<code>.json : historique long (rejeu), si présent -------------
    hist = payload.get("historique")
    hist_hash = None
    if hist and hist.get("dates"):
        hb = _compact(hist).encode("utf-8")
        hist_hash = _short_hash(hb)
        with open(os.path.join(out_dir, f"hist_{code}.json"), "wb") as fp:
            fp.write(hb)

    bbox = _bbox_from_features(feats)

    # -- signal du jour (léger, reste inline / chiffré) ---------------------
    dept = {
        "date_donnees": payload.get("date_donnees"),
        "moyenne_departement": payload.get("moyenne_departement"),
        "niveau_departement": payload.get("niveau_departement"),
        "nb_mailles": payload.get("nb_mailles"),
        "genere_le": payload.get("genere_le"),
        "top": payload.get("top", []),
        "mailles": payload.get("mailles", {}),
        "prevision": payload.get("prevision"),
        "stations": payload.get("stations", []),
        "stations_date": payload.get("stations_date"),
    }
    registre = {
        "code": code, "nom": nom, "bbox": bbox,
        "geom": f"geom_{code}.json?v={geom_hash}",
        "hist": (f"hist_{code}.json?v={hist_hash}" if hist_hash else None),
    }
    return dept, registre


def ecrire_sorties(departements, template_path, out_dir, params):
    """Assemble la page finale multi-département.

    - ``departements`` : liste de tuples (code, nom, payload).
    - ``params`` : réglages globaux communs (versant_k, choc, essences, lag).
    Écrit dans ``out_dir`` : index.html (page légère) + geom_<code>.json +
    hist_<code>.json pour chaque département.
    """
    os.makedirs(out_dir, exist_ok=True)
    registre = []
    dept_data = {}
    genere_le = None
    for code, nom, payload in departements:
        dept, reg = decouper_departement(code, nom, payload, out_dir)
        dept_data[code] = dept
        registre.append(reg)
        genere_le = genere_le or payload.get("genere_le")

    inline = {
        "genere_le": genere_le,
        "params": params,
        "departements": registre,
        "dept": dept_data,
    }
    data_json = _compact(inline)

    with open(template_path, encoding="utf-8") as fp:
        template = fp.read()
    if "/*__CHAMPIPI_DATA__*/null" not in template:
        raise RuntimeError("Marqueur /*__CHAMPIPI_DATA__*/null absent du template.")
    html = template.replace("/*__CHAMPIPI_DATA__*/null", data_json)
    out_html = os.path.join(out_dir, "index.html")
    with open(out_html, "w", encoding="utf-8") as fp:
        fp.write(html)

    # rapport de tailles (utile en CI)
    def _mb(name):
        p = os.path.join(out_dir, name)
        return os.path.getsize(p) / 1e6 if os.path.exists(p) else 0.0
    infos = {"index.html": round(_mb("index.html"), 3)}
    for reg in registre:
        infos[f"geom_{reg['code']}.json"] = round(_mb(f"geom_{reg['code']}.json"), 3)
        if reg["hist"]:
            infos[f"hist_{reg['code']}.json"] = round(_mb(f"hist_{reg['code']}.json"), 3)
    return infos
