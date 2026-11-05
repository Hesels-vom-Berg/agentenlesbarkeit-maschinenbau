"""Web Bot Auth: Signatur nach RFC 9421 für den explorativen, signierten Abruf.

Nur der zusätzliche Arm "roh_signiert" signiert. Der Hauptabruf für H4 bleibt
unsigniert und unverifiziert (Präregistrierung, Erhebung 5).

Schlüssel: Ed25519. Der private Schlüssel wird mit `python -m agentenlesbarkeit schluessel`
auf dem Erhebungsrechner erzeugt und nie veröffentlicht. Das Verzeichnis mit dem
öffentlichen Schlüssel liefert leitstand.one unter
/.well-known/http-message-signatures-directory aus (Pages Function, signierte Antwort).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SIGNATUR_AGENT = "https://leitstand.one"
TAG = "web-bot-auth"
GUELTIG_S = 60
LABEL = "sig1"


def b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def oeffentlich_x(schluessel: Ed25519PrivateKey) -> str:
    roh = schluessel.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url(roh)


def jwk_thumbprint(x: str) -> str:
    """JWK-Thumbprint nach RFC 7638 (SHA-256) für einen Ed25519-Schlüssel (RFC 8037)."""
    kanonisch = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(kanonisch.encode("utf-8")).digest())


def authority(url: str) -> str:
    t = urlsplit(url)
    host = (t.hostname or "").lower()
    standard = 443 if t.scheme == "https" else 80
    return host if t.port in (None, standard) else f"{host}:{t.port}"


def erzeuge(verzeichnis: str) -> dict:
    """Erzeugt ein Schlüsselpaar. Schreibt privat.pem (nur für den Eigentümer lesbar),
    oeffentlich.jwk.json und gibt die Werte für das Verzeichnis und das Pages-Secret zurück."""
    ziel = Path(verzeichnis)
    ziel.mkdir(parents=True, exist_ok=True)
    privat_pfad = ziel / "privat.pem"
    if privat_pfad.exists():
        raise SystemExit(f"{privat_pfad} existiert schon. Bestehenden Schlüssel nicht überschreiben.")
    s = Ed25519PrivateKey.generate()
    pem = s.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption())
    fd = os.open(privat_pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    x = oeffentlich_x(s)
    d = b64url(s.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                               serialization.NoEncryption()))
    oeffentlich = {"kty": "OKP", "crv": "Ed25519", "x": x}
    (ziel / "oeffentlich.jwk.json").write_text(json.dumps(oeffentlich, indent=2), encoding="utf-8")
    return {"keyid": jwk_thumbprint(x), "oeffentlich": oeffentlich,
            "pages_secret": json.dumps({**oeffentlich, "d": d}, separators=(",", ":"))}


def lade(pfad: str) -> Ed25519PrivateKey:
    schluessel = serialization.load_pem_private_key(Path(pfad).read_bytes(), password=None)
    if not isinstance(schluessel, Ed25519PrivateKey):
        raise SystemExit("Kein Ed25519-Schlüssel")
    return schluessel


def signatur_basis(komponenten: list[tuple[str, str]], params: str) -> str:
    zeilen = [f"{name}: {wert}" for name, wert in komponenten]
    zeilen.append(f'"@signature-params": {params}')
    return "\n".join(zeilen)


def signiere(url: str, schluessel: Ed25519PrivateKey, jetzt: int | None = None,
             nonce: str | None = None, agent: str = SIGNATUR_AGENT) -> dict[str, str]:
    """Gibt die drei Kopfzeilen für eine Anfrage an url zurück."""
    jetzt = int(time.time()) if jetzt is None else jetzt
    nonce = nonce or base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    keyid = jwk_thumbprint(oeffentlich_x(schluessel))
    agent_wert = f'"{agent}"'
    params = (f'("@authority" "signature-agent");created={jetzt};keyid="{keyid}";alg="ed25519";'
              f'expires={jetzt + GUELTIG_S};nonce="{nonce}";tag="{TAG}"')
    basis = signatur_basis([('"@authority"', authority(url)), ('"signature-agent"', agent_wert)], params)
    sig = base64.b64encode(schluessel.sign(basis.encode("ascii"))).decode("ascii")
    return {
        "Signature-Agent": agent_wert,
        "Signature-Input": f"{LABEL}={params}",
        "Signature": f"{LABEL}=:{sig}:",
    }
