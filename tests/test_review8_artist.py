# Artist photo, eighth independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_artist_photo import ArtistPhoto, FakeCoverUp, ARTIST_MBID, pictures  # noqa: E402
from test_cover_replace import Engine, MBID, Fake, jpeg, size, give  # noqa: E402
from test_executor import audio, target  # noqa: E402
from test_review7_cover import wv  # noqa: E402
import companions, coverup_loader  # noqa: E402
coverup_loader.load_coverup(str(companions.coverup_dir()))


class R8(ArtistPhoto):
    pass


for _n in dir(ArtistPhoto):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


# 1. CoverUp.artist_photo reuses the last FRONT search whenever it was for the same folder,
#    whoever the artist is. Two loose tracks in one folder (an inbox, a "Singles" folder):
#    album 1 (artist A) needs a cover -> front search, _last = that folder; album 2 (artist B)
#    already has its covers (no front search) -> its artist photo is A's.
class StaleLastSearch(R8):
    def test_a_front_search_is_reused_only_for_its_own_album_and_artist(self):
        from coverup.model import ArtType
        cls = type(self)._real_coverup                     # (setUp puts a fake in artwork.CoverUp)
        cu = cls.__new__(cls)
        res = types.SimpleNamespace(artist={ArtType.ARTIST_THUMB: object()})
        a_ids = {"artist_mbid": "a" * 8 + "-1111-1111-1111-111111111111"}
        cu._last = ("/m/Singles", None, res, None, None, None, ("Artist A", a_ids["artist_mbid"]))
        self.assertIsNotNone(cu._reusable("/m/Singles", "Artist A", a_ids))
        self.assertIsNone(cu._reusable("/m/Singles", "Artist B", {"artist_mbid": "b" * 8 + "-1111-1111-1111-111111111111"}),
                          "Artist B would be given Artist A's photo (the folder's last front search)")
        cu._last = ("/m/Singles", None, res, None, None, None)        # (a search from before: never reused)
        self.assertIsNone(cu._reusable("/m/Singles", "Artist A", a_ids))


StaleLastSearch._real_coverup = artwork.CoverUp


# 2. A collaboration (two album-artist IDs, folder "A & B") gets ONE artist's photo:
#    CoverUp's make_query takes the first ID only, and TagMender skips CoverUp's own
#    "collaboration folder: no single-artist photo" rule (decide()).
class Collaboration(R8):
    def test_collaboration_gets_no_single_artist_photo(self):
        import coverup_batch as cb
        self.assertEqual(cb.make_query({"dir": self.album, "files": [], "artist": "A & B", "album": "x", "year": None,
                                        "ids": {"artist_mbid": "id-a;id-b"}, "barcode": "", "catno": "",
                                        "discs": 1, "tracks": 1, "images": []}).artist_mbid, "id-a")
        album = self.lib / "Alpha & Beta" / "2001 - Duo"
        g = [audio(album / "01 a.flac", 440, title="a"), audio(album / "02 b.flac", 550, title="b")]
        tg = [target(n + 1, t, artist="Alpha & Beta", musicbrainz_albumid=[MBID],
                     musicbrainz_albumartistid=[ARTIST_MBID, "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee"])
              for n, t in enumerate("ab")]
        prop = self.proposal(source_dir=str(album), files=[str(f) for f in g], targets=tg)
        self.run_one(self.prof(), prop)
        self.assertEqual(FakeCoverUp.calls, [], "a single-artist photo was looked up for a collaboration")
        self.assertFalse((album.parent / "artist.jpg").exists())
        self.assertNotIn(8, [t for t, _s in pictures(g[0])])


# 3. Repeated lookups: an album of WavPack (or APE, DSF, WMA ...) tracks - never embedded
#    into - "lacks" the artist picture for ever: one artist lookup per artist on every run.
class WavPackEveryRun(R8):
    def test_no_artist_lookup_on_the_second_run(self):
        shutil.rmtree(self.album)
        self.f1 = wv(self.album / "01 a.wv", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = wv(self.album / "02 b.wv", 550, album="Some Album", title="b", artist="Some Artist")
        prof = self.prof()
        self.run_one(prof, self.prop())
        self.assertTrue((self.album.parent / "artist.jpg").exists())
        FakeCoverUp.calls = []
        self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up again on every run (nothing can ever be embedded)")


# 4. Repeated lookups: a copy profile. The engine asks the SOURCE tracks (never changed by a
#    copy profile) whether they have the artist picture - the copies have it after run 1.
class CopyEveryRun(R8):
    def test_no_artist_lookup_on_the_second_run(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="copy",
                            destination=str(self.dest))
        _p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        copies = [x for x in self.dest.rglob("*.flac")]
        self.assertTrue(copies and all(8 in [t for t, _s in pictures(c)] for c in copies))
        FakeCoverUp.calls = []
        self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up again on every run although every copy has it")


# 5. Repeated lookups: in place, the artist folder not named after the album artist
#    ("Beatles, The" for "The Beatles"): artist.jpg is never written there, so the folder
#    "lacks" it for ever, though every track has the picture.
class FolderNameEveryRun(R8):
    def test_no_artist_lookup_on_the_second_run(self):
        prof = self.prof()
        prop = lambda: self.prop(artist="The Some Artist")      # folder is "Some Artist"
        self.run_one(prof, prop())
        self.assertIn(8, [t for t, _s in pictures(self.f1)])
        FakeCoverUp.calls = []
        self.run_one(prof, prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up again on every run (the file can never be written)")


# 6. No front cover found, artist photo found: the tracks' ONLY picture is the artist photo
#    (players show it as the album's cover); with artwork.replace "never" (or min_px 0)
#    TagMender then treats the tracks as having a cover and never embeds a front later.
class OnlyPictureIsTheArtist(R8):
    def test_front_still_added_on_a_later_run(self):
        from mutagen.flac import FLAC
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t.clear_pictures()
            t.save()
        prof = self.prof(replace="never", files=False, sources=["caa"])
        self.run_one(prof, self.prop())                         # (no front provider: nothing found)
        self.assertEqual([t for t, _s in pictures(self.f1)], [])   # (no artist photo alone either)
        prop = self.prop()
        prov = Fake(self.tmp / "stf", jpeg(900, 900))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        self.assertIn(3, [t for t, _s in pictures(self.f1)],
                      f"no front cover ever embedded: needs_art={artwork.needs_art(self.album, [self.f1], prof)!r}")

    def test_artist_photo_is_not_the_only_picture(self):
        self.run_one(self.prof(sources=["caa"]), self.prop())
        self.assertNotEqual([t for t, _s in pictures(self.f1)], [8],
                            "the artist photo is the track's only picture (shown as the album cover)")


# 7. Move profile: the artist folder's artist.jpg (TagMender's own, from an earlier run)
#    goes WITH a loose single moved out of that folder, as one of the single's companions:
#    the artist folder loses its photo and the single's album folder gets an artist photo
#    as its only picture (so no cover.jpg is written there).
class LooseSingleTakesArtistJpg(R8):
    def test_artist_jpg_stays_in_the_artist_folder(self):
        artist = self.album.parent
        (artist / "artist.jpg").write_bytes(jpeg(1000, 1000, (0, 255, 0)))
        loose = audio(artist / "single.flac", 660, title="single", artist="Some Artist")
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(loose)],
                             targets=[target(1, "Single", album="Single", artist="Some Artist", totaltracks="1",
                                             musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])])
        prop.group_files = [str(loose)]
        p, res = self.run_one(prof, prop)
        comps = [c["src"] for a in p["albums"] for c in a["companions"]]
        self.assertTrue((artist / "artist.jpg").exists(),
                        f"artist.jpg left the artist folder with the loose single; companions {comps}; "
                        f"now at {[str(x) for x in self.dest.rglob('artist.jpg')]}")


    def test_single_gets_its_cover_jpg(self):
        artist = self.album.parent
        (artist / "artist.jpg").write_bytes(jpeg(1000, 1000, (0, 255, 0)))
        loose = audio(artist / "single.flac", 660, title="single", artist="Some Artist")
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(loose)],
                             targets=[target(1, "Single", album="Single", artist="Some Artist", totaltracks="1",
                                             musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])])
        prop.group_files = [str(loose)]
        prov = Fake(self.tmp / "stf", jpeg(900, 900))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        d = next(self.dest.rglob("single.flac"), None) or next(x for x in self.dest.rglob("*.flac") if "Single" in str(x))
        self.assertEqual(sorted(x.name for x in d.parent.iterdir() if x.suffix == ".jpg"), ["cover.jpg"],
                         "the single's new album folder holds the artist photo instead of its cover.jpg")


    def test_artist_folder_jpg_does_not_become_the_singles_cover(self):
        artist = self.album.parent
        photo = jpeg(1000, 1000, (0, 255, 0))
        (artist / "folder.jpg").write_bytes(photo)              # the artist photo (Kodi / CoverUp name)
        loose = audio(artist / "single.flac", 660, title="single", artist="Some Artist")
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="basic", operation="move",
                            destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(loose)],
                             targets=[target(1, "Single", album="Single", artist="Some Artist", totaltracks="1",
                                             musicbrainz_albumid=[MBID])])
        prop.group_files = [str(loose)]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [Fake(self.tmp / "stf", None)]):
            Engine(prof).add_artwork(prop)
        executor.Executor(pl.build([prop], prof)).run()
        d = next(x for x in self.dest.rglob("*.flac")).parent
        fc = artwork.folder_cover(d)
        self.assertFalse(fc is not None and fc.read_bytes() == photo,
                         f"the artist photo moved with the single and is now its album cover: {fc}")


# 8. Undo of a move job: the shared artist.jpg it created, later replaced by a media server
#    (Jellyfin/Plex "save artwork into media folders"), pins the album where the job put it.
class UndoPinnedByArtistJpg(R8):
    def test_album_goes_back_although_artist_jpg_changed(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        photo = next(self.dest.rglob("artist.jpg"))
        photo.write_bytes(jpeg(1200, 1200, (9, 9, 9)))            # the media server's own photo
        try:
            out = executor.undo(p["job"])
        except Exception as exc:
            out = [str(exc)]
        self.assertTrue(self.f1.exists(), f"the album stayed in the destination: {out}")


# 9. The artist folder already holds the artist's photo as folder.jpg (CoverUp's batch / Kodi
#    name - the photo CoverUp itself saves there): TagMender still looks the artist up and
#    writes a second, different artist photo, artist.jpg, beside it (and embeds that one).
class FolderJpgIsTheArtistPhoto(R8):
    def test_no_second_artist_photo(self):
        mine = jpeg(1500, 1500, (1, 2, 3))
        (self.album.parent / "folder.jpg").write_bytes(mine)
        self.run_one(self.prof(artist={"embed": False}), self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertFalse((self.album.parent / "artist.jpg").exists(), "a second artist photo beside folder.jpg")


# 10. artist.embed false on a move (or copy) profile: never looked up, so the new artist
#     folder never gets artist.jpg (the engine checks the folder only for in-place profiles).
class MoveFileOnly(R8):
    def test_moved_album_gets_artist_jpg_without_embedding(self):
        art = self.prof(artist={"enabled": True, "file": "artist.jpg", "embed": False})["artwork"]
        prof = self.profile(artwork=art, tag_level="enthusiast", operation="move", destination=str(self.dest))
        _p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual([x.parent.name for x in self.dest.rglob("artist.jpg")], ["Some Artist"])


if __name__ == "__main__":
    unittest.main()
