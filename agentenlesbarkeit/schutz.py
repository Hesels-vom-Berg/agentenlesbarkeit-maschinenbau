"""Erkennung von Schutzanbietern und Challenge-Seiten, Abrufergebnis (Codebuch A4).

Die Marker sind bewusst eng gefasst: Ein Skript eines Schutzanbieters auf einer
normalen Seite ist keine Challenge. Im Pretest werden die Marker an bekannten
Fällen geprüft und, falls nötig, vor der Erhebung ergänzt.
"""

from __future__ import annotations

import re

from . import konfig

ANBIETER_HEADER = [
    ("cloudflare", lambda h: "cloudflare" in h.get("server", "").lower() or "cf-ray" in h),
    ("akamai", lambda h: "akamai" in h.get("server", "").lower() or "akamai-grn" in h
     or "x-akamai-transformed" in h),
    ("cloudfront", lambda h: "x-amz-cf-id" in h),
    ("fastly", lambda h: "x-fastly-request-id" in h or "fastly" in h.get("server", "").lower()),
    ("imperva", lambda h: "x-iinfo" in h or "incap_ses_" in h.get("set-cookie", "")
     or "visid_incap_" in h.get("set-cookie", "")),
    ("sucuri", lambda h: "x-sucuri-id" in h or "sucuri" in h.get("server", "").lower()),
    ("vercel", lambda h: "x-vercel-id" in h),
    ("datadome", lambda h: "x-datadome" in h or "datadome=" in h.get("set-cookie", "")),
    ("aws_waf", lambda h: "x-amzn-waf-action" in h),
    ("azure_front_door", lambda h: "x-azure-ref" in h),
    ("myra", lambda h: "myra" in h.get("server", "").lower()),
    ("bunny", lambda h: "bunnycdn" in h.get("server", "").lower()),
]

CHALLENGE_HEADER = [
    ("cloudflare", lambda h: h.get("cf-mitigated", "").lower() == "challenge"),
    ("vercel", lambda h: h.get("x-vercel-mitigated", "").lower() == "challenge"),
    ("aws_waf", lambda h: h.get("x-amzn-waf-action", "").lower() in ("challenge", "captcha")),
    ("sucuri", lambda h: "x-sucuri-block" in h),
]

CHALLENGE_KOERPER = [
    ("cloudflare", re.compile(r"window\._cf_chl_opt|<title>\s*(Just a moment|Nur einen Moment|"
                              r"Einen Moment|Attention Required! \| Cloudflare)", re.I)),
    ("cloudflare", re.compile(r"Sorry, you have been blocked", re.I)),
    ("akamai", re.compile(r"<title>\s*Access Denied\s*</title>[\s\S]{0,4000}errors\.edgesuite\.net", re.I)),
    ("imperva", re.compile(r"Incapsula incident ID|_Incapsula_Resource", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|geo\.captcha-delivery", re.I)),
    ("human", re.compile(r"px-captcha|_pxCaptcha", re.I)),
    ("aws_waf", re.compile(r"awsWafCookieDomainList|AwsWafIntegration", re.I)),
    ("sucuri", re.compile(r"Sucuri WebSite Firewall - Access Denied", re.I)),
]

CAPTCHA_GENERISCH = re.compile(r'class=["\'][^"\']*\b(g-recaptcha|h-captcha|cf-turnstile)\b', re.I)


def anbieter(header: dict[str, str]) -> str:
    h = {k.lower(): v for k, v in header.items()}
    return ",".join(n for n, pruef in ANBIETER_HEADER if pruef(h))


def challenge(status: int | None, header: dict[str, str], html: str) -> str:
    """Gibt den Anbieter der erkannten Challenge oder Sperrseite zurück, sonst ''."""
    h = {k.lower(): v for k, v in header.items()}
    for name, pruef in CHALLENGE_HEADER:
        if pruef(h):
            return name
    kopf = html[:200_000]
    for name, muster in CHALLENGE_KOERPER:
        if muster.search(kopf):
            return name
    if status in konfig.ABWEISUNG_STATUS and CAPTCHA_GENERISCH.search(kopf):
        return "captcha"
    return ""


def ergebnis(status: int | None, fehler: str, nicht_durchgefuehrt: bool, challenge_marker: str) -> str:
    """erfolgreich · abgewiesen · technisch_fehlgeschlagen · nicht_durchgefuehrt"""
    if nicht_durchgefuehrt:
        return "nicht_durchgefuehrt"
    if fehler:
        return "technisch_fehlgeschlagen"
    if challenge_marker:
        return "abgewiesen"
    if status in konfig.ABWEISUNG_STATUS:
        return "abgewiesen"
    if status is not None and 200 <= status < 300:
        return "erfolgreich"
    return "technisch_fehlgeschlagen"
