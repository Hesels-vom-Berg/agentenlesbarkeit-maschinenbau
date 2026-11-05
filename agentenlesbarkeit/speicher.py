"""Speicherung der Rohantworten als WARC (ISO 28500) plus Index als JSON Lines.

Je Lauf entsteht ein Verzeichnis mit
  antworten.warc.gz  Anfragen, Antworten und gerenderte DOMs
  index.jsonl        eine Zeile je Abruf mit Bedingung, Ergebnis und WARC-Verweis

Alle Merkmale werden später aus diesen Dateien berechnet, nicht live.
Der Index verweist über die WARC-Record-ID; gelesen wird mit lese_record().
"""

from __future__ import annotations

import io
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

ENTFERNTE_HEADER = {"content-encoding", "transfer-encoding", "content-length"}


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Speicher:
    def __init__(self, verzeichnis: str | Path) -> None:
        self.verzeichnis = Path(verzeichnis)
        self.verzeichnis.mkdir(parents=True, exist_ok=True)
        self._warc_pfad = self.verzeichnis / "antworten.warc.gz"
        self._index_pfad = self.verzeichnis / "index.jsonl"
        self._sperre = threading.Lock()

    # --- Schreiben ------------------------------------------------------------

    def _schreibe(self, records) -> list[str]:
        ids = []
        with self._sperre, open(self._warc_pfad, "ab") as f:
            w = WARCWriter(f, gzip=True)
            for r in records:
                w.write_record(r)
                ids.append(r.rec_headers.get_header("WARC-Record-ID"))
        return ids

    def http(self, url: str, methode: str, anfrage_header: dict[str, str], status: int,
             grund: str, antwort_header: list[tuple[str, str]], koerper: bytes) -> str:
        """Speichert Anfrage und Antwort. Der Körper ist dekodiert; Kodierungs-Header
        werden entfernt und im Index vermerkt. Gibt die Record-ID der Antwort zurück."""
        puffer = io.BytesIO()
        w = WARCWriter(puffer, gzip=False)
        teil = url.split("/", 3)
        pfad = "/" + (teil[3] if len(teil) > 3 else "")
        anfrage = w.create_warc_record(
            url, "request",
            http_headers=StatusAndHeaders(f"{methode} {pfad} HTTP/1.1",
                                          list(anfrage_header.items()), is_http_request=True))
        header = [(k, v) for k, v in antwort_header if k.lower() not in ENTFERNTE_HEADER]
        header.append(("Content-Length", str(len(koerper))))
        antwort = w.create_warc_record(
            url, "response", payload=io.BytesIO(koerper),
            http_headers=StatusAndHeaders(f"{status} {grund}".strip(), header, protocol="HTTP/1.1"))
        ids = self._schreibe([anfrage, antwort])
        return ids[1]

    def gerendert(self, url: str, dom: str, meta: dict) -> str:
        w = WARCWriter(io.BytesIO(), gzip=False)
        daten = dom.encode("utf-8")
        rec = w.create_warc_record(
            "urn:gerendert:" + url, "resource", payload=io.BytesIO(daten),
            warc_content_type="text/html; charset=utf-8", length=len(daten))
        metarec = w.create_warc_record(
            "urn:gerendert:" + url, "metadata",
            payload=io.BytesIO(json.dumps(meta, ensure_ascii=False).encode("utf-8")),
            warc_content_type="application/json")
        ids = self._schreibe([rec, metarec])
        return ids[0]

    def index(self, eintrag: dict) -> None:
        eintrag = {"zeit": jetzt_iso(), **eintrag}
        with self._sperre, open(self._index_pfad, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")

    # --- Lesen ---------------------------------------------------------------

    def eintraege(self) -> list[dict]:
        if not self._index_pfad.exists():
            return []
        with open(self._index_pfad, encoding="utf-8") as f:
            return [json.loads(z) for z in f if z.strip()]

    def lese_records(self, ids: set[str]) -> dict[str, bytes]:
        """Liest mehrere Records in einem Durchlauf."""
        gefunden: dict[str, bytes] = {}
        if not ids or not self._warc_pfad.exists():
            return gefunden
        with open(self._warc_pfad, "rb") as f:
            for rec in ArchiveIterator(f):
                rid = rec.rec_headers.get_header("WARC-Record-ID")
                if rid in ids:
                    gefunden[rid] = rec.content_stream().read()
        return gefunden

    def lese_record(self, record_id: str) -> bytes | None:
        """Gibt den Inhalt (HTTP-Körper bzw. Ressource) eines Records zurück."""
        if not record_id or not self._warc_pfad.exists():
            return None
        with open(self._warc_pfad, "rb") as f:
            for rec in ArchiveIterator(f):
                if rec.rec_headers.get_header("WARC-Record-ID") == record_id:
                    return rec.content_stream().read()
        return None
