# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""Lyrics (profile "lyrics": {"enabled": true}, or Audiophile) are looked up and embedded in an
MP3 that already has lyrics, when its USLT frame has a description: the file ends up with two
USLT frames - its own and LRCLIB's.

levels.audiophile skips a track whose tags already hold lyrics - have[i].get("lyrics") - and
"measurements never replace what the file already has" checks the same key. tagio.read now
names a described USLT "lyrics:<desc>" (it was read as "lyrics"), so neither check sees it:
the lookup runs and "lyrics" is added as a new USLT(xxx, "") next to the old frame.

Regression: passes with TM_REPO=<prev>. No network: LRCLIB is a stub."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

import mutagen  # noqa: E402
from mutagen.id3 import USLT  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)


class StubHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, kind=None, *a, **k):
        self.calls.append(url)
        return {"plainLyrics": "la la la from LRCLIB"}


class LyricsOverDescribedUslt(Base):
    def test_existing_described_lyrics_are_not_doubled(self):
        files = []
        for n, freq in ((1, 440), (2, 550)):
            f = audio(self.album / f"0{n} {'ab'[n - 1]}.mp3", freq, album="Some Album", title="ab"[n - 1],
                      artist="Some Artist")
            t = mutagen.File(f).tags
            t.add(USLT(encoding=3, lang="eng", desc="Lyrics", text="my own lyrics"))
            t.save(f)
            files.append(f)
        prof = self.profile(lyrics={"enabled": True, "embed": True, "sidecar": False, "synced": False})
        prop = self.proposal(files=[str(f) for f in files], targets=[target(1, "First"), target(2, "Second")])
        http = StubHttp()
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=http), prop)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        uslt = mutagen.File(files[0]).tags.getall("USLT")
        self.assertEqual([(u.desc, u.text) for u in uslt], [("Lyrics", "my own lyrics")])
        self.assertEqual(http.calls, [])              # (nothing to look up: both tracks have lyrics)


if __name__ == "__main__":
    unittest.main()
