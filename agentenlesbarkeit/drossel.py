"""Drosselung: höchstens eine Anfrage je Host im Abstand konfig.ABSTAND_S.

Der Abstand gilt ab Beginn der vorigen Anfrage an denselben Host
(Hostname und Port). Verschiedene Hosts laufen parallel.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from . import konfig


def host_schluessel(url: str) -> str:
    t = urlsplit(url)
    port = t.port or (443 if t.scheme == "https" else 80)
    return f"{(t.hostname or '').lower()}:{port}"


class Drossel:
    def __init__(self, abstand_s: float = konfig.ABSTAND_S,
                 uhr: Callable[[], float] = time.monotonic,
                 schlafen: Callable[[float], object] = asyncio.sleep) -> None:
        self.abstand_s = abstand_s
        self._uhr = uhr
        self._schlafen = schlafen
        self._schloesser: dict[str, asyncio.Lock] = {}
        self._zuletzt: dict[str, float] = {}
        self.protokoll: list[tuple[str, float]] = []   # (Host, Startzeit) für Tests und Prüfung

    async def warte(self, url: str) -> None:
        host = host_schluessel(url)
        schloss = self._schloesser.setdefault(host, asyncio.Lock())
        async with schloss:
            letzte = self._zuletzt.get(host)
            if letzte is not None:
                rest = self.abstand_s - (self._uhr() - letzte)
                if rest > 0:
                    await self._schlafen(rest)
            jetzt = self._uhr()
            self._zuletzt[host] = jetzt
            self.protokoll.append((host, jetzt))
