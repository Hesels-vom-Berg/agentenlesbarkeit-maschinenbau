# Agentenlesbarkeit im Maschinenbau

Abruf- und Auswertungsskripte der Studie **Agentenlesbarkeit im Maschinenbau 2027**. Hypothesen, Schwellen, Prognosen, Codebuch und Abrufregeln stehen in der Präregistrierung: <https://leitstand.one/studie> (English: <https://leitstand.one/en/study>). Dieses Repository setzt die Fassung 1.0 um. Werte, die die Präregistrierung festlegt, stehen gesammelt in [`agentenlesbarkeit/konfig.py`](agentenlesbarkeit/konfig.py).

Veröffentlicht werden hier Code, Codebuch und später der aggregierte Datensatz. Domainlisten, Sichtung und Rohantworten bleiben intern (siehe [Datenhaltung](#datenhaltung)).

## Regeln des Agenten

- Kennung `leitstand-studie/1.0 (+https://leitstand.one/studie)` und Kopfzeile `From: studie@leitstand.one`, auch im gerenderten Abruf. Es wird nie eine Browser- oder Bot-Kennung vorgetäuscht.
- Vor jeder Anfrage wird die robots.txt des Ziel-Hosts nach RFC 9309 geprüft, auch nach jeder Weiterleitung. Ausgewertet wird sie mit [`robots.py`](agentenlesbarkeit/robots.py), ohne Fremdbibliothek.
  - 4xx bedeutet „fehlt“: alles ist frei.
  - 5xx und 429 bedeuten „Serverfehler“: alles gilt als gesperrt.
  - Ein Netzfehler bedeutet „unerreichbar“: alles gilt als gesperrt und wird als technischer Fehlschlag notiert.
- Höchstens eine Anfrage je Host alle 5 Sekunden ([`drossel.py`](agentenlesbarkeit/drossel.py)). Ein größerer `Crawl-delay` in der robots.txt wird eingehalten.
- Vorbehalte nach dem TDM Reservation Protocol werden respektiert: Datei, Header oder Meta-Element. Bei einem Vorbehalt werden nur Status und Header gespeichert, der Inhalt wird verworfen.
- Challenges und Sperren werden nicht umgangen, sondern als Abweisung notiert. Eine Abweisung wird frühestens nach 24 Stunden einmal wiederholt.
- Chromium lädt keine Bilder, Medien und Schriften.

## Installation

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
python -m playwright install chromium
pytest -q
```

## Ablauf

Alle Ausgaben gehören nach `intern/`. Das Verzeichnis ist von Git ausgeschlossen.

```bash
# 1. Ziehung: Verzeichnisse vereinigen, deduplizieren, auf DE/CH beschränken, ordnen
python -m agentenlesbarkeit ziehung --rahmen intern/rahmen_vdma.csv intern/rahmen_swissmem.csv \
    --startwert <veröffentlichter Startwert> --aus intern/ziehung
#    -> sichtungsliste.csv (intern) und ziehungsprotokoll.json (SHA-256 der Domainliste, veröffentlichen)

# 2. Sichtung von Hand in Rangfolge: Spalte geschaeftsmodell (serie, produkt, projekt) oder
#    ausschluss (haendler, holding, nicht_erreichbar, auf_wunsch, ...); dann Stand und Stichprobe
python -m agentenlesbarkeit sichtung --liste intern/ziehung/sichtungsliste.csv \
    --stichprobe intern/ziehung/stichprobe.csv

# 3. Vollerhebung Zugang (H1, H4): robots.txt und Startseite, primäre Bedingung
python -m agentenlesbarkeit zugang --domains intern/ziehung/sichtungsliste.csv \
    --modus vollerhebung --netz rechenzentrum --client roh --aus intern/laeufe/voll_rz

# 4. Stichprobe: TDM-Datei, llms.txt, ai.txt, roh und Chromium, in beiden Netzen
python -m agentenlesbarkeit zugang --domains intern/ziehung/stichprobe.csv --modus stichprobe \
    --netz rechenzentrum --client roh,chromium --aus intern/laeufe/stichprobe_rz
python -m agentenlesbarkeit zugang --domains intern/ziehung/stichprobe.csv --modus stichprobe \
    --netz geschaeftsanschluss --client roh,chromium --aus intern/laeufe/stichprobe_ga

# 5. Nach frühestens 24 Stunden: Abweisungen einmal wiederholen (je Lauf)
python -m agentenlesbarkeit wiederholung --aus intern/laeufe/voll_rz --netz rechenzentrum --client roh

# 6. Merkmale A1–A5 aus den gespeicherten Läufen
python -m agentenlesbarkeit merkmale-a --lauf intern/laeufe/stichprobe_rz intern/laeufe/stichprobe_ga \
    --aus intern/merkmale/a_stichprobe.csv
```

Für den Pretest an 20 Domains außerhalb der Stichprobe reicht eine Textdatei mit einer Domain je Zeile.

## Module und Codebuch

| Modul | Codebuch | Hypothese |
|---|---|---|
| `robots.py` | A1 robots.txt, A2 KI-Regel (Typ, Herkunft), Content Signal | H1 |
| `abruf.py`, `schutz.py` | A4 Abrufbarkeit, Schutzanbieter, Challenge | H4 |
| `zugang.py` | Ablauf je Domain, TDM-Vorbehalt, Wiederholung | H1, H4 |
| `merkmale_a.py` | A1–A5 je Domain, A5 Anteil ohne JavaScript | H1, H2, H4 |
| `ziehung.py` | Ziehung, Hash, Sichtung, Schichtanteile, Gewichte | alle |

Noch nicht enthalten: Seitenauswahl aus der Sitemap, Bereich B (Kenngrößen, PDF, Texterkennung, strukturierte Daten), der Codierbogen für Bereich C und die Auswertung (Wilson, Newcombe, Cluster-Bootstrap).

## Datenhaltung

- `intern/` und alle Domainlisten, WARC-Dateien und Indizes sind per `.gitignore` ausgeschlossen. Veröffentlicht werden nur der Hash der Domainliste, die Ziehungsregel und aggregierte Anteile.
- Rohantworten liegen als WARC (ISO 28500) vor. Sie können personenbezogene Angaben enthalten, die auf den abgerufenen Seiten stehen. Sie werden nicht ausgewertet, nicht weitergegeben und zwölf Monate nach Veröffentlichung der Ergebnisse gelöscht.
- Alle Merkmale werden aus den gespeicherten Antworten berechnet, nicht live. Jede Auswertung ist damit wiederholbar.

## Lizenz

Code: MIT (siehe `LICENSE`). Aggregierter Datensatz bei Veröffentlichung: CC BY 4.0.

---

**English.** Fetch and analysis scripts for the preregistered study *Agent Readability in Machinery 2027* (<https://leitstand.one/en/study>). The agent identifies as `leitstand-studie/1.0`, follows RFC 9309 for every host and redirect, makes at most one request per host every 5 seconds, respects TDM reservations, and never circumvents blocks. Domain lists and raw responses stay internal; only hashes and aggregate shares are published. Code under MIT, aggregated data under CC BY 4.0.
