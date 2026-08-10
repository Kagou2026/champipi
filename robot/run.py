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
                    ESSENCE_GROUPES, ESSENCE_ORDRE, VERSANT_CLASSES, VERSANT_K,
                    CHOC_K, CHOC_K_CHAUD, CHOC_MIN, CHOC_OPT,
                    CHOC_FENETRE_RECENTE, CHOC_FENETRE_REF,
                    TEMP_MIN, TEMP_OPT_BAS, TEMP_OPT_HAUT, TEMP_MAX,
                    PREV_HORIZON_JOURS, PREV_CAP_SOL_MM)
from fetch_sim import fetch_sim_features, organiser_par_maille
from fetch_temp import temperatures_par_maille
from fetch_prevision import prevision_par_maille
from compute_index import calcul_indice, niveau, lag_jours, refroidissement, modulation_choc
from versant import stress_hydrothermique, indice_module
from backfill_hist import historique_a_jour


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


def construire_prevision(terrain, mailles, prev_list, hist_fin, historique=None):
    """Série d'indice PROJETÉE dans le futur (prévision de sortie).

    Pluie/température/ET0 : prévision Open-Meteo. SWI : projeté par bilan hydrique
    à partir du dernier SWI connu — SWI(j+1)=clamp(SWI+(pluie−ET0)/CAP,0,1). On
    ancre de préférence sur le dernier SWI SAFRAN de l'historique (continuité de
    la timeline), à défaut sur le SWI live SIM.
    Renvoie {dates, mailles:{mid:{i,s,w,p,t}}} (mêmes clés que l'historique) ou None."""
    from datetime import date as _date
    futures = set()
    for pv in prev_list:
        futures.update(d for d in pv if d > hist_fin)
    dates = sorted(futures)[:PREV_HORIZON_JOURS]
    if not dates:
        return None
    hist_m = (historique or {}).get("mailles", {})
    pos = {d: i for i, d in enumerate(dates)}
    out = {}
    for i, cell in enumerate(terrain):
        mid = cell["maille_id"]; m = mailles.get(mid)
        pv = prev_list[i] if i < len(prev_list) else {}
        if not m or not pv:
            continue
        coef = m.get("coef_terrain", 1.0)
        # ancre SWI : dernier SWI SAFRAN de l'historique (continuité) sinon live.
        w_hist = hist_m.get(mid, {}).get("w") if hist_m else None
        swi = (w_hist[-1] if w_hist and w_hist[-1] is not None else m.get("swi"))
        pvdates = sorted(pv)
        # projection du SWI jour par jour au-delà de l'ancre (hist_fin)
        swi_by = {}
        cur = swi
        for d in pvdates:
            if d <= hist_fin:
                swi_by[d] = swi
                continue
            pr = pv[d].get("precip"); et = pv[d].get("et0")
            if cur is not None and pr is not None and et is not None:
                cur = max(0.0, min(1.0, cur + (pr - et) / PREV_CAP_SOL_MM))
            swi_by[d] = cur
        cols = {k: [None] * len(dates) for k in ("i", "s", "w", "p", "t")}
        for d in dates:
            dd = _date.fromisoformat(d)
            p15 = sum(pv[x]["precip"] for x in pvdates
                      if pv[x]["precip"] is not None
                      and 0 <= (dd - _date.fromisoformat(x)).days <= 14)
            tp = pv[d].get("temp"); sw = swi_by.get(d)
            r = calcul_indice(sw, p15, tp, coef)
            R = refroidissement([pv[x].get("temp") for x in pvdates if x <= d])
            k = pos[d]
            cols["i"][k] = modulation_choc(r["indice"], R, tp)
            cols["s"][k] = round(stress_hydrothermique(sw, tp), 3) if sw is not None else None
            cols["w"][k] = round(sw, 3) if sw is not None else None
            cols["p"][k] = round(p15, 1)
            cols["t"][k] = round(tp, 1) if tp is not None else None
        out[mid] = cols
    return {"dates": dates, "mailles": out}


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
        tinfo = temps[i] if i < len(temps) else {}
        temp_par_date = tinfo.get("par_date", {}) if tinfo else {}
        temp_recent = tinfo.get("recent") if tinfo else None
        # Température d'un jour donné : vraie valeur Open-Meteo du jour, sinon
        # repli sur la moyenne récente (curseur temporel : chaque jour de la
        # série doit refléter SA température, plus une valeur figée).
        temp_du = lambda d: temp_par_date.get(d, temp_recent)
        # Coef terrain = géologie × couverture forestière × essence hôte.
        coef_geo = cell["coef_terrain"]
        coef_foret = cell.get("coef_foret", 1.0)
        coef_essence = cell.get("coef_essence", 1.0)
        coef = coef_geo * coef_foret * coef_essence
        hist_sim = sim.get(mid, {}).get("historique", [])

        # Série de température moyenne (Open-Meteo) pour le CHOC THERMIQUE :
        # un refroidissement récent déclenche la fructification (cf. compute_index).
        temp_dates = sorted(temp_par_date)
        def R_at(d):
            if not d:
                return None
            return refroidissement([temp_par_date[x] for x in temp_dates if x <= d])

        # Série d'indices sur l'historique, avec la vraie température de chaque
        # jour, l'indice MODULÉ par le choc thermique du jour, et `stress` pour
        # que le curseur recolore chaque face de forêt (versant) par jour.
        serie = []
        for h in hist_sim:
            t_h = temp_du(h["date"])
            r = calcul_indice(h["swi"], h["pluie_15j"], t_h, coef)
            ind_h = modulation_choc(r["indice"], R_at(h["date"]), t_h)
            serie.append({"date": h["date"], "swi": h["swi"],
                          "pluie_15j": h["pluie_15j"], "temp": t_h,
                          "indice": ind_h,
                          "stress": round(stress_hydrothermique(h["swi"], t_h), 3)})

        dernier = hist_sim[-1] if hist_sim else {}
        temp = temp_du(dernier.get("date"))   # température du jour courant
        res = calcul_indice(dernier.get("swi"), dernier.get("pluie_15j"), temp, coef)
        # Indice du jour modulé par le choc thermique (refroidissement récent).
        indice_jour = modulation_choc(res["indice"], R_at(dernier.get("date")), temp)
        niv_jour = niveau(indice_jour)
        if indice_jour is not None:
            indices_jour.append(indice_jour)

        # Stress hydro-thermique du jour (+1 sec/chaud, -1 froid/humide) : pilote
        # le SIGNE de la modulation versant (cf. versant.py). Identique au
        # dernier point de `serie` pour que carte du jour et curseur coïncident.
        stress = stress_hydrothermique(dernier.get("swi"), temp)

        mailles[mid] = {
            "maille_id": mid,
            "lat": cell.get("lat"),
            "lon": cell.get("lon"),
            "indice": indice_jour,
            "niveau": niv_jour,
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
                    ind = indice_module(indice_jour, o["expo"], stress)
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
                    "properties": {"maille_id": mid, "indice": indice_jour,
                                   "groupe": grp, "versant": None},
                })
        else:
            features.append({
                "type": "Feature", "geometry": cell["geometry"],
                "properties": {"maille_id": mid, "indice": indice_jour,
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

    # Historique long (rejeu dans le temps) : SAFRAN 2022→hier, complété du
    # plus récent disponible. Absent si data/historique.json manque (le curseur
    # côté page retombe alors sur la série 15 j).
    print("    Historique (rejeu long)…")
    historique = historique_a_jour()
    if historique:
        print(f"    {historique['n_jours']} jours "
              f"({historique['debut']} → {historique['fin']})")

    print("    Prévision de sortie (Open-Meteo)…")
    hist_fin = historique["fin"] if historique else date_donnees
    prevision = construire_prevision(terrain, mailles, prevision_par_maille(coords),
                                     hist_fin, historique)
    if prevision:
        print(f"    prévision {prevision['dates'][0]} → {prevision['dates'][-1]} "
              f"({len(prevision['dates'])} j)")

    payload = {
        "genere_le": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "date_donnees": date_donnees,
        "moyenne_departement": moyenne,
        "niveau_departement": niveau(moyenne),
        "lag_indicatif": LAG_JOURS_PLAINE,
        "versant_k": VERSANT_K,
        "choc": {"k": CHOC_K, "k_chaud": CHOC_K_CHAUD, "min": CHOC_MIN,
                 "opt": CHOC_OPT, "recent": CHOC_FENETRE_RECENTE,
                 "ref": CHOC_FENETRE_REF, "tmin": TEMP_MIN, "tob": TEMP_OPT_BAS,
                 "toh": TEMP_OPT_HAUT, "tmax": TEMP_MAX},
        "nb_mailles": len({f["properties"]["maille_id"] for f in features}),
        "essence_groupes": essence_groupes,
        "top": top,
        "mailles": mailles,
        "historique": historique,
        "prevision": prevision,
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
