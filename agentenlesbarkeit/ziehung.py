"""Ziehung und Sichtung (Präregistrierung, Abschnitt Grundgesamtheit und Stichprobe).

1. ziehe(): Verzeichnisse vereinigen, nach Domain deduplizieren, auf Deutschland und
   Schweiz beschränken, mit veröffentlichtem Startwert zufällig ordnen. Ausgegeben werden
   die interne Sichtungsliste und das Ziehungsprotokoll mit SHA-256 der Domainliste.
2. stand(): liest die ausgefüllte Sichtungsliste in Rangfolge, füllt je Geschäftsmodell
   100 Plätze und schätzt aus allen bis zum Stopp gesichteten Domains die Schichtanteile.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from . import konfig

SCHICHTEN = ("serie", "produkt", "projekt")
JE_SCHICHT = 100
LAENDER = {"de": "DE", "deutschland": "DE", "germany": "DE",
           "ch": "CH", "schweiz": "CH", "switzerland": "CH", "suisse": "CH", "svizzera": "CH"}
SPALTEN_DOMAIN = ("domain", "website", "webseite", "url", "homepage", "internet")
SPALTEN_LAND = ("land", "country", "staat")
SPALTEN_NAME = ("name", "firma", "unternehmen", "company")


def normalisiere_domain(wert: str) -> str:
    wert = (wert or "").strip()
    if not wert:
        return ""
    if "://" not in wert:
        wert = "http://" + wert
    host = (urlsplit(wert).hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]{2,}", host.encode("idna").decode("ascii")
                        if host else ""):
        return ""
    return host


def _spalte(kopf: list[str], namen: tuple[str, ...]) -> str | None:
    for k in kopf:
        if k.strip().lower() in namen:
            return k
    return None


def lese_rahmen(dateien: list[str]) -> tuple[dict[str, dict], dict]:
    eintraege: dict[str, dict] = {}
    zaehler: dict[str, dict] = {}
    for datei in dateien:
        quelle = Path(datei).stem
        z = zaehler.setdefault(quelle, {"zeilen": 0, "ohne_domain": 0, "ausserhalb_de_ch": 0})
        with open(datei, encoding="utf-8-sig", newline="") as f:
            leser = csv.DictReader(f)
            kopf = leser.fieldnames or []
            sp_d, sp_l, sp_n = (_spalte(kopf, SPALTEN_DOMAIN), _spalte(kopf, SPALTEN_LAND),
                                _spalte(kopf, SPALTEN_NAME))
            if sp_d is None:
                raise SystemExit(f"{datei}: keine Spalte für die Website gefunden ({', '.join(SPALTEN_DOMAIN)})")
            for zeile in leser:
                z["zeilen"] += 1
                d = normalisiere_domain(zeile.get(sp_d, ""))
                if not d:
                    z["ohne_domain"] += 1
                    continue
                land = LAENDER.get((zeile.get(sp_l) or "").strip().lower(), "") if sp_l else ""
                if sp_l and land not in ("DE", "CH"):
                    z["ausserhalb_de_ch"] += 1
                    continue
                host = (urlsplit(zeile.get(sp_d, "") if "://" in zeile.get(sp_d, "")
                                 else "http://" + zeile.get(sp_d, "").strip()).hostname or d).strip(".").lower()
                e = eintraege.setdefault(d, {"domain": d, "host": host, "quellen": set(), "land": land,
                                             "name": (zeile.get(sp_n) or "").strip() if sp_n else ""})
                e["quellen"].add(quelle)
                e["land"] = e["land"] or land
    return eintraege, zaehler


def domain_hash(domains: list[str]) -> str:
    daten = "".join(d + "\n" for d in sorted(domains)).encode("utf-8")
    return hashlib.sha256(daten).hexdigest()


def ziehe(dateien: list[str], startwert: int, ausgabe: str) -> dict:
    eintraege, zaehler = lese_rahmen(dateien)
    domains = sorted(eintraege)
    reihenfolge = domains[:]
    random.Random(startwert).shuffle(reihenfolge)
    ziel = Path(ausgabe)
    ziel.mkdir(parents=True, exist_ok=True)
    with open(ziel / "sichtungsliste.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rang", "domain", "host", "quellen", "land", "name",
                    "geschaeftsmodell", "groesse", "ausschluss", "notiz"])
        for rang, d in enumerate(reihenfolge, 1):
            e = eintraege[d]
            w.writerow([rang, d, e["host"], "+".join(sorted(e["quellen"])), e["land"], e["name"],
                        "", "", "", ""])
    protokoll = {
        "fassung": konfig.FASSUNG,
        "datum": date.today().isoformat(),
        "startwert": startwert,
        "verfahren": "Vereinigung der Verzeichnisse, Domain normalisiert (Kleinschreibung, ohne www., "
                     "ohne Pfad), dedupliziert, Sitz DE oder CH; sortiert und mit Python random.Random("
                     "startwert).shuffle geordnet; Sichtung in dieser Reihenfolge.",
        "quellen": zaehler,
        "domains": len(domains),
        "sha256_domainliste": domain_hash(domains),
        "hash_regel": "SHA-256 über die sortierten Domains, je Domain eine Zeile mit LF, UTF-8",
    }
    (ziel / "ziehungsprotokoll.json").write_text(json.dumps(protokoll, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    return protokoll


def stand(sichtungsliste: str, ausgabe: str | None = None) -> dict:
    with open(sichtungsliste, encoding="utf-8", newline="") as f:
        zeilen = sorted(csv.DictReader(f), key=lambda z: int(z["rang"]))
    plaetze: dict[str, list[dict]] = {s: [] for s in SCHICHTEN}
    gesichtet = {s: 0 for s in SCHICHTEN}
    ausgeschlossen: dict[str, int] = {}
    stopp = None
    luecke = None
    for z in zeilen:
        gm = z["geschaeftsmodell"].strip().lower()
        aus = z["ausschluss"].strip().lower()
        if not gm and not aus:
            luecke = int(z["rang"])
            break
        if aus:
            ausgeschlossen[aus] = ausgeschlossen.get(aus, 0) + 1
            continue
        if gm not in SCHICHTEN:
            raise SystemExit(f"Rang {z['rang']}: Geschäftsmodell '{gm}' unbekannt ({', '.join(SCHICHTEN)})")
        gesichtet[gm] += 1
        if len(plaetze[gm]) < JE_SCHICHT:
            plaetze[gm].append(z)
        if all(len(p) >= JE_SCHICHT for p in plaetze.values()):
            stopp = int(z["rang"])
            break
    summe = sum(gesichtet.values())
    anteile = {s: (gesichtet[s] / summe if summe else None) for s in SCHICHTEN}
    ergebnis = {
        "voll": stopp is not None,
        "stopp_rang": stopp,
        "erste_luecke_rang": luecke,
        "plaetze": {s: len(p) for s, p in plaetze.items()},
        "gesichtet_geeignet": gesichtet,
        "ausgeschlossen": ausgeschlossen,
        "schichtanteile": anteile,
        "gewichte": {s: (anteile[s] * len(SCHICHTEN) if anteile[s] is not None else None) for s in SCHICHTEN},
    }
    if ausgabe and stopp is not None:
        with open(ausgabe, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["domain", "host", "schicht", "rang", "land", "groesse"])
            for s in SCHICHTEN:
                for z in plaetze[s]:
                    w.writerow([z["domain"], z.get("host") or z["domain"], s, z["rang"], z["land"], z["groesse"]])
    return ergebnis
