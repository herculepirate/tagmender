# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""A described lyrics field ("lyrics:Verse", from a tagger script or plugin) written to a FLAC,
Ogg or WavPack file now REPLACES the lyrics the file already has.

tagio.stored() folds "lyrics:<desc>" into the target's "lyrics" for every family but ID3, so
the diff sees "lyrics": ["new verse"] against the file's "lyrics": ["my old lyrics"] and
replaces it. Picard 3.0.2 (VCommentFile._save, run on the same FLAC with LYRICS="my old
lyrics" and metadata["lyrics:Verse"] = "new verse") writes LYRICS = ["my old lyrics",
"new verse"]: the file's own lyrics are part of the metadata it saves, and every lyrics:* value
is appended to LYRICS (vorbis.py: name.startswith('lyrics:') -> 'lyrics', setdefault().append).

Regression: passes with TM_REPO=<prev> (which wrote a LYRICS:VERSE field and left LYRICS)."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

import mutagen  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class LyricsDescReplacesFileLyrics(Base):
    def test_file_lyrics_are_kept(self):
        for f in (self.f1, self.f2):
            fl = mutagen.File(f)
            fl["LYRICS"] = ["my old lyrics"]
            fl.save()
        prof = self.profile(picard={"enable_tagger_scripts": True,
                                    "list_of_scripts": ["$set(lyrics:Verse,new verse)"]})
        prop = self.proposal(targets=[target(1, "First"), target(2, "Second")])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", p["albums"][0]["conflicts"])
        executor.Executor(p).run()
        self.assertIn("my old lyrics", mutagen.File(self.f1).tags.get("LYRICS") or [])


if __name__ == "__main__":
    unittest.main()
