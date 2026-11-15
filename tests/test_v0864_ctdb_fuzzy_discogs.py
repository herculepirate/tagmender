"""A fuzzy CUETools (CTDB) link to a Discogs release is no disc-ID proof (TOC-2), and a label
code with one digit in the folder name is a catalogue number (CAT-3), in docs/MATCHING_RULES.md.

Modelled on a 2 CD + DVD box listed twice, a US and a UK edition: your rip logs' disc IDs are
attached in MusicBrainz to the UK entry only, which links the UK Discogs entry; CTDB's fuzzy
lookup lists both Discogs entries with a "relevance" (a CD of about these lengths). The files'
tags, another tagger's, carry the US barcode (weak: BAR-5, TAG-3). Before, every CTDB Discogs
link counted as the disc ID, the two Discogs entries tied and the country order chose the US one.

Controls: a CTDB entry without "relevance" (the disc's own, as every older fixture writes it)
still counts as the disc ID; a Discogs release linked only by a fuzzy MusicBrainz match inherits
nothing; "CD 1", "Vol 1", "Disc 1" stay no catalogue numbers. Synthetic releases: no network,
no library files.
"""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=["US", "GB"])
DM = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))


def ean(body12):
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - s % 10) % 10)


US_BARCODE = ean("509990000106")
UK_BARCODE = ean("509990000204")
LENS = (200.0, 305.0, 410.0, 250.0, 180.0)
FOLDER = "[2009.03.23, LANTCDX 1] Copper Fields [2 CD + DVD, box set, ltd.]"


def album(**kw):
    a = {"artist": "Northern Lanterns", "title": "Copper Fields", "year": 2009, "discs": 1,
         "folder": FOLDER,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)],
         # the tags another tagger wrote: the US box's barcode
         "barcodes": {US_BARCODE}, "tag_barcodes": {US_BARCODE}, "tags_by_tagger": True, "catnos": set(),
         # your disc ID: attached to the UK MusicBrainz entry; CTDB lists both Discogs boxes fuzzily
         "toc_releases": {"mb-uk", "mb-us"}, "exact_toc_releases": {"mb-uk"},
         "toc_discogs": {"2000001", "2000002"}, "toc_discogs_fuzzy": {"2000001": 32, "2000002": 32}}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x", "url": "u", "artist": "Northern Lanterns", "title": "Copper Fields",
         "year": 2009, "country": "", "label": "Lantern", "catnos": set(), "barcodes": set(),
         "disc_count": 1, "formats": {"cd"},
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)]}
    c.update(kw)
    return c


def us_dg():
    return cand(source="discogs", id="2000002", country="US", barcodes={US_BARCODE}, catnos={"lantcdn1"})


def uk_dg():
    return cand(source="discogs", id="2000001", country="UK", barcodes={UK_BARCODE}, catnos={"lantcdx1"})


def mb_uk():
    return cand(id="mb-uk", country="XE", barcodes={UK_BARCODE}, catnos={"lantcdx1"}, discogs_ids=["2000001"])


def mb_us():
    return cand(id="mb-us", country="US", barcodes={US_BARCODE}, catnos={"lantcdn1"}, discogs_ids=["2000002"])


def run(a, *cands):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    r = mc.classify(scored, CFG)
    r["scored"] = scored
    return r


class FakeHttp:
    def __init__(self, xml="", releases=None):
        self.xml, self.releases, self.asked = xml, releases or {}, []

    def get_json(self, url, host):
        self.asked.append(url)
        if "api.discogs.com/releases/" in url:
            return self.releases.get(url.rsplit("/", 1)[-1])
        return None

    def get_text(self, url, host):
        return self.xml


class TOC2_CtdbRelevance(unittest.TestCase):
    def test_TOC2_entries_with_a_relevance_are_fuzzy_without_one_exact(self):
        xml = ('<metadata source="discogs" id="2000001" relevance="32"/>'
               '<metadata source="discogs" id="2000002" relevance="79"/>'
               '<metadata source="discogs" id="2000003" relevance="100"/>'
               '<metadata source="discogs" id="2000004"/>')
        a = {"tocs": {1: "1 2 32172 150 8086"}}
        ma.toc_releases(FakeHttp(xml), a)
        self.assertEqual(a["toc_discogs"], {"2000001", "2000002", "2000003", "2000004"})
        # (relevance 100 is still CTDB's fuzzy score, never the disc's own)
        self.assertEqual(a["toc_discogs_fuzzy"], {"2000001": 32, "2000002": 79, "2000003": 100})

    def test_TOC2_exact_on_one_disc_is_exact(self):
        class H(FakeHttp):
            n = 0

            def get_text(self, url, host):
                H.n += 1
                return ('<metadata source="discogs" id="2000001"/>' if H.n == 1
                        else '<metadata source="discogs" id="2000001" relevance="40"/>')
        a = {"tocs": {1: "1 2 32172 150 8086", 2: "1 2 40000 150 9000"}}
        ma.toc_releases(H(), a)
        self.assertEqual(a["toc_discogs"], {"2000001"})
        self.assertEqual(a["toc_discogs_fuzzy"], {})

    def test_TOC2_fetch_order_is_exact_then_tags_then_relevance(self):
        a = {"toc_discogs": {"9", "30", "100", "2000"}, "toc_discogs_fuzzy": {"9": 40, "30": 79, "2000": 71},
             "tag_discogs_links": {"55"}}
        self.assertEqual(ma.toc_discogs_order(a), ["100", "55", "30", "2000", "9"])
        # (older callers: no fuzzy marks, every CTDB link exact, by number)
        self.assertEqual(ma.toc_discogs_order({"toc_discogs": {"30", "100", "9"}}), ["9", "30", "100"])


class TOC2_FuzzyLinkIsNoProof(unittest.TestCase):
    def test_TOC2_a_fuzzy_ctdb_link_gives_only_the_fuzzy_clue(self):
        s = mc.score_candidate(album(), us_dg(), CFG)
        self.assertNotIn("disc TOC", s["clues"])
        self.assertIn("disc TOC (fuzzy)", s["clues"])

    def test_TOC2_an_exact_ctdb_link_still_counts(self):
        # control: no "relevance" (every older fixture) keeps the full clue
        s = mc.score_candidate(album(toc_discogs_fuzzy={}), us_dg(), CFG)
        self.assertEqual(s["clues"]["disc TOC"], mc.TOC_CLUE)
        s = mc.score_candidate(album(toc_discogs_fuzzy=None), us_dg(), CFG)
        self.assertEqual(s["clues"]["disc TOC"], mc.TOC_CLUE)

    def test_TOC2_neither_box_is_proven_by_a_fuzzy_ctdb_link(self):
        # before: both had "disc TOC", and with it the US pick read "your disc ID" (via=disc_id)
        dg = run(album(), us_dg(), uk_dg())
        self.assertFalse([s["cand"]["id"] for s in dg["scored"] if "disc TOC" in s["clues"]])


class TOC2_InheritedFromTheDiscIdRelease(unittest.TestCase):
    def test_TOC2_the_discogs_entry_the_disc_id_release_links_inherits_the_proof(self):
        a = album()
        mb = run(a, mb_uk(), mb_us())
        self.assertEqual(ma.disc_id_discogs(a, mb), {"2000001"})
        self.assertNotIn("2000001", a["toc_discogs_fuzzy"])
        self.assertIn("2000002", a["toc_discogs_fuzzy"])
        uk = mc.score_candidate(a, uk_dg(), CFG)
        us = mc.score_candidate(a, us_dg(), CFG)
        self.assertEqual(uk["clues"]["disc TOC"], mc.TOC_CLUE)
        self.assertNotIn("disc TOC", us["clues"])

    def test_TOC2_a_fuzzy_musicbrainz_match_passes_nothing_on(self):
        # control: the US MusicBrainz entry is only a fuzzy match of your disc
        a = album()
        mb = run(a, mb_us())
        self.assertEqual(ma.disc_id_discogs(a, mb), set())
        self.assertIn("2000002", a["toc_discogs_fuzzy"])
        self.assertNotIn("disc TOC", mc.score_candidate(a, us_dg(), CFG)["clues"])

    def test_TOC2_no_disc_id_known_exactly_passes_nothing_on(self):
        a = album(exact_toc_releases=set())
        self.assertEqual(ma.disc_id_discogs(a, run(a, mb_uk(), mb_us())), set())

    def test_TOC2_a_linked_release_the_search_missed_is_fetched(self):
        det = {"id": 2000001, "title": "Copper Fields", "artists": [{"name": "Northern Lanterns"}],
               "year": 2009, "country": "UK", "formats": [{"name": "CD", "qty": "1"}],
               "labels": [{"name": "Lantern", "catno": "LANTCDX 1"}], "identifiers": [],
               "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": f"{int(s) // 60}:{int(s) % 60:02d}",
                              "type_": "track"} for i, s in enumerate(LENS)]}
        http = FakeHttp(releases={"2000001": det})
        have = [us_dg()]
        out = ma.with_discogs_ids(http, DM, have, {"2000001", "2000002"})
        self.assertEqual([str(c["id"]) for c in out], ["2000002", "2000001"])
        self.assertEqual(http.asked, ["https://api.discogs.com/releases/2000001"])

    def test_TOC2_a_failed_fetch_leaves_that_one_out(self):
        class Down(FakeHttp):
            def get_json(self, url, host):
                raise OSError("down")
        self.assertEqual(ma.with_discogs_ids(Down(), DM, [], {"2000001"}), [])

    def test_TOC2_combined_the_uk_box_and_its_musicbrainz_twin(self):
        # the folder's number and the disc IDs: the UK box on both databases, sure
        a = album(catnos={"lantcdx1"}, catnos_from_folder={"lantcdx1"})
        mb = run(a, mb_uk(), mb_us())
        ma.disc_id_discogs(a, mb)
        dg = run(a, us_dg(), uk_dg())
        self.assertEqual((dg["status"], str(dg["best"]["cand"]["id"])), ("auto", "2000001"), dg["why"])
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], str(out["best"]["cand"]["id"])), ("auto", "2000001"), out.get("why"))
        self.assertEqual(out.get("mb_twin"), "mb-uk")


class BAR5_TaggersBarcodeAgainstYourDiscId(unittest.TestCase):
    def test_BAR5_your_exact_disc_id_outweighs_another_taggers_barcode(self):
        # no folder number: MusicBrainz's entry carrying your disc ID, not the tags' barcode one
        r = run(album(), mb_us(), mb_uk())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "mb-uk"), r["why"])
        uk = next(s for s in r["scored"] if s["cand"]["id"] == "mb-uk")
        self.assertFalse(uk["conflict"])
        self.assertIn("barcode in the tags another tagger wrote differs (not proof of the pressing)", uk["notes"])

    def test_BAR5_your_own_barcode_still_differs(self):
        # control: a barcode not another tagger's (the rip's own tags) stays a different product
        r = run(album(tags_by_tagger=False), mb_us(), mb_uk())
        uk = next(s for s in r["scored"] if s["cand"]["id"] == "mb-uk")
        self.assertTrue(uk["conflict"])
        self.assertEqual(r["best"]["cand"]["id"], "mb-us")

    def test_BAR5_without_your_disc_id_or_number_the_conflict_stands(self):
        # control: a release with neither your disc ID nor your folder's number
        s = mc.score_candidate(album(exact_toc_releases=set()), mb_uk(), CFG)
        self.assertTrue(s["conflict"])


class CAT3_OneDigitLabelCode(unittest.TestCase):
    def test_CAT3_a_label_code_and_one_digit_is_read(self):
        self.assertEqual(mc.catno_hints(FOLDER), ["LANTCDX 1"])
        self.assertEqual(mc.catno_hints("Copper Fields [XLCD 1]"), ["XLCD 1"])

    def test_CAT3_disc_volume_and_part_numbers_stay_out(self):
        for text in ("[CD 1]", "[Disc 1]", "[DISC 1]", "[Vol 1]", "[VOL 1]", "[Volume 2]", "[PART 2]",
                     "(Disk 2)", "[TAKE 1]", "[SPECIAL 2]", "[Lantcdx 1]", "[2 CD + DVD, box set 1]",
                     "[LANTCDX 1 x]", "[BOX SET 2]", "[LIVE 1]"):
            self.assertEqual(mc.catno_hints(text), [], text)

    def test_CAT3_the_folder_number_drops_the_tags_barcode_pressing(self):
        # MusicBrainz side: the US entry carries the tags' barcode (another tagger's, weak), the UK
        # entry your disc ID and your folder's number
        a = album(catnos={"lantcdx1"}, catnos_from_folder={"lantcdx1"})
        r = run(a, mb_us(), mb_uk())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "mb-uk"), r["why"])

    def test_CAT3_tags_naming_the_ruled_out_box_do_not_hold_a_disc_id_pick(self):
        # the tags' release ID names the US box, which the folder's number rules out: your exact
        # disc ID on the UK box says which is right, so it is no "nothing else says" hold
        a = album(catnos={"lantcdx1"}, catnos_from_folder={"lantcdx1"}, tag_release_mb={"mb-us"})
        r = run(a, mb_us(), mb_uk())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "mb-uk"), r["why"])
        # control: without your disc ID on it, the hold stands
        a = album(catnos={"lantcdx1"}, catnos_from_folder={"lantcdx1"}, tag_release_mb={"mb-us"},
                  exact_toc_releases=set(), toc_releases=set())
        r = run(a, mb_us(), mb_uk())
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(any("written by another tagger" in w for w in r["why"]), r["why"])


if __name__ == "__main__":
    unittest.main()
