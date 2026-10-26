# Fourteenth independent review (Picard code): this test failed before its fix.
"""plan.diff_tags + tagio.write: the previous TagMender lower-cased every field name, so the
files it tagged carry "performer:hammond organ" (TMCL role "hammond organ"; Vorbis
"Jon Lord (hammond organ)"). Now the role keeps MusicBrainz's case ("performer:Hammond
organ"): the planner sees a new field, and the writer replaces only the roles named -
the old lower-case credit stays, so a retag of an already tagged library lists every such
performer twice (Picard, which writes TMCL / PERFORMER whole, never does)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

os.environ["TAGMENDER_PICARD_INI"] = str(Path(tempfile.mkdtemp()) / "no-Picard.ini")
import plan  # noqa: E402
import profiles  # noqa: E402
import tagio  # noqa: E402


def retag(p, target):
    if hasattr(tagio, "stored"):
        target = tagio.stored(p, {k: v for k, v in target.items() if tagio.holds(p, k)})
    changes, _rows = plan.diff_tags(tagio.read(p), target, profiles.default(), 100.0)
    if changes:
        tagio.write(p, changes)
    return tagio.read(p)


class RoleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def check(self, ext):
        p = make(self.tmp / f"a{ext}")
        tagio.write(p, {"performer:hammond organ": ["Jon Lord"], "title": ["Child in Time"]})  # as tagged before
        got = retag(p, {"performer:Hammond organ": ["Jon Lord"], "title": ["Child in Time"]})
        credits = {k: v for k, v in got.items() if k.lower().startswith("performer")}
        self.assertEqual(sum(v.count("Jon Lord") for v in credits.values()), 1,
                         f"{ext}: the organ credit is now there twice: {credits}")

    def test_mp3(self):
        self.check(".mp3")

    def test_flac(self):
        self.check(".flac")


if __name__ == "__main__":
    unittest.main()
