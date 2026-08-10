"""Prévision météo par maille via Open-Meteo (gratuit, sans clé).

Pour la PRÉVISION de sortie, on récupère par centroïde de maille les séries
quotidiennes : pluie (precipitation_sum), température moyenne (temperature_2m_mean)
et évapotranspiration de référence (et0_fao_evapotranspiration), sur une fenêtre
qui couvre le passé récent (pour amorcer la pluie 15 j et le refroidissement) et
la prévision à ~16 jours.

SAFRAN ne fournit pas de prévision : c'est donc la seule source pour le futur.
Le SWI futur est ensuite projeté par bilan hydrique dans run.py (pluie − ET0).
"""
import time
import requests

from config import OPENMETEO_URL


def prevision_par_maille(coords, past_days=20, forecast_days=16, lot=50,
                         timeout=60, essais=3, backoff=5):
    """coords : liste de (lat, lon). Renvoie une liste (même ordre) de dicts :
        {"YYYY-MM-DD": {"precip": mm, "temp": °C, "et0": mm}, ...}
    Jour(s) manquant(s) simplement absents du dict.
    """
    out = []
    for i in range(0, len(coords), lot):
        chunk = coords[i:i + lot]
        params = {
            "latitude": ",".join(str(c[0]) for c in chunk),
            "longitude": ",".join(str(c[1]) for c in chunk),
            "daily": "precipitation_sum,temperature_2m_mean,et0_fao_evapotranspiration",
            "past_days": past_days,
            "forecast_days": forecast_days,
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
                print(f"      prévision : tentative {essai}/{essais} échouée "
                      f"({type(e).__name__}) — nouvel essai dans {attente:.0f}s")
                time.sleep(attente)
        if isinstance(data, dict):
            data = [data]
        for point in data:
            d = point.get("daily", {}) or {}
            dates = d.get("time", []) or []
            pr = d.get("precipitation_sum", []) or []
            tp = d.get("temperature_2m_mean", []) or []
            et = d.get("et0_fao_evapotranspiration", []) or []
            serie = {}
            for j, dt in enumerate(dates):
                serie[dt] = {
                    "precip": pr[j] if j < len(pr) else None,
                    "temp": tp[j] if j < len(tp) else None,
                    "et0": et[j] if j < len(et) else None,
                }
            out.append(serie)
        time.sleep(0.4)
    return out


if __name__ == "__main__":
    res = prevision_par_maille([(44.52, 3.55)])
    items = list(res[0].items())
    print("jours:", len(items))
    print("passé  :", items[0])
    print("futur  :", items[-1])
