"""Backfill de l'HISTORIQUE des stations Météo-France (pluie + température).

Pourquoi : le calque « stations » de la carte n'affichait qu'une PHOTO du jour
(les 30 derniers jours téléchargés par ``fetch_stations``). Quand on remontait
le curseur temporel d'un an, les jauges de pluie, les thermomètres et les badges
❄ ne bougeaient pas — ils montraient toujours avant-hier. Ce script constitue la
série longue pour que le calque suive la date, exactement comme les mailles.

Source (open-data, sans clé) : « Données climatologiques de base - quotidiennes »
(S3 Météo-France). Deux réseaux par département :
  * QUOT       (principal)      : RR + TN/TX/TM ;
  * QUOT_COMP  (complémentaire) : pluviomètres, RR seul (T° vide partout).
Deux tranches par réseau :
  * ``previous-1950-2024`` : l'archive (~10-16 Mo/dépt en QUOT) ;
  * ``latest-2025-2026``   : la tranche courante, déjà utilisée par le robot.

Sortie : ``data/stations_hist.json``, compact et SPARSE. Les séries sont des
CHAÎNES de valeurs séparées par des virgules (vide = jour non renseigné), en
DIXIÈMES d'unité (mm ou °C) :

    {"debut":"2021-12-01","fin":"2026-08-21","n":1725,
     "st":{"48001001":{"n":"MENDE","y":44.5,"x":3.5,"a":731,
                       "rr":"0,,12,3,...", "tn":"...", "tx":"...", "tm":"..."}}}

Une station sans température (pluviomètre) n'a PAS les clés tn/tx/tm — ce qui
évite d'écrire ~1700 virgules pour rien et divise le poids du fichier.

Le décodage côté page : ``s.split(",").map(v => v === "" ? null : +v / 10)``.

``debut`` est volontairement antérieur au 1er janvier 2022 (début de la timeline
du site) : le cumul 15 j et la fenêtre de choc thermique du 1er janvier 2022 ont
besoin des jours de décembre 2021 pour être calculables.
"""
import csv
import gzip
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

import requests

sys.path.insert(0, "robot")   # import direct quand lancé depuis la racine
from config import STATION_DEPTS   # noqa: E402

HIST_OUT = "data/stations_hist.json"

# Début des DONNÉES stockées : ~1 mois avant le début de la timeline du site
# (2022-01-01), pour amorcer le cumul 15 j et la fenêtre de choc thermique.
DEBUT = "2021-12-01"

BASE = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE"
# (gabarit d'URL, porte-t-il la température ?) — l'ordre importe peu, on fusionne.
SOURCES = [
    (BASE + "/QUOT/Q_{dep}_previous-1950-2024_RR-T-Vent.csv.gz", True),
    (BASE + "/QUOT/Q_{dep}_latest-2025-2026_RR-T-Vent.csv.gz", True),
    (BASE + "/QUOT_COMP/Q-COMP_{dep}_previous-1950-2024_RR-T-Vent.csv.gz", False),
    (BASE + "/QUOT_COMP/Q-COMP_{dep}_latest-2025-2026_RR-T-Vent.csv.gz", False),
]


def _flt(row, key):
    """float d'une cellule CSV, ou None si vide/absente/non numérique."""
    try:
        v = row.get(key, "")
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dixiemes(v):
    """°C ou mm -> dixièmes (entier), pour une sérialisation courte. None -> None."""
    return None if v is None else int(round(v * 10))


# tranche courante seule : ce qu'il faut pour un rattrapage quotidien (fichiers
# légers), sans retélécharger l'archive 1950-2024.
SOURCES_COURANTES = [s for s in SOURCES if "latest" in s[0]]


def moissonner(depts=None, debut=DEBUT, timeout=900, verbose=True, sources=None):
    """Télécharge et filtre les CSV. Renvoie {num: station} où station =
    {"nom","lat","lon","alti","rr":{AAAAMMJJ:mm},"t":{AAAAMMJJ:(tn,tx,tm)}}.

    Streaming : les fichiers font 10-16 Mo compressés (plusieurs centaines
    décompressés), on ne garde en mémoire que les jours >= `debut`.
    Une source absente (404) ou un département injoignable est simplement sauté.
    """
    depts = depts or STATION_DEPTS
    limite = debut.replace("-", "")
    stations = {}
    for dep in depts:
        for tmpl, _t in (sources or SOURCES):
            url = tmpl.format(dep=dep)
            try:
                with requests.get(url, stream=True, timeout=timeout) as resp:
                    if resp.status_code != 200:
                        if verbose:
                            print(f"     ⚠ {resp.status_code} {url.rsplit('/', 1)[-1]}")
                        continue
                    gz = gzip.GzipFile(fileobj=resp.raw)
                    reader = csv.DictReader(
                        io.TextIOWrapper(gz, encoding="latin-1"), delimiter=";")
                    n = 0
                    for row in reader:
                        jour = row.get("AAAAMMJJ", "")
                        if not jour or jour < limite:
                            continue
                        num = row.get("NUM_POSTE")
                        try:
                            lat = float(row["LAT"]); lon = float(row["LON"])
                            alti = float(row["ALTI"])
                        except (TypeError, ValueError, KeyError):
                            continue
                        st = stations.get(num)
                        if st is None:
                            st = stations[num] = {
                                "nom": (row.get("NOM_USUEL") or "").strip(),
                                "lat": lat, "lon": lon, "alti": alti,
                                "rr": {}, "t": {}}
                        rr = _flt(row, "RR")
                        if rr is not None:
                            st["rr"][jour] = rr       # vide ≠ 0 mm : jour non inscrit
                        tn = _flt(row, "TN"); tx = _flt(row, "TX")
                        tm = _flt(row, "TM")
                        if tm is None:
                            tm = _flt(row, "TNTXM")
                        if tm is None and tn is not None and tx is not None:
                            tm = (tn + tx) / 2.0
                        if tn is not None or tx is not None or tm is not None:
                            st["t"][jour] = (tn, tx, tm)
                        n += 1
            except requests.exceptions.RequestException as e:
                if verbose:
                    print(f"     ⚠ {type(e).__name__} sur {url.rsplit('/', 1)[-1]}")
                continue
            if verbose:
                print(f"     {dep} · {url.rsplit('/', 1)[-1][:34]:34} {n:7d} lignes")
    return stations


def _jours(debut, fin):
    d0 = date.fromisoformat(debut); d1 = date.fromisoformat(fin)
    return [(d0 + timedelta(days=k)).isoformat() for k in range((d1 - d0).days + 1)]


def _serie(valeurs, dates_aaaammjj):
    """[valeurs par jour] -> "12,,0,3" (dixièmes, vide = manquant), ou None si
    la série est intégralement vide (clé alors omise du JSON)."""
    out = []
    vide = True
    for j in dates_aaaammjj:
        v = valeurs.get(j)
        if v is None:
            out.append("")
        else:
            out.append(str(_dixiemes(v)))
            vide = False
    return None if vide else ",".join(out)


def construire(stations, debut=DEBUT, fin=None):
    """{num: station brute} -> payload compact prêt à écrire."""
    if fin is None:
        jours = set()
        for s in stations.values():
            jours.update(s["rr"]); jours.update(s["t"])
        if not jours:
            return None
        j = max(jours)
        fin = f"{j[:4]}-{j[4:6]}-{j[6:]}"
    dates = _jours(debut, fin)
    cles = [d.replace("-", "") for d in dates]
    st = {}
    for num, s in stations.items():
        rr = _serie(s["rr"], cles)
        if rr is None:
            continue                      # station sans une seule pluie : inutile
        e = {"n": s["nom"], "y": round(s["lat"], 5), "x": round(s["lon"], 5),
             "a": round(s["alti"]), "rr": rr}
        for i, k in enumerate(("tn", "tx", "tm")):
            serie = _serie({j: v[i] for j, v in s["t"].items() if v[i] is not None},
                           cles)
            if serie is not None:
                e[k] = serie
        st[num] = e
    return {"genere_le": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "debut": debut, "fin": fin, "n": len(dates), "st": st}


def ecrire(payload, out=HIST_OUT):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(out) / 1e6


# ---------------------------------------------------------------------------
# Mise à jour quotidienne (miroir de backfill_hist.historique_a_jour)
# ---------------------------------------------------------------------------
# Le gros fichier est COMMITTÉ une fois (ce backfill) ; le robot quotidien le
# complète en mémoire avec les jours parus depuis, sans le réécrire ni tout
# retélécharger. Deux chemins, du moins cher au plus sûr :
#   1. les 30 j déjà en mémoire (``fetch_stations`` les a téléchargés pour la
#      correction de pluie) suffisent tant que le fichier committé n'a pas plus
#      d'un mois de retard — cas normal : coût zéro ;
#   2. sinon on retélécharge la tranche « latest » (petite) à partir du jour
#      manquant : le site se répare tout seul si le fichier n'est pas committé
#      pendant des mois.

def charger(out=HIST_OUT):
    if not os.path.exists(out):
        return None
    return json.load(open(out, encoding="utf-8"))


def _tokens(valeurs, cles):
    """[cles AAAAMMJJ] -> (liste de jetons str, au moins une valeur ?)."""
    out = []; plein = False
    for j in cles:
        v = valeurs.get(j)
        if v is None:
            out.append("")
        else:
            out.append(str(_dixiemes(v))); plein = True
    return out, plein


def _prolonger(entree, cle, tokens, plein, n_base):
    """Ajoute `tokens` à la fin de la série `cle` d'une station (crée la série
    avec un préfixe vide de `n_base` jours si elle n'existait pas)."""
    ancienne = entree.get(cle)
    if ancienne is not None:
        entree[cle] = ancienne + "," + ",".join(tokens)
    elif plein:
        entree[cle] = ",".join([""] * n_base + tokens)


def fusionner(base, stations, debut_iso, fin_iso):
    """Prolonge le payload `base` de `debut_iso` à `fin_iso` avec {num: station}."""
    dates = _jours(debut_iso, fin_iso)
    cles = [d.replace("-", "") for d in dates]
    n_base = base["n"]
    vides = [""] * len(cles)
    for num, e in base["st"].items():
        s = stations.get(num)
        rr_t, rr_p = _tokens(s["rr"], cles) if s else (vides, False)
        _prolonger(e, "rr", rr_t, rr_p, n_base)
        for i, k in enumerate(("tn", "tx", "tm")):
            if s:
                t_t, t_p = _tokens({j: v[i] for j, v in s["t"].items()
                                    if v[i] is not None}, cles)
            else:
                t_t, t_p = vides, False
            _prolonger(e, k, t_t, t_p, n_base)
    # stations apparues depuis le backfill (poste neuf) : préfixe vide
    for num, s in stations.items():
        if num in base["st"]:
            continue
        rr_t, rr_p = _tokens(s["rr"], cles)
        if not rr_p:
            continue
        e = {"n": s.get("nom", ""), "y": round(s["lat"], 5), "x": round(s["lon"], 5),
             "a": round(s["alti"])}
        _prolonger(e, "rr", rr_t, rr_p, n_base)
        for i, k in enumerate(("tn", "tx", "tm")):
            t_t, t_p = _tokens({j: v[i] for j, v in s["t"].items()
                                if v[i] is not None}, cles)
            _prolonger(e, k, t_t, t_p, n_base)
        base["st"][num] = e
    base["n"] = n_base + len(cles)
    base["fin"] = fin_iso
    return base


def _memoire_en_dict(stations):
    """[station de fetch_stations] -> {num: station} (même forme que moissonner)."""
    return {s["num"]: {"nom": s.get("nom", ""), "lat": s["lat"], "lon": s["lon"],
                       "alti": s["alti"], "rr": s.get("rr", {}), "t": s.get("t", {})}
            for s in (stations or [])}


def stations_hist_a_jour(stations=None, out=HIST_OUT, verbose=True):
    """Renvoie le payload historique stations complété jusqu'au dernier jour
    publié, ou None si le fichier committé est absent (la page retombe alors sur
    la photo du jour, comme avant). Best-effort : en cas de pépin réseau on
    renvoie au moins le fichier committé."""
    base = charger(out)
    if base is None:
        return None
    suite = _next(base["fin"])
    mem = _memoire_en_dict(stations)
    jours_mem = set()
    for s in mem.values():
        jours_mem.update(s["rr"])
    # la mémoire couvre-t-elle le trou en entier ?
    debut_mem = min(jours_mem) if jours_mem else None
    couvre = debut_mem is not None and debut_mem <= suite.replace("-", "")
    try:
        if couvre:
            src = mem
        else:
            if verbose:
                print(f"    stations_hist : retard > fenêtre mémoire "
                      f"(depuis {base['fin']}) → retéléchargement tranche courante")
            src = moissonner(debut=suite, verbose=False,
                             sources=SOURCES_COURANTES)
        jours = set()
        for s in src.values():
            jours.update(s["rr"]); jours.update(s["t"])
        jours = {j for j in jours if j >= suite.replace("-", "")}
        if not jours:
            return base
        j = max(jours)
        fin = f"{j[:4]}-{j[4:6]}-{j[6:]}"
        base = fusionner(base, src, suite, fin)
        if verbose:
            print(f"    stations_hist complété : {base['debut']} → {base['fin']} "
                  f"({base['n']} j, {len(base['st'])} stations)")
    except Exception as e:      # top-up best-effort
        if verbose:
            print(f"    (top-up stations_hist ignoré : {type(e).__name__}: {e})")
    return base


def _next(d):
    return (datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()


def main():
    print(f"1/3 Moisson des archives stations ({len(STATION_DEPTS)} départements, "
          f"depuis {DEBUT})…")
    stations = moissonner()
    print(f"   {len(stations)} stations vues")
    print("2/3 Mise en forme compacte…")
    payload = construire(stations)
    if not payload:
        raise SystemExit("Aucune donnée station récupérée.")
    n_t = sum(1 for s in payload["st"].values() if "tm" in s)
    print(f"   {len(payload['st'])} stations retenues ({n_t} avec température), "
          f"{payload['n']} jours ({payload['debut']} → {payload['fin']})")
    print("3/3 Écriture…")
    mo = ecrire(payload)
    print(f"\nOK → {HIST_OUT} ({mo:.1f} Mo)")


if __name__ == "__main__":
    main()
