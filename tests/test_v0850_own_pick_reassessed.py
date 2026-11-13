"""TAG-1 / REP-1: TagMender's previous choice is history, not independent evidence. Its own earlier
pick keeps its standing while what decided it is still found on your copy; a deciding clue no
longer found (an OCR invention the reading no longer makes, lengths that no longer tell it from
the tied pressings) is disproved, a clue not looked for now is only missing.
- Apocalyptica - Reflections: Discogs 26572991 (Egypt cassette, 063 683-4) "pressing chosen by
  catalogue number 636834 on the scans" - read inside OCR noise "06400636834", no number at all;
- Megadeth - Rust In Peace (Instrumental): an instrumental album no release found is: held."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import test_engine as te  # noqa: E402
from test_v0845_replan_stable import _History  # noqa: E402

CFG = {"review_min_confidence": 70, "auto_min_margin": 5}
OCR_REASON = "pressing chosen by catalogue number 636834 on the scans (not on 13771912, 8003949)"
LEN_REASON = "pressing chosen by track lengths closest (off by 0.8s on average, others 2.5s+) (not on x, y)"


def cand(cid, conf=93.1, **kw):
    s = {"cand": {"id": cid, "source": "discogs"}, "confidence": conf, "tracks_ok": True, "notes": [],
         "clues": {}, "code": "", "tagged": False, "tagger_tagged": False}
    s.update(kw)
    return s


class OWN_DigitsPrinted(unittest.TestCase):
    def test_OWN_a_number_inside_ocr_noise_is_not_printed(self):
        self.assertFalse(mc.digits_printed("re=ety >> 06400636834 ngsougso654", "636834"))

    def test_OWN_a_number_printed_in_groups_is(self):
        self.assertTrue(mc.digits_printed("Cat. No. 063 683-4", "636834"))
        self.assertTrue(mc.digits_printed("LC 0309 063 683-4", "636834"))
        self.assertTrue(mc.digits_printed("CDP 7 91935 2", "7919352"))
        self.assertFalse(mc.digits_printed("0636834567", "636834"))

    def test_OWN_pressing_clues_no_longer_read_the_noise(self):
        c = {"source": "discogs", "id": "26572991", "catnos": {"636834"}}
        agree, _ = mc.pressing_clues("re=ety >> 06400636834 ngsougso654", c)
        self.assertNotIn("catalogue number", agree)
        agree, _ = mc.pressing_clues("MIRAGE RECORDS 063 683-4", c)
        self.assertIn("catalogue number", agree)


class OWN_Standing(unittest.TestCase):
    def tied(self, **own):
        mine = cand("26572991", **dict({"tagged": True, "own_decided": OCR_REASON, "scans_read": True}, **own))
        return mine, [cand("13771912"), cand("6499288"), mine, cand("8003949")]

    def test_OWN_its_deciding_code_no_longer_read_is_disproved(self):
        mine, scored = self.tied()
        self.assertIsNone(mc._kept_choice(scored[0], scored, CFG))
        self.assertFalse(mine["tagged"], "no standing left, never kept in a tie either")
        self.assertTrue(any("weighed afresh" in n for n in mine["notes"]))

    def test_OWN_its_deciding_code_still_read_keeps_it(self):
        mine, scored = self.tied(clues={"catalogue number": "catalogue number 636834 on the scans"})
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)
        self.assertTrue(mine["tagged"])

    def test_OWN_scans_not_read_now_is_missing_not_disproved(self):
        mine, scored = self.tied(scans_read=False)
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)

    def test_OWN_a_reason_that_is_no_clue_of_your_copy_keeps_it(self):
        for why in ("pressing chosen by your preferred country order (CA, US) puts Canada first", ""):
            mine, scored = self.tied(own_decided=why)
            self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine, why)

    def test_OWN_lengths_that_no_longer_tell_it_apart_are_disproved(self):
        mine = cand("7426872d", conf=100.0, tagged=True, own_decided=LEN_REASON, len_err=1.4, len_diffs=[1.4])
        scored = [cand("200d226d", conf=100.0, len_err=1.3, len_diffs=[1.3]), mine,
                  cand("2b2021d2", conf=100.0, len_err=1.5, len_diffs=[1.5])]
        self.assertIsNone(mc._kept_choice(scored[0], scored, CFG))
        # (still clearly closest: kept)
        mine = cand("7426872d", conf=100.0, tagged=True, own_decided=LEN_REASON, len_err=0.8, len_diffs=[0.8])
        scored = [cand("200d226d", conf=100.0, len_err=2.5, len_diffs=[2.5]), mine,
                  cand("2b2021d2", conf=100.0, len_err=2.9, len_diffs=[2.9])]
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)


class OWN_History(_History):
    """The job's deciding evidence line is kept with the files TagMender's job tagged."""

    def ident(self, rid, evidence):
        return {"tier": "exact", "provider": "discogs", "release_id": rid,
                "url": f"https://www.discogs.com/release/{rid}", "evidence": evidence}

    def test_OWN_the_deciding_evidence_is_recorded_and_a_later_keeping_job_carries_it(self):
        f = Path("/music/Apocalyptica/2002 - Reflections/01.opus")
        rows = [{"field": "discogs_release_id", "before": ["2044995"], "after": ["26572991"], "action": "changed"}]
        self.job("j1", f.parent, [(f, f, "tag", rows)], self.ident("26572991", ["artist 1.00", OCR_REASON]), ts=1.0)
        self.assertEqual(ma.tagmender_decided([f]), OCR_REASON)
        kept = [{"field": "discogs_release_id", "before": ["26572991"], "after": ["26572991"], "action": "kept"}]
        self.job("j2", f.parent, [(f, f, "none", kept)],
                 self.ident("26572991", ["kept the release your files are tagged with (TAG-1)"]), ts=2.0)
        ma._OWN_INDEX[:] = [None, {}]
        self.assertEqual(ma.tagmender_decided([f]), OCR_REASON, "a job that only kept it keeps its reason")

    def test_OWN_a_release_chosen_anew_without_a_recorded_reason_has_none(self):
        f = Path("/music/Apocalyptica/2002 - Reflections/01.opus")
        rows = [{"field": "discogs_release_id", "before": [], "after": ["26572991"], "action": "added"}]
        self.job("j1", f.parent, [(f, f, "tag", rows)], self.ident("26572991", [OCR_REASON]), ts=1.0)
        rows2 = [{"field": "discogs_release_id", "before": ["26572991"], "after": ["8003949"], "action": "changed"}]
        self.job("j2", f.parent, [(f, f, "tag", rows2)], self.ident("8003949", ["artist 1.00"]), ts=2.0)
        self.assertEqual(ma.tagmender_decided([f]), "")


class OWN_CopyVersion(te.Base):
    def test_OWN_copy_version_words(self):
        album = {"title": "Rust In Peace (Instrumental)", "folder": "1990 - Rust In Peace (Instrumental)"}
        self.assertEqual(mc.copy_version(album, {"title": "Rust in Peace", "tracks": [{"title": "Hangar 18"}]}),
                         "instrumental")
        self.assertEqual(mc.copy_version(album, {"title": "Rust in Peace (Instrumentals)", "tracks": []}), "")
        self.assertEqual(mc.copy_version({"title": "Rust in Peace", "folder": "1990 - Rust in Peace"},
                                         {"title": "Rust in Peace", "tracks": []}), "")

    def test_OWN_an_instrumental_album_no_release_is_is_held(self):
        d = self.album("Band I", "Album I", folder="1990 - Album I (Instrumental)")
        e = self.engine(te.FakeHttp(mb=[te.mb_release("mb-i", "Band I", "Album I")]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.reason_codes), ("unresolved", ["conflicting_sources"]), (p.reason, p.why))
        self.assertIn("instrumental version", p.reason)

    def test_OWN_an_instrumental_release_matches_an_instrumental_copy(self):
        # (MusicBrainz's disambiguation names the version)
        d = self.album("Band I", "Album I", folder="1990 - Album I (Instrumental)")
        rel = te.mb_release("mb-i", "Band I", "Album I")
        rel["disambiguation"] = "instrumental version"
        e = self.engine(te.FakeHttp(mb=[rel]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.status, "identified", (p.reason, p.why))


if __name__ == "__main__":
    unittest.main()
