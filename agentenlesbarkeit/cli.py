"""Kommandozeile: python -m agentenlesbarkeit <befehl> ...

  ziehung       Verzeichnisse vereinigen, ordnen, Ziehungsprotokoll mit Hash schreiben
  sichtung      Stand der Sichtung, Schichtanteile, Stichprobenliste
  zugang        robots.txt und Startseite abrufen (Vollerhebung oder Stichprobe)
  wiederholung  Abweisungen nach 24 Stunden einmal wiederholen
  merkmale-a    Merkmale A1 bis A5 aus gespeicherten Läufen berechnen
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import konfig, merkmale_a, ziehung, zugang
from .abruf import Abrufer, Renderer
from .drossel import Drossel
from .speicher import Speicher


def _host(wert: str) -> str:
    wert = wert.strip()
    if not wert:
        return ""
    if "://" not in wert:
        wert = "http://" + wert
    return (urlsplit(wert).hostname or "").strip(".").lower()


def _domains(datei: str) -> list[str]:
    """Hosts zum Abruf. Bevorzugt die Spalte host (mit www., wie im Verzeichnis), sonst domain."""
    text = Path(datei).read_text(encoding="utf-8-sig")
    if datei.endswith(".csv"):
        import csv
        zeilen = list(csv.DictReader(text.splitlines()))
        roh = [z.get("host") or z.get("domain", "") for z in zeilen]
    else:
        roh = [z.split("#", 1)[0] for z in text.splitlines()]
    gesehen, liste = set(), []
    for r in roh:
        h = _host(r)
        schluessel = ziehung.normalisiere_domain(h) if h else ""
        if schluessel and schluessel not in gesehen:
            gesehen.add(schluessel)
            liste.append(h)
    return liste


async def _zugang(args, domains: list[str], versuch: int) -> None:
    speicher = Speicher(args.aus)
    drossel = Drossel()
    async with Abrufer(speicher, drossel, args.netz) as abrufer:
        if "chromium" in args.client:
            async with Renderer(speicher, drossel, args.netz, abrufer) as renderer:
                await zugang.lauf(domains, abrufer, renderer, args.modus, args.parallel, versuch)
        else:
            await zugang.lauf(domains, abrufer, None, args.modus, args.parallel, versuch)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentenlesbarkeit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"Fassung {konfig.FASSUNG}")
    u = p.add_subparsers(dest="befehl", required=True)

    z = u.add_parser("ziehung", help="Verzeichnisse vereinigen und ordnen")
    z.add_argument("--rahmen", nargs="+", required=True, help="CSV-Dateien der Verzeichnisse")
    z.add_argument("--startwert", type=int, required=True, help="veröffentlichter Startwert")
    z.add_argument("--aus", required=True, help="Ausgabeverzeichnis (intern, nicht veröffentlichen)")

    s = u.add_parser("sichtung", help="Stand der Sichtung")
    s.add_argument("--liste", required=True, help="ausgefüllte sichtungsliste.csv")
    s.add_argument("--stichprobe", help="Ausgabe der Stichprobenliste, sobald alle Schichten voll sind")

    for name, hilfe in (("zugang", "robots.txt und Startseite abrufen"),
                        ("wiederholung", "Abweisungen nach 24 Stunden wiederholen")):
        g = u.add_parser(name, help=hilfe)
        g.add_argument("--domains", required=(name == "zugang"), help="Textdatei oder CSV mit Spalte domain")
        g.add_argument("--aus", required=True, help="Laufverzeichnis (WARC und Index)")
        g.add_argument("--netz", required=True, choices=["rechenzentrum", "geschaeftsanschluss"])
        g.add_argument("--client", default="roh", help="roh, chromium oder roh,chromium")
        g.add_argument("--modus", default="vollerhebung", choices=["vollerhebung", "stichprobe"])
        g.add_argument("--parallel", type=int, default=20, help="gleichzeitige Domains (Hosts)")

    m = u.add_parser("merkmale-a", help="Merkmale A1 bis A5 berechnen")
    m.add_argument("--lauf", nargs="+", required=True, help="ein oder mehrere Laufverzeichnisse")
    m.add_argument("--aus", required=True, help="CSV-Datei")
    m.add_argument("--primaer-netz", default="rechenzentrum")

    args = p.parse_args(argv)

    if args.befehl == "ziehung":
        prot = ziehung.ziehe(args.rahmen, args.startwert, args.aus)
        print(json.dumps(prot, ensure_ascii=False, indent=2))
    elif args.befehl == "sichtung":
        print(json.dumps(ziehung.stand(args.liste, args.stichprobe), ensure_ascii=False, indent=2))
    elif args.befehl == "zugang":
        domains = _domains(args.domains)
        print(f"{len(domains)} Domains, Netz {args.netz}, Client {args.client}, Modus {args.modus}",
              file=sys.stderr)
        asyncio.run(_zugang(args, domains, versuch=1))
    elif args.befehl == "wiederholung":
        eintraege = Speicher(args.aus).eintraege()
        faellig: set[str] = set()
        for c in args.client.split(","):
            faellig |= set(zugang.faellige_wiederholungen(
                [e for e in eintraege if e.get("netz") == args.netz], c.strip()))
        print(f"{len(faellig)} Domains zur Wiederholung", file=sys.stderr)
        if faellig:
            asyncio.run(_zugang(args, sorted(faellig), versuch=2))
    elif args.befehl == "merkmale-a":
        n = merkmale_a.berechne(args.lauf, args.aus, args.primaer_netz)
        print(f"{n} Domains -> {args.aus}", file=sys.stderr)
    return 0
