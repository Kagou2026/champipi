"""Récupération des postes quotidiens Météo-France (réseau climatologique).

Données open-data, SANS clé : « Données climatologiques de base - quotidiennes »
(data.gouv, org Météo-France). Un fichier CSV.gz par département sur le S3 OVH.
On lit la pluie quotidienne (RR) des ~15 derniers jours pour la Lozère et ses
départements limitrophes, afin de CORRIGER localement le cumul 15 j de SAFRAN
(trop lissé à 8 km pour les orages cévenols). Cf. mémoire champipi-pluie-fiabilite.

On lit AUSSI la température quotidienne (TN mini, TX maxi, TM moyenne) quand elle
est présente. ATTENTION : seul le réseau PRINCIPAL (QUOT) la renseigne — le réseau
complémentaire (QUOT_COMP) est fait de pluviomètres, la température y est vide
partout, MALGRÉ un en-tête de fichier identique « …_RR-T-Vent ». La température
sert au « choc thermique » observé (refroidissement récent → appel à pousse).

Format CSV : séparateur ';', encodage latin-1, colonnes utiles
  NUM_POSTE ; NOM_USUEL ; LAT ; LON ; ALTI ; AAAAMMJJ ; RR (pluie mm/j) ;
  TN (mini °C) ; TX (maxi °C) ; TM (moyenne °C) ; TNTXM ((TN+TX)/2) ; ...
Un champ vide = valeur NON renseignée (pour RR, à ne pas confondre avec 0 mm ;
pour TM, on retombe sur TNTXM puis sur (TN+TX)/2).
"""
import csv
import gzip
import io
from datetime import date, timedelta

import requests

from config import (STATION_DEPTS, STATION_CSV_URLS, STATION_FENETRE_JOURS,
                    STATION_SERIE_JOURS, STATION_FRESH_OK_J, STATION_FRESH_MUET_J,
                    STATION_COUV_MIN)


def _flt(row, key):
    """float d'une cellule CSV, ou None si vide/absente/non numérique."""
    try:
        v = row.get(key, "")
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_stations(depts=None, fenetre=None, timeout=120):
    """Renvoie une liste de stations : {num, nom, lat, lon, alti, rr:{AAAAMMJJ: mm}}.

    Seules les stations avec coordonnées valides et au moins un jour de pluie
    renseigné dans la fenêtre sont conservées. Un département injoignable est
    simplement sauté (dégradation propre).
    """
    depts = depts or STATION_DEPTS
    fenetre = fenetre or STATION_FENETRE_JOURS
    # on garde une marge (fenetre + 3 j) pour absorber le décalage de publication
    limite = (date.today() - timedelta(days=fenetre + 3)).strftime("%Y%m%d")
    stations = {}
    # Deux réseaux (principal + complémentaire) par département, fusionnés par
    # NUM_POSTE. Chaque source manquante est simplement sautée.
    for dep in depts:
        for tmpl in STATION_CSV_URLS:
            url = tmpl.format(dep=dep)
            try:
                r = requests.get(url, timeout=timeout)
                r.raise_for_status()
            except requests.exceptions.RequestException:
                continue
            texte = gzip.decompress(r.content).decode("latin-1")
            for row in csv.DictReader(io.StringIO(texte), delimiter=";"):
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
                        "num": num, "nom": (row.get("NOM_USUEL") or "").strip(),
                        "lat": lat, "lon": lon, "alti": alti, "rr": {}, "t": {}}
                rr = _flt(row, "RR")
                if rr is not None:
                    st["rr"][jour] = rr   # RR manquant : jour non inscrit (≠ 0 mm)
                # Température : TN/TX bruts, TM = moyenne (TM sinon TNTXM sinon
                # (TN+TX)/2). Réseau complémentaire → tout None, jour non inscrit.
                tn = _flt(row, "TN"); tx = _flt(row, "TX")
                tm = _flt(row, "TM")
                if tm is None:
                    tm = _flt(row, "TNTXM")
                if tm is None and tn is not None and tx is not None:
                    tm = (tn + tx) / 2.0
                if tn is not None or tx is not None or tm is not None:
                    st["t"][jour] = (tn, tx, tm)
    return [s for s in stations.values() if s["rr"]]


def cumul_15j(station, fin_iso, min_jours=10):
    """Cumul RR (mm) sur la fenêtre de 15 j finissant à `fin_iso` (ISO AAAA-MM-JJ).

    Tolère quelques trous : si au moins `min_jours` sont renseignés, on
    extrapole à la fenêtre pleine (somme × 15 / n). Sinon None (station trop
    lacunaire pour être fiable ici).
    """
    fin = date.fromisoformat(fin_iso)
    vals = []
    for k in range(15):
        j = (fin - timedelta(days=k)).strftime("%Y%m%d")
        v = station["rr"].get(j)
        if v is not None:
            vals.append(v)
    if len(vals) < min_jours:
        return None
    return sum(vals) * 15.0 / len(vals)


def serie_pluie(station, fin_iso, jours=None):
    """Série quotidienne [ (AAAA-MM-JJ, mm|None), ... ] pour la fiche du calque.

    `jours` jours (défaut STATION_SERIE_JOURS) finissant à `fin_iso`, ordre
    chronologique, jour courant en dernier. Purement pour l'AFFICHAGE : le cumul
    qui sert à la correction reste calculé sur 15 j (cf. cumul_15j)."""
    jours = jours or STATION_SERIE_JOURS
    fin = date.fromisoformat(fin_iso)
    out = []
    for k in range(jours - 1, -1, -1):
        d = fin - timedelta(days=k)
        out.append((d.isoformat(), station["rr"].get(d.strftime("%Y%m%d"))))
    return out


def a_temperature(station):
    """True si la station porte au moins un jour de température (réseau principal)."""
    return bool(station.get("t"))


def serie_temp(station, fin_iso, jours=None):
    """Série de température [ (AAAA-MM-JJ, tn, tx, tm), ... ] pour la fiche.

    `jours` jours (défaut STATION_SERIE_JOURS) finissant à `fin_iso`, ordre
    chronologique (jour courant en dernier). Chaque valeur manquante = None. Une
    station sans température renvoie une série de (date, None, None, None)."""
    jours = jours or STATION_SERIE_JOURS
    fin = date.fromisoformat(fin_iso)
    t = station.get("t", {})
    out = []
    for k in range(jours - 1, -1, -1):
        d = fin - timedelta(days=k)
        tn, tx, tm = t.get(d.strftime("%Y%m%d"), (None, None, None))
        out.append((d.isoformat(), tn, tx, tm))
    return out


def etat_fraicheur(station, frontier_iso, fenetre=None):
    """État de fraîcheur / santé d'une station vis-à-vis du FRONT du réseau.

    `frontier_iso` = jour le plus récent publié par l'ENSEMBLE du réseau. On juge
    chaque poste par rapport aux autres (et non à « aujourd'hui »), ce qui absorbe
    le délai de publication du paquet climato : il est commun à tous les postes.

    Renvoie un dict :
      dernier   : AAAA-MM-JJ du dernier jour renseigné (None si aucun) ;
      retard_j  : nb de jours entre `frontier` et `dernier` (None si aucun) ;
      jours     : nb de jours renseignés sur la fenêtre ;
      fenetre   : taille de la fenêtre examinée ;
      etat      : "ok" (à jour) | "lacunaire" (partiel/un peu en retard) |
                  "muet" (rien depuis longtemps → probablement hors service).

    NB : "muet" est un signal de FIABILITÉ des données, pas un diagnostic
    officiel de panne (impossible à établir depuis cette seule source)."""
    fenetre = fenetre or STATION_SERIE_JOURS
    fin = date.fromisoformat(frontier_iso)
    n = 0
    for k in range(fenetre):
        j = (fin - timedelta(days=k)).strftime("%Y%m%d")
        if station["rr"].get(j) is not None:
            n += 1
    if not station["rr"]:
        return {"dernier": None, "retard_j": None, "jours": 0,
                "fenetre": fenetre, "etat": "muet"}
    dj = max(station["rr"])                      # AAAAMMJJ le plus récent renseigné
    dernier = f"{dj[:4]}-{dj[4:6]}-{dj[6:]}"
    retard = (fin - date.fromisoformat(dernier)).days
    couv = n / fenetre if fenetre else 0.0
    if retard > STATION_FRESH_MUET_J:
        etat = "muet"
    elif retard > STATION_FRESH_OK_J or couv < STATION_COUV_MIN:
        etat = "lacunaire"
    else:
        etat = "ok"
    return {"dernier": dernier, "retard_j": retard, "jours": n,
            "fenetre": fenetre, "etat": etat}
