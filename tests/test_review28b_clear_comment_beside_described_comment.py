"""FLAC/Ogg/WavPack: a tagger script that clears the plain comment while setting a described
one - "$set(comment:x,c1)$unset(comment)" or "...$set(comment,)" - leaves the file's old
comment ("ripped by EAC") in place; Picard 3.0.2 removes it and keeps only "c1 (x)".

In Picard the scripts run on the file's metadata, so the plain "comment" is gone from what
is saved, and VCommentFile._save writes the COMMENT field whole from what is left
("comment:x" as "c1 (x)", picard/formats/vorbis.py:333-337) - replacing the old value
(for "$set(comment,)" _remove_deleted_tags, vorbis.py:403-415, also drops values not ending
in ")"). TagMender treats the unset/emptied plain comment as "leave the file's", and its
Vorbis/APEv2 COMMENT write (tagio.py, the described-comment fold "text (desc)", ~l.214-250
and 360-370) adds "c1 (x)" beside the values the file already has, so both end up in
COMMENT (cause located by behaviour, not pinned to a single line). Checked end to end with Picard's
code (FLAC: Picard COMMENT=["c1 (x)"], TagMender ["ripped by EAC", "c1 (x)"]; WavPack the
same). MP3 and M4A are not affected this way. Fails with TM_REPO=prev too (not a regression).
"""
import unittest

import mutagen

import _review28b_common as c


class ClearCommentBesideDescribed(unittest.TestCase):
    def check(self, script):
        files = c.album(".flac", 2)
        for f in files:
            m = mutagen.File(str(f))
            m.tags["COMMENT"] = ["ripped by EAC"]
            m.save()
        p, res = c.run(files, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        for f in files:
            self.assertEqual(mutagen.File(str(f)).tags.get("COMMENT"), ["c1 (x)"])

    def test_unset(self):
        self.check("$set(comment:x,c1)$unset(comment)")

    def test_set_empty(self):
        self.check("$set(comment:x,c1)$set(comment,)")


if __name__ == "__main__":
    unittest.main()
