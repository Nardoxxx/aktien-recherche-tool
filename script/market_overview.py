#!/usr/bin/env python3
"""
market_overview.py - holt Indexstaende (S&P 500, Nasdaq 100, DAX, Euro Stoxx 50,
VIX) ueber die freien EODHD-EOD-Endpunkte (funktioniert auch ohne bezahltes Abo)
und erkennt automatisch, ob sich der Gesamtmarkt gerade in einer Korrektur/Krise
befindet (Definition unten). Ergebnis ist die deterministische Zahlen-Basis fuer
den "Marktueberblick"-Abschnitt im Report - nur die textliche Einordnung/News
kommen weiterhin vom Agenten per WebSearch, die Indexstaende selbst nicht mehr.

Krisen-Definition (bewusst einfach und transparent, kein Geheimnis):
Ein Index gilt als "in Korrektur", wenn sein aktueller Schlusskurs mindestens
KRISE_SCHWELLE_PCT unter seinem Hoch der letzten LOOKBACK_TAGE Handelstage liegt.
Bricht IRGENDEIN beobachteter Index diese Schwelle, gilt global "krise_erkannt".

Nutzung:
    python3 market_overview.py --out market.json
    python3 market_overview.py --dry-run --out market.json   # Mock-Daten zum Testen
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

ENV_PATH = os.path.expanduser("~/.config/aktien-recherche/.env")
EODHD_BASE = "https://eodhd.com/api"

INDICES = {
    "S&P 500": "GSPC.INDX",
    "Nasdaq 100": "NDX.INDX",
    "DAX": "GDAXI.INDX",
    "Euro Stoxx 50": "STOXX50E.INDX",
    "VIX": "VIX.INDX",
}

LOOKBACK_TAGE = 130  # ca. 6 Boersenmonate
KRISE_SCHWELLE_PCT = -7.0  # ab -7% vom 6-Monats-Hoch gilt ein AKTIEN-Index als "in Korrektur"
VIX_KRISE_SCHWELLE = 30.0  # VIX-Sonderfall: hier ist ein HOHER absoluter Stand die Krise, nicht
                            # "weit unters Hoch gefallen" - ein fallender VIX bedeutet Beruhigung,
                            # keine Krise. Deshalb eigene, umgekehrte Logik statt der generischen Regel.


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


def fetch_index_history(symbol, api_key, days=LOOKBACK_TAGE):
    params = {"api_token": api_key, "fmt": "json", "period": "d", "order": "d", "limit": days}
    url = f"{EODHD_BASE}/eod/{symbol}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aktien-recherche-tool/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def analyze_index(name, symbol, api_key):
    history = fetch_index_history(symbol, api_key)
    closes = [h.get("adjusted_close") or h.get("close") for h in history if h.get("close") is not None]
    if not closes:
        return {"name": name, "symbol": symbol, "fehler": "keine Daten"}
    aktuell = closes[0]
    hoch = max(closes)
    abstand_pct = round((aktuell - hoch) / hoch * 100, 2)

    if name == "VIX":
        # Sonderfall: hoher VIX-STAND ist die Krise, nicht "weit unter eigenem Hoch".
        in_korrektur = aktuell >= VIX_KRISE_SCHWELLE
    else:
        in_korrektur = abstand_pct <= KRISE_SCHWELLE_PCT

    return {
        "name": name,
        "symbol": symbol,
        "aktuell": round(aktuell, 2),
        "hoch_lookback": round(hoch, 2),
        "abstand_vom_hoch_pct": abstand_pct,
        "in_korrektur": in_korrektur,
    }


def mock_indices():
    # Realistische Platzhalter fuer --dry-run, kein API-Call
    return {
        "S&P 500": {"name": "S&P 500", "symbol": "GSPC.INDX", "aktuell": 7755.2, "hoch_lookback": 7810.0, "abstand_vom_hoch_pct": -0.7, "in_korrektur": False},
        "Nasdaq 100": {"name": "Nasdaq 100", "symbol": "NDX.INDX", "aktuell": 30019.9, "hoch_lookback": 30195.7, "abstand_vom_hoch_pct": -0.58, "in_korrektur": False},
        "DAX": {"name": "DAX", "symbol": "GDAXI.INDX", "aktuell": 26338.6, "hoch_lookback": 26541.3, "abstand_vom_hoch_pct": -0.76, "in_korrektur": False},
        "Euro Stoxx 50": {"name": "Euro Stoxx 50", "symbol": "STOXX50E.INDX", "aktuell": 6530.5, "hoch_lookback": 6570.3, "abstand_vom_hoch_pct": -0.61, "in_korrektur": False},
        "VIX": {"name": "VIX", "symbol": "VIX.INDX", "aktuell": 15.19, "hoch_lookback": 18.0, "abstand_vom_hoch_pct": -15.6, "in_korrektur": False},
    }


def run(dry_run=False, out_path=None):
    if dry_run:
        indices = mock_indices()
    else:
        api_key = load_api_key()
        if not api_key:
            print(f"Kein EODHD_API_KEY gefunden (siehe {ENV_PATH}).", file=sys.stderr)
            sys.exit(1)
        indices = {}
        for name, symbol in INDICES.items():
            try:
                indices[name] = analyze_index(name, symbol, api_key)
            except Exception as e:
                print(f"WARNUNG: Index-Abruf fehlgeschlagen bei {name} ({symbol}): {e}", file=sys.stderr)

    krise_erkannt = any(i.get("in_korrektur") for i in indices.values())

    output = {
        "erzeugt_am": datetime.now().isoformat(timespec="seconds"),
        "modus": "dry-run" if dry_run else "live",
        "krise_schwelle_pct": KRISE_SCHWELLE_PCT,
        "lookback_tage": LOOKBACK_TAGE,
        "krise_erkannt": krise_erkannt,
        "indizes": indices,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Marktueberblick geschrieben nach {out_path}", file=sys.stderr)
    else:
        print(text)
    return output


def main():
    parser = argparse.ArgumentParser(description="Indexstaende holen und Krisen-/Korrektur-Status erkennen")
    parser.add_argument("--dry-run", action="store_true", help="Mock-Daten statt echter API-Calls")
    parser.add_argument("--out", help="Ergebnis als JSON in diese Datei schreiben statt nach stdout")
    args = parser.parse_args()
    run(dry_run=args.dry_run, out_path=args.out)


if __name__ == "__main__":
    main()
