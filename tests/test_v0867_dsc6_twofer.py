# DSC-6 (docs/MATCHING_RULES.md): a folder named after the albums it holds, joined ("1992 - The
# Stranger & An Innocent Man" holding "1977 - The Stranger" and "1983 - An Innocent Man"), each
# tagged as its own album, disc 1 of 1, is one album: the set they were issued in (a twofer).
# Before, each sub-folder was planned as its own album and matched to that album's standalone CD;
# the set's barcode on the scans beside them never counted. Synthetic data only.
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def album(parent, name, album_title, artist="Band T", disc="1/1", first=1, n=3, freq=300, titles=None):
    """An album's folder: N tracks (10, 20, 30 s ...) numbered from FIRST, tagged as ALBUM_TITLE."""
    for i in range(n):
        meta = dict(title=f"{titles or album_title} {i}", album=album_title, albumartist=artist, artist=artist,
                    track=str(first + i), disc=disc)
        flac(parent / name, f"{first + i:02d}.flac", 10 * (i % 3 + 1), freq=freq + 10 * i, **meta)
    return parent / name


def twofer(lib, name="1992 - Alpha & Beta", artist="Band T", second_artist=None):
    top = lib / artist / name
    album(top, "1977 - Alpha", "Alpha", artist)
    album(top, "1983 - Beta", "Beta", second_artist or artist, freq=600)
    return top


def set_release(rid, artist, title, discs=("Alpha", "Beta")):
    """A MusicBrainz release of len(DISCS) CDs, three tracks each (10/20/30 s), titled as the albums."""
    rel = te.mb_release(rid, artist, title)
    rel["date"] = "1992-02"
    rel["media"] = [{"format": "CD", "position": n + 1, "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"{d} {i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"{d} {i}"}}
        for i, ms in enumerate(te.LENS)]} for n, d in enumerate(discs)]
    return rel


class DSC6Twofer(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def test_DSC6_a_folder_named_after_its_two_albums_is_one(self):
        top = twofer(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files)) for g in gs], [(top, 6)])
        a = ma.read_album(top, "Band T", top.name)
        self.assertEqual([(t["disc"], t["num"]) for t in a["tracks"]],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])

    def test_DSC6_the_set_is_identified_and_tagged_disc_n_of_2(self):
        top = twofer(self.lib)
        standalone = set_release("mb-alpha", "Band T", "Alpha", discs=("Alpha",))
        eng = self.engine(SetHttp(mb=[set_release("mb-set", "Band T", "Alpha & Beta"), standalone]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([g.folder for g in gs], [top])
        p = eng.identify(gs[0])
        self.assertEqual((p.status, p.release_id), ("identified", "mb-set"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"2"})

    def test_DSC6_joined_with_and_plus_and_in_any_order(self):
        for name in ("1992 - Beta and Alpha", "Alpha + Beta (2CD) [Label]", "ALPHA & beta"):
            with self.subTest(name=name):
                top = twofer(self.lib, name=name)
                self.assertEqual([g.folder for g in self.groups()], [top])
                shutil.rmtree(top)

    def test_DSC6_a_folder_named_otherwise_stays_apart(self):
        for name in ("Band T - Collection", "1992 - Alpha & Gamma", "1992 - Alpha", "Alpha & Beta & Gamma"):
            with self.subTest(name=name):
                top = twofer(self.lib, name=name)
                self.assertEqual(sorted(g.folder.name for g in self.groups()), ["1977 - Alpha", "1983 - Beta"])
                shutil.rmtree(top)

    def test_DSC6_two_artists_stay_apart(self):
        twofer(self.lib, second_artist="Band U")
        self.assertEqual(sorted(g.folder.name for g in self.groups()), ["1977 - Alpha", "1983 - Beta"])

    def test_DSC6_a_folder_with_tracks_of_its_own_stays_apart(self):
        top = twofer(self.lib)
        album(self.lib / "Band T", top.name, "Gamma", freq=900)
        self.assertNotIn(top, [g.folder for g in self.groups()] and
                         [g.folder for g in self.groups() if len(g.files) == 6])

    def test_DSC6_only_when_the_set_folder_is_asked_for(self):
        top = twofer(self.lib)
        gs = self.groups(top / "1983 - Beta")
        self.assertEqual([g.folder for g in gs], [top / "1983 - Beta"])

    def test_DSC6_a_bonus_folder_numbered_on_stays_on_the_album_disc(self):
        top = self.lib / "Band V" / "Delta"
        album(top, "", "Delta", "Band V")
        album(top, "Bonus", "Delta", "Band V", first=4, n=2, freq=700)
        a = ma.read_album(top, "Band V", "Delta")
        self.assertEqual({t["disc"] for t in a["tracks"]}, {1})

    def test_DSC6_disc_folders_tagged_one_and_two_keep_their_tags(self):
        top = self.lib / "Band V" / "Epsilon"
        album(top, "CD2", "Epsilon", "Band V", disc="1/2")
        album(top, "CD1", "Epsilon", "Band V", disc="2/2", freq=800, titles="Zeta")
        a = ma.read_album(top, "Band V", "Epsilon")
        # (the tags say which disc each folder is, whatever the folders' order)
        self.assertEqual([t["disc"] for t in a["tracks"]], [1, 1, 1, 2, 2, 2])
        self.assertEqual({t["title"] for t in a["tracks"] if t["disc"] == 1}, {"Epsilon 0", "Epsilon 1", "Epsilon 2"})


if __name__ == "__main__":
    unittest.main()
