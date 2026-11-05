"""Feste Werte der Erhebung, Präregistrierung Fassung 1.1.

Alles, was die Präregistrierung (https://leitstand.one/studie) festlegt, steht
hier an einer Stelle. Änderungen während einer Welle sind nicht zulässig.
"""

FASSUNG = "1.1"

# Deklarierter Agent. Das Produktkennzeichen für robots.txt ist PRODUKT.
PRODUKT = "leitstand-studie"
USER_AGENT = "leitstand-studie/1.0 (+https://leitstand.one/studie)"
KONTAKT = "studie@leitstand.one"

# Drosselung: höchstens eine Anfrage je Host in diesem Abstand (Sekunden).
ABSTAND_S = 5.0
# Wiederholung einer Abweisung frühestens nach dieser Zeit (Stunden).
WIEDERHOLUNG_H = 24.0

# Zeitgrenzen und Größengrenzen.
TIMEOUT_VERBINDUNG_S = 10.0
TIMEOUT_GESAMT_S = 30.0
MAX_WEITERLEITUNGEN = 5
MAX_ROBOTS_BYTES = 500 * 1024          # RFC 9309: mindestens 500 KiB auswerten
MAX_HTML_BYTES = 15 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_SITEMAP_BYTES = 50 * 1024 * 1024   # entpackt
MAX_SITEMAP_DATEIEN = 5
MAX_SITEMAP_URLS = 50_000

# Seitenbudget je Domain (Präregistrierung, Abschnitt "Für Websitebetreiber").
MAX_WEITERE_SEITEN = 10
MAX_PRODUKTSEITEN = 5
MAX_PRODUKTSEITEN_EN = 2
MAX_PDF_JE_SEITE = 1
MAX_BILDER_JE_SEITE = 3

# Gerenderter Abruf: diese Ressourcentypen lädt der Browser nicht.
GESPERRTE_RESSOURCEN = ("image", "media", "font")
RENDER_WARTEN_MS = 15_000

# Statuscodes, die als Abweisung zählen (Codebuch A4).
ABWEISUNG_STATUS = frozenset({401, 403, 429, 451, 503})

# KI-Liste, Fassung 1.0. Klasse: training, suche_agent oder gemischt.
# Die Klasse dient nur der Beschreibung des Regeltyps (Codebuch A2);
# für H1 zählt jedes Kennzeichen der Liste gleich.
KI_LISTE = {
    "gptbot": "training",
    "chatgpt-user": "suche_agent",
    "oai-searchbot": "suche_agent",
    "claudebot": "training",
    "claude-user": "suche_agent",
    "claude-searchbot": "suche_agent",
    "claude-web": "suche_agent",
    "anthropic-ai": "training",
    "perplexitybot": "suche_agent",
    "perplexity-user": "suche_agent",
    "google-extended": "training",
    "google-cloudvertexbot": "suche_agent",
    "applebot-extended": "training",
    "ccbot": "training",
    "bytespider": "training",
    "meta-externalagent": "training",
    "meta-externalfetcher": "suche_agent",
    "amazonbot": "gemischt",
    "cohere-ai": "gemischt",
    "cohere-training-data-crawler": "training",
    "diffbot": "training",
    "duckassistbot": "suche_agent",
    "mistralai-user": "suche_agent",
    "youbot": "suche_agent",
    "ai2bot": "training",
    "timpibot": "training",
    "omgili": "training",
    "imagesiftbot": "training",
}

# Kennzeichen einer vom Dienstleister vorangestellten, verwalteten robots.txt.
# Im Pretest an bekannten Fällen zu bestätigen (Präregistrierung, Erhebung 1).
VERWALTET_BEGINN = r"^\s*#\s*BEGIN\s+Cloudflare\s+Managed\s+content\b"
VERWALTET_ENDE = r"^\s*#\s*END\s+Cloudflare\s+Managed\s+content\b"
