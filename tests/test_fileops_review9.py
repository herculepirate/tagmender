# Ninth independent review of TagMender's file operations (code at 72671d5). Each test asserts
# the correct behaviour and fails at 72671d5.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import re
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy, groups, tree, work  # noqa: E402


# ---------------------------------------------------------------------------------------
# R9-01. engine._group_key (engine.py:56-66) keys a file by album artist only when THAT
# file has an ALBUMARTIST tag; a file without one gets ("tags", "", title). An album where
# some tracks carry ALBUMARTIST and others do not (a bonus track added from another source,
# a few tracks re-tagged in foobar/Mp3tag, TPE2 missing on one MP3) is split into two
# groups in ONE folder: before 72671d5 exclusions.album_key fell back to the track artist
# (= the album artist) and the album was one group. Now the plan sees a "mixed folder":
# the identified part moves without its cover/log/cue ("folder holds other music too"),
# the rest stays behind as a separate one-track album.
class R9_01_PartialAlbumArtistSplitsAnAlbum(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")
        self.al = self.lib / "Band" / "2001 - Album"
        self.a = audio(self.al / "01 a.flac", 440, album="Album", artist="Band", albumartist="Band", title="a")
        self.b = audio(self.al / "02 b.flac", 550, album="Album", artist="Band", albumartist="Band", title="b")
        self.c = audio(self.al / "03 c.flac", 660, album="Album", artist="Band", title="c")   # no ALBUMARTIST
        (self.al / "rip.log").write_text("EAC log")
        (self.al / "cover.jpg").write_bytes(b"front")

    def test_one_group(self):
        gs = groups(self.profile(), self.lib)
        self.assertEqual([len(g.files) for g in gs], [3], [[f.name for f in g.files] for g in gs])

    def test_move_takes_the_album_folder_along(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        gs = groups(prof, self.lib)
        props = []
        for g in gs:
            n = len(g.files)
            props.append(pl.Proposal(
                source_dir=str(g.folder), files=[str(f) for f in g.files],
                targets=[target(k + 1, f"T{k}", album="Album", artist="Band", totaltracks=["3"]) for k in range(n)],
                provider="discogs", release_id="777", confidence=97, release={"album": ["Album"]}))
        p = pl.build(props, prof)
        executor.Executor(p).run()
        self.assertFalse((self.al / "rip.log").exists(), f"left behind: {tree(self.lib)}; notes "
                         f"{[a['notes'] for a in p['albums']]}")


# ---------------------------------------------------------------------------------------
# R9-02. plan._earlier_copy / plan._prior_album_dir (plan.py:691-726) take every copy the
# state database records for a source file (plan.copies_of: WHERE src=?), whatever
# profile or destination made it. Two copy profiles from one source library ("Lossy" to
# the lossy share, "Car" to a USB stick), or one copy profile whose destination was
# changed (the lossy library moved to another share, the old one kept): the second
# destination never gets its copies. With nothing to change the album is "unchanged"
# (silently never copied); with any tag to change the plan "brings the copy's tags up to
# date" in the OTHER library (op "tag" on files outside this profile's folders, new tracks
# mapped into that library's album folder), the executor refuses ("... is outside the
# folders this profile may change") and the album FAILS, on every run, for good. Where
# the other library lies inside this profile's roots, its copies are retagged instead.
class R9_02_CopiesOfAnotherDestination(Lossy):
    def test_second_copy_profile(self):
        res = executor.Executor(pl.build([self.proposal()], self.lossy())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        car = self.tmp / "car"
        car.mkdir()
        prof2 = self.profile(name="car", operation="copy", destination=str(car), formats=LOSSY)
        p2 = pl.build([self.proposal()], prof2)
        ops = [(fe["op"], fe["dst"]) for fe in p2["albums"][0]["files"]]
        res2 = executor.Executor(p2).run()
        self.assertTrue(all(op == "copy" and d.startswith(str(car) + "/") for op, d in ops),
                        f"planned {ops}; result {res2['albums']}")

    def test_second_copy_profile_with_new_tags(self):
        res = executor.Executor(pl.build([self.proposal()], self.lossy())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        car = self.tmp / "car"
        car.mkdir()
        prof2 = self.profile(name="car", operation="copy", destination=str(car), formats=LOSSY)
        t = [target(1, "First", label=["New Label"]), target(2, "Second", label=["New Label"])]
        res2 = executor.Executor(pl.build([self.proposal(targets=t)], prof2)).run()
        self.assertEqual(res2["counts"], {"done": 1}, res2["albums"])
        self.assertEqual(len([x for x in tree(car) if x.endswith(".mp3")]), 2, tree(car))


# ---------------------------------------------------------------------------------------
# R9-03. naming.Ctx._derive (naming.py:132-137) no longer splits an album artist without
# an ALBUMARTISTS tag. Right for new files, but a lossy library made by a copy profile
# before 72671d5 has "Garfunkel & Simon/2001 - Album/" (and "Earth & Fire & Wind/") and
# no "copies" rows (the table is new). The next run of the copy profile renders "Simon &
# Garfunkel/...", plan._earlier_copy looks only there (and at the suffixed name), finds
# nothing and copies every such album a SECOND time; the old copies stay. Every
# Discogs-identified album whose artist holds "&", "," or " and " is duplicated.
class R9_03_ArtistSplitChangeDuplicatesLossyLibrary(Lossy):
    def test_x(self):
        def old_derive(ctx):
            t = ctx.tags
            date = (t.get("originalyear") or t.get("originaldate") or t.get("date") or [""])[0]
            if str(date)[:4].isdigit():
                t["_year"] = [str(date)[:4]]
            names = t.get("albumartists") or []
            if not names:
                aa = (t.get("albumartist") or t.get("artist") or [""])[0]
                names = [a.strip() for a in re.split(r"\s*(?:,|&| and )\s*", aa) if a.strip()] or ([aa] if aa else [])
            if names:
                t["_albumartists_sorted"] = sorted(dict.fromkeys(names), key=str.casefold)
            a, aa = (t.get("artist") or [""])[0], (t.get("albumartist") or [""])[0]
            if a and aa and a != aa:
                t["_multiartist"] = ["1"]
        prof = self.lossy()
        t = [target(1, "First", artist="Simon & Garfunkel"), target(2, "Second", artist="Simon & Garfunkel")]
        prop = lambda: self.proposal(targets=t)  # noqa: E731
        with mock.patch.object(naming.Ctx, "_derive", old_derive):          # the version before 72671d5
            res = executor.Executor(pl.build([prop()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        db.execute("DELETE FROM copies")                                    # it kept no "copies" table
        db.commit()
        db.close()
        self.assertTrue((self.dest / "Garfunkel & Simon").is_dir(), tree(self.dest))
        p2 = pl.build([prop()], prof)
        executor.Executor(p2).run()
        mp3 = [x for x in tree(self.dest) if x.endswith(".mp3")]
        self.assertEqual(len(mp3), 2, f"the lossy library now holds the album twice: {mp3}")


# ---------------------------------------------------------------------------------------
# R9-04. Staging names now end in the audio extension (".tm-<job>-<hash>.part.flac",
# executor._stage_name, executor.py:353-358). TagMender's own scans skip hidden files, but
# match_albums.read_album (match_albums.py:666-677) and enrich.album_dirs (enrich.py:359-364)
# list audio by extension only. A staging copy left by an interrupted job (until `tagmender
# recover` runs; a job's in-place tag copy carries the NEW tags and release IDs) is taken
# as a track: read_album's duplicate-track pass PREFERS it (it carries release IDs, it is
# newer) and drops the real "01 a.flac" - web_tag.py then writes tags to, and files, the
# staging copy into the library and leaves the real track behind; enrich measures album
# ReplayGain with the extra copy and writes tags (and a ".tm-...part.lrc") to it. Before
# 72671d5 the name ended in ".part" and no tool saw it as audio.
class R9_04_StagingCopyTakenForATrack(Base):
    def setUp(self):
        super().setUp()
        import match_albums  # noqa: F401
        self.stage = executor._stage_name(self.f1, "20261025-101010-ab12")
        shutil.copy2(self.f1, self.stage)
        tagio.write(self.stage, {"musicbrainz_albumid": ["new-release"], "title": ["a"]})

    def test_read_album(self):
        import match_albums as ma
        al = ma.read_album(self.album, "Some Artist", "2001 - Some Album")
        self.assertEqual(sorted(Path(t["path"]).name for t in al["tracks"]), ["01 a.flac", "02 b.flac"])

    def test_enrich(self):
        import enrich
        got = [[f.name for f in fs] for _d, fs in enrich.album_dirs([self.lib])]
        self.assertEqual(got, [["01 a.flac", "02 b.flac"]])


# ---------------------------------------------------------------------------------------
# R9-05. engine._group_key: without an ALBUMARTIST tag the title alone groups. Two
# DIFFERENT albums that share a title, by different artists, loose in one folder (a
# downloads/inbox folder, "Singles/", a library root: "Greatest Hits" by Queen and by ABBA,
# "Live", "Unplugged", "Demo"), are now one group: before 72671d5 they were two (album_key
# used the track artist). The group is matched as one album (neither release fits it) and
# never as two singles, so a fingerprint can no longer identify each track; with
# unresolved.action "move" (the shipped Inbox profile) both go to _unresolved.
class R9_05_SameTitleDifferentArtistsMerge(Base):
    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        d = self.lib / "Downloads"
        audio(d / "Queen - Bohemian Rhapsody.mp3", 440, album="Greatest Hits", artist="Queen", title="BR")
        audio(d / "ABBA - Dancing Queen.mp3", 550, album="Greatest Hits", artist="ABBA", title="DQ")
        gs = groups(self.profile(), self.lib)
        self.assertEqual(sorted(len(g.files) for g in gs), [1, 1], [[f.name for f in g.files] for g in gs])


# ---------------------------------------------------------------------------------------
# R9-06. The R8-03 fix is incomplete. engine._DISC_SUFFIX (engine.py:52-53) strips only a
# numeric, trailing disc: "Album (Disc One)" / "Album (Disc Two)", "Album (Disc 1: Studio)"
# / "(Disc 2: Live)", "Album (CD 1) [Remastered]" stay two titles (engine.DISC_DIR knows
# "Disc One" for folders). The disc set is still two groups with one source folder, and the
# new plan._companions shared_ok (plan.py:337-347) now gives the album folder's own files
# to NEITHER: both discs move, cover.jpg and Scans/ stay behind in the old folder, which
# then holds nothing but them.
class R9_06_DiscWordTitlesOrphanScans(Base):
    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        al = self.lib / "Artist" / "Album"
        audio(al / "CD1" / "01 a.flac", 440, album="Album (Disc One)", artist="Artist", title="a")
        audio(al / "CD2" / "01 b.flac", 550, album="Album (Disc Two)", artist="Artist", title="b")
        (al / "cover.jpg").write_bytes(b"front")
        (al / "Scans").mkdir()
        (al / "Scans" / "back.jpg").write_bytes(b"back")
        prof = self.profile(operation="move", destination=str(self.dest))
        gs = groups(prof, self.lib)
        self.assertEqual([len(g.files) for g in gs], [2], "the disc set is one album")
        props = []
        for g in gs:
            props.append(pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                     targets=[target(1, f"T{n}", discnumber=[str(n)], totaldiscs=["2"],
                                                     totaltracks=["1"]) for n in range(1, len(g.files) + 1)],
                                     provider="discogs", release_id="777", confidence=97,
                                     release={"album": ["Real Album"]}))
        res = executor.Executor(pl.build(props, prof)).run()
        self.assertEqual(res["counts"], {"done": len(props)}, res["albums"])
        self.assertFalse(al.exists(), f"left in the old folder: {tree(self.lib)}; moved: {tree(self.dest)}")


# ---------------------------------------------------------------------------------------
# R9-07. The R8-09 fix (a restore that changes nothing writes nothing) covers tag
# snapshots only: executor._restore (executor.py:463) skips only kind "tags". Every format
# backed up as a whole file - DSF, DFF, WAV, AIFF (executor._backup, executor.py:450),
# WavPack/APE/Musepack/WMA (family "apev2"/"asf") - still gets a full copy of the file in
# the job folder for a plain rename-move, and the rollback or undo of that rename
# os.replace()s the file with that copy: a new inode. A DSD/WavPack library hard-linked to
# the download client's seeding copy now stores those tracks twice (and the job folder
# holds a copy of every moved track until pruned).
from test_fileops_review8 import make_dsf  # noqa: E402


class R9_07_RenameUndoRewritesWholeFileBackups(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")
        self.album = self.lib / "Artist" / "2001 - Album"
        self.f1 = make_dsf(self.album / "01 a.dsf", 1)
        self.f2 = make_dsf(self.album / "02 b.dsf", 2)
        self.t = [target(1, "First"), target(2, "Second")]
        for f, t in ((self.f1, self.t[0]), (self.f2, self.t[1])):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        self.seed = self.tmp / "downloads" / "01 a.dsf"
        self.seed.parent.mkdir()
        os.link(self.f1, self.seed)

    def plan(self):
        p = pl.build([self.proposal(targets=self.t)], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual([(fe["op"], fe["changes"]) for fe in p["albums"][0]["files"]], [("move", {})] * 2)
        return p

    def test_rollback(self):
        p = self.plan()
        d2 = Path(p["albums"][0]["files"][1]["dst"])
        d2.parent.mkdir(parents=True)
        d2.write_bytes(b"squatter")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1}, res)
        self.assertTrue(os.path.samefile(self.f1, self.seed), "rollback rewrote a file it had only renamed")

    def test_undo(self):
        p = self.plan()
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertTrue(os.path.samefile(self.f1, self.seed), "undo rewrote a file the job had only renamed")


# ---------------------------------------------------------------------------------------
# R9-08. executor._clear_undo_leftovers (executor.py:1182-1203) looks for the staging files
# of an undo or rollback cut short under the NEW name only (".tm-undo-tags-<hash>.part.flac");
# recover's stages() also tries _old_stage_names, but this does not. A rollback or undo
# interrupted under 10afdc9 left ".tm-undo-tags-<hash>.part" (a full copy of the track,
# hidden): neither `tagmender recover` nor running the undo again removes it any more.
class R9_08_OldUndoLeftoversStay(Base):
    def test_x(self):
        import hashlib
        h = hashlib.sha1(self.f1.name.encode()).hexdigest()[:12]
        left = self.album / f".tm-undo-tags-{h}.part"            # how 10afdc9 named it
        shutil.copy2(self.f1, left)
        files = {(0, 0): {"src": str(self.f1), "dst": str(self.f1), "op": "tag", "state": "committed"}}
        executor._clear_undo_leftovers(files, {})
        self.assertFalse(left.exists(), "the interrupted undo's copy is left in the album folder")


if __name__ == "__main__":
    unittest.main(verbosity=2)
