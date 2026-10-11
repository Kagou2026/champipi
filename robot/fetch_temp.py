"""Température quotidienne par maille via Open-Meteo (gratuit, sans clé).

On récupère la température moyenne de CHAQUE jour de la fenêtre (past_days) pour
chaque centroïde de maille. Deux usages :
  - `par_date` : {date -> température}, pour rejouer la série jour par jour
    (le curseur temporel : l'indice de chaque jour doit refléter la vraie temp
    de ce jour, pas une moyenne figée) ;
  - `recent`   : moyenne des derniers jours, valeur de repli quand une date SIM
    n'a pas de température correspondante.

Fenêtre par défaut : 16 jours passés + le jour courant, ce qui couvre largement
les ~15 jours d'historique fournis par le flux SIM.
"""
import time
import requests

from config import OPENMETEO_URL


def temperatures_par_maille(coords, past_days=16, recent=3, lot=50, timeout=60,
                            essais=3, backoff=5):
    """coords : liste de (lat, lon).

    Renvoie une liste (même ordre que `coords`) de dicts :
        {"par_date": {"YYYY-MM-DD": temp, ...}, "recent": float|None}
    `par_date` peut être vide et `recent` None en cas d'échec sur une maille.
    """
    out = []
    for i in range(0, len(coords), lot):
        chunk = coords[i:i + lot]
        params = {
            "latitude": ",".join(str(c[0]) for c in chunk),
            "longitude": ",".join(str(c[1]) for c in chunk),
            "daily": "temperature_2m_mean",
            "past_days": past_days,
            "forecast_days": 1,
            "timezone": "Europe/Paris",
        }
        # Open-Meteo est gratuit et parfois lent : on réessaie sur erreur réseau
        # (timeout / 5xx) avec backoff au lieu d'échouer au premier coup.
        data = None
        for essai in range(1, essais + 1):
            try:
                r = requests.get(OPENMETEO_URL, params=params, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                break
            except requests.exceptions.RequestException as e:
                if essai == essais:
                    raise
                attente = backoff * essai
                print(f"      températures : tentative {essai}/{essais} échouée "
                      f"({type(e).__name__}) — nouvel essai dans {attente:.0f}s")
                time.sleep(attente)
        if isinstance(data, dict):   # cas d'un seul point
            data = [data]
        for point in data:
            daily = point.get("daily", {}) or {}
            dates = daily.get("time", []) or []
            valeurs = daily.get("temperature_2m_mean", []) or []
            par_date = {d: round(v, 1) for d, v in zip(dates, valeurs)
                        if v is not None}
            # Moyenne des `recent` derniers jours disponibles (repli).
            derniers = [v for v in valeurs[-recent:] if v is not None]
            moy = round(sum(derniers) / len(derniers), 1) if derniers else None
            out.append({"par_date": par_date, "recent": moy})
        time.sleep(0.4)
    return out


def temperatures_depuis_historique(terrain, historique, fenetre=20, recent=3):
    """T° de maille depuis l'HISTORIQUE LONG (SAFRAN QUOT_SIM2, colonne T),
    même structure de sortie que `temperatures_par_maille` (même ordre que
    `terrain`). Renvoie [] si l'historique est absent → l'appelant retombe sur
    Open-Meteo. Une maille sans T dans l'historique donne par_date vide et
    recent None (composante température neutralisée pour elle seule)."""
    if not historique or not historique.get("dates"):
        return []
    dates = historique["dates"]
    mailles = historique.get("mailles", {})
    out = []
    for cell in terrain:
        arr = (mailles.get(cell["maille_id"]) or {}).get("t") or []
        par_date = {d: round(v, 1) for d, v in zip(dates[-fenetre:], arr[-fenetre:])
                    if v is not None}
        derniers = [v for v in arr[-recent:] if v is not None]
        moy = round(sum(derniers) / len(derniers), 1) if derniers else None
        out.append({"par_date": par_date, "recent": moy})
    return out


if __name__ == "__main__":
    res = temperatures_par_maille([(44.52, 3.55)])
    print("recent :", res[0]["recent"])
    print("jours  :", list(res[0]["par_date"].items())[-5:])
