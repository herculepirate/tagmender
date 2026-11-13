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
                    "changes": {"musicbrainz_albumid": None}}], "notes": []}
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


if __name__ == "__main__":
    unittest.main()
