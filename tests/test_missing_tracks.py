"""Albums with tracks missing from the folder (Mekong Delta - Pictures At An Exhibition: 24 of
the CD's 32), a shop's barcode sticker on the scans, and a TOC from a rip log or CDTOC tag
whose disc the folder holds only part of.

Run:  python3 tests/test_missing_tracks.py   (offline; needs ffmpeg and the Discogs plugin clone,
TAGMENDER_DISCOGS_PLUGIN or plugins_builtin/discogs)
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (sets up XDG folders, the plugin)
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENS = (200.0, 305.0, 410.0, 150.0, 260.0)


def album(nums, barcodes=(), catnos=(), lens=LENS, **kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": n, "title": f"T{n}", "secs": lens[n - 1]} for n in nums],
         "barcodes": set(barcodes), "catnos": set(catnos)}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "CA", "label": "L", "catnos": {"cat12"}, "barcodes": {"0801837013429"},
         "disc_count": 1, "tracks": [{"disc": 1, "title": f"T{i + 1}", "secs": s} for i, s in enumerate(LENS)]}
    c.update(kw)
    return c


def decide(a, cands):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    return mc.classify(scored, CFG)


class Numbers(unittest.TestCase):
    def test_compact_lists(self):
        self.assertEqual(mc.compact_numbers([2, 4, 5, 7, 8, 9, 10, 14]), "2, 4, 5, 7-10, 14")
        self.assertEqual(mc.compact_numbers([1]), "1")
        self.assertEqual(mc.missing_text({1: [2, 4, 5, 7, 8, 9, 10, 14]}), "2, 4, 5, 7-10, 14")
        self.assertEqual(mc.missing_text({1: [3], 2: [5]}), "1:3, 2:5")

    def test_slot_view(self):
        v = mc.slot_view(album([1, 3, 5]), cand())
        self.assertEqual((v["slots"], v["missing"], v["full_tracks"]), ([0, 2, 4], {1: [2, 4]}, 5))
        self.assertEqual([t["title"] for t in v["tracks"]], ["T1", "T3", "T5"])

    def test_no_slot_view_when_files_collide_or_lack_a_number(self):
        a = album([1, 3, 3])
        self.assertIsNone(mc.slot_view(a, cand()))
        a = album([1, 3])
        a["tracks"][1]["num"] = 0
        self.assertIsNone(mc.slot_view(a, cand()))
        self.assertIsNone(mc.slot_view(album([1, 3, 6], lens=LENS + (99.0,)), cand()))

    def test_multi_disc_slots(self):
        tracks = [{"disc": d, "title": f"D{d}T{i}", "secs": 100.0 + 10 * i} for d in (1, 2) for i in range(1, 4)]
        a = {"artist": "A", "title": "B", "year": 0, "discs": 2, "barcodes": set(), "catnos": set(),
             "tracks": [{"disc": 1, "num": 1, "title": "", "secs": 110.0}, {"disc": 1, "num": 2, "title": "", "secs": 120.0},
                        {"disc": 2, "num": 1, "title": "", "secs": 110.0}, {"disc": 2, "num": 3, "title": "", "secs": 130.0}]}
        v = mc.slot_view(a, {"tracks": tracks, "disc_count": 2})
        self.assertEqual((v["slots"], mc.missing_text(v["missing"])), ([0, 1, 3, 5], "1:3, 2:2"))


class Matching(unittest.TestCase):
    def test_proven_release_with_missing_tracks_is_exact(self):
        r = decide(album([1, 3, 5], barcodes={"0801837013429"}), [cand()])
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertEqual(r["best"]["missing"], {1: [2, 4]})
        self.assertEqual(r["best"]["cand"]["slots"], [0, 2, 4])
        self.assertTrue(any("tracks 2, 4 of 5 are missing" in n for n in r["best"]["notes"]))
        summary = ma.summarize(r)
        self.assertEqual((summary["slots"], summary["missing"], summary["release_tracks"]), ([0, 2, 4], {"1": [2, 4]}, 5))

    def test_disc_toc_proves_it_too(self):
        a = album([1, 3, 5], toc_releases={"x1"}, exact_toc_releases={"x1"})   # (exact: a fuzzy one proves nothing)
        r = decide(a, [cand(barcodes=set())])
        self.assertEqual(r["status"], "auto", r["why"])

    def test_title_alone_never_tags_missing_tracks(self):
        r = decide(album([1, 3, 5]), [cand(barcodes=set(), catnos=set())])
        self.assertNotEqual(r["status"], "auto")
        self.assertIn("track count differs", r["why"])
        self.assertNotIn("durations unavailable", r["why"])          # the lengths were compared
        self.assertNotIn("missing", r["best"])
        self.assertTrue(any("fit 3 of their 3 own tracks" in n for n in r["best"]["notes"]), r["best"]["notes"])

    def test_catalogue_number_alone_is_not_enough(self):
        r = decide(album([1, 3, 5], catnos={"cat12"}), [cand(barcodes=set())])
        self.assertNotEqual(r["status"], "auto")

    def test_a_file_off_its_slot_keeps_it_from_being_tagged(self):
        a = album([1, 3, 5], barcodes={"0801837013429"})
        a["tracks"][1]["secs"] = 300.0                                # not track 3's 410 s
        r = decide(a, [cand()])
        self.assertNotEqual(r["status"], "auto")
        self.assertNotIn("missing", r["best"])

    def test_titles_typed_with_the_works_name_still_fit(self):
        a = album([1, 3, 5], barcodes={"0801837013429"})
        a["tracks"][0]["title"] = "First Light for Group: T1"
        self.assertEqual(decide(a, [cand()])["status"], "auto")
        a["tracks"][1]["title"] = "Something else"
        self.assertNotEqual(decide(a, [cand()])["status"], "auto")


class Sticker(unittest.TestCase):
    def test_unlisted_barcode_with_the_catalogue_number_agreeing_is_ignored(self):
        a = album([1, 2, 3, 4, 5], barcodes={"5020083807827"}, catnos={"cat12"},
                  unlisted_barcodes={"5020083807827"})
        s = mc.score_candidate(a, cand(), CFG)
        self.assertFalse(s["conflict"])
        self.assertIn("barcode 5020083807827 on the scans is on no release (a shop sticker?): ignored", s["notes"])

    def test_a_listed_barcode_or_nothing_else_agreeing_still_conflicts(self):
        a = album([1, 2, 3, 4, 5], barcodes={"5020083807827"}, catnos={"cat12"})
        self.assertTrue(mc.score_candidate(a, cand(), CFG)["conflict"])
        a = album([1, 2, 3, 4, 5], barcodes={"5020083807827"}, unlisted_barcodes={"5020083807827"})
        self.assertTrue(mc.score_candidate(a, cand(), CFG)["conflict"])

    def test_unlisted_scan_barcodes(self):
        class Http:
            db = None

            def get_json(self, url, host):
                return {"data": []} if host == "deezer" else {"results": [], "resultCount": 0}
        a = {"barcodes": {"5020083807827", "0801837013429"}, "tag_barcodes": set()}
        res = {"scored": [{"cand": cand()}]}
        self.assertEqual(ma.unlisted_scan_barcodes(Http(), a, (res, None)), {"5020083807827"})
        a["tag_barcodes"] = {"5020083807827"}                 # the tags' barcode is your copy's own
        self.assertEqual(ma.unlisted_scan_barcodes(Http(), a, (res, None)), set())


    def test_a_barcode_is_unlisted_only_when_both_databases_answered(self):
        self.assertTrue(ma.both_databases_answered(True, True, {}))
        self.assertFalse(ma.both_databases_answered(True, False, {}))       # MusicBrainz only
        self.assertFalse(ma.both_databases_answered(False, True, None))     # Discogs only
        self.assertFalse(ma.both_databases_answered(True, True, {"discogs": "outage"}))
        self.assertFalse(ma.both_databases_answered(True, True, {"musicbrainz": "rate"}))


class Rescore(unittest.TestCase):
    """Scoring a match with tracks missing again (a shop's candidates added, a barcode found
    unlisted) keeps which tracks are missing."""

    def _missing_match(self):
        a = album([1, 3, 5], barcodes={"0801837013429"})
        scored = [mc.score_candidate(a, c, CFG) for c in (cand(), cand(id="x2", barcodes=set(), catnos=set(),
                                                                        title="Other"))]
        r = dict(mc.classify(scored, CFG), scored=scored, source="mb")
        self.assertEqual((r["status"], r["best"].get("missing")), ("auto", {1: [2, 4]}))
        return a, r

    def test_rescored_all_keeps_the_missing_tracks(self):
        a, r = self._missing_match()
        r2 = ma._rescored_all(r, a, CFG)
        self.assertEqual((r2["status"], r2["best"].get("missing")), ("auto", {1: [2, 4]}))
        self.assertEqual(ma.summarize(r2).get("slots"), [0, 2, 4])

    def test_rescored_with_new_candidates_keeps_the_missing_tracks(self):
        a, r = self._missing_match()
        r3 = ma._rescored(r, [cand(id="x3", title="Zzz", barcodes=set(), catnos=set())], "mb", a, CFG)
        self.assertEqual(len(r3["scored"]), 3)
        self.assertEqual(r3["best"].get("missing"), {1: [2, 4]})
        self.assertEqual(ma.summarize(r3).get("slots"), [0, 2, 4])
        self.assertIs(ma._rescored(r, [cand()], "mb", a, CFG), r)     # the whole release is not new

    def test_scored_again_after_fingerprints_keeps_the_missing_tracks(self):
        # two pressings of the files' 3 of 5 tracks: unsure; fingerprints pointing at one still
        # leave it unsure (0.8.3: they name recordings, which both pressings carry)
        a = album([1, 3, 5], barcodes={"0801837013429"})
        scored = [mc.score_candidate(a, c, CFG) for c in (cand(), cand(id="x2", country="DE"))]
        r = dict(mc.classify(scored, CFG), scored=scored, source="mb")
        self.assertEqual((r["status"], r["best"].get("missing")), ("review", {1: [2, 4]}))
        a["fp_releases"] = {"x1"}
        r2 = ma._scored_again(r, [], "mb", a, CFG)
        self.assertEqual((r2["status"], r2["best"].get("missing")), ("review", {1: [2, 4]}))
        self.assertEqual(ma.summarize(r2).get("slots"), [0, 2, 4])
        self.assertEqual(len(ma._scored_again(r, [cand(id="x3", title="Zzz")], "mb", a, CFG)["scored"]), 3)


class FolderSays(unittest.TestCase):
    def test_a_release_carrying_the_folder_number_is_not_told_it_lacks_it(self):
        a = album([1, 3, 5], catnos={"irscd993626"}, catnos_from_folder={"irscd993626"},
                  catnos_typed={"irscd993626"})
        other = cand(id="x2", catnos={"cdp123"}, barcodes=set())
        mine = cand(catnos={"irscd993626"}, barcodes=set())
        scored = [mc.score_candidate(a, c, CFG) for c in (other, mine)]
        r = mc.classify(scored, CFG)
        s = next(x for x in scored if x["cand"]["id"] == "x1")
        self.assertIn("catalogue number (your rip's notes) agrees", s["notes"])
        if r["best"]["cand"]["id"] == "x1":
            self.assertFalse(any("folder catalogue number is not on it" in w for w in r["why"]), r["why"])


class Tocs(unittest.TestCase):
    TOC = "1 3 13650 150 4650 9150"            # three tracks of 60 s

    def test_cdtoc_tag(self):
        self.assertEqual(ma.cdtoc_tag_toc("3+96+122A+23BE+3552"), self.TOC)
        self.assertEqual(ma.cdtoc_tag_toc("nonsense"), "")
        self.assertEqual(ma.cdtoc_tag_toc("3+96+122A"), "")

    def test_a_log_of_the_whole_disc_fits_the_files_left(self):
        tracks = [{"num": 1, "secs": 60.0}, {"num": 3, "secs": 60.0}]
        self.assertTrue(ma.toc_fits_slots(self.TOC, tracks))
        self.assertFalse(ma.toc_fits_slots(self.TOC, [{"num": 1, "secs": 60.0}, {"num": 3, "secs": 61.0}]))
        self.assertFalse(ma.toc_fits_slots(self.TOC, [{"num": 1, "secs": 60.0}, {"num": 1, "secs": 60.0}]))
        infos = [("a", dict(t, disc_eff=1)) for t in tracks]
        kept, foreign = ma.fitting_logs(infos, [{"toc": self.TOC}])
        self.assertEqual((len(kept), foreign), (1, []))
        self.assertEqual(ma.merge_tocs(infos, {1: self.TOC}), {1: self.TOC})
        self.assertEqual(ma.tag_tocs([("a", dict(t, disc_eff=1, cdtoc="3+96+122A+23BE+3552")) for t in tracks]),
                         {1: self.TOC})


class Engine(Base):
    """The whole path: identified with tracks missing, planned, applied, read back."""

    def test_present_files_take_their_own_tracks(self):
        d = self.album("Band M", "Album M", barcode="0123456789012")
        os.remove(d / "02.flac")
        http = FakeHttp(mb=[mb_release("mb-m", "Band M", "Album M")])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-m"), p.why)
        self.assertIn("incomplete_album", p.reason_codes)
        self.assertEqual([t["tracknumber"] for t in p.targets], [["1"], ["3"]])
        self.assertEqual({t["totaltracks"][0] for t in p.targets}, {"3"})
        self.assertEqual([t["title"] for t in p.targets], [["T0"], ["T2"]])
        # HF-5: the gaps are no tag of their own; the plan's notes tell them (below)
        self.assertFalse([t for t in p.targets if "tagmender_missing" in t])
        self.assertTrue(any("tracks 2 of 3 are missing" in e for e in p.evidence), p.evidence)
        plan = self.engine(FakeHttp(mb=[mb_release("mb-m", "Band M", "Album M")])).plan(d)
        self.assertEqual(plan["albums"][0]["status"], "ready", pl.preview(plan))
        self.assertTrue(any("tracks 2 of the release are not in this folder" in n for n in plan["albums"][0]["notes"]),
                        plan["albums"][0]["notes"])
        res = executor.Executor(plan).run()
        self.assertEqual(res["counts"], {"done": 1})
        t = tagio.read(d / "03.flac")
        self.assertEqual((t["tracknumber"], t.get("tagmender_missing"), t["musicbrainz_albumid"]), (["3"], None, ["mb-m"]))  # HF-5
        self.assertEqual(t.get("totaltracks") or t.get("tracktotal"), ["3"])
        again = self.engine(FakeHttp(mb=[mb_release("mb-m", "Band M", "Album M")])).plan(d)
        self.assertEqual(again["albums"][0]["status"], "unchanged", pl.preview(again))

    def test_without_proof_it_stays_unresolved(self):
        d = self.album("Band N", "Album N")
        os.remove(d / "02.flac")
        rel = mb_release("mb-n", "Band N", "Album N")
        rel["barcode"] = ""
        eng = self.engine(FakeHttp(mb=[rel]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.tier, "exact")
        self.assertFalse(any("tagmender_missing" in t for t in p.targets))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
