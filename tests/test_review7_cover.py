# Cover replacement, seventh independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa: E402
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import test_cover_replace as tcr  # noqa: E402
from test_cover_replace import jpeg, size, give, Fake, Engine, MBID  # noqa: E402
from test_executor import audio, target  # noqa: E402


class Counting(Fake):
    def __init__(self, staging, data, calls):
        super().__init__(staging, data)
        self.calls, self.min_px = calls, 500

    def front(self, *a, **k):
        self.calls.append(1)
        return super().front(*a, **k)


def _skip_base_tests(*classes):
    for cls in classes:
        for n in dir(tcr.Replace):
            if n.startswith("test_") and n not in cls.__dict__:
                setattr(cls, n, None)


# 1. needs_art judges "shared" on the SOURCE folder, the plan writes cover.jpg into the
#    DESTINATION folder (move/copy). A loose single beside the artist's albums, moved into
#    its own album folder, with a good embedded front: before d31fe38 needs_art said "no
#    picture in the folder", the own front was used (no lookup) and the new album folder got
#    cover.jpg; now nothing is looked for and the album folder never gets a cover file.
class MovedLooseSingle(tcr.Replace):
    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        self.loose = audio(self.artist / "01 single.flac", 660, album="Single", artist="Some Artist",
                           title="single")
        give(self.loose, jpeg(800, 800, (200, 10, 10)))
        audio(self.artist / "2003 - Other" / "01.flac", 300, album="Other", artist="Some Artist")
        shutil.rmtree(self.album)

    def _go(self, prof):
        calls = []
        prop = self.proposal(source_dir=str(self.artist), files=[str(self.loose)],
                             targets=[target(1, "Single", album="Single", totaltracks="1",
                                             musicbrainz_albumid=[MBID])])
        prop.group_files = [str(self.loose)]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [Counting(self.tmp / "st", jpeg(900, 900), calls)]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        a = p["albums"][0]
        res = executor.Executor(p).run()
        moved = [Path(fe["dst"]) for fe in a["files"]]
        return a, res, moved

    def test_moved_single_gets_a_cover_file(self):
        prof = self.profile(artwork=self.art(), operation="move", destination=str(self.dest))
        a, res, moved = self._go(prof)
        d = moved[0].parent
        others = [x for x in d.rglob("*") if x.suffix == ".flac" and x not in moved]
        self.assertEqual(others, [], f"destination {d} is shared; test premise wrong")
        pics = [x.name for x in d.iterdir() if x.suffix.lower() in (".jpg", ".png")]
        self.assertTrue(pics, f"{d} (the single's own new folder) got no cover file; plan artwork "
                              f"{a.get('artwork')}, status {a['status']}")


# 2. Big-library cost: _other_music lists and stats EVERY audio file below the folder before
#    looking at any (before d31fe38 it stopped at the first other album's track). For a loose
#    track at a library root (or at the top of a watched folder holding the library) that is
#    a full walk of the library, done by small_covers, CoverUp.front and plan (twice).
class RootWalk(tcr.Replace):
    def test_other_music_stops_at_the_first_other_album(self):
        root = self.lib
        loose = audio(root / "loose.flac", 700, album="Loose", artist="X")
        one = root / "Some Artist" / "2001 - Some Album" / "01 a.flac"
        for i in range(60):
            d = root / f"Artist {i:02d}" / "Album"
            d.mkdir(parents=True)
            for j in range(5):
                shutil.copy(one, d / f"{j:02d}.flac")
        seen = []
        real = Path.is_file

        def counting(self_):
            if self_.suffix == ".flac":
                seen.append(self_)
            return real(self_)
        with mock.patch.object(Path, "is_file", counting):
            shared = artwork._other_music(root, [loose])
        self.assertTrue(shared)
        self.assertLess(len(seen), 20, f"_other_music stat'ed {len(seen)} tracks of the library to answer "
                                       f"'is there another album here' (the first one answers it)")


# 3. The untagged bonus-folder rule: an untagged album of loose tracks directly in an artist
#    folder (3 tracks) beside the artist's untagged EP folder (2 tracks): the EP counts as
#    "its bonus folder", so the artist photo Artist/folder.jpg is the album's own cover and is
#    embedded into its tracks when nothing is found online.
class LooseAlbumBesideEP(tcr.Replace):
    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        shutil.rmtree(self.album)
        self.loose = [audio(self.artist / f"0{i} t{i}.flac", 300 + 50 * i) for i in (1, 2, 3)]
        audio(self.artist / "2004 - EP" / "01 e.flac", 900)
        audio(self.artist / "2004 - EP" / "02 f.flac", 950)

    def test_artist_photo_not_embedded(self):
        photo = jpeg(1000, 1000, (10, 200, 10))
        (self.artist / "folder.jpg").write_bytes(photo)
        prof = self.profile(artwork=self.art())
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
        g = [x for x in e.albums_in(self.lib) if self.loose[0] in x.files]
        assert len(g) == 1 and g[0].folder == self.artist, g
        prop = self.proposal(source_dir=str(self.artist), files=[str(f) for f in self.loose],
                             targets=[target(i, f"T{i}", totaltracks="3", musicbrainz_albumid=[MBID])
                                      for i in (1, 2, 3)])
        prop.group_files = [str(f) for f in g[0].files]
        prov = Fake(self.tmp / "staging", None); prov.min_px = 500
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        self.assertNotEqual((prop.artwork or {}).get("source"), "the album's own cover",
                            "the artist photo was taken as the loose album's own cover (embedded into its tracks)")


# 4. "01 - a.mp3 beside 01 a.flac" (the docstring's own example of a copy): an untagged MP3
#    transcode named with a dash makes the album folder "shared": its small cover.jpg is never
#    replaced (the embedded ones are).
class DashedUntaggedCopy(tcr.Replace):
    def test_cover_file_replaced(self):
        audio(self.album / "01 - a.mp3", 440)
        prof = self.profile(artwork=self.art())
        files = [self.f1, self.f2]
        small = artwork.small_covers(self.album, files, prof, ours=[str(f) for f in files])
        self.assertFalse(small["shared"], "an untagged copy '01 - a.mp3' of '01 a.flac' makes the folder shared")


_skip_base_tests(MovedLooseSingle, RootWalk, LooseAlbumBesideEP, DashedUntaggedCopy)


# 5. needs_art asks every track for an embedded cover, also tracks of formats TagMender never
#    embeds into (WavPack, APE, DSF, WMA, Musepack): an album of .wv files with a good
#    cover.jpg is looked up (CoverUp: a pipeline run, web requests) on every run, and the plan
#    never writes anything.
def wv(p, freq=440, **tags):
    import subprocess
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    "-c:a", "wavpack", *meta, "-y", str(p)], check=True)
    return p


class WavPackAlbum(tcr.Replace):
    def test_no_lookup_every_run(self):
        from test_review5_cover_watch import FoundCoverUp
        shutil.rmtree(self.album)
        self.f1 = wv(self.album / "01 a.wv", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = wv(self.album / "02 b.wv", 550, album="Some Album", title="b", artist="Some Artist")
        (self.album / "cover.jpg").write_bytes(jpeg(1000, 1000))
        prof = self.profile(artwork=self.art())
        calls, statuses = [], []
        for n in range(3):
            if n == 1:
                calls.clear()
            prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                          target(2, "Second", musicbrainz_albumid=[MBID])])
            prop.group_files = [str(self.f1), str(self.f2)]
            with mock.patch.object(artwork, "chain", lambda *a, **k: [FoundCoverUp(self.tmp / "st", calls)]):
                Engine(prof).add_artwork(prop)
            p = pl.build([prop], prof)
            statuses.append(p["albums"][0]["status"])
            if p["albums"][0]["status"] == "ready":
                executor.Executor(p).run()
        self.assertEqual(calls, [], f"{len(calls)} CoverUp lookups in runs 2-3 for a WavPack album with a "
                                    f"1000px cover.jpg; statuses {statuses}; needs_art: "
                                    f"{artwork.needs_art(self.album, [self.f1, self.f2], prof)!r}")


# 6. A tagged album whose Extras folder is tagged a little differently ("Some Album (Bonus
#    Tracks)"): the folder counts as shared, so the album's small cover.jpg is never replaced
#    while its embedded covers are - the album ends up with a 900px front in the tracks and the
#    old 300px cover.jpg beside them, for good (CoverUp also never sees the album's own scans).
class TaggedExtras(tcr.Replace):
    def test_small_cover_file_replaced_too(self):
        audio(self.album / "Extras" / "01 demo.flac", 880, album="Some Album (Bonus Tracks)",
              artist="Some Artist", title="demo")
        prof = self.profile(artwork=self.art())
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
        g = [x for x in e.albums_in(self.lib) if self.f1 in x.files]
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        prop.group_files = [str(f) for f in g[0].files]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [Counting(self.tmp / "st", jpeg(900, 900), [])]):
            Engine(prof).add_artwork(prop)
        executor.Executor(pl.build([prop], prof)).run()
        got = (size(artwork.embedded_front(self.f1)), size((self.album / "cover.jpg").read_bytes()))
        self.assertEqual(got, ((900, 900), (900, 900)), "embedded front vs cover.jpg after the run")


_skip_base_tests(WavPackAlbum, TaggedExtras)


# 6b. Same layout, tracks without embedded pictures, the album's real 1000px cover.jpg, and
#     artwork.generate on: nothing found online -> a GENERATED cover is embedded into the
#     tracks beside the real cover.jpg ("never generated over the album's own cover").
class TaggedExtrasGenerated(tcr.Replace):
    def test_no_generated_cover_beside_a_real_one(self):
        from mutagen.flac import FLAC
        from test_review3_cover_watch import FakeCoverUp
        for f in (self.f1, self.f2):
            t = FLAC(f); t.clear_pictures(); t.save()
        (self.album / "cover.jpg").write_bytes(jpeg(1000, 1000, (0, 0, 255)))
        audio(self.album / "Extras" / "01 demo.flac", 880, album="Some Album (Bonus Tracks)",
              artist="Some Artist", title="demo")
        prof = self.profile(artwork=self.art(generate="template"))
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
        g = [x for x in e.albums_in(self.lib) if self.f1 in x.files]
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        prop.group_files = [str(f) for f in g[0].files]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [FakeCoverUp(self.tmp / "st")]):
            Engine(prof).add_artwork(prop)
        executor.Executor(pl.build([prop], prof)).run()
        self.assertNotIn("generated", (prop.artwork or {}).get("source", ""),
                         "a generated cover was embedded beside the album's real 1000px cover.jpg")


_skip_base_tests(TaggedExtrasGenerated)

if __name__ == "__main__":
    unittest.main()
