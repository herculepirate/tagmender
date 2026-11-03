# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A naming script that uses a custom tag a tagger script sets with capitals - $set(MyTag,Live)
and "%MyTag%/%title%" - names the files right the first time, but a second TagMender run over
those files (FLAC, Ogg, Opus, M4A, WavPack) finds %MyTag% empty and moves every file out
of its folder: "Live/Track 1.flac" -> "Track 1.flac". Picard 3.0.2 names the file
"Live/Track 1.flac" both times (its tagger script sets MyTag again; checked end to end with
Picard's own code on the file TagMender wrote). MP3 and WMA are not affected (the tag keeps
the spelling and tagio.spellings gives it back).

Also on the first run, %mytag% (lower case) gets the value in every format, where Picard
leaves it empty (its names are case-sensitive): "%MyTag% %mytag%" is "Live Live" in TagMender,
"Live " in Picard - and then "Live " on TagMender's second run, so that file is renamed too.

Cause: plan.Planner.album builds the naming fields from `merged` (plan.py:~970-1010), whose
names are lower case (tagio.read -> field_name). The spelling a script used is restored only
for fields that change (_spelled_names: `field_name(k) in changes`, plan.py:513-516), and only
for an ID3 TXXX through tagio.spellings. On the second run MyTag is unchanged (the file already
holds it), so the naming script sees only "mytag"; on the first run it sees both.

Fix: build the naming fields from the tagger script's output as Picard does (the scripted
target with its own spelling), whether or not the field changes; drop the lower-cased copy of
a field a script spelled otherwise.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402

SCRIPT = "$set(MyTag,Live)"


def rerun(dest, ext, naming):
    """TagMender again over the files its first run made (the same profile and script)."""
    lib = Path(tempfile.mkdtemp(prefix="tm29B-again-")) / "lib" / "album"
    lib.mkdir(parents=True)
    files = []
    for x in sorted(Path(dest).rglob("*" + ext)):
        shutil.copy(x, lib / x.name)
        files.append(lib / x.name)
    return c.move_run(files, naming, [SCRIPT])


class CustomTagCaseSecondRun(unittest.TestCase):
    def _twice(self, ext, naming="%MyTag%/%title%"):
        files = c.album(ext, 2)
        p, res, dest = c.move_run(files, naming, [SCRIPT])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(p, res))
        first = c.names(dest)
        p, res, dest2 = rerun(dest, ext, naming)
        self.assertEqual(res["counts"], {"done": 1}, c.detail(p, res))
        return first, c.names(dest2)

    def test_flac(self):
        first, second = self._twice(".flac")
        self.assertEqual(first, ["Live/Track 1.flac", "Live/Track 2.flac"])
        self.assertEqual(second, first, "a second run names the files as the first (Picard: Live/...)")

    def test_opus(self):
        first, second = self._twice(".opus")
        self.assertEqual(second, first)

    def test_m4a(self):
        first, second = self._twice(".m4a")
        self.assertEqual(second, first)

    def test_wavpack(self):
        first, second = self._twice(".wv")
        self.assertEqual(second, first)

    def test_lower_case_reference_first_run(self):
        first, _second = self._twice(".mp3", "%MyTag% [%mytag%]/%title%")
        self.assertEqual(first, ["Live []/Track 1.mp3", "Live []/Track 2.mp3"],
                         "Picard: %mytag% is empty (only MyTag was set)")


if __name__ == "__main__":
    unittest.main()
