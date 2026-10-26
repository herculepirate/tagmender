# Nineteenth independent review (B): this test failed before its fix.
# Nineteenth independent review (B).
"""A tagger script that sets a comment or lyrics with a description ("comment:ripper",
"lyrics:verse" - Picard's names for a COMM/USLT frame with that description) is written by
TagMender as a custom field: an ID3 TXXX:comment:ripper / TXXX:lyrics:verse frame, a Vorbis
COMMENT:RIPPER / LYRICS:VERSE field. Picard 3.0.2 writes COMM (desc "ripper", lang "eng") and
USLT (desc "verse") in ID3 (picard/formats/id3.py: name.startswith('comment:') ->
_save_comment_tag, name.startswith('lyrics:') -> _save_lyrics_tag), and in Vorbis COMMENT=
"EAC (ripper)" and LYRICS (picard/formats/vorbis.py). Checked with the formats harness
(tests/data/picard_parity/make_formats.py) on these two fields. Players see neither the comment
nor the lyrics."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review19b_common  # noqa: E402,F401

import mutagen  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)

SCRIPT = "$set(comment:ripper,EAC)$set(lyrics:verse,la la)"


class ScriptCommentLyricsDescFrames(Base):
    def _run(self, files):
        prof = self.profile(picard={"enable_tagger_scripts": True, "list_of_scripts": [SCRIPT]})
        prop = self.proposal(files=[str(f) for f in files], targets=[target(1, "First"), target(2, "Second")])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", p["albums"][0]["conflicts"])
        executor.Executor(p).run()

    def test_mp3_gets_comm_and_uslt_frames(self):
        m1 = audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        m2 = audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        self._run([m1, m2])
        tags = mutagen.File(m1).tags
        keys = sorted(tags.keys())
        self.assertNotIn("TXXX:comment:ripper", keys, keys)
        self.assertNotIn("TXXX:lyrics:verse", keys, keys)
        comm = [f for f in tags.getall("COMM") if f.desc == "ripper"]
        self.assertEqual([str(x) for c in comm for x in c.text], ["EAC"], keys)
        uslt = [f for f in tags.getall("USLT") if f.desc == "verse"]
        self.assertEqual([u.text for u in uslt], ["la la"], keys)

    def test_flac_gets_comment_and_lyrics_fields(self):
        self._run([self.f1, self.f2])
        tags = mutagen.File(self.f1).tags
        self.assertNotIn("COMMENT:RIPPER", {k.upper() for k in tags.keys()})
        self.assertEqual(tags.get("COMMENT"), ["EAC (ripper)"])
        self.assertEqual(tags.get("LYRICS"), ["la la"])


if __name__ == "__main__":
    unittest.main()
