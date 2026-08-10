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

from config import (TERRAIN_FILE, FORET_GEOM_FILE, VERSANT_GEOM_FILE,
                    SITE_TEMPLATE, SITE_OUTPUT, LAG_JOURS_PLAINE,
                    ESSENCE_GROUPES, ESSENCE_ORDRE, VERSANT_CLASSES)
from fetch_sim import fetch_sim_features, organiser_par_maille
from fetch_temp import temperatures_par_maille
from compute_index import calcul_indice, niveau, lag_jours
from versant import stress_hydrothermique, indice_module


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


def charger_versant_geom():
    """Faces de forêt découpées par versant (nord/sud/neutre) avec exposition.

    Structure : {maille_id: {groupe: {classe: {"expo": x, "geom": geojson}}}}.
    Absent = versant pas encore construit (build_versant.py) : le robot retombe
    proprement sur foret_geom (rendu sans versant)."""
    try:
        with open(VERSANT_GEOM_FILE, encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return {}


def main():
    print("1/5 Chargement du terrain...")
    terrain = charger_terrain()
    foret_geom = charger_foret_geom()
    versant_geom = charger_versant_geom()
    src_rendu = "versant" if versant_geom else ("forêt" if foret_geom else "carrés 8 km")
    print(f"    {len(terrain)} mailles, {len(foret_geom)} emprises forêt, "
          f"{len(versant_geom)} mailles versant → rendu : {src_rendu}")

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
    faces_top = []      # meilleures FACES (versant) pour le top 5
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

        # Stress hydro-thermique du jour (+1 sec/chaud, -1 froid/humide) : pilote
        # le SIGNE de la modulation versant (cf. versant.py).
        stress = stress_hydrothermique(dernier.get("swi"), temp)

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
            "stress": round(stress, 2),
            "date": dernier.get("date"),
            "serie": serie,
        }

        # Rendu carte. Priorité au VERSANT : chaque groupe d'essence est
        # découpé en faces nord/sud/neutre, chacune colorée par l'indice de la
        # maille MODULÉ par son exposition et le stress du jour (une face nord
        # ressort quand il fait sec, une face sud quand il fait froid).
        # Replis successifs : foret_geom (sans versant), puis carré 8 km.
        par_groupe_v = versant_geom.get(mid) if versant_geom else None
        if par_groupe_v:
            for grp, faces in par_groupe_v.items():
                for classe, o in faces.items():
                    ind = indice_module(res["indice"], o["expo"], stress)
                    features.append({
                        "type": "Feature", "geometry": o["geom"],
                        "properties": {"maille_id": mid, "indice": ind,
                                       "groupe": grp, "versant": classe,
                                       "expo": o["expo"]},
                    })
                    if ind is not None:
                        faces_top.append({
                            "indice": ind, "niveau": niveau(ind),
                            "maille_id": mid, "essence": grp,
                            "versant": classe, "expo": o["expo"],
                            "altitude": cell["altitude"],
                            "geologie": cell["geologie_classe"]})
        elif foret_geom:
            par_groupe = foret_geom.get(mid)
            if not par_groupe:
                continue  # rien de boisé ici
            for grp, geom in par_groupe.items():
                features.append({
                    "type": "Feature", "geometry": geom,
                    "properties": {"maille_id": mid, "indice": res["indice"],
                                   "groupe": grp, "versant": None},
                })
        else:
            features.append({
                "type": "Feature", "geometry": cell["geometry"],
                "properties": {"maille_id": mid, "indice": res["indice"],
                               "groupe": None, "versant": None},
            })

    # Synthèse départementale (une valeur par maille, pas par sous-couche).
    moyenne = round(sum(indices_jour) / len(indices_jour)) if indices_jour else None

    # Top 5 des "coins du jour". Avec le versant, on classe par FACE modulée,
    # en ne gardant que la meilleure face de chaque maille (5 coins distincts).
    if faces_top:
        best_par_maille = {}
        for f in faces_top:
            b = best_par_maille.get(f["maille_id"])
            if b is None or f["indice"] > b["indice"]:
                best_par_maille[f["maille_id"]] = f
        top = sorted(best_par_maille.values(),
                     key=lambda f: f["indice"], reverse=True)[:5]
    else:
        ids_rendus = {f["properties"]["maille_id"] for f in features}
        top = [
            {"indice": p["indice"], "niveau": p["niveau"],
             "altitude": p["altitude"], "geologie": p["geologie_classe"],
             "essence": p["essence_dominante"], "versant": None, "expo": None}
            for p in sorted(
                (p for mid, p in mailles.items()
                 if mid in ids_rendus and p["indice"] is not None),
                key=lambda p: p["indice"], reverse=True)[:5]
        ]
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
        "nb_mailles": len({f["properties"]["maille_id"] for f in features}),
        "essence_groupes": essence_groupes,
        "top": top,
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
