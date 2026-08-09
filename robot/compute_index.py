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
