# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B). Regression.
"""naming.Ctx no longer derives %_multiartist% per track ("1" when the track artist differs
from the album artist); the engine sets Picard's ~multiartist instead - but only in
Engine._identified (from MusicBrainz, or for Discogs-only releases). A release found by the
web lookup (Engine._web: Bandcamp, a Discogs page found on the web ...) gets no ~multiartist
at all, so the "picard" preset's $if(%_multiartist%,%artist% - ,) names every track of a
various-artists compilation "01 Title" instead of "01 Artist - Title" - as Picard names
tracks by more than one artist (picard/album.py _load_tracks: multiartists = len(artists) > 1),
and as the previous version named them. Files named by the previous version are renamed."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

import match_albums as ma  # noqa: E402
import web_tag  # noqa: E402
from test_engine import Base, FakeHttp  # noqa: E402  (the repo's own test helpers)
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


class WebCompilationLosesMultiartist(Base):
    def test_web_compilation_tracks_named_with_their_artists(self):
        # (an album from the web is now a Partial album, tagged in place and never renamed:
        # its ~multiartist is what the naming script would see)
        d = self.album("Various Artists", "Hits")
        e = self.engine(FakeHttp(), providers={"web": {"enabled": True}}, naming={"preset": "picard"})
        tracks = [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 10.0 * (i + 1), "artist": f"Singer {i}"}
                  for i in range(3)]
        cand = web_tag._cand("bandcamp", "x", "https://x.bandcamp.com/album/hits", "Various Artists", "Hits", "",
                             tracks=tracks, digital=True)
        with mock.patch.object(ma, "process", return_value={"status": "nomatch", "why": []}), \
                mock.patch.object(web_tag, "album_candidates", return_value=[("Bandcamp", cand)]):
            p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "partial"), p.why)
        self.assertEqual([t["artist"] for t in p.targets], [["Singer 0"], ["Singer 1"], ["Singer 2"]])
        self.assertTrue(all(t.get("~multiartist") == ["1"] for t in p.targets))

if __name__ == "__main__":
    unittest.main()
