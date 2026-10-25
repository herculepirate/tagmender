# Sixth review of TagMender's file operations. Each test asserts the correct behaviour
# and fails at 3933e10.
#
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


def groups(prof, path):
    """engine.Engine.albums_in without providers (as `tagmender scan` does)."""
    return eng_mod.Engine.albums_in(type("E", (), {"profile": prof})(), path)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


class Base6(Base):
    """Base without its stock album."""
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")


def work(p):
    """Albums of a plan that would still do something."""
    return [(a["source_dir"], a["status"],
             [(Path(fe["src"]).name, fe["op"], fe["dst"]) for fe in a["files"] if fe["op"] != "none"],
             [(Path(c["src"]).name, c["op"], c["dst"]) for c in a["companions"]], a["notes"])
            for a in p["albums"] if pl.has_work(a) or a["status"] not in ("unchanged", "unresolved")]


# ---------------------------------------------------------------------------------------
# R6-01. Copy is not idempotent. After `copy` has put an album into the destination, the
# next plan of the same source (the next `tagmender run`, a GUI re-preview, the watcher
# after the source folder changed) finds the destination names taken by the copies it
# made itself (plan._unique: cand.exists() and not the same inode) and plans a SECOND
# copy of every track and companion as "01.F ~1a2b.flac", "cover ~6611.jpg" ... next to
# the first. Applied, the destination album holds every track twice; a third run is
# blocked ("already exists ... and so does the suffixed name"). Every preset, plain
# albums, disc sets, compilations.
class R6_01_CopyTwiceDuplicatesTheAlbum(Base):
    def test_second_copy_run_is_unchanged(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        res = executor.Executor(pl.build([self.proposal()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        before = tree(self.dest)
        p2 = pl.build([self.proposal()], prof)
        self.assertEqual(work(p2), [], "the second plan copies the album again")
        executor.Executor(p2).run()
        self.assertEqual(tree(self.dest), before)


# ---------------------------------------------------------------------------------------
# R6-02. plan._own_album_dir (plan.py:621-639) takes dst.parent for "the album folder" of
# every identified file. For a single track (acoustid recording, or any file without an
# album artist) and for the "singleton" preset that folder is SHARED by design:
#   singleton  "Artist/Singles/Artist - Title"   -> album folder = Artist/Singles
#   picard     "Artist/Title" (no album artist)  -> album folder = the ARTIST folder
#   tagmender  "Artist/S0" (no album, no year)    -> album folder = the ARTIST folder
# Each single has its own release_id, so the second single in a plan (or any single
# when the folder already holds another one on disk) is "a different release there" and
# goes to "Solo ~51c5/..." / "Singles ~51c5/...": a new look-alike artist folder per
# single, and the library's own Artist/Singles is never used again.
class R6_02_SinglesGetSuffixedArtistFolders(Base):
    def single(self, k, artist="Solo"):
        d = self.lib / "inbox" / f"s{k}"
        f = audio(d / "x.flac", 400 + 50 * k)
        return pl.Proposal(source_dir=str(d), files=[str(f)], provider="acoustid", release_id=f"rec{k}",
                           confidence=99.0, targets=[{"title": [f"Song {k}"], "artist": [artist],
                                                      "musicbrainz_recordingid": [f"rec{k}"]}],
                           release={"title": [f"Song {k}"], "artist": [artist]})

    def check(self, preset):
        prof = self.profile(operation="move", destination=str(self.dest), naming={"preset": preset})
        p = pl.build([self.single(0), self.single(1)], prof)
        tops = {Path(fe["dst"]).relative_to(self.dest).parts[0] for a in p["albums"] for fe in a["files"]}
        self.assertEqual(tops, {"Solo"}, f"{preset}: {[fe['dst'] for a in p['albums'] for fe in a['files']]}")
        folders = {str(Path(fe["dst"]).parent) for a in p["albums"] for fe in a["files"]}
        self.assertEqual(len(folders), 1, folders)

    def test_singleton_preset(self):
        self.check("singleton")

    def test_picard_preset(self):
        self.check("picard")

    def test_tagmender_preset(self):
        self.check("tagmender")

    def test_single_into_an_existing_singles_folder(self):
        prof = self.profile(operation="move", destination=str(self.dest), naming={"preset": "singleton"})
        old = audio(self.dest / "Solo" / "Singles" / "Solo - Old Song.flac", 300, title="Old Song", artist="Solo",
                    musicbrainz_albumid="old-release")
        p = pl.build([self.single(0)], prof)
        dst = Path(p["albums"][0]["files"][0]["dst"])
        self.assertEqual(dst.parent, old.parent, f"planned into {dst.parent}")


# ---------------------------------------------------------------------------------------
# R6-03. _other_release_there (plan.py:145-180) is given only the files the plan KEEPS as
# "this album's own". A file of the same album that the plan skips - the profile's
# formats (an MP3 bonus track in a FLAC album), a file rule (Skip "interview"), an
# unreadable file - or one read_album drops as a duplicate copy, is "untagged music of
# something else". An album that is already where the template puts it is then "sharing
# its folder with a different release" and the whole album is moved to
# "2001 - Real Album ~xxxx", leaving the skipped track alone in the old folder: a move
# within the library splits an already-organised album.
class R6_03_SkippedTrackSplitsAnOrganisedAlbum(Base):
    def organised(self, prof):
        res = executor.Executor(pl.build([self.proposal()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        home = self.lib / "Real Artist" / "2001 - Real Album"
        self.assertEqual(sorted(x.name for x in home.glob("*.flac")), ["01.F.flac", "02.S.flac"])
        return home

    def replan(self, prof, home, extra, t3):
        files = [home / "01.F.flac", home / "02.S.flac", extra]
        prop = self.proposal(source_dir=str(home), files=[str(f) for f in files],
                             targets=[target(1, "First"), target(2, "Second"), t3])
        return pl.build([prop], prof)

    def test_mp3_bonus_outside_the_profile_formats(self):
        prof = self.profile(operation="move", destination=str(self.lib), formats=["flac"])
        home = self.organised(prof)
        bonus = audio(home / "03 bonus.mp3", 700, album="Real Album", albumartist="Real Artist", title="Bonus")
        p = self.replan(prof, home, bonus, target(3, "Bonus"))
        self.assertEqual(work(p), [], "an organised album is moved away from its bonus track")

    def test_file_skipped_by_a_rule(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "iv", "name": "interviews", "enabled": True, "action": "skip",
                                               "target": "file",
                                               "when": {"field": "title", "op": "contains", "value": "interview"}}]})
        prof = self.profile(operation="move", destination=str(self.lib))
        home = self.organised(prof)
        iv = audio(home / "03.I.flac", 700, album="Real Album", albumartist="Real Artist", title="Interview")
        p = self.replan(prof, home, iv, target(3, "Interview"))
        self.assertEqual(work(p), [], "an organised album is moved away from its skipped interview track")


# ---------------------------------------------------------------------------------------
# R6-04. Since 3933e10 a nested album with its own tags ("Main/Bonus CD", album tag
# "Main (Bonus CD)") has the outer folder as owner and is split off by album_key: its
# group's folder is "Main", not "Main/Bonus CD". Moving it, plan._companions(Main, ...)
# sees Main's own tracks (not moving with the bonus) and gives up: the bonus disc's own
# cover, log and cue stay in "Main/Bonus CD" with no music, the cue naming tracks that
# are gone. (Main's companions skip the bonus folder, so nobody takes them.) Before
# 3933e10 the bonus folder was its own group and took its companions.
class R6_04_NestedAlbumLeavesItsCompanions(Base6):
    def test_bonus_disc_takes_its_cover_log_and_cue(self):
        m = self.lib / "Band" / "Main"
        a = [audio(m / "01 a.flac", 500, album="Main", albumartist="Band"),
             audio(m / "02 b.flac", 510, album="Main", albumartist="Band")]
        bdir = m / "Bonus CD"
        b = [audio(bdir / "01 x.flac", 600, album="Main (Bonus CD)", albumartist="Band"),
             audio(bdir / "02 y.flac", 610, album="Main (Bonus CD)", albumartist="Band")]
        (m / "cover.jpg").write_bytes(b"main cover")
        (bdir / "cover.jpg").write_bytes(b"bonus cover")
        (bdir / "bonus.log").write_text("EAC")
        (bdir / "bonus.cue").write_text('FILE "01 x.flac" WAVE\nFILE "02 y.flac" WAVE\n')
        prof = self.profile(operation="move", destination=str(self.dest))
        gs = groups(prof, self.lib)
        props = []
        for g in gs:
            name = tagio.read(g.files[0])["album"][0]
            props.append(self.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                       release_id=name,
                                       targets=[target(1, "One", album=name, artist="Band"),
                                                target(2, "Two", album=name, artist="Band")]))
        p = pl.build(props, prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        left = tree(m) if m.exists() else []
        self.assertEqual(left, [], f"left behind without music: {left}; notes {[a['notes'] for a in p['albums']]}")


# ---------------------------------------------------------------------------------------
# R6-05. The same owner rule makes an artist folder holding one loose track the owner of
# every album folder inside it ("Band/single.flac" next to "Band/Album A/", "Band/Album
# B/"; a very common layout). Then:
#   a) tagged: every album is split off a "mixed folder" whose folder is "Band"; moving an
#      album, plan._companions(Band, ...) sees the loose track and gives up, so every
#      album leaves its cover, log, cue and Scans/ behind in its emptied folder;
#   b) untagged (the auto-tagging case): every key is ("folder", ...), so albums_in merges
#      the loose track and ALL the artist's albums into one "untagged disc set" - one
#      group of every track the artist has, which can never be identified as any album.
class R6_05_LooseTrackInArtistFolder(Base6):
    def make(self, tagged):
        art = self.lib / "Band"
        tags = (lambda n: {"album": n, "albumartist": "Band"}) if tagged else (lambda n: {})
        loose = audio(art / "single.flac", 300, **tags("Single"))
        a = [audio(art / "Album A" / "01.flac", 500, **tags("Album A")),
             audio(art / "Album A" / "02.flac", 510, **tags("Album A"))]
        b = [audio(art / "Album B" / "01.flac", 600, **tags("Album B")),
             audio(art / "Album B" / "02.flac", 610, **tags("Album B"))]
        for d in ("Album A", "Album B"):
            (art / d / "cover.jpg").write_bytes(d.encode())
            (art / d / "rip.log").write_text("EAC")
        return art, loose, a, b

    def test_untagged_albums_are_not_merged_with_the_loose_track(self):
        art, loose, a, b = self.make(tagged=False)
        gs = groups(self.profile(), self.lib)
        sets = sorted(sorted(map(str, g.files)) for g in gs)
        self.assertIn(sorted(map(str, a)), sets, [(str(g.folder), len(g.files)) for g in gs])
        self.assertIn(sorted(map(str, b)), sets)

    def test_tagged_album_moves_with_its_cover_and_log(self):
        art, loose, a, b = self.make(tagged=True)
        prof = self.profile(operation="move", destination=str(self.dest))
        g = [g for g in groups(prof, self.lib) if sorted(g.files) == sorted(a)][0]
        prop = self.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], release_id="A",
                             targets=[target(1, "One", album="Album A", artist="Band"),
                                      target(2, "Two", album="Album A", artist="Band")])
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        left = tree(art / "Album A") if (art / "Album A").exists() else []
        self.assertEqual(left, [], f"left behind without music: {left}; notes {p['albums'][0]['notes']}")


# ---------------------------------------------------------------------------------------
# R6-06. Executor.album (executor.py:573-583) refuses an album when a file's st_ctime
# differs from the plan's. Moving album 1's companion (copy, place, os.unlink(src))
# changes the link count - and so the ctime - of every other name of that file. Files
# deduplicated with hard links (jdupes -L / rdfind on an Unraid share: the same EAC log,
# cover or folder.jpg in several albums) therefore make every later album of the same
# job that shares one fail with "rip.log changed since the plan was made"; nothing in
# that album is done although nobody changed anything.
class R6_06_HardLinkedCompanionRefusesTheNextAlbum(Base):
    def test_both_albums_move(self):
        alb2 = self.lib / "Other" / "Second"
        g1 = audio(alb2 / "01.flac", 700, album="Second")
        g2 = audio(alb2 / "02.flac", 750, album="Second")
        os.link(self.album / "rip.log", alb2 / "rip.log")          # deduplicated: one file, two names
        prof = self.profile(operation="move", destination=str(self.dest))
        p2 = self.proposal(source_dir=str(alb2), files=[str(g1), str(g2)], release_id="888",
                           targets=[target(1, "A", album="Second"), target(2, "B", album="Second")])
        p = pl.build([self.proposal(), p2], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)


# ---------------------------------------------------------------------------------------
# R6-07. A disc set with one disc tagged and one not (Album/CD1 untagged, Album/CD2
# tagged "Album") is a "mixed folder" (engine.py:157-171): the untagged tracks become
# singles whose folder is the ALBUM folder. The first single planned is moved with the
# whole album folder's companions: plan._companions(Album, {CD1/01.flac: ...}) finds no
# audio directly in Album, treats Album as the single's folder and sends Album/cover.jpg,
# rip.log, rip.cue and Scans/ to wherever that one track goes (here the artist folder of
# a fingerprinted recording). Every later group of the album lists the same companions
# and is refused ("cover.jpg is gone since the plan was made"). R5-02 fixed only the
# all-untagged case.
class R6_07_HalfTaggedDiscSetCoverLeavesWithOneTrack(Base6):
    def test_album_cover_does_not_go_with_a_single(self):
        alb = self.lib / "Band" / "Box"
        u = [audio(alb / "CD1" / "01.flac", 500), audio(alb / "CD1" / "02.flac", 510)]
        t = [audio(alb / "CD2" / "01.flac", 600, album="Box", albumartist="Band"),
             audio(alb / "CD2" / "02.flac", 610, album="Box", albumartist="Band")]
        (alb / "cover.jpg").write_bytes(b"box cover")
        (alb / "rip.log").write_text("EAC")
        (alb / "Scans").mkdir()
        (alb / "Scans" / "booklet.jpg").write_bytes(b"booklet")
        prof = self.profile(operation="move", destination=str(self.dest))
        props = []
        for k, g in enumerate(groups(prof, self.lib)):
            if g.single:
                props.append(pl.Proposal(source_dir=str(g.folder), files=[str(g.files[0])], provider="acoustid",
                                         release_id=f"rec{k}", confidence=99.0,
                                         targets=[{"title": [f"Song {k}"], "artist": ["Band"]}],
                                         release={"title": [f"Song {k}"], "artist": ["Band"]}))
            else:
                props.append(self.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                           release_id="box", targets=[target(1, "C", album="Box", artist="Band"),
                                                                      target(2, "D", album="Box", artist="Band")]))
        p = pl.build(props, prof)
        took = {a["source_dir"] + " " + str([Path(fe["src"]).relative_to(alb) for fe in a["files"]]):
                [Path(c["src"]).name for c in a["companions"]] for a in p["albums"]}
        for a in p["albums"]:
            if len(a["files"]) == 1:
                self.assertFalse(a["companions"], f"the album's companions go with one track: {took}")


# ---------------------------------------------------------------------------------------
# R6-08. The layout 3933e10 was made for (Rotting Christ - A Dead Poem: disc 1 in the
# album folder, the bonus disc in a folder inside it) moved with a preset that makes
# "Disc 01/", "Disc 02/": plan._companions maps the album folder itself to "Disc 01"
# (dmap[src_dir] is disc 1's destination), so the ALBUM's cover.jpg, booklet Scans/ and
# folder-level files land inside "Disc 01/" and the album folder has no cover (with
# artwork on, a second cover.jpg is then written next to the disc folders). A CD1/CD2
# layout of the same album gets them in the album folder.
class R6_08_NestedBonusDiscAlbumCoverInDisc01(Base6):
    def test_album_cover_and_scans_go_to_the_album_folder(self):
        top = self.lib / "Rotting Christ" / "1997 - A Dead Poem"
        a = audio(top / "01.flac", 500, album="A Dead Poem", albumartist="Rotting Christ")
        b = audio(top / "1997 - Darkness We Feel (free bonus CD)" / "01.flac", 600, album="A Dead Poem",
                  albumartist="Rotting Christ")
        (top / "cover.jpg").write_bytes(b"front")
        (top / "Scans").mkdir()
        (top / "Scans" / "booklet.jpg").write_bytes(b"booklet")
        prof = self.profile(operation="move", destination=str(self.dest))
        (g,) = groups(prof, self.lib)
        prop = self.proposal(source_dir=str(g.folder), files=[str(a), str(b)], targets=[
            target(1, "One", album="A Dead Poem", artist="Rotting Christ", totaldiscs=["2"]),
            target(1, "Two", album="A Dead Poem", artist="Rotting Christ", discnumber=["2"], totaldiscs=["2"])])
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = Path(p["albums"][0]["files"][0]["dst"]).parent.parent
        self.assertEqual(new.name, "2001 - A Dead Poem")
        self.assertTrue((new / "cover.jpg").exists() and (new / "Scans" / "booklet.jpg").exists(),
                        f"album folder now holds: {tree(new)}")


# ---------------------------------------------------------------------------------------
# R6-09. plan._own_album_dir (plan.py:632) appends the collision suffix " ~1a2b" to an
# album folder name that filenames.make_path has already cut to max_dir_bytes (the
# suffix room is reserved for the FILE name only). Two releases whose (long, here
# Japanese) titles share the folder name: the second is planned into a 257-byte folder
# name, "ready", and fails at apply with ENAMETOOLONG (rolled back, nothing done). The
# same suffix also breaks a max_path_chars limit the user set for Windows clients.
class R6_09_SuffixedAlbumFolderTooLong(Base):
    def test_suffixed_folder_fits_the_limit(self):
        w = "\u3042" * 16                                 # 48 UTF-8 bytes a word
        props = []
        for n, freq in ((1, 500), (2, 700)):
            alb = self.lib / "VA" / f"V{n}"
            f = audio(alb / "01.flac", freq, album=f"x{n}")
            title = " ".join([w] * 5 + [f"Vol.{n}"])
            props.append(self.proposal(source_dir=str(alb), files=[str(f)], release_id=str(n),
                                       targets=[target(1, f"S{n}", album=title, artist="X", totaltracks=["1"])]))
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build(props, prof)
        for a in p["albums"]:
            for fe in a["files"]:
                for part in Path(fe["dst"]).relative_to(self.dest).parts:
                    self.assertLessEqual(len(part.encode()), 255, part)
        res = executor.Executor(p).run()
        self.assertNotIn("failed", res["counts"], res)


# ---------------------------------------------------------------------------------------
# R6-10. A loose track in a folder named on the command line (`tagmender run ~/inbox`,
# a folder that is not one of the profile's sources) is a single whose folder is that
# root. Planner.library_roots (plan.py:732-739) deliberately leaves the command-line
# folder out, so _companions is not limited to per-track companions there: the single
# takes every non-music folder of the inbox (Documents/, Software/ ...) and every
# .jpg/.pdf/.txt/.log lying in it into the library folder of that one track.
class R6_10_LooseTrackInACommandLineFolder(Base6):
    def test_loose_track_takes_only_its_own_companions(self):
        inbox = self.tmp / "inbox"
        loose = audio(inbox / "loose.flac", 300, title="loose")
        audio(inbox / "Some Album" / "01.flac", 500, album="Some Album", albumartist="Band")
        audio(inbox / "Some Album" / "02.flac", 510, album="Some Album", albumartist="Band")
        (inbox / "Documents").mkdir()
        (inbox / "Documents" / "taxes.pdf").write_bytes(b"%PDF private")
        (inbox / "readme.txt").write_text("downloader notes")
        (inbox / "wallpaper.jpg").write_bytes(b"jpg")
        prof = self.profile(operation="move", destination=str(self.dest))     # sources: lib, not inbox
        g = [g for g in groups(prof, inbox) if g.files == [loose]][0]
        self.assertEqual(g.folder, inbox)
        prop = pl.Proposal(source_dir=str(g.folder), files=[str(loose)], provider="acoustid", release_id="rec",
                           confidence=99.0, targets=[{"title": ["Loose"], "artist": ["Solo"]}],
                           release={"title": ["Loose"], "artist": ["Solo"]})
        p = pl.build([prop], prof, extra_roots=[inbox])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(sorted(tree(inbox)), ["Documents/taxes.pdf", "Some Album/01.flac", "Some Album/02.flac",
                                               "readme.txt", "wallpaper.jpg"],
                         f"moved into the library with one track: {tree(self.dest)}")


# ---------------------------------------------------------------------------------------
# R6-11. Not idempotent: a stray track of another release inside an album folder that is
# already where the template puts the album. The album is planned first (album_key
# order), _other_release_there sees the stray (which this same plan moves out) and the
# album is moved to "2001 - Real Album ~xxxx". The next run finds "2001 - Real Album"
# empty and moves the album back: every album that held a stray is moved twice, and
# between the runs it sits under a suffixed name.
class R6_11_StrayTrackMovesTheAlbumTwice(Base6):
    def test_plan_apply_plan_is_unchanged(self):
        home = self.lib / "Real Artist" / "2001 - Real Album"
        a = [audio(home / "01.F.flac", 500, album="Real Album", albumartist="Real Artist"),
             audio(home / "02.S.flac", 510, album="Real Album", albumartist="Real Artist")]
        stray = audio(home / "zz stray.flac", 900, album="Other", albumartist="Someone")
        prof = self.profile(operation="move", destination=str(self.lib))

        def props():
            out = []
            for g in groups(prof, self.lib):
                if tagio.read(g.files[0])["album"] == ["Other"]:
                    out.append(self.proposal(source_dir=str(g.folder), files=[str(g.files[0])], release_id="999",
                                             targets=[target(1, "Stray", album="Other", artist="Someone",
                                                             totaltracks=["1"])]))
                else:
                    out.append(self.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files]))
            return out
        p1 = pl.build(props(), prof)
        res = executor.Executor(p1).run()
        self.assertNotIn("failed", res["counts"], res)
        p2 = pl.build(props(), prof)
        self.assertEqual(work(p2), [], f"after the first run: {tree(self.lib)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
