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
                    LAG_JOURS_PLAINE, ESSENCE_GROUPES, ESSENCE_ORDRE)
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
    # `mailles` : détail complet par maille (série 15 j, sous-scores...), indexé
    # par maille_id et consulté au clic. `features` : géométries légères, une par
    # (maille × groupe d'essence), qui ne portent que de quoi colorer/filtrer.
    # On évite ainsi de dupliquer la série sur chaque sous-couche d'essence.
    mailles = {}
    features = []
    indices_jour = []
    for i, cell in enumerate(terrain):
        mid = cell["maille_id"]
        temp = temps[i] if i < len(temps) else None
        # Coef terrain = géologie × couverture forestière × essence hôte.
        coef_geo = cell["coef_terrain"]
        coef_foret = cell.get("coef_foret", 1.0)
        coef_essence = cell.get("coef_essence", 1.0)
        coef = coef_geo * coef_foret * coef_essence
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

        mailles[mid] = {
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
            "coef_essence": coef_essence,
            "taux_boise": cell.get("taux_boise"),
            "essence_dominante": cell.get("essence_dominante"),
            "essence_repartition": cell.get("essence_repartition", {}),
            "coef_terrain": coef,
            "s_humidite": res["s_humidite"],
            "s_pluie": res["s_pluie"],
            "s_temp": res["s_temp"],
            "lag_jours": lag_jours(cell["altitude"]),
            "date": dernier.get("date"),
            "serie": serie,
        }

        # Rendu "emprise forestière" par essence : on dessine chaque groupe
        # d'essence de la maille en couche distincte (filtrable, teintée selon
        # l'étage). Une maille sans forêt n'est pas dessinée. Repli sur le
        # carré 8 km si les emprises ne sont pas encore générées.
        if foret_geom:
            par_groupe = foret_geom.get(mid)
            if not par_groupe:
                continue  # rien de boisé ici
            for grp, geom in par_groupe.items():
                features.append({
                    "type": "Feature", "geometry": geom,
                    "properties": {"maille_id": mid, "indice": res["indice"],
                                   "groupe": grp},
                })
        else:
            features.append({
                "type": "Feature", "geometry": cell["geometry"],
                "properties": {"maille_id": mid, "indice": res["indice"],
                               "groupe": None},
            })

    # Synthèse départementale (une valeur par maille, pas par sous-couche).
    moyenne = round(sum(indices_jour) / len(indices_jour)) if indices_jour else None
    ids_rendus = {f["properties"]["maille_id"] for f in features}
    top = sorted(
        (p for mid, p in mailles.items()
         if mid in ids_rendus and p["indice"] is not None),
        key=lambda p: p["indice"], reverse=True,
    )[:5]
    date_donnees = max((p["date"] or "" for p in mailles.values()), default="")

    essence_groupes = [
        {"key": k, "label": ESSENCE_GROUPES[k]["label"],
         "etage": ESSENCE_GROUPES[k]["etage"], "alpha": ESSENCE_GROUPES[k]["alpha"],
         "defaut": ESSENCE_GROUPES[k]["defaut"]}
        for k in ESSENCE_ORDRE
    ]

    payload = {
        "genere_le": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "date_donnees": date_donnees,
        "moyenne_departement": moyenne,
        "niveau_departement": niveau(moyenne),
        "lag_indicatif": LAG_JOURS_PLAINE,
        "nb_mailles": len(ids_rendus),
        "essence_groupes": essence_groupes,
        "top": [{"indice": p["indice"], "niveau": p["niveau"],
                 "altitude": p["altitude"], "geologie": p["geologie_classe"],
                 "essence": p["essence_dominante"], "lat": None} for p in top],
        "mailles": mailles,
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
