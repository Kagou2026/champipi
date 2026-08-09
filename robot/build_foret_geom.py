"""Génère data/foret_geom.json : l'emprise forestière (simplifiée) par maille.

Pour chaque maille du terrain, dissout les polygones boisés BD Forêt V2
clippés à la maille, simplifie et arrondit les coordonnées. Le résultat sert
au rendu "emprise forestière" : la carte dessine les forêts coloriées par
l'indice de leur maille, au lieu des carrés de 8 km.

Statique : à relancer seulement pour réactualiser BD Forêt. Requêtes lourdes
(~1-2 min pour la Lozère).
"""
import json
import time

from config import TERRAIN_FILE, FORET_GEOM_FILE
from foret import emprise_forestiere


def main():
    with open(TERRAIN_FILE, encoding="utf-8") as fp:
        cellules = json.load(fp)["cellules"]
    print(f"Emprise forestière pour {len(cellules)} mailles...")

    geoms = {}
    vides = 0
    for i, cell in enumerate(cellules, 1):
        g = emprise_forestiere(cell["geometry"])
        if g is None:
            vides += 1
        else:
            geoms[cell["maille_id"]] = g
        if i % 5 == 0 or i == len(cellules):
            print(f"    {i}/{len(cellules)} mailles ({vides} sans forêt)")
        time.sleep(0.3)

    with open(FORET_GEOM_FILE, "w", encoding="utf-8") as fp:
        json.dump(geoms, fp, ensure_ascii=False, separators=(",", ":"))

    import os
    ko = os.path.getsize(FORET_GEOM_FILE) / 1024
    print(f"\n{FORET_GEOM_FILE} écrit : {len(geoms)} mailles avec forêt, "
          f"{vides} sans. Poids {ko:.0f} Ko ({ko/1024:.2f} Mo).")


if __name__ == "__main__":
    main()
