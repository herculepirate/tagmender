"""An album held for your choice whose job went through (TagMender's earlier writes for a pick
it withdrew taken back, TAG-1) is reported "Unresolved", never "Failed"."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import report  # noqa: E402


def album(**kw):
    a = {"source_dir": "/x/Band/Album", "status": "unresolved", "label": "Unresolved",
         "identification": {"tier": "unresolved", "reason": "conflicting_sources: your folder names the instrumental version",
                            "reason_codes": ["conflicting_sources"]},
         "files": [{"src": "/x/Band/Album/01.flac", "dst": "/x/Band/Album/01.flac", "op": "tag",
                    "changes": {"musicbrainz_albumid": None}, "taken_back": ["musicbrainz_albumid"]}], "notes": []}
    a.update(kw)
    return a


class HeldAlbumReport(unittest.TestCase):
    def test_held_and_written_is_unresolved_not_failed(self):
        rows = report.rows_of(album(), {"status": "done"}, applied=True) \
            if hasattr(report, "rows_of") else None
        if rows is None:
            self.skipTest("row builder not exposed")
        r = [x for x in rows if x["section"] == "failed"][0]
        self.assertEqual(r["status"], "Unresolved")
        self.assertIn("taken back", r["issue"])

    def test_a_real_failure_stays_failed(self):
        if not hasattr(report, "rows_of"):
            self.skipTest("row builder not exposed")
        a = album(identification={"tier": "exact", "reason": ""}, status="ready")
        rows = report.rows_of(a, {"status": "failed", "error": "disk full"}, applied=True)
        self.assertTrue(any(x["status"] == "Failed" for x in rows), rows)

    def test_a_first_run_unresolved_album_says_nothing_was_taken_back(self):
        # (review: every applied Unresolved album - its TAGMENDER mark is work too - was told "what an
        # earlier run wrote for it was taken back", on a first run with nothing to take back)
        a = album(files=[{"src": "/x/Band/Album/01.flac", "dst": "/x/Band/Album/01.flac", "op": "tag",
                          "changes": {"tagmender": ["Unresolved"]}}],
                  identification={"tier": "unresolved", "reason": "no_candidates: nothing found",
                                  "reason_codes": ["no_candidates"]})
        rows = report.rows_of(a, {"status": "done"}, applied=True)
        r = [x for x in rows if x["section"] == "failed"][0]
        self.assertEqual(r["status"], "Unresolved")
        self.assertNotIn("taken back", r["issue"])

    def test_a_plan_saved_before_the_record_still_says_so(self):
        a = album(files=[{"src": "/x/Band/Album/01.flac", "dst": "/x/Band/Album/01.flac", "op": "tag",
                          "changes": {"musicbrainz_albumid": None},
                          "diff": [{"field": "musicbrainz_albumid", "before": ["x"], "after": [],
                                    "action": "restored (as before TagMender's job)"}]}])
        r = [x for x in report.rows_of(a, {"status": "done"}, applied=True) if x["section"] == "failed"][0]
        self.assertIn("taken back", r["issue"])

    def test_all_kept_by_your_policy_says_nothing_was_taken_back(self):
        # (fix check: every restored field kept by a preserve/lock policy - the TAG-1 note stays, but
        # the plan records nothing taken back)
        a = album(notes=["what TagMender's earlier job wrote of that release is taken back (TAG-1)"],
                  files=[{"src": "/x/Band/Album/01.flac", "dst": "/x/Band/Album/01.flac", "op": "tag",
                          "changes": {"tagmender": ["Unresolved"]}, "taken_back": []}])
        r = [x for x in report.rows_of(a, {"status": "done"}, applied=True) if x["section"] == "failed"][0]
        self.assertNotIn("taken back", r["issue"])


if __name__ == "__main__":
    unittest.main()
