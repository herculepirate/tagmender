# Eighteenth independent review (B): this test failed before its fix.
# Eighteenth independent review (B). Regression.
"""engine.Engine.identify now sets ~multiartist on every identified album whose TARGETS' first
artists differ - including a MusicBrainz release after the Discogs Enrichment has been applied.
Picard computes %_multiartist% from MusicBrainz's own track artists only, before any plugin
runs (picard/album.py _finalize_loading_track: artists.add(tm['artist']) comes before
run_track_metadata_processors; the Discogs Enrichment plugin applies its release later, in
its album processor's callback, and never touches ~multiartist). mb_tags already sets
~multiartist from the MusicBrainz release (all its tracks), so for a MusicBrainz release the
new check can only add a flag Picard does not set.

With the plugin's "Discogs only" tag source (merge_mode=discogs_only), a Discogs track credit
"Band M feat. Guest" on one track of a single-artist MusicBrainz release now makes every
track "multi-artist": the "picard" naming preset ($if(%_multiartist%,%artist% - ,)) names all
files "01 Band M - T0" instead of Picard's "01 T0", and the previous version's files are moved
on the next run. (The same with a profile's field_sources {"artist": ["discogs"]}.)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review18b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, dg_release, mb_release  # noqa: E402  (the repo's own helpers)


class DiscogsArtistMakesMbReleaseMultiartist(Base):
    def test_names_as_picard_for_a_single_artist_musicbrainz_release(self):
        self.picard_ini(merge_mode="discogs_only")
        d = self.album("Band M", "Album M")
        dest = self.tmp / "dest"
        dest.mkdir()
        dg = dg_release(950, "Band M", "Album M")
        dg["tracklist"][1]["artists"] = [{"name": "Band M", "join": "feat."}, {"name": "Guest", "join": ""}]
        e = self.engine(FakeHttp(mb=[mb_release("mb-m", "Band M", "Album M", discogs=950)], dg=[dg]),
                        operation="move", destination=str(dest), sources=[{"path": str(self.lib)}],
                        naming={"preset": "picard"})
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["provider"], "musicbrainz+discogs", a["identification"])
        names = sorted(Path(fe["dst"]).stem for fe in a["files"])
        # (MusicBrainz: every track by "Band M" - Picard sets no %_multiartist%)
        self.assertEqual(names, ["01 T0", "02 T1", "03 T2"])


if __name__ == "__main__":
    unittest.main()
