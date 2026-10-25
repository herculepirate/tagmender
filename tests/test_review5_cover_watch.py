# Cover replacement and watched folders, fifth independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import json
import os
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
import test_watch_folders as tw  # noqa: E402
import watcher  # noqa: E402
from test_cover_replace import jpeg, size, give, Fake, Engine, MBID  # noqa: E402
from test_executor import audio, target  # noqa: E402


def _say(*_a):
    pass


# Review 5 proofs. Run from anywhere:
#   python3 -W ignore test_review5.py


def _targets():
    return [target(1, "First", musicbrainz_albumid=[MBID]), target(2, "Second", musicbrainz_albumid=[MBID])]


class FlacPlusMp3(tcr.Replace):
    """An album held twice in one folder (FLAC + an MP3 copy named a little differently),
    a small cover.jpg. Engine (group_files) says the cover is the album's and small; the plan
    (_holds_other_music, which knows only prop.files) says the folder holds other music and
    silently never replaces it - so the cover is looked up again on every run, for nothing."""

    def setUp(self):
        super().setUp()
        for f, n in ((self.f1, "01 - a.mp3"), (self.f2, "02 - b.mp3")):
            audio(self.album / n, 440, album="Some Album", title=f.stem[3:], artist="Some Artist")

    def _run(self, prof, calls):
        prop = self.proposal(targets=_targets())
        prop.group_files = [str(p) for p in sorted(self.album.iterdir()) if p.suffix in (".flac", ".mp3")]
        prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
        real = prov.front
        prov.front = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        return pl.build([prop], prof)

    def test_cover_file_replaced_and_not_looked_up_again(self):
        prof = self.profile(artwork=self.art())
        p1 = self._run(prof, [])
        executor.Executor(p1).run()
        cover = size((self.album / "cover.jpg").read_bytes())
        calls = []
        p2 = self._run(prof, calls)
        self.assertEqual((cover, calls), ((800, 800), []),
                         f"cover.jpg after run 1: {cover}; run 2 looked up {len(calls)}x, plan "
                         f"{p2['albums'][0]['status']}, notes {p2['albums'][0]['notes']}")


class FoundCoverUp(artwork.CoverUp):
    name = "coverup"

    def __init__(self, staging, calls):
        self.staging, self.s, self._last, self.calls = staging, {"embed_max_side": 1200, "jpeg_quality": 95}, None, calls
        self.min_px, self.enlarge, self.generate_mode, self.limit = 500, True, "off", 10 ** 8

    def front(self, folder, ids, artist="", album="", files=None):
        self.calls.append(1)                     # (a real CoverUp: a pipeline run, web requests)
        path, w, h = artwork._stage(jpeg(800, 800), self.staging)
        return {"front": str(path), "width": w, "height": h, "source": "coverup:web"}


class FlacPlusMp3CoverUp(FlacPlusMp3):
    """As FlacPlusMp3, with the default sources (CoverUp first): every run asks CoverUp."""

    def _run(self, prof, calls):
        prop = self.proposal(targets=_targets())
        prop.group_files = [str(p) for p in sorted(self.album.iterdir()) if p.suffix in (".flac", ".mp3")]
        prov = FoundCoverUp(self.tmp / "staging", calls)
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        return pl.build([prop], prof)

    def test_runs_3_to_5_ask_coverup_again(self):
        prof = self.profile(artwork=self.art())
        executor.Executor(self._run(prof, [])).run()
        calls = []
        for _ in range(3):
            p = self._run(prof, calls)
            self.assertEqual(p["albums"][0]["status"], "unchanged")
        self.assertEqual(calls, [], f"{len(calls)} CoverUp lookups in 3 runs that change nothing")


class Mp3CopiesWithoutArt(tcr.Replace):
    """Same album twice (FLAC with a good embedded front and cover.jpg; MP3 copies without
    pictures). needs_art now walks group_files: "01 - a.mp3 has no cover" - a lookup on every
    run although the plan never writes the MP3s (they are not prop.files)."""

    def test_no_lookup_for_files_the_plan_never_writes(self):
        big = jpeg(1000, 1000)
        from mutagen.flac import FLAC
        for f in (self.f1, self.f2):
            t = FLAC(f); t.clear_pictures(); t.save()
            give(f, big)
        (self.album / "cover.jpg").write_bytes(big)
        audio(self.album / "01 - a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        audio(self.album / "02 - b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        prof = self.profile(artwork=self.art())
        files = [str(p) for p in sorted(self.album.iterdir()) if p.suffix in (".flac", ".mp3")]
        # what identify() hands on: the grouping's files (all four) and read_album's (the FLACs)
        import match_albums as ma
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = prof
        g = e.albums_in(self.album.parent)
        self.assertEqual(len(g), 1)
        own = sorted(t["path"] for t in ma.read_album(self.album, self.album.parent.name, self.album.name)["tracks"])
        self.assertEqual(own, [str(self.f1), str(self.f2)])
        calls = []
        for n in range(3):
            if n == 1:
                calls.clear()                     # (run 1 tags the album; runs 2 and 3 have nothing to do)
            prop = self.proposal(targets=_targets())
            prop.group_files = [str(f) for f in g[0].files]
            prov = FoundCoverUp(self.tmp / "staging", calls)
            with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
                Engine(prof).add_artwork(prop)
            p = pl.build([prop], prof)
            if n == 0:
                executor.Executor(p).run()
            else:
                self.assertEqual(p["albums"][0]["status"], "unchanged")
        why = artwork.needs_art(self.album, [self.f1, self.f2], prof)    # (what the plan writes)
        self.assertEqual((why, calls), ("", []), f"needs_art over group_files: {why!r}; {len(calls)} CoverUp "
                                                 f"lookups in 2 runs that change nothing")


class ArtistFolderPhoto(tcr.Replace):
    """A loose, tagged track in an artist folder ("Artist/01 single.flac") beside the
    artist's UNTAGGED album folders, and the artist photo as Artist/folder.jpg (Kodi,
    Jellyfin). _other_music skips untagged music in subfolders ("its untagged bonus
    folder"), and plan._holds_other_music looks only at disc folders: the artist photo is
    the loose track's "own cover" - replaced in place when small, embedded when large."""

    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        self.loose = audio(self.artist / "01 single.flac", 660, album="Single", title="single",
                           artist="Some Artist")
        # the artist's other (untagged) albums
        for d in ("2003 - Untagged One", "2005 - Untagged Two"):
            audio(self.artist / d / "01.flac", 300)
            audio(self.artist / d / "02.flac", 400)
        # the existing tagged album is moved away so only untagged neighbours remain
        import shutil
        shutil.rmtree(self.album)

    def _prop(self):
        e = eng_mod.Engine.__new__(eng_mod.Engine); e.profile = self.profile(artwork=self.art())
        g = [x for x in e.albums_in(self.lib) if self.loose in x.files]
        assert len(g) == 1 and g[0].folder == self.artist and g[0].files == [self.loose], g
        return self.proposal(source_dir=str(self.artist), files=[str(self.loose)],
                             targets=[target(1, "Single", musicbrainz_albumid=[MBID])])

    def test_small_artist_photo_is_not_replaced(self):
        photo = jpeg(400, 400, (10, 200, 10))
        (self.artist / "folder.jpg").write_bytes(photo)
        prof = self.profile(artwork=self.art())
        prop = self._prop()
        prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        self.assertEqual((self.artist / "folder.jpg").read_bytes(), photo,
                         "the artist folder's folder.jpg (artist photo) was overwritten with the single's cover")

    def test_large_artist_photo_is_not_embedded_as_the_cover(self):
        photo = jpeg(1000, 1000, (10, 200, 10))
        (self.artist / "folder.jpg").write_bytes(photo)
        prof = self.profile(artwork=self.art())
        prop = self._prop()
        prov = Fake(self.tmp / "staging", None); prov.min_px = 500      # nothing found online
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        src = (prop.artwork or {}).get("source")
        self.assertNotEqual(src, "the album's own cover",
                            "the artist photo (Artist/folder.jpg) was taken as the single's own cover "
                            "and is embedded into it")


for _cls in [c for c in list(globals().values()) if isinstance(c, type) and issubclass(c, tcr.Replace) and c is not tcr.Replace]:
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


# Review 5 (watcher performance): every pass, and every album a pass files, parses EVERY job
# journal ever written in full (busy_jobs + pending_jobs in Watcher._tick, pending_jobs again
# in Executor._run - one job per album). One `tagmender run` over a big library leaves a
# journal of ~1.2 KB per track (100k tracks: ~110 MB; parsing it took ~5 s and ~800 MB RSS
# here), and it is never deleted (prune removes only backups): the watcher re-parses it
# 2 + (albums filed) times per pass, every interval_seconds, forever.


class FinishedJournals(tw.DropBase):
    def test_a_pass_does_not_parse_finished_jobs_journals(self):
        prof = self.profile()
        old = executor.job_dir("20260101-old-library-run")
        old.mkdir(parents=True)
        lines = [json.dumps({"ev": "file", "album": i, "idx": 0, "state": "committed", "t": 1.0})
                 for i in range(2000)] + [json.dumps({"ev": "end", "status": "done", "t": 2.0})]
        (old / "journal.jsonl").write_text("\n".join(lines) + "\n")
        self.drop("Band X", "Album X")
        self.drop("Band Y", "Album Y")
        reads = []
        real = executor.Journal.read

        def counting(j):
            if j.folder == old:
                reads.append(1)
            return real(j)
        with mock.patch.object(executor.Journal, "read", counting):
            res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(sorted(res.values()), ["done", "done"], res)
        # a job folder from before the ENDED mark is read once, then marked; never again
        self.assertLessEqual(len(reads), 1, f"one pass parsed a finished job's whole journal {len(reads)} times")
        self.assertTrue((old / executor.ENDED).exists())
        reads.clear()
        with mock.patch.object(executor.Journal, "read", counting):
            executor.pending_jobs()
            executor.busy_jobs()
        self.assertEqual(reads, [])


for _cls in (FlacPlusMp3, FlacPlusMp3CoverUp, Mp3CopiesWithoutArt, ArtistFolderPhoto,):                 # (Replace's own tests run in test_cover_replace)
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__ and not any(
                _n in b.__dict__ for b in _cls.__mro__[1:] if b is not tcr.Replace and b is not object):
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()
