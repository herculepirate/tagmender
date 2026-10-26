# Twenty-ninth independent review (A): an M4A total the user preserves (Picard's "Preserve
# these tags: totaltracks") or locks in the profile (field_policy "lock" / "preserve") is
# removed when the release or a tagger script makes it 0 ("$set(totaltracks,0)"): trkn (1, 9)
# becomes (1, 0). The preview says "removed totaltracks".
#
# This round's "a value the format holds as none" block (plan.py:940-943) appends the field
# to `removals` - and pops it from the target - without asking the field's policy, unlike
# every other removal path beside it (the ~remove list and the unset-total branch, which last
# round taught to leave a preserved total). Picard 3 (File.copy_metadata) puts a preserved tag
# back over the script's value and writes (1, 9); "lock" means TagMender never changes it.
# Regression: passes with TM_REPO=prev (it left the total as the file had it).
# Fix: add `and p.policy(k_) not in ("preserve", "lock", "fill_missing")` to that condition
# (and drop the None from the target then, so nothing is written for the field).
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import mutagen  # noqa: E402
import picard_config  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

INI = Path(os.environ["TAGMENDER_PICARD_INI"])


class M4aTotalZeroPolicy(unittest.TestCase):
    def tearDown(self):
        INI.unlink(missing_ok=True)
        picard_config._cache.clear()

    def run_album(self, script=None, policy=None, total="2"):
        root = Path(tempfile.mkdtemp(prefix="tm29A-m4a0-"))
        self.addCleanup(shutil.rmtree, root, True)
        ex.CONFIG_DIR, ex.STATE_DIR = root / "config", root / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        files = []
        for n in (1, 2):
            f = c.encode(root / "lib" / "album" / f"0{n}.m4a", 440 + 20 * n, 1)
            m = mutagen.File(f)
            m["trkn"] = [(n, 9)]
            m.save()
            files.append(f)
        d = {"name": "t", "sources": [{"path": str(root / "lib")}], "artwork": {"enabled": False},
             "mark_resolved": False,
             "picard": {"enable_tagger_scripts": bool(script), "list_of_scripts": [script] if script else []}}
        if policy:
            d["field_policy"] = {"default": "replace", "fields": {"totaltracks": policy}}
        prof = pf.from_dict(d)
        targets = [{"title": [f"T{n}"], "album": ["A"], "artist": ["B"], "albumartist": ["B"],
                    "tracknumber": [str(n)], "totaltracks": [total]} for n in (1, 2)]
        p = pl.build([pl.Proposal(source_dir=str(files[0].parent), files=[str(f) for f in files], targets=targets,
                                  provider="musicbrainz", release_id="x", confidence=100.0, margin=50.0,
                                  evidence=["x"], release={"album": ["A"], "albumartist": ["B"]})], prof)
        res = executor.Executor(p).run()
        self.assertFalse(res["counts"].get("failed"), res)
        return [mutagen.File(f)["trkn"][0] for f in files]

    def test_picard_preserved_total_stays(self):
        INI.write_text("[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags=totaltracks\n")
        picard_config._cache.clear()
        self.assertEqual(self.run_album("$set(totaltracks,0)"), [(1, 9), (2, 9)],
                         "a total Picard preserves was removed")

    def test_profile_locked_total_stays(self):
        self.assertEqual(self.run_album("$set(totaltracks,0)", policy="lock"), [(1, 9), (2, 9)],
                         "a total the profile locks was removed")

    def test_profile_preserved_total_stays_release_zero(self):
        self.assertEqual(self.run_album(policy="preserve", total="0"), [(1, 9), (2, 9)],
                         "a total the profile preserves was removed")


if __name__ == "__main__":
    unittest.main()
