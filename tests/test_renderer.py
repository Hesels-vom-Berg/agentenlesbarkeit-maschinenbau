"""Gerenderter Abruf gegen einen lokalen Server mit echtem Chromium (langsamer Test)."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentenlesbarkeit import konfig, merkmale_a
from agentenlesbarkeit.abruf import Abrufer, Renderer
from agentenlesbarkeit.drossel import Drossel
from agentenlesbarkeit.speicher import Speicher

SEITE = b"""<!doctype html><html><head><title>Pumpen</title></head><body>
<h1>Statischer Kopf</h1><div id="app"></div><img src="/bild.png" alt="">
<script>document.getElementById('app').textContent =
 'Nennleistung 45 kW und Foerderhoehe 80 m dynamisch geladen aus der Datenbank';</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    log: list = []

    def do_GET(self):  # noqa: N802
        Handler.log.append((self.path, self.headers.get("User-Agent"), self.headers.get("From")))
        if self.path == "/robots.txt":
            body, typ = b"User-agent: *\nDisallow: /intern\n", "text/plain"
        elif self.path == "/bild.png":
            body, typ = b"\x89PNG", "image/png"
        else:
            body, typ = SEITE, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    Handler.log = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_gerendert_mit_kennung_ohne_bilder_und_robots(server, tmp_path):
    sp = Speicher(tmp_path / "lauf")

    async def ablauf():
        drossel = Drossel(abstand_s=0.05)
        async with Abrufer(sp, drossel, "rechenzentrum") as a:
            await a.hole(server + "/", "startseite", "lokal", extra={"versuch": 1})
            async with Renderer(sp, drossel, "rechenzentrum", a) as r:
                ein = await r.rendere(server + "/", "startseite", "lokal", extra={"versuch": 1})
                gesperrt = await r.rendere(server + "/intern/x", "seite", "lokal")
        return ein, gesperrt
    ein, gesperrt = asyncio.run(ablauf())

    assert ein["ergebnis"] == "erfolgreich" and ein["status"] == 200
    assert gesperrt["ergebnis"] == "nicht_durchgefuehrt"
    pfade = [p for p, _, _ in Handler.log]
    assert "/bild.png" not in pfade
    assert not any(p.startswith("/intern") for p in pfade)
    assert all(ua == konfig.USER_AGENT for _, ua, _ in Handler.log)
    assert all(f == konfig.KONTAKT for _, _, f in Handler.log)

    dom = sp.lese_record(ein["record_id"]).decode("utf-8")
    assert "dynamisch geladen" in dom
    roh = [e for e in sp.eintraege() if e["client"] == "roh" and e["art"] == "startseite"][0]
    roh_html = sp.lese_record(roh["record_id"]).decode("utf-8")
    anteil = merkmale_a.anteil_ohne_js(roh_html, dom)
    assert anteil is not None and anteil < 0.5     # der Großteil des Texts entsteht erst per JavaScript
