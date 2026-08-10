"""Génère data/foret_geom.json : l'emprise forestière PAR GROUPE D'ESSENCE,
rattachée à sa maille DOMINANTE — sans découper les forêts.

Différence avec l'ancienne version : on ne clippe plus les polygones au bord
de la maille 8 km (une forêt à cheval sur deux carrés n'est plus coupée).
Chaque polygone forestier de la BD Forêt V2 est gardé ENTIER, puis affecté à
la maille où repose la plus grande part de sa surface. La couleur héritée est
donc celle de cette maille (son indice du jour), appliquée à la forêt complète.

Procédé :
  1. charge les mailles (terrain.json) et les indexe spatialement (STRtree) ;
  2. télécharge la BD Forêt V2 par bbox de maille, en dédupliquant les
     polygones par `id` (les bbox se chevauchent, un même polygone revient) ;
  3. pour chaque polygone boisé (poids de structure > 0), cherche la maille
     dont l'intersection de surface est la plus grande = maille dominante ;
  4. groupe les polygones ENTIERS par (maille dominante, groupe d'essence)
     et dissout (union) chaque groupe.

Structure du fichier (inchangée, donc run.py / template.html ne bougent pas) :
    { maille_id: { "chene": <geojson>, "mixte": <geojson>, ... }, ... }

Les géométries peuvent maintenant DÉBORDER de la maille : c'est voulu.

Statique : à relancer seulement pour réactualiser BD Forêt. Requêtes lourdes
(~1-2 min pour la Lozère).
"""
import json
import os
import time
from collections import Counter

from shapely.geometry import shape
from shapely.strtree import STRtree

from config import TERRAIN_FILE, FORET_GEOM_FILE, FORET_POIDS
from foret import fetch_bdforet, groupe_essence, _dissoudre


def charger_mailles():
    """(ids, geoms shapely, STRtree) des mailles du terrain."""
    with open(TERRAIN_FILE, encoding="utf-8") as fp:
        cellules = json.load(fp)["cellules"]
    ids = [c["maille_id"] for c in cellules]
    geoms = [shape(c["geometry"]).buffer(0) for c in cellules]
    return cellules, ids, geoms, STRtree(geoms)


def collecter_polygones(cellules):
    """Télécharge la BD Forêt par maille et déduplique par `id`.

    Renvoie {feature_id: (groupe_essence, shapely_geom)} pour les seuls
    polygones à couvert exploitable (poids de structure > 0).
    """
    par_id = {}
    n = len(cellules)
    for i, cell in enumerate(cellules, 1):
        maille = shape(cell["geometry"])
        try:
            feats = fetch_bdforet(maille)
        except Exception as e:
            print(f"  ! BD Forêt échec maille {cell['maille_id']} : {e}")
            feats = []
        for f in feats:
            fid = f.get("id")
            if fid in par_id:
                continue  # déjà vu via une bbox voisine
            props = f.get("properties") or {}
            if FORET_POIDS.get(props.get("tfv_g11"), 0.0) <= 0:
                continue
            try:
                geom = shape(f["geometry"]).buffer(0)
            except Exception:
                continue
            if geom.is_empty:
                continue
            par_id[fid] = (groupe_essence(props), geom)
        if i % 5 == 0 or i == n:
            print(f"    {i}/{n} mailles téléchargées "
                  f"({len(par_id)} polygones forêt uniques)", flush=True)
        time.sleep(0.1)
    return par_id


def maille_dominante(geom, ids, geoms, tree):
    """maille_id dont l'intersection de surface avec `geom` est la plus grande.

    None si le polygone ne touche aucune maille du département (bordure). On
    compare les aires en WGS84 : la distorsion locale est quasi constante, le
    classement des surfaces reste correct.
    """
    idx = tree.query(geom)  # candidats dont la bbox recoupe (indices)
    meilleur, aire_max = None, 0.0
    for j in idx:
        try:
            a = geom.intersection(geoms[j]).area
        except Exception:
            continue
        if a > aire_max:
            aire_max, meilleur = a, ids[j]
    return meilleur


def main():
    cellules, ids, geoms, tree = charger_mailles()
    print(f"Emprises forestières (forêts entières) pour {len(cellules)} mailles...")

    par_id = collecter_polygones(cellules)
    print(f"  {len(par_id)} polygones forêt uniques à affecter.")

    # Regroupe les polygones ENTIERS par (maille dominante, groupe d'essence).
    par_maille = {}
    orphelins = 0
    for grp, geom in par_id.values():
        mid = maille_dominante(geom, ids, geoms, tree)
        if mid is None:
            orphelins += 1
            continue
        par_maille.setdefault(mid, {}).setdefault(grp, []).append(geom)

    # Dissout (union + simplification) chaque (maille, groupe).
    resultat = {}
    compte_grp = Counter()
    for mid, groupes in par_maille.items():
        out = {}
        for grp, polys in groupes.items():
            g = _dissoudre(polys)
            if g is not None:
                out[grp] = g
                compte_grp[grp] += 1
        if out:
            resultat[mid] = out

    with open(FORET_GEOM_FILE, "w", encoding="utf-8") as fp:
        json.dump(resultat, fp, ensure_ascii=False, separators=(",", ":"))

    ko = os.path.getsize(FORET_GEOM_FILE) / 1024
    vides = len(cellules) - len(resultat)
    print(f"\n{FORET_GEOM_FILE} écrit : {len(resultat)} mailles avec forêt, "
          f"{vides} sans, {orphelins} polygones hors mailles ignorés.")
    print(f"Poids {ko:.0f} Ko ({ko/1024:.2f} Mo).")
    print("Mailles par groupe d'essence :", dict(compte_grp))


if __name__ == "__main__":
    main()
