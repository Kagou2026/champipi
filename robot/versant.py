"""Modulation de l'indice cèpe par le versant (ubac / adret).

Contrairement à la géologie ou à l'essence (coefficients STATIQUES), l'effet
du versant est DYNAMIQUE : son signe s'inverse selon le déficit du jour.
  - sol sec / temps chaud  -> le versant nord (frais, humide) est favorisé ;
  - temps froid            -> le versant sud (plus chaud) est favorisé.

On résume l'état de la maille en un `stress` hydro-thermique dans [-1, +1]
(+1 = sec/chaud, -1 = froid/humide) à partir du SWI et de la température, puis
on module l'indice de chaque face de forêt :

    mod = 1 + VERSANT_K * expo * stress
    indice_face = clamp( indice_maille * mod , 0, 100 )

où `expo` (statique) décrit l'exposition de la face (~[-1,+1], + = nord).

Les seuils sont volontairement explicites (config.py) pour être recalés avec
les observations de terrain : `VERSANT_K` est le paramètre principal à caler.
"""
from config import (
    SWI_MIN, SWI_OPTIMAL, VERSANT_K,
    VERSANT_TEMP_MID, VERSANT_TEMP_DEMI,
    VERSANT_POIDS_HYDRIQUE, VERSANT_POIDS_THERMIQUE,
)


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def stress_hydrothermique(swi, temp):
    """État de la maille dans [-1, +1] : +1 = sec/chaud, -1 = froid/humide.

    Renvoie 0.0 (neutre) si les deux entrées manquent : sans information, le
    versant ne module pas.
    """
    composantes = []
    if swi is not None:
        dryness = (SWI_OPTIMAL - swi) / (SWI_OPTIMAL - SWI_MIN)
        composantes.append((VERSANT_POIDS_HYDRIQUE, _clamp(dryness, -1.0, 1.0)))
    if temp is not None:
        chaleur = (temp - VERSANT_TEMP_MID) / VERSANT_TEMP_DEMI
        composantes.append((VERSANT_POIDS_THERMIQUE, _clamp(chaleur, -1.0, 1.0)))
    if not composantes:
        return 0.0
    # Renormalise les poids sur les composantes disponibles (robuste au manque).
    poids_total = sum(w for w, _ in composantes)
    s = sum(w * v for w, v in composantes) / poids_total
    return _clamp(s, -1.0, 1.0)


def modulation(expo, stress):
    """Multiplicateur appliqué à l'indice de la maille pour une face donnée."""
    if expo is None:
        return 1.0
    return 1.0 + VERSANT_K * expo * stress


def indice_module(indice_maille, expo, stress):
    """Indice de la face = indice de la maille modulé par le versant (0-100)."""
    if indice_maille is None:
        return None
    v = indice_maille * modulation(expo, stress)
    return int(round(_clamp(v, 0.0, 100.0)))
