# TOC-1 (docs/MATCHING_RULES.md): your disc's table of contents, read from the rip log, fits
# your files to the sector, and MusicBrainz has that very disc ID attached to the release: the
# release's lengths are your files' own, whatever lengths it lists, and an artist the folder
# names otherwise is the database's credit. Its title must agree (a disc ID attached to the
# wrong release names another album). Filed on it below organize_min_confidence.
# OCR-1: the investigation's time budget holds within a scan, not only between scans.
# Synthetic albums and candidates: no library, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
# Food - Food (Feral Records, 1999): the disc's own lengths, and the database's, off by 5-6 s
# either way (were they all short, LEN-1 alone would take them as sleeve times)
DISC = [219.0, 450.0, 539.0, 186.0, 330.0, 470.0, 288.0, 245.0, 238.0, 115.0]
LISTED = [s + (6.0 if i % 2 else -6.0) for i, s in enumerate(DISC)]
TOC = "1 10 231407 150 16575 50325 90747 104642 129450 164850 186450 204825 222900"


def album(**kw):
    a = {"artist": "Iain Bellamy", "title": "Food", "year": 1999, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(DISC)],
         "barcodes": set(), "catnos": set(), "tocs": {1: TOC}, "tocs_fit": [1],
         "toc_releases": {"food"}, "exact_toc_releases": {"food"}, "exact_toc_media": {"food": {1}},
         "exact_toc_on": {"food": {TOC: {1}}}}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "food", "url": "u", "artist": "Food", "title": "Food", "year": 1999,
         "country": "GB", "label": "Feral Records", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LISTED)]}
    c.update(kw)
    return c


def pick(a, *cands):
    return mc.classify([mc.score_candidate(a, c, CFG) for c in cands], CFG)


class TOC1(unittest.TestCase):
    def test_TOC1_the_disc_id_proves_the_album_whatever_it_lists_and_credits(self):
        r = pick(album(), cand())
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertTrue(r["best"]["toc_proven"])
        notes = " ".join(r["best"]["notes"])
        self.assertIn("credited as the database does (Food)", notes)
        self.assertIn("table of contents", notes)

    def test_TOC1_without_the_rule_it_was_left_for_review(self):
        """The lengths alone: 4 s off on every track, the artist not the folder's."""
        r = pick(album(exact_toc_releases=set(), exact_toc_media={}, exact_toc_on={}), cand())
        self.assertNotEqual(r["status"], "auto")
        self.assertFalse(r["best"].get("toc_proven"))

    def test_TOC1_a_log_of_another_rip_proves_nothing(self):
        """The log's TOC does not fit these files (another rip, another pressing)."""
        r = pick(album(tocs_fit=[]), cand())
        self.assertNotEqual(r["status"], "auto")
        self.assertFalse(r["best"].get("toc_proven"))

    def test_TOC1_a_disc_id_on_a_release_of_another_title_is_no_proof(self):
        r = pick(album(), cand(title="Something Else Entirely"))
        self.assertNotEqual(r["status"], "auto")
        self.assertFalse(r["best"].get("toc_proven"))

    def test_TOC1_every_disc_needs_its_disc_id_on_the_release(self):
        two = [{"disc": d, "num": i + 1, "title": f"T{d}{i}", "secs": s} for d in (1, 2) for i, s in enumerate(DISC)]
        listed = [{"disc": d, "title": f"T{d}{i}", "secs": s} for d in (1, 2) for i, s in enumerate(LISTED)]
        toc2 = TOC.replace("231407", "231408")
        c = cand(tracks=listed, disc_count=2)
        a = album(discs=2, tracks=two, tocs={1: TOC, 2: toc2}, tocs_fit=[1, 2])
        self.assertFalse(mc.toc_lengths(a, c, True))                          # (disc 1's only)
        a["exact_toc_on"] = {"food": {TOC: {1, 2}}}                        # disc 1's ID on both media,
        self.assertFalse(mc.toc_lengths(a, c, True))                          # disc 2's on none
        a["exact_toc_on"] = {"food": {TOC: {2}, toc2: {1}}}                # each on the other's medium
        self.assertFalse(mc.toc_lengths(a, c, True))
        a["exact_toc_on"] = {"food": {TOC: {1}, toc2: {2}}}
        self.assertTrue(mc.toc_lengths(a, c, True))

    def test_TOC1_a_disc_without_a_toc_is_not_proven(self):
        """Disc 2 lossy or not cut on CD sectors: no TOC, nothing proves its lengths."""
        two = [{"disc": d, "num": i + 1, "title": f"T{d}{i}", "secs": s} for d in (1, 2) for i, s in enumerate(DISC)]
        listed = [{"disc": d, "title": f"T{d}{i}", "secs": s} for d in (1, 2) for i, s in enumerate(LISTED)]
        a = album(discs=2, tracks=two)                                     # (a TOC for disc 1 only)
        self.assertFalse(mc.toc_lengths(a, cand(tracks=listed, disc_count=2), True))
        r = pick(a, cand(tracks=listed, disc_count=2))
        self.assertFalse(r["best"].get("toc_proven"))

    def test_TOC1_with_the_artist_otherwise_the_track_titles_must_agree(self):
        """A disc ID attached in MusicBrainz to the wrong "Greatest Hits"."""
        wrong = [{"disc": 1, "title": f"Other {i}", "secs": s} for i, s in enumerate(LISTED)]
        r = pick(album(), cand(tracks=wrong))
        self.assertNotEqual(r["status"], "auto")
        self.assertFalse(r["best"].get("toc_proven"))

    def test_TOC1_a_tie_decided_by_country_is_not_filed_on_it(self):
        cfg = dict(CFG, preferred_countries=["GB"])
        a = album(toc_releases={"food", "us"}, exact_toc_releases={"food", "us"},
                  exact_toc_media={"food": {1}, "us": {1}}, exact_toc_on={"food": {TOC: {1}}, "us": {TOC: {1}}})
        r = mc.classify([mc.score_candidate(a, c, cfg) for c in (cand(), cand(id="us", country="US"))], cfg)
        prop = pl.Proposal(source_dir="/x", files=["/x/1.flac"], targets=[{}], status="identified", tier="exact",
                           confidence=r["best"]["confidence"], margin=r["margin"],
                           toc_proven=bool(r["best"].get("toc_proven")))
        self.assertEqual(r["margin"], 0.0)
        self.assertEqual(pl._files_on_evidence(prop, pf.default(), prop.confidence), "")

    def test_TOC1_a_fuzzy_disc_match_or_a_short_disc_proves_nothing(self):
        self.assertFalse(mc.toc_lengths(album(exact_toc_releases={"other"}), cand(), True))
        short = album(tracks=album()["tracks"][:2])
        self.assertFalse(mc.toc_lengths(short, cand(), True))
        self.assertFalse(mc.toc_lengths(album(), cand(), False))                    # (track count differs)

    def test_TOC1_filed_below_the_organize_threshold(self):
        prop = pl.Proposal(source_dir="/x", files=["/x/1.flac"], targets=[{}], status="identified", tier="exact",
                           confidence=71.4, margin=15.3, toc_proven=True)
        prof = pf.default()
        self.assertIn("table of contents", pl._files_on_evidence(prop, prof, 71.4))
        prop.toc_proven = False
        self.assertEqual(pl._files_on_evidence(prop, prof, 71.4), "")
        prop.toc_proven = True
        prof["matching"]["extra_careful"] = True          # (it asks for more proof, not less)
        self.assertEqual(pl._files_on_evidence(prop, prof, 71.4), "")


class OCR1(unittest.TestCase):
    def test_OCR1_a_scan_stops_reading_when_the_budget_is_used(self):
        from PIL import Image
        im = Image.new("L", (1200, 900), 200)
        calls = []
        with mock.patch.object(ma, "_tess", lambda *a, **k: calls.append(k) or ""):
            ma.ocr_located_codes(im, deadline=time.time() - 1)
            self.assertEqual(calls, [], "no turn begun past the deadline")
            ma.ocr_located_codes(im, deadline=time.time() + 100)
        self.assertEqual(len(calls), 4)                                    # (every turn, in time)
        self.assertTrue(all(k.get("deadline") for k in calls))

    def test_OCR1_tesseract_is_given_only_the_time_left(self):
        from PIL import Image
        seen = {}

        def run(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return mock.Mock(stdout="")
        with mock.patch.object(ma, "_tesseract", run):
            ma._tess(Image.new("L", (40, 40), 255), 11, deadline=time.time() + 30)
            self.assertLessEqual(seen["timeout"], 30)
            ma._tess(Image.new("L", (40, 40), 255), 11)
            self.assertEqual(seen["timeout"], 180)
        self.assertIsNone(ma._tess_or_empty(Image.new("L", (40, 40), 255), 7, deadline=time.time() - 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
