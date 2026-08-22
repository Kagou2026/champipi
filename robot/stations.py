"""Interpolation relief-consciente des pluviomètres vers une maille.

IDW pondéré par la distance ET par l'écart d'altitude (en montagne la pluie
dépend fortement du relief : une station 400 m plus haut n'est pas un bon proxy).
Renvoie un cumul 15 j estimé et une CONFIANCE α ∈ [0,1] qui dit à quel point on
peut se fier aux stations proches. Le robot combine ensuite :

    pluie15j_corrigée = SAFRAN + α · (estimé − SAFRAN)

soit : on garde SAFRAN quand aucune station n'est proche (α→0), on suit le
terrain quand la couverture est bonne (α→1). Cf. mémoire champipi-pluie-fiabilite.
"""
import math

from config import (STATION_IDW_PUISSANCE, STATION_ALT_ECHELLE_M,
                    STATION_RAYON_KM, STATION_DIST_REF_KM, STATION_CONF_REF,
                    SWI_NUDGE_MM_PAR_UNITE, SWI_NUDGE_MAX,
                    STATION_CHOC_JOURS, STATION_CHOC_SCORE_MIN, GEL_SEUIL_C,
                    PLUIE_15J_MIN, CHOC_FENETRE_RECENTE)
from fetch_stations import cumul_15j, serie_temp
from compute_index import refroidissement, score_choc, score_temperature


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estime_cumul15j(lat, lon, alti, stations, fin_iso):
    """Renvoie (cumul_estimé_mm, confiance_α, n_stations_utiles).

    (None, 0.0, 0) si aucune station exploitable dans le rayon.
    """
    num = den = conf = 0.0
    n = 0
    for s in stations:
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        if d > STATION_RAYON_KM:
            continue
        c = cumul_15j(s, fin_iso)
        if c is None:
            continue
        # poids = inverse-distance (IDW) × ressemblance d'altitude
        wd = 1.0 / (d ** STATION_IDW_PUISSANCE + 1e-6)
        wa = math.exp(-abs((alti or 0.0) - s["alti"]) / STATION_ALT_ECHELLE_M)
        w = wd * wa
        num += w * c
        den += w
        # confiance : somme de proximités pures (indépendante de l'altitude)
        conf += 1.0 / (1.0 + (d / STATION_DIST_REF_KM) ** 2)
        n += 1
    if den <= 0:
        return None, 0.0, 0
    estime = num / den
    alpha = max(0.0, min(1.0, conf / STATION_CONF_REF))
    return estime, alpha, n


def corrige(swi, p15, lat, lon, alti, stations, fin_iso):
    """Correction locale d'un couple (SWI, pluie 15 j) SAFRAN par les stations.

    Renvoie (swi_corr, p15_corr, alpha, estime). Sans station exploitable
    (alpha=0) ou sans pluie SAFRAN, renvoie les entrées inchangées.

      pluie15j_corr = SAFRAN + alpha·(estimé − SAFRAN)     (symétrique)
      SWI poussé UNIQUEMENT vers le haut, et seulement si les stations sont plus
      mouillées que SAFRAN (option b) : un orage local que le modèle de sol n'a
      pas ingéré doit humidifier le sol ; on ne l'assèche pas sur un simple écart
      (conservateur). Poussée bornée par SWI_NUDGE_MAX.
    """
    if not stations or p15 is None:
        return swi, p15, 0.0, None
    est, alpha, _ = estime_cumul15j(lat, lon, alti, stations, fin_iso)
    if est is None or alpha <= 0:
        return swi, p15, 0.0, est
    p_corr = max(0.0, p15 + alpha * (est - p15))
    swi_corr = swi
    if swi is not None and est > p15:
        bump = min(SWI_NUDGE_MAX, (est - p15) / SWI_NUDGE_MM_PAR_UNITE) * alpha
        swi_corr = min(1.0, swi + bump)
    return swi_corr, p_corr, alpha, est


def choc_station(station, fin_iso, cumul15=None):
    """Choc thermique OBSERVÉ à une station + verdict « appel à pousse ».

    Réplique EXACTE du choc de maille (cf. compute_index) mais sur la température
    RÉELLE de la station (TM). Renvoie None si la station n'a pas de température
    (réseau complémentaire / pluviomètre). Sinon un dict :

      R              : refroidissement °C (moyenne réf −10..−4 moins récente −3..0),
                       > 0 = il a refroidi (favorable) ;
      score          : score_choc(R) ∈ [−1, +1] (rampe signée sur |R|) ;
      tmean_recent   : T° moyenne des jours récents (fenêtre du choc) ;
      tmin_recent    : T° mini la plus basse des jours récents (garde-fou gel) ;
      viable         : le refroidissement aboutit-il dans la plage thermique ? ;
      gel            : un gel récent est-il détecté (tmin_recent <= GEL_SEUIL_C) ? ;
      humide         : sol assez humide localement (cumul15 >= PLUIE_15J_MIN) ? ;
      choc_thermique : refroidissement marqué ET viable ET sans gel (co-facteur T
                       seul, indépendamment de l'humidité) ;
      appel          : choc_thermique ET humide → les DEUX co-facteurs réunis,
                       c'est ce qui allume le badge « appel à pousse » sur la carte.

    Un coup de froid sur sol sec (humide=False) n'est PAS un appel à pousse.
    """
    serie = serie_temp(station, fin_iso, STATION_CHOC_JOURS)  # (date,tn,tx,tm)
    tmeans = [row[3] for row in serie]
    if not any(v is not None for v in tmeans):
        return None                      # station sans température
    R = refroidissement(tmeans)
    sc = score_choc(R)
    recents = serie[-CHOC_FENETRE_RECENTE:]
    tm_rec = [r[3] for r in recents if r[3] is not None]
    tn_rec = [r[1] for r in recents if r[1] is not None]
    tmean_recent = sum(tm_rec) / len(tm_rec) if tm_rec else None
    tmin_recent = min(tn_rec) if tn_rec else None
    st = score_temperature(tmean_recent)
    viable = st is not None and st > 0
    gel = tmin_recent is not None and tmin_recent <= GEL_SEUIL_C
    humide = cumul15 is not None and cumul15 >= PLUIE_15J_MIN
    choc_thermique = (sc >= STATION_CHOC_SCORE_MIN) and viable and not gel
    appel = choc_thermique and humide
    return {
        "R": None if R is None else round(R, 1),
        "score": round(sc, 2),
        "tmean_recent": None if tmean_recent is None else round(tmean_recent, 1),
        "tmin_recent": None if tmin_recent is None else round(tmin_recent, 1),
        "viable": viable, "gel": gel, "humide": humide,
        "choc_thermique": choc_thermique, "appel": appel,
    }
