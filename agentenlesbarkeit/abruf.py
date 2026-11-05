"""Abruf: einfacher HTTP-Client (roh) und Chromium (gerendert), robots-konform und gedrosselt.

Regeln (Präregistrierung, Abschnitt Erhebung):
- Jede Anfrage trägt die deklarierte Kennung; kein Vortäuschen eines Browsers oder Bots.
- Vor jeder Anfrage wird die robots.txt des Ziel-Hosts geprüft, auch bei Weiterleitungen.
- Höchstens eine Anfrage je Host im Abstand konfig.ABSTAND_S.
- Sperren werden nicht umgangen. Eine Abweisung wird gespeichert, nicht wiederholt
  (die Wiederholung nach 24 Stunden steuert der Aufrufer).
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from . import konfig, robots, schutz, signatur
from .drossel import Drossel
from .speicher import Speicher

ANFRAGE_HEADER = {
    "User-Agent": konfig.USER_AGENT,
    "From": konfig.KONTAKT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


def herkunft(url: str) -> str:
    t = urlsplit(url)
    port = t.port or (443 if t.scheme == "https" else 80)
    return f"{t.scheme}://{(t.hostname or '').lower()}:{port}"


@dataclass
class Antwort:
    url: str
    url_final: str = ""
    status: int | None = None
    grund: str = ""
    header: list[tuple[str, str]] = field(default_factory=list)
    koerper: bytes = b""
    fehler: str = ""                 # zeitueberschreitung, dns, tls, verbindung, weiterleitungen, protokoll
    nicht_durchgefuehrt: bool = False
    abgeschnitten: bool = False
    kette: list[tuple[str, int]] = field(default_factory=list)
    record_id: str = ""

    def header_wert(self, name: str) -> str:
        name = name.lower()
        return ", ".join(v for k, v in self.header if k.lower() == name)


def fehlerart(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "zeitueberschreitung"
    text = repr(exc).lower()
    if isinstance(exc, ssl.SSLError) or "ssl" in text or "certificate" in text:
        return "tls"
    if any(s in text for s in ("name or service not known", "nodename", "getaddrinfo",
                               "temporary failure in name resolution", "no address associated")):
        return "dns"
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "verbindung"
    return "protokoll"


class Abrufer:
    """Einfacher HTTP-Client mit robots-Prüfung, Drossel und WARC-Speicherung."""

    def __init__(self, speicher: Speicher, drossel: Drossel, netz: str,
                 transport: httpx.AsyncBaseTransport | None = None, signatur_schluessel=None) -> None:
        """signatur_schluessel: Ed25519-Schlüssel für den explorativen Arm roh_signiert, sonst None."""
        self.speicher = speicher
        self.signatur_schluessel = signatur_schluessel
        self.client_name = "roh_signiert" if signatur_schluessel is not None else "roh"
        self.drossel = drossel
        self.netz = netz
        self._transport = transport
        self._robots: dict[str, robots.Robots] = {}
        self._robots_sperre: dict[str, asyncio.Lock] = {}
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Abrufer":
        self.client = httpx.AsyncClient(
            headers=ANFRAGE_HEADER, follow_redirects=False, transport=self._transport,
            timeout=httpx.Timeout(konfig.TIMEOUT_GESAMT_S, connect=konfig.TIMEOUT_VERBINDUNG_S))
        return self

    async def __aexit__(self, *a) -> None:
        if self.client:
            await self.client.aclose()

    # --- Einzelanfrage --------------------------------------------------------

    async def _anfrage(self, url: str, max_bytes: int) -> Antwort:
        assert self.client is not None
        await self.drossel.warte(url)
        a = Antwort(url=url, url_final=url)
        zusatz = signatur.signiere(url, self.signatur_schluessel) if self.signatur_schluessel else None
        try:
            async with self.client.stream("GET", url, headers=zusatz) as r:
                a.status, a.grund = r.status_code, r.reason_phrase
                a.header = list(r.headers.multi_items())
                teile, groesse = [], 0
                async for stueck in r.aiter_bytes():
                    groesse += len(stueck)
                    if groesse > max_bytes:
                        teile.append(stueck[: max(0, len(stueck) - (groesse - max_bytes))])
                        a.abgeschnitten = True
                        break
                    teile.append(stueck)
                a.koerper = b"".join(teile)
        except Exception as exc:  # noqa: BLE001 – jede Fehlerart wird als Beobachtung notiert
            a.fehler = fehlerart(exc)
        return a

    def _speichere(self, a: Antwort, art: str, domain: str, extra: dict | None = None,
                   koerper_verwerfen: bool = False) -> None:
        header = {k.lower(): v for k, v in a.header}
        marker = schutz.challenge(a.status, header, a.koerper.decode("utf-8", errors="replace")) \
            if a.status is not None else ""
        if a.status is not None:
            a.record_id = self.speicher.http(
                a.url, "GET", ANFRAGE_HEADER, a.status, a.grund, a.header,
                b"" if koerper_verwerfen else a.koerper)
        self.speicher.index({
            "domain": domain, "art": art, "client": self.client_name, "netz": self.netz,
            "url": a.url, "url_final": a.url_final, "status": a.status, "fehler": a.fehler,
            "nicht_durchgefuehrt": a.nicht_durchgefuehrt, "abgeschnitten": a.abgeschnitten,
            "kette": a.kette, "record_id": a.record_id,
            "header": header, "koerper_verworfen": koerper_verwerfen,
            "challenge": marker, "schutz": schutz.anbieter(header),
            "ergebnis": schutz.ergebnis(a.status, a.fehler, a.nicht_durchgefuehrt, marker),
            **(extra or {}),
        })

    # --- robots.txt -----------------------------------------------------------

    async def robots_fuer(self, url: str, domain: str) -> robots.Robots:
        schluessel = herkunft(url)
        sperre = self._robots_sperre.setdefault(schluessel, asyncio.Lock())
        async with sperre:
            if schluessel in self._robots:
                return self._robots[schluessel]
            t = urlsplit(url)
            ziel = f"{t.scheme}://{t.netloc}/robots.txt"
            kette: list[tuple[str, int]] = []
            a = Antwort(url=ziel)
            for _ in range(konfig.MAX_WEITERLEITUNGEN + 1):
                a = await self._anfrage(ziel, konfig.MAX_ROBOTS_BYTES)
                if a.status and 300 <= a.status < 400 and a.header_wert("location"):
                    kette.append((ziel, a.status))
                    ziel = urljoin(ziel, a.header_wert("location"))
                    continue
                break
            a.kette, a.url_final = kette, ziel
            if a.fehler or a.status is None:
                # Nicht erreichbar: alles gilt als gesperrt (RFC 9309, 2.3.1.4).
                rb = robots.unerreichbar(a.fehler or "verbindung")
            elif a.status == 429 or a.status >= 500:
                # Serverfehler: alles gilt als gesperrt; 429 wird vorsichtshalber ebenso behandelt.
                marker = schutz.challenge(a.status, {k.lower(): v for k, v in a.header},
                                          a.koerper.decode("utf-8", errors="replace"))
                rb = robots.serverfehler(abgewiesen=bool(marker) or a.status in (429, 503))
            elif 300 <= a.status < 400:
                # Mehr als fünf Weiterleitungen oder Weiterleitung ohne Ziel: wie nicht vorhanden.
                rb = robots.fehlt()
            elif 400 <= a.status < 500:
                rb = robots.fehlt()
            else:
                rb = robots.parse(a.koerper.decode("utf-8", errors="replace"))
                verzoegerung = rb.crawl_delay(konfig.PRODUKT)
                if verzoegerung:
                    self.drossel.setze_mindestabstand(url, verzoegerung)
            self._speichere(a, "robots", domain, {"robots_zustand": rb.zustand,
                                                  "robots_herkunft": schluessel})
            self._robots[schluessel] = rb
            return rb

    # --- Seite mit Weiterleitungen --------------------------------------------

    async def hole(self, url: str, art: str, domain: str, max_bytes: int = konfig.MAX_HTML_BYTES,
                   extra: dict | None = None, verwerfen=None) -> Antwort:
        """Holt url mit Weiterleitungen. verwerfen(antwort) -> True: Körper wird nicht
        gespeichert (TDM-Vorbehalt); Status, Header und Klassifikation bleiben erhalten."""
        kette: list[tuple[str, int]] = []
        ziel = url
        for _ in range(konfig.MAX_WEITERLEITUNGEN + 1):
            rb = await self.robots_fuer(ziel, domain)
            if not rb.erlaubt(konfig.PRODUKT, ziel):
                if rb.zustand == "unerreichbar":
                    # Kein Zugriffsvorbehalt, sondern Host nicht erreichbar: technischer Fehlschlag.
                    a = Antwort(url=url, url_final=ziel, fehler=f"robots_{rb.fehler}", kette=kette)
                else:
                    a = Antwort(url=url, url_final=ziel, nicht_durchgefuehrt=True, kette=kette)
                grund = {"robots_zustand": rb.zustand, "robots_abgewiesen": rb.abgewiesen}
                self._speichere(a, art, domain, {**(extra or {}), **grund})
                return a
            a = await self._anfrage(ziel, max_bytes)
            if a.status and 300 <= a.status < 400 and a.header_wert("location"):
                # Zwischenschritte werden mitgespeichert, damit die Kette nachprüfbar ist.
                self._speichere(a, art + "_weiterleitung", domain, extra)
                kette.append((ziel, a.status))
                ziel = urljoin(ziel, a.header_wert("location"))
                continue
            a.url, a.url_final, a.kette = url, ziel, kette
            weg = bool(verwerfen and a.status is not None and verwerfen(a))
            self._speichere(a, art, domain, {**(extra or {}), "tdm_vorbehalt": weg} if weg else extra, weg)
            return a
        a = Antwort(url=url, url_final=ziel, fehler="weiterleitungen", kette=kette)
        self._speichere(a, art, domain, extra)
        return a


# --- TDM Reservation Protocol ------------------------------------------------

def tdm_aus_json(text: str, pfad: str = "/") -> bool | None:
    """Wertet /.well-known/tdmrep.json aus. True = Vorbehalt für pfad, None = keine Angabe."""
    try:
        daten = json.loads(text)
    except ValueError:
        return None
    if not isinstance(daten, list):
        return None
    ergebnis = None
    for regel in daten:
        if not isinstance(regel, dict):
            continue
        ort = str(regel.get("location", ""))
        muster = "^" + re.escape(ort).replace(r"\*", ".*") + "$"
        if re.match(muster, pfad) or (ort.endswith("*") and pfad.startswith(ort[:-1])):
            ergebnis = str(regel.get("tdm-reservation", "0")) == "1"
    return ergebnis


def tdm_aus_html(html: str) -> bool:
    return bool(re.search(r'<meta[^>]+name=["\']tdm-reservation["\'][^>]+content=["\']1["\']', html, re.I)
                or re.search(r'<meta[^>]+content=["\']1["\'][^>]+name=["\']tdm-reservation["\']', html, re.I))


# --- Gerenderter Abruf -----------------------------------------------------------

class Renderer:
    """Chromium über Playwright, mit deklarierter Kennung. Bilder, Medien, Schriften gesperrt."""

    def __init__(self, speicher: Speicher, drossel: Drossel, netz: str, robots_quelle: Abrufer) -> None:
        self.speicher = speicher
        self.drossel = drossel
        self.netz = netz
        self.robots_quelle = robots_quelle
        self._pw = None
        self._browser = None
        self._kontext = None

    async def __aenter__(self) -> "Renderer":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch()
        self._kontext = await self._browser.new_context(
            user_agent=konfig.USER_AGENT, locale="de-DE",
            extra_http_headers={"From": konfig.KONTAKT})

        async def sperren(route):
            if route.request.resource_type in konfig.GESPERRTE_RESSOURCEN:
                await route.abort()
            else:
                await route.continue_()
        await self._kontext.route("**/*", sperren)
        return self

    async def __aexit__(self, *a) -> None:
        if self._kontext:
            await self._kontext.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def rendere(self, url: str, art: str, domain: str, extra: dict | None = None,
                      inhalt_verwerfen: bool = False) -> dict:
        from playwright.async_api import Error as PwFehler
        rb = await self.robots_quelle.robots_fuer(url, domain)
        eintrag: dict = {"domain": domain, "art": art, "client": "chromium", "netz": self.netz,
                         "url": url, "url_final": url, "status": None, "fehler": "",
                         "nicht_durchgefuehrt": False, "record_id": "", "header": {},
                         "erster_status": None, "titel": "", **(extra or {})}
        if not rb.erlaubt(konfig.PRODUKT, url):
            eintrag["nicht_durchgefuehrt"] = True
            eintrag["ergebnis"] = "nicht_durchgefuehrt"
            self.speicher.index(eintrag)
            return eintrag
        await self.drossel.warte(url)
        seite = await self._kontext.new_page()
        antworten: list = []
        seite.on("response", lambda r: antworten.append(r)
                 if r.request.is_navigation_request() and r.frame == seite.main_frame else None)
        try:
            erste = await seite.goto(url, wait_until="load", timeout=konfig.TIMEOUT_GESAMT_S * 1000)
            try:
                await seite.wait_for_load_state("networkidle", timeout=konfig.RENDER_WARTEN_MS)
            except PwFehler:
                pass
            if erste is not None:
                eintrag["erster_status"] = erste.status
            letzte = antworten[-1] if antworten else erste
            if letzte is not None:
                eintrag["status"] = letzte.status
                eintrag["header"] = {k.lower(): v for k, v in (await letzte.all_headers()).items()}
            eintrag["url_final"] = seite.url
            eintrag["titel"] = (await seite.title())[:300]
            dom = await seite.content()
            eintrag["challenge"] = schutz.challenge(eintrag["status"], eintrag["header"], dom)
            eintrag["challenge_bestanden"] = (not eintrag["challenge"]
                                              and eintrag["erster_status"] in konfig.ABWEISUNG_STATUS
                                              and eintrag["status"] is not None
                                              and 200 <= eintrag["status"] < 300)
            ziel_rb = await self.robots_quelle.robots_fuer(seite.url, domain)
            if not ziel_rb.erlaubt(konfig.PRODUKT, seite.url):
                # Weiterleitung auf einen gesperrten Pfad: Ergebnis wird verworfen.
                eintrag["nicht_durchgefuehrt"] = True
                dom = ""
            if inhalt_verwerfen or tdm_aus_html(dom):
                eintrag["tdm_vorbehalt"] = True
                inhalt_verwerfen = True
                dom = ""
            eintrag["record_id"] = self.speicher.gerendert(
                seite.url, dom, {"start": url, "status": eintrag["status"],
                                 "erster_status": eintrag["erster_status"]})
            eintrag["inhalt_verworfen"] = inhalt_verwerfen
        except PwFehler as exc:
            text = str(exc).lower()
            if "timeout" in text:
                eintrag["fehler"] = "zeitueberschreitung"
            elif "err_name_not_resolved" in text:
                eintrag["fehler"] = "dns"
            elif "err_cert" in text or "ssl" in text:
                eintrag["fehler"] = "tls"
            else:
                eintrag["fehler"] = "verbindung"
        finally:
            await seite.close()
        eintrag["schutz"] = schutz.anbieter(eintrag["header"])
        eintrag["ergebnis"] = schutz.ergebnis(eintrag["status"], eintrag["fehler"],
                                              eintrag["nicht_durchgefuehrt"], eintrag.get("challenge", ""))
        self.speicher.index(eintrag)
        return eintrag
