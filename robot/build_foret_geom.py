"""Génère data/foret_geom.json : l'emprise forestière PAR GROUPE D'ESSENCE et
par maille.

Pour chaque maille du terrain, dissout les polygones boisés BD Forêt V2
clippés à la maille, séparément pour chaque groupe d'essence (chêne, hêtre,
pin, feuillus indéterminés...). Structure du fichier :

    { maille_id: { "chene": <geojson>, "mixte": <geojson>, ... }, ... }

La carte dessine ainsi chaque essence en couche distincte : filtre par cases
à cocher et teinte plus forte pour les essences principales.

Statique : à relancer seulement pour réactualiser BD Forêt. Requêtes lourdes
(~1-2 min pour la Lozère).
"""
import json
import time
from collections import Counter

from config import TERRAIN_FILE, FORET_GEOM_FILE
from foret import emprises_par_groupe


def main():
    with open(TERRAIN_FILE, encoding="utf-8") as fp:
        cellules = json.load(fp)["cellules"]
    print(f"Emprises forestières par essence pour {len(cellules)} mailles...")

    geoms = {}
    vides = 0
    compte_grp = Counter()
    for i, cell in enumerate(cellules, 1):
        parGroupe = emprises_par_groupe(cell["geometry"])
        if not parGroupe:
            vides += 1
        else:
            geoms[cell["maille_id"]] = parGroupe
            compte_grp.update(parGroupe.keys())
        if i % 5 == 0 or i == len(cellules):
            print(f"    {i}/{len(cellules)} mailles ({vides} sans forêt)")
        time.sleep(0.3)

    with open(FORET_GEOM_FILE, "w", encoding="utf-8") as fp:
        json.dump(geoms, fp, ensure_ascii=False, separators=(",", ":"))

    import os
    ko = os.path.getsize(FORET_GEOM_FILE) / 1024
    print(f"\n{FORET_GEOM_FILE} écrit : {len(geoms)} mailles avec forêt, "
          f"{vides} sans. Poids {ko:.0f} Ko ({ko/1024:.2f} Mo).")
    print("Mailles par groupe d'essence :", dict(compte_grp))


if __name__ == "__main__":
    main()
