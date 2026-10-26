# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A FLAC / Ogg / Opus file whose lyrics are in UNSYNCEDLYRICS (the Mp3tag and Bandcamp
convention) gets a second set of lyrics from LRCLIB at the Audiophile level (or with "lyrics":
{"enabled": true}).

Picard 3.0.2 reads UNSYNCEDLYRICS as "lyrics" (vorbis.py, PICARD-3199: "We read both but
always write LYRICS"). tagio.read has no such mapping (VORBIS_R): it gives "unsyncedlyrics", so
levels.audiophile's check - `any(k == "lyrics" or k.startswith("lyrics:") for k in have[i])` -
finds no lyrics, looks them up, and the plan adds LYRICS beside the user's own: two different
lyrics in one file, and a player that reads LYRICS shows LRCLIB's instead of the user's. No
network: LRCLIB is a stub."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

import mutagen  # noqa: E402
import engine  # noqa: E402


class StubHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, kind=None, *a, **k):
        self.calls.append(url)
        return {"plainLyrics": "la la la from LRCLIB"}


class UnsyncedLyrics(unittest.TestCase):
    def test_own_unsyncedlyrics_count_as_lyrics(self):
        d = Path(tempfile.mkdtemp())
        files = []
        for n in (1, 2):
            f = c.make(d / "lib" / "al" / f"0{n}.flac")
            m = mutagen.File(f)
            m["UNSYNCEDLYRICS"] = [f"my own lyrics {n}"]
            m.save()
            files.append(f)
        c.fresh_state()
        prof = c.profile(d / "lib", lyrics={"enabled": True, "embed": True, "sidecar": False, "synced": False})
        prop = c.proposal(files)
        http = StubHttp()
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=http), prop)
        p = c.pl.build([prop], prof)
        c.executor.Executor(p).run()
        self.assertEqual(http.calls, [])                       # (both tracks have lyrics)
        self.assertNotIn("LYRICS", mutagen.File(files[0]).tags.keys())


if __name__ == "__main__":
    unittest.main()
