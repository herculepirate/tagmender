# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""engine._album_only (an album whose pressing is unknown) keeps only the hidden variables in
ALBUM_ONLY_HIDDEN - a fixed list of Picard's own. A variable the user's tagger script $set
(the common Picard recipe $set(_shelf,...) for the file naming script, which this version now
passes to naming) is not on it and is dropped, while the same script's ordinary tags
($set(shelf,...)) are kept. The same album is then filed under another folder when it is an
album-only match than when it is identified with its pressing (here "Album/..." instead of
"Metal/Album/..."), and a later run that finds its pressing moves it again."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)
import match_albums as ma  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class AlbumOnlyScriptVariable(Base):
    def dirs(self, album_only):
        d = self.album("Band", "Album")
        dest = self.tmp / ("dest-ao" if album_only else "dest-full")
        dest.mkdir()
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}],
                        picard={"enable_tagger_scripts": True,
                                "list_of_scripts": ["$set(_shelf,Metal)$set(shelf,Metal)"]},
                        naming={"preset": "tagmender", "template": "%_shelf%/%album%/$num(%tracknumber%,2) %title%"})
        best = {"source": "mb", "id": MBID, "mb_id": MBID, "url": "u", "confidence": 100.0, "notes": []}
        tied = [{"source": "mb", "id": MBID, "label": "Label MB", "country": "DE", "formats": ["cd"], "year": 2005},
                {"source": "mb", "id": "other", "label": "Label MB", "country": "US", "formats": ["cd"], "year": 2005}]
        out = ({"status": "review", "why": ["runner-up within 0.0 points"], "best": best, "album_only": {"tied": tied}}
               if album_only else {"status": "auto", "why": [], "best": best, "margin": 20.0})
        with mock.patch.object(ma, "process", return_value=out):
            a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        return a, sorted({str(Path(fe["dst"]).parent.relative_to(dest)) for fe in a["files"]})

    def test_album_only_match_keeps_the_scripts_naming_variable(self):
        _a, full = self.dirs(album_only=False)
        self.assertEqual(full, ["Metal/Album"])
        a, ao = self.dirs(album_only=True)
        self.assertEqual(a["files"][0]["changes"].get("shelf"), ["Metal"])      # the script's tag stays
        self.assertEqual(ao, ["Metal/Album"], "the script's %_shelf% is dropped for an album-only match")


if __name__ == "__main__":
    unittest.main()
