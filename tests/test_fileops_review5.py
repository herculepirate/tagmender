# Fifth review of TagMender's file operations: planner / grouping / CLI cases the crash
# fuzzers cannot reach. Each test asserts the correct behaviour and fails at 69e21bf.
#
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import argparse
import io
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


def groups(prof, path):
    """engine.Engine.albums_in without providers (as `tagmender scan` does)."""
    return eng_mod.Engine.albums_in(type("E", (), {"profile": prof})(), path)


class Base5(Base):
    """Base without its stock album, so a scan of the library sees only the test's own."""
    def setUp(self):
        super().setUp()
        import shutil
        shutil.rmtree(self.lib / "Some Artist")


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


# ---------------------------------------------------------------------------------------
# R5-01. A two-disc album ripped into ONE folder ("1-01 a.flac", "2-01 c.flac"), the most
# common flat layout, moved with the default "tagmender" preset (which makes "Disc 01/",
# "Disc 02/" below the album folder). plan._companions (plan.py:184-185) sees the files of
# one source folder going to two folders and gives up: every companion (cover, log, cue,
# scans) stays in the old folder, which is left holding no music; the cue there now names
# files that are gone. The disc-folder -> flat case right above it (commonpath) is handled.
class R5_01_FlatDiscSetLosesItsCompanions(Base):
    def test_flat_two_disc_album_takes_its_cover_log_and_scans(self):
        alb = self.lib / "Band" / "Box (2CD)"
        a = audio(alb / "1-01 a.flac", 500, album="Box")
        b = audio(alb / "2-01 b.flac", 600, album="Box")
        (alb / "cover.jpg").write_bytes(b"front")
        (alb / "rip.log").write_text("EAC")
        (alb / "Scans").mkdir()
        (alb / "Scans" / "back.jpg").write_bytes(b"back")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(alb), files=[str(a), str(b)],
                             targets=[target(1, "Alpha", album="Box", discnumber=["1"], totaldiscs=["2"]),
                                      target(1, "Beta", album="Box", discnumber=["2"], totaldiscs=["2"])])
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = Path(p["albums"][0]["files"][0]["dst"]).parent.parent           # .../2001 - Box
        self.assertEqual(sorted(tree(new)), sorted(["Disc 01/101.A.flac", "Disc 02/201.B.flac", "cover.jpg",
                                                    "rip.log", "Scans/back.jpg"]),
                         f"left behind in the old folder: {tree(alb) if alb.exists() else []}; "
                         f"notes {p['albums'][0]['notes']}")


# ---------------------------------------------------------------------------------------
# R5-02. An UNTAGGED disc set (Album/CD1, Album/CD2) - exactly the library-auto-tagging
# case. engine.albums_in (engine.py:118-135) files both disc folders under the album
# folder, but exclusions.album_key groups untagged files by their OWN folder (CD1 vs CD2),
# so the album looks "mixed" and every untagged track becomes a single (key "folder" ->
# one Group per file). Identified as singles, each track is planned on its own; the first
# one planned takes the album folder's cover/log with it (plan._companions treats the
# album folder as its folder) and every later one meets a "gone since the plan" conflict.
class R5_02_UntaggedDiscSetSplitIntoSingles(Base5):
    def make(self):
        alb = self.lib / "Some Band" / "Untagged Box"
        fs = [audio(alb / "CD1" / "01.flac", 500), audio(alb / "CD1" / "02.flac", 520),
              audio(alb / "CD2" / "01.flac", 600)]
        for f in fs:                                       # ffmpeg writes an encoder tag only
            self.assertFalse(tagio.read(f).get("album"))
        (alb / "cover.jpg").write_bytes(b"front")
        return alb, fs

    def test_grouped_as_one_album(self):
        alb, fs = self.make()
        g = groups(self.profile(), self.lib)
        self.assertEqual([(x.folder, sorted(x.files), x.single) for x in g], [(alb, sorted(fs), False)],
                         [(str(x.folder), [str(f) for f in x.files], x.single) for x in g])

    def test_the_album_cover_does_not_leave_with_one_track(self):
        alb, fs = self.make()
        prof = self.profile(operation="move", destination=str(self.dest))
        props = [pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                             targets=[target(1, f"T{k}", album=f"Single {k}", totaltracks=["1"])],
                             provider="musicbrainz", release_id=str(k), confidence=97.0)
                 for k, g in enumerate(groups(prof, self.lib))]
        p = pl.build(props, prof)
        res = executor.Executor(p).run()
        self.assertNotIn("conflict", res["counts"], res)
        moved_cover = [c for a in p["albums"] for c in a["companions"] if c["src"].endswith("cover.jpg")]
        self.assertLessEqual(len(moved_cover), 1)
        self.assertFalse(moved_cover and "Single" in moved_cover[0]["dst"],
                         f"the album's cover went with one track: {moved_cover}")


# ---------------------------------------------------------------------------------------
# R5-03. engine.DISC_DIR (engine.py:47) also matches any folder named with one or two
# digits ("^\d{1,2}$"). Albums named "19", "21", "25" (Adele), "1" (The Beatles), "4"
# (Beyonce) in an Artist/Album layout are taken for disc folders of the ARTIST folder:
# all of the artist's albums become one "mixed folder" with source_dir = the artist
# folder. Moving them: the first album planned takes the artist folder's own files
# (artist.jpg) and its non-music subfolders (Photos/) into its album folder; every later
# album lists the same files as its companions and is refused at apply time
# ("... is gone since the plan was made").
class R5_03_NumberedAlbumFoldersTakenForDiscs(Base5):
    def make(self):
        art = self.lib / "Adele"
        a = [audio(art / "19" / "01 a.flac", 500, album="19", albumartist="Adele"),
             audio(art / "19" / "02 b.flac", 520, album="19", albumartist="Adele")]
        b = [audio(art / "21" / "01 a.flac", 600, album="21", albumartist="Adele"),
             audio(art / "21" / "02 b.flac", 620, album="21", albumartist="Adele")]
        (art / "artist.jpg").write_bytes(b"artist photo")
        (art / "Photos").mkdir()
        (art / "Photos" / "live.jpg").write_bytes(b"live")
        return art, a, b

    def test_each_numbered_album_is_its_own_folder(self):
        art, a, b = self.make()
        g = groups(self.profile(), self.lib)
        self.assertEqual(sorted(str(x.folder) for x in g), [str(art / "19"), str(art / "21")])

    def test_moving_both_albums_leaves_the_artist_folder_files_alone(self):
        art, a, b = self.make()
        prof = self.profile(operation="move", destination=str(self.dest))
        props = []
        for g in groups(prof, self.lib):
            name = tagio.read(g.files[0])["album"][0]
            props.append(self.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                       targets=[target(1, "One", album=name, artist="Adele"),
                                                target(2, "Two", album=name, artist="Adele")],
                                       release_id=name))
        p = pl.build(props, prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        self.assertTrue((art / "artist.jpg").exists() and (art / "Photos" / "live.jpg").exists(),
                        f"artist folder files moved into an album: {tree(self.dest)}")


# ---------------------------------------------------------------------------------------
# R5-04. Disc folders named in words ("Disc One", "Disc Two", "Side A", "Side B") are not
# recognised by DISC_DIR (it needs a digit): each disc becomes an album of its own with
# its disc folder as source_dir, so a move leaves the album folder's cover, booklet scans
# and the cue/log that cover both discs behind, without music.
class R5_04_DiscFoldersNamedInWords(Base5):
    def test_disc_one_disc_two_is_one_album(self):
        alb = self.lib / "Band" / "Double"
        fs = [audio(alb / "Disc One" / "01.flac", 500, album="Double"),
              audio(alb / "Disc Two" / "01.flac", 600, album="Double")]
        g = groups(self.profile(), self.lib)
        self.assertEqual([(x.folder, sorted(x.files)) for x in g], [(alb, sorted(fs))],
                         [(str(x.folder), len(x.files)) for x in g])

    def test_vinyl_sides_are_one_album(self):
        alb = self.lib / "Band" / "LP"
        fs = [audio(alb / "Side A" / "A1.flac", 500, album="LP"),
              audio(alb / "Side B" / "B1.flac", 600, album="LP")]
        g = groups(self.profile(), self.lib)
        self.assertEqual([(x.folder, sorted(x.files)) for x in g], [(alb, sorted(fs))],
                         [(str(x.folder), len(x.files)) for x in g])


# ---------------------------------------------------------------------------------------
# R5-05. The default "tagmender" preset names the album folder "$words(%album%,5)".
# Two different releases of one album artist and year whose titles share their first five
# words ("Now That's What I Call Music! 47" / "... 48", "The Best of the Rest Vol 1/2")
# get the same folder. plan._unique only de-duplicates FILE names, so the second album is
# silently poured into the first one's folder (its cover.jpg/rip.log become
# "cover ~1a2b.jpg" next to the other album's) - two releases mixed in one folder, which
# every later scan then treats as a "mixed folder". No conflict, no note for the tracks.
class R5_05_TwoReleasesOneFolder(Base):
    def test_two_releases_never_share_an_album_folder(self):
        props = []
        for n, freq in ((47, 500), (48, 700)):
            alb = self.lib / "VA" / f"Now {n}"
            f = audio(alb / "01.flac", freq, album=f"Now {n}")
            g = audio(alb / "02.flac", freq + 50, album=f"Now {n}")
            (alb / "cover.jpg").write_bytes(f"cover {n}".encode())
            title = f"Now That's What I Call Music! {n}"
            props.append(self.proposal(source_dir=str(alb), files=[str(f), str(g)], release_id=str(n),
                                       targets=[target(1, f"Song {n}a", album=title, artist="Various Artists",
                                                       discogs_release_id=[str(n)]),
                                                target(2, f"Song {n}b", album=title, artist="Various Artists",
                                                       discogs_release_id=[str(n)])]))
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build(props, prof)
        folders = [{str(Path(fe["dst"]).parent) for fe in a["files"]} for a in p["albums"]]
        self.assertTrue(all(a["status"] in ("ready", "blocked") for a in p["albums"]))
        if all(a["status"] == "ready" for a in p["albums"]):
            self.assertFalse(folders[0] & folders[1], f"both releases planned into {folders[0] & folders[1]}")


# ---------------------------------------------------------------------------------------
# R5-06. Exit codes. tagmender._execute (tagmender.py:492-496 and 515-517):
#   a) albums the plan marks "blocked" (destination share not mounted, path too long,
#      collisions) are never applied, yet `tagmender run --yes` / `apply --yes` exits 0
#      ("Nothing to change.") - a cron job sees success while the share was unmounted;
#      the executor also counts "blocked" without making the job partial;
#   b) an album whose only work is a new cover.jpg / lyrics file / companion (its tracks
#      need nothing) is "ready" but not "actionable": "Nothing to change.", exit 0, and
#      the plan is never applied.
class R5_06_ExitCodes(Base):
    def args(self):
        return argparse.Namespace(yes=True, json=False)

    def test_blocked_album_is_not_success(self):
        import tagmender
        gone = self.tmp / "share-not-mounted"                   # destination missing
        prof = self.profile(operation="move", destination=str(gone))
        p = pl.build([self.proposal()], prof)
        self.assertEqual(p["albums"][0]["status"], "blocked")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = tagmender._execute(p, self.args())
        self.assertNotEqual(rc, tagmender.EXIT_OK, "nothing was done (destination unmounted) but exit 0")

    def test_plan_with_only_new_lyrics_is_applied(self):
        import tagmender
        prof = self.profile()
        p0 = pl.build([self.proposal()], prof)
        executor.Executor(p0).run()                            # tags now as identified
        prop = self.proposal(sidecars={str(self.f1): "[00:00.00]la la"})
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", pl.preview(p))
        self.assertTrue(p["albums"][0]["sidecars"])
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            rc = tagmender._execute(p, self.args())
        self.assertEqual(rc, tagmender.EXIT_OK)
        self.assertTrue(self.f1.with_suffix(".lrc").exists(), f"lyrics never written: {out.getvalue()!r}")


# ---------------------------------------------------------------------------------------
# R5-07. A saved plan applied later: Executor.album (executor.py:573-580) accepts a file
# as unchanged when size and mtime match. A tag edit made in Picard / foobar2000 with
# "preserve file modification times" on keeps both (FLAC padding absorbs the new tag, the
# mtime is put back). The plan's changes were decided against the OLD tags, so they are
# written over the user's newer edit - even against the field policy: a fill_missing
# field the user has just filled is overwritten. The plan records the inode
# (plan._fp) but nothing compares it, and st_ctime (which no tool can put back) is not
# recorded at all.
class R5_07_StalePlanOverwritesNewerTags(Base):
    def test_tag_edit_with_preserved_mtime_is_noticed(self):
        from mutagen.flac import FLAC
        prof = self.profile(field_policy={"default": "replace", "fields": {"genre": "fill_missing"}})
        prop = self.proposal(targets=[target(1, "First", genre=["Rock"]), target(2, "Second", genre=["Rock"])])
        p = pl.build([prop], prof)
        rows = {r["field"]: r["action"] for r in p["albums"][0]["files"][0]["diff"]}
        self.assertEqual(rows["genre"], "added")
        saved = pl.save(p, self.tmp / "plan.json")
        # hours later the user fills the genre in a tagger that preserves timestamps
        st = os.stat(self.f1)
        f = FLAC(self.f1)
        f["genre"] = "Jazz"
        f.save()
        os.utime(self.f1, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual((os.stat(self.f1).st_size, os.stat(self.f1).st_mtime_ns), (st.st_size, st.st_mtime_ns))
        executor.Executor(pl.load(saved)).run()
        self.assertEqual(tagio.read(self.f1).get("genre"), ["Jazz"],
                         "the user's newer genre was overwritten by a plan made before it (fill_missing)")


# ---------------------------------------------------------------------------------------
# R5-08. `tagmender recover` always exits 0 (tagmender.py:575-584), also when recover could
# not put an album back and says so only in its text ("put back as it was before the job
# (problems: [...cannot move back...])"): the album stays half-done (one track at the
# destination with new tags, the other at the source), the job is marked recovered (new
# jobs may start) and a script or the systemd unit running `tagmender recover` sees
# success. It also lets a StepError (job being run by another process) escape as a
# traceback instead of an error message and exit code.
class R5_08_RecoverExitCode(Base):
    def test_recover_that_left_an_album_half_done_is_not_success(self):
        import tagmender
        sys.path.insert(0, str(REPO / "tests"))
        from test_fileops_crash import die, in_child
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        calls = []
        real_place = executor._place

        def child():
            def place(stage, dst):
                calls.append(dst)
                if len(calls) == 2:
                    die()                                   # power cut while committing track 2
                return real_place(stage, dst)
            with mock.patch.object(executor, "_place", place):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        self.assertFalse(self.f1.exists())                    # track 1 was moved
        audio(self.f1, 300, title="another file the downloader put back")
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            rc = tagmender.main(["recover"])
        self.assertIn("problems", out.getvalue())
        self.assertNotEqual(rc, tagmender.EXIT_OK, out.getvalue())


# ---------------------------------------------------------------------------------------
# R5-09. engine.Engine.identify (engine.py:172-175) takes the proposal's files from
# match_albums.read_album, which os.walk()s the WHOLE group folder (match_albums.py:673)
# and excludes only g.others. So an album's proposal also holds:
#   - every file of a nested album ("Main/Bonus/" - its own group in albums_in),
#   - files in folders albums_in deliberately skips ("_extras/", "2delete/", hidden ones).
# With the default unresolved action "mark" the parent's plan writes TAGMENDER=Unresolved
# and the parent's reason into the nested album's tracks and into the skipped folder's
# files; the nested album's own entry then meets "changed since the plan was made" and is
# not applied. With unresolved "move" both are carried off with the parent. (The extra
# files also make the track count differ from the release, so such albums can never be
# identified as such.)
class R5_09_ProposalTakesNestedAndSkippedFiles(Base5):
    def test_album_proposal_holds_only_the_groups_files(self):
        m = self.lib / "Band" / "Main"
        audio(m / "01 a.flac", 500, album="Main", artist="Band")
        audio(m / "02 b.flac", 600, album="Main", artist="Band")
        other = audio(m / "Bonus" / "01 x.flac", 700, album="Other", artist="Band")
        demo = audio(m / "_extras" / "demo.flac", 800, album="Main", artist="Band", title="demo")
        prof = self.profile(providers={"musicbrainz": {"enabled": True}, "discogs": {"enabled": False},
                                       "web": {"enabled": False}})
        with mock.patch.object(eng_mod.ma, "process", lambda *a, **k: {"status": "nomatch", "why": ["none"]}):
            e = eng_mod.Engine(prof, http=object(), dm=None)
            gs = e.albums_in(self.lib)
            props = [e.identify(g) for g in gs]
        for g, pr in zip(gs, props):
            self.assertEqual(sorted(pr.files), sorted(map(str, g.files)), f"proposal for {g.folder}")
        before = (tagio.read(other), tagio.read(demo))
        p = pl.build(props, prof)
        res = executor.Executor(p).run()
        self.assertNotIn("conflict", res["counts"], res)
        self.assertEqual(tagio.read(demo).get("tagmender"), before[1].get("tagmender"),
                         "a file in a folder the engine skips was marked")


# ---------------------------------------------------------------------------------------
# R5-10. The same read_album walk with a loose track lying in a library / inbox ROOT
# (albums_in makes it a single whose folder is the root): its proposal holds EVERY audio
# file below the root - all the albums of the inbox. Unidentified (len(files) != 1, so no
# fingerprint), the single's unresolved entry marks, or with unresolved "move" carries
# off, the whole inbox; each album's own entry is then refused as changed/gone. The
# watcher does exactly this for an inbox with one loose file. Likewise
# `tagmender run Album/03.flac` (albums_in(file) -> a single in Album/) plans the whole
# album, not the one file asked for.
class R5_10_LooseTrackTakesTheWholeInbox(Base5):
    def engine(self, prof):
        mock.patch.object(eng_mod.ma, "process", lambda *a, **k: {"status": "nomatch", "why": ["none"]}).start()
        self.addCleanup(mock.patch.stopall)
        return eng_mod.Engine(prof, http=object(), dm=None)

    def prof(self, **kw):
        return self.profile(providers={"musicbrainz": {"enabled": True}, "discogs": {"enabled": False},
                                       "web": {"enabled": False}}, **kw)

    def test_loose_track_in_the_root(self):
        hold = self.tmp / "hold"
        hold.mkdir()
        prof = self.prof(unresolved={"action": "move", "move_to": str(hold), "allow_partial": False})
        loose = audio(self.lib / "loose.flac", 300, title="loose")
        alb = [audio(self.lib / "Band" / "Main" / "01 a.flac", 500, album="Main", artist="Band"),
               audio(self.lib / "Band" / "Main" / "02 b.flac", 600, album="Main", artist="Band")]
        e = self.engine(prof)
        gs = e.albums_in(self.lib)
        single = [g for g in gs if g.folder == self.lib][0]
        prop = e.identify(single)
        self.assertEqual(prop.files, [str(loose)], "the loose track's proposal holds the whole library")
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertTrue(all(f.exists() for f in alb), f"album carried off with the loose track: {tree(hold)}")

    def test_run_on_one_file(self):
        prof = self.prof()
        a = audio(self.lib / "Band" / "Main" / "01 a.flac", 500, album="Main", artist="Band")
        b = audio(self.lib / "Band" / "Main" / "02 b.flac", 600, album="Main", artist="Band")
        e = self.engine(prof)
        (g,) = e.albums_in(a)
        self.assertEqual(e.identify(g).files, [str(a)], "`tagmender run FILE` plans the whole album")


# ---------------------------------------------------------------------------------------
# R5-11. plan._cue_rewrites (plan.py:266-275) only knows entries written relative to the
# sheet's folder, exactly as os.path.relpath spells them. A playlist that names its tracks
# by absolute path (foobar2000/MusicBee exports, Unraid paths) or as "./01 a.flac" is
# moved with the album unchanged: every entry now points at a file that is gone.
class R5_11_PlaylistEntriesNotRewritten(Base):
    def run_with(self, lines):
        (self.album / "album.m3u8").write_text("#EXTM3U\n" + "\n".join(lines) + "\n", encoding="utf-8")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = [Path(fe["dst"]) for fe in p["albums"][0]["files"]]
        m3u = new[0].parent / "album.m3u8"
        self.assertTrue(m3u.exists())
        return new, m3u.read_text(encoding="utf-8")

    def test_absolute_entries(self):
        new, text = self.run_with([str(self.f1), str(self.f2)])
        for line in text.splitlines()[1:]:
            self.assertTrue((self.dest / line).exists() or (new[0].parent / line).exists(),
                            f"playlist entry {line!r} points at nothing after the move")

    def test_dot_slash_entries(self):
        new, text = self.run_with(["./01 a.flac", "./02 b.flac"])
        for line in text.splitlines()[1:]:
            self.assertTrue((new[0].parent / line).exists(),
                            f"playlist entry {line!r} points at nothing after the move")


# ---------------------------------------------------------------------------------------
# R5-12. The profile setting "remove_empty_dirs" (profiles.py:88) is read nowhere:
# Executor.album always calls cleanup_dirs after a move (executor.py:600-601), so a user
# who set it to false (folders other programs watch or expect, e.g. an *arr root folder
# per artist) still has the emptied source folders removed. (Likewise
# filenames.max_album_words is never read; the preset's 5 words are fixed.)
class R5_12_RemoveEmptyDirsIgnored(Base):
    def test_source_folder_kept_when_remove_empty_dirs_is_off(self):
        prof = self.profile(operation="move", destination=str(self.dest), remove_empty_dirs=False)
        self.assertFalse(prof["remove_empty_dirs"])
        p = pl.build([self.proposal()], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(self.album.is_dir(), "emptied source folder removed although remove_empty_dirs is false")



class BonusDiscInsideItsAlbum(Base5):
    """A folder inside a folder holding the album belongs to it when both are tagged as that
    album (Rotting Christ - A Dead Poem keeps its bonus disc); untagged or otherwise
    tagged folders are albums of their own."""

    def test_one_group(self):
        top = self.lib / "Rotting Christ" / "Albums" / "1997 - A Dead Poem"
        a = audio(top / "01.flac", 500, album="A Dead Poem", albumartist="Rotting Christ")
        b = audio(top / "1997 - Darkness We Feel (free bonus CD)" / "01.flac", 600, album="A Dead Poem",
                  albumartist="Rotting Christ")
        e = eng_mod.Engine.__new__(eng_mod.Engine)
        e.profile = self.profile()
        groups = e.albums_in(self.lib)
        self.assertEqual([(g.folder, sorted(map(str, g.files))) for g in groups], [(top, sorted([str(a), str(b)]))])

if __name__ == "__main__":
    unittest.main(verbosity=2)
