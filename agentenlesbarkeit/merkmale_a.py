"""Merkmale aus Bereich A je Domain, berechnet aus gespeicherten Antworten (nicht live).

A1 robots.txt · A2 KI-Regel · A3 Nutzungssignal · A4 Abrufbarkeit je Bedingung · A5 ohne JavaScript
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import lxml.html

from . import robots
from .abruf import herkunft, tdm_aus_json
from .speicher import Speicher

UNSICHTBAR = ("script", "style", "noscript", "template", "svg", "head")
BEDINGUNGEN = [("roh", "rechenzentrum"), ("roh", "geschaeftsanschluss"),
               ("chromium", "rechenzentrum"), ("chromium", "geschaeftsanschluss")]


def sichtbarer_text(html: str) -> str:
    if not html.strip():
        return ""
    try:
        baum = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return ""
    for el in baum.xpath("//" + " | //".join(UNSICHTBAR)):
        el.drop_tree()
    return " ".join(baum.text_content().split())


def woerter(text: str) -> Counter:
    return Counter(w for w in re.findall(r"\w+", text.lower()) if len(w) > 1)


def anteil_ohne_js(roh_html: str, gerendert_html: str) -> float | None:
    """A5: Anteil der Wörter der gerenderten Seite, die schon im Server-HTML stehen."""
    g = woerter(sichtbarer_text(gerendert_html))
    if not g:
        return None
    r = woerter(sichtbarer_text(roh_html))
    gemeinsam = sum(min(n, r[w]) for w, n in g.items())
    return round(gemeinsam / sum(g.values()), 4)


def _ist_textdatei(e: dict, koerper: bytes) -> bool:
    if not e.get("status") or not (200 <= e["status"] < 300):
        return False
    anfang = koerper[:512].lstrip().lower()
    return bool(anfang) and not (anfang.startswith(b"<!doctype") or anfang.startswith(b"<html"))


def _zusammenfassen(versuche: list[dict]) -> str:
    """A4 einer Bedingung: abgewiesen zählt nur, wenn auch die Wiederholung abgewiesen wurde."""
    if not versuche:
        return ""
    v = sorted(versuche, key=lambda e: e.get("versuch", 1))
    erstes = v[0].get("ergebnis", "")
    if erstes != "abgewiesen":
        return erstes
    zweite = [e for e in v if e.get("versuch") == 2]
    if not zweite:
        return "abgewiesen_unbestaetigt"
    return "abgewiesen" if zweite[-1].get("ergebnis") == "abgewiesen" else "uneinheitlich"


def berechne(laeufe: list[str], ausgabe: str, primaer_netz: str = "rechenzentrum") -> int:
    eintraege: list[dict] = []
    speicher: dict[str, Speicher] = {}
    for lauf in laeufe:
        sp = Speicher(lauf)
        for e in sp.eintraege():
            e["_lauf"] = lauf
            eintraege.append(e)
        speicher[lauf] = sp

    benoetigt: dict[str, set[str]] = {}
    for e in eintraege:
        if e.get("record_id"):
            benoetigt.setdefault(e["_lauf"], set()).add(e["record_id"])
    inhalte: dict[str, bytes] = {}
    for lauf, ids in benoetigt.items():
        inhalte.update(speicher[lauf].lese_records(ids))

    domains = sorted({e["domain"] for e in eintraege if e.get("domain")})
    spalten = ["domain", "a1_robots", "a2_ki_regel", "a2_typ", "a2_herkunft", "a2_genannt",
               "a3_content_signal", "a3_tdm_vorbehalt", "a3_llms_txt", "a3_ai_txt"]
    spalten += [f"a4_{c}_{n}" for c, n in BEDINGUNGEN] + [f"a4_schutz_{primaer_netz}",
                                                          "a4_challenge_bestanden_chromium",
                                                          "a4_robots_abgewiesen", "a5_ohne_js"]
    zeilen = []
    for d in domains:
        ed = [e for e in eintraege if e.get("domain") == d]
        z: dict[str, object] = {"domain": d}

        start_roh = [e for e in ed if e.get("art") == "startseite" and e.get("client") == "roh"
                     and e.get("netz") == primaer_netz and e.get("versuch", 1) == 1]
        final = start_roh[-1]["url_final"] if start_roh and start_roh[-1].get("url_final") else f"https://{d}/"
        rob = [e for e in ed if e.get("art") == "robots" and e.get("netz") == primaer_netz]
        passend = [e for e in rob if e.get("robots_herkunft") == herkunft(final)] or rob
        if passend:
            r = passend[0]
            zustand = r.get("robots_zustand", "")
            if zustand == "vorhanden":
                rb = robots.parse(inhalte.get(r.get("record_id", ""), b"").decode("utf-8", errors="replace"))
            else:
                rb = robots.Robots(zustand=zustand)
            typ = robots.ki_regeltyp(rb)
            z.update({"a1_robots": zustand, "a2_ki_regel": int(bool(typ["ki_regel"])), "a2_typ": typ["typ"],
                      "a2_herkunft": typ["herkunft"], "a2_genannt": typ["genannt"]})
            signale = [w for _, w in rb.content_signals]
        else:
            signale = []

        kopf_signal = start_roh[-1].get("header", {}).get("content-signal", "") if start_roh else ""
        z["a3_content_signal"] = int(bool(signale or kopf_signal))
        tdm = any(e.get("tdm_vorbehalt") for e in ed)
        for e in ed:
            if e.get("art") == "tdmrep" and e.get("status") and 200 <= e["status"] < 300:
                tdm = tdm or bool(tdm_aus_json(inhalte.get(e.get("record_id", ""), b"").decode(
                    "utf-8", errors="replace"), "/"))
        z["a3_tdm_vorbehalt"] = int(tdm)
        for art, spalte in (("llms_txt", "a3_llms_txt"), ("ai_txt", "a3_ai_txt")):
            treffer = [e for e in ed if e.get("art") == art]
            z[spalte] = int(any(_ist_textdatei(e, inhalte.get(e.get("record_id", ""), b"")) for e in treffer)) \
                if treffer else ""

        for c, n in BEDINGUNGEN:
            versuche = [e for e in ed if e.get("art") == "startseite" and e.get("client") == c
                        and e.get("netz") == n]
            # Beim http-Rückfall zählt der letzte Abruf des jeweiligen Versuchs.
            je_versuch: dict[int, dict] = {}
            for e in versuche:
                je_versuch[e.get("versuch", 1)] = e
            z[f"a4_{c}_{n}"] = _zusammenfassen(list(je_versuch.values()))
        z[f"a4_schutz_{primaer_netz}"] = start_roh[-1].get("schutz", "") if start_roh else ""
        # robots.txt selbst mit 429, 503 oder Challenge beantwortet: Startseite nicht abgerufen.
        # Getrennt ausgewiesen für die Sensitivitätsrechnung zu H4.
        z["a4_robots_abgewiesen"] = int(bool(start_roh and start_roh[-1].get("robots_abgewiesen")))
        chrom = [e for e in ed if e.get("art") == "startseite" and e.get("client") == "chromium"
                 and e.get("netz") == primaer_netz and e.get("versuch", 1) == 1]
        z["a4_challenge_bestanden_chromium"] = int(bool(chrom and chrom[-1].get("challenge_bestanden"))) \
            if chrom else ""

        if start_roh and chrom and start_roh[-1].get("record_id") and chrom[-1].get("record_id"):
            roh_html = inhalte.get(start_roh[-1]["record_id"], b"").decode("utf-8", errors="replace")
            ger_html = inhalte.get(chrom[-1]["record_id"], b"").decode("utf-8", errors="replace")
            wert = anteil_ohne_js(roh_html, ger_html)
            z["a5_ohne_js"] = "" if wert is None else wert
        else:
            z["a5_ohne_js"] = ""
        zeilen.append(z)

    Path(ausgabe).parent.mkdir(parents=True, exist_ok=True)
    with open(ausgabe, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=spalten, extrasaction="ignore")
        w.writeheader()
        for z in zeilen:
            w.writerow(z)
    return len(zeilen)
