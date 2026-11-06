"""Offline tests for M2 group A: barcodes and ASINs under their other tag names,
label + catalogue number searches, and more Discogs master versions when the album
has a code to compare. No network: every lookup is answered by a stub.

Run:  python3 tests/test_codes_labels.py [discogs-plugin-dir]
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse as up
from pathlib import Path

# TagMender settings and state of the tests never touch the real ones
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm-state-")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import reasons  # noqa: E402
from mutagen.id3 import ID3, TXXX  # noqa: E402
from mutagen.mp4 import MP4, MP4FreeForm  # noqa: E402
from mutagen.apev2 import APEv2  # noqa: E402

PLUGIN_DIR = (sys.argv[1] if len(sys.argv) > 1 else
              os.environ.get("TAGMENDER_DISCOGS_PLUGIN", "plugins_builtin/discogs"))
CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
UPC = "724384260927"            # a real check digit (UPC-A)
EAN = "0724384260927"           # the same code as EAN-13
BAD = "4006408060927"           # thirteen digits, wrong check digit


def tone(path, secs=1, **meta):
    """A short generated file; ffmpeg writes the Vorbis/ID3 tags given."""
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={secs}", "-ar", "8000",
                    "-ac", "1", *tags, str(path), "-y"], check=True)
    return path


def album(**kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s}
                    for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": set(), "catnos": set(), "catno_search": [], "search_labels": []}
    a.update(kw)
    return a


class Spy:
    """Answers every search with nothing and records what was asked."""
    def __init__(self):
        self.urls = []

    def get_json(self, url, host):
        self.urls.append(up.unquote(url))
        return {"releases": []} if host == "mb" else {"results": []}


class CodeTagNames(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vorbis_upc_ean_and_asin(self):
        p = tone(self.tmp / "a.flac", UPC=f"{UPC[0]} {UPC[1:6]} {UPC[6:11]} {UPC[11]}", ASIN="B000002ABC")
        info = ma.read_file(p)
        self.assertEqual((info["barcode"], info["asin"]), (UPC, "B000002ABC"))

    def test_wrong_check_digit_is_not_a_barcode(self):
        info = ma.read_file(tone(self.tmp / "a.flac", **{"EAN/UPN": BAD}))
        self.assertEqual(info["barcode"], "")

    def test_ean_upn_spelling(self):
        info = ma.read_file(tone(self.tmp / "b.flac", **{"EAN/UPN": EAN}))
        self.assertEqual((info["barcode"], info["barcode_from"]), (EAN, "EAN/UPN"))
        p = tone(self.tmp / "b.mp3")
        tags = ID3()
        tags.add(TXXX(encoding=3, desc="EAN/UPN", text=[EAN]))
        tags.save(p)
        self.assertEqual(ma.read_file(p)["barcode"], EAN)

    def test_empty_barcode_tag_keeps_a_code_read_before(self):
        t = {"barcode": "", "barcode_from": ""}
        ma._code_tag(t, "UPC", UPC)
        ma._code_tag(t, "BARCODE", "")
        self.assertEqual((t["barcode"], t["barcode_from"]), (UPC, "UPC"))
        ma._code_tag(t, "BARCODE", "5099749")              # a real BARCODE: Picard's, kept
        self.assertEqual((t["barcode"], t["barcode_from"]), ("5099749", ""))
        p = tone(self.tmp / "a.mp3")
        tags = ID3()
        tags.add(TXXX(encoding=3, desc="UPC", text=[UPC]))
        tags.add(TXXX(encoding=3, desc="BARCODE", text=[""]))
        tags.save(p)
        self.assertEqual(ma.read_file(p)["barcode"], UPC)

    def test_picard_barcode_tag_wins_over_other_names(self):
        info = ma.read_file(tone(self.tmp / "a.flac", BARCODE="5099749", UPC=UPC))
        self.assertEqual(info["barcode"], "5099749")       # as Picard wrote it, never second-guessed

    def test_asin_that_is_a_barcode_counts_as_one(self):
        info = ma.read_file(tone(self.tmp / "a.flac", AMAZON_ID=EAN))
        self.assertEqual((info["barcode"], info["asin"]), (EAN, ""))
        info = ma.read_file(tone(self.tmp / "b.flac", ASIN="0306406152"))     # ISBN-10-like
        self.assertEqual((info["barcode"], info["asin"]), ("", "0306406152"))
        info = ma.read_file(tone(self.tmp / "c.flac", ASIN="not an id"))
        self.assertEqual((info["barcode"], info["asin"]), ("", ""))

    def test_id3_txxx(self):
        p = tone(self.tmp / "a.mp3")
        tags = ID3()
        tags.add(TXXX(encoding=3, desc="UPC", text=[UPC]))
        tags.add(TXXX(encoding=3, desc="Amazon ID", text=["B00004TZXL"]))
        tags.save(p)
        info = ma.read_file(p)
        self.assertEqual((info["barcode"], info["asin"]), (UPC, "B00004TZXL"))

    def test_mp4_freeform(self):
        p = tone(self.tmp / "a.m4a")
        f = MP4(p)
        f["----:com.apple.iTunes:EAN"] = [MP4FreeForm(EAN.encode())]
        f["----:com.apple.iTunes:ASIN"] = [MP4FreeForm(b"B00004TZXL")]
        f.save()
        info = ma.read_file(p)
        self.assertEqual((info["barcode"], info["asin"]), (EAN, "B00004TZXL"))

    def test_ape_tags(self):
        p = tone(self.tmp / "a.wv")
        tags = APEv2()
        tags["UPC"] = UPC
        tags["ASIN"] = "B00004TZXL"
        tags.save(p)
        info = ma.read_file(p)
        self.assertEqual((info["barcode"], info["asin"]), (UPC, "B00004TZXL"))

    def test_album_keeps_them_unless_blind(self):
        d = self.tmp / "Artist Alpha" / "2010 - First Light"
        d.mkdir(parents=True)
        tone(d / "01.flac", UPC=UPC, ASIN="B00004TZXL", LABEL="Art Music", title="T0", tracknumber="1")
        (d / "info.txt").write_text("Ripped by me\nLabel: Dobles Music / Tidal\n")
        a = ma.read_album(d, "Artist Alpha", d.name)
        self.assertEqual(a["barcodes"], {mc.norm_code(UPC)})
        self.assertEqual(a["asins"], ["B00004TZXL"])
        self.assertEqual(a["barcode_sources"], ["UPC"])     # said in the result, not hidden
        self.assertEqual(a["search_labels"], ["Art Music", "Dobles Music", "Tidal"])
        blind = ma.read_album(d, "Artist Alpha", d.name, blind=True)
        self.assertEqual((blind["barcodes"], blind["asins"], blind["barcode_sources"]), (set(), [], []))
        self.assertEqual(blind["search_labels"], ["Dobles Music", "Tidal"])   # notes: what any copy has


class LabelsFromText(unittest.TestCase):
    def test_label_lines(self):
        text = ('Artist: X\nLabel: Dobles Music / Tidal\nREM LABEL "Art Music"\n'
                "labelled in 2005\nLabel: 2005\n")
        self.assertEqual(ma.labels_from_text(text), ["Dobles Music", "Tidal", "Art Music"])
        self.assertEqual(ma.labels_from_text("Record label = Foo; Bar"), ["Foo", "Bar"])
        self.assertEqual(ma.labels_from_text("the label of the disc says nothing"), [])


class LabelCatnoSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN_DIR)

    def test_mb_asks_label_and_catno_before_the_title(self):
        h = Spy()
        ma.mb_search(h, album(catno_search=["AMG 099"], search_labels=['Art "Music"']), CFG)
        queries = [u.split("query=")[1].split("&")[0] for u in h.urls if "query=" in u]
        self.assertIn('label:"Art  Music" AND catno:"AMG 099"', queries)
        first_title = next(i for i, x in enumerate(queries) if x.startswith("release:"))
        self.assertLess(queries.index('label:"Art  Music" AND catno:"AMG 099"'), first_title)

    def test_discogs_asks_label_and_catno(self):
        h = Spy()
        ma.discogs_search(h, album(catno_search=["AMG 099"], search_labels=["Art Music"]), CFG, self.dm)
        self.assertTrue(any("label=Art Music&catno=AMG 099" in u for u in h.urls), h.urls)

    def test_shared_number_does_not_skip_the_label(self):
        # A bare number other labels share fills the results; the label search must
        # still be sent (it is asked before the bare number, which ends the search)
        class Flood(Spy):
            def get_json(self, url, host):
                super().get_json(url, host)
                u = up.unquote(url)
                if "query=" not in u and "search?" not in u:
                    return None                             # release details: unreachable
                if host == "mb" and 'query=catno:"AMG 099"&' in u:
                    return {"releases": [{"id": f"r{i}", "track-count": 3} for i in range(15)]}
                if host == "discogs" and "search?" in u and u.endswith("&catno=AMG 099") \
                        and "label=" not in u:
                    return {"results": [{"id": i, "title": f"Other - Disc {i}"} for i in range(15)]}
                return {"releases": []} if host == "mb" else {"results": []}
        a = album(catno_search=["AMG 099"], search_labels=["Art Music"])
        h = Flood()
        ma.mb_search(h, a, CFG)
        self.assertTrue(any('query=label:"Art Music" AND catno:"AMG 099"' in u for u in h.urls), h.urls)
        h = Flood()
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertTrue(any("label=Art Music&catno=AMG 099" in u for u in h.urls), h.urls)
        # the order itself: number with artist, then label with number, then the bare number
        h = Spy()
        ma.mb_search(h, a, CFG)
        qs = [u.split("query=")[1].split("&")[0] for u in h.urls if "query=" in u]
        self.assertEqual(qs[:3], ['catno:"AMG 099" AND artist:"Artist Alpha"',
                                  'label:"Art Music" AND catno:"AMG 099"', 'catno:"AMG 099"'])
        h = Spy()
        ma.discogs_search(h, a, CFG, self.dm)
        ss = [u.split("per_page=15&")[1] for u in h.urls if "per_page=15&" in u]
        self.assertEqual(ss[:3], ["catno=AMG 099&artist=Artist Alpha", "label=Art Music&catno=AMG 099",
                                  "catno=AMG 099"])

    def test_no_label_or_no_catno_no_label_query(self):
        for a in (album(catno_search=["AMG 099"]), album(search_labels=["Art Music"])):
            h = Spy()
            ma.mb_search(h, a, CFG)
            ma.discogs_search(h, a, CFG, self.dm)
            self.assertFalse(any("label:" in u or "label=" in u for u in h.urls), h.urls)

    def test_asin_is_searched_on_musicbrainz(self):
        h = Spy()
        ma.mb_search(h, album(asins=["B00004TZXL"]), CFG)
        self.assertTrue(any("query=asin:B00004TZXL" in u for u in h.urls), h.urls)

    def test_pairs_are_capped(self):
        a = album(search_labels=["A", "B", "C"], catno_search=["1", "2", "3"])
        self.assertEqual(len(ma.label_catnos(a)), 4)


def mb_release(rid, lens_ms, catno="AMG 099"):
    return {"id": rid, "title": "Endorama", "date": "2003", "country": "RU",
            "artist-credit": [{"name": "Kreator", "joinphrase": ""}],
            "label-info": [{"catalog-number": catno, "label": {"name": "Art Music"}}],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms} for i, ms in enumerate(lens_ms)]}]}


class LabelHttp:
    """MusicBrainz finds the release only by label + catalogue number (no title or catalogue
    number search answers); the lengths decide whether it is yours."""
    def __init__(self, lens_ms, label="Art Music", catno="AMG 099"):
        self.rel = mb_release("mb-amg", lens_ms, catno=catno)
        self.label, self.catno = label, catno
        self.queries = []
        self.discogs_urls = []

    def get_json(self, url, host):
        if host != "mb":
            self.discogs_urls.append(up.unquote(url))
            return {"results": []}
        if "/ws/2/release/?" in url:
            query = up.unquote(url.split("query=")[1].split("&")[0])
            self.queries.append(query)
            hit = query == f'label:"{self.label}" AND catno:"{self.catno}"'
            return {"releases": [{"id": "mb-amg", "track-count": 3}] if hit else []}
        return self.rel if "/release/mb-amg" in url else None


class LabelCatnoCandidates(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "Kreator" / "Unnamed Rip"
        self.d.mkdir(parents=True)
        for i in range(3):
            tone(self.d / f"{i + 1:02d}.flac", secs=10 * (i + 1), title=f"T{i}", album="Endorama",
                 albumartist="Kreator", tracknumber=str(i + 1), LABEL="Art Music", CATALOGNUMBER="AMG 099")
        self.row = {"AlbumArtistFolder": "Kreator", "AlbumFolder": self.d.name, "FullPath": str(self.d),
                    "Status": "untagged", "OcrCandidateImages": ""}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_album(self, lens_ms):
        args = types.SimpleNamespace(ocr=False, use_discogs=False)
        return ma.process(self.row, LabelHttp(lens_ms), CFG, None, args)

    def test_found_by_label_and_scored(self):
        o = self.run_album([10000, 20000, 30000])
        self.assertEqual((o["status"], o["best"]["id"]), ("auto", "mb-amg"), o["why"])

    def test_another_edition_found_by_label_is_not_auto(self):
        o = self.run_album([10000, 20000, 30000, 40000])        # a bonus track: not your copy
        self.assertTrue(any(a.startswith("mb:mb-amg ") for a in o["alts"]), o["alts"])
        self.assertNotEqual(o["status"], "auto", o["why"])


class DocumentLabels(unittest.TestCase):
    """A label only a document names (here one in a subfolder) is searched with the album's
    catalogue number: in the first search, or in the documents step when the tags' own two
    labels took the first search's places."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "Kreator" / "Unnamed Rip"
        (self.d / "extras").mkdir(parents=True)
        self.row = {"AlbumArtistFolder": "Kreator", "AlbumFolder": self.d.name, "FullPath": str(self.d),
                    "Status": "untagged", "OcrCandidateImages": ""}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_album(self, doc_label, doc_catno="", discogs=False, doc="details.txt", **tags):
        for i in range(3):
            tone(self.d / f"{i + 1:02d}.flac", secs=10 * (i + 1), title=f"T{i}", album="Endorama",
                 albumartist="Kreator", tracknumber=str(i + 1), **tags)
        if doc.endswith(".txt"):
            text = f"Ripped by me\nLabel: {doc_label}\n" + (f"Cat#: {doc_catno}\n" if doc_catno else "")
        else:                                   # a page the notes are not read from: a bare number
            text = f"Ripped by me\nLabel: {doc_label}\n" + (f"{doc_catno}\n" if doc_catno else "")
        (self.d / "extras" / doc).write_text(text)
        h = LabelHttp([10000, 20000, 30000], label=doc_label, catno=doc_catno or "AMG 099")
        dm = ma.load_plugin(PLUGIN_DIR) if discogs else None
        o = ma.process(self.row, h, CFG, dm, types.SimpleNamespace(ocr=False, use_discogs=discogs))
        return o, h

    def found(self, o):
        return (o["best"] or {}).get("id") == "mb-amg" or any(a.startswith("mb:mb-amg ") for a in o["alts"])

    def test_document_label_searched_with_the_tag_catno(self):
        o, h = self.run_album("Art Music", CATALOGNUMBER="AMG 099")
        self.assertIn('label:"Art Music" AND catno:"AMG 099"', h.queries)
        self.assertTrue(self.found(o), (o["best"], o["alts"]))
        self.assertNotIn("codes_from_ocr", o)               # a label is not a code read

    def test_document_label_first_when_the_tags_name_two(self):
        o, h = self.run_album("Roadrunner", CATALOGNUMBER="AMG 099", LABEL="Nuclear Blast; Century Media")
        self.assertIn('label:"Roadrunner" AND catno:"AMG 099"', h.queries)
        self.assertTrue(self.found(o), (o["best"], o["alts"]))
        # the tag labels' pairs were searched once, in the first search, not again
        self.assertEqual(h.queries.count('label:"Nuclear Blast" AND catno:"AMG 099"'), 1)

    def test_document_label_on_discogs_and_asin_not_asked_again(self):
        o, h = self.run_album("Roadrunner", discogs=True, CATALOGNUMBER="AMG 099",
                              LABEL="Nuclear Blast; Century Media", ASIN="B00004TZXL")
        self.assertTrue(self.found(o), (o["best"], o["alts"]))
        searches = [u for u in h.discogs_urls if "/database/search" in u]
        self.assertTrue(any("label=Roadrunner&catno=AMG 099" in u for u in searches), searches)
        # the documents step asks only what is new: the album's ASIN and its tag-label
        # pairs were asked in the first search and not again
        self.assertEqual(h.queries.count("asin:B00004TZXL"), 1, h.queries)
        self.assertEqual(sum("label=Nuclear Blast&catno=AMG 099" in u for u in searches), 1, searches)
        self.assertEqual(sum("catno=AMG 099&artist=" in u for u in searches), 1, searches)

    def test_document_label_and_new_catno_are_paired_with_the_tag_labels_too(self):
        o, h = self.run_album("Roadrunner", doc_catno="RR 1234-2", CATALOGNUMBER="AMG 099",
                              LABEL="Nuclear Blast; Century Media")
        self.assertIn('label:"Roadrunner" AND catno:"RR 1234-2"', h.queries)
        self.assertIn('label:"Nuclear Blast" AND catno:"RR 1234-2"', h.queries)
        # R3: a "Cat#:" line of the notes is read with the folder (folder-name weight), so it
        # is the album's own number from the start, not a code the documents step found (the
        # documents step's own pairing: test_a_number_only_a_document_names_...)
        self.assertIsNone(o.get("codes_from_ocr"))
        self.assertTrue(self.found(o), (o["best"], o["alts"]))

    def test_a_number_only_a_document_names_is_paired_with_every_label(self):
        # the documents step itself: "RR 1234-2" on a page the notes are not read from (.rtf)
        o, h = self.run_album("Roadrunner", doc_catno="RR 1234-2", doc="details.rtf", CATALOGNUMBER="AMG 099",
                              LABEL="Nuclear Blast; Century Media")
        self.assertEqual(o.get("codes_from_ocr"), "RR 1234-2")
        self.assertIn('label:"Roadrunner" AND catno:"RR 1234-2"', h.queries)
        self.assertIn('label:"Nuclear Blast" AND catno:"RR 1234-2"', h.queries)
        self.assertTrue(self.found(o), (o["best"], o["alts"]))

    def test_document_label_without_a_catno_is_not_searched(self):
        o, h = self.run_album("Art Music")
        self.assertFalse(any(x.startswith("label:") for x in h.queries), h.queries)
        self.assertNotIn("codes_from_ocr", o)


class BarcodeSourceNote(unittest.TestCase):
    def test_the_result_says_a_barcode_came_from_a_upc_tag(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "Kreator" / "Unnamed Rip"
            d.mkdir(parents=True)
            tone(d / "01.flac", title="T0", album="Endorama", albumartist="Kreator", tracknumber="1", UPC=UPC)
            row = {"AlbumArtistFolder": "Kreator", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            o = ma.process(row, Spy(), CFG, None, types.SimpleNamespace(ocr=False, use_discogs=False))
            self.assertEqual(o["barcode_sources"], ["UPC"])
            self.assertIn("barcode read from the UPC tag, not BARCODE", ma.to_row(o)["Notes"])
            # not a reason: an album that finds nothing is still filed as "no candidate"
            self.assertFalse(any("barcode read from" in w for w in o["why"]), o["why"])
            words = "; ".join(o["why"]) or "no candidates found"
            self.assertEqual(reasons.classify(words), ["no_candidate"])
            self.assertNotIn("barcode read from", ma.to_row(o)["WhyNotAuto"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_found_album_shows_it_as_evidence_not_a_doubt(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "Kreator" / "Unnamed Rip"
            d.mkdir(parents=True)
            for i in range(3):
                tone(d / f"{i + 1:02d}.flac", secs=10 * (i + 1), title=f"T{i}", album="Endorama",
                     albumartist="Kreator", tracknumber=str(i + 1), LABEL="Art Music",
                     CATALOGNUMBER="AMG 099", UPC=UPC)
            row = {"AlbumArtistFolder": "Kreator", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            o = ma.process(row, LabelHttp([10000, 20000, 30000]), CFG, None,
                           types.SimpleNamespace(ocr=False, use_discogs=False))
            self.assertEqual(o["best"]["id"], "mb-amg", o["alts"])
            self.assertIn("barcode read from the UPC tag, not BARCODE", o["best"]["notes"])
            self.assertFalse(any("barcode read from" in w for w in o["why"]), o["why"])
            self.assertIn("barcode read from the UPC tag", ma.to_row(o)["Evidence"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def dg_release(rid, barcode="", catno="X"):
    return {"id": rid, "title": "First Light", "year": 2010, "country": "Canada",
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": "Artist Alpha"}],
            "labels": [{"name": "L", "catno": catno}], "formats": [{"name": "CD", "qty": "1"}],
            "identifiers": [{"type": "Barcode", "value": barcode}] if barcode else [],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": "3:00"}
                          for i in range(3)]}


class MasterHttp:
    """One search hit with a master of 40 versions; version 120 carries the barcode, and
    the versions in `catno_x1` are listed (and released) with catalogue number X1."""
    def __init__(self, with_barcode=120, catno_x1=()):
        self.with_barcode = with_barcode
        self.catno_x1 = set(catno_x1)
        self.fetched = []

    def get_json(self, url, host):
        if "/database/search" in url:
            return {"results": [{"id": 100, "title": "Artist Alpha - First Light", "year": "2010",
                                 "master_id": 9}]}
        if "/masters/9/versions" in url:
            return {"versions": [{"id": 100 + i, "format": "CD", "country": "Canada",
                                  "catno": "X1" if 100 + i in self.catno_x1 else "Y"} for i in range(40)]}
        rid = int(url.rsplit("/", 1)[1])
        self.fetched.append(rid)
        if rid == 100:                                      # the search hit: two tracks, not yours
            return dict(dg_release(100), tracklist=dg_release(100)["tracklist"][:2])
        return dg_release(rid, barcode=UPC if rid == self.with_barcode else "",
                          catno="X1" if rid in self.catno_x1 else "Y")


class MasterVersionsCap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN_DIR)

    def test_no_code_fetches_six(self):
        h = MasterHttp()
        a = album(tracks=album()["tracks"][:3])
        a["tracks"].append({"disc": 1, "num": 4, "title": "T3", "secs": 180.0})   # no version has 4
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertEqual(len([r for r in h.fetched if r != 100]), 6)

    def test_barcode_looks_further_and_stops_when_found(self):
        h = MasterHttp(with_barcode=120)
        a = album(barcodes={UPC})
        a["tracks"].append({"disc": 1, "num": 4, "title": "T3", "secs": 180.0})
        out = ma.discogs_search(h, a, CFG, self.dm)
        versions = [r for r in h.fetched if r != 100]
        self.assertIn(120, versions)
        self.assertEqual(max(versions), 120)                # stopped at the one with your barcode
        self.assertIn("120", [str(c["id"]) for c in out])   # a candidate, scored like the others

    def test_cap_is_25(self):
        h = MasterHttp(with_barcode=0)
        a = album(barcodes={UPC})
        a["tracks"].append({"disc": 1, "num": 4, "title": "T3", "secs": 180.0})
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertEqual(len([r for r in h.fetched if r != 100]), 25)

    def test_catno_only_cap_is_25(self):
        h = MasterHttp(with_barcode=0)
        a = album(catnos={"X1"})
        a["tracks"].append({"disc": 1, "num": 4, "title": "T3", "secs": 180.0})
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertEqual(len([r for r in h.fetched if r != 100]), 25)

    def test_every_version_sharing_your_catno_is_fetched(self):
        # nine versions share X1: after the first matches, the rest are still compared,
        # and fetching stops before the first version that does not share it
        shared = set(range(110, 119))
        h = MasterHttp(with_barcode=0, catno_x1=shared)
        a = album(catnos={"X1"})
        a["tracks"].append({"disc": 1, "num": 4, "title": "T3", "secs": 180.0})
        out = ma.discogs_search(h, a, CFG, self.dm)
        versions = [r for r in h.fetched if r != 100]
        self.assertEqual(set(versions), shared)
        self.assertEqual(len(versions), 9)
        self.assertTrue(shared <= {c["id"] for c in out} | {int(c["id"]) for c in out})


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
