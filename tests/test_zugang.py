import asyncio
import csv
from datetime import datetime, timedelta, timezone

import httpx

from agentenlesbarkeit import konfig, merkmale_a, zugang
from agentenlesbarkeit.abruf import Abrufer
from agentenlesbarkeit.drossel import Drossel
from agentenlesbarkeit.speicher import Speicher

CF_SEITE = ("<html><head><title>Pumpen</title></head><body><h1>Kreiselpumpen</h1><p>Förderhöhe 80 m</p>"
            "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></body></html>")
CHALLENGE = ("<html><head><title>Just a moment...</title></head><body>"
             "<script>window._cf_chl_opt={cvId:'3'}</script></body></html>")


class Netz:
    def __init__(self):
        self.anfragen = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.anfragen.append(req)
        host, scheme, pfad = req.url.host, req.url.scheme, req.url.path
        if host == "langsam.de" or (host == "traege.de" and pfad == "/"):
            raise httpx.ReadTimeout("zu langsam", request=req)
        if host == "nurwww.de":
            raise httpx.ConnectError("[Errno -2] Name or service not known", request=req)
        if host == "nurhttp.de" and scheme == "https":
            raise httpx.ConnectError("Connection refused", request=req)
        if host == "kaputt.de" and pfad == "/robots.txt":
            return httpx.Response(503)
        if host == "gesperrt.de" and pfad == "/robots.txt":
            return httpx.Response(200, text="User-agent: leitstand-studie\nDisallow: /\n")
        if host == "www.umleitung.de" and pfad == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        if host == "challenge.de" and pfad == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if pfad == "/robots.txt":
            return httpx.Response(404, text="nicht da")
        if host == "umleitung.de" and pfad == "/":
            return httpx.Response(301, headers={"Location": "https://www.umleitung.de/"})
        if host == "challenge.de":
            return httpx.Response(403, text=CHALLENGE, headers={"cf-mitigated": "challenge",
                                                                "server": "cloudflare", "cf-ray": "1"})
        if host == "tdm.de":
            return httpx.Response(200, text='<html><head><meta name="tdm-reservation" content="1"></head>'
                                            '<body>vertraulich</body></html>')
        if host == "stichprobe.de":
            if pfad == "/.well-known/tdmrep.json":
                return httpx.Response(200, json=[{"location": "/*", "tdm-reservation": 1}])
            if pfad == "/llms.txt":
                return httpx.Response(200, text="# Stichprobe GmbH\n> Pumpen\n")
            if pfad == "/ai.txt":
                return httpx.Response(200, text="<!DOCTYPE html><html><body>Seite nicht gefunden</body></html>")
        return httpx.Response(200, text=CF_SEITE, headers={"server": "cloudflare", "cf-ray": "2",
                                                           "content-type": "text/html"})


def _lauf(tmp_path, domains, modus="vollerhebung"):
    netz = Netz()
    sp = Speicher(tmp_path / "lauf")

    async def ablauf():
        async with Abrufer(sp, Drossel(abstand_s=0.01), "rechenzentrum",
                           transport=httpx.MockTransport(netz)) as a:
            await zugang.lauf(domains, a, None, modus, parallel=5)
    asyncio.run(ablauf())
    return netz, sp


def _start(sp, domain):
    return [e for e in sp.eintraege() if e["domain"] == domain and e["art"] == "startseite"][-1]


def test_kennung_und_ergebnisse(tmp_path):
    domains = ["offen.de", "challenge.de", "gesperrt.de", "umleitung.de", "langsam.de", "kaputt.de",
               "nurhttp.de", "traege.de", "nurwww.de"]
    netz, sp = _lauf(tmp_path, domains)

    assert all(r.headers["user-agent"] == konfig.USER_AGENT for r in netz.anfragen)
    assert all(r.headers["from"] == konfig.KONTAKT for r in netz.anfragen)

    e = _start(sp, "offen.de")
    assert (e["ergebnis"], e["schutz"], e["challenge"]) == ("erfolgreich", "cloudflare", "")

    e = _start(sp, "challenge.de")
    assert (e["ergebnis"], e["challenge"], e["status"]) == ("abgewiesen", "cloudflare", 403)

    e = _start(sp, "gesperrt.de")
    assert e["ergebnis"] == "nicht_durchgefuehrt"
    assert not [r for r in netz.anfragen if r.url.host == "gesperrt.de" and r.url.path == "/"]

    e = _start(sp, "umleitung.de")
    assert e["ergebnis"] == "nicht_durchgefuehrt" and e["url_final"] == "https://www.umleitung.de/"
    assert not [r for r in netz.anfragen if r.url.host == "www.umleitung.de" and r.url.path == "/"]

    e = _start(sp, "langsam.de")
    assert (e["ergebnis"], e["fehler"]) == ("technisch_fehlgeschlagen", "robots_zeitueberschreitung")

    e = _start(sp, "nurwww.de")
    assert e["ergebnis"] == "erfolgreich" and e["www_rueckfall"] is True
    assert e["url_final"] == "https://www.nurwww.de/"

    e = _start(sp, "traege.de")
    assert (e["ergebnis"], e["fehler"]) == ("technisch_fehlgeschlagen", "zeitueberschreitung")

    e = _start(sp, "kaputt.de")
    assert e["ergebnis"] == "nicht_durchgefuehrt"   # robots.txt 503: alles gesperrt
    assert e["robots_abgewiesen"] is True           # für die Sensitivitätsrechnung zu H4

    e = _start(sp, "nurhttp.de")
    assert e["ergebnis"] == "erfolgreich" and e["http_rueckfall"] is True


def test_tdm_vorbehalt_verwirft_koerper(tmp_path):
    _, sp = _lauf(tmp_path, ["tdm.de"])
    e = _start(sp, "tdm.de")
    assert e["tdm_vorbehalt"] is True and e["ergebnis"] == "erfolgreich"
    assert sp.lese_record(e["record_id"]) == b""


def test_stichprobe_und_merkmale(tmp_path):
    _, sp = _lauf(tmp_path, ["stichprobe.de", "offen.de", "challenge.de"], modus="stichprobe")
    arten = {e["art"] for e in sp.eintraege() if e["domain"] == "stichprobe.de"}
    assert {"robots", "tdmrep", "llms_txt", "ai_txt", "startseite"} <= arten
    assert _start(sp, "stichprobe.de")["tdm_vorbehalt"] is True

    ziel = tmp_path / "a.csv"
    assert merkmale_a.berechne([str(tmp_path / "lauf")], str(ziel)) == 3
    zeilen = {z["domain"]: z for z in csv.DictReader(open(ziel, encoding="utf-8"))}
    s = zeilen["stichprobe.de"]
    assert (s["a1_robots"], s["a3_tdm_vorbehalt"], s["a3_llms_txt"], s["a3_ai_txt"]) == ("fehlt", "1", "1", "0")
    assert zeilen["challenge.de"]["a4_roh_rechenzentrum"] == "abgewiesen_unbestaetigt"
    assert zeilen["challenge.de"]["a1_robots"] == "vorhanden"
    assert zeilen["offen.de"]["a4_roh_rechenzentrum"] == "erfolgreich"


def test_wiederholung_faellig_nach_24_stunden():
    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    e = [{"domain": "a.de", "art": "startseite", "client": "roh", "versuch": 1,
          "ergebnis": "abgewiesen", "zeit": t0.strftime("%Y-%m-%dT%H:%M:%SZ")},
         {"domain": "b.de", "art": "startseite", "client": "roh", "versuch": 1,
          "ergebnis": "erfolgreich", "zeit": t0.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    assert zugang.faellige_wiederholungen(e, "roh", t0 + timedelta(hours=23)) == []
    assert zugang.faellige_wiederholungen(e, "roh", t0 + timedelta(hours=24)) == ["a.de"]
    e.append({"domain": "a.de", "art": "startseite", "client": "roh", "versuch": 2,
              "ergebnis": "abgewiesen", "zeit": "2026-11-11T09:00:00Z"})
    assert zugang.faellige_wiederholungen(e, "roh", t0 + timedelta(hours=30)) == []
    assert merkmale_a._zusammenfassen([e[0], e[2]]) == "abgewiesen"
