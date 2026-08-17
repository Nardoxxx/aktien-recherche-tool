#!/usr/bin/env python3
"""
screener.py - woechentlicher Breiten-Screener ueber US + EU-Boersen, um neue
Kandidaten fuer die feste Watchlist zu finden (nicht fuer die tiefe
Einzelanalyse - das macht analyse.py fuer die feste Watchlist).

Nutzt die EODHD Screener-API (/screener), die im "All-in-One Package"
enthalten ist. WICHTIG: Die genauen Filterfeld-Namen der Screener-API sind
NICHT gegen die echte API verifiziert (kein Zugang beim Bau dieses Skripts).
Beim ersten echten Lauf unbedingt gegen https://eodhd.com/financial-apis/stock-market-screener-api
pruefen und FILTERS unten ggf. anpassen.

Nutzung:
    python3 screener.py --dry-run --out screener_data.json
    python3 screener.py --out screener_data.json
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(PROJECT_DIR, "watchlist.yaml")
ENV_PATH = os.path.expanduser("~/.config/aktien-recherche/.env")
EODHD_BASE = "https://eodhd.com/api"

# EODHD-Exchange-Codes, die wir abdecken wollen (US + wichtigste EU-Boersen).
# TODO beim ersten echten Lauf verifizieren, ob diese Codes exakt so von der
# Screener-API akzeptiert werden.
EXCHANGES = ["US", "XETRA", "PA", "AS", "MI", "LSE", "SW", "MC", "CO"]

# Grobe Filter fuer "Wachstumspotenzial" - bewusst simpel gehalten, damit der
# Screener nicht zu eng wird. Marktkapitalisierung als Mindest-Liquiditaetsfilter.
# TODO: pruefen, ob die Screener-API Umsatzwachstum/PEG direkt als Filterfeld
# unterstuetzt: wenn ja, hier ergaenzen statt erst nach dem Abruf zu sortieren.
MIN_MARKET_CAP = 5_000_000_000  # 5 Mrd. - filtert Micro-/Small-Caps raus
RESULT_LIMIT_PER_EXCHANGE = 50
TOP_N_CANDIDATES = 10


def load_watchlist_symbols():
    """Liest nur die eodhd_symbol-Werte aus watchlist.yaml (fuer Dedupe)."""
    symbols = set()
    if not os.path.exists(WATCHLIST_PATH):
        return symbols
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("eodhd_symbol:"):
                symbols.add(stripped.split(":", 1)[1].strip())
    return symbols


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


def fetch_screener_page(exchange, api_key, offset=0):
    filters = json.dumps([
        ["exchange", "=", exchange],
        ["market_capitalization", ">", MIN_MARKET_CAP],
    ])
    params = {
        "api_token": api_key,
        "fmt": "json",
        "filters": filters,
        "limit": RESULT_LIMIT_PER_EXCHANGE,
        "offset": offset,
        "sort": "market_capitalization.desc",
    }
    url = f"{EODHD_BASE}/screener?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aktien-recherche-tool/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("data", []) if isinstance(data, dict) else data


def momentum_score(row):
    """Grobe Sortierung nach Kursmomentum, solange keine Wachstums-/Bewertungsfelder
    aus der Screener-API bestaetigt sind. price vs 200-Tage-Linie in %."""
    price = row.get("adjusted_close") or row.get("close")
    ma200 = row.get("200day_ma") or row.get("200DayMA")
    if not price or not ma200:
        return -999
    try:
        return (float(price) - float(ma200)) / float(ma200) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return -999


def run(dry_run=False, out_path=None):
    watchlist_symbols = load_watchlist_symbols()
    candidates = []

    if dry_run:
        mock_names = [
            ("SNOW.US", "Snowflake"), ("CRWD.US", "CrowdStrike"), ("DHL.XETRA", "DHL Group"),
            ("BNP.PA", "BNP Paribas"), ("ADYEN.AS", "Adyen"), ("STLA.MI", "Stellantis"),
        ]
        for i, (symbol, name) in enumerate(mock_names):
            candidates.append({
                "ticker": symbol.split(".")[0],
                "eodhd_symbol": symbol,
                "name": f"(Mock) {name}",
                "umsatzwachstum_yoy": 12.0 + i * 3.5,
                "kgv": 18.0 + i * 2,
                "market_cap": 20_000_000_000 + i * 5_000_000_000,
            })
    else:
        api_key = load_api_key()
        if not api_key:
            print(
                "Kein EODHD_API_KEY gefunden. Siehe analyse.py fuer Setup-Hinweise. "
                "Fuer einen Test ohne Key: --dry-run verwenden.",
                file=sys.stderr,
            )
            sys.exit(1)
        raw_rows = []
        for exchange in EXCHANGES:
            try:
                rows = fetch_screener_page(exchange, api_key)
                raw_rows.extend(rows)
            except Exception as e:
                print(f"WARNUNG: Screener-Fehler bei Boerse {exchange}: {e}", file=sys.stderr)

        # Bereits in der festen Watchlist vorhandene Titel rausfiltern
        filtered = [r for r in raw_rows if r.get("code") and f"{r.get('code')}.{r.get('exchange')}" not in watchlist_symbols]
        filtered.sort(key=momentum_score, reverse=True)
        for row in filtered[:TOP_N_CANDIDATES]:
            candidates.append({
                "ticker": row.get("code"),
                "eodhd_symbol": f"{row.get('code')}.{row.get('exchange')}",
                "name": row.get("name"),
                "umsatzwachstum_yoy": None,  # TODO: sobald Feldname verifiziert, hier einsetzen
                "kgv": row.get("pe_ratio") or row.get("PERatio"),
                "market_cap": row.get("market_capitalization"),
            })

    output = {
        "erzeugt_am": datetime.now().isoformat(timespec="seconds"),
        "modus": "dry-run" if dry_run else "live",
        "kandidaten": candidates,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Ergebnis geschrieben nach {out_path}", file=sys.stderr)
    else:
        print(text)
    return output


def main():
    parser = argparse.ArgumentParser(description="Woechentlicher Breiten-Screener fuer neue Watchlist-Kandidaten")
    parser.add_argument("--dry-run", action="store_true", help="Mock-Daten statt echter API-Calls")
    parser.add_argument("--out", help="Ergebnis als JSON in diese Datei schreiben statt nach stdout")
    args = parser.parse_args()
    run(dry_run=args.dry_run, out_path=args.out)


if __name__ == "__main__":
    main()
