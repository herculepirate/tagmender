# TAG-5 (docs/MATCHING_RULES.md): a file that gets a release's IDs below the replace threshold gets
# that release's own numbering too (Stevie Wonder - My Cherie Amour, UICY-20348: 12 tracks and 5
# second copies, the kept files numbered 2, 4, 6 ... 17 of 17 beside the 12-track release's IDs).
# A field your policy keeps stays. Synthetic tags: no network, no files.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan  # noqa: E402
import profiles as pf  # noqa: E402

BEFORE = {"title": ["My Cehrie Amour"], "tracknumber": ["2"], "totaltracks": ["17"]}
TARGET = {"title": ["My Cherie Amour"], "tracknumber": ["1"], "totaltracks": ["12"], "discnumber": ["1"],
          "totaldiscs": ["1"], "musicbrainz_albumid": ["0ede8b3f"], "musicbrainz_trackid": ["bc2f"]}


def prof(**fields):
    return pf.from_dict({"name": "default", "field_policy": {"default": "replace", "fields": fields}})


def run(p, before=BEFORE, target=TARGET, conf=88.8):
    changes, rows = plan.diff_tags(dict(before), dict(target), p, conf)
    plan._numbered_with_ids(before, target, p, changes, rows)
    return changes, rows


class TAG5NumberedWithIds(unittest.TestCase):
    def test_TAG5_release_ids_below_the_threshold_bring_their_numbering(self):
        changes, rows = run(prof())
        self.assertEqual(changes["musicbrainz_trackid"], ["bc2f"])
        self.assertEqual((changes["tracknumber"], changes["totaltracks"]), (["1"], ["12"]))
        self.assertNotIn("title", changes)          # (the rest stays below the threshold, as before)
        self.assertIn("written with its IDs", " ".join(r["action"] for r in rows if r["field"] == "tracknumber"))

    def test_TAG5_no_release_ids_no_numbering(self):
        target = {k: v for k, v in TARGET.items() if not k.startswith("musicbrainz")}
        changes, _rows = run(prof(), target=target)
        self.assertNotIn("tracknumber", changes)

    def test_TAG5_ids_already_there_change_nothing_more(self):
        before = dict(BEFORE, musicbrainz_albumid=["0ede8b3f"], musicbrainz_trackid=["bc2f"])
        changes, _rows = run(prof(), before=before)
        self.assertNotIn("tracknumber", changes)

    def test_TAG5_your_policy_keeps_its_field(self):
        changes, _rows = run(prof(totaltracks="fill_missing"))
        self.assertEqual(changes["tracknumber"], ["1"])
        self.assertNotIn("totaltracks", changes)
        changes, _rows = run(prof(tracknumber="lock"))
        self.assertNotIn("tracknumber", changes)


if __name__ == "__main__":
    unittest.main()
