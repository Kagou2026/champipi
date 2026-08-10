"""Calcul de l'indice cèpe (0-100) pour une maille.

L'indice combine trois sous-scores normalisés (0-1) :
  - humidité du sol (SWI)              -> poids POIDS_HUMIDITE
  - cumul de pluie sur 15 jours        -> poids POIDS_PLUIE
  - température moyenne récente        -> poids POIDS_TEMPERATURE

La somme pondérée (0-1) est mise à l'échelle 0-100, puis multipliée par le
coefficient terrain (géologie : calcaire fortement pénalisé).

Ces seuils viennent de la biologie du cèpe et de sources de cueilleurs ;
ils sont volontairement explicites dans config.py pour être recalés ensuite.
"""
from config import (
    SWI_MIN, SWI_OPTIMAL,
    PLUIE_15J_MIN, PLUIE_15J_OPTIMAL,
    TEMP_MIN, TEMP_OPT_BAS, TEMP_OPT_HAUT, TEMP_MAX,
    POIDS_HUMIDITE, POIDS_PLUIE, POIDS_TEMPERATURE,
    LAG_JOURS_PLAINE, LAG_JOURS_ALTITUDE, ALTITUDE_SEUIL,
    CHOC_FENETRE_RECENTE, CHOC_FENETRE_REF, CHOC_MIN, CHOC_OPT,
    CHOC_K, CHOC_K_CHAUD,
)


def _rampe(x, bas, haut):
    """Monte linéairement de 0 (à `bas`) vers 1 (à `haut`)."""
    if x is None:
        return None
    if x <= bas:
        return 0.0
    if x >= haut:
        return 1.0
    return (x - bas) / (haut - bas)


def score_humidite(swi):
    return _rampe(swi, SWI_MIN, SWI_OPTIMAL)


def score_pluie(pluie_15j):
    return _rampe(pluie_15j, PLUIE_15J_MIN, PLUIE_15J_OPTIMAL)


def score_temperature(temp):
    """Cloche : 0 hors [TEMP_MIN, TEMP_MAX], plateau à 1 sur la plage optimale."""
    if temp is None:
        return None
    if temp <= TEMP_MIN or temp >= TEMP_MAX:
        return 0.0
    if temp < TEMP_OPT_BAS:
        return (temp - TEMP_MIN) / (TEMP_OPT_BAS - TEMP_MIN)
    if temp <= TEMP_OPT_HAUT:
        return 1.0
    return (TEMP_MAX - temp) / (TEMP_MAX - TEMP_OPT_HAUT)


def niveau(indice):
    """Étiquette lisible de l'indice."""
    if indice is None:
        return "inconnu"
    if indice < 15:
        return "très faible"
    if indice < 35:
        return "faible"
    if indice < 55:
        return "moyen"
    if indice < 75:
        return "bon"
    return "excellent"


def lag_jours(altitude):
    if altitude is not None and altitude >= ALTITUDE_SEUIL:
        return LAG_JOURS_ALTITUDE
    return LAG_JOURS_PLAINE


def refroidissement(temps):
    """Refroidissement R (°C) sur les derniers jours d'une série de températures
    MOYENNES quotidiennes (ordre chronologique, le jour courant en dernier).

    R = moyenne(fenêtre de référence, jours -10..-4) − moyenne(fenêtre récente,
    jours -3..0). R > 0 = il a refroidi (favorable), R < 0 = réchauffement.
    Renvoie None si l'on n'a pas assez de jours renseignés dans chaque fenêtre.
    """
    vals = [t for t in temps if t is not None]
    if len(vals) < CHOC_FENETRE_RECENTE + 2:
        return None
    recents = vals[-CHOC_FENETRE_RECENTE:]
    ref = vals[-(CHOC_FENETRE_RECENTE + CHOC_FENETRE_REF):-CHOC_FENETRE_RECENTE]
    if len(recents) < 2 or len(ref) < 3:
        return None
    return sum(ref) / len(ref) - sum(recents) / len(recents)


def score_choc(R):
    """Score de choc thermique dans [-1, +1] à partir du refroidissement R (°C).

    Rampe sur |R| entre CHOC_MIN (zone morte) et CHOC_OPT ; signe + si
    refroidissement (favorable), − si réchauffement (défavorable)."""
    if R is None:
        return 0.0
    x = abs(R)
    if x <= CHOC_MIN:
        s = 0.0
    elif x >= CHOC_OPT:
        s = 1.0
    else:
        s = (x - CHOC_MIN) / (CHOC_OPT - CHOC_MIN)
    return s if R > 0 else -s


def modulation_choc(indice, R, temp=None):
    """Applique le choc thermique à l'indice de la maille (multiplicatif, borné).

    Gain asymétrique : CHOC_K côté refroidissement, CHOC_K_CHAUD (plus doux) côté
    réchauffement (évite de double-compter la cloche de température).

    GARDE-FOU biologique : le bonus de refroidissement n'est accordé que s'il
    ABOUTIT dans une fenêtre de température viable — un refroidissement qui plonge
    vers le gel (ou une valeur hors plage) n'est pas un déclencheur de pousse. On
    pondère donc le score POSITIF par score_temperature(temp) (0 hors plage). Le
    côté réchauffement n'est pas atténué (un coup de chaud reste défavorable)."""
    if indice is None:
        return indice
    s = score_choc(R)
    if s > 0:
        st = score_temperature(temp)
        s *= (st if st is not None else 0.0)
    k = CHOC_K if s >= 0 else CHOC_K_CHAUD
    v = indice * (1 + k * s)
    return int(round(max(0.0, min(100.0, v))))


def calcul_indice(swi, pluie_15j, temp, coef_terrain):
    """Renvoie un dict détaillant l'indice et ses composantes.

    Les sous-scores manquants (None) sont neutralisés en redistribuant les
    poids sur les composantes disponibles, pour rester robuste.
    """
    composantes = [
        (score_humidite(swi), POIDS_HUMIDITE),
        (score_pluie(pluie_15j), POIDS_PLUIE),
        (score_temperature(temp), POIDS_TEMPERATURE),
    ]
    dispo = [(s, w) for s, w in composantes if s is not None]
    if not dispo:
        return {"indice": None, "niveau": "inconnu",
                "s_humidite": None, "s_pluie": None, "s_temp": None}

    poids_total = sum(w for _, w in dispo)
    meteo = sum(s * w for s, w in dispo) / poids_total  # 0-1
    indice = round(meteo * 100 * (coef_terrain if coef_terrain is not None else 1.0))

    return {
        "indice": indice,
        "niveau": niveau(indice),
        "s_humidite": None if score_humidite(swi) is None else round(score_humidite(swi), 2),
        "s_pluie": None if score_pluie(pluie_15j) is None else round(score_pluie(pluie_15j), 2),
        "s_temp": None if score_temperature(temp) is None else round(score_temperature(temp), 2),
    }
