#!/usr/bin/env python3
"""
html_dashboard.py - baut eine einzelne, selbst-enthaltene HTML-Dashboard-Seite
(kein CDN, kein Build-Schritt, kein Internet noetig) aus den JSON-Outputs von
analyse.py, market_overview.py und portfolio_split.py. Zum Oeffnen im Browser
gedacht - Obsidian kann eingebettetes JS nicht ausfuehren.

Design folgt dem dataviz-Skill-Playbook: Score-Baender nutzen die feste
Status-Palette (gut/warnung/ernst/kritisch), Cluster-Verteilung nutzt die
ersten 3 kategorialen Slots (validiert all-pairs), Portfolio-Gewichtung ist
eine Balken- statt Kreisgrafik (zu viele Positionen fuer sichere Kreis-Farben),
Light+Dark Mode ueber CSS Custom Properties, sortierbare Tabelle als
Accessibility-Fallback.

Nutzung:
    python3 html_dashboard.py --in letzte_recherche.json --market market.json \
        --portfolio portfolio.json --out dashboard.html
"""

import argparse
import json
import os
from datetime import datetime

STATUS = {
    "stark": "#0ca30c",
    "solide": "#fab219",
    "durchwachsen": "#ec835a",
    "kritisch": "#d03b3b",
}

# Gleiche Schwellenwerte wie in dashboard_notes.py - ein Titel ist Swing-Kandidat,
# wenn er spuerbar unter seinem 52-Wochen-Hoch notiert UND fundamental noch solide ist.
SWING_MIN_ABSTAND_VOM_HOCH_PCT = -10.0
SWING_MIN_SCORE = 50.0

CAT_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a"]
CAT_DARK = ["#3987e5", "#d95926", "#199e70"]


def score_band(score):
    if score is None:
        return "durchwachsen"
    if score >= 70:
        return "stark"
    if score >= 50:
        return "solide"
    if score >= 30:
        return "durchwachsen"
    return "kritisch"


def compute_abstand_52w_hoch(m):
    """Nicht in der letzte_recherche.json persistiert (nur eine abgeleitete
    Report-Kennzahl), deshalb hier lokal aus preis/52w_hoch nachgerechnet -
    identische Formel wie in dashboard_notes.py."""
    preis = m.get("preis")
    hoch = m.get("52w_hoch")
    if not preis or not hoch:
        return None
    return round((preis - hoch) / hoch * 100, 1)


def esc(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_score_bars(titel_list):
    ranked = sorted(titel_list, key=lambda t: (t.get("score") is None, -(t.get("score") or 0)))
    max_score = 100
    rows = []
    for t in ranked:
        score = t.get("score")
        if score is None:
            continue
        band = score_band(score)
        color = STATUS[band]
        pct = score / max_score * 100
        name = esc(t["metriken"].get("name") or t["ticker"])
        rows.append(f'''
      <div class="bar-row">
        <div class="bar-label">{esc(t["ticker"])}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:{pct:.1f}%; background:{color};" title="{name}: {score:.1f} / 100"></div>
        </div>
        <div class="bar-value">{score:.1f}</div>
      </div>''')
    return "\n".join(rows)


def build_cluster_donut(titel_list):
    counts = {}
    for t in titel_list:
        c = t.get("cluster") or "sonstige"
        counts[c] = counts.get(c, 0) + 1
    labels = list(counts.keys())
    # Fixe Reihenfolge: bekannte Cluster zuerst, "sonstige" zuletzt
    order = ["chip-lieferkette-nvidia", "ruestung", "sonstige"]
    labels = [l for l in order if l in counts] + [l for l in labels if l not in order]
    total = sum(counts.values())

    circumference = 2 * 3.14159265 * 15.9155  # r fuer 100-Einheiten-Umfang-Trick
    segments = []
    legend = []
    offset = 0
    for i, label in enumerate(labels):
        count = counts[label]
        pct = count / total * 100
        color_light = CAT_LIGHT[i % 3]
        color_dark = CAT_DARK[i % 3]
        segments.append(
            f'<circle class="donut-seg" cx="21" cy="21" r="15.9155" fill="transparent" '
            f'stroke="var(--cat-{i % 3 + 1})" stroke-width="4" '
            f'stroke-dasharray="{pct:.2f} {100 - pct:.2f}" stroke-dashoffset="{100 - offset:.2f}">'
            f'<title>{esc(label)}: {count} Titel ({pct:.0f}%)</title></circle>'
        )
        offset += pct
        legend.append(f'<div class="legend-item"><span class="legend-dot" style="background:var(--cat-{i % 3 + 1})"></span>{esc(label)} ({count})</div>')
    return "\n".join(segments), "\n".join(legend)


def build_portfolio_bars(portfolio):
    """Vertraegt beide portfolio_split.py-Ausgabeformate: den Einmalbetrag-Modus
    (Feld 'betrag_eur' pro Position) und den --sparplan-Modus (Feld
    'betrag_pro_rate_eur' + 'betraege_eur'-Dict pro Position, keine 'betrag_eur')."""
    if not portfolio or not portfolio.get("positionen"):
        return None
    is_sparplan = "betrag_pro_rate_eur" in portfolio["positionen"][0]
    rows = []
    max_pct = max(p["anteil_pct"] for p in portfolio["positionen"])
    for p in portfolio["positionen"]:
        pct = p["anteil_pct"]
        width = pct / max_pct * 100
        if is_sparplan:
            betrag_label = f"{p['betrag_pro_rate_eur']:.0f} EUR/Monat"
        else:
            betrag_label = f"{p['betrag_eur']:.0f} EUR"
        rows.append(f'''
      <div class="bar-row">
        <div class="bar-label">{esc(p["ticker"])}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:{width:.1f}%; background:var(--series-1);" title="{esc(p['name'])}: {pct:.1f}% ({betrag_label})"></div>
        </div>
        <div class="bar-value">{pct:.1f}%</div>
      </div>''')
    return "\n".join(rows)


def build_stat_tiles(market):
    if not market or not market.get("indizes"):
        return ""
    tiles = []
    for name, idx in market["indizes"].items():
        if idx.get("fehler"):
            continue
        aktuell = idx["aktuell"]
        abstand = idx["abstand_vom_hoch_pct"]
        krise = idx.get("in_korrektur")
        delta_class = "delta-bad" if krise else "delta-ok"
        if name == "VIX":
            sub = f"Krisen-Schwelle: 30 (aktuell {aktuell:.1f})"
        else:
            sub = f"{abstand:+.1f}% vom 6M-Hoch"
        badge = ' <span class="status-badge critical">Korrektur</span>' if krise else ""
        tiles.append(f'''
      <div class="stat-tile">
        <div class="stat-label">{esc(name)}{badge}</div>
        <div class="stat-value">{aktuell:,.1f}</div>
        <div class="stat-delta {delta_class}">{esc(sub)}</div>
      </div>''')
    return "\n".join(tiles)


def check_ipo_alerts(ipo_radar, history_path):
    """Vergleicht den aktuellen IPO-Radar-Stand mit dem zuletzt gespeicherten
    (history_path, ein einfaches JSON: {name: {termin, status}}). Aendert sich
    Termin oder Status eines bereits bekannten Kandidaten, wird ein Alert
    erzeugt. Neue Kandidaten (erstmals im Radar) loesen KEINEN Alert aus -
    sonst wuerde der allererste Lauf faelschlich "Alarm" schlagen, obwohl es
    nur die Erstbefuellung ist. Schreibt danach den neuen Stand zurueck."""
    if not ipo_radar:
        return []

    history = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}

    alerts = []
    new_history = {}
    for ipo in ipo_radar:
        name = ipo.get("name")
        if not name:
            continue
        neu = {"termin": ipo.get("termin"), "status": ipo.get("status")}
        alt = history.get(name)
        if alt and (alt.get("termin") != neu["termin"] or alt.get("status") != neu["status"]):
            alerts.append({
                "name": name,
                "alt_termin": alt.get("termin"),
                "neu_termin": neu["termin"],
                "alt_status": alt.get("status"),
                "neu_status": neu["status"],
            })
        new_history[name] = neu

    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(new_history, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    return alerts


def build_ipo_alert_banner(alerts):
    if not alerts:
        return ""
    items = []
    for a in alerts:
        teile = []
        if a["alt_termin"] != a["neu_termin"]:
            teile.append(f'Termin: "{esc(a["alt_termin"])}" → <strong>"{esc(a["neu_termin"])}"</strong>')
        if a["alt_status"] != a["neu_status"]:
            teile.append(f'Status: "{esc(a["alt_status"])}" → <strong>"{esc(a["neu_status"])}"</strong>')
        items.append(f'<li><strong>{esc(a["name"])}</strong>: {" · ".join(teile)}</li>')
    return f'''
  <div class="card alert-card">
    <h2>🔔 IPO-Update seit letztem Lauf</h2>
    <ul class="alert-list">{"".join(items)}</ul>
  </div>'''


def build_ipo_radar(ipo_radar):
    """Karte fuer bevorstehende/kuerzliche Boersengaenge (IPOs), die (noch) nicht
    in der regulaeren Watchlist sind - meist weil zu wenig Handelshistorie fuer
    den regulaeren Score existiert. Rein qualitativ recherchiert (WebSearch),
    kein Zahlen-Score. Feld 'ipo_radar' ist eine Liste von Objekten mit name,
    ticker, status, termin, bewertung, cluster, beschreibung, einschaetzung."""
    if not ipo_radar:
        return None
    blocks = []
    for ipo in ipo_radar:
        status_class = "ipo-status-live" if "gelistet" in (ipo.get("status") or "") else "ipo-status-pending"
        blocks.append(f'''
      <div class="ipo-item">
        <div class="ipo-item-head">
          <strong>{esc(ipo.get("name"))}</strong>
          {f'({esc(ipo.get("ticker"))})' if ipo.get("ticker") and "offen" not in ipo.get("ticker", "") and "steht" not in ipo.get("ticker", "") else ''}
          <span class="ipo-status {status_class}">{esc(ipo.get("status"))}</span>
          · {esc(ipo.get("termin"))} · {esc(ipo.get("bewertung"))}
        </div>
        <p class="ipo-beschreibung">{esc(ipo.get("beschreibung"))}</p>
        <p class="ipo-einschaetzung">{esc(ipo.get("einschaetzung"))}</p>
      </div>''')
    return "\n".join(blocks)


def build_swing_table(titel_list):
    """Eigene, prominente Sektion nur der Swing-Kandidaten (spuerbar unter
    52-Wochen-Hoch UND fundamental weiter solide) - sortiert nach Score. Jeder
    Kandidat bekommt eine Kopfzeile mit Kennzahlen PLUS (falls recherchiert,
    Feld 'swing_ausblick' in metriken) einen echten Wochen-Ausblick-Absatz -
    nicht nur die nackte Zahl, sondern warum der Titel in den naechsten Wochen
    interessant sein koennte."""
    kandidaten = []
    for t in titel_list:
        m = t["metriken"]
        score = t.get("score")
        abstand = compute_abstand_52w_hoch(m)
        if (abstand is not None and score is not None
                and abstand <= SWING_MIN_ABSTAND_VOM_HOCH_PCT and score >= SWING_MIN_SCORE):
            kandidaten.append((t, abstand))

    if not kandidaten:
        return None

    kandidaten.sort(key=lambda pair: -pair[0]["score"])
    blocks = []
    for t, abstand in kandidaten:
        m = t["metriken"]
        band = score_band(t["score"])
        color = STATUS[band]
        preis_str = f"{m['preis']:.2f} {esc(m.get('waehrung') or '')}" if m.get("preis") is not None else "k.A."
        hoch_str = f"{m['52w_hoch']:.2f}" if m.get("52w_hoch") is not None else "k.A."
        earnings = esc(m.get("naechste_earnings_datum")) or "kein Termin bekannt"
        ausblick = m.get("swing_ausblick")
        ausblick_html = (
            f'<p class="swing-ausblick">{esc(ausblick)}</p>'
            if ausblick else
            '<p class="swing-ausblick swing-ausblick-fehlt">Noch kein recherchierter Wochen-Ausblick '
            'für diesen Titel - nur das automatische Zahlen-Signal.</p>'
        )
        blocks.append(f'''
      <div class="swing-item">
        <div class="swing-item-head">
          <strong>{esc(t["ticker"])}</strong> · {esc(m.get("name"))}
          <span class="score-dot" style="background:{color}"></span>Score {t["score"]:.1f}
          · {preis_str} <span class="swing-neg">({abstand:.1f}% vom Hoch bei {hoch_str})</span>
          · Nächste Zahlen: {earnings}
        </div>
        {ausblick_html}
      </div>''')
    return "\n".join(blocks), len(kandidaten)


def build_table_rows(titel_list):
    ranked = sorted(titel_list, key=lambda t: (t.get("score") is None, -(t.get("score") or 0)))
    rows = []
    for t in ranked:
        m = t["metriken"]
        score = t.get("score")
        band = score_band(score) if score is not None else None
        color = STATUS.get(band, "#898781")
        abstand_tbl = compute_abstand_52w_hoch(m)
        swing = "🟢" if (abstand_tbl is not None and score is not None
                         and abstand_tbl <= SWING_MIN_ABSTAND_VOM_HOCH_PCT and score >= SWING_MIN_SCORE) else ""

        score_str = f"{score:.1f}" if score is not None else "k.A."
        score_sort = score if score is not None else -1
        preis_str = f"{m['preis']:.2f}" if m.get("preis") is not None else "k.A."
        preis_sort = m.get("preis") or 0
        kgv_str = str(m["kgv"]) if m.get("kgv") is not None else "k.A."
        kgv_sort = m.get("kgv") or 0
        wachstum_str = f"{m['umsatzwachstum_yoy']:.1f}%" if m.get("umsatzwachstum_yoy") is not None else "k.A."
        wachstum_sort = m.get("umsatzwachstum_yoy") or 0

        rows.append(f'''
        <tr>
          <td>{esc(t["ticker"])}</td>
          <td>{esc(m.get("name"))}</td>
          <td data-sort="{score_sort}"><span class="score-dot" style="background:{color}"></span>{score_str}</td>
          <td data-sort="{preis_sort}">{preis_str} {esc(m.get('waehrung') or '')}</td>
          <td data-sort="{kgv_sort}">{kgv_str}</td>
          <td data-sort="{wachstum_sort}">{wachstum_str}</td>
          <td>{esc(t.get('region'))}</td>
          <td>{esc(t.get('cluster') or '-')}</td>
          <td>{swing}</td>
        </tr>''')
    return "\n".join(rows)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aktien Dashboard</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --grid: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
    --cat-1: #2a78d6;
    --cat-2: #eb6834;
    --cat-3: #1baf7a;
    --good: #0ca30c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --grid: #2c2c2a;
      --baseline: #383835;
      --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
      --cat-1: #3987e5;
      --cat-2: #d95926;
      --cat-3: #199e70;
      --good: #0ca30c;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5;
    --cat-1: #3987e5;
    --cat-2: #d95926;
    --cat-3: #199e70;
    --good: #0ca30c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; flex-wrap: wrap; gap: 8px; }}
  h1 {{ font-size: 22px; margin: 0; }}
  .meta {{ color: var(--text-muted); font-size: 13px; }}
  .theme-toggle {{
    background: var(--surface-1); border: 1px solid var(--border); color: var(--text-secondary);
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
  }}
  .disclaimer {{
    background: var(--surface-1); border: 1px solid var(--border); border-left: 3px solid #fab219;
    padding: 10px 14px; border-radius: 6px; font-size: 13px; color: var(--text-secondary); margin: 16px 0 24px;
  }}
  .alert-card {{ border-left: 3px solid var(--cat-1); }}
  .alert-list {{ margin: 0; padding-left: 20px; font-size: 13px; color: var(--text-primary); line-height: 1.6; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; margin-bottom: 20px;
  }}
  .card h2 {{ font-size: 15px; margin: 0 0 16px; color: var(--text-primary); }}
  .stat-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .stat-tile {{ flex: 1; min-width: 140px; }}
  .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }}
  .stat-value {{ font-size: 24px; font-weight: 600; font-variant-numeric: proportional-nums; }}
  .stat-delta {{ font-size: 12px; margin-top: 2px; }}
  .delta-ok {{ color: var(--text-muted); }}
  .delta-bad {{ color: #d03b3b; font-weight: 600; }}
  .status-badge {{ font-size: 10px; padding: 1px 6px; border-radius: 10px; font-weight: 600; }}
  .status-badge.critical {{ background: #d03b3b; color: white; }}
  .grid-2 {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }}
  @media (max-width: 800px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 2px; height: 22px; }}
  .bar-label {{ width: 56px; font-size: 12px; color: var(--text-secondary); flex-shrink: 0; text-align: right; font-variant-numeric: tabular-nums; }}
  .bar-track {{ flex: 1; background: var(--grid); border-radius: 3px; height: 18px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 3px; min-width: 2px; }}
  .bar-value {{ width: 44px; font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }}
  .donut-wrap {{ display: flex; align-items: center; gap: 20px; }}
  .donut-svg {{ width: 140px; height: 140px; transform: rotate(-90deg); }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-secondary); margin-bottom: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--grid); }}
  th {{ color: var(--text-secondary); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ color: var(--text-primary); }}
  th::after {{ content: " ⇅"; color: var(--text-muted); font-size: 10px; }}
  td {{ font-variant-numeric: tabular-nums; }}
  .score-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
  .swing-card {{ border-left: 3px solid #fab219; }}
  .swing-neg {{ color: #d03b3b; font-weight: 600; }}
  .swing-empty {{ color: var(--text-muted); font-size: 13px; }}
  .swing-item {{ padding: 12px 0; border-bottom: 1px solid var(--grid); }}
  .swing-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .swing-item-head {{ font-size: 13px; color: var(--text-secondary); margin-bottom: 6px; }}
  .swing-item-head strong {{ color: var(--text-primary); font-size: 14px; }}
  .swing-ausblick {{ font-size: 13px; color: var(--text-primary); line-height: 1.5; margin: 0; }}
  .swing-ausblick-fehlt {{ color: var(--text-muted); font-style: italic; }}
  .weltlage-text {{ font-size: 13px; color: var(--text-primary); line-height: 1.6; margin: 0; }}
  .ipo-item {{ padding: 12px 0; border-bottom: 1px solid var(--grid); }}
  .ipo-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .ipo-item-head {{ font-size: 13px; color: var(--text-secondary); margin-bottom: 6px; }}
  .ipo-item-head strong {{ color: var(--text-primary); font-size: 14px; }}
  .ipo-status {{ font-size: 10px; padding: 1px 6px; border-radius: 10px; font-weight: 600; margin: 0 4px; }}
  .ipo-status-live {{ background: var(--good); color: white; }}
  .ipo-status-pending {{ background: var(--grid); color: var(--text-secondary); }}
  .ipo-beschreibung {{ font-size: 13px; color: var(--text-secondary); line-height: 1.5; margin: 0 0 4px; }}
  .ipo-einschaetzung {{ font-size: 13px; color: var(--text-primary); line-height: 1.5; margin: 0; }}
  footer {{ color: var(--text-muted); font-size: 12px; text-align: center; margin-top: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📊 Aktien Dashboard</h1>
    <button class="theme-toggle" onclick="toggleTheme()">🌓 Modus wechseln</button>
  </header>
  <div class="meta">Erzeugt: {erzeugt_am} · Modus: {modus}</div>

  <div class="disclaimer">⚠️ Keine Anlageberatung — rein mathematische Kennzahlen-Einordnung auf Basis der EODHD-Free-Tier-Daten und Web-Recherche. Alle Scores sind relative Einordnung innerhalb dieser Watchlist.</div>

  {ipo_alert_banner}

  <div class="card">
    <h2>Marktüberblick</h2>
    <div class="stat-row">
      {stat_tiles}
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Score-Ranking ({anzahl_titel} Titel)</h2>
      {score_bars}
    </div>
    <div class="card">
      <h2>Watchlist nach Cluster</h2>
      <div class="donut-wrap">
        <svg class="donut-svg" viewBox="0 0 42 42">
          <circle cx="21" cy="21" r="15.9155" fill="transparent" stroke="var(--grid)" stroke-width="4"></circle>
          {donut_segments}
        </svg>
        <div>{donut_legend}</div>
      </div>
    </div>
  </div>

  {swing_card}

  {ipo_card}

  {weltlage_card}

  {portfolio_card}

  <div class="card">
    <h2>Alle Titel im Detail</h2>
    <table id="main-table">
      <thead>
        <tr>
          <th onclick="sortTable(0)">Ticker</th>
          <th onclick="sortTable(1)">Name</th>
          <th onclick="sortTable(2)">Score</th>
          <th onclick="sortTable(3)">Kurs</th>
          <th onclick="sortTable(4)">KGV</th>
          <th onclick="sortTable(5)">Wachstum</th>
          <th onclick="sortTable(6)">Region</th>
          <th onclick="sortTable(7)">Cluster</th>
          <th onclick="sortTable(8)">Swing</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

  <footer>Aktien Recherche Tool · Score-Bänder: 🟢 ≥70 stark · 🟡 ≥50 solide · 🟠 ≥30 durchwachsen · 🔴 &lt;30 schwach</footer>
</div>

<script>
function toggleTheme() {{
  const root = document.documentElement;
  const current = root.getAttribute('data-theme');
  if (current === 'dark') {{ root.setAttribute('data-theme', 'light'); }}
  else if (current === 'light') {{ root.removeAttribute('data-theme'); }}
  else {{ root.setAttribute('data-theme', 'dark'); }}
}}

let sortDir = {{}};
function sortTable(col) {{
  const table = document.getElementById('main-table');
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const dir = sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {{
    const cellA = a.cells[col];
    const cellB = b.cells[col];
    const sortA = cellA.dataset.sort !== undefined ? parseFloat(cellA.dataset.sort) : cellA.innerText.toLowerCase();
    const sortB = cellB.dataset.sort !== undefined ? parseFloat(cellB.dataset.sort) : cellB.innerText.toLowerCase();
    if (sortA < sortB) return dir ? -1 : 1;
    if (sortA > sortB) return dir ? 1 : -1;
    return 0;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>
"""


def build(data, market=None, portfolio=None, ipo_history_path=None):
    titel_list = data.get("titel", [])
    stat_tiles = build_stat_tiles(market)
    score_bars = build_score_bars(titel_list)
    donut_segments, donut_legend = build_cluster_donut(titel_list)
    portfolio_bars = build_portfolio_bars(portfolio)

    if portfolio_bars:
        if "monatsrate" in (portfolio or {}):
            kapital_label = f"Sparplan {portfolio.get('monatsrate', 0):.0f} EUR/Monat"
        else:
            kapital_label = f"{(portfolio or {}).get('kapital', 0):.0f} EUR"
        portfolio_card = f'''
  <div class="card">
    <h2>Portfolio-Vorschlag ({kapital_label}, nur Score ≥ {(portfolio or {}).get("min_score", 50):.0f})</h2>
    {portfolio_bars}
  </div>'''
    else:
        portfolio_card = ""

    swing_result = build_swing_table(titel_list)
    if swing_result:
        swing_blocks, swing_count = swing_result
        swing_card = f'''
  <div class="card swing-card">
    <h2>🟡 Swing-Kandidaten ({swing_count}) — ≥10% unter 52-Wochen-Hoch, Score ≥ 50, Ausblick für die nächsten Wochen</h2>
    {swing_blocks}
  </div>'''
    else:
        swing_card = '<div class="card swing-card"><h2>🟡 Swing-Kandidaten</h2><div class="swing-empty">Aktuell kein Titel, der gleichzeitig ≥10% unter seinem 52-Wochen-Hoch UND fundamental solide (Score ≥ 50) ist.</div></div>'

    weltlage = data.get("weltlage_notiz")
    weltlage_html = (
        f'<div class="card"><h2>🌍 Weltlage / Makro-Einordnung</h2><p class="weltlage-text">{esc(weltlage)}</p></div>'
        if weltlage else ""
    )

    ipo_blocks = build_ipo_radar(data.get("ipo_radar"))
    ipo_card = (
        f'<div class="card"><h2>🚀 IPO-Radar (noch nicht in der Watchlist)</h2>{ipo_blocks}</div>'
        if ipo_blocks else ""
    )

    ipo_alerts = []
    if ipo_history_path and data.get("ipo_radar"):
        ipo_alerts = check_ipo_alerts(data["ipo_radar"], ipo_history_path)
    ipo_alert_banner = build_ipo_alert_banner(ipo_alerts)

    html = HTML_TEMPLATE.format(
        erzeugt_am=esc(data.get("erzeugt_am", "k.A.")),
        modus=esc(data.get("modus", "k.A.")),
        stat_tiles=stat_tiles or '<div class="stat-label">Keine Marktdaten geladen.</div>',
        anzahl_titel=len([t for t in titel_list if t.get("score") is not None]),
        score_bars=score_bars,
        donut_segments=donut_segments,
        donut_legend=donut_legend,
        swing_card=swing_card,
        weltlage_card=weltlage_html,
        ipo_card=ipo_card,
        ipo_alert_banner=ipo_alert_banner,
        portfolio_card=portfolio_card,
        table_rows=build_table_rows(titel_list),
    )
    return html


def main():
    parser = argparse.ArgumentParser(description="HTML-Dashboard aus JSON-Daten erzeugen")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--market", dest="market_path")
    parser.add_argument("--portfolio", dest="portfolio_path")
    parser.add_argument("--ipo-history", dest="ipo_history_path",
                         help="Pfad zur IPO-Verlaufsdatei (Standard: ipo_radar_history.json neben --in)")
    parser.add_argument("--out", dest="out_path", required=True)
    args = parser.parse_args()

    with open(args.in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    market = None
    if args.market_path:
        with open(args.market_path, "r", encoding="utf-8") as f:
            market = json.load(f)

    portfolio = None
    if args.portfolio_path:
        with open(args.portfolio_path, "r", encoding="utf-8") as f:
            portfolio = json.load(f)

    ipo_history_path = args.ipo_history_path or os.path.join(
        os.path.dirname(os.path.abspath(args.in_path)), "ipo_radar_history.json"
    )

    html = build(data, market=market, portfolio=portfolio, ipo_history_path=ipo_history_path)
    with open(args.out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard geschrieben nach {args.out_path}")


if __name__ == "__main__":
    main()
