#!/usr/bin/env python3
"""
analyse.py - Kern-Skript des Aktien Recherche Tools.

Holt für jeden Ticker in watchlist.yaml Rohdaten von der EODHD-API (Kurs,
Fundamentaldaten, Earnings-Kalender, Analysten-Konsens), berechnet daraus einen
transparent nachvollziehbaren Score (0-100) und schreibt das Ergebnis als JSON
nach stdout bzw. in eine Datei. report_template.py macht daraus den lesbaren
Markdown-Report.

WICHTIG: Läuft absichtlich ohne externe Python-Pakete (nur Standardbibliothek),
damit es in der Scheduled-Task-Umgebung ohne "pip install" zuverlässig läuft.
Das bedeutet u.a. einen selbstgeschriebenen Mini-YAML-Parser (siehe unten) statt
PyYAML - er kann NUR das schmale Format von watchlist.yaml lesen, kein YAML
im Allgemeinen.

Nutzung:
    python3 analyse.py --dry-run                # Mock-Daten, kein API-Call, zum Testen der Pipeline
    python3 analyse.py                            # echter Lauf, braucht EODHD_API_KEY
    python3 analyse.py --ticker NVDA               # nur ein Ticker (schneller Einzeltest)
    python3 analyse.py --out report_data.json      # Ergebnis zusätzlich in Datei schreiben
    python3 analyse.py --score-only --in recherchiert.json --out scored.json
                                                    # Score NEU berechnen aus einer Datei, in der die
                                                    # Fundamentaldaten-Felder bereits von Hand/Agent
                                                    # befuellt wurden (siehe "Free-Tier-Modus" unten)

Free-Tier-Modus (kein bezahltes EODHD-Abo):
Der EODHD-Free-Plan blockiert /fundamentals und /calendar/earnings ("Only EOD data
allowed for free users"), erlaubt aber /real-time und /eod (Kurs-Historie) weiterhin.
Deshalb versucht dieses Skript pro Ticker zuerst die vollen Fundamentaldaten zu holen;
schlaegt das mit HTTP 403 fehl, faellt es automatisch auf einen "Nur-Kurs"-Modus
zurueck: Kurs, 50-/200-Tage-Linie und 52-Wochen-Range werden selbst aus der freien
EOD-Historie berechnet, alle anderen Felder (KGV, PEG, Wachstum, Margen, ROE,
Analysten-Kursziel, Earnings-Termin) bleiben None und werden im Report als
"Recherche noetig" markiert. Der Agent im Scheduled Task recherchiert diese Werte
dann selbst per WebSearch/WebFetch bei einer freien Quelle (z.B. stockanalysis.com),
traegt sie in die JSON-Datei ein und ruft danach `--score-only` auf, damit der Score
mit den recherchierten Zahlen neu (und weiterhin deterministisch) berechnet wird.
Sobald irgendwann ein bezahltes EODHD-Abo existiert, macht das Skript automatisch
wieder alles in einem Rutsch - kein Code-Aenderung noetig.

Score-Formel (bewusst transparent, keine Black Box):
    Score = 0.30 * Wachstum
          + 0.25 * Bewertung
          + 0.20 * Qualitaet
          + 0.15 * Momentum
          + 0.10 * AnalystenKonsens

Jede Teilkomponente wird zunaechst pro Kennzahl auf einen 0-100 Perzentilwert
INNERHALB DER WATCHLIST normiert (relative Einordnung: "guenstiger/staerker als
wie viel Prozent der anderen Watchlist-Titel"), dann innerhalb der Komponente
gemittelt. Das macht den Score fuer diese konkrete Watchlist aussagekraeftig,
nicht als absolute Marktkennzahl.
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(PROJECT_DIR, "watchlist.yaml")
ENV_PATH = os.path.expanduser("~/.config/aktien-recherche/.env")

EODHD_BASE = "https://eodhd.com/api"

# Waehrung je EODHD-Exchange-Suffix, fuer korrekte Anzeige im Report (Kennzahlen/
# Score selbst sind waehrungsneutrale Verhaeltnisse und davon nicht betroffen).
EXCHANGE_CURRENCY = {
    "US": "USD", "XETRA": "EUR", "PA": "EUR", "AS": "EUR", "MC": "EUR",
    "CO": "DKK", "SW": "CHF", "MI": "EUR", "LSE": "EUR",
}


def currency_for_symbol(symbol):
    exchange = symbol.split(".")[-1] if "." in symbol else "US"
    return EXCHANGE_CURRENCY.get(exchange, "EUR")

WEIGHTS = {
    "wachstum": 0.30,
    "bewertung": 0.25,
    "qualitaet": 0.20,
    "momentum": 0.15,
    "analysten": 0.10,
}


# ---------------------------------------------------------------------------
# Mini-YAML-Parser (nur fuer unser eigenes, schmales watchlist.yaml-Format)
# ---------------------------------------------------------------------------

def load_watchlist(path=WATCHLIST_PATH):
    """Parst watchlist.yaml. Erwartet: eine flache Liste von Objekten unter
    'watchlist:', jedes Objekt beginnt mit '  - key: value' und hat weitere
    Zeilen '    key: value'. Kommentare (#) und Leerzeilen werden ignoriert."""
    entries = []
    current = None
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.split(" #", 1)[0].rstrip()  # Inline-Kommentare grob entfernen
            if not stripped.strip():
                continue
            if stripped.strip().startswith("#"):
                continue
            if stripped.strip() == "watchlist:":
                continue
            if line.startswith("  - "):
                if current is not None:
                    entries.append(current)
                current = {}
                key_value = line[len("  - "):]
                _parse_kv_into(current, key_value)
            elif line.startswith("    ") and current is not None:
                key_value = line.strip()
                _parse_kv_into(current, key_value)
    if current is not None:
        entries.append(current)
    return entries


def _parse_kv_into(target_dict, key_value_str):
    if ":" not in key_value_str:
        return
    key, _, value = key_value_str.partition(":")
    key = key.strip()
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    target_dict[key] = value


# ---------------------------------------------------------------------------
# API-Key laden
# ---------------------------------------------------------------------------

def load_api_key():
    if "EODHD_API_KEY" in os.environ:
        return os.environ["EODHD_API_KEY"]
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("EODHD_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# EODHD API-Calls
# ---------------------------------------------------------------------------

def api_get(path, api_key, params=None):
    params = dict(params or {})
    params["api_token"] = api_key
    params["fmt"] = "json"
    url = f"{EODHD_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aktien-recherche-tool/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_fundamentals(symbol, api_key):
    return api_get(f"/fundamentals/{symbol}", api_key)


def fetch_realtime_price(symbol, api_key):
    return api_get(f"/real-time/{symbol}", api_key)


def fetch_next_earnings(symbol, api_key):
    today = date.today()
    frm = today.isoformat()
    to = date(today.year + 1, today.month, today.day).isoformat()
    data = api_get("/calendar/earnings", api_key, {"symbols": symbol, "from": frm, "to": to})
    events = data.get("earnings", []) if isinstance(data, dict) else []
    events = sorted(events, key=lambda e: e.get("report_date", "9999-99-99"))
    return events[0] if events else None


def fetch_eod_history(symbol, api_key, days=280):
    """Free-Tier-tauglich: taegliche Kurs-Historie (funktioniert auch ohne
    bezahltes Abo, im Gegensatz zu /fundamentals und /calendar/earnings)."""
    return api_get(f"/eod/{symbol}", api_key, {"period": "d", "order": "d", "limit": days})


def compute_price_metrics_from_history(history, realtime):
    """Baut Kurs/Momentum-Kennzahlen NUR aus freien Endpunkten (/real-time,
    /eod) - kein /fundamentals noetig. history: Liste von {date, close, ...},
    neueste zuerst (order=d)."""
    closes = [_to_float(h.get("adjusted_close") or h.get("close")) for h in history]
    closes = [c for c in closes if c is not None]

    price = _to_float(realtime.get("close")) if isinstance(realtime, dict) else None
    if price is None and closes:
        price = closes[0]

    kurs_50t = round(sum(closes[:50]) / len(closes[:50]), 2) if len(closes) >= 50 else None
    kurs_200t = round(sum(closes[:200]) / len(closes[:200]), 2) if len(closes) >= 200 else None
    jahr_closes = closes[:252] if closes else []
    hoch_52w = round(max(jahr_closes), 2) if jahr_closes else None
    tief_52w = round(min(jahr_closes), 2) if jahr_closes else None

    return {
        "preis": price,
        "kurs_50t": kurs_50t,
        "kurs_200t": kurs_200t,
        "52w_hoch": hoch_52w,
        "52w_tief": tief_52w,
    }


def free_tier_metrics(symbol, entry, api_key):
    """Fallback-Pfad, wenn /fundamentals mit 403 (Free-Plan) fehlschlaegt.
    Liefert Kurs-Kennzahlen aus freien Endpunkten, alle Fundamentaldaten-Felder
    bleiben None und sind vom Agenten per Web-Recherche zu befuellen (siehe
    Modul-Docstring "Free-Tier-Modus")."""
    realtime = api_get(f"/real-time/{symbol}", api_key)
    history = fetch_eod_history(symbol, api_key)
    preis_metriken = compute_price_metrics_from_history(history, realtime)

    return {
        "symbol": symbol,
        "name": entry.get("name"),
        "sektor": entry.get("sektor"),
        "beschreibung": None,
        "waehrung": currency_for_symbol(symbol),
        "kgv": None,
        "peg": None,
        "ev_ebitda": None,
        "umsatzwachstum_yoy": None,
        "gewinnwachstum_yoy": None,
        "gewinnmarge": None,
        "operative_marge": None,
        "roe": None,
        "beta": None,
        "analysten_kursziel": None,
        "analysten_strong_buy": None,
        "analysten_buy": None,
        "analysten_hold": None,
        "analysten_sell": None,
        "analysten_strong_sell": None,
        "naechste_earnings_datum": None,
        "naechste_earnings_eps_schaetzung": None,
        "quelle": "eodhd-free+recherche-noetig",
        **preis_metriken,
    }


# ---------------------------------------------------------------------------
# Rohdaten -> Kennzahlen extrahieren (echte API-Struktur)
# ---------------------------------------------------------------------------

def extract_metrics(symbol, fundamentals, realtime, next_earnings):
    highlights = fundamentals.get("Highlights", {}) or {}
    valuation = fundamentals.get("Valuation", {}) or {}
    technicals = fundamentals.get("Technicals", {}) or {}
    general = fundamentals.get("General", {}) or {}
    analyst = fundamentals.get("AnalystRatings", {}) or {}

    price = None
    if isinstance(realtime, dict):
        price = _to_float(realtime.get("close"))

    metrics = {
        "symbol": symbol,
        "name": general.get("Name"),
        "sektor": general.get("Sector"),
        "beschreibung": general.get("Description"),
        "preis": price,
        "waehrung": general.get("CurrencyCode"),
        "kgv": _to_float(highlights.get("PERatio")),
        "peg": _to_float(highlights.get("PEGRatio")),
        "ev_ebitda": _to_float(valuation.get("EnterpriseValueEbitda")),
        "umsatzwachstum_yoy": _to_float(highlights.get("QuarterlyRevenueGrowthYOY")),
        "gewinnwachstum_yoy": _to_float(highlights.get("QuarterlyEarningsGrowthYOY")),
        "gewinnmarge": _to_float(highlights.get("ProfitMargin")),
        "operative_marge": _to_float(highlights.get("OperatingMarginTTM")),
        "roe": _to_float(highlights.get("ReturnOnEquityTTM")),
        "kurs_50t": _to_float(technicals.get("50DayMA")),
        "kurs_200t": _to_float(technicals.get("200DayMA")),
        "52w_hoch": _to_float(technicals.get("52WeekHigh")),
        "52w_tief": _to_float(technicals.get("52WeekLow")),
        "beta": _to_float(technicals.get("Beta")),
        "analysten_kursziel": _to_float(highlights.get("WallStreetTargetPrice")),
        "analysten_strong_buy": _to_int(analyst.get("StrongBuy")),
        "analysten_buy": _to_int(analyst.get("Buy")),
        "analysten_hold": _to_int(analyst.get("Hold")),
        "analysten_sell": _to_int(analyst.get("Sell")),
        "analysten_strong_sell": _to_int(analyst.get("StrongSell")),
        "naechste_earnings_datum": next_earnings.get("report_date") if next_earnings else None,
        "naechste_earnings_eps_schaetzung": _to_float(next_earnings.get("estimate")) if next_earnings else None,
        "quelle": "eodhd-paid",
    }
    return metrics


def _to_float(value):
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        if value in (None, "", "None"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Score-Berechnung
# ---------------------------------------------------------------------------

def percentile_rank(values_by_symbol, symbol, higher_is_better=True):
    """Gibt einen 0-100 Perzentilwert fuer values_by_symbol[symbol] zurueck,
    relativ zu allen (nicht-None) Werten im Dict. None -> None (wird beim
    Komponenten-Mittel ausgelassen, nicht als 0 gewertet)."""
    value = values_by_symbol.get(symbol)
    if value is None:
        return None
    valid = [v for v in values_by_symbol.values() if v is not None]
    if len(valid) <= 1:
        return 50.0
    rank = sum(1 for v in valid if v < value) if higher_is_better else sum(1 for v in valid if v > value)
    return round(100.0 * rank / (len(valid) - 1), 1)


def momentum_raw(m):
    """Grobe Momentum-Kennzahl: Abstand Kurs zu 50T/200T-Linie in %, gemittelt."""
    if not m["preis"] or not m["kurs_50t"] or not m["kurs_200t"]:
        return None
    dist_50 = (m["preis"] - m["kurs_50t"]) / m["kurs_50t"] * 100
    dist_200 = (m["preis"] - m["kurs_200t"]) / m["kurs_200t"] * 100
    return (dist_50 + dist_200) / 2


def analysten_upside_raw(m):
    if not m["preis"] or not m["analysten_kursziel"]:
        return None
    return (m["analysten_kursziel"] - m["preis"]) / m["preis"] * 100


def compute_scores(all_metrics):
    """all_metrics: dict symbol -> metrics-dict (aus extract_metrics).
    Gibt dict symbol -> {score, subscores, formel} zurueck."""
    symbols = list(all_metrics.keys())

    wachstum_raw = {s: _avg_ignore_none([all_metrics[s]["umsatzwachstum_yoy"], all_metrics[s]["gewinnwachstum_yoy"]]) for s in symbols}
    bewertung_raw_kgv = {s: all_metrics[s]["kgv"] for s in symbols}
    bewertung_raw_peg = {s: all_metrics[s]["peg"] for s in symbols}
    qualitaet_raw = {s: _avg_ignore_none([all_metrics[s]["gewinnmarge"], all_metrics[s]["operative_marge"], all_metrics[s]["roe"]]) for s in symbols}
    momentum_raw_vals = {s: momentum_raw(all_metrics[s]) for s in symbols}
    analysten_raw_vals = {s: analysten_upside_raw(all_metrics[s]) for s in symbols}

    results = {}
    for s in symbols:
        wachstum_pct = percentile_rank(wachstum_raw, s, higher_is_better=True)
        # Bei Bewertung ist NIEDRIGER KGV/PEG besser -> higher_is_better=False
        kgv_pct = percentile_rank(bewertung_raw_kgv, s, higher_is_better=False)
        peg_pct = percentile_rank(bewertung_raw_peg, s, higher_is_better=False)
        bewertung_pct = _avg_ignore_none([kgv_pct, peg_pct])
        qualitaet_pct = percentile_rank(qualitaet_raw, s, higher_is_better=True)
        momentum_pct = percentile_rank(momentum_raw_vals, s, higher_is_better=True)
        analysten_pct = percentile_rank(analysten_raw_vals, s, higher_is_better=True)

        subscores = {
            "wachstum": wachstum_pct,
            "bewertung": bewertung_pct,
            "qualitaet": qualitaet_pct,
            "momentum": momentum_pct,
            "analysten": analysten_pct,
        }

        gewichtete_teile = []
        gewichtssumme = 0.0
        for key, weight in WEIGHTS.items():
            val = subscores[key]
            if val is not None:
                gewichtete_teile.append(val * weight)
                gewichtssumme += weight
        score = round(sum(gewichtete_teile) / gewichtssumme, 1) if gewichtssumme > 0 else None

        results[s] = {
            "score": score,
            "subscores": subscores,
            "gewichte": WEIGHTS,
        }
    return results


def _avg_ignore_none(values):
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


# ---------------------------------------------------------------------------
# Mock-Daten fuer --dry-run (kein API-Call, zum Testen der Pipeline)
# ---------------------------------------------------------------------------

def mock_metrics(entry, seed):
    import random
    rnd = random.Random(seed)
    price = round(rnd.uniform(20, 900), 2)
    ma50 = price * rnd.uniform(0.9, 1.1)
    ma200 = price * rnd.uniform(0.85, 1.15)
    return {
        "symbol": entry["eodhd_symbol"],
        "name": entry["name"],
        "sektor": entry.get("sektor"),
        "beschreibung": f"(Mock) {entry['name']} ist ein Platzhaltertext fuer den Dry-Run - im echten Lauf kommt hier die EODHD-Firmenbeschreibung.",
        "preis": price,
        "waehrung": "USD" if entry.get("region", "").startswith("US") else "EUR",
        "kgv": round(rnd.uniform(8, 60), 1),
        "peg": round(rnd.uniform(0.5, 4), 2),
        "ev_ebitda": round(rnd.uniform(5, 40), 1),
        "umsatzwachstum_yoy": round(rnd.uniform(-5, 40), 1),
        "gewinnwachstum_yoy": round(rnd.uniform(-10, 50), 1),
        "gewinnmarge": round(rnd.uniform(2, 40), 1),
        "operative_marge": round(rnd.uniform(2, 45), 1),
        "roe": round(rnd.uniform(2, 50), 1),
        "kurs_50t": round(ma50, 2),
        "kurs_200t": round(ma200, 2),
        "52w_hoch": round(price * rnd.uniform(1.0, 1.3), 2),
        "52w_tief": round(price * rnd.uniform(0.6, 0.95), 2),
        "beta": round(rnd.uniform(0.5, 2.0), 2),
        "analysten_kursziel": round(price * rnd.uniform(0.85, 1.35), 2),
        "analysten_strong_buy": rnd.randint(0, 15),
        "analysten_buy": rnd.randint(0, 15),
        "analysten_hold": rnd.randint(0, 10),
        "analysten_sell": rnd.randint(0, 3),
        "analysten_strong_sell": rnd.randint(0, 2),
        "naechste_earnings_datum": "2026-08-26" if entry["ticker"] == "NVDA" else None,
        "naechste_earnings_eps_schaetzung": round(rnd.uniform(0.5, 5), 2),
        "quelle": "mock",
    }


# ---------------------------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------------------------

def run(dry_run=False, only_ticker=None, out_path=None):
    watchlist = load_watchlist()
    if only_ticker:
        watchlist = [e for e in watchlist if e["ticker"].upper() == only_ticker.upper()]
        if not watchlist:
            print(f"Ticker {only_ticker} nicht in watchlist.yaml gefunden.", file=sys.stderr)
            sys.exit(1)

    all_metrics = {}

    if dry_run:
        for i, entry in enumerate(watchlist):
            all_metrics[entry["eodhd_symbol"]] = mock_metrics(entry, seed=i)
    else:
        api_key = load_api_key()
        if not api_key:
            print(
                "Kein EODHD_API_KEY gefunden. Entweder als Umgebungsvariable setzen "
                f"oder in {ENV_PATH} als 'EODHD_API_KEY=...' hinterlegen. "
                "Fuer einen Test ohne Key: --dry-run verwenden.",
                file=sys.stderr,
            )
            sys.exit(1)
        for entry in watchlist:
            symbol = entry["eodhd_symbol"]
            try:
                fundamentals = fetch_fundamentals(symbol, api_key)
                realtime = fetch_realtime_price(symbol, api_key)
                next_earnings = fetch_next_earnings(symbol, api_key)
                all_metrics[symbol] = extract_metrics(symbol, fundamentals, realtime, next_earnings)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    # Free-Plan: /fundamentals + /calendar/earnings gesperrt.
                    # Fallback auf reine Kurs-Kennzahlen aus freien Endpunkten.
                    try:
                        all_metrics[symbol] = free_tier_metrics(symbol, entry, api_key)
                        print(
                            f"HINWEIS: {symbol} nur mit Free-Tier-Kursdaten befuellt "
                            "(Fundamentaldaten brauchen Recherche, siehe Modul-Docstring).",
                            file=sys.stderr,
                        )
                    except Exception as e2:
                        print(f"WARNUNG: Auch Free-Tier-Fallback fehlgeschlagen bei {symbol}: {e2}", file=sys.stderr)
                else:
                    print(f"WARNUNG: API-Fehler bei {symbol}: {e}", file=sys.stderr)
            except Exception as e:  # bewusst breit: ein Ticker soll den Gesamtlauf nicht abbrechen
                print(f"WARNUNG: Fehler bei {symbol}: {e}", file=sys.stderr)

    scores = compute_scores(all_metrics)

    output = {
        "erzeugt_am": datetime.now().isoformat(timespec="seconds"),
        "modus": "dry-run" if dry_run else "live",
        "gewichte": WEIGHTS,
        "titel": [],
    }
    for entry in watchlist:
        symbol = entry["eodhd_symbol"]
        if symbol not in all_metrics:
            continue
        output["titel"].append({
            "ticker": entry["ticker"],
            "region": entry.get("region"),
            "cluster": entry.get("cluster"),
            "notiz_watchlist": entry.get("notiz"),
            "metriken": all_metrics[symbol],
            "score": scores[symbol]["score"],
            "subscores": scores[symbol]["subscores"],
        })

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Ergebnis geschrieben nach {out_path}", file=sys.stderr)
    else:
        print(text)
    return output


def refresh_prices(in_path, out_path=None):
    """Fuer den taeglichen Lauf im Free-Tier-Modus: nimmt eine bereits komplette
    JSON-Datei (z.B. das Ergebnis der letzten woechentlichen Tiefen-Recherche
    inkl. von Hand/Agent ergaenzter Fundamentaldaten) und aktualisiert NUR die
    Kurs-Felder (preis, kurs_50t, kurs_200t, 52w_hoch, 52w_tief) ueber die
    freien EODHD-Endpunkte. Alle Fundamentaldaten-Felder bleiben unveraendert
    (sie werden nur einmal pro Woche neu recherchiert, siehe Modul-Docstring).
    Score wird NICHT automatisch neu berechnet - danach --score-only aufrufen."""
    api_key = load_api_key()
    if not api_key:
        print(f"Kein EODHD_API_KEY gefunden (siehe {ENV_PATH}).", file=sys.stderr)
        sys.exit(1)

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for t in data.get("titel", []):
        symbol = t["metriken"]["symbol"]
        try:
            realtime = api_get(f"/real-time/{symbol}", api_key)
            history = fetch_eod_history(symbol, api_key)
            preis_metriken = compute_price_metrics_from_history(history, realtime)
            t["metriken"].update(preis_metriken)
        except Exception as e:
            print(f"WARNUNG: Kurs-Refresh fehlgeschlagen bei {symbol}: {e}", file=sys.stderr)

    data["kurse_aktualisiert_am"] = datetime.now().isoformat(timespec="seconds")

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Kurse aktualisiert, geschrieben nach {out_path}", file=sys.stderr)
    else:
        print(text)
    return data


def rescore(in_path, out_path=None):
    """Liest eine JSON-Datei im Output-Format von run() (z.B. nach Free-Tier-Lauf
    plus von Hand/Agent ergaenzter Fundamentaldaten) und berechnet die Scores neu.
    Aendert NUR score/subscores, laesst alle metriken-Felder unangetastet."""
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_metrics = {t["metriken"]["symbol"]: t["metriken"] for t in data.get("titel", [])}
    scores = compute_scores(all_metrics)
    for t in data["titel"]:
        symbol = t["metriken"]["symbol"]
        t["score"] = scores[symbol]["score"]
        t["subscores"] = scores[symbol]["subscores"]
    data["neu_bewertet_am"] = datetime.now().isoformat(timespec="seconds")

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Neu bewertetes Ergebnis geschrieben nach {out_path}", file=sys.stderr)
    else:
        print(text)
    return data


def main():
    parser = argparse.ArgumentParser(description="Aktien Recherche Tool - Kennzahlen & Score berechnen")
    parser.add_argument("--dry-run", action="store_true", help="Mock-Daten statt echter API-Calls (zum Testen)")
    parser.add_argument("--ticker", help="Nur diesen einen Ticker verarbeiten (z.B. NVDA)")
    parser.add_argument("--out", help="Ergebnis als JSON in diese Datei schreiben statt nach stdout")
    parser.add_argument("--score-only", action="store_true",
                         help="Score aus einer bereits vorhandenen (z.B. von Hand recherchierten) "
                              "JSON-Datei neu berechnen, siehe --in")
    parser.add_argument("--refresh-prices", action="store_true",
                         help="Nur Kurs-Felder in einer bestehenden JSON-Datei aktualisieren "
                              "(taeglicher Free-Tier-Modus), Fundamentaldaten bleiben stehen")
    parser.add_argument("--in", dest="in_path", help="Eingabedatei fuer --score-only / --refresh-prices")
    args = parser.parse_args()

    if args.score_only:
        if not args.in_path:
            print("--score-only braucht --in <datei>", file=sys.stderr)
            sys.exit(1)
        rescore(args.in_path, out_path=args.out)
    elif args.refresh_prices:
        if not args.in_path:
            print("--refresh-prices braucht --in <datei>", file=sys.stderr)
            sys.exit(1)
        refresh_prices(args.in_path, out_path=args.out)
    else:
        run(dry_run=args.dry_run, only_ticker=args.ticker, out_path=args.out)


if __name__ == "__main__":
    main()
