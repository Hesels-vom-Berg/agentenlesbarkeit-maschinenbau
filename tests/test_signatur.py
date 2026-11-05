import asyncio
import base64
import json
import os
import stat

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentenlesbarkeit import signatur
from agentenlesbarkeit.abruf import Abrufer
from agentenlesbarkeit.drossel import Drossel
from agentenlesbarkeit.speicher import Speicher

# Testschlüssel test-key-ed25519 aus RFC 9421, Anhang B.1.4
RFC_PRIVAT = b"""-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIJ+DYvh6SEqVTm50DFtMDoQikTmiCqirVv9mWG9qfSnF
-----END PRIVATE KEY-----
"""


def _rfc_schluessel():
    return serialization.load_pem_private_key(RFC_PRIVAT, password=None)


def test_thumbprint_wie_im_web_bot_auth_beispiel():
    # Cloudflare und der Web-Bot-Auth-Entwurf verwenden diesen Schlüssel mit dieser keyid.
    x = signatur.oeffentlich_x(_rfc_schluessel())
    assert x == "JrQLj5P_89iXES9-vFgrIy29clF9CC_oPPsw3c5D0bs"
    assert signatur.jwk_thumbprint(x) == "poqkLGiymh_W0uP6PZFw-dvez3QJT5SolqXBCW38r0U"


def test_rfc9421_testvektor_b26():
    basis = signatur.signatur_basis(
        [('"date"', "Tue, 20 Apr 2021 02:07:55 GMT"), ('"@method"', "POST"), ('"@path"', "/foo"),
         ('"@authority"', "example.com"), ('"content-type"', "application/json"), ('"content-length"', "18")],
        '("date" "@method" "@path" "@authority" "content-type" "content-length");'
        'created=1618884473;keyid="test-key-ed25519"')
    sig = base64.b64encode(_rfc_schluessel().sign(basis.encode())).decode()
    assert sig == "wqcAqbmYJ2ji2glfAMaRy4gruYYnx2nEFN2HN6jrnDnQCK1u02Gb04v9EDgwUPiu4A0w6vuQv5lIp5WPpBKRCw=="


def test_kopfzeilen_und_unabhaengige_pruefung():
    from http_message_signatures import HTTPMessageVerifier, HTTPSignatureKeyResolver, algorithms

    s = _rfc_schluessel()
    url = "https://www.beispiel.de/produkte?id=7"
    kopf = signatur.signiere(url, s, jetzt=1_800_000_000, nonce="abc")
    assert kopf["Signature-Agent"] == '"https://leitstand.one"'
    assert kopf["Signature-Input"].startswith('sig1=("@authority" "signature-agent");created=1800000000;')
    assert 'tag="web-bot-auth"' in kopf["Signature-Input"]
    assert "expires=1800000060" in kopf["Signature-Input"]

    class Aufloeser(HTTPSignatureKeyResolver):
        def resolve_public_key(self, key_id):
            assert key_id == "poqkLGiymh_W0uP6PZFw-dvez3QJT5SolqXBCW38r0U"
            return s.public_key()

    aktuell = signatur.signiere(url, s)            # unabhängige Prüfung mit aktueller Zeit
    anfrage = httpx.Request("GET", url, headers=aktuell)
    ergebnis = HTTPMessageVerifier(signature_algorithm=algorithms.ED25519,
                                   key_resolver=Aufloeser()).verify(anfrage, max_age=None)
    assert ergebnis and ergebnis[0].label == "sig1"


def test_authority_mit_und_ohne_port():
    assert signatur.authority("https://Beispiel.de/x") == "beispiel.de"
    assert signatur.authority("http://beispiel.de:8080/x") == "beispiel.de:8080"


def test_erzeuge_schreibt_privat_nur_fuer_eigentuemer(tmp_path, capsys):
    werte = signatur.erzeuge(str(tmp_path / "sig"))
    privat = tmp_path / "sig" / "privat.pem"
    jwk = tmp_path / "sig" / "privat.jwk.json"
    assert stat.S_IMODE(os.stat(privat).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(jwk).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "sig").st_mode) == 0o700
    geheim = json.loads(jwk.read_text())
    assert set(geheim) == {"kty", "crv", "x", "d"}
    assert geheim["d"] not in json.dumps(werte)       # privater Teil nie im Rückgabewert
    oeffentlich = json.loads((tmp_path / "sig" / "oeffentlich.jwk.json").read_text())
    assert "d" not in oeffentlich and oeffentlich["x"] == geheim["x"]
    assert werte["keyid"] == signatur.jwk_thumbprint(geheim["x"])
    s = signatur.lade(str(privat))
    pub = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(oeffentlich["x"] + "="))
    pub.verify(s.sign(b"probe"), b"probe")


def test_kommandozeile_gibt_privaten_schluessel_nie_aus(tmp_path, capsys):
    from agentenlesbarkeit.cli import main
    main(["schluessel", "--aus", str(tmp_path / "sig")])
    ausgabe = capsys.readouterr().out
    d = json.loads((tmp_path / "sig" / "privat.jwk.json").read_text())["d"]
    pem = (tmp_path / "sig" / "privat.pem").read_text()
    assert d not in ausgabe and "PRIVATE KEY" not in ausgabe and pem not in ausgabe
    assert "privat.jwk.json" in ausgabe


def test_signierter_arm_signiert_jede_anfrage_mit_ihrer_authority(tmp_path):
    gesehen = []

    def netz(req):
        gesehen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "a.de":
            return httpx.Response(301, headers={"Location": "https://www.a.de/"})
        return httpx.Response(200, text="<html><body>ok</body></html>")

    sp = Speicher(tmp_path / "lauf")

    async def ablauf():
        async with Abrufer(sp, Drossel(abstand_s=0.01), "rechenzentrum",
                           transport=httpx.MockTransport(netz), signatur_schluessel=_rfc_schluessel()) as a:
            return await a.hole("https://a.de/", "startseite", "a.de")
    antwort = asyncio.run(ablauf())
    assert antwort.status == 200
    assert all("signature" in r.headers and r.headers["signature-agent"] == '"https://leitstand.one"'
               for r in gesehen)
    assert {e["client"] for e in sp.eintraege()} == {"roh_signiert"}
