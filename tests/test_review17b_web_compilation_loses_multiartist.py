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


class WebCompilationLosesMultiartist(Base):
    def test_web_compilation_tracks_named_with_their_artists(self):
        d = self.album("Various Artists", "Hits")
        dest = self.tmp / "dest"
        dest.mkdir()
        e = self.engine(FakeHttp(), operation="move", destination=str(dest), sources=[{"path": str(self.lib)}],
                        providers={"web": {"enabled": True}}, naming={"preset": "picard"})
        tracks = [{"title": f"T{i}", "album": "Hits", "albumartist": "Various Artists", "artist": f"Singer {i}",
                   "tracknumber": str(i + 1), "totaltracks": "3"} for i in range(3)]
        best = {"cand": {"id": "x", "url": "https://x.bandcamp.com/album/hits", "title": "Hits",
                         "artist": "Various Artists"}, "confidence": 99.0}
        with mock.patch.object(ma, "process", return_value={"status": "nomatch", "why": []}), \
                mock.patch.object(web_tag, "find", return_value=("Bandcamp", best, [])), \
                mock.patch.object(web_tag, "tags_for", return_value=tracks):
            a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        names = sorted(Path(fe["dst"]).stem for fe in a["files"])
        self.assertEqual(names, ["01 Singer 0 - T0", "02 Singer 1 - T1", "03 Singer 2 - T2"])


if __name__ == "__main__":
    unittest.main()
