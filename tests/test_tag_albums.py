# Tests for tag_albums.py, with fake_picard.py standing in for Picard.
# Run: python3 test_tag_albums.py
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import mutagen  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
import tag_albums as ta  # noqa: E402

MBID = "641b0578-7135-4567-a728-6ef74f322ccb"
OTHER = "11111111-2222-4333-8444-555555555555"


def audio(p, secs=1, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + secs}:duration={secs}",
                    "-ar", "48000", *meta, str(p), "-y"], check=True)


def all_tags(p):
    f = mutagen.File(p)
    tags = {str(k): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in (f.tags or {}).items()}
    pics = [pic.data for pic in f.pictures] if isinstance(f, FLAC) else []
    return tags, pics


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "ambient_music"
        self.album = self.root / "Old Artist" / "1989 - Old Album"
        self.out = self.tmp / "tagging"
        self.db = self.tmp / "match_state.db"
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        db.commit()
        self.dbc = db

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_result(self, folder, status="auto", source="mb", mbid=MBID):
        o = {"path": str(folder), "status": status, "best": {"source": source, "id": mbid, "artist": "A",
                                                             "title": "T", "confidence": 100.0}}
        self.dbc.execute("INSERT OR REPLACE INTO results VALUES (?,?)", (str(folder), json.dumps(o)))
        self.dbc.commit()

    def make_album(self):
        audio(self.album / "001.TL.flac", 1, title="The Lorelei", artist="Old Artist", comment="keep me")
        audio(self.album / "002.O.mp3", 2, title="Overture", artist="Old Artist")
        audio(self.album / "003.X.m4a", 3, title="Third", artist="Old Artist")
        audio(self.album / "004.Y.opus", 4, title="Fourth", artist="Old Artist")
        f = FLAC(self.album / "001.TL.flac")
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", b"\xff\xd8\xff old cover"
        f.add_picture(pic)
        f.save()
        (self.album / "cover.jpg").write_bytes(b"jpg")
        (self.album / "Covers").mkdir()
        (self.album / "Covers" / "p0001.jpg").write_bytes(b"scan")
        (self.album / "rip.log").write_text("log")
        self.add_result(self.album)

    def run_tool(self, *extra, skip=""):
        env = dict(os.environ, FAKE_PICARD_ROOT=str(self.root), FAKE_PICARD_SKIP=skip,
                   TAGMENDER_SKIP_PICARD_CHECK="1")
        cmd = [sys.executable, str(HERE / "tag_albums.py"), "--matches-db", str(self.db), "--out-dir",
               str(self.out), "--library-root", str(self.root),
               "--picard", f"{sys.executable} {Path(__file__).resolve().parent / 'fake_picard.py'}", *extra]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def run_id(self):
        return sorted(p.name for p in self.out.glob("run-*"))[-1][4:]


class Seeding(Base):
    def test_release_id_written_and_read_back_in_every_format(self):
        self.make_album()
        for p in ta.audio_files(self.album):
            ta.write_albumid(p, MBID)
            self.assertEqual(ta.read_ids(p)["albumid"], MBID, p.name)
            self.assertEqual(ta.read_ids(p)["trackid"], "", p.name)

    def test_audio_key_survives_a_retag(self):
        self.make_album()
        p = self.album / "001.TL.flac"
        k = ta.audio_key(p)
        ta.write_albumid(p, MBID)
        self.assertEqual(ta.audio_key(p), k)


class Selection(Base):
    def test_only_confident_musicbrainz_matches_with_existing_folders(self):
        self.make_album()
        audio(self.root / "B" / "2000 - Review" / "01.flac", 1)
        self.add_result(self.root / "B" / "2000 - Review", status="review")
        audio(self.root / "C" / "2001 - Discogs" / "01.flac", 1)
        self.add_result(self.root / "C" / "2001 - Discogs", source="discogs", mbid="123")
        self.add_result(self.root / "D" / "gone")
        albums, skipped = ta.select_albums(self.db)
        self.assertEqual([a["path"] for a in albums], [str(self.album)])
        self.assertIn("folder no longer exists", dict(skipped)[str(self.root / "D" / "gone")])

    def test_album_carrying_another_release_is_left_alone(self):
        self.make_album()
        ta.write_albumid(self.album / "001.TL.flac", OTHER)
        albums, skipped = ta.select_albums(self.db)
        self.assertEqual(albums, [])
        self.assertIn("another release", skipped[0][1])


class Artwork(Base):
    """Zoviet France - Norsch / Eostre: scans in subfolders were left in the old folder, and
    loose scans were dropped next to the music."""
    def test_scans_end_up_under_covers_and_nothing_is_left_behind(self):
        self.make_album()
        (self.album / "1983 LP release").mkdir()
        (self.album / "1983 LP release" / "01. front.jpg").write_bytes(b"lp front")
        (self.album / "01. zoviet - Eostre [front].jpg").write_bytes(b"loose scan")
        rc, out = self.run_tool("--apply")
        self.assertEqual(rc, 0, out)
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertFalse(self.album.exists(), "old folder must not be left with scans in it")
        self.assertEqual((new / "covers" / "1983 LP release" / "01. front.jpg").read_bytes(), b"lp front")
        self.assertEqual((new / "covers" / "p0001.jpg").read_bytes(), b"scan")    # Covers/ merged into covers/
        self.assertEqual((new / "covers" / "01. zoviet - Eostre [front].jpg").read_bytes(), b"loose scan")
        self.assertTrue((new / "cover.jpg").exists())                 # Picard/players read this one
        self.assertTrue((new / "rip.log").exists())                   # not artwork: stays put

    def test_leftover_cover_and_log_go_next_to_the_music_not_into_covers(self):
        # Eostre: Picard left Cover.jpg/folder.jpg and the log behind; they must sit with the music
        self.make_album()
        os.environ["FAKE_PICARD_LEAVE"] = "cover.jpg,rip.log"
        try:
            rc, out = self.run_tool("--apply")
        finally:
            os.environ.pop("FAKE_PICARD_LEAVE")
        self.assertEqual(rc, 0, out)
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertTrue((new / "cover.jpg").exists())
        self.assertTrue((new / "rip.log").exists())
        self.assertFalse((new / "covers" / "cover.jpg").exists())
        self.assertFalse(self.album.exists())

    def test_scan_folder_picard_moved_goes_under_covers(self):
        # Shadow, Thief of the Sun: Picard moved Scans/ (it matches the pattern) next to the music
        self.make_album()
        (self.album / "Scans").mkdir()
        (self.album / "Scans" / "back.jpg").write_bytes(b"back")
        os.environ["FAKE_PICARD_MOVE_DIRS"] = "Scans"
        try:
            rc, out = self.run_tool("--apply")
        finally:
            os.environ.pop("FAKE_PICARD_MOVE_DIRS")
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertEqual((new / "covers" / "Scans" / "back.jpg").read_bytes(), b"back", out)
        self.assertFalse((new / "Scans").exists())

    def test_cover_already_misplaced_in_covers_is_moved_back(self):
        new = self.root / "A" / "2000 - B"
        (new / "covers").mkdir(parents=True)
        (new / "covers" / "Cover.jpg").write_bytes(b"c")
        (new / "covers" / "folder.jpg").write_bytes(b"f")
        moves = ta.tidy_artwork(str(self.root / "gone"), str(new), [])
        self.assertTrue((new / "Cover.jpg").exists() and (new / "folder.jpg").exists())
        self.assertEqual(len(moves), 2)

    def test_every_file_from_the_source_ends_up_in_the_target(self):
        self.make_album()
        (self.album / "deep" / "deeper").mkdir(parents=True)
        (self.album / "deep" / "deeper" / "x.txt").write_text("x")
        (self.album / "notes.nfo").write_text("n")
        names = sorted(p.name for p in self.album.rglob("*") if p.is_file())
        os.environ["FAKE_PICARD_LEAVE"] = "notes.nfo"
        try:
            self.run_tool("--apply")
        finally:
            os.environ.pop("FAKE_PICARD_LEAVE")
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertFalse(self.album.exists())
        got = sorted(p.name for p in new.rglob("*") if p.is_file())
        self.assertEqual(len(got), len(names))

    def test_undo_puts_the_scans_back_where_they_were(self):
        self.make_album()
        (self.album / "1983 LP release").mkdir()
        (self.album / "1983 LP release" / "01. front.jpg").write_bytes(b"lp front")
        layout = sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*") if p.is_file())
        self.run_tool("--apply")
        rc, out = self.run_tool("--undo", self.run_id())
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*") if p.is_file()), layout)
        self.assertFalse((self.root / "New Artist").exists())

    def test_tidy_an_earlier_run_then_undo(self):
        self.make_album()
        (self.album / "original LP").mkdir()
        (self.album / "original LP" / "01. front.jpg").write_bytes(b"lp")
        layout = sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*") if p.is_file())
        self.run_tool("--apply", "--no-tidy")                 # like the run made before tidying existed
        self.assertTrue((self.album / "original LP" / "01. front.jpg").exists())
        rc, out = self.run_tool("--tidy", self.run_id())
        self.assertEqual(rc, 0, out)
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertTrue((new / "covers" / "original LP" / "01. front.jpg").exists())
        self.assertFalse(self.album.exists())
        self.run_tool("--undo", self.run_id())
        self.assertEqual(sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*") if p.is_file()), layout)

    def test_tidy_finds_the_albums_of_an_interrupted_run(self):
        self.make_album()
        (self.album / "original LP").mkdir()
        (self.album / "original LP" / "01. front.jpg").write_bytes(b"lp")
        self.run_tool("--apply", "--no-tidy")
        jpath = self.out / f"run-{self.run_id()}" / "journal.json"
        j = json.loads(jpath.read_text())
        for r in j[0]["files"]:
            r.pop("new_path", None)                       # as if stopped before checking
        jpath.write_text(json.dumps(j))
        rc, out = self.run_tool("--tidy", self.run_id())
        self.assertEqual(rc, 0, out)
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertTrue((new / "covers" / "original LP" / "01. front.jpg").exists(), out)
        rc, out = self.run_tool("--undo", self.run_id())
        self.assertTrue((self.album / "original LP" / "01. front.jpg").exists(), out)
        self.assertEqual(len(ta.audio_files(self.album)), 4)

    def test_folders_changed_during_the_run_are_found_without_walking_everything(self):
        old = self.root / "Untouched" / "2000 - Old"
        old.mkdir(parents=True)
        t0 = __import__("time").time()
        os.utime(old, (t0 - 1000, t0 - 1000))
        os.utime(old.parent, (t0 - 1000, t0 - 1000))
        new = self.root / "Fresh Artist" / "2001 - New" / "CD1"
        new.mkdir(parents=True)
        found = {str(p) for p in ta.changed_dirs(self.root, t0 - 5)}
        self.assertIn(str(new), found)
        self.assertNotIn(str(old), found)


class Runs(Base):
    def test_dry_run_changes_nothing(self):
        self.make_album()
        before = {p.name: all_tags(p) for p in ta.audio_files(self.album)}
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("Dry run", out)
        self.assertEqual({p.name: all_tags(p) for p in ta.audio_files(self.album)}, before)
        self.assertTrue((self.out / "tag_plan.csv").exists())

    def test_apply_tags_with_picard_and_reports_where_it_went(self):
        self.make_album()
        rc, out = self.run_tool("--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("'tagged': 1", out)
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        self.assertEqual(len(ta.audio_files(new)), 4)
        self.assertFalse(self.album.exists())
        res = list((self.out / f"run-{self.run_id()}" / "results.csv").read_text().splitlines())
        self.assertIn(str(new), res[1])

    def test_undo_puts_everything_back_exactly(self):
        self.make_album()
        before = {p.name: all_tags(p) for p in ta.audio_files(self.album)}
        extras = sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*")
                        if p.is_file() and p.suffix != ".flac" and p.suffix not in ta.AUDIO_EXT)
        self.run_tool("--apply")
        rc, out = self.run_tool("--undo", self.run_id())
        self.assertEqual(rc, 0, out)
        after = {p.name: all_tags(p) for p in ta.audio_files(self.album)}
        self.assertEqual(after, before)                      # tags, pictures, names, folder
        self.assertEqual(sorted(str(p.relative_to(self.album)) for p in self.album.rglob("*")
                                if p.is_file() and p.suffix not in ta.AUDIO_EXT), extras)
        self.assertFalse((self.root / "New Artist").exists())

    def test_two_albums_in_one_batch_tag_and_undo(self):
        self.make_album()
        second = self.root / "Second Artist" / "2001 - Second Album"
        audio(second / "01.flac", 5, title="One")
        audio(second / "02.flac", 6, title="Two")
        self.add_result(second, mbid=OTHER)
        before = {p: all_tags(p) for p in ta.audio_files(self.album) + ta.audio_files(second)}
        rc, out = self.run_tool("--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("'tagged': 2", out)
        rc, out = self.run_tool("--undo", self.run_id())
        self.assertEqual(rc, 0, out)
        self.assertEqual({p: all_tags(p) for p in ta.audio_files(self.album) + ta.audio_files(second)}, before)

    def test_album_picard_did_not_save_is_reported_and_undo_removes_the_seed(self):
        self.make_album()
        before = {p.name: all_tags(p) for p in ta.audio_files(self.album)}
        rc, out = self.run_tool("--apply", skip="Old Album")
        self.assertIn("'not saved': 1", out)
        self.run_tool("--undo", self.run_id())
        self.assertEqual({p.name: all_tags(p) for p in ta.audio_files(self.album)}, before)

    def test_refuses_when_picard_is_open(self):
        self.make_album()
        orig = ta.picard_running
        ta.picard_running = lambda: "123 /app/bin/picard"
        try:
            sys.argv = ["tag_albums.py", "--matches-db", str(self.db), "--out-dir", str(self.out),
                        "--library-root", str(self.root), "--apply"]
            with self.assertRaises(SystemExit) as cm:
                ta.main()
            self.assertIn("Picard is open", str(cm.exception))
        finally:
            ta.picard_running = orig
        self.assertEqual(ta.read_ids(self.album / "001.TL.flac")["albumid"], "")

    def test_picard_process_detection(self):
        yes = ["bwrap --args 42 /app/bin/picard", "/usr/bin/python3 /usr/bin/picard -e LOAD x",
               "picard", "/usr/bin/flatpak run --branch=stable org.musicbrainz.Picard"]
        no = ["code /home/hp/Documents/Github/picard-discogs-enrichment",
              "python3 tag_albums.py --picard flatpak run org.musicbrainz.Picard", "bash", "vim picard.txt"]
        for c in yes:
            self.assertTrue(ta.is_picard_process(c), c)
        for c in no:
            self.assertFalse(ta.is_picard_process(c), c)

    def test_session_restore_is_refused(self):
        ini = self.tmp / "Picard.ini"
        ini.write_text("[setting]\nsession_load_last_on_startup=true\n")
        self.assertTrue(ta.session_restore_on(ini))
        ini.write_text("[setting]\nmove_files_to=/x\n")
        self.assertFalse(ta.session_restore_on(ini))

    def test_refuses_albums_picard_would_move_into_another_library(self):
        extreme = self.tmp / "extreme_music" / "Band" / "1990 - Album"
        audio(extreme / "01.flac", 7, title="One")
        self.add_result(extreme)
        ini = self.tmp / "Picard.ini"
        ini.write_text(f"[setting]\nmove_files=true\nmove_files_to={self.root}\n")
        rc, out = self.run_tool("--apply", "--picard-ini", str(ini))
        self.assertNotEqual(rc, 0)
        self.assertIn("another library", out)
        self.assertEqual(ta.read_ids(extreme / "01.flac")["albumid"], "")   # nothing touched
        self.assertTrue(extreme.exists())
        rc, out = self.run_tool("--picard-ini", str(ini))                   # dry run warns
        self.assertIn("WARNING", out)
        ini.write_text(f"[setting]\nmove_files=true\nmove_files_to={self.tmp / 'extreme_music'}\n")
        self.assertIsNone(ta.outside_move_target([{"path": str(extreme)}], ini))
        ini.write_text(f"[setting]\nmove_files=false\nmove_files_to={self.root}\n")
        self.assertIsNone(ta.outside_move_target([{"path": str(extreme)}], ini))

    def test_no_vox_editions_are_left_alone(self):
        novox = self.root / "Band" / "2015 - Coma Ecliptic [No Vox]"
        audio(novox / "01.flac", 8, title="One")
        self.add_result(novox)
        tagged_novox = self.root / "Band2" / "2001 - Plain Folder"
        audio(tagged_novox / "01.flac", 9, title="One", album="Colors [no vox]")
        self.add_result(tagged_novox, mbid=OTHER)
        albums, skipped = ta.select_albums(self.db)
        self.assertEqual(albums, [])
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all("[no vox]" in why for _p, why in skipped))
        self.assertTrue(ta.is_no_vox("/m/Meshuggah/noVox/2025 - Immutable"))
        self.assertFalse(ta.is_no_vox("/m/Band/2001 - Novocaine"))

    def test_tagged_albums_are_marked_resolved(self):
        self.make_album()
        rc, out = self.run_tool("--apply")
        new = self.root / "New Artist" / "1999 - 1989 - Old Album (tagged)"
        for p in ta.audio_files(new):
            self.assertEqual(ta.read_tagmender(p), ("Resolved", ""), p)

    def test_review_albums_marked_unresolved_with_reason_and_undo(self):
        self.make_album()
        self.add_result(self.album, status="review")
        self.dbc.execute("UPDATE results SET result=json_set(result,'$.why',json('[\"track count differs\"]'))")
        self.dbc.commit()
        before = {p.name: all_tags(p) for p in ta.audio_files(self.album)}
        rc, out = self.run_tool("--mark-unresolved")                      # dry run
        self.assertIn("1 album(s) to mark", out)
        self.assertEqual({p.name: all_tags(p) for p in ta.audio_files(self.album)}, before)
        rc, out = self.run_tool("--mark-unresolved", "--apply")
        self.assertEqual(rc, 0, out)
        for p in ta.audio_files(self.album):
            self.assertEqual(ta.read_tagmender(p), ("Unresolved", "review: track count differs"), p)
        rc, out = self.run_tool("--mark-unresolved", "--apply")             # already marked
        self.assertIn("0 album(s) to mark", out)
        rc, out = self.run_tool("--undo", sorted(p.name for p in self.out.glob("run-*"))[0][4:])
        self.assertEqual({p.name: all_tags(p) for p in ta.audio_files(self.album)}, before)

    def test_picard_must_preserve_the_tag(self):
        ini = self.tmp / "Picard.ini"
        ini.write_text("[setting]\nclear_existing_tags=true\npreserved_tags=comment, totaltracks\n")
        self.assertFalse(ta.picard_preserves_tagmender(ini))
        ini.write_text("[setting]\nclear_existing_tags=true\npreserved_tags=comment, tagmender, tagmender_reason\n")
        self.assertTrue(ta.picard_preserves_tagmender(ini))

    def test_picard_commands_load_settle_save_quit(self):
        cmds = ta.picard_commands(["/m/A B/1999 - X"], 10)
        self.assertEqual(cmds[:3], ["-e", "LOAD", "/m/A B/1999 - X"])
        joined = " ".join(cmds)
        self.assertLess(joined.index("PAUSE"), joined.index("SAVE_MATCHED"))
        self.assertTrue(joined.endswith("QUIT FORCE"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
