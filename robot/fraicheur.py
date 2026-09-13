"""Dates sûres + fraîcheur des sources.

Deux services rendus au reste du robot :

1. ``parse_iso`` / ``iso_ok`` : lire une date « AAAA-MM-JJ » sans jamais lever
   d'exception. Leçon du 12/09/2026 : le WFS SIM a servi des lignes à date
   nulle, ``date.fromisoformat('')`` a explosé à l'étape 4 et les trois
   départements sont tombés. Toute date venant d'une SOURCE EXTERNE passe
   désormais par ici ; une date illisible vaut None et le calcul continue
   sans elle (au pire une correction en moins, jamais un plantage).

2. ``evaluer`` : dire si chaque source est À JOUR, EN RETARD ou PÉRIMÉE par
   rapport à aujourd'hui, pour prévenir (log de l'action + page) au lieu de
   laisser croire que la carte reflète la météo du jour. Les seuils tiennent
   compte du délai NORMAL de publication de chaque source (SIM = J-1,
   paquet climato des stations = 1 à 3 j) : un retard « normal » n'alerte pas.
"""
from datetime import date, timedelta

# retard (jours) au-delà duquel une source passe « retard » puis « périmé ».
# Tolérances = délai de publication habituel + marge d'un run raté.
SEUILS = {
    #             (retard, périmé)   délai normal
    "sim":        (3, 6),        # J-1 (SAFRAN-ISBA, WFS DREAL Bretagne)
    "stations":   (5, 9),        # paquet climato Météo-France : J-1 à J-3
    "historique": (3, 6),        # rejeu long, doit suivre SIM
    "temp":       (2, 5),        # Open-Meteo : archive J-1 / prévision
}
ETAT_ORDRE = {"ok": 0, "retard": 1, "perime": 2, "absent": 3}


def parse_iso(s):
    """'2026-08-29', '2026-08-29Z' ou '2026-08-29T00:00' -> date ; sinon None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().rstrip("Z")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def iso_ok(s):
    """True si `s` est une date ISO lisible."""
    return parse_iso(s) is not None


def iso_ymd(s8):
    """'20260829' (AAAAMMJJ, clés des séries stations) -> '2026-08-29' ; None si illisible."""
    if not s8 or not isinstance(s8, str) or len(s8) != 8 or not s8.isdigit():
        return None
    iso = f"{s8[:4]}-{s8[4:6]}-{s8[6:]}"
    return iso if iso_ok(iso) else None


def _etat(retard, cle):
    if retard is None:
        return "absent"
    s_ret, s_per = SEUILS[cle]
    if retard > s_per:
        return "perime"
    if retard > s_ret:
        return "retard"
    return "ok"


def evaluer(sources, aujourdhui=None):
    """Évalue la fraîcheur des sources d'un département.

    ``sources`` : {cle: date_iso|None} pour les clés de SEUILS, plus
    ``prevision`` (bool, dispo ou non). Renvoie
      {"date": aujourd'hui, "sources": {cle: {date, retard_j, etat}},
       "etat": pire état, "avertissements": [phrases FR]}
    Les phrases servent telles quelles au log et à la page."""
    today = aujourdhui or date.today()
    libelle = {"sim": "Humidité des sols (SAFRAN-ISBA)",
               "stations": "Pluviomètres Météo-France",
               "historique": "Historique long (rejeu)",
               "temp": "Températures (Open-Meteo)"}
    out, avert, pire = {}, [], "ok"
    for cle in SEUILS:
        d = parse_iso(sources.get(cle))
        retard = (today - d).days if d else None
        etat = _etat(retard, cle)
        out[cle] = {"date": d.isoformat() if d else None,
                    "retard_j": retard, "etat": etat}
        if ETAT_ORDRE[etat] > ETAT_ORDRE[pire]:
            pire = etat
        if etat == "absent":
            avert.append(f"{libelle[cle]} : aucune donnée reçue.")
        elif etat == "perime":
            avert.append(f"{libelle[cle]} : dernière donnée du {d.strftime('%d/%m')} "
                         f"({retard} j) — la carte ne reflète plus la météo actuelle.")
        elif etat == "retard":
            avert.append(f"{libelle[cle]} : dernière donnée du {d.strftime('%d/%m')} "
                         f"({retard} j de retard).")
    if not sources.get("prevision"):
        out["prevision"] = {"etat": "absent"}
        avert.append("Prévision de sortie indisponible (Open-Meteo).")
        if ETAT_ORDRE["retard"] > ETAT_ORDRE[pire]:
            pire = "retard"
    else:
        out["prevision"] = {"etat": "ok"}
    return {"date": today.isoformat(), "sources": out, "etat": pire,
            "avertissements": avert}
