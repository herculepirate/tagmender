# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression; the
# backup code is unchanged since at least review 20).
"""M4A: an album with a file that has a flag atom (cpil, pgap or pcst) cannot be tagged at
all. The run fails with "'bool' object is not iterable; album put back as it was".

Who writes these atoms: iTunes (cpil on compilations, pgap on gapless albums, which it sets
on many imports), Picard 3.0.2 (cpil=True on every Various Artists release; pgap/pcst from
the gapless/podcast tags), and TagMender itself (cpil on Various Artists releases, as Picard).
So a second TagMender run over an M4A Various Artists album it tagged, or a run over one that
Picard or iTunes tagged, fails every time: the album is never re-tagged, renamed or moved.
Found by the end-to-end second-run sweep (TagMender run again over TagMender's output and over
Picard's output: every M4A compilation failed).

Cause: tag_albums._backup_raw (tag_albums.py:640) records an MP4 tag's values with
_mp4_value(v, ...) (tag_albums.py:704-714), which iterates over each value; mutagen gives a
flag atom as a plain bool (MP4Tags["cpil"] is True), not a list - "for v in values" raises
TypeError, the executor (executor.py:1088-1100) rolls the album back.

Fix: in _mp4_value, wrap a value that is not a list or tuple (a bool) as a one-value record
with a marker, and in restore_tags (tag_albums.py:~864) put it back as the scalar.
"""
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402


def flag(files, atom):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags[atom] = True
        m.tags["\xa9nam"] = ["old title"]
        m.save()


class M4AFlagAtom(unittest.TestCase):
    def _run(self, atom):
        files = c.album(".m4a", 2)
        flag(files, atom)
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(mutagen.File(str(files[0])).tags["\xa9nam"], ["Track 1"])

    def test_compilation_cpil(self):
        self._run("cpil")

    def test_gapless_pgap(self):
        self._run("pgap")

    def test_podcast_pcst(self):
        self._run("pcst")

    def test_second_run_over_own_compilation(self):
        files = c.album(".m4a", 2)
        targets = c.proposal(files).targets
        for t in targets:
            t["compilation"] = ["1"]
            t["albumartist"] = ["Various Artists"]
        p, res = c.run(files, targets=[dict(t) for t in targets])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertIs(mutagen.File(str(files[0])).tags.get("cpil"), True, "TagMender wrote cpil, as Picard")
        for t in targets:                       # (MusicBrainz corrected a title since)
            t["title"] = [t["title"][0] + " (Remastered)"]
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))


if __name__ == "__main__":
    unittest.main()
