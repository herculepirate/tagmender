# Cover replacement, sixth independent review: each test failed before its fix.
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
from test_cover_replace import jpeg, size, Fake, Engine, MBID  # noqa: E402
from test_executor import audio, target  # noqa: E402


def _say(*_a):
    pass


# Review 6: artwork._other_music (reads tags) and plan._holds_other_music (membership only)
# disagree; the engine looks a cover up on every run that the plan never writes.


class Counting(Fake):
    def __init__(self, staging, data, calls):
        super().__init__(staging, data)
        self.calls, self.min_px = calls, 500

    def front(self, *a, **k):
        self.calls.append(1)
        return super().front(*a, **k)


class Mismatch(tcr.Replace):
    """Album already carries the target's tags (a stable library), small cover.jpg + small
    embedded fronts. An extra file below the album that albums_in does not group with it."""

    def setUp(self):
        super().setUp()
        from mutagen.flac import FLAC
        for f, t in ((self.f1, "First"), (self.f2, "Second")):
            m = FLAC(f)
            m["album"], m["albumartist"], m["artist"], m["title"] = "Real Album", "Real Artist", "Real Artist", t
            m.save()

    def extra(self):
        raise unittest.SkipTest('base')

    art_kw = {}

    def runs(self, n=3):
        prof = self.profile(artwork=self.art(**self.art_kw))
        calls, statuses = [], []
        for i in range(n):
            e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
            g = [x for x in e.albums_in(self.lib) if self.f1 in x.files]
            assert len(g) == 1, g
            prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                          target(2, "Second", musicbrainz_albumid=[MBID])],
                                 files=[str(self.f1), str(self.f2)])
            prop.group_files = [str(f) for f in g[0].files]
            if i == 1:
                calls.clear()
            with mock.patch.object(artwork, "chain", lambda *a, **k: [Counting(self.tmp / "st", jpeg(800, 800), calls)]):
                Engine(prof).add_artwork(prop)
            p = pl.build([prop], prof)
            statuses.append(p["albums"][0]["status"])
            if p["albums"][0]["status"] == "ready":
                executor.Executor(p).run()
        return calls, statuses, size((self.album / "cover.jpg").read_bytes())

    def test_converges(self):
        self.extra()
        calls, statuses, cover = self.runs()
        self.assertEqual(calls, [], f"lookups on runs 2-3: {len(calls)}; statuses {statuses}; cover.jpg {cover}")


class UntaggedCopy(Mismatch):
    """01 a.mp3 with no tags beside the tagged 01 a.flac (a transcode without tags)."""
    def extra(self):
        audio(self.album / "01 a.mp3", 440)


class TwoDelete(Mismatch):
    """A duplicate of the album's own track put aside in Album/2delete (albums_in never looks there)."""
    def extra(self):
        audio(self.album / "2delete" / "01 a.flac", 440, album="Real Album", albumartist="Real Artist",
              artist="Real Artist", title="First")


class UntaggedCopyFilesOnly(UntaggedCopy):
    art_kw = {"embed_front": False}


class TwoDeleteFilesOnly(TwoDelete):
    art_kw = {"embed_front": False}


for _cls in (Mismatch, UntaggedCopy, TwoDelete, UntaggedCopyFilesOnly, TwoDeleteFilesOnly):
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__ and _n != "test_converges":
            setattr(_cls, _n, None)


# Review 6: needs_art says "no picture in the folder" for a folder the plan never writes a
# cover into (shared with other music): with cover files only, a lookup on every run.


class LooseSingleFilesOnly(tcr.Replace):
    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        self.loose = audio(self.artist / "01 single.flac", 660, album="Real Album", albumartist="Real Artist",
                           artist="Real Artist", title="First")
        for d in ("2003 - Other", "2005 - Another"):
            audio(self.artist / d / "01.flac", 300, album=d, artist="Real Artist")
        shutil.rmtree(self.album)

    def test_no_lookup_every_run(self):
        prof = self.profile(artwork=self.art(embed_front=False))
        calls = []
        for i in range(3):
            if i == 1:
                calls.clear()
            prop = self.proposal(source_dir=str(self.artist), files=[str(self.loose)],
                                 targets=[target(1, "First", totaltracks="1", musicbrainz_albumid=[MBID])])
            prop.group_files = [str(self.loose)]
            with mock.patch.object(artwork, "chain", lambda *a, **k: [Counting(self.tmp / "st", jpeg(800, 800), calls)]):
                Engine(prof).add_artwork(prop)
            p = pl.build([prop], prof)
            if p["albums"][0]["status"] == "ready":
                executor.Executor(p).run()
        self.assertEqual(calls, [], f"{len(calls)} lookups in 2 runs that write nothing; plan: "
                                    f"{p['albums'][0]['status']} {p['albums'][0].get('artwork')}")





class LooseSingleDefaultCoverUp(LooseSingleFilesOnly):
    def test_no_lookup_every_run(self):
        from test_review5_cover_watch import FoundCoverUp
        prof = self.profile(artwork=self.art())
        calls = []
        for i in range(3):
            if i == 1:
                calls.clear()
            prop = self.proposal(source_dir=str(self.artist), files=[str(self.loose)],
                                 targets=[target(1, "First", totaltracks="1", musicbrainz_albumid=[MBID])])
            prop.group_files = [str(self.loose)]
            with mock.patch.object(artwork, "chain", lambda *a, **k: [FoundCoverUp(self.tmp / "st", calls)]):
                Engine(prof).add_artwork(prop)
            p = pl.build([prop], prof)
            if p["albums"][0]["status"] == "ready":
                executor.Executor(p).run()
        self.assertEqual(calls, [], f"{len(calls)} CoverUp lookups in 2 runs that write nothing")


# Review 6: the round-5 artist-photo fix holds only for a TAGGED loose single. An UNTAGGED
# loose single (the library's untagged tracks - what TagMender is for) beside the artist's
# untagged album folders: every untagged subfolder counts as "its bonus folder", so the
# artist photo (Artist/folder.jpg) is the single's own cover and is embedded into it.


class UntaggedArtistFolderPhoto(tcr.Replace):
    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        self.loose = audio(self.artist / "01 single.flac", 660)          # no tags
        for d in ("2003 - Untagged One", "2005 - Untagged Two"):
            audio(self.artist / d / "01.flac", 300)
            audio(self.artist / d / "02.flac", 400)
        shutil.rmtree(self.album)

    def _prop(self, prof):
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
        g = [x for x in e.albums_in(self.lib) if self.loose in x.files]
        assert len(g) == 1 and g[0].folder == self.artist and g[0].files == [self.loose], g
        prop = self.proposal(source_dir=str(self.artist), files=[str(self.loose)],
                             targets=[target(1, "Single", totaltracks="1", musicbrainz_albumid=[MBID])])
        prop.group_files = [str(f) for f in g[0].files]
        return prop

    def test_large_artist_photo_is_not_embedded_as_the_cover(self):
        photo = jpeg(1000, 1000, (10, 200, 10))
        (self.artist / "folder.jpg").write_bytes(photo)
        prof = self.profile(artwork=self.art())
        prop = self._prop(prof)
        prov = Fake(self.tmp / "staging", None); prov.min_px = 500      # nothing found online
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        emb = artwork.embedded_front(self.loose)
        from test_cover_replace import size
        self.assertTrue(emb is None, f"the artist photo was embedded into the single "
                               f"(source {prop.artwork and prop.artwork.get('source')!r}, {size(emb) if emb else None}, same bytes as the photo: {emb == photo})")


for _cls in (Counting, Mismatch, UntaggedCopy, TwoDelete, UntaggedCopyFilesOnly, TwoDeleteFilesOnly, LooseSingleFilesOnly, LooseSingleDefaultCoverUp, UntaggedArtistFolderPhoto,):                 # (Replace's own tests run in test_cover_replace)
    if not (isinstance(_cls, type) and issubclass(_cls, tcr.Replace)):
        continue
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and not any(_n in b.__dict__ for b in _cls.__mro__ if b is not tcr.Replace):
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()
