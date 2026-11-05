"""robots.txt nach RFC 9309: Auswertung, Regelabgleich und KI-Regeltyp (Codebuch A1, A2).

Bewusst ohne Fremdbibliothek, damit jede Regel hier nachlesbar ist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from . import konfig


@dataclass
class Regel:
    erlaubt: bool
    muster: str
    zeile: int


@dataclass
class Gruppe:
    agenten: list[str] = field(default_factory=list)   # normalisierte Kennzeichen
    agenten_zeilen: list[int] = field(default_factory=list)
    regeln: list[Regel] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class Robots:
    """Ausgewertete robots.txt.

    zustand: vorhanden · fehlt (4xx, alles frei) · serverfehler (5xx oder 429, alles gesperrt) ·
             unerreichbar (Netzfehler, alles gesperrt)
    abgewiesen: die Anfrage nach robots.txt selbst wurde mit 429, 503 oder Challenge beantwortet.
    """

    zustand: str = "vorhanden"
    abgewiesen: bool = False
    fehler: str = ""
    gruppen: list[Gruppe] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    content_signals: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    verwaltet_zeilen: tuple[int, int] | None = None
    ungueltige_zeilen: int = 0

    # --- Abgleich -----------------------------------------------------------

    def gruppen_fuer(self, produkt: str) -> list[Gruppe]:
        p = normalisiere_agent(produkt)
        treffer = [g for g in self.gruppen if p in g.agenten]
        if treffer:
            return treffer
        return [g for g in self.gruppen if "*" in g.agenten]

    def erlaubt(self, produkt: str, url: str) -> bool:
        if self.zustand == "fehlt":
            return True
        if self.zustand in ("serverfehler", "unerreichbar"):
            return False
        teile = urlsplit(url)
        pfad = teile.path or "/"
        if teile.query:
            pfad += "?" + teile.query
        if pfad == "/robots.txt":
            return True
        regeln = [r for g in self.gruppen_fuer(produkt) for r in g.regeln]
        return entscheide(regeln, pfad)

    def crawl_delay(self, produkt: str) -> float | None:
        """Crawl-delay der für produkt geltenden Gruppen (nicht Teil von RFC 9309, wird aber befolgt)."""
        werte = [g.crawl_delay for g in self.gruppen_fuer(produkt) if g.crawl_delay is not None]
        return max(werte) if werte else None

    # --- KI-Regel (A2) ------------------------------------------------------

    def ki_kennzeichen(self) -> dict[str, list[int]]:
        """Kennzeichen der KI-Liste, die eine Gruppe ausdrücklich nennt, mit Zeilen."""
        gefunden: dict[str, list[int]] = {}
        for g in self.gruppen:
            for a, z in zip(g.agenten, g.agenten_zeilen):
                if a in konfig.KI_LISTE:
                    gefunden.setdefault(a, []).append(z)
        return gefunden

    def ki_signal_zeilen(self) -> list[int]:
        return [z for z, werte in self.content_signals if any(k.startswith("ai-") for k in werte)]


def normalisiere_agent(wert: str) -> str:
    wert = wert.strip().lower()
    if wert == "*":
        return "*"
    return re.split(r"[/\s]", wert, maxsplit=1)[0]


def _normalisiere_pfad(p: str) -> str:
    # Prozentkodierung vereinheitlichen; "/" bleibt Trennzeichen.
    return unquote(p)


def _muster_regex(muster: str) -> re.Pattern[str]:
    ende = muster.endswith("$")
    if ende:
        muster = muster[:-1]
    teile = [re.escape(t) for t in _normalisiere_pfad(muster).split("*")]
    return re.compile("^" + ".*".join(teile) + ("$" if ende else ""), re.S)


def entscheide(regeln: list[Regel], pfad: str) -> bool:
    """Längste passende Regel gewinnt, bei Gleichstand Allow (RFC 9309, 2.2.2)."""
    pfad = _normalisiere_pfad(pfad)
    bester: tuple[int, bool] | None = None
    for r in regeln:
        if not r.muster:
            continue
        if _muster_regex(r.muster).match(pfad):
            laenge = len(r.muster.encode("utf-8"))
            if bester is None or laenge > bester[0] or (laenge == bester[0] and r.erlaubt):
                bester = (laenge, r.erlaubt)
    return True if bester is None else bester[1]


def parse_content_signal(wert: str) -> dict[str, str]:
    werte: dict[str, str] = {}
    for teil in wert.split(","):
        if "=" in teil:
            k, v = teil.split("=", 1)
            werte[k.strip().lower()] = v.strip().lower()
    return werte


def parse(text: str) -> Robots:
    """Wertet den Inhalt einer robots.txt aus (Zustand: vorhanden)."""
    rb = Robots()
    text = text.lstrip("﻿")
    zeilen = text.splitlines()

    beginn = ende = None
    for i, zeile in enumerate(zeilen, 1):
        if beginn is None and re.match(konfig.VERWALTET_BEGINN, zeile, re.I):
            beginn = i
        elif beginn is not None and ende is None and re.match(konfig.VERWALTET_ENDE, zeile, re.I):
            ende = i
    if beginn is not None:
        rb.verwaltet_zeilen = (beginn, ende or len(zeilen))

    aktuell: Gruppe | None = None
    letzte_war_agent = False
    for i, roh in enumerate(zeilen, 1):
        zeile = roh.split("#", 1)[0].strip()
        if not zeile:
            continue
        if ":" not in zeile:
            rb.ungueltige_zeilen += 1
            continue
        schluessel, wert = zeile.split(":", 1)
        schluessel = schluessel.strip().lower()
        wert = wert.strip()
        if schluessel == "user-agent":
            if aktuell is None or not letzte_war_agent:
                aktuell = Gruppe()
                rb.gruppen.append(aktuell)
            aktuell.agenten.append(normalisiere_agent(wert))
            aktuell.agenten_zeilen.append(i)
            letzte_war_agent = True
            continue
        letzte_war_agent = False
        if schluessel in ("allow", "disallow"):
            if aktuell is not None:
                aktuell.regeln.append(Regel(schluessel == "allow", wert, i))
        elif schluessel == "sitemap":
            rb.sitemaps.append(wert)
        elif schluessel == "content-signal":
            rb.content_signals.append((i, parse_content_signal(wert)))
        elif schluessel == "crawl-delay" and aktuell is not None:
            try:
                aktuell.crawl_delay = max(0.0, float(wert.replace(",", ".")))
            except ValueError:
                rb.ungueltige_zeilen += 1
        else:
            # Andere Felder (z. B. Crawl-delay) sind erlaubt, werden aber nicht ausgewertet.
            pass
    return rb


def fehlt() -> Robots:
    return Robots(zustand="fehlt")


def serverfehler(abgewiesen: bool = False) -> Robots:
    return Robots(zustand="serverfehler", abgewiesen=abgewiesen)


def unerreichbar(fehler: str = "") -> Robots:
    return Robots(zustand="unerreichbar", fehler=fehler)


# --- Regeltyp und Herkunft (Codebuch A2) ------------------------------------

def _wurzel_gesperrt(rb: Robots, produkt: str) -> bool:
    return not rb.erlaubt(produkt, "https://x/")


def ki_regeltyp(rb: Robots) -> dict[str, object]:
    """Gibt ki_regel (bool), typ und herkunft zurück.

    typ: keine · nur_nutzungssignal · training_gesperrt_suche_frei · alle_ki_gesperrt ·
         alles_gesperrt · nur_freigaben · gemischt
    herkunft: eigene · dienstleister · beides · (leer, wenn keine Regel)
    """
    if rb.zustand != "vorhanden":
        typ = {"serverfehler": "alles_gesperrt", "fehlt": "keine"}.get(rb.zustand, "")
        return {"ki_regel": False, "typ": typ, "herkunft": "", "genannt": ""}

    genannt = rb.ki_kennzeichen()
    signal = rb.ki_signal_zeilen()
    ki_regel = bool(genannt or signal)

    stern_gesperrt = _wurzel_gesperrt(rb, "eine-unbekannte-kennung")
    gesperrt = {a for a in genannt if _wurzel_gesperrt(rb, a)}
    frei = set(genannt) - gesperrt

    if stern_gesperrt and not frei:
        typ = "alles_gesperrt"
    elif not ki_regel:
        typ = "keine"
    elif not genannt:
        typ = "nur_nutzungssignal"
    elif not gesperrt:
        typ = "nur_freigaben"
    else:
        klassen_gesperrt = {konfig.KI_LISTE[a] for a in gesperrt}
        if "suche_agent" not in klassen_gesperrt and "training" in klassen_gesperrt and not stern_gesperrt:
            typ = "training_gesperrt_suche_frei"
        elif not frei and "suche_agent" in klassen_gesperrt:
            typ = "alle_ki_gesperrt"
        else:
            typ = "gemischt"

    herkunft = ""
    if ki_regel:
        zeilen = [z for zs in genannt.values() for z in zs] + signal
        if rb.verwaltet_zeilen:
            a, b = rb.verwaltet_zeilen
            innen = [z for z in zeilen if a <= z <= b]
            aussen = [z for z in zeilen if not (a <= z <= b)]
            herkunft = "beides" if innen and aussen else ("dienstleister" if innen else "eigene")
        else:
            herkunft = "eigene"
    return {
        "ki_regel": ki_regel,
        "typ": typ,
        "herkunft": herkunft,
        "genannt": " ".join(sorted(genannt)),
    }
