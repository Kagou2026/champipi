"""Température quotidienne par maille via Open-Meteo (gratuit, sans clé).

On récupère la température moyenne des derniers jours pour chaque centroïde de
maille, puis on renvoie la moyenne récente (lissée) — représentative du régime
thermique local, déjà corrigé du relief par le modèle sous-jacent.
"""
import time
import requests

from config import OPENMETEO_URL


def temperatures_par_maille(coords, jours=3, lot=50, timeout=60):
    """coords : liste de (lat, lon). Renvoie une liste de température moyenne (°C).

    Valeur = moyenne des `jours` derniers jours de temperature_2m_mean.
    Renvoie None pour une maille en cas d'échec.
    """
    temps = []
    for i in range(0, len(coords), lot):
        chunk = coords[i:i + lot]
        params = {
            "latitude": ",".join(str(c[0]) for c in chunk),
            "longitude": ",".join(str(c[1]) for c in chunk),
            "daily": "temperature_2m_mean",
            "past_days": jours,
            "forecast_days": 1,
            "timezone": "Europe/Paris",
        }
        r = requests.get(OPENMETEO_URL, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):   # cas d'un seul point
            data = [data]
        for point in data:
            serie = (point.get("daily", {}) or {}).get("temperature_2m_mean", [])
            valeurs = [v for v in serie if v is not None]
            temps.append(round(sum(valeurs) / len(valeurs), 1) if valeurs else None)
        time.sleep(0.4)
    return temps
