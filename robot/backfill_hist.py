"""Backfill de l'historique Champipi à partir des CSV SAFRAN/SIM quotidiens.

Source : Météo-France « Données changement climatique - SIM quotidienne »
(meteo.data.gouv.fr, licence ouverte). Même grille 8 km et même définition de
SWI que le flux temps réel → l'indice historique est comparable à l'indice du
jour. On y prend aussi la pluie quotidienne (PRELIQ+PRENEI, cumulée sur 15 j) et
la température (T, SAFRAN), ce qui évite tout appel API externe pour l'historique.

Sortie : data/historique.json, compact :
    {"genere_le":..., "debut":"2022-01-01", "fin":"...",
     "dates":[...],                       # dates globales triées
     "mailles": {maille_id: {"i":[indice...], "s":[stress...]}}}  # alignés sur dates

Le robot quotidien (run.py) complétera ce fichier avec le jour courant et
embarquera la série longue dans la page.
"""
import csv
import gzip
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

import requests

sys.path.insert(0, "robot")  # permet l'import direct quand lancé depuis la racine
from config import TERRAIN_FILE  # noqa: E402
from compute_index import calcul_indice, refroidissement, modulation_choc  # noqa: E402
from versant import stress_hydrothermique  # noqa: E402

DATASET_API = ("https://www.data.gouv.fr/api/1/datasets/"
               "donnees-changement-climatique-sim-quotidienne/")
GRILLE_MAP = "data/safran_grille.json"
HIST_OUT = "data/historique.json"

DEBUT = "2022-01-01"                 # première date exposée
WARMUP = "2021-12-15"               # lu en amont pour le cumul pluie 15 j


def _fmt(d):  # "20220101" -> "2022-01-01"
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def lister_sources(an_min=None):
    """URLs des CSV.gz SIM annuels (année >= an_min) + le fichier 'latest'.

    Le jeu est publié par année (QUOT_SIM2_YYYY). Backfill : an_min = année
    d'amorçage (DEBUT-1, pour le cumul pluie 15 j à cheval sur le 1er janvier).
    Mise à jour quotidienne : an_min = année courante (fichier léger) + 'latest'.
    """
    if an_min is None:
        an_min = int(WARMUP[:4])
    js = requests.get(DATASET_API, timeout=60).json()
    annees, latest = [], None
    for r in js.get("resources", []):
        if r.get("format") != "csv.gz":
            continue
        titre = r.get("title", "")
        if titre == "QUOT_SIM2_latest":
            latest = (titre, r["url"]); continue
        if titre.startswith("QUOT_SIM2_"):
            suf = titre.rsplit("_", 1)[-1]
            if suf.isdigit() and int(suf) >= an_min:
                annees.append((int(suf), titre, r["url"]))
    annees.sort()
    urls = [(t, u) for _, t, u in annees]
    if latest:
        urls.append(latest)
    return urls


def charger_grille():
    mp = json.load(open(GRILLE_MAP, encoding="utf-8"))
    # (lambx, lamby) -> maille_id
    rev = {(v[0], v[1]): mid for mid, v in mp.items()}
    return mp, rev


def charger_coef():
    cells = json.load(open(TERRAIN_FILE, encoding="utf-8"))["cellules"]
    coef = {}
    for c in cells:
        coef[c["maille_id"]] = (c["coef_terrain"] * c.get("coef_foret", 1.0)
                                * c.get("coef_essence", 1.0))
    return coef


def moissonner(urls, rev):
    """Renvoie {maille_id: {date: {"preliq":mm, "t":°C, "swi":x}}} filtré sur nos points."""
    data = {}
    cols = None
    for titre, url in urls:
        print(f"  … {titre}")
        n_match = 0
        with requests.get(url, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            gz = gzip.GzipFile(fileobj=resp.raw)
            reader = csv.reader(io.TextIOWrapper(gz, encoding="utf-8"), delimiter=";")
            header = next(reader)
            idx = {name: i for i, name in enumerate(header)}
            need = ("LAMBX", "LAMBY", "DATE", "PRELIQ", "PRENEI", "T", "SWI")
            ix = {k: idx[k] for k in need}
            for row in reader:
                try:
                    key = (int(row[ix["LAMBX"]]), int(row[ix["LAMBY"]]))
                except (ValueError, IndexError):
                    continue
                mid = rev.get(key)
                if mid is None:
                    continue
                d = _fmt(row[ix["DATE"]])
                if d < WARMUP:
                    continue
                def f(col):
                    v = row[ix[col]]
                    try:
                        return float(v)
                    except ValueError:
                        return None
                preliq = f("PRELIQ"); prenei = f("PRENEI")
                pluie = (preliq or 0.0) + (prenei or 0.0)
                data.setdefault(mid, {})[d] = {
                    "pluie": pluie, "t": f("T"), "swi": f("SWI")}
                n_match += 1
        print(f"     {n_match} lignes retenues")
    return data


def series_par_maille(data, coef, debut=DEBUT):
    """{maille_id: {date: (indice, stress, swi, pluie15, temp)}} pour dates >= `debut`.

    Le cumul pluie 15 j utilise la fenêtre complète (y compris des jours
    antérieurs à `debut`, présents dans `data` pour l'amorçage). On stocke aussi
    swi/pluie/temp pour que le volet latéral puisse suivre la date choisie."""
    out = {}
    for mid, jours in data.items():
        dates = sorted(jours)
        tlist = [jours[x]["t"] for x in dates]   # série de température (chrono)
        s = {}
        for i, d in enumerate(dates):
            if d < debut:
                continue
            fenetre = dates[max(0, i - 14):i + 1]
            pluie15 = sum(jours[x]["pluie"] for x in fenetre
                          if jours[x]["pluie"] is not None)
            swi = jours[d]["swi"]; t = jours[d]["t"]
            r = calcul_indice(swi, pluie15, t, coef.get(mid, 1.0))
            # Choc thermique : refroidissement sur la série jusqu'à ce jour.
            R = refroidissement(tlist[:i + 1])
            indice = modulation_choc(r["indice"], R, t)
            s[d] = (indice, round(stress_hydrothermique(swi, t), 3),
                    None if swi is None else round(swi, 3),
                    round(pluie15, 1),
                    None if t is None else round(t, 1))
        out[mid] = s
    return out


def aligner(par_maille):
    """{mid: {date:(i,s,w,p,t)}} -> (dates triées, {mid: {"i","s","w","p","t"}}).

    i=indice, s=stress, w=SWI, p=pluie 15 j (mm), t=température (°C)."""
    toutes = set()
    for s in par_maille.values():
        toutes.update(s)
    dates = sorted(toutes)
    pos = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    mailles = {}
    for mid, s in par_maille.items():
        cols = {k: [None] * n for k in ("i", "s", "w", "p", "t")}
        for d, (ind, stre, swi, pl, tp) in s.items():
            k = pos[d]
            cols["i"][k] = ind; cols["s"][k] = stre
            cols["w"][k] = swi; cols["p"][k] = pl; cols["t"][k] = tp
        mailles[mid] = cols
    return dates, mailles


def _next(d):
    return (datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()


def historique_a_jour():
    """Charge data/historique.json (committé) et le complète avec les jours
    récents non encore présents (SAFRAN année courante + 'latest'), sans le
    réécrire. Renvoie {"debut","fin","n_jours","dates","mailles"} ou None si le
    fichier de base est absent (le robot retombera alors sur la série 15 j).

    Lignée SAFRAN de bout en bout : mêmes SWI/pluie/température que le backfill,
    donc le rejeu long est homogène (pas de mélange avec la source du jour)."""
    if not os.path.exists(HIST_OUT):
        return None
    h = json.load(open(HIST_OUT, encoding="utf-8"))
    dates = h["dates"]
    par = {}
    for mid, arr in h["mailles"].items():
        d = {}
        for i, dt in enumerate(dates):
            if arr["i"][i] is not None:
                d[dt] = (arr["i"][i], arr["s"][i],
                         arr.get("w", [None] * len(dates))[i],
                         arr.get("p", [None] * len(dates))[i],
                         arr.get("t", [None] * len(dates))[i])
        par[mid] = d
    hmax = dates[-1] if dates else DEBUT

    try:
        _, rev = charger_grille()
        coef = charger_coef()
        urls = lister_sources(an_min=date.today().year)   # année courante + latest
        data = moissonner(urls, rev)
        recent = series_par_maille(data, coef, debut=_next(hmax))
        n_new = sum(len(s) for s in recent.values())
        for mid, s in recent.items():
            par.setdefault(mid, {}).update(s)
        print(f"    historique complété : +{n_new} points après {hmax}")
    except Exception as e:   # top-up best-effort : on garde au moins le committé
        print(f"    (top-up historique ignoré : {e})")

    dates2, mailles2 = aligner(par)
    return {"debut": dates2[0] if dates2 else None,
            "fin": dates2[-1] if dates2 else None,
            "n_jours": len(dates2), "dates": dates2, "mailles": mailles2}


def main():
    print("1/4 Sources de données…")
    urls = lister_sources()
    for t, _ in urls:
        print("   -", t)
    print("2/4 Grille + coefficients terrain…")
    _, rev = charger_grille()
    coef = charger_coef()
    print("3/4 Moisson (filtrée sur 82 mailles Lozère)…")
    data = moissonner(urls, rev)
    print(f"   {len(data)} mailles moissonnées")
    print("4/4 Calcul indice + stress et écriture…")
    par = series_par_maille(data, coef, DEBUT)
    dates, mailles = aligner(par)
    payload = {"debut": DEBUT, "fin": dates[-1] if dates else None,
               "n_jours": len(dates), "dates": dates, "mailles": mailles}
    json.dump(payload, open(HIST_OUT, "w", encoding="utf-8"),
              separators=(",", ":"))
    import os
    mo = os.path.getsize(HIST_OUT) / 1e6
    print(f"\nOK. {len(dates)} jours ({dates[0]} → {dates[-1]}), "
          f"{len(mailles)} mailles → {HIST_OUT} ({mo:.1f} Mo)")


if __name__ == "__main__":
    main()
