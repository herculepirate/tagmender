# DSC-3 / MED-14 (docs/MATCHING_RULES.md): a box set's discs filed as "CDnn <title>" folders
# beside "CDxx-yy <title>/Part N" folders (two discs of one album of the set) are one album,
# in the discs' order; and a set whose every disc's album tag ends in "[... 10CD Box]" is a CD
# rip by its own tags (the CD box, not the same set's download). Before, each "Part N" folder
# was an album of its own - identified to another release and filed into another folder than
# the rest of the box - and a CD box's files tied with its download and fell to a web source.
# The files' own Discogs ID stays a lead only (TAG-2): fetched and scored, never chosen for it.
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
from test_v0821_engine_rules import flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


def audio_file(d, name, secs, rate, bits, freq, **meta):
    """A short sine file at this rate and bit depth (secs whole: whole CD sectors at 44.1 kHz)."""
    d.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    fmt = ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if bits == 24 else ["-sample_fmt", "s16"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", str(rate), "-ac", "1", *fmt, *tags, str(d / name), "-y"], check=True)


def disc_folder(parent, name, n, album, artist):
    """Disc n of a set: three short tracks titled "D<n>-<i>", tagged with their disc number."""
    for i in range(3):
        flac(parent / name, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, title=f"D{n}-{i}",
             album=album, albumartist=artist, artist=artist, track=str(i + 1), disc=str(n))
    return parent / name


def box(lib):
    """A 4-disc box: CD01, CD02-03 (Part 1 = disc 2, Part 2 = disc 3), CD04; three tracks a disc."""
    top = lib / "Band H" / "2019 - Sets Box (4CD)"
    disc_folder(top, "CD01 1974 Alpha", 1, "Alpha [2019 Sets 4CD Box]", "Band H")
    disc_folder(top / "CD02-03 1973 Beta Out-Takes", "Part 1", 2, "Beta 1 & 2 [2019 Sets 4CD Box]", "Band H")
    disc_folder(top / "CD02-03 1973 Beta Out-Takes", "Part 2", 3, "Beta 1 & 2 [2019 Sets 4CD Box]", "Band H")
    disc_folder(top, "CD04 1975 Gamma", 4, "Gamma [2019 Sets 4CD Box]", "Band H")
    return top


class DSC3_DiscRangeParts(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def test_DSC3_part_folders_of_a_disc_range_are_discs_of_the_box(self):
        top = box(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files), g.others) for g in gs], [(top, 12, [])])
        album = ma.read_album(top, "Band H", top.name)
        order = [(t["disc"], Path(t["path"]).parent.name) for t in album["tracks"]][::3]
        self.assertEqual(order, [(1, "CD01 1974 Alpha"), (2, "Part 1"), (3, "Part 2"), (4, "CD04 1975 Gamma")])
        self.assertEqual(album["discs"], 4)

    def test_DSC3_the_part_number_names_the_disc(self):
        P = Path
        self.assertEqual(engine.box_part_disc(P("/b/CD02-03 Beta/Part 1")), 2)
        self.assertEqual(engine.box_part_disc(P("/b/CD10-11 1975 Live, London/Part 2")), 11)
        self.assertIsNone(engine.box_part_disc(P("/b/CD02-03 Beta/Part 3")))       # (outside the range)
        self.assertIsNone(engine.box_part_disc(P("/b/Live At The Rainbow/Part 1")))  # (no disc range above)
        self.assertIsNone(engine.box_part_disc(P("/b/CD03-02 Beta/Part 1")))
        # (a disc's title beginning with a number: no disc range)
        self.assertIsNone(engine.box_part_disc(P("/b/CD1 - 12 Inch Mixes/Part 1")))
        self.assertIsNone(engine.box_part_disc(P("/b/CD1-12 Inch Mixes/Part 1")))
        self.assertIsNone(engine.box_part_disc(P("/b/CD1-12\" Mixes/Part 1")))
        self.assertIsNone(engine.box_part_disc(P("/b/CD1-40 Greatest/Part 1")))
        self.assertEqual(engine.box_part_disc(P("/b/CD2-3/Part 2")), 3)

    def test_DSC3_a_part_tagged_as_another_disc_keeps_the_box_apart(self):
        top = self.lib / "Band H" / "Box (3CD)"
        disc_folder(top, "CD01 Alpha", 1, "Alpha [Box]", "Band H")
        disc_folder(top / "CD02-03 Beta", "Part 1", 2, "Beta [Box]", "Band H")
        disc_folder(top / "CD02-03 Beta", "Part 2", 1, "Beta [Box]", "Band H")     # (says disc 1, not 3)
        gs = self.groups()
        self.assertGreater(len(gs), 1)
        # (the two parts are still one album of their own: Beta's two discs)
        self.assertIn((top / "CD02-03 Beta", 6), [(g.folder, len(g.files)) for g in gs])

    def test_DSC3_only_the_range_folder_asked_for(self):
        top = box(self.lib)
        gs = self.groups(top / "CD02-03 1973 Beta Out-Takes")
        self.assertEqual([(g.folder.name, len(g.files)) for g in gs], [("CD02-03 1973 Beta Out-Takes", 6)])

    def test_DSC3_an_artist_folder_of_albums_is_not_one_box(self):
        artist = self.lib / "Band N"
        for n, name in ((1, "2001 - Album X"), (2, "2003 - Album Y")):
            for i in range(2):
                flac(artist / name, f"{i + 1:02d}.flac", 10, title=f"T{i}", album=name[7:], albumartist="Band N",
                     artist="Band N", track=str(i + 1), disc="1")
        # an ordinary 2-CD album in "CD1"/"CD2" stays one album
        disc_folder(artist / "2005 - Album Z", "CD1", 1, "Album Z", "Band N")
        disc_folder(artist / "2005 - Album Z", "CD2", 2, "Album Z", "Band N")
        # "Part 1"/"Part 2" of an album that is no disc range: two folders, as before
        for n in (1, 2):
            flac(artist / "2007 - Live" / f"Part {n}", "01.flac", 10, title=f"L{n}", album=f"Live Part {n}",
                 albumartist="Band N", artist="Band N", track="1")
        got = sorted((str(g.folder.relative_to(artist)), len(g.files)) for g in self.groups())
        self.assertEqual(got, [("2001 - Album X", 2), ("2003 - Album Y", 2), ("2005 - Album Z", 6),
                               ("2007 - Live/Part 1", 1), ("2007 - Live/Part 2", 1)])


class DSC4_AlbumWideTrackTotal(unittest.TestCase):
    def test_DSC4_a_box_of_discs_some_of_equal_counts_saying_the_sum(self):
        def infos(counts, total):
            return [(Path(f"/b/CD{d}/{n:02d}.flac"), {"disc_eff": d, "num": n, "tracktotal": total})
                    for d, c in enumerate(counts, 1) for n in range(1, c + 1)]
        self.assertEqual(ma._track_total(infos([2, 9, 6, 9], 26)), 26)      # (was 104: 26 per disc)
        self.assertEqual(ma._track_total(infos([5, 5], 10)), 20)           # (two equal discs: per disc)
        self.assertEqual(ma._track_total(infos([2, 9, 6, 9], 30)), 120)    # (no exact sum: per disc)


def dg_set(rid, fmt, durs, title="Album K", artist="Band K", codec="FLAC"):
    """A Discogs release of two discs, three tracks each, positions "1-1" ... "2-3"."""
    rel = te.dg_release(rid, artist, title)
    rel["formats"] = [{"name": "CD", "qty": "2", "descriptions": ["Album"]}] if fmt == "cd" else \
        [{"name": "File", "qty": "6", "descriptions": [codec, "Album"]}]
    rel["tracklist"] = [{"type_": "track", "position": f"{n}-{i + 1}", "title": f"{'TU'[n - 1]}{i}", "duration": d}
                        for n in (1, 2) for i, d in enumerate(durs)]
    return rel


class MED14_TheSetsOwnTags(te.Base):
    def make(self, dg_id="", rate=44100, bits=16, extra_secs=0.0):
        """The set's six files: CD audio by default (44.1 kHz, 16 bits, whole sectors)."""
        top = self.lib / "Band K" / "2020 - Album K"
        for n, sub in ((1, "Alpha"), (2, "Beta")):
            for i in range(3):
                extra = {"discogs_release_id": dg_id} if dg_id else {}
                audio_file(top / f"CD0{n} - {sub}", f"{i + 1:02d}.flac", 10 * (i + 1) + extra_secs, rate, bits,
                           300 + 100 * n + 10 * i, title=f"{'TU'[n - 1]}{i}", album=f"{sub} [2020 Album K 2CD Box]",
                           albumartist="Band K", artist="Band K", track=str(i + 1), disc=str(n), totaldiscs="2",
                           **extra)
        return top

    def copy(self, **kw):
        top = self.make(**kw)
        album = ma.read_album(top, "Band K", top.name)
        cd = mc.score_candidate(album, ma.discogs_candidate(dg_set(9101, "cd", ("0:10", "0:20", "0:30")),
                                                            self.engine(te.FakeHttp()).dm), self.engine(te.FakeHttp()).cfg)
        return album, cd

    def test_MED14_the_audio_outranks_the_tags(self):
        # hi-res audio, or CD-quality tracks that are no whole CD sectors: never a CD, whatever
        # the album tags copied from the box's titles say
        for kw, kind in (({"rate": 96000, "bits": 24}, ""), ({"extra_secs": 0.01}, "digital")):
            with self.subTest(**kw):
                album, cd = self.copy(**kw)
                self.assertEqual(album["tags_media"], "cd")
                self.assertEqual(album["media_hint"], "")                 # (never as the folder name)
                self.assertEqual(mc.copy_kind(album), kind)
                self.assertEqual(ma.copy_source(album), kind)
                self.assertNotIn("cd release", str(cd.get("clues")))
                for d in (self.lib / "Band K").iterdir():
                    for f in d.rglob("*.flac"):
                        f.unlink()

    def test_MED14_cd_audio_and_the_tags(self):
        album, cd = self.copy()
        self.assertEqual((mc.copy_kind(album), album["media_hint"]), ("cd", ""))
        self.assertIn("cd release", cd["clues"].get("format", ""))

    def identify(self, http):
        eng = self.engine(http)
        gs = eng.albums_in(self.lib)
        self.assertEqual(len(gs), 1)
        return eng.identify(gs[0])

    def test_MED14_the_disc_count_in_every_album_tag(self):
        P = Path
        tagged = [(P(f"/b/CD{n}/01.flac"), {"album": f"{t} [2020 Set 2CD Box]", "disc_eff": n})
                  for n, t in ((1, "First"), (2, "Second"))]
        self.assertEqual(ma._tags_box_count(tagged), "2CD in every disc's album tag")
        self.assertEqual(ma._tags_box_count([(p, dict(i, album="First [2020 Set 3CD Box]"))
                                             for p, i in tagged]), "")        # (3 named, 2 discs)
        self.assertEqual(ma._tags_box_count(tagged[:1] + [(P("/b/CD2/01.flac"), {"album": "Second", "disc_eff": 2})]),
                         "")                                                   # (one disc says nothing)
        self.assertEqual(ma._tags_box_count([(P("/b/01.flac"), {"album": "X [1CD]", "disc_eff": 1})]), "")

    def test_MED14_a_weak_clue_against_a_download_like_the_files(self):
        # a FLAC download fits FLAC files as well as the tags' "2CD" fits the CD: the album is
        # known, its pressing is not - the download is never picked over the CD
        lens = ("0:10", "0:20", "0:30")
        p = self.identify(te.FakeHttp(dg=[dg_set(9101, "cd", lens), dg_set(9102, "file", lens)]))
        self.assertNotEqual(p.release_id, "9102", p.why)
        self.assertEqual(p.tier, "album_only", p.why)
        self.assertEqual({(t.get("media") or ["CD"])[0] for t in p.targets}, {"CD"})   # (the CD's, never "Digital Media")
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)

    def test_MED14_the_files_own_discogs_id_is_a_lead_not_a_proof(self):
        # TAG-2: the release the files name is fetched and scored, and loses to the copy's lengths
        named = dg_set(9201, "cd", ("3:10", "4:20", "5:30"))
        fits = dg_set(9202, "cd", ("0:10", "0:20", "0:30"))
        named["title"] = fits["title"] = "Album K"
        http = te.FakeHttp(dg=[named, fits])
        self.make(dg_id="9201")
        p = self.identify(http)
        self.assertTrue(any(h == "discogs" and u.endswith("/releases/9201") for h, u in http.calls), http.calls)
        self.assertNotEqual(p.release_id, "9201", p.why)
        self.assertEqual(p.release_id, "9202", p.why)

    def setUp(self):
        super().setUp()
        if self._testMethodName.endswith("like_the_files"):
            self.make()


class MED15_EachDiscItsOwnMedia(te.Base):
    """MED-15: a box listing a Blu-ray before its CDs (and the box itself): the CD rip's files are
    tagged "CD" each, never the release's formats joined, and the folder is no "Blu-ray" edition."""

    def release(self):
        rel = dg_set(9301, "cd", ("0:10", "0:20", "0:30"))
        rel["year"], rel["released"] = 2019, "2019-03-01"
        rel["formats"] = [{"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio", "Multichannel"]},
                          {"name": "CD", "qty": "2", "descriptions": ["Album", "Remastered"]},
                          {"name": "Box Set", "qty": "1", "descriptions": ["Compilation"]}]
        rel["tracklist"] = [{"type_": "track", "position": f"CD{n}-{i + 1}", "title": f"{'TU'[n - 1]}{i}",
                             "duration": d} for n in (1, 2) for i, d in enumerate(("0:10", "0:20", "0:30"))]
        return rel

    def test_MED15_the_track_media_of_its_own_disc(self):
        eng = self.engine(te.FakeHttp())
        album, tracks, n = engine.discogs_parts(self.release(), eng.dm)
        self.assertNotIn("media", album)
        self.assertEqual({t["media"] for t in tracks}, {"CD, Album, Remastered"})
        # (no carrier named by the positions: the release's media in order, by quantity)
        rel = self.release()
        for t in rel["tracklist"]:
            t["position"] = t["position"][2:]
        rel["formats"] = rel["formats"][1:] + [{"name": "DVD", "qty": "1", "descriptions": ["DVD-Video"]}]
        _a, tracks, _n = engine.discogs_parts(rel, eng.dm)
        self.assertEqual({t["media"] for t in tracks}, {"CD, Album, Remastered"})

    def test_MED15_a_cd_box_listing_a_blu_ray_is_no_blu_ray_edition(self):
        top = self.lib / "Band K" / "Album K (2CD)"
        for n in (1, 2):
            for i in range(3):
                audio_file(top / f"CD{n}", f"{i + 1:02d}.flac", 10 * (i + 1), 44100, 16, 300 + 100 * n + 10 * i,
                           title=f"{'TU'[n - 1]}{i}", album="Album K", albumartist="Band K", artist="Band K",
                           track=str(i + 1), disc=str(n))
        eng = self.engine(te.FakeHttp(dg=[self.release()]))
        p = eng.identify(eng.albums_in(self.lib)[0])
        self.assertEqual((p.tier, p.release_id), ("exact", "9301"), p.why)
        self.assertEqual({t["media"][0] for t in p.targets}, {"CD, Album, Remastered"})
        import naming
        self.assertNotIn("Blu-ray", naming.edition(dict(p.targets[0], **{"~edition_media": p.targets[0]["media"]})))


if __name__ == "__main__":
    unittest.main()
