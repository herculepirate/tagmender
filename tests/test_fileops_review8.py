# Eighth review of TagMender's file operations. Each test asserts the correct behaviour
# and fails at 10afdc9.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import errno
import os
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from mutagen.id3 import ID3, TIT2  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import Lossy, tree, work  # noqa: E402


def groups(prof, path):
    return eng_mod.Engine.albums_in(type("E", (), {"profile": prof})(), path)


def id3_in_flac(path):
    """An ID3v2 tag in front of a FLAC file, as EAC writes it with "Add ID3 tag" ticked."""
    t = ID3()
    t.add(TIT2(encoding=3, text=["from EAC"]))
    t.save(str(path))
    assert Path(path).read_bytes()[:3] == b"ID3"


def make_dsf(path, seed=1, blocks=2):
    """A minimal valid stereo DSD64 .dsf file (no ID3 chunk)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ch, bs = 2, 4096
    data = bytes((i * 7 + seed * 13) & 0xFF for i in range(bs * ch * blocks))
    fmt = b"fmt " + struct.pack("<QIIIIIIQII", 52, 1, 0, 2, ch, 2822400, 1, bs * 8 * blocks, bs, 0)
    dat = b"data" + struct.pack("<Q", 12 + len(data)) + data
    total = 28 + len(fmt) + len(dat)
    path.write_bytes(b"DSD " + struct.pack("<QQQ", 28, total, 0) + fmt + dat)
    return path


# ---------------------------------------------------------------------------------------
# R8-01. FLAC files with an ID3v2 tag in front (EAC with an external FLAC encoder and
# "Add ID3 tag" ticked - a classic EAC rip). The planner reads "01 a.flac" as FLAC (mutagen
# scores the ".flac" extension), but every write goes to a staging copy named
# ".tm-<job>-<hash>.part" (executor._stage_name, executor.py:338-342): without the
# extension mutagen takes the "ID3" header for an MP3 and fails "can't sync to MPEG frame".
#   - every tag write to such an album fails, run after run (the plan shows the changes);
#   - undo of a move that wrote no tags (a rename) restores the tag snapshot through a
#     ".tm-undo-tags-*.part" copy too (executor._restore): it fails the same way, every
#     time - the job can never be undone ("undone with problems" for ever).
class R8_01_Id3InFlac(Base):
    def test_tags_are_written(self):
        id3_in_flac(self.f1)
        p = pl.build([self.proposal()], self.profile())
        self.assertEqual(p["albums"][0]["status"], "ready")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])

    def test_undo_of_a_rename_move(self):
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((self.f1, t1), (self.f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        id3_in_flac(self.f1)
        before = self.f1.read_bytes()
        p = pl.build([self.proposal(targets=[t1, t2])], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual([(fe["op"], fe["changes"]) for fe in p["albums"][0]["files"]], [("move", {})] * 2)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = executor.undo(p["job"])
        if any(o.startswith("PROBLEM") for o in out):
            out += ["-- again --"] + executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(self.f1.read_bytes(), before)


# ---------------------------------------------------------------------------------------
# R8-02. DSF files that already carry tags (the SACD-ISO extractors, foobar2000, Picard all
# tag DSF) can never be changed, renamed or moved: executor._backup (executor.py:432) takes
# a tag snapshot for every "id3" family file except WAV/AIFF, and tag_albums.backup_tags
# saves the file's _DSFID3 tag to an empty temp file - mutagen's DSF save needs a DSF file
# there and raises "DSF chunk truncated". The album fails for op "tag" AND for a plain move.
# An untagged DSF works once, but undo (or the job itself) leaves an ID3 chunk in it, so
# every later job on that file fails the same way.
class R8_02_TaggedDsf(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")
        self.al = self.lib / "Artist" / "2001 - Album"
        self.d1 = make_dsf(self.al / "01 a.dsf", 1)
        self.d2 = make_dsf(self.al / "02 b.dsf", 2)
        for f, t in ((self.d1, "a"), (self.d2, "b")):
            tagio.write(f, {"title": [t], "album": ["Old"]})

    def prop(self, **kw):
        return self.proposal(source_dir=str(self.al), files=[str(self.d1), str(self.d2)], **kw)

    def test_tag_in_place(self):
        res = executor.Executor(pl.build([self.prop()], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.d1)["album"], ["Real Album"])

    def test_move(self):
        res = executor.Executor(pl.build([self.prop()], self.profile(operation="move",
                                                                       destination=str(self.dest)))).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertFalse(self.d1.exists())


# ---------------------------------------------------------------------------------------
# R8-03. A disc set whose discs are tagged with different album titles ("Album (Disc 1)",
# "Album (Disc 2)" - what freedb gives EAC for every multi-disc rip) in Album/CD1, Album/CD2
# with Album/cover.jpg and Album/Scans/. engine.albums_in makes two groups with the SAME
# source folder (Album: the disc folders belong to it), and plan._companions gives each of
# them the album folder's own files: both plan to move cover.jpg and Scans/*. Disc 1 moves
# them into ITS folder (".../Disc 01/cover.jpg", ".../Disc 01/Scans/"), disc 2 is refused
# ("cover.jpg is gone since the plan was made") and stays behind in the old folder.
class R8_03_DiscSetWithPerDiscAlbumTitles(Base):
    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        al = self.lib / "Artist" / "Album"
        audio(al / "CD1" / "01 a.flac", 440, album="Album (Disc 1)", artist="Artist", title="a")
        audio(al / "CD2" / "01 b.flac", 550, album="Album (Disc 2)", artist="Artist", title="b")
        (al / "cover.jpg").write_bytes(b"front")
        (al / "Scans").mkdir()
        (al / "Scans" / "back.jpg").write_bytes(b"back")
        prof = self.profile(operation="move", destination=str(self.lib))
        props = []
        for n, g in enumerate(groups(prof, self.lib), 1):
            props.append(pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                     targets=[target(1, f"T{n}", discnumber=[str(n)], totaldiscs=["2"],
                                                     totaltracks=["1"])],
                                     provider="discogs", release_id="777", confidence=97,
                                     release={"album": ["Real Album"]}))
        p = pl.build(props, prof)
        claimed = [c["src"] for a in p["albums"] for c in a["companions"]]
        self.assertEqual(len(claimed), len(set(claimed)), f"one file planned by two albums: {sorted(claimed)}")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": len(p["albums"])}, res["albums"])
        home = self.lib / "Real Artist" / "2001 - Real Album"
        self.assertFalse((home / "Disc 01" / "cover.jpg").exists(), "the album's cover filed inside disc 1")


# ---------------------------------------------------------------------------------------
# R8-04. A compilation without an ALBUMARTIST tag (track artists only - the usual freedb /
# EAC rip of a Various Artists CD) is split into one single-track group per track:
# exclusions.album_key falls back to the TRACK artist, so engine.albums_in sees a "mixed
# folder" of 3 albums. Identification then sees one track at a time (the rest excluded),
# never the album; with a fingerprint each track is planned as a single and a move files
# them into "Blur/1999 - Hits 1999/", "Oasis/1999 - Hits 1999/", "Pulp/1999 - Hits 1999/".
import test_engine as te  # noqa: E402


class R8_04_CompilationWithoutAlbumArtist(te.Base):
    ARTISTS = ["Blur", "Oasis", "Pulp"]

    def comp(self):
        d = self.lib / "Various" / "1999 - Hits 1999"
        d.mkdir(parents=True, exist_ok=True)
        for i, a in enumerate(self.ARTISTS):
            meta = ["-metadata", f"title=T{i}", "-metadata", "album=Hits 1999", "-metadata", f"artist={a}",
                    "-metadata", f"track={i + 1}", "-metadata", "date=1999"]
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *meta, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return d

    def test_one_album(self):
        self.comp()
        eng = self.engine(te.FakeHttp(mb=[te.mb_release("mb-h", "Various Artists", "Hits 1999")]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([len(g.files) for g in gs], [3], "the compilation is split into singles")

    def test_not_scattered_over_artist_folders(self):
        self.comp()
        http = te.FakeHttp(mb=[te.mb_release("mb-h", "Various Artists", "Hits 1999")])
        real = http.get_json
        arts = self.ARTISTS

        def get(url, host):
            if host == "acoustid":
                a = arts[int(url.split("duration=")[1].split("&")[0]) // 10 - 1]
                return {"results": [{"id": "ac-" + a, "score": 0.97, "recordings": [
                    {"id": "rec-" + a, "title": "T", "artists": [{"id": "ar-" + a, "name": a}]}]}]}
            return real(url, host)
        http.get_json = get
        dest = self.tmp / "dest"
        dest.mkdir()
        with mock.patch.object(ma, "fingerprint_ready", return_value=True), \
                mock.patch.object(ma, "fingerprint",
                                  side_effect=lambda p, db: {"duration": 10 * int(Path(p).stem), "fingerprint": "AQ"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"):
            eng = self.engine(http, operation="move", destination=str(dest), sources=[{"path": str(self.lib)}])
            p = eng.plan(self.lib)
        folders = {str(Path(fe["dst"]).parent.relative_to(dest)) for a in p["albums"] for fe in a["files"]
                   if fe["op"] == "move"}
        self.assertLessEqual(len(folders), 1, f"one compilation filed into {sorted(folders)}")


# ---------------------------------------------------------------------------------------
# R8-05. The R7-02 exemption check (plan.py:689-690) compares the RAW album title with the
# SANITISED folder name: a one-track release whose title holds a character the file-name
# rules change (":" "?" "*" '"' "<" ">" "|" "/", leading dots: "Is This It?", "Star Wars:
# ...", "...Ready for It?") counts as a "single" in a shared folder, is never checked for
# a different release there, and is filed INTO the album's folder as "01.ITI ~xxxx.flac".
class R8_05_OneTrackReleaseTitleWithQuestionMark(Base):
    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        home = self.dest / "Band" / "2001 - Is This It_"
        audio(home / "01.ITI.flac", 500, album="Is This It?", albumartist="Band", title="Is This It?",
              musicbrainz_albumid="album-release")
        audio(home / "02.SK.flac", 510, album="Is This It?", albumartist="Band", title="Some Kind",
              musicbrainz_albumid="album-release")
        s = audio(self.lib / "inbox" / "single" / "01.flac", 700)
        t = target(1, "Is This It?", album="Is This It?", artist="Band", totaltracks=["1"], date=["2001-06-23"],
                   musicbrainz_albumid=["single-release"], discogs_release_id=[])
        prop = self.proposal(source_dir=str(s.parent), files=[str(s)], targets=[t], provider="musicbrainz",
                             release_id="single-release")
        p = pl.build([prop], self.profile(operation="move", destination=str(self.dest)))
        dst = Path(p["albums"][0]["files"][0]["dst"])
        self.assertNotEqual(dst.parent, home, f"the one-track release is filed into the album: {dst}")


# ---------------------------------------------------------------------------------------
# R8-06. Copy re-run after an album-folder collision went away. Run 1 found another release
# in "Real Artist/2001 - Real Album" of the lossy library and rightly copied the album to
# "2001 - Real Album ~xxxx". When that other release leaves (deleted from both libraries,
# re-tagged, renamed...), the next run's template folder is the plain one again;
# plan._earlier_copy (plan.py:660-674) looks only there (plain or suffixed FILE name) and
# the whole album is copied a SECOND time; the lossy library holds it twice, for good.
class R8_06_CopyAfterFolderClashGone(Lossy):
    def test_x(self):
        prof = self.lossy()
        plain = self.dest / "Real Artist" / "2001 - Real Album"
        other = audio(plain / "01.X.mp3", 300, title="X")
        tagio.write(other, {"discogs_release_id": ["999"], "album": ["Real Album Vol 2"]})
        res = executor.Executor(pl.build([self.proposal()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((plain / "01.F.mp3").exists())
        shutil.rmtree(plain)
        p2 = pl.build([self.proposal()], prof)
        self.assertEqual(work(p2), [], "the second run copies the album again")
        executor.Executor(p2).run()
        mp3 = [x for x in tree(self.dest) if x.endswith(".mp3")]
        self.assertEqual(len(mp3), 2, mp3)


# ---------------------------------------------------------------------------------------
# R8-07. %_albumartists_sorted% (naming.py:132-137) without an "albumartists" tag splits the
# album artist at "," "&" " and " and sorts the pieces. Discogs-only tags (engine
# discogs_only_tags; the plugin's extract_discogs_fields sets albumartist only) do not
# carry "albumartists", so a band name is scrambled into another artist folder:
#   "Earth, Wind & Fire" -> "Earth & Fire & Wind/",  "Simon & Garfunkel" -> "Garfunkel & Simon/"
# while the band's MusicBrainz-tagged albums (mb_tags sets albumartists) go to
# "Earth, Wind & Fire/": one artist's albums end up split over two folders.
class R8_07_BandNameScrambled(Base):
    def test_discogs_only_album(self):
        dm = ma.load_plugin(str(__import__("companions").discogs_dir()))
        import picard_config
        det = {"id": 1, "title": "That's the Way of the World", "year": 1975, "released": "1975-03-15",
               "artists": [{"name": "Earth, Wind & Fire", "anv": "", "join": "", "id": 5}],
               "tracklist": [{"position": "A1", "title": "Shining Star", "duration": "0:01", "type_": "track"},
                             {"position": "A2", "title": "Way", "duration": "0:01", "type_": "track"}],
               "formats": [{"name": "Vinyl", "qty": "1"}], "labels": [{"name": "Columbia", "catno": "PC 33280"}]}
        a, t, _n = eng_mod.discogs_parts(det, dm, None, picard_config.enrichment())
        tags = eng_mod.discogs_only_tags(a, t)
        p = pl.build([self.proposal(targets=tags)], self.profile(operation="move", destination=str(self.dest)))
        dst = Path(p["albums"][0]["files"][0]["dst"])
        self.assertEqual(dst.relative_to(self.dest).parts[0], "Earth, Wind & Fire", dst)


# ---------------------------------------------------------------------------------------
# R8-08. Executor.album (executor.py:575-578) checks every file before the album begins,
# outside the album's try block, and catches only FileNotFoundError. Any other error from
# os.stat - ESTALE (an NFS export of an Unraid share whose mover just moved the file),
# EACCES (a folder whose permissions changed since the plan), EIO, ENOTCONN (a dropped
# CIFS/FUSE mount) - escapes Executor.run: the albums after it are never processed, no
# "end" is journalled, and the job is left "interrupted" (every new job refuses to start
# until `tagmender recover`). It should be that album's conflict, as a missing file is.
class R8_08_StatErrorAbortsTheJob(Base):
    def test_x(self):
        other = self.lib / "Other" / "Album2"
        g = audio(other / "01.flac", 700, album="X", title="x")
        p = pl.build([self.proposal(),
                      self.proposal(source_dir=str(other), files=[str(g)],
                                    targets=[target(1, "Gee", album="Second Album")])], self.profile())
        real = os.stat
        bad = str(self.f1)

        def stat(path, *a, **k):
            if str(path) == bad:
                raise OSError(errno.ESTALE, "Stale file handle", bad)
            return real(path, *a, **k)
        with mock.patch.object(os, "stat", stat):
            try:
                res = executor.Executor(p).run()
            except OSError as exc:
                res = f"the job raised {exc!r}"
        self.assertIsInstance(res, dict, res)
        self.assertEqual(res["albums"][1]["status"], "done", res)
        self.assertEqual(executor.pending_jobs(), [])


# ---------------------------------------------------------------------------------------
# R8-09. Rollback and undo of a move that wrote nothing (a same-filesystem rename, which
# keeps the inode) put the file back by rename and then ALWAYS rewrite it from the tag
# snapshot (executor._undo_step, executor.py:1028-1059 -> _restore: copy to a staging
# file, os.replace). Nothing needed restoring, yet the file gets a new inode: a library
# hard-linked with the download client's seeding copy (the *arr import the code already
# caters for) now stores every such track twice. One failing track in a move job (a
# squatter at its destination) is enough: every track renamed before it is duplicated.
class R8_09_RollbackOfRenameBreaksHardLinks(Base):
    def setUp(self):
        super().setUp()
        self.t = [target(1, "First"), target(2, "Second")]
        for f, t in ((self.f1, self.t[0]), (self.f2, self.t[1])):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        self.seed = self.tmp / "downloads" / "01 a.flac"
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
        self.assertEqual(res["counts"], {"failed": 1})
        self.assertTrue(os.path.samefile(self.f1, self.seed), "rollback rewrote a file it had only renamed")

    def test_undo(self):
        p = self.plan()
        executor.Executor(p).run()
        executor.undo(p["job"])
        self.assertTrue(os.path.samefile(self.f1, self.seed), "undo rewrote a file the job had only renamed")


if __name__ == "__main__":
    unittest.main()
