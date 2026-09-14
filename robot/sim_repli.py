"""Repli SIM : compléter le flux WFS (DREAL Bretagne) par SAFRAN QUOT_SIM2_latest.

Pourquoi : le WFS `dreal_b:qry_sorties_sim` n'est qu'un relais — la DREAL
moissonne chaque jour le point SIM de Météo-France et le republie. Quand ce
moissonnage rate (12/09/2026 : un lot entier à date nulle, puis plus rien), la
carte reste figée alors que Météo-France, elle, continue de publier
`QUOT_SIM2_latest.csv.gz` (data.gouv, J-1) : même modèle SAFRAN-ISBA, même
grille 8 km, même SWI. Ce fichier est DÉJÀ ingéré par backfill_hist pour
l'historique long ; on s'en sert ici pour prolonger la série « live » du WFS.

Contrat : appeler `completer_sim` AVANT toute correction stations de
l'historique (corrige_historique_complet / corrige_historique modifient w/p en
place) — run.py applique ensuite `corrige()` à la série live, donc les valeurs
doivent être du SAFRAN brut, sinon double correction.

Pur Python (pas de shapely) : testable depuis n'importe où.
"""
from fraicheur import iso_ok

SOURCE_REPLI = "safran_latest"


def completer_sim(sim, historique, sim_fin):
    """Ajoute à `sim[mid]["historique"]` les jours de `historique` postérieurs à
    `sim_fin` (dernier jour lisible du WFS ; None = WFS vide).

    `sim`        : sortie de fetch_sim.organiser_par_maille (modifiée en place ;
                   une maille absente du WFS est créée sans géométrie — la carte
                   n'utilise que les géométries du terrain).
    `historique` : sortie de backfill_hist.historique_a_jour, NON corrigée.

    Renvoie {"points": n, "jours": [dates ajoutées], "fin": nouvelle fin}
    (points = 0 si rien à compléter)."""
    vide = {"points": 0, "jours": [], "fin": sim_fin}
    if not historique:
        return vide
    dates = historique.get("dates") or []
    idx = [k for k, d in enumerate(dates)
           if iso_ok(d) and (sim_fin is None or d > sim_fin)]
    if not idx:
        return vide
    n = 0
    jours = set()
    for mid, arr in (historique.get("mailles") or {}).items():
        w = arr.get("w") or []
        p = arr.get("p") or []
        entree = sim.get(mid)
        hist = entree["historique"] if entree else []
        deja = {h.get("date") for h in hist}
        for k in idx:
            d = dates[k]
            if d in deja:
                continue
            swi = w[k] if k < len(w) else None
            pl = p[k] if k < len(p) else None
            if swi is None and pl is None:
                continue        # jour vide dans l'historique : rien à apporter
            hist.append({"date": d, "swi": swi, "anomalie_swi": None,
                         "etr": None, "pluie_15j": pl, "source": SOURCE_REPLI})
            jours.add(d)
            n += 1
        if hist and entree is None:      # maille absente du WFS : créée sans géométrie
            sim[mid] = {"lat": None, "lon": None, "geometry": None, "historique": hist}
        hist.sort(key=lambda h: h["date"])
    if not n:
        return vide
    jours = sorted(jours)
    return {"points": n, "jours": jours, "fin": jours[-1]}


def message_repli(rep, wfs_fin):
    """Phrase FR pour le log et l'encadré de la page."""
    if not rep or not rep["points"]:
        return None
    from fraicheur import parse_iso
    fr = lambda s: (parse_iso(s).strftime("%d/%m") if parse_iso(s) else "?")
    j = rep["jours"]
    periode = (f"le {fr(j[0])} vient" if len(j) == 1
               else f"les jours {fr(j[0])} → {fr(j[-1])} viennent")
    if wfs_fin:
        return (f"Humidité des sols : le flux DREAL Bretagne s'arrête au "
                f"{fr(wfs_fin)} ; {periode} directement de Météo-France "
                f"(SAFRAN quotidien).")
    return (f"Humidité des sols : flux DREAL Bretagne indisponible ; {periode} "
            f"directement de Météo-France (SAFRAN quotidien).")
