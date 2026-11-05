import csv

from agentenlesbarkeit import ziehung


def _csv(pfad, kopf, zeilen):
    with open(pfad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(kopf)
        w.writerows(zeilen)
    return str(pfad)


def test_normalisierung():
    n = ziehung.normalisiere_domain
    assert n("https://www.Beispiel.de/de/produkte") == "beispiel.de"
    assert n("beispiel.ch") == "beispiel.ch"
    assert n("www.müller-pumpen.de") == "müller-pumpen.de"
    assert n("") == "" and n("keine domain") == ""


def test_ziehung_dedupliziert_filtert_und_ist_reproduzierbar(tmp_path):
    vdma = _csv(tmp_path / "vdma.csv", ["Firma", "Website", "Land"],
                [["A", "https://www.a.de", "Deutschland"], ["B", "b.de", "DE"],
                 ["C", "c.it", "Italien"], ["D", "", "DE"], ["S", "s.ch", "Schweiz"]])
    swissmem = _csv(tmp_path / "swissmem.csv", ["name", "url", "country"],
                    [["S", "www.s.ch", "CH"], ["T", "t.ch", "Switzerland"]])
    p1 = ziehung.ziehe([vdma, swissmem], 20261104, str(tmp_path / "z1"))
    p2 = ziehung.ziehe([vdma, swissmem], 20261104, str(tmp_path / "z2"))
    assert p1["domains"] == 4                         # a.de, b.de, s.ch, t.ch
    assert p1["sha256_domainliste"] == p2["sha256_domainliste"]
    assert p1["quellen"]["vdma"] == {"zeilen": 5, "ohne_domain": 1, "ausserhalb_de_ch": 1}
    l1 = (tmp_path / "z1" / "sichtungsliste.csv").read_text(encoding="utf-8")
    l2 = (tmp_path / "z2" / "sichtungsliste.csv").read_text(encoding="utf-8")
    assert l1 == l2
    zeilen = list(csv.DictReader(l1.splitlines()))
    s = [z for z in zeilen if z["domain"] == "s.ch"][0]
    assert s["quellen"] == "swissmem+vdma" and s["land"] == "CH"
    a = [z for z in zeilen if z["domain"] == "a.de"][0]
    assert a["host"] == "www.a.de"                   # Abruf mit dem Host aus dem Verzeichnis
    assert ziehung.domain_hash(["b.de", "a.de"]) == ziehung.domain_hash(["a.de", "b.de"])


def test_sichtung_quoten_und_anteile(tmp_path, monkeypatch):
    monkeypatch.setattr(ziehung, "JE_SCHICHT", 2)
    liste = _csv(tmp_path / "s.csv",
                 ["rang", "domain", "quellen", "land", "name", "geschaeftsmodell", "groesse", "ausschluss", "notiz"],  # noqa: E501
                 [[1, "a.de", "v", "DE", "", "serie", "", "", ""],
                  [2, "b.de", "v", "DE", "", "serie", "", "", ""],
                  [3, "c.de", "v", "DE", "", "", "", "haendler", ""],
                  [4, "d.de", "v", "DE", "", "serie", "", "", ""],
                  [5, "e.de", "v", "DE", "", "produkt", "", "", ""],
                  [6, "f.de", "v", "DE", "", "projekt", "", "", ""],
                  [7, "g.de", "v", "DE", "", "produkt", "", "", ""],
                  [8, "h.de", "v", "DE", "", "projekt", "", "", ""],
                  [9, "i.de", "v", "DE", "", "", "", "", ""]])
    erg = ziehung.stand(liste, str(tmp_path / "stichprobe.csv"))
    assert erg["voll"] and erg["stopp_rang"] == 8
    assert erg["gesichtet_geeignet"] == {"serie": 3, "produkt": 2, "projekt": 2}
    assert erg["ausgeschlossen"] == {"haendler": 1}
    assert abs(sum(erg["schichtanteile"].values()) - 1) < 1e-9
    stichprobe = list(csv.DictReader(open(tmp_path / "stichprobe.csv", encoding="utf-8")))
    assert [z["domain"] for z in stichprobe if z["schicht"] == "serie"] == ["a.de", "b.de"]
