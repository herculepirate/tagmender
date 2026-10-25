# Cover replacement and watched folders, fourth independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa: E402
import engine as eng_mod  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import test_cover_replace as tcr  # noqa: E402
import test_watch_folders as tw  # noqa: E402
import watcher  # noqa: E402
from test_cover_replace import jpeg, size, give, Fake, Engine, MBID  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_review3_cover_watch import FakeCoverUp  # noqa: E402


def _say(*_a):                        # (the proofs printed what they saw; the values are still computed)
    pass


# Review 4: embed_cap() takes CoverUp's embed_max_side for every profile, also one whose
# cover sources do not include CoverUp (sources ["caa", "discogs"]: _finish embeds those at
# EMBED_MAX_SIDE, 1200). With CoverUp set to embed at 600 px and min_px 800, a 700x700
# embedded front is "as large as TagMender embeds" and is never replaced - although this
# profile embeds 1200 px covers and the user asked for at least 800.


class CapWithoutCoverUp(Base):
    def test_caa_only_profile_replaces_a_700px_front(self):
        ini = self.tmp / "Picard.ini"
        ini.write_text("[plugin.be95dccc-b521-488a-843f-4af29033de41]\nembed_max_side=600\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        self.addCleanup(os.environ.pop, "TAGMENDER_PICARD_INI", None)
        artwork._CAP.clear()
        self.addCleanup(artwork._CAP.clear)
        for f in (self.f1, self.f2):
            give(f, jpeg(700, 700))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 800,
                                     "sources": ["caa", "discogs"]})
        why = artwork.needs_art(self.album, [self.f1, self.f2], prof)
        self.assertIn("smaller than 800", why, f"700x700 fronts kept for a CAA-only profile "
                                               f"(embed_cap() = {artwork.embed_cap()})")


# Review 4: "never generated over the album's own cover" (04d0b82) holds only while that
# cover is SMALL: own/keep come from small_covers(), which is {} when nothing is below
# min_px. An album with a good 1000x1000 cover.jpg whose tracks have no embedded picture
# (very common: rips that keep the cover beside the files), with web/online sources that
# find nothing and artwork.generate on, gets a GENERATED cover embedded into every track
# while its real cover sits in the folder.


class GeneratedOverLargeOwn(Base):
    def test_real_folder_cover_is_not_replaced_in_tracks_by_a_generated_one(self):
        (self.album / "cover.jpg").write_bytes(jpeg(1000, 1000, (0, 0, 255)))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "min_px": 500,
                                     "sources": ["web", "caa"], "generate": "template"})
        prov = FakeCoverUp(self.tmp / "staging")            # web: nothing found (offline, or no match)
        prop = self.proposal(targets=[target(1, "First", musicbrainz_albumid=[MBID]),
                                      target(2, "Second", musicbrainz_albumid=[MBID])])
        _say("\nneeds_art:", artwork.needs_art(self.album, [self.f1, self.f2], prof))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        executor.Executor(pl.build([prop], prof)).run()
        got = artwork.embedded_front(self.f1)
        self.assertNotIn("generated", (prop.artwork or {}).get("source", ""),
                         f"a generated cover was embedded although the folder has its real "
                         f"1000x1000 cover.jpg (embedded now: {size(got) if got else None})")


# Review 4: a copy profile whose album is copied only in part (a track the profile leaves
# out: its formats, a file rule, a hard link) is judged at its SOURCE on every run
# (_art_view needs a copy of every file): the source's small cover is "small" forever, so
# every run looks the cover up online again - and does nothing with it.


class PartialCopy(tcr.Replace):
    def test_second_run_of_a_partly_copied_album_does_not_look_again(self):
        f3 = audio(self.album / "03 c.mp3", 660, album="Some Album", title="c", artist="Some Artist")
        give(f3, self.small)
        prof = self.profile(artwork=self.art(), operation="copy", destination=str(self.dest), formats=["flac"])
        files = [str(self.f1), str(self.f2), str(f3)]
        tg = [target(1, "First", musicbrainz_albumid=[MBID]), target(2, "Second", musicbrainz_albumid=[MBID]),
              target(3, "Third", musicbrainz_albumid=[MBID])]

        def run(calls):
            prop = self.proposal(files=files, targets=tg)
            prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
            real = prov.front
            prov.front = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
            with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
                Engine(prof).add_artwork(prop)
            return pl.build([prop], prof)

        p1 = run([])
        executor.Executor(p1).run()
        copies = sorted(self.dest.rglob("*.flac"))
        self.assertEqual([size(artwork.embedded_front(c)) for c in copies], [(800, 800)] * 2)
        calls = []
        p2 = run(calls)
        self.assertEqual(p2["albums"][0]["status"], "unchanged")
        self.assertEqual(calls, [], "run 2 looked the cover up online again although the library's "
                                    "copies have the larger cover already")


# Review 4 (performance; the review-3 LibraryInside fix covers only albums_in): a loose track
# at the top of a watched folder that holds the library (a copy profile keeps it there; an
# unresolved one stays there under every profile) makes Watcher.due() call
# ex.audio_files(<watched folder>) - an rglob + stat of EVERY file of the library inside - on
# every pass (interval_seconds, 300 s by default), once per loose track, for the "still
# ignored?" check, whatever the album's state.


class RootWalk(tw.DropBase):
    def test_idle_pass_does_not_walk_the_library(self):
        self.dest = self.inbox / "Library"
        self.dest.mkdir()
        prof = self.profile(operation="copy")
        self.drop("Band X", "Album X")
        self.drop("Band Y", "Album Y")
        loose = self.drop("Band Z", "Loose", n=1)
        one = next(loose.glob("*.flac"))
        shutil.move(str(one), str(self.inbox / "loose track.flac"))
        loose.rmdir()
        tw.settle(self.inbox)
        w = watcher.Watcher([prof], once=True)
        _say("\npass 1:", w.tick())
        lib = {str(p) for p in self.dest.rglob("*.flac")}
        self.assertTrue(lib, "the albums were copied into the library inside")
        walked = []
        real = ex.audio_files

        def counting(folder):
            out = real(folder)
            walked.extend(str(p) for p in out if str(p) in lib)
            return out
        with mock.patch.object(ex, "audio_files", counting), \
                mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            _say("pass 2:", w.tick())
        self.assertEqual(walked, [], f"an idle pass listed and stat'ed {len(walked)} library files "
                                     f"(of {len(lib)}) for a loose track's 'still ignored?' check")


# Review 4: an album filed into a disc folder of its own - disc 1 of a 2-CD set that has
# only disc 1, or a box set's disc 3 ("Album/Disc 01/") - keeps its cover.jpg in that disc
# folder (plan step 5: the album's folder is the common folder of its tracks, "Disc 01").
# needs_art is asked about the folder ABOVE it (Engine._art_view and albums_in lift a disc
# folder to its parent), finds "no picture in the folder", and the cover is looked up online
# again on every run - and nothing is done with it (the plan finds Disc 01's cover.jpg).


class OneDiscFolder(tcr.Replace):
    def _run(self, prof, calls, files=None):
        tg = [target(1, "First", musicbrainz_albumid=[MBID], totaldiscs=["2"]),
              target(2, "Second", musicbrainz_albumid=[MBID], totaldiscs=["2"])]
        prop = self.proposal(targets=tg, **({"files": files, "source_dir": str(Path(files[0]).parent.parent)}
                                            if files else {}))
        prov = Fake(self.tmp / "staging", jpeg(800, 800)); prov.min_px = 500
        real = prov.front
        prov.front = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        return pl.build([prop], prof)

    def test_copy_second_run_does_not_look_again(self):
        prof = self.profile(artwork=self.art(), operation="copy", destination=str(self.dest))
        p1 = self._run(prof, [])
        r1 = executor.Executor(p1).run()
        self.assertEqual(r1["counts"], {"done": 1}, r1)
        copies = sorted(self.dest.rglob("*.flac"))
        _say("\n", [str(c.relative_to(self.dest)) for c in copies],
              [str(c.relative_to(self.dest)) for c in self.dest.rglob("cover.jpg")])
        self.assertEqual([c.parent.name for c in copies], ["Disc 01"] * 2)
        self.assertEqual([size(c.read_bytes()) for c in self.dest.rglob("cover.jpg")], [(800, 800)])
        calls = []
        p2 = self._run(prof, calls)
        self.assertEqual(p2["albums"][0]["status"], "unchanged")
        self.assertEqual(calls, [], "run 2 looked the cover up again: "
                         + artwork.needs_art(copies[0].parent.parent, copies, prof))

    def test_library_run_in_place_does_not_look_again(self):
        mv = self.profile(artwork=self.art(), operation="move", destination=str(self.dest))
        executor.Executor(self._run(mv, [])).run()
        copies = sorted(self.dest.rglob("*.flac"))
        self.assertEqual([c.parent.name for c in copies], ["Disc 01"] * 2)
        inplace = self.profile(artwork=self.art(), sources=[{"path": str(self.dest)}])
        import engine as eng_mod
        e = eng_mod.Engine.__new__(eng_mod.Engine)
        e.profile = inplace
        g = e.albums_in(self.dest)
        self.assertEqual(len(g), 1)
        self.assertEqual(artwork.needs_art(g[0].folder, g[0].files, inplace), "",
                         f"an in-place run over the library looks {g[0].folder.name}'s cover up on every run")


# Review 4: undo protects tags edited after the job ("its tags were edited after the job;
# left as they are"), but _tags_sig sees the pictures only as a COUNT (~pictures). A cover
# replaced by the job and then changed by the user (mp3tag / Picard / a player: "set cover",
# the one front swapped for their own scan) is silently overwritten by the old small cover
# when the job is undone - the user's picture is lost without a warning.


class UndoOverEdit(tcr.Replace):
    def test_undo_keeps_a_picture_set_after_the_job(self):
        prof = self.profile(artwork=self.art(files=False))
        p = pl.build([self.found(prof, jpeg(900, 900))], prof)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(size(artwork.embedded_front(self.f1)), (900, 900))
        # the user sets their own scan as the cover afterwards (tags untouched)
        mine = jpeg(1400, 1400, (0, 200, 0))
        from mutagen.flac import FLAC, Picture
        t = FLAC(self.f1)
        t.clear_pictures()
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", mine
        t.add_picture(pic)
        t.save()
        out = executor.undo(p["job"])
        _say("\n", out)
        self.assertEqual(size(artwork.embedded_front(self.f1)), (1400, 1400),
                         "undo replaced the user's own cover (set after the job) with the old 300 px one, "
                         "without a word")


# Review 4 (regression of 04d0b82): an UNTAGGED album folder holding the album twice - FLAC
# and an MP3 copy named a little differently ("01 a.flac", "01 - a.mp3") - is one album to
# the grouping and to read_album (which drops the MP3 copy, so prop.files is the FLACs).
# artwork._other_music now takes the untagged MP3s for ANOTHER album's music (their stems
# differ from the FLACs', so the "copy of its own track" exception misses them): the album's
# small cover.jpg is never replaced and is not its "own" cover; CoverUp's local scans are off.


class UntaggedTwoFormats(Base):
    def test_untagged_album_with_mp3_copies_keeps_its_own_cover(self):
        d = self.lib / "Some Band" / "Some Album"
        fl = [audio(d / "01 a.flac", 300), audio(d / "02 b.flac", 500)]
        audio(d / "01 - a.mp3", 300)
        audio(d / "02 - b.mp3", 500)
        (d / "cover.jpg").write_bytes(jpeg(300, 300))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "min_px": 500})
        e = eng_mod.Engine.__new__(eng_mod.Engine)
        e.profile = prof
        groups = e.albums_in(d.parent)
        self.assertEqual(len(groups), 1, groups)                 # one album to the grouping
        album = ma.read_album(d, d.parent.name, d.name)
        own = sorted(t["path"] for t in album["tracks"])
        self.assertEqual(own, sorted(map(str, fl)))             # read_album keeps the FLACs: prop.files
        mine = groups[0].files                                   # what identify() hands on (group_files)
        small = artwork.small_covers(d, mine, prof)
        self.assertFalse(artwork._other_music(d, mine), "the album's own MP3 copies taken for another album")
        self.assertTrue(small.get("file"), "the small cover.jpg is not replaced")


class GroupFilesReachArtwork(Base):
    # 2 (engine side): the album's covers are judged with every file of the album as found
    def test_identify_hands_on_the_groups_files(self):
        e = eng_mod.Engine.__new__(eng_mod.Engine)
        e.profile = self.profile(artwork={"enabled": True})
        g = eng_mod.Group(self.album, [self.f1, self.f2, self.album / "01 - a.mp3"])
        with mock.patch.object(eng_mod.Engine, "_identify",
                               lambda self_, g_: pl.Proposal(source_dir=str(g_.folder), files=[str(self.f1)])):
            prop = e.identify(g)
        self.assertEqual(e._art_view(prop)[1], [str(f) for f in g.files])


for _cls in (PartialCopy, OneDiscFolder, UndoOverEdit,):                 # (Replace's own tests run in test_cover_replace)
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()
