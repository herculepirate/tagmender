# Fifteenth independent review (B): this test failed before its fix.
"""naming.py's %_multiartist% is not Picard's. Picard (album.py _load_tracks) sets
~multiartist on EVERY track of a release whose tracks have more than one artist between them,
and its file naming presets ("Preset 3", TagMender's "picard" preset) then name every track
"NN Artist - Title". TagMender's Ctx sets it only on a track whose own artist differs from the
album artist, and the planner never passes the ~multiartist mb_tags.release_tags works out
(engine._identified drops every hidden variable from the targets). On a "Band" album with one
"Band feat. Guest" track Picard names track 1 "01 Band - T0", TagMender "01 T0": the files of
a library Picard named are all renamed. The same goes for every hidden variable: a tagger
script's $set(_name,...), which Picard keeps in the track's metadata for the file naming script
to read (a common Picard recipe), never reaches TagMender's naming."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)
import mb_tags  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


def release_with_guest():
    rel = mb_release(MBID, "Band", "Album")
    t = rel["media"][0]["tracks"][1]
    t["artist-credit"] = [{"name": "Band", "joinphrase": " feat. ", "artist": {"id": "a-1", "name": "Band",
                                                                               "sort-name": "Band, The"}},
                          {"name": "Guest", "joinphrase": "", "artist": {"id": "g-1", "name": "Guest",
                                                                          "sort-name": "Guest"}}]
    return rel


class MultiArtistNaming(Base):
    def test_mb_tags_sets_picards_multiartist_on_every_track(self):
        tags = mb_tags.release_tags(release_with_guest())
        self.assertEqual([t.get("~multiartist") for t in tags], [["1"], ["1"], ["1"]])

    def test_picard_preset_names_every_track_with_its_artist(self):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir()
        e = self.engine(FakeHttp(mb=[release_with_guest()]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}], naming={"preset": "picard", "template": ""})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        names = [Path(fe["dst"]).stem for fe in a["files"]]
        # Picard 3.0.2 (Preset 3): $if(%_multiartist%,%artist% - ,) on every track of this release
        # (disc 1 of 2: the release's DVD counts, in Picard too)
        self.assertEqual(names, ["1-01 Band - T0", "1-02 Band feat. Guest - T1", "1-03 Band - T2"], names)

    def test_naming_reads_a_variable_a_tagger_script_set(self):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir()
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}],
                        picard={"enable_tagger_scripts": True, "list_of_scripts": ["$set(_shelf,Live Shelf)"]},
                        naming={"preset": "tagmender", "template": "%_shelf%/%album%/$num(%tracknumber%,2) %title%"})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        dirs = sorted({str(Path(fe["dst"]).parent.relative_to(dest)) for fe in a["files"]})
        self.assertEqual(dirs, ["Live Shelf/Album"], dirs)


if __name__ == "__main__":
    unittest.main()
