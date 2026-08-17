#!/usr/bin/env python3
"""
portfolio_split.py - regelbasierter Vorschlag, wie ein gegebenes Kapital auf
die Watchlist-Titel aufgeteilt werden koennte. KEINE Anlageberatung, sondern
eine transparente, nachvollziehbare Rechenregel - jeder Schritt ist unten
dokumentiert und kann angepasst werden.

Regeln (in dieser Reihenfolge angewendet):
1. Nur Titel mit Score >= MIN_SCORE UND bekanntem Kurs qualifizieren ueberhaupt.
2. Basis-Gewicht = max(0, score - MIN_SCORE) - nur der "Ueberschuss" ueber die
   Mindestqualitaet zaehlt, das verhindert dass ein Score-50-Titel fast so viel
   Gewicht bekommt wie ein Score-95-Titel.
3. Auf 100% normiert.
4. Cap pro Einzeltitel (MAX_POSITION_PCT) - Klumpenrisiko-Schutz.
5. Cap pro thematischem Cluster (MAX_CLUSTER_PCT) - schuetzt davor, dass die
   bewusst uebergewichteten Themen (Chip-Lieferkette, Ruestung) das ganze
   Portfolio dominieren, auch wenn sie hohe Scores haben.
6. Ueberschuss aus beiden Caps wird proportional auf die verbleibenden,
   nicht gedeckelten Positionen umverteilt (mehrere Iterationen bis stabil).
7. Kurse werden NUR FUER DIE STUECKZAHL-SCHAETZUNG grob in EUR umgerechnet
   (siehe FX_RATES) - das sind KEINE Echtzeit-Wechselkurse, nur zur groben
   Einordnung ob Bruchteilsaktien noetig sind.

Nutzung:
    python3 portfolio_split.py --in letzte_recherche.json --kapital 1300
    python3 portfolio_split.py --in letzte_recherche.json --kapital 1300 --out portfolio.json
"""

import argparse
import json
import sys

MIN_SCORE = 50.0
MAX_POSITION_PCT = 15.0
MAX_CLUSTER_PCT = 30.0

# Grobe FX-Naeherung (Stand ca. Mitte 2026) NUR fuer die Stueckzahl-Einordnung,
# kein Ersatz fuer den tatsaechlichen Wechselkurs beim Kauf.
FX_TO_EUR = {
    "EUR": 1.0,
    "USD": 0.92,
    "CHF": 1.06,
    "DKK": 0.134,
    "GBP": 1.17,
}


def apply_position_cap(weights, cap, max_iter=10):
    w = dict(weights)
    for _ in range(max_iter):
        over = {k: v for k, v in w.items() if v > cap + 1e-9}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
        under = {k: v for k, v in w.items() if v < cap - 1e-9}
        under_total = sum(under.values())
        if under_total <= 0:
            break
        for k in under:
            w[k] += excess * (w[k] / under_total)
    return w


def apply_cluster_cap(weights, cluster_map, cap, max_iter=10):
    w = dict(weights)
    for _ in range(max_iter):
        cluster_sums = {}
        for k, v in w.items():
            c = cluster_map.get(k)
            if c:
                cluster_sums[c] = cluster_sums.get(c, 0) + v
        over_clusters = {c for c, s in cluster_sums.items() if s > cap + 1e-9}
        if not over_clusters:
            break
        total_excess = 0.0
        for c in over_clusters:
            s = cluster_sums[c]
            scale = cap / s
            for k in w:
                if cluster_map.get(k) == c:
                    reduce_by = w[k] * (1 - scale)
                    w[k] -= reduce_by
                    total_excess += reduce_by
        recipients = {k: v for k, v in w.items() if cluster_map.get(k) not in over_clusters}
        recipients_total = sum(recipients.values())
        if recipients_total <= 0:
            break
        for k in recipients:
            w[k] += total_excess * (w[k] / recipients_total)
    return w


def build_allocation(data, kapital):
    kandidaten = []
    for t in data.get("titel", []):
        score = t.get("score")
        preis = t["metriken"].get("preis")
        if score is None or preis is None or score < MIN_SCORE:
            continue
        kandidaten.append(t)

    if not kandidaten:
        return {"kapital": kapital, "min_score": MIN_SCORE, "positionen": [],
                "hinweis": f"Kein Titel erreicht den Mindest-Score von {MIN_SCORE}."}

    basis_gewicht = {t["ticker"]: max(0.0, t["score"] - MIN_SCORE) for t in kandidaten}
    gesamt = sum(basis_gewicht.values())
    pct = {k: v / gesamt * 100 for k, v in basis_gewicht.items()}

    cluster_map = {t["ticker"]: t.get("cluster") for t in kandidaten}

    # Cluster- und Positions-Cap abwechselnd anwenden, bis stabil
    for _ in range(5):
        pct = apply_cluster_cap(pct, cluster_map, MAX_CLUSTER_PCT)
        pct = apply_position_cap(pct, MAX_POSITION_PCT)

    # Finale Normierung auf exakt 100% (Rundungsfehler aus den Iterationen ausgleichen)
    total_final = sum(pct.values())
    pct = {k: v / total_final * 100 for k, v in pct.items()}

    ticker_by_name = {t["ticker"]: t for t in kandidaten}
    positionen = []
    for ticker, anteil in sorted(pct.items(), key=lambda kv: -kv[1]):
        t = ticker_by_name[ticker]
        m = t["metriken"]
        preis = m["preis"]
        waehrung = m.get("waehrung", "EUR")
        fx = FX_TO_EUR.get(waehrung, 1.0)
        preis_eur = preis * fx
        betrag_eur = kapital * anteil / 100
        stueckzahl = betrag_eur / preis_eur if preis_eur else None
        positionen.append({
            "ticker": ticker,
            "name": m.get("name"),
            "cluster": t.get("cluster"),
            "score": t.get("score"),
            "anteil_pct": round(anteil, 1),
            "betrag_eur": round(betrag_eur, 2),
            "kurs": preis,
            "waehrung": waehrung,
            "kurs_ca_eur": round(preis_eur, 2),
            "stueckzahl_ca": round(stueckzahl, 3) if stueckzahl else None,
            "bruchteilsaktie_noetig": bool(preis_eur and preis_eur > betrag_eur),
        })

    return {
        "kapital": kapital,
        "min_score": MIN_SCORE,
        "max_position_pct": MAX_POSITION_PCT,
        "max_cluster_pct": MAX_CLUSTER_PCT,
        "anzahl_qualifizierter_titel": len(kandidaten),
        "anzahl_ausgeschlossen": len(data.get("titel", [])) - len(kandidaten),
        "positionen": positionen,
    }


def main():
    parser = argparse.ArgumentParser(description="Regelbasierter Kapital-Aufteilungs-Vorschlag (keine Anlageberatung)")
    parser.add_argument("--in", dest="in_path", required=True, help="Gescorte JSON-Datei")
    parser.add_argument("--kapital", type=float, default=1300.0, help="Verfuegbares Kapital in EUR")
    parser.add_argument("--out", help="Ergebnis als JSON in diese Datei schreiben statt nach stdout")
    args = parser.parse_args()

    with open(args.in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = build_allocation(data, args.kapital)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Portfolio-Vorschlag geschrieben nach {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
