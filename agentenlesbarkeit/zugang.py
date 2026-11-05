"""Zugangsmessung je Domain (Codebuch A1, A3, A4, A5; Hypothesen H1 und H4).

Modi:
  vollerhebung  robots.txt und Startseite, primäre Bedingung (Präregistrierung: Vollerhebung Zugang)
  stichprobe    zusätzlich TDM-Datei, llms.txt, ai.txt und, falls gewünscht, Chromium
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import konfig
from .abruf import Abrufer, Antwort, Renderer, tdm_aus_html, tdm_aus_json


def _text(a: Antwort) -> str:
    return a.koerper.decode("utf-8", errors="replace")


def _tdm_in_antwort(a: Antwort) -> bool:
    return a.header_wert("tdm-reservation").strip() == "1" or tdm_aus_html(_text(a))


async def pruefe_domain(domain: str, abrufer: Abrufer, renderer: Renderer | None,
                        modus: str, versuch: int = 1) -> None:
    extra = {"modus": modus, "versuch": versuch}
    start = f"https://{domain}/"
    tdm = False

    if modus == "stichprobe":
        # Der TDM-Vorbehalt wird bei jedem Versuch neu geprüft, er kann sich ändern.
        a = await abrufer.hole(f"https://{domain}/.well-known/tdmrep.json", "tdmrep", domain,
                               max_bytes=1_000_000, extra=extra)
        if a.status and 200 <= a.status < 300:
            tdm = bool(tdm_aus_json(_text(a), "/"))
    if modus == "stichprobe" and versuch == 1:
        for datei in ("llms.txt", "ai.txt"):
            await abrufer.hole(f"https://{domain}/{datei}", datei.replace(".", "_"), domain,
                               max_bytes=2_000_000, extra=extra)

    def verwerfen(a: Antwort) -> bool:
        return tdm or _tdm_in_antwort(a)

    seite = await abrufer.hole(start, "startseite", domain, extra=extra, verwerfen=verwerfen)
    fehler = seite.fehler.removeprefix("robots_")
    if fehler == "dns" and not domain.startswith("www."):
        # Verzeichnis nennt die Domain ohne www., der Name löst aber nur mit www. auf.
        seite = await abrufer.hole(f"https://www.{domain}/", "startseite", domain,
                                   extra={**extra, "www_rueckfall": True}, verwerfen=verwerfen)
        fehler = seite.fehler.removeprefix("robots_")
    if fehler in ("verbindung", "tls") and not seite.nicht_durchgefuehrt:
        # Ein Agent mit nur dem Domainnamen versucht danach http://. Beides wird gespeichert.
        host = urlsplit(seite.url_final or start).hostname or domain
        seite = await abrufer.hole(f"http://{host}/", "startseite", domain,
                                   extra={**extra, "http_rueckfall": True}, verwerfen=verwerfen)

    if renderer is not None:
        ziel = seite.url_final if (seite.url_final and not seite.nicht_durchgefuehrt) else start
        await renderer.rendere(ziel, "startseite", domain, extra=extra,
                               inhalt_verwerfen=tdm or (seite.status is not None and _tdm_in_antwort(seite)))


async def lauf(domains: list[str], abrufer: Abrufer, renderer: Renderer | None,
               modus: str, parallel: int = 20, versuch: int = 1) -> None:
    sem = asyncio.Semaphore(parallel)

    async def eine(d: str) -> None:
        async with sem:
            try:
                await pruefe_domain(d, abrufer, renderer, modus, versuch)
            except Exception as exc:  # noqa: BLE001 – eine Domain darf den Lauf nicht abbrechen
                abrufer.speicher.index({"domain": d, "art": "lauf_fehler", "fehler": repr(exc)[:500],
                                        "modus": modus, "versuch": versuch})

    await asyncio.gather(*(eine(d) for d in domains))


def faellige_wiederholungen(eintraege: list[dict], client: str, jetzt: datetime | None = None) -> list[str]:
    """Domains, deren Startseite beim ersten Versuch abgewiesen wurde, mindestens
    konfig.WIEDERHOLUNG_H zurückliegt und noch nicht wiederholt ist."""
    jetzt = jetzt or datetime.now(timezone.utc)
    erste: dict[str, datetime] = {}
    wiederholt: set[str] = set()
    for e in eintraege:
        if e.get("art") != "startseite" or e.get("client") != client:
            continue
        if e.get("versuch") == 1 and e.get("ergebnis") == "abgewiesen":
            erste.setdefault(e["domain"], datetime.strptime(e["zeit"], "%Y-%m-%dT%H:%M:%SZ")
                             .replace(tzinfo=timezone.utc))
        if e.get("versuch") == 2:
            wiederholt.add(e["domain"])
    grenze = konfig.WIEDERHOLUNG_H * 3600
    return sorted(d for d, t in erste.items()
                  if d not in wiederholt and (jetzt - t).total_seconds() >= grenze)
