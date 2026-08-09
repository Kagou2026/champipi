"""Robot quotidien Champipi.

Étapes :
  1. charge le masque terrain statique (géologie, altitude, géométrie) ;
  2. récupère les données SIM du jour + 15 j d'historique par maille ;
  3. récupère la température récente par maille (Open-Meteo) ;
  4. calcule l'indice cèpe (aujourd'hui + série 15 j) par maille ;
  5. génère site/index.html auto-porté (données embarquées).

Le chiffrement de la page est réalisé ensuite par le workflow GitHub Actions.
"""
import json
from datetime import datetime, timezone

from config import (TERRAIN_FILE, FORET_GEOM_FILE, SITE_TEMPLATE, SITE_OUTPUT,
                    LAG_JOURS_PLAINE)
from fetch_sim import fetch_sim_features, organiser_par_maille
from fetch_temp import temperatures_par_maille
from compute_index import calcul_indice, niveau, lag_jours


def charger_terrain():
    with open(TERRAIN_FILE, encoding="utf-8") as fp:
        return json.load(fp)["cellules"]


def charger_foret_geom():
    """Emprises forestières par maille (rendu carte). Absent = pas encore
    généré : on retombera sur les carrés 8 km."""
    try:
        with open(FORET_GEOM_FILE, encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return {}


def main():
    print("1/5 Chargement du terrain...")
    terrain = charger_terrain()
    foret_geom = charger_foret_geom()
    print(f"    {len(terrain)} mailles, {len(foret_geom)} emprises forestières")

    print("2/5 Données SIM (humidité, pluie)...")
    sim = organiser_par_maille(fetch_sim_features())

    print("3/5 Températures (Open-Meteo)...")
    coords = [(c["lat"], c["lon"]) for c in terrain]
    temps = temperatures_par_maille(coords)

    print("4/5 Calcul de l'indice par maille...")
    features = []
    indices_jour = []
    for i, cell in enumerate(terrain):
        mid = cell["maille_id"]
        temp = temps[i] if i < len(temps) else None
        # Coefficient terrain = géologie × boisement (deux facteurs statiques).
        coef_geo = cell["coef_terrain"]
        coef_foret = cell.get("coef_foret", 1.0)
        coef = coef_geo * coef_foret
        hist_sim = sim.get(mid, {}).get("historique", [])

        # Série d'indices sur l'historique (température maintenue = actuelle).
        serie = []
        for h in hist_sim:
            r = calcul_indice(h["swi"], h["pluie_15j"], temp, coef)
            serie.append({"date": h["date"], "swi": h["swi"],
                          "pluie_15j": h["pluie_15j"], "indice": r["indice"]})

        dernier = hist_sim[-1] if hist_sim else {}
        res = calcul_indice(dernier.get("swi"), dernier.get("pluie_15j"), temp, coef)
        if res["indice"] is not None:
            indices_jour.append(res["indice"])

        # Rendu "emprise forestière" : on dessine la forêt de la maille, pas le
        # carré 8 km. Une maille sans forêt (causse, ville, champs) n'est pas
        # dessinée du tout. Repli sur le carré si les emprises ne sont pas
        # encore générées.
        if foret_geom:
            geom = foret_geom.get(mid)
            if geom is None:
                continue  # rien de boisé ici : aucune surcouche
        else:
            geom = cell["geometry"]

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "maille_id": mid,
                "indice": res["indice"],
                "niveau": res["niveau"],
                "swi": dernier.get("swi"),
                "pluie_15j": dernier.get("pluie_15j"),
                "anomalie_swi": dernier.get("anomalie_swi"),
                "temp": temp,
                "altitude": cell["altitude"],
                "geologie_classe": cell["geologie_classe"],
                "geologie_descr": cell["geologie_descr"],
                "coef_geologie": coef_geo,
                "coef_foret": coef_foret,
                "taux_boise": cell.get("taux_boise"),
                "coef_terrain": coef,
                "s_humidite": res["s_humidite"],
                "s_pluie": res["s_pluie"],
                "s_temp": res["s_temp"],
                "lag_jours": lag_jours(cell["altitude"]),
                "date": dernier.get("date"),
                "serie": serie,
            },
        })

    # Synthèse départementale
    moyenne = round(sum(indices_jour) / len(indices_jour)) if indices_jour else None
    top = sorted(
        (f["properties"] for f in features if f["properties"]["indice"] is not None),
        key=lambda p: p["indice"], reverse=True,
    )[:5]
    date_donnees = max((f["properties"]["date"] or "" for f in features), default="")

    payload = {
        "genere_le": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "date_donnees": date_donnees,
        "moyenne_departement": moyenne,
        "niveau_departement": niveau(moyenne),
        "lag_indicatif": LAG_JOURS_PLAINE,
        "nb_mailles": len(features),
        "top": [{"indice": p["indice"], "niveau": p["niveau"],
                 "altitude": p["altitude"], "geologie": p["geologie_classe"],
                 "lat": None} for p in top],
        "geojson": {"type": "FeatureCollection", "features": features},
    }

    print("5/5 Génération de site/index.html...")
    with open(SITE_TEMPLATE, encoding="utf-8") as fp:
        template = fp.read()
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = template.replace("/*__CHAMPIPI_DATA__*/null", data_json)
    with open(SITE_OUTPUT, "w", encoding="utf-8") as fp:
        fp.write(html)

    print(f"\nOK. Moyenne département : {moyenne} ({niveau(moyenne)}) "
          f"| données du {date_donnees} | {len(features)} mailles")


if __name__ == "__main__":
    main()
