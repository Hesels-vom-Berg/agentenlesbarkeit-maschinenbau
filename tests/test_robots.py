from pathlib import Path

from agentenlesbarkeit import robots

P = "leitstand-studie"


def test_gruppen_und_gross_kleinschreibung():
    rb = robots.parse("User-Agent: GPTBot/1.0\nuser-agent: CCBot\nDISALLOW: /\n\nUser-agent: *\nAllow: /\n")
    assert len(rb.gruppen) == 2
    assert rb.gruppen[0].agenten == ["gptbot", "ccbot"]
    assert not rb.erlaubt("GPTBot", "https://x.de/seite")
    assert not rb.erlaubt("ccbot", "https://x.de/")
    assert rb.erlaubt(P, "https://x.de/seite")


def test_laengste_regel_und_gleichstand():
    rb = robots.parse("User-agent: *\nDisallow: /intern\nAllow: /intern/oeffentlich\n"
                      "Disallow: /a\nAllow: /a\n")
    assert not rb.erlaubt(P, "https://x.de/intern/geheim")
    assert rb.erlaubt(P, "https://x.de/intern/oeffentlich/seite")
    assert rb.erlaubt(P, "https://x.de/a")          # Gleichstand: Allow gewinnt


def test_platzhalter_und_ende():
    rb = robots.parse("User-agent: *\nDisallow: /*.pdf$\nDisallow: /suche*?q=\n")
    assert not rb.erlaubt(P, "https://x.de/datenblatt.pdf")
    assert rb.erlaubt(P, "https://x.de/datenblatt.pdf?v=2")
    assert not rb.erlaubt(P, "https://x.de/suche/x?q=pumpe")


def test_eigene_gruppe_vor_stern_und_ohne_regeln():
    rb = robots.parse("User-agent: *\nDisallow: /\n\nUser-agent: leitstand-studie\nAllow: /\n")
    assert rb.erlaubt(P, "https://x.de/")
    assert not rb.erlaubt("anderer-bot", "https://x.de/")
    assert robots.parse("User-agent: GPTBot\nDisallow: /\n").erlaubt(P, "https://x.de/")
    assert robots.parse("").erlaubt(P, "https://x.de/")
    assert robots.parse("User-agent: *\nDisallow:\n").erlaubt(P, "https://x.de/")


def test_robots_txt_selbst_und_zustaende():
    rb = robots.parse("User-agent: *\nDisallow: /\n")
    assert rb.erlaubt(P, "https://x.de/robots.txt")
    assert robots.fehlt().erlaubt(P, "https://x.de/")
    assert not robots.serverfehler().erlaubt(P, "https://x.de/")


def test_kommentare_bom_und_regeln_vor_gruppe():
    rb = robots.parse("﻿Disallow: /ohne-gruppe\n# Kommentar\nUser-agent: * # alle\nDisallow: /x # weg\n")
    assert rb.erlaubt(P, "https://x.de/ohne-gruppe")
    assert not rb.erlaubt(P, "https://x.de/x")


def test_leitstand_eigene_regel():
    text = (Path(__file__).parent / "leitstand_robots.txt").read_text(encoding="utf-8")
    rb = robots.parse(text)
    typ = robots.ki_regeltyp(rb)
    assert typ["ki_regel"] is True
    assert typ["typ"] == "training_gesperrt_suche_frei"
    assert typ["herkunft"] == "eigene"
    assert "gptbot" in typ["genannt"] and "claudebot" in typ["genannt"]
    assert rb.erlaubt(P, "https://leitstand.one/studie")
    assert rb.content_signals and rb.content_signals[0][1]["ai-train"] == "no"


def test_regeltypen():
    def typ(text):
        return robots.ki_regeltyp(robots.parse(text))["typ"]
    assert typ("User-agent: *\nDisallow: /admin\n") == "keine"
    assert typ("User-agent: *\nDisallow: /\n") == "alles_gesperrt"
    assert typ("User-agent: GPTBot\nUser-agent: ChatGPT-User\nUser-agent: PerplexityBot\nDisallow: /\n"
               "\nUser-agent: *\nAllow: /\n") == "alle_ki_gesperrt"
    assert typ("User-agent: GPTBot\nAllow: /\n") == "nur_freigaben"
    assert typ("User-agent: *\nContent-Signal: ai-train=no\nAllow: /\n") == "nur_nutzungssignal"
    assert typ("User-agent: GPTBot\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n"
               "\nUser-agent: OAI-SearchBot\nAllow: /\n") == "gemischt"
    assert typ("User-agent: GPTBot\nDisallow: /intern/\n") == "nur_freigaben"


def test_herkunft_dienstleister_und_beides():
    verwaltet = ("# BEGIN Cloudflare Managed content\n"
                 "User-agent: *\nContent-Signal: search=yes,ai-train=no\nAllow: /\n"
                 "User-agent: GPTBot\nDisallow: /\n"
                 "# END Cloudflare Managed Content\n")
    rb = robots.parse(verwaltet + "\nUser-agent: *\nDisallow: /admin\n")
    t = robots.ki_regeltyp(rb)
    assert t["herkunft"] == "dienstleister" and t["ki_regel"]
    rb = robots.parse(verwaltet + "\nUser-agent: PerplexityBot\nDisallow: /\n")
    assert robots.ki_regeltyp(rb)["herkunft"] == "beides"


def test_crawl_delay():
    rb = robots.parse("User-agent: *\nCrawl-delay: 10\n\nUser-agent: GPTBot\nCrawl-delay: 2,5\nDisallow: /\n")
    assert rb.crawl_delay(P) == 10.0
    assert rb.crawl_delay("GPTBot") == 2.5
    assert robots.parse("User-agent: *\nAllow: /\n").crawl_delay(P) is None


def test_serverfehler_gilt_als_alles_gesperrt_ohne_ki_regel():
    t = robots.ki_regeltyp(robots.serverfehler())
    assert t == {"ki_regel": False, "typ": "alles_gesperrt", "herkunft": "", "genannt": ""}
