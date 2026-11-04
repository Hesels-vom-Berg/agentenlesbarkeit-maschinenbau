import asyncio

from agentenlesbarkeit.drossel import Drossel, host_schluessel


class Uhr:
    def __init__(self):
        self.t = 1000.0
        self.geschlafen = []

    def __call__(self):
        return self.t

    async def schlafen(self, s):
        self.geschlafen.append(round(s, 3))
        self.t += s


def test_abstand_je_host_und_parallele_hosts():
    uhr = Uhr()
    d = Drossel(abstand_s=5.0, uhr=uhr, schlafen=uhr.schlafen)

    async def ablauf():
        await d.warte("https://a.de/robots.txt")
        await d.warte("https://b.de/robots.txt")   # anderer Host: kein Warten
        uhr.t += 1.5
        await d.warte("https://a.de/")              # gleicher Host: 3,5 s Rest
        await d.warte("https://a.de/seite")         # wieder volle 5 s
    asyncio.run(ablauf())
    assert uhr.geschlafen == [3.5, 5.0]
    starts = [t for h, t in d.protokoll if h == "a.de:443"]
    assert all(b - a >= 5.0 for a, b in zip(starts, starts[1:]))


def test_gleichzeitige_anfragen_werden_gestaffelt():
    uhr = Uhr()
    d = Drossel(abstand_s=5.0, uhr=uhr, schlafen=uhr.schlafen)

    async def ablauf():
        await asyncio.gather(*(d.warte(f"https://a.de/{i}") for i in range(4)))
    asyncio.run(ablauf())
    starts = [t for _, t in d.protokoll]
    assert starts == [1000.0, 1005.0, 1010.0, 1015.0]


def test_host_schluessel():
    assert host_schluessel("https://WWW.A.de/x") == "www.a.de:443"
    assert host_schluessel("http://a.de:8080/") == "a.de:8080"
