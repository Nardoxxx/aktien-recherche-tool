#!/usr/bin/env python3
"""
report_template.py - baut aus dem JSON-Output von analyse.py (und optional
screener.py) einen lesbaren Markdown-Report fuer den Vault.

WICHTIG zum Arbeitsteilung-Prinzip dieses Tools:
Alle ZAHLEN in diesem Report sind deterministisch aus der EODHD-API berechnet
(siehe analyse.py) - nicht vom Sprachmodell geschaetzt. Was dieses Skript NICHT
liefert, weil es dafuer echtes Web-Wissen/Suche braucht: die qualitative
Einordnung (Geschaeftsmodell in eigenen Worten, "Warum Potenzial"-Fliesstext,
aktuelle News/Makro-Einordnung, Ketteneffekte). Diese Stellen sind im
erzeugten Markdown als HTML-Kommentare "<!-- AGENT: ... -->" markiert.

Ablauf im echten Betrieb (Scheduled Task):
  1. analyse.py laeuft, erzeugt JSON mit den Kennzahlen
  2. report_template.py laeuft, erzeugt Markdown-Skelett mit allen Zahlen +
     AGENT-Platzhaltern
  3. Der Claude-Agent im Scheduled Task recherchiert (WebSearch) und ersetzt
     jeden AGENT-Platzhalter durch echten Text, dann speichert die finale
     Datei im Vault ab.

Nutzung:
    python3 report_template.py --in report_data.json --type daily --out out.md
    python3 report_template.py --in report_data.json --type weekly --screener screener_data.json --out out.md
"""

import argparse
import json
from datetime import datetime


def fmt_num(value, digits=1, suffix=""):
    if value is None:
        return "k.A."
    return f"{value:.{digits}f}{suffix}"


def fmt_pct(value, digits=1):
    return fmt_num(value, digits, "%")


def score_badge(score):
    if score is None:
        return "k.A."
    if score >= 70:
        return f"**{score} / 100** (stark)"
    if score >= 50:
        return f"**{score} / 100** (solide)"
    if score >= 30:
        return f"**{score} / 100** (durchwachsen)"
    return f"**{score} / 100** (schwach)"


def days_until(date_str):
    if not date_str:
        return None
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    delta = (target - datetime.now().date()).days
    return delta


def render_ticker_section(titel):
    m = titel["metriken"]
    sub = titel["subscores"]
    name = m.get("name") or titel["ticker"]
    lines = []
    lines.append(f"### {name} ({titel['ticker']}) — {titel.get('region', '')}")
    lines.append("")
    lines.append(f"**Score:** {score_badge(titel.get('score'))}")
    lines.append("")
    if titel.get("notiz_watchlist"):
        lines.append(f"> {titel['notiz_watchlist']}")
        lines.append("")
    if m.get("notiz_recherche"):
        lines.append(f"> [!info] Recherche-Hinweis\n> {m['notiz_recherche']}")
        lines.append("")
    lines.append("<!-- AGENT: 2-3 Saetze, was das Unternehmen konkret macht (Geschaeftsmodell, "
                  "Hauptprodukte/Umsatzquellen), in eigenen, verstaendlichen Worten. -->")
    lines.append("")
    lines.append("**Kennzahlen**")
    lines.append("")
    lines.append("| Kennzahl | Wert |")
    lines.append("|---|---|")
    lines.append(f"| Kurs | {fmt_num(m.get('preis'), 2)} {m.get('waehrung') or ''} |")
    lines.append(f"| KGV (P/E) | {fmt_num(m.get('kgv'), 1)} |")
    lines.append(f"| PEG-Ratio | {fmt_num(m.get('peg'), 2)} |")
    lines.append(f"| EV/EBITDA | {fmt_num(m.get('ev_ebitda'), 1)} |")
    lines.append(f"| Umsatzwachstum YoY | {fmt_pct(m.get('umsatzwachstum_yoy'))} |")
    lines.append(f"| Gewinnwachstum YoY | {fmt_pct(m.get('gewinnwachstum_yoy'))} |")
    lines.append(f"| Gewinnmarge | {fmt_pct(m.get('gewinnmarge'))} |")
    lines.append(f"| Operative Marge | {fmt_pct(m.get('operative_marge'))} |")
    lines.append(f"| ROE | {fmt_pct(m.get('roe'))} |")
    lines.append(f"| Beta | {fmt_num(m.get('beta'), 2)} |")
    lines.append(f"| 52-Wochen-Range | {fmt_num(m.get('52w_tief'), 2)} – {fmt_num(m.get('52w_hoch'), 2)} |")
    lines.append(f"| Analysten-Kursziel | {fmt_num(m.get('analysten_kursziel'), 2)} |")
    lines.append("")
    lines.append("**Score-Herleitung** (0-100 Perzentil innerhalb der Watchlist, dann gewichtet)")
    lines.append("")
    lines.append("| Baustein | Gewicht | Perzentil |")
    lines.append("|---|---|---|")
    lines.append(f"| Wachstum | 30% | {fmt_num(sub.get('wachstum'), 1)} |")
    lines.append(f"| Bewertung | 25% | {fmt_num(sub.get('bewertung'), 1)} |")
    lines.append(f"| Qualität/Profitabilität | 20% | {fmt_num(sub.get('qualitaet'), 1)} |")
    lines.append(f"| Momentum | 15% | {fmt_num(sub.get('momentum'), 1)} |")
    lines.append(f"| Analysten-Konsens | 10% | {fmt_num(sub.get('analysten'), 1)} |")
    lines.append("")
    lines.append("<!-- AGENT: 3-5 Saetze 'Warum Potenzial' - fasse die obigen Zahlen verstaendlich "
                  "zusammen (nicht nur wiederholen, sondern einordnen: was faellt auf, was ist die "
                  "Kernthese?). -->")
    lines.append("")
    earnings_date = m.get("naechste_earnings_datum")
    delta = days_until(earnings_date)
    if earnings_date:
        countdown = f" (in {delta} Tagen)" if delta is not None else ""
        lines.append(f"**Nächster wichtiger Termin:** Quartalszahlen am {earnings_date}{countdown}"
                      f", EPS-Schätzung: {fmt_num(m.get('naechste_earnings_eps_schaetzung'), 2)}")
    else:
        lines.append("**Nächster wichtiger Termin:** kein Termin in den nächsten 12 Monaten in der API gefunden.")
    lines.append("")
    lines.append("<!-- AGENT: Markt-/News-Einordnung der letzten 24-48h zu diesem Titel "
                  "(per WebSearch recherchieren, 2-4 Saetze, nur wenn es echte relevante News gibt). -->")
    lines.append("")
    lines.append("<!-- AGENT: 1-2 Saetze kurze Risiken (Bewertung zu hoch? Regulatorik? "
                  "Konkurrenzdruck? Zyklik?). -->")
    lines.append("")
    return "\n".join(lines)


def group_by_cluster(titel_list):
    clusters = {}
    for t in titel_list:
        cluster = t.get("cluster")
        if cluster:
            clusters.setdefault(cluster, []).append(t)
    return clusters


def render_fokus_section(titel_list):
    lines = ["## Diese Woche im Fokus", ""]
    lines.append("<!-- AGENT: Marktüberblick ergänzen - Stände S&P 500, Nasdaq, DAX, Euro Stoxx 50, "
                  "VIX, und die wichtigsten Makro-Termine der Woche (Fed/EZB, Inflationsdaten). -->")
    lines.append("")
    upcoming = [t for t in titel_list if t["metriken"].get("naechste_earnings_datum")]
    upcoming.sort(key=lambda t: t["metriken"]["naechste_earnings_datum"])
    if upcoming:
        lines.append("**Anstehende Quartalszahlen in der Watchlist:**")
        lines.append("")
        for t in upcoming[:8]:
            d = t["metriken"]["naechste_earnings_datum"]
            delta = days_until(d)
            countdown = f" (in {delta} Tagen)" if delta is not None else ""
            lines.append(f"- {t['metriken'].get('name') or t['ticker']} ({t['ticker']}): {d}{countdown}")
        lines.append("")
    clusters = group_by_cluster(titel_list)
    if clusters:
        lines.append("**Beobachtete Cluster / Ketteneffekte:**")
        lines.append("")
        for cluster_name, members in clusters.items():
            tickers = ", ".join(t["ticker"] for t in members)
            lines.append(f"- `{cluster_name}`: {tickers}")
        lines.append("")
        lines.append("<!-- AGENT: Falls ein Titel aus einem Cluster diese Woche Zahlen bringt (siehe "
                      "oben), hier konkret erklaeren, welche anderen Cluster-Mitglieder davon "
                      "wahrscheinlich mitbewegt werden und warum (Lieferketten-/Sektor-Logik). -->")
        lines.append("")
    return "\n".join(lines)


def render_screener_section(screener_data):
    if not screener_data or not screener_data.get("kandidaten"):
        return ""
    lines = ["## Screener-Kandidaten dieser Woche", ""]
    lines.append("Automatisch aus S&P 500 / STOXX Europe 600 gefiltert, noch NICHT in der festen "
                  "Watchlist - manuelle Pruefung/Aufnahme durch Leonardo noetig.")
    lines.append("")
    lines.append("| Ticker | Name | Umsatzwachstum YoY | KGV | Kommentar |")
    lines.append("|---|---|---|---|---|")
    for k in screener_data["kandidaten"]:
        lines.append(
            f"| {k.get('ticker')} | {k.get('name')} | {fmt_pct(k.get('umsatzwachstum_yoy'))} | "
            f"{fmt_num(k.get('kgv'), 1)} | <!-- AGENT: 1 Satz warum interessant --> |"
        )
    lines.append("")
    return "\n".join(lines)


def build_report(data, report_type="daily", screener_data=None):
    today = datetime.now().strftime("%Y-%m-%d")
    titel_list = data.get("titel", [])
    titel_sorted = sorted(titel_list, key=lambda t: (t.get("score") is None, -(t.get("score") or 0)))

    tag_type = "daily" if report_type == "daily" else "weekly"
    lines = []
    lines.append("---")
    lines.append("tags: [aktien-report, " + tag_type + "]")
    lines.append(f"date: {today}")
    lines.append("---")
    lines.append("")
    titel_ueberschrift = "Aktien-Tagesreport" if report_type == "daily" else "Aktien-Wochenreport"
    lines.append(f"# {titel_ueberschrift} {today}")
    lines.append("")
    lines.append("> [!warning] Kein Finanzrat\n"
                  "> Dieser Report ist eine rein mathematische Kennzahlen-Einordnung auf Basis "
                  "der EODHD-Daten, keine Anlageberatung. Alle Scores sind relative Einordnung "
                  "innerhalb der eigenen Watchlist, keine absolute Kaufempfehlung.")
    lines.append("")
    lines.append(render_fokus_section(titel_list))
    lines.append("")
    if report_type == "weekly" and screener_data:
        lines.append(render_screener_section(screener_data))
        lines.append("")
    lines.append("## Watchlist im Detail (sortiert nach Score)")
    lines.append("")
    for t in titel_sorted:
        lines.append(render_ticker_section(t))
    lines.append("---")
    lines.append(f"*Rohdaten erzeugt: {data.get('erzeugt_am', 'k.A.')} · Modus: {data.get('modus', 'k.A.')}*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="JSON-Kennzahlen zu Markdown-Report zusammenbauen")
    parser.add_argument("--in", dest="in_path", required=True, help="JSON-Datei von analyse.py")
    parser.add_argument("--screener", dest="screener_path", help="JSON-Datei von screener.py (nur weekly)")
    parser.add_argument("--type", dest="report_type", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--out", dest="out_path", help="Markdown-Ausgabedatei (sonst stdout)")
    args = parser.parse_args()

    with open(args.in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    screener_data = None
    if args.screener_path:
        with open(args.screener_path, "r", encoding="utf-8") as f:
            screener_data = json.load(f)

    report = build_report(data, report_type=args.report_type, screener_data=screener_data)

    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report geschrieben nach {args.out_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
