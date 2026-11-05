# Tests for 0.8.0's stricter evidence rules (no network; fakes and synthetic albums):
# - Q3: a barcode only typed in notes, an .nfo or a saved page scores, but proves nothing;
# - Q4: ISRCs (recordings) no longer prove the pressing;
# - Q5: a fuzzy disc ID or CTDB match is told, weighs 1, and never picks a pressing;
# - Q6: MusicBrainz's own Discogs links decide whether two entries are one pressing;
# - Q7: tags another tagger wrote never outrank the folder name; tags TagMender wrote do.
import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_engine as te  # noqa: E402  (its settings and state dirs are temporary)
import executor  # noqa: E402
import exclusions  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import tagio  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])   # tie rules without the country preference
EU_BARCODE = "4006759000014"


def album(**kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s}
                    for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "CA", "label": "L", "catnos": set(), "barcodes": set(),
         "disc_count": 1,
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
    c.update(kw)
    return c


def pick(a, *cands, ocr="", cfg=CFG):
    return mc.classify([mc.score_candidate(a, c, cfg, ocr) for c in cands], cfg)


def outcome(r):
    return r["status"], r["best"]["cand"]["id"]


# ---- Q3: a barcode typed in the notes -----------------------------------------------------
NOTES = "Source: tracker\nOriginal release: Century Media EU\nBarcode: 4006759000014\n"


class NotesBarcode(unittest.TestCase):
    def eu_jp(self, **eu):
        return cand(id="eu", country="XE", barcodes={EU_BARCODE}, **eu), cand(id="jp", country="JP", **(
            {"catnos": {mc.norm_code("VICP-64123")}} if eu.get("catnos") else {}))

    def test_a_notes_barcode_no_longer_outranks_the_folders_country_and_number(self):
        # (the audit's notes_demo: auto eu before, from the notes' barcode alone)
        code = {mc.norm_code("VICP-64123")}
        a = album(folder="2010 - First Light [Japan, VICP-64123]", catnos=code, catnos_from_folder=code,
                  note_barcodes=[EU_BARCODE])
        r = pick(a, *self.eu_jp(catnos={"cm1234"}), ocr=NOTES)
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])
        a = album(folder="2010 - First Light [Japan]", note_barcodes=[EU_BARCODE])
        r = pick(a, *self.eu_jp(), ocr=NOTES)
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])

    def test_a_notes_barcode_alone_picks_no_pressing(self):
        a = album(note_barcodes=[EU_BARCODE])
        s = mc.score_candidate(a, self.eu_jp()[0], CFG, NOTES)
        self.assertEqual(s["code"], mc.NOTES_BARCODE)           # it keeps its points ...
        self.assertNotIn("barcode", s["clues"])                 # ... but tells no pressing apart
        self.assertEqual(mc.CLUE_WEIGHTS[mc.NOTES_BARCODE], 0)
        self.assertFalse(mc.barcode_proves(s))
        self.assertFalse(ma.hard_proof(s))
        r = pick(a, *self.eu_jp(), ocr=NOTES)
        self.assertEqual(r["status"], "review", r["why"])

    def test_a_barcode_printed_on_the_copy_still_proves_it(self):
        # read off a scan (not among the notes' barcodes): proof, as before
        r = pick(album(folder="2010 - First Light [Japan]"), *self.eu_jp(), ocr="Barcode 4006759000014")
        self.assertEqual(outcome(r), ("auto", "eu"), r["why"])
        self.assertEqual(r["best"]["code"], "barcode (OCR)")
        # in the notes AND decoded off a scan: the scan's
        a = album(folder="2010 - First Light [Japan]", note_barcodes=[EU_BARCODE], ocr_barcodes={EU_BARCODE})
        self.assertEqual(outcome(pick(a, *self.eu_jp(), ocr=NOTES)), ("auto", "eu"))

    def test_a_cue_catalog_or_a_pdf_booklet_keeps_full_weight_a_saved_page_does_not(self):
        docs = ("(album.cue)\nCATALOG 4006759000014\n(notes.nfo)\nBarcode: 4006759000014\n"
                "(Discogs - EU.html)\nBarcode 4006759000014\n")
        typed, printed = ma.split_documents(docs)
        self.assertIn("4006759000014", printed)                  # the cue sheet's CATALOG: the disc's MCN
        self.assertNotIn("CATALOG", typed)
        a = album(folder="2010 - First Light [Japan]", note_barcodes=[EU_BARCODE],
                  typed_text=typed, printed_text=printed)
        self.assertFalse(mc.typed_only_barcode(a, EU_BARCODE))
        self.assertEqual(outcome(pick(a, *self.eu_jp(), ocr=docs)), ("auto", "eu"))
        # only a saved web page and an .nfo name it (not in the "Barcode:" lines of the notes)
        typed, printed = ma.split_documents("(Discogs - EU.html)\nBarcode 4006759000014\n(booklet.pdf)\nTracks\n")
        a = album(folder="2010 - First Light [Japan]", typed_text=typed, printed_text=printed)
        self.assertTrue(mc.typed_only_barcode(a, EU_BARCODE))
        r = pick(a, *self.eu_jp(), ocr=typed + printed)
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])
        # printed in a PDF booklet: the copy's own print
        typed, printed = ma.split_documents("(booklet.pdf)\n4 006759 000014\n")
        a = album(note_barcodes=[EU_BARCODE], typed_text=typed, printed_text=printed)
        self.assertFalse(mc.typed_only_barcode(a, EU_BARCODE))


# ---- Q4: ISRCs name recordings, not pressings ----------------------------------------------
class Isrc(unittest.TestCase):
    def test_isrcs_no_longer_overrule_the_folder_country(self):
        # (the audit's isrc_demo, case B: review eu at 0.0 points before)
        a = album(folder="2010 - First Light [Japan]", isrc_releases={"eu"})
        r = pick(a, cand(id="eu", country="XE"), cand(id="jp", country="JP"))
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])
        # case A: ISRCs on two other pressings
        a = album(folder="2010 - First Light [Japan, VICP-12345]", isrc_releases={"eu", "us"})
        r = pick(a, cand(id="eu", country="XE"), cand(id="us", country="US"), cand(id="jp", country="JP"))
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])

    def test_an_isrc_still_scores_and_proves_the_album(self):
        s = mc.score_candidate(album(isrc_releases={"eu"}), cand(id="eu"), CFG)
        self.assertIn("isrc", s["clues"])
        self.assertTrue(mc._numbers_prove_album(s, CFG))         # the album (its recordings) ...
        self.assertNotIn("isrc", mc.EVIDENCE_CLUES)               # ... not the pressing

    def test_an_unlisted_pressing_is_not_proven_by_isrcs(self):
        code = {mc.norm_code("ABC-12345")}
        a = album(folder="2010 - First Light [Japan, ABC-12345]", catnos=code, catnos_from_folder=code,
                  isrc_releases={"jp"})
        r = pick(a, cand(id="eu", country="XE", catnos={"xyz1"}), cand(id="jp", country="JP", catnos={"xyz2"}))
        self.assertNotEqual(r["status"], "auto", r["why"])

    def test_unofficial_release_is_not_backed_by_isrcs(self):
        s = mc.score_candidate(album(isrc_releases={"bt"}, tag_release_mb={"bt"}), cand(id="bt", unofficial=True),
                               CFG)
        self.assertTrue(mc.unofficial_why(s))


# ---- Q5: fuzzy disc ID matches -------------------------------------------------------------
class FuzzyDiscId(unittest.TestCase):
    def test_a_fuzzy_match_alone_picks_no_pressing(self):
        # (the audit's fuzzy_demo: auto eu "pressing chosen by disc ID" before)
        a = album(toc_releases={"eu"}, exact_toc_releases=set())
        r = pick(a, cand(id="eu", country="XE", formats={"cd"}), cand(id="jp", country="JP", formats={"cd"}))
        self.assertEqual(r["status"], "review", r["why"])
        eu = mc.score_candidate(a, cand(id="eu"), CFG)
        self.assertNotIn("disc TOC", eu["clues"])
        self.assertEqual(eu["clues"]["disc TOC (fuzzy)"], mc.FUZZY_TOC_CLUE)
        self.assertEqual(mc.CLUE_WEIGHTS["disc TOC (fuzzy)"], 1)
        self.assertFalse(mc._exact_toc(eu))
        self.assertFalse(ma.hard_proof(eu))
        self.assertNotIn("disc TOC (fuzzy)", mc.EVIDENCE_CLUES)

    def test_a_fuzzy_match_never_overrules_the_folder(self):
        a = album(folder="2010 - First Light [Japan]", toc_releases={"eu"}, exact_toc_releases=set())
        self.assertEqual(outcome(pick(a, cand(id="eu", country="XE"), cand(id="jp", country="JP"))), ("auto", "jp"))

    def test_an_exact_disc_id_still_picks_the_pressing(self):
        a = album(toc_releases={"eu"}, exact_toc_releases={"eu"})
        r = pick(a, cand(id="eu", country="XE", formats={"cd"}), cand(id="jp", country="JP", formats={"cd"}))
        self.assertEqual(outcome(r), ("auto", "eu"), r["why"])
        self.assertEqual(r["best"]["clues"]["disc TOC"], mc.EXACT_TOC_CLUE)

    def test_ctdb_links_to_discogs_count_fully_only_when_the_disc_is_known_exactly(self):
        dg = cand(id="77", source="discogs")
        s = mc.score_candidate(album(toc_discogs={"77"}), dg, CFG)
        self.assertIn("disc TOC (fuzzy)", s["clues"])
        s = mc.score_candidate(album(toc_discogs={"77"}, toc_releases={"m"}, exact_toc_releases={"m"}), dg, CFG)
        self.assertEqual(s["clues"]["disc TOC"], mc.TOC_CLUE)


# ---- Q6: MusicBrainz's Discogs links -------------------------------------------------------
class DiscogsLinks(unittest.TestCase):
    def mb(self, **kw):
        return cand(id="mb1", source="mb", country="DE", barcodes={EU_BARCODE}, catnos={"cm7701"},
                    formats={"cd"}, **kw)

    def dg(self, rid="222"):
        return cand(id=rid, source="discogs", country="DE", barcodes={EU_BARCODE}, catnos={"cm7701"},
                    formats={"cd"})

    def test_a_link_to_another_discogs_release_is_another_pressing(self):
        # (the audit's xlink_demo: True before)
        self.assertFalse(mc.same_pressing(self.mb(discogs_id="111"), self.dg()))
        self.assertFalse(mc.same_pressing(self.dg(), self.mb(discogs_id="111")))
        self.assertTrue(mc._stated_apart(self.mb(discogs_id="111"), self.dg()))
        # every link counts (the pressing listed twice on Discogs)
        self.assertTrue(mc.same_pressing(self.mb(discogs_id="111", discogs_ids=["111", "222"]), self.dg()))
        # no link at all: the numbers decide, as before
        self.assertTrue(mc.same_pressing(self.mb(), self.dg()))
        # your exact disc ID on both
        self.assertTrue(mc.same_pressing(self.mb(discogs_id="111"), self.dg(), disc_on_both=True))

    def test_mb_twins_respects_the_links(self):
        mb_s = mc.score_candidate(album(), self.mb(discogs_id="111", discogs_ids=["111"]), CFG)
        dg_s = mc.score_candidate(album(), self.dg(), CFG)
        mb_res, dg_res = mc.classify([mb_s], CFG), mc.classify([dg_s], CFG)
        mb_res["scored"], dg_res["scored"] = [mb_s], [dg_s]
        self.assertEqual(mc.mb_twins(mb_res, dg_res), [])
        self.assertNotIn("mb_twin", mc.combine(mb_res, dg_res))
        mb_s["cand"]["discogs_ids"] = ["111", "222"]
        self.assertEqual(mc.mb_twins(mb_res, dg_res), [mb_s])

    def test_every_linked_discogs_release_is_read(self):
        rel = {"relations": [{"target-type": "url", "url": {"resource": "https://www.discogs.com/release/111-x"}},
                             {"target-type": "url", "url": {"resource": "https://www.discogs.com/master/5"}},
                             {"target-type": "url", "url": {"resource": "https://www.discogs.com/release/222"}}]}
        self.assertEqual(ma.mb_linked_discogs_ids(rel), ["111", "222"])
        self.assertEqual(ma.mb_linked_discogs_id(rel), "111")
        self.assertEqual(ma.mb_linked_discogs_id({}), "")


# ---- Q7: tags another tagger wrote ---------------------------------------------------------
class TaggerTags(unittest.TestCase):
    def releases(self):
        return (cand(id="eu", country="XE", barcodes={EU_BARCODE}, date="2010-02-01"),
                cand(id="jp", country="JP", barcodes={"4988002000000"}, date="2010-03-03"))

    def tagged(self, by_tagger):
        return album(folder="2010 - First Light [Japan]", barcodes={EU_BARCODE}, tag_barcodes={EU_BARCODE},
                     tag_release_mb={"eu"}, tag_date="2010-02-01", tag_country="XE", tags_by_tagger=by_tagger)

    def test_another_taggers_pick_against_the_folder_goes_to_review(self):
        # (the audit's retag_demo: auto eu before, whatever the folder said)
        r = pick(self.tagged(True), *self.releases())
        self.assertEqual(r["status"], "review", r["why"])
        said = " ".join(r["why"])
        self.assertIn("written by another tagger", said)
        self.assertIn("eu", said)
        self.assertIn("JP", said)
        s = mc.score_candidate(self.tagged(True), self.releases()[0], CFG)
        self.assertEqual(s["code"], mc.TAGS_BARCODE)
        self.assertFalse(mc.barcode_proves(s))

    def test_an_untagged_copy_is_unchanged(self):
        r = pick(album(folder="2010 - First Light [Japan]"), *self.releases())
        self.assertEqual(outcome(r), ("auto", "jp"), r["why"])
        # a barcode tag without release IDs is the ripper's or shop's: still your copy's own
        a = album(folder="2010 - First Light [Japan]", barcodes={EU_BARCODE}, tag_barcodes={EU_BARCODE})
        self.assertEqual(outcome(pick(a, *self.releases())), ("auto", "eu"))

    def test_tags_tagmender_wrote_still_count(self):
        r = pick(self.tagged(False), *self.releases())
        self.assertEqual(outcome(r), ("auto", "eu"), r["why"])

    def test_another_taggers_pressing_tags_break_no_tie(self):
        # (0.8.1, the auditor's p1: the country, date, label and media another tagger wrote
        # came FROM the release it chose, and picked it among tied pressings)
        rel = (cand(id="de", country="DE", date="2010-02-01", label="Alpha"),
               cand(id="gb", country="GB", date="2010-03-01", label="Beta"))
        for kw in (dict(tag_country="GB"), dict(tag_date="2010-03-01"), dict(tag_label="Beta")):
            r = pick(album(tag_release_mb={"gb"}, tags_by_tagger=True, **kw), *rel)
            self.assertEqual(r["status"], "review", (kw, r["why"]))
            r = pick(album(tag_release_mb={"gb"}, tags_by_tagger=False, **kw), *rel)
            self.assertEqual(outcome(r), ("auto", "gb"), (kw, r["why"]))
        # Mekong Delta - 1989 - Toccata [Aaarrg! Records, Germany, AAARRG 17], tagged by TagMender as
        # the Austrian CD: re-processed (a watched folder given a moving profile), it stays automatic
        a = album(folder="1989-Toccata [Germany]", barcodes={EU_BARCODE}, tag_barcodes={EU_BARCODE},
                  tag_release_mb={"at"}, tag_country="AT", tags_by_tagger=False)
        rel = (cand(id="de", country="DE", formats={"cd"}), cand(id="at", country="AT", formats={"cd"},
                                                                barcodes={EU_BARCODE}))
        self.assertEqual(outcome(pick(a, *rel)), ("auto", "at"))
        self.assertEqual(pick(dict(a, tags_by_tagger=True), *rel)["status"], "review")

    def test_a_scan_or_exact_disc_id_still_backs_the_tagged_release(self):
        a = dict(self.tagged(True), ocr_barcodes={EU_BARCODE})
        self.assertEqual(outcome(pick(a, *self.releases())), ("auto", "eu"))
        a = dict(self.tagged(True), toc_releases={"eu"}, exact_toc_releases={"eu"})
        self.assertEqual(pick(a, *self.releases())["best"]["cand"]["id"], "eu")


# ---- review fixes ---------------------------------------------------------------------------
def tracks(*secs):
    return [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)]


class WeakEvidence(unittest.TestCase):
    def test_a_weak_barcode_never_excuses_wrong_track_lengths(self):
        eu = cand(id="eu", country="XE", barcodes={EU_BARCODE})
        for secs in ((230.0, 335.0, 440.0), (260.0, 365.0, 470.0)):      # 30 s and 60 s off
            for a, ocr in ((album(tracks=tracks(*secs), note_barcodes=[EU_BARCODE]), NOTES),
                           (album(tracks=tracks(*secs), barcodes={EU_BARCODE}, tag_barcodes={EU_BARCODE},
                                  tag_release_mb={"eu"}, tags_by_tagger=True), "")):
                r = pick(a, eu, ocr=ocr)
                self.assertIn(r["best"]["code"], mc.WEAK_BARCODES)
                self.assertNotEqual(r["status"], "auto", r["why"])
                self.assertTrue(any("durations agree" in w for w in r["why"]), r["why"])
                self.assertFalse(any("track lengths not compared" in n for n in r["best"]["notes"]))

    def test_an_isrc_alone_picks_no_pressing(self):
        r = pick(album(isrc_releases={"a"}), cand(id="a", country="DE"), cand(id="b", country="GB"))
        self.assertEqual(r["status"], "review", r["why"])

    def test_another_taggers_ids_never_break_a_tie(self):
        rel = (cand(id="a", country="DE"), cand(id="b", country="GB"))
        for a in (album(toc_releases={"a"}, tag_release_mb={"b"}, tags_by_tagger=True),
                  album(tag_release_mb={"b"}, tags_by_tagger=True)):
            r = pick(a, *rel)
            self.assertEqual(r["status"], "review", (outcome(r), r["why"]))
        # the IDs TagMender wrote itself still keep their release
        self.assertEqual(outcome(pick(album(tag_release_mb={"b"}, tags_by_tagger=False), *rel)), ("auto", "b"))


class OwnJobHistory(unittest.TestCase):
    """match_albums.tagmender_wrote reads the job history (state.db and the jobs' plans)."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(exclusions, "STATE_DIR", self.tmp)
        self.patch.start()
        ma._OWN_JOBS.clear()
        ma._OWN_INDEX[:] = [None, {}]

    def tearDown(self):
        self.patch.stop()
        ma._OWN_JOBS.clear()
        ma._OWN_INDEX[:] = [None, {}]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def job(self, job, folder, files, mb, status="done", job_status="done", tier="exact", ts=1.0):
        db = sqlite3.connect(self.tmp / "state.db")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY, profile TEXT, started REAL, ended REAL,"
                   " status TEXT, summary TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS job_albums (job TEXT, album TEXT, status TEXT, label TEXT,"
                   " detail TEXT, ts REAL)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (job, "default", ts, ts, job_status, ""))
        db.execute("INSERT INTO job_albums VALUES (?,?,?,?,?,?)", (job, str(folder), status, "", "", ts))
        db.commit()
        db.close()
        jd = self.tmp / "jobs" / job
        jd.mkdir(parents=True)
        plan = {"albums": [{"source_dir": str(folder), "identification": {"tier": tier, "release_id": mb},
                            "files": [{"src": str(f), "dst": str(f), "diff": [
                                {"field": "musicbrainz_albumid", "before": [], "after": [mb]}]} for f in files]}]}
        (jd / "plan.json").write_text(json.dumps(plan))

    def test_history(self):
        d = Path("/music/A/1989 - X")
        files = [d / "01.flac", d / "02.flac"]
        mine = [(f, "aaaa", "") for f in files]
        self.assertFalse(ma.tagmender_wrote(mine))                 # no history at all
        self.job("j1", d, files, "aaaa")
        self.assertTrue(ma.tagmender_wrote(mine))
        self.assertFalse(ma.tagmender_wrote([(files[0], "bbbb", "")]))      # retagged since
        self.assertFalse(ma.tagmender_wrote(mine + [(d / "03.flac", "aaaa", "")]))   # a file it never wrote
        self.job("j2", d, files, "cccc", job_status="undone", ts=2.0)   # undone: not counted
        self.assertTrue(ma.tagmender_wrote(mine))
        self.assertFalse(ma.tagmender_wrote([(f, "cccc", "") for f in files]))
        self.job("j3", d, files, "eeee", tier="album_only", ts=3.0)
        self.assertFalse(ma.tagmender_wrote([(f, "eeee", "") for f in files]))
        self.job("j4", d, files, "ffff", status="failed", ts=4.0)
        self.assertFalse(ma.tagmender_wrote([(f, "ffff", "") for f in files]))


    def plan_job(self, job, folder, entries, rid, tier="exact", ts=1.0):
        """A done job whose plan holds these file entries [(src, dst, op, diff rows)]."""
        db = sqlite3.connect(self.tmp / "state.db")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY, profile TEXT, started REAL, ended REAL,"
                   " status TEXT, summary TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS job_albums (job TEXT, album TEXT, status TEXT, label TEXT,"
                   " detail TEXT, ts REAL)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (job, "default", ts, ts, "done", ""))
        db.execute("INSERT INTO job_albums VALUES (?,?,?,?,?,?)", (job, str(folder), "done", "", "", ts))
        db.commit()
        db.close()
        jd = self.tmp / "jobs" / job
        jd.mkdir(parents=True)
        plan = {"albums": [{"source_dir": str(folder), "identification": {"tier": tier, "release_id": rid},
                            "files": [{"src": str(s), "dst": str(d), "op": op, "diff": diff}
                                      for s, d, op, diff in entries]}]}
        (jd / "plan.json").write_text(json.dumps(plan))

    def test_a_move_after_tagging_in_place_carries_the_authorship(self):
        a, b, c = Path("/in/X"), Path("/lib/Artist/X"), Path("/copies/X")
        ids = [{"field": "musicbrainz_albumid", "before": [], "after": ["aaaa"]}]
        title = [{"field": "title", "before": ["x"], "after": ["y"]}]
        self.plan_job("j1", a, [(a / "01.flac", a / "01.flac", "tag", ids)], "aaaa")
        self.assertTrue(ma.tagmender_wrote([(a / "01.flac", "aaaa", "")]))
        # moved by a job that did not identify the album exactly, its IDs left as they were
        self.plan_job("j2", a, [(a / "01.flac", b / "01.flac", "move", title)], "", tier="album_only", ts=2.0)
        self.assertTrue(ma.tagmender_wrote([(b / "01.flac", "aaaa", "")]))
        self.assertFalse(ma.tagmender_wrote([(a / "01.flac", "aaaa", "")]))    # (nothing there now)
        self.assertFalse(ma.tagmender_wrote([(b / "01.flac", "bbbb", "")]))    # retagged since
        # copied on: both copies are TagMender's
        self.plan_job("j3", b, [(b / "01.flac", c / "01.flac", "copy", [])], "", tier="album_only", ts=3.0)
        self.assertTrue(ma.tagmender_wrote([(b / "01.flac", "aaaa", "")]))
        self.assertTrue(ma.tagmender_wrote([(c / "01.flac", "aaaa", "")]))

    def test_ids_kept_that_are_the_identified_release_count(self):
        d = Path("/music/B/2001 - Y")
        title = [{"field": "title", "before": ["x"], "after": ["y"]}]
        self.plan_job("j1", d, [(d / "01.flac", d / "01.flac", "tag", title)], "aaaa")
        self.assertTrue(ma.tagmender_wrote([(d / "01.flac", "aaaa", "")]))
        # a preserved ID of another release is not TagMender's
        self.assertFalse(ma.tagmender_wrote([(d / "01.flac", "bbbb", "")]))
        # nor an ID a job identified only loosely
        e = Path("/music/B/2002 - Z")
        self.plan_job("j2", e, [(e / "01.flac", e / "01.flac", "tag", title)], "cccc", tier="album_only", ts=2.0)
        self.assertFalse(ma.tagmender_wrote([(e / "01.flac", "cccc", "")]))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed for the audio fixtures")
class ReprocessedAlbum(te.Base):
    """The whole path: TagMender tags an album in place, then identifies it again (as the
    watcher does when the folder's profile changed); files another tagger wrote go to review."""
    AT = "aaaaaaaa-0000-4000-8000-000000000001"
    DE = "dddddddd-0000-4000-8000-000000000002"

    def http(self):
        at = te.mb_release(self.AT, "Mekong Delta", "Toccata", country="AT")
        at["barcode"] = "0123456789012"
        de = te.mb_release(self.DE, "Mekong Delta", "Toccata", country="DE")
        de["barcode"] = EU_BARCODE
        return te.FakeHttp(mb=[at, de])

    def test_tagmender_tagged_album_stays_automatic(self):
        http = self.http()
        d = self.album("Mekong Delta", "Toccata", folder="1989 - Toccata [Germany]", barcode="0123456789012")
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.release_id), ("identified", self.AT), p.why)
        self.assertEqual(executor.Executor(eng.plan(d)).run()["counts"], {"done": 1})
        self.assertEqual(tagio.read(d / "01.flac")["musicbrainz_albumid"], [self.AT])
        self.assertFalse(ma.read_album(d, "Mekong Delta", d.name)["tags_by_tagger"])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.release_id), ("identified", self.AT), p.why)

    def test_moved_by_a_second_profile_change_it_stays_automatic(self):
        http = self.http()
        d = self.album("Mekong Delta", "Toccata", folder="1989 - Toccata [Germany]", barcode="0123456789012")
        eng = self.engine(http)
        self.assertEqual(executor.Executor(eng.plan(d)).run()["counts"], {"done": 1})
        dest = self.tmp / "dest"
        dest.mkdir()
        eng = self.engine(http, operation="move", destination=str(dest))
        self.assertEqual(executor.Executor(eng.plan(d)).run()["counts"], {"done": 1})
        moved = {p.parent for p in dest.rglob("*.flac")}
        self.assertEqual(len(moved), 1, list(dest.rglob("*")))
        self.assertFalse(d.exists() and any(d.glob("*.flac")))
        album_dir = moved.pop()
        if album_dir.name.startswith("Disc "):
            album_dir = album_dir.parent
        self.assertFalse(ma.read_album(album_dir, "Mekong Delta", album_dir.name)["tags_by_tagger"])
        eng = self.engine(http)
        p = eng.identify(eng.albums_in(album_dir)[0])
        self.assertEqual((p.status, p.release_id), ("identified", self.AT), p.why)
        # and moved on once more: still TagMender's own tags
        dest2 = self.tmp / "dest2"
        dest2.mkdir()
        eng = self.engine(http, operation="move", destination=str(dest2))
        self.assertEqual(executor.Executor(eng.plan(album_dir)).run()["counts"], {"done": 1})
        again = {p.parent for p in dest2.rglob("*.flac")}
        self.assertEqual(len(again), 1, list(dest2.rglob("*")))
        album_dir = again.pop()
        if album_dir.name.startswith("Disc "):
            album_dir = album_dir.parent
        self.assertFalse(ma.read_album(album_dir, "Mekong Delta", album_dir.name)["tags_by_tagger"])

    def test_another_taggers_album_goes_to_review(self):
        d = self.album("Mekong Delta", "Toccata", folder="1989 - Toccata [Germany]", barcode="0123456789012",
                       MUSICBRAINZ_ALBUMID=self.AT, RELEASECOUNTRY="AT")
        self.assertTrue(ma.read_album(d, "Mekong Delta", d.name)["tags_by_tagger"])
        eng = self.engine(self.http())
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.status, "identified")
        self.assertTrue(any("written by another tagger" in w and self.AT in w for w in p.why), p.why)
        # blind (validate.py): the IDs are ignored, nothing is "another tagger's"
        self.assertFalse(ma.read_album(d, "Mekong Delta", d.name, blind=True)["tags_by_tagger"])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed for the audio fixtures")
class SavedPageBarcode(te.Base):
    """A barcode only a saved web page in the folder types: searched, never the copy's own
    (the documents' code search once made it the album's barcode, and so full proof)."""
    EU = "eeeeeeee-0000-4000-8000-000000000001"
    JP = "jjjjjjjj-0000-4000-8000-000000000002"

    def test_a_saved_pages_barcode_is_not_your_copys(self):
        eu = te.mb_release(self.EU, "Artist Alpha", "First Light", country="XE")
        eu["barcode"] = EU_BARCODE
        jp = te.mb_release(self.JP, "Artist Alpha", "First Light", country="JP")
        jp["barcode"] = ""
        d = self.album("Artist Alpha", "First Light", folder="2005 - First Light [Japan]")
        (d / "Discogs - EU.html").write_text(f"<html><body><p>Barcode {EU_BARCODE}</p></body></html>")
        eng = self.engine(te.FakeHttp(mb=[eu, jp]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual((p.status, p.release_id), ("identified", self.EU), p.why)


if __name__ == "__main__":
    unittest.main()
