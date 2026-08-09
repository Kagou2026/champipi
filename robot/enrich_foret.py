"""Ajoute le boisement (BD Forêt V2) au terrain.json EXISTANT.

Évite de refaire tout build_terrain.py (géologie, altitude, SIM) : on charge
data/terrain.json, on calcule taux_boise + coef_foret par maille, et on
réécrit le fichier. À relancer seulement si l'on veut réactualiser BD Forêt.
"""
import json
from collections import Counter

from config import TERRAIN_FILE
from foret import enrichir_cellules


def main():
    with open(TERRAIN_FILE, encoding="utf-8") as fp:
        data = json.load(fp)
    cellules = data["cellules"]
    print(f"Boisement BD Forêt V2 pour {len(cellules)} mailles...")

    enrichir_cellules(cellules)

    with open(TERRAIN_FILE, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)

    taux = [c["taux_boise"] for c in cellules if c.get("taux_boise") is not None]
    coefs = Counter()
    for c in cellules:
        cf = c.get("coef_foret", 1.0)
        cat = ("nu (<0.2)" if cf < 0.2 else
               "faible (0.2-0.6)" if cf < 0.6 else
               "correct (0.6-1)" if cf < 1.0 else "plein (1.0)")
        coefs[cat] += 1
    print(f"\nTerrain réécrit : {TERRAIN_FILE}")
    if taux:
        print(f"Taux boisé moyen : {sum(taux)/len(taux):.2f} "
              f"(min {min(taux):.2f}, max {max(taux):.2f})")
    print("Répartition coef_foret :", dict(coefs))


if __name__ == "__main__":
    main()
