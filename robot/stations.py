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
from compute_index import (refroidissement, score_choc, score_temperature,
                           indices_lagues)
from versant import stress_hydrothermique


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


# ---------------------------------------------------------------------------
# Correction de TOUT l'historique (et plus seulement des 15 derniers jours)
# ---------------------------------------------------------------------------
# Jusqu'ici la correction par pluviomètres ne portait que sur la queue récente :
# les stations n'étaient téléchargées que sur 30 j. Résultat, l'indice d'aujourd'hui
# (corrigé, donc sensible aux orages locaux) et celui d'il y a un an (SAFRAN pur,
# lissé à 8 km) n'étaient pas fabriqués de la même façon — incomparables, ce qui
# fausse toute calibration sur le carnet de cueillettes. Avec l'archive
# `data/stations_hist.json` (cf. backfill_stations.py) on peut appliquer EXACTEMENT
# la même correction sur toute la période.
#
# Coût : ~230 mailles × ~1700 jours × ~40 stations utiles. Deux précalculs le
# rendent tenable en pur Python (aucune dépendance en plus pour la CI) :
#   1. les cumuls 15 j de chaque station, calculés UNE fois en fenêtre glissante ;
#   2. les poids maille↔station, invariants dans le temps (les postes ne bougent pas).

def _decoder_serie(txt, n):
    """ "12,,0,3" (dixièmes) -> [1.2, None, 0.0, 0.3] complété à n valeurs."""
    out = [None] * n
    if not txt:
        return out
    for i, v in enumerate(txt.split(",")):
        if i >= n:
            break
        if v:
            out[i] = int(v) / 10.0
    return out


def _cumuls_glissants(rr, fenetre=15, min_jours=10):
    """Cumul `fenetre` jours finissant à chaque index (réplique de cumul_15j :
    tolère les trous, extrapole si >= min_jours renseignés, sinon None)."""
    n = len(rr)
    out = [None] * n
    somme = 0.0
    nb = 0
    for k in range(n):
        v = rr[k]
        if v is not None:
            somme += v; nb += 1
        sortant = k - fenetre
        if sortant >= 0 and rr[sortant] is not None:
            somme -= rr[sortant]; nb -= 1
        if nb >= min_jours:
            out[k] = somme * float(fenetre) / nb
    return out


def preparer_hist(stations_hist):
    """Prépare l'archive stations pour la correction longue.

    Renvoie {"debut", "n", "postes":[{num,lat,lon,alti,c15}]} ou None."""
    if not stations_hist or not stations_hist.get("st"):
        return None
    n = stations_hist.get("n") or 0
    postes = []
    for num, e in stations_hist["st"].items():
        rr = _decoder_serie(e.get("rr"), n)
        postes.append({"num": num, "lat": e["y"], "lon": e["x"], "alti": e["a"],
                       "c15": _cumuls_glissants(rr)})
    return {"debut": stations_hist["debut"], "n": n, "postes": postes}


def poids_maille(lat, lon, alti, postes, poids_mini=1e-4):
    """Poids (IDW × ressemblance d'altitude) et confiance de chaque station pour
    une maille — invariants dans le temps. Renvoie [(c15, w, conf), ...] trié par
    poids décroissant, les contributions négligeables étant écartées."""
    cand = []
    for p in postes:
        d = _haversine_km(lat, lon, p["lat"], p["lon"])
        if d > STATION_RAYON_KM:
            continue
        w = (1.0 / (d ** STATION_IDW_PUISSANCE + 1e-6)) \
            * math.exp(-abs((alti or 0.0) - p["alti"]) / STATION_ALT_ECHELLE_M)
        cf = 1.0 / (1.0 + (d / STATION_DIST_REF_KM) ** 2)
        cand.append((p["c15"], w, cf))
    if not cand:
        return []
    wmax = max(c[1] for c in cand)
    cand = [c for c in cand if c[1] >= wmax * poids_mini]
    cand.sort(key=lambda c: -c[1])
    return cand


def corrige_au_jour(swi, p15, poids, k):
    """Même correction que `corrige`, mais à l'index `k` de l'archive et avec des
    poids précalculés. Renvoie (swi_corr, p15_corr, alpha, estime)."""
    if p15 is None or not poids:
        return swi, p15, 0.0, None
    num = den = conf = 0.0
    for c15, w, cf in poids:
        c = c15[k] if 0 <= k < len(c15) else None
        if c is None:
            continue
        num += w * c; den += w; conf += cf
    if den <= 0:
        return swi, p15, 0.0, None
    est = num / den
    alpha = max(0.0, min(1.0, conf / STATION_CONF_REF))
    if alpha <= 0:
        return swi, p15, 0.0, est
    p_corr = max(0.0, p15 + alpha * (est - p15))
    swi_corr = swi
    if swi is not None and est > p15:
        bump = min(SWI_NUDGE_MAX, (est - p15) / SWI_NUDGE_MM_PAR_UNITE) * alpha
        swi_corr = min(1.0, swi + bump)
    return swi_corr, p_corr, alpha, est


def corrige_historique_complet(historique, terrain, coef_par_maille, prep):
    """Corrige TOUT l'historique long (w, p, s, i) par les stations.

    Pourquoi : jusqu'ici seule la queue récente était corrigée (les stations
    n'étaient téléchargées que sur 30 j). L'indice d'aujourd'hui était donc
    sensible aux orages locaux quand celui d'il y a un an, purement SAFRAN,
    restait lissé à 8 km — deux recettes pour un même chiffre, ce qui interdisait
    de caler les seuils sur des cueillettes passées. Avec l'archive stations
    (cf. backfill_stations.py) on applique EXACTEMENT la même correction partout.

    Renvoie (nb_points_corriges, dernier_index_couvert) ; l'index sert à ne pas
    re-corriger ces jours-là avec la passe « queue récente » (la correction n'est
    pas idempotente quand alpha < 1)."""
    if not historique or not prep:
        return 0, -1
    from datetime import date as _d
    dates = historique.get("dates") or []
    if not dates:
        return 0, -1
    debut = _d.fromisoformat(prep["debut"])
    # index de chaque date de l'historique dans les séries de l'archive stations
    idx_arch = [(_d.fromisoformat(dt) - debut).days for dt in dates]
    dernier = max([k for k, ks in enumerate(idx_arch) if 0 <= ks < prep["n"]],
                  default=-1)
    cellinfo = {c["maille_id"]: c for c in terrain}
    n_pts = 0
    for mid, arr in historique["mailles"].items():
        c = cellinfo.get(mid)
        if not c:
            continue
        # poids station->maille : invariants dans le temps, calculés une fois
        poids = poids_maille(c.get("lat"), c.get("lon"), c["altitude"],
                             prep["postes"])
        if not poids:
            continue
        tser = arr.get("t", [])
        touche = False
        for k, ks in enumerate(idx_arch):
            if ks < 0 or ks >= prep["n"]:
                continue
            p = arr["p"][k] if k < len(arr["p"]) else None
            if p is None:
                continue
            w = arr["w"][k] if k < len(arr["w"]) else None
            w_c, p_c, _, _ = corrige_au_jour(w, p, poids, ks)
            if p_c == p and w_c == w:
                continue          # aucune station utile ce jour-là -> SAFRAN
            t = tser[k] if k < len(tser) else None
            arr["w"][k] = None if w_c is None else round(w_c, 3)
            arr["p"][k] = round(p_c, 1)
            arr["s"][k] = (round(stress_hydrothermique(w_c, t), 3)
                           if w_c is not None else None)
            touche = True; n_pts += 1
        if touche:
            idx = indices_lagues(arr.get("w", []), arr.get("p", []), tser,
                                 coef_par_maille.get(mid, 1.0), c["altitude"])
            for k in range(len(dates)):
                if k < len(idx) and arr["i"][k] is not None:
                    arr["i"][k] = idx[k]
    return n_pts, dernier
