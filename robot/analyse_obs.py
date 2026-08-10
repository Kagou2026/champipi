"""Analyse LOCALE des observations de cueillette vs l'indice Champipi.

But : confronter tes cueillettes (export privé du site) à ce que le modèle
prédisait, pour voir si les coefficients sont bons ou s'il manque un facteur.
Rien n'est publié : ce script tourne chez toi et lit ton fichier privé.

Principe clé (validé sur le terrain) : la POUSSE suit le DÉCLENCHEUR avec un
décalage (~10 j en plaine, ~15 j en altitude). On NE compare donc PAS la
cueillette à l'indice du jour, mais au MEILLEUR indice de la fenêtre de lag qui
précède (le déclencheur). C'est la bonne base de comparaison.

Usage :
    python robot/analyse_obs.py [chemin_observations.json]
Par défaut, cherche champipi_observations.json dans le dossier courant.
"""
import json
import math
import sys
from datetime import date

HIST = "data/historique.json"
TERR = "data/terrain.json"


def charger_obs(argv):
    for p in ([argv[1]] if len(argv) > 1 else
              ["champipi_observations.json", "data/observations.json"]):
        try:
            with open(p, encoding="utf-8") as fp:
                return json.load(fp), p
        except FileNotFoundError:
            continue
    sys.exit("Aucun fichier d'observations trouvé (passe le chemin en argument).")


def near_maille(terr, lat, lon):
    best, bd = None, 1e18
    for mid, c in terr.items():
        if c.get("lat") is None:
            continue
        dx = c["lat"] - lat
        dy = (c["lon"] - lon) * math.cos(lat * math.pi / 180)
        d = dx * dx + dy * dy
        if d < bd:
            bd, best = d, mid
    return best


def idx_date(dates, d):
    if d in dates:
        return dates.index(d)
    # plus proche
    td = date.fromisoformat(d)
    return min(range(len(dates)),
               key=lambda j: abs((date.fromisoformat(dates[j]) - td).days))


def lag_de(alt):
    return 15 if (alt is not None and alt >= 1000) else 10


def spearman(xs, ys):
    """Corrélation de rang (sans dépendance externe)."""
    n = len(xs)
    if n < 3:
        return None

    def rangs(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            moy = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = moy
            i = j + 1
        return r
    rx, ry = rangs(xs), rangs(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)) *
                    sum((ry[i] - my) ** 2 for i in range(n)))
    return num / den if den else None


def main():
    obs, chemin = charger_obs(sys.argv)
    H = json.load(open(HIST, encoding="utf-8"))
    terr = {c["maille_id"]: c for c in json.load(open(TERR, encoding="utf-8"))["cellules"]}
    dates = H["dates"]
    print(f"Observations : {chemin} ({len(obs)} point(s))")
    print(f"Historique   : {dates[0]} → {dates[-1]}\n")

    lignes = []
    for o in obs:
        mid = near_maille(terr, o["lat"], o["lon"])
        c = terr.get(mid, {})
        m = H["mailles"].get(mid)
        k = idx_date(dates, o["date"])
        lag = lag_de(c.get("altitude"))
        jour = m["i"][k] if m else None
        # meilleur indice sur la fenêtre de lag précédente (déclencheur)
        decl, jbest = None, k
        if m:
            for j in range(max(0, k - lag), k + 1):
                v = m["i"][j]
                if v is not None and (decl is None or v > decl):
                    decl, jbest = v, j
        lignes.append({
            "date": o["date"], "res": o.get("result"), "eff": o.get("effort"),
            "esp": o.get("espece"), "mid": mid, "alt": c.get("altitude"),
            "geo": c.get("geologie_classe"), "ess": c.get("essence_dominante"),
            "jour": jour, "decl": decl, "decl_date": dates[jbest] if m else None,
        })

    # Tableau par observation
    print(f"{'date':11} {'res':>3} {'effort':11} {'idx_jour':>8} "
          f"{'idx_décl':>8} {'décl_le':11} {'alt':>5} {'géol':8} essence")
    for l in lignes:
        print(f"{l['date']:11} {str(l['res']):>3} {str(l['eff'] or ''):11} "
              f"{str(l['jour']):>8} {str(l['decl']):>8} {str(l['decl_date'] or ''):11} "
              f"{str(round(l['alt']) if l['alt'] else ''):>5} {str(l['geo'] or ''):8} {l['ess'] or ''}")

    # Écarts (sur base fenêtre-lag) : résultat/10*100 vs indice déclencheur
    pts = [(l["res"], l["decl"]) for l in lignes if l["res"] is not None and l["decl"] is not None]
    print(f"\n{len(pts)} point(s) exploitable(s) (résultat + indice déclencheur).")
    if len(pts) >= 3:
        rho = spearman([p[0] for p in pts], [p[1] for p in pts])
        print(f"Corrélation de rang résultat ↔ indice déclencheur : ρ = {rho:+.2f}"
              if rho is not None else "Corrélation indéterminée.")
    else:
        print("Trop peu de points pour une corrélation (il en faut ≥ 3) — "
              "continue à saisir tes cueillettes, y compris les zéros après vraie recherche.")

    # Diagnostic qualitatif par point
    print("\nLecture :")
    for l in lignes:
        if l["res"] is None or l["decl"] is None:
            continue
        attendu = l["res"] * 10  # 0-10 -> 0-100 (repère grossier)
        ecart = l["decl"] - attendu
        if l["res"] >= 6 and l["decl"] < 40:
            verdict = "SOUS-ESTIMÉ : bonne cueillette mais indice déclencheur faible → coef/ facteur à revoir ici."
        elif l["res"] <= 2 and l["decl"] >= 60 and l["eff"] in ("recherche", "intensive"):
            verdict = "SUR-ESTIMÉ : indice élevé mais rien trouvé (vraie recherche) → sur-optimisme du modèle."
        elif l["res"] <= 2 and l["eff"] == "balade":
            verdict = "peu informatif (zéro en simple balade : peut-être pas assez cherché)."
        else:
            verdict = "cohérent (indice déclencheur et cueillette vont dans le même sens)."
        print(f"  {l['date']} maille {l['mid']} : cueillette {l['res']}/10, "
              f"déclencheur {l['decl']} → {verdict}")

    print("\nRappel méthodo : petit échantillon = diagnostic, pas ré-optimisation. "
          "Un zéro n'est probant qu'après vraie prospection (champ 'effort').")


if __name__ == "__main__":
    main()
