#!/usr/bin/env python3
"""
dashboard_notes.py - erzeugt aus dem gescorten JSON (Output von analyse.py /
analyse.py --score-only) pro Watchlist-Titel eine eigene Notiz mit
Frontmatter-Properties. Das ist die Datengrundlage fuer die Obsidian Base
"Aktien Dashboard.base" - Bases lesen Frontmatter-Properties aus Notizen in
einem Ordner, nicht direkt aus JSON.

Jede Notiz wird bei jedem Lauf KOMPLETT UEBERSCHRIEBEN (Dashboard zeigt immer
den aktuellsten Stand). Die Historie/Lernschleife liegt separat in
Performance-Tracking.md und Krisen-Log.md - das hier ist bewusst nur der
"Jetzt-Zustand" fuers Dashboard.

Swing-/Dip-Signal (auf Wunsch ergaenzt, 17.08.2026):
Ein Titel gilt als "swing_kandidat", wenn er spuerbar unter seinem
52-Wochen-Hoch notiert (moeglicher guenstigerer Einstieg) UND fundamental
weiterhin solide ist (Score >= 50) - die Kombination "eigentlich gut, aber
gerade im Kurs zurueckgekommen" ist das Muster, das fuer einen mehrwoechigen
Swing-Einstieg interessant sein kann. Reine Kursschwaeche OHNE soliden Score
wird bewusst NICHT als Kandidat markiert (das waere ein "fallendes Messer",
kein Dip-Kauf).

Nutzung:
    python3 dashboard_notes.py --in letzte_recherche.json --out-dir "../../../03 Bereiche/Finanzen und Vermögensaufbau/Aktien Reports/Titel"
"""

import argparse
import json
import os
import re
from datetime import datetime

# Schwellenwerte fuer das Swing-/Dip-Signal - bewusst als Konstanten oben, leicht
# anpassbar, keine versteckte Magie.
SWING_MIN_ABSTAND_VOM_HOCH_PCT = -10.0  # mind. 10% unter 52-Wochen-Hoch
SWING_MIN_SCORE = 50.0  # fundamental mindestens "solide"


def slugify(ticker):
    return re.sub(r"[^A-Za-z0-9_-]", "_", ticker)


def compute_abstand_52w_hoch(m):
    preis = m.get("preis")
    hoch = m.get("52w_hoch")
    if not preis or not hoch:
        return None
    return round((preis - hoch) / hoch * 100, 1)


def yaml_escape(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def build_note(titel):
    m = titel["metriken"]
    sub = titel.get("subscores", {})
    ticker = titel["ticker"]
    name = m.get("name") or ticker
    abstand = compute_abstand_52w_hoch(m)
    swing = bool(
        abstand is not None
        and abstand <= SWING_MIN_ABSTAND_VOM_HOCH_PCT
        and titel.get("score") is not None
        and titel["score"] >= SWING_MIN_SCORE
    )

    fm_fields = {
        "tags": "[aktien-titel]",
        "ticker": ticker,
        "name": name,
        "region": titel.get("region"),
        "cluster": titel.get("cluster"),
        "score": titel.get("score"),
        "kurs": m.get("preis"),
        "waehrung": m.get("waehrung"),
        "kgv": m.get("kgv"),
        "umsatzwachstum_yoy": m.get("umsatzwachstum_yoy"),
        "gewinnwachstum_yoy": m.get("gewinnwachstum_yoy"),
        "score_wachstum": sub.get("wachstum"),
        "score_bewertung": sub.get("bewertung"),
        "score_qualitaet": sub.get("qualitaet"),
        "score_momentum": sub.get("momentum"),
        "score_analysten": sub.get("analysten"),
        "abstand_52w_hoch_pct": abstand,
        "swing_kandidat": swing,
        "naechste_earnings_datum": m.get("naechste_earnings_datum"),
        "analysten_kursziel": m.get("analysten_kursziel"),
        "letzte_aktualisierung": datetime.now().strftime("%Y-%m-%d"),
        "quelle": m.get("quelle"),
    }

    lines = ["---"]
    lines.append(f'tags: {fm_fields["tags"]}')
    for key, value in fm_fields.items():
        if key == "tags":
            continue
        lines.append(f"{key}: {yaml_escape(value)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {name} ({ticker})")
    lines.append("")
    lines.append(f"Automatisch generierte Dashboard-Notiz, wird bei jedem Lauf ueberschrieben. "
                 f"Vollstaendiger Report mit Begruendung: siehe jeweils aktuellster Wochen-/Tagesreport "
                 f"in [[03 Bereiche/Finanzen und Vermögensaufbau/Aktien Reports/]].")
    lines.append("")
    if swing:
        lines.append(f"> [!tip] Swing-Kandidat\n"
                     f"> {abstand}% unter 52-Wochen-Hoch bei weiterhin solidem Score ({titel['score']}) - "
                     f"moeglicher Dip-Einstieg, kein Freibrief. Immer erst den aktuellen Report pruefen.")
        lines.append("")

    return "\n".join(lines)


def run(in_path, out_dir):
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(out_dir, exist_ok=True)

    written = []
    for titel in data.get("titel", []):
        content = build_note(titel)
        filename = f"{slugify(titel['ticker'])}.md"
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(filename)

    print(f"{len(written)} Dashboard-Notizen geschrieben nach {out_dir}")
    return written


def main():
    parser = argparse.ArgumentParser(description="Pro-Ticker-Dashboard-Notizen aus gescorter JSON erzeugen")
    parser.add_argument("--in", dest="in_path", required=True, help="Gescorte JSON-Datei (von analyse.py)")
    parser.add_argument("--out-dir", dest="out_dir", required=True, help="Zielordner fuer die Notizen")
    args = parser.parse_args()
    run(args.in_path, args.out_dir)


if __name__ == "__main__":
    main()
