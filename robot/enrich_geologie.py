"""(Re)remplit la géologie BRGM d'un terrain_<code>.json existant, sans rebuild.

Pourquoi : le WFS BRGM (geoservices.brgm.fr) est derrière un pare-feu
applicatif qui rejette certaines adresses (« Request Rejected » depuis le
conteneur cloud le 11/10/2026) ; build_departement.py laisse alors toutes les
mailles en « inconnu » (coef 0.6). Ce script rejoue UNIQUEMENT la géologie
depuis une machine acceptée, maille par maille, en reprenant là où il s'est
arrêté (les mailles déjà classées sont sautées) — il peut donc tourner par
tranches de quelques minutes.

Usage :
    python robot/enrich_geologie.py 12            # mailles 'inconnu' seulement
    python robot/enrich_geologie.py 12 --tout     # refait toutes les mailles
    python robot/enrich_geologie.py 12 --max 60   # au plus 60 mailles ce coup-ci
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from config import DEPARTEMENTS, GEOLOGIE_COEFFICIENT
from build_terrain import classer_geologie, geologie_au_point


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    code = args[0]
    tout = "--tout" in sys.argv
    maxi = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else None
    ctx = DEPARTEMENTS[code]
    path = ctx["terrain"]
    doc = json.load(open(path, encoding="utf-8"))
    cells = doc["cellules"]
    a_faire = [c for c in cells if tout or c.get("geologie_classe") in (None, "inconnu")]
    print(f"[{code}] {len(a_faire)}/{len(cells)} mailles à (re)classer")
    n = 0
    for c in a_faire:
        if maxi is not None and n >= maxi:
            break
        descr = geologie_au_point(c["lat"], c["lon"])
        if descr is None:
            continue              # échec réseau : on la retrouvera au prochain passage
        classe = classer_geologie(descr)
        c["geologie_descr"] = descr
        c["geologie_classe"] = classe
        c["coef_terrain"] = GEOLOGIE_COEFFICIENT[classe]
        n += 1
        if n % 20 == 0:
            print(f"    {n} faites")
            json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(0.25)
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    from collections import Counter
    reste = sum(1 for c in cells if c.get("geologie_classe") in (None, "inconnu"))
    print(f"    {n} mailles classées ce passage ; reste {reste} inconnues")
    print(f"    géol : {dict(Counter(c['geologie_classe'] for c in cells))}")


if __name__ == "__main__":
    main()
