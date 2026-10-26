# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""naming.Ctx._derive still sets %_multiartist% on any track whose artist differs from the album
artist - on top of Picard's own ~multiartist, which this version now passes to naming. Picard
(picard/album.py _load_tracks, lines 799-803) sets ~multiartist only when the release's tracks
have MORE THAN ONE artist between them (len(artists) > 1, the album artist not counted). On a
release whose every track is credited "Band feat. Guest" (a single or EP with a featured
artist, album artist "Band") Picard sets no ~multiartist, and its file naming preset (Preset 3,
TagMender's "picard" preset: $if(%_multiartist%,%artist% - ,)) names the tracks "01 T0" -
TagMender names them "01 Band feat. Guest - T0". (The same when a tagger script $unset-s
_multiartist.) Not new in this version, but it is the "picard" preset's naming that this
version set out to make Picard's."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)
import mb_tags  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


def release_all_featuring():
    rel = mb_release(MBID, "Band", "Album")
    rel["media"] = rel["media"][:1]                               # the CD only
    for t in rel["media"][0]["tracks"]:
        t["artist-credit"] = [{"name": "Band", "joinphrase": " feat. ",
                               "artist": {"id": "a-1", "name": "Band", "sort-name": "Band, The"}},
                              {"name": "Guest", "joinphrase": "", "artist": {"id": "g-1", "name": "Guest",
                                                                              "sort-name": "Guest"}}]
    return rel


class OneCreditOnEveryTrack(Base):
    def test_no_multiartist_from_mb_tags(self):
        tags = mb_tags.release_tags(release_all_featuring())
        self.assertEqual([t.get("artist") for t in tags], [["Band feat. Guest"]] * 3)
        self.assertEqual([t.get("~multiartist") for t in tags], [None] * 3)     # as Picard: one artist

    def test_picard_preset_names_as_picard(self):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir()
        e = self.engine(FakeHttp(mb=[release_all_featuring()]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}], naming={"preset": "picard", "template": ""})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        names = [Path(fe["dst"]).stem for fe in a["files"]]
        self.assertEqual(names, ["01 T0", "02 T1", "03 T2"], names)


if __name__ == "__main__":
    unittest.main()
