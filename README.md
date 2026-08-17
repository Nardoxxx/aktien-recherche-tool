# Aktien Recherche Tool

Persoenliches, automatisiertes Aktien-Recherche-Tool fuer Leonardo: taeglich/woechentlich
US- und EU-Aktien aus `watchlist.yaml` mathematisch/analytisch auswerten (Score 0-100,
transparent hergeleitet), Ergebnis als Markdown-Reports in `reports/` ablegen. Ein lokaler
Sync-Job auf Leonardos Rechner zieht `reports/` regelmaessig in sein Obsidian-Vault.

## Kontext fuer den Cloud-Agenten (wichtig, jeder Lauf startet ohne Vorwissen!)

Dieses Tool laeuft OHNE bezahltes Datenabo (EODHD Free-Plan, siehe `EODHD_API_KEY` als env var).
Der Free-Plan blockiert `/fundamentals`, `/calendar/earnings` und `/screener` ("Only EOD data
allowed for free users"), erlaubt aber `/real-time` und `/eod` (auch fuer Indizes). Deshalb:

- **Kurse/Indexstaende:** deterministisch per Skript (`analyse.py`, `market_overview.py`) - kostenlos.
- **Fundamentaldaten** (KGV, PEG, Wachstum, Margen, ROE, Analysten-Kursziel, naechster Earnings-Termin):
  MUSST DU per WebSearch/WebFetch recherchieren (z.B. stockanalysis.com fuer US-Titel, WebSearch fuer
  EU-/OTC-Titel wo WebFetch oft blockiert/duenn ist - siehe Erfahrungswerte unten). Nicht raten, fehlende
  Werte bleiben `null`.
- **Waehrungs-Vorsicht:** Analysten-Kursziele nur uebernehmen, wenn die Waehrung der Quelle zur
  EODHD-Heimatboerse passt (z.B. NICHT ein USD-ADR-Kursziel gegen einen EUR/DKK/CHF-Heimatkurs
  verrechnen) - bei Mismatch das Feld leer lassen statt falsch zu rechnen.
- **Screener** (`screener.py`) ist auf dem Free-Plan nicht nutzbar, Abschnitt im Report leer lassen
  mit Hinweis "pausiert (Free-Plan)".

## Ablauf WERKTAGS (leichter Lauf, kein WebFetch noetig)

```bash
python3 script/analyse.py --refresh-prices --in script/letzte_recherche.json --out script/letzte_recherche.json
python3 script/analyse.py --score-only --in script/letzte_recherche.json --out script/letzte_recherche.json
python3 script/market_overview.py --out script/market.json
python3 script/dashboard_notes.py --in script/letzte_recherche.json --out-dir reports/titel
python3 script/report_template.py --in script/letzte_recherche.json --type daily --out "reports/YYYY-MM-DD.md"
```
Fuer den letzten Schritt (Dashboard): falls `script/portfolio.json` bereits existiert (aus einem
frueheren Wochenend-Lauf), mit einbeziehen, sonst ohne `--portfolio`:
```bash
python3 script/html_dashboard.py --in script/letzte_recherche.json --market script/market.json \
  $( [ -f script/portfolio.json ] && echo "--portfolio script/portfolio.json" ) \
  --out reports/Dashboard.html
```
Danach: Markt-/News-Einordnung fuer Titel mit auffaelliger Kursbewegung (>5%) per WebSearch ergaenzen,
alle `<!-- AGENT: ... -->`-Platzhalter im generierten Report durch echten Text ersetzen (siehe
`report_template.py`-Docstring fuer die Bedeutung jedes Platzhalters). Bei `market.json` mit
`krise_erkannt: true`: neue Zeile in `reports/Krisen-Log.md` ergaenzen (Format siehe Datei-Kopf).
Alles committen und pushen.

## Ablauf WOCHENENDE (Tiefenrecherche, WebFetch-intensiv)

Wie oben, aber OHNE `--refresh-prices` - stattdessen volle Neu-Recherche: `python3 script/analyse.py
--out script/letzte_recherche.json` (holt Kurse fuer alle Titel neu), dann fuer JEDEN der 31 Titel
Fundamentaldaten per WebSearch/WebFetch recherchieren und ins JSON eintragen (Feldnamen siehe
`analyse.py` `extract_metrics()`), dann `--score-only`, `--type weekly`. Zusaetzlich:
`python3 script/portfolio_split.py --in script/letzte_recherche.json --sparplan "300,1000"
--monatsrate 300 --out script/portfolio.json` ausfuehren (Leonardos echter Plan: ~300 EUR
Starteinzahlung, ~1.000 EUR Kapital Ende des Monats, danach ~300 EUR/Monat laufend - bei Aenderung
des Plans die Zahlen hier und im Skript-Aufruf anpassen) und daraus `reports/Portfolio-Vorschlag.md`
bauen (Format/Ton siehe die bereits im Repo liegende Version als Vorlage - Sparplan-Tabelle mit
Spalten fuer alle drei Kapital-Punkte, Hinweis dass sich bei so kleinen Betraegen pro Position ein
echter monatlicher Sparplan beim Broker eher eignet als Einzelkaeufe). Danach in
`reports/Performance-Tracking.md` fuer jeden Titel eine neue Log-Zeile ergaenzen (Datum, Score, Kurs).
Zum Schluss: `python3 script/html_dashboard.py --in script/letzte_recherche.json --market
script/market.json --portfolio reports/portfolio.json --out reports/Dashboard.html` (das
selbst-enthaltene HTML-Dashboard mit Charts, wird lokal im Browser geoeffnet, nicht in Obsidian).

## Score-Formel

Siehe Docstring in `script/analyse.py` - Wachstum 30%, Bewertung 25%, Qualitaet 20%, Momentum 15%,
Analysten-Konsens 10%, jeweils Perzentil-normiert innerhalb der Watchlist.

## Watchlist

`watchlist.yaml` - 31 Titel, editierbar. Cluster `chip-lieferkette-nvidia` und `ruestung` sind bewusste
thematische Schwerpunkte.

## Wichtig

- Niemals den Free-Tier-Grenzwert sprengen: max. 20 EODHD-Calls/Tag ausserhalb Kurse/Indizes (die sind
  unlimitiert im Rahmen der 100.000/Tag Free-Kurs-Limits).
- Reports sind rein mathematische Kennzahlen-Einordnung, KEINE Anlageberatung - Disclaimer in jedem
  Report beibehalten (siehe `report_template.py`).
