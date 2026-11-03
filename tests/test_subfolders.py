# Tests for the album's covers and misc folders (profile "subfolders", tidy.py, plan.Planner's
# companion step): pictures into covers/, rip logs and notes into misc/, the front cover, cue
# sheets and playlists left beside the audio, a disc set's own folders, nested Scans/, a name
# already taken, planning again (nothing new), undo, the matcher still reading the moved files,
# a move profile, the option off, and a version split. Generated audio, mocked answers.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own helpers)
from test_split_versions import Folders  # noqa: E402

import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tidy  # noqa: E402

ON = {"covers": True, "misc": True}
JPG = b"\xff\xd8\xff\xe0 picture "
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def two_discs(rid, artist, title, lens=((10000, 20000), (30000, 40000))):
    r = mb_release(rid, artist, title)
    r["media"] = [{"format": "CD", "position": n + 1, "tracks": [
        {"id": f"{rid}-d{n}t{i}", "position": i + 1, "title": f"D{n + 1}T{i}", "length": ms,
         "recording": {"id": f"{rid}-d{n}r{i}", "title": f"D{n + 1}T{i}"}} for i, ms in enumerate(disc)]}
        for n, disc in enumerate(lens)]
    return r


class Tidy(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(pl, "mounted", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def single(self):
        d = self.album("Band A", "Album A")
        (d / "cover.jpg").write_bytes(JPG + b"front")
        (d / "back.jpg").write_bytes(JPG + b"back")
        (d / "booklet.pdf").write_bytes(b"%PDF-1.4 booklet")
        (d / "Album A.log").write_text("Exact Audio Copy V1.0\n")
        (d / "info.txt").write_text("Label: Label MB\n")
        (d / "Album A.cue").write_text('FILE "01.flac" WAVE\n  TRACK 01 AUDIO\n')
        (d / "Album A.m3u").write_text("01.flac\n02.flac\n03.flac\n")
        (d / "Scans" / "Booklet").mkdir(parents=True)
        (d / "Scans" / "Booklet" / "01.jpg").write_bytes(JPG + b"page 1")
        (d / "Scans" / "tray.png").write_bytes(b"\x89PNG tray")
        (d / "Scans" / "scans.nfo").write_text("scanned at 600 dpi\n")
        return d

    def plan_of(self, d, http=None, **prof):
        prof.setdefault("subfolders", ON)
        eng = self.engine(http or FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A")]), **prof)
        props = eng.identify_all(eng.albums_in(d)[0])
        return pl.build(props, eng.profile, extra_roots=[self.lib])

    def tree(self, d):
        return sorted(str(p.relative_to(d)) for p in Path(d).rglob("*"))

    def test_rules(self):
        s = dict(tidy.DEFAULTS, **ON)
        a = Path("/m/A")
        homes = {a, a / "CD1"}
        cases = {"cover.jpg": "cover.jpg", "Folder.PNG": "Folder.PNG", "back.jpg": "covers/back.jpg",
                 "x.heic": "covers/x.heic", "b.pdf": "covers/b.pdf", "r.log": "misc/r.log",
                 "a.cue": "a.cue", "a.m3u8": "a.m3u8", "01.lrc": "01.lrc", "Scans/front.jpg": "covers/front.jpg",
                 "Scans/Booklet/1.jpg": "covers/Booklet/1.jpg", "covers/Booklet/1.jpg": "covers/Booklet/1.jpg",
                 "misc/r.log": "misc/r.log", "covers/n.txt": "misc/n.txt", "CD1/cover.jpg": "CD1/cover.jpg",
                 "CD1/back.jpg": "CD1/covers/back.jpg", "CD1/Scans/x.jpg": "CD1/covers/x.jpg",
                 "CD1/r.log": "CD1/misc/r.log", "Extras/v.mkv": "Extras/v.mkv",
                 "a.md5": "a.md5", "a.ffp": "a.ffp", "a.sfv": "a.sfv", "a.sha256": "a.sha256",
                 "album.nfo": "album.nfo", "artist.nfo": "artist.nfo", "album.xml": "album.xml",
                 "CD1/album.nfo": "CD1/album.nfo", "00-group.nfo": "misc/00-group.nfo",
                 "Scans/album.nfo": "misc/album.nfo"}
        for src, want in cases.items():
            self.assertEqual(tidy.target(a / src, a, homes, s)[0], a / want, src)
        s["where"] = "album"
        self.assertEqual(tidy.target(a / "CD1/back.jpg", a, homes, s)[0], a / "covers/CD1/back.jpg")
        self.assertEqual(tidy.target(a / "CD1/cover.jpg", a, homes, s)[0], a / "CD1/cover.jpg")
        self.assertEqual(tidy.target(a / "my.jpg", a, homes, s, ("my",))[0], a / "my.jpg")
        self.assertEqual(tidy.target(Path("/m/B/x.jpg"), a, homes, s)[0], Path("/m/B/x.jpg"))
        s.update(misc=False)
        self.assertEqual(tidy.target(a / "r.log", a, homes, s)[0], a / "r.log")

    def test_a_front_cover_named_otherwise_stays(self):
        a = Path("/m/A")
        homes = {a, a / "CD1"}
        for pics, kept in ((["Front Cover.jpg", "back.jpg"], "Front Cover.jpg"),
                           (["Band A - Album A - Front.jpg", "Band A - Album A - Back.jpg"],
                            "Band A - Album A - Front.jpg"),
                           (["00 - front.jpg", "01 - inlay.jpg"], "00 - front.jpg"),
                           (["cover1.jpg", "cd.jpg"], "cover1.jpg"), (["scan.jpg"], "scan.jpg"),
                           (["back.jpg"], None), (["x.jpg", "y.jpg"], None), (["cover.jpg", "front 2.jpg"], None)):
            got = tidy.likely_fronts([a / x for x in pics], a, homes)
            self.assertEqual(got, {a / kept} if kept else set(), pics)
        s = dict(tidy.DEFAULTS, **ON)
        keep = {a / "Front Cover.jpg"}
        self.assertEqual(tidy.target(a / "Front Cover.jpg", a, homes, s, (), set(), keep=keep)[0], a / "Front Cover.jpg")
        self.assertEqual(tidy.fronts_at([a / "Front Cover.jpg"], a, homes, (), keep), {a})
        for name in ("Front Cover.jpg", "Band A - Album A - Front.jpg", "00 - front.jpg", "cover1.jpg"):
            d = self.album("Band A", "Album A")
            (d / name).write_bytes(JPG + b"front")
            (d / "back.jpg").write_bytes(JPG + b"back")
            p = self.plan_of(d)
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            self.assertEqual([x for x in self.tree(d) if not x.endswith(".flac")],
                             sorted(["covers", "covers/back.jpg", name]), name)
            executor.undo(p["job"])
            for x in (d / name, d / "back.jpg"):
                x.unlink()

    def test_a_folder_with_more_than_pictures_and_papers_is_left_as_it_is(self):
        d = self.album("Band A", "Album A")
        (d / "cover.jpg").write_bytes(JPG + b"front")
        (d / "Extras").mkdir()
        (d / "Extras" / "clip.mkv").write_bytes(b"video")
        (d / "Extras" / "clip.txt").write_text("about the clip\n")
        (d / "Extras" / "still.jpg").write_bytes(JPG + b"still")
        (d / "Booklet").mkdir()
        (d / "Booklet" / "01.jpg").write_bytes(JPG + b"page")
        (d / "Booklet" / "Thumbs.db").write_bytes(b"junk")
        (d / "Album A.md5").write_text("0123 *01.flac\n")
        p = self.plan_of(d)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        got = self.tree(d)
        for f in ("Extras/clip.mkv", "Extras/clip.txt", "Extras/still.jpg", "covers/Booklet/01.jpg",
                  "Album A.md5", "cover.jpg"):
            self.assertIn(f, got, got)
        self.assertNotIn("misc", got)

    def test_a_front_cover_kept_in_scans_goes_beside_the_music_when_none_is(self):
        s = dict(tidy.DEFAULTS, **ON)
        a = Path("/m/A")
        homes = {a}
        fronted = tidy.fronts_at([a / "Covers/front.jpg", a / "Covers/back.jpg"], a, homes)
        self.assertEqual(fronted, set())
        self.assertEqual(tidy.target(a / "Covers/front.jpg", a, homes, s, (), fronted)[0], a / "front.jpg")
        self.assertEqual(tidy.target(a / "Scans/cover.png", a, homes, s, (), fronted)[0], a / "covers/cover.png")
        fronted = tidy.fronts_at([a / "folder.jpg", a / "Scans/front.jpg"], a, homes)
        self.assertEqual(fronted, {a})
        self.assertEqual(tidy.target(a / "Scans/front.jpg", a, homes, s, (), fronted)[0], a / "covers/front.jpg")

    def test_the_album_keeps_a_front_cover_from_its_covers_folder(self):
        d = self.album("Band A", "Album A")
        (d / "Covers").mkdir()
        (d / "Covers" / "front.jpg").write_bytes(JPG + b"front")
        (d / "Covers" / "back.jpg").write_bytes(JPG + b"back")
        p = self.plan_of(d)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual([x for x in self.tree(d) if not x.endswith(".flac")],
                         ["covers", "covers/back.jpg", "front.jpg"])
        self.assertEqual(self.plan_of(d)["albums"][0]["status"], "unchanged")

    def test_a_small_front_brought_from_scans_is_replaced_by_the_larger_cover_at_once(self):
        # covers/Artwork/front.jpg (small) goes beside the music; the larger cover found
        # replaces it as it moves, not on the next run (planning again plans nothing)
        import io
        from PIL import Image

        def jpeg(side, colour):
            buf = io.BytesIO()
            Image.new("RGB", (side, side), colour).save(buf, "JPEG")
            return buf.getvalue()
        d = self.album("Band A", "Album A")
        (d / "covers" / "Artwork").mkdir(parents=True)
        small = jpeg(200, "red")
        (d / "covers" / "Artwork" / "front.jpg").write_bytes(small)
        (d / "covers" / "Artwork" / "back.jpg").write_bytes(jpeg(200, "blue"))
        big = Path(self.lib) / "big.jpg"
        big.write_bytes(jpeg(800, "green"))
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A")]), subfolders=ON)
        props = eng.identify_all(eng.albums_in(d)[0])
        props[0].artwork = {"front": str(big), "source": "test", "why": "test"}
        eng.profile["artwork"]["enabled"] = True          # (the cover is given here: nothing looked up)
        p = pl.build(props, eng.profile, extra_roots=[self.lib])
        moved = [c for c in p["albums"][0]["companions"] if c["dst"] == str(d / "front.jpg")]
        self.assertEqual(len(moved), 1, pl.preview(p))
        self.assertTrue(moved[0].get("rewrite"), moved)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        from artwork import _size_of
        self.assertEqual(_size_of((d / "front.jpg").read_bytes()), (800, 800))
        self.assertFalse((d / "cover.jpg").exists())
        self.assertEqual(sorted(x for x in self.tree(d) if not x.endswith(".flac")),
                         ["covers", "covers/back.jpg", "front.jpg"])
        executor.undo(p["job"])
        self.assertEqual((d / "covers" / "Artwork" / "front.jpg").read_bytes(), small)
        self.assertFalse((d / "front.jpg").exists())
        # a front as large as min_px is moved as it is
        (d / "covers" / "Artwork" / "front.jpg").write_bytes(jpeg(600, "red"))
        props = eng.identify_all(eng.albums_in(d)[0])
        props[0].artwork = {"front": str(big), "source": "test", "why": "test"}
        p = pl.build(props, eng.profile, extra_roots=[self.lib])
        moved = [c for c in p["albums"][0]["companions"] if c["dst"] == str(d / "front.jpg")]
        self.assertEqual(len(moved), 1)
        self.assertFalse(moved[0].get("rewrite"), moved)

    def test_profile_checks(self):
        self.assertEqual(pf.validate({"name": "x"}), [])
        self.assertFalse(pf.from_dict({"name": "x"})["subfolders"]["covers"])
        for bad in ({"covers_name": "a/b"}, {"misc_name": ""}, {"covers_name": ".hidden"}, {"where": "x"},
                    {"covers": "yes"}, {"covers_name": "Extras"}, {"misc_name": "Disc 1"}):
            self.assertTrue(pf.validate({"name": "x", "subfolders": bad}), bad)

    def test_single_album_in_place_then_again_then_undo(self):
        d = self.single()
        before = self.tree(d)
        p = self.plan_of(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready", pl.preview(p))
        text = pl.preview(p)
        self.assertIn("(into the covers folder)", text)
        self.assertIn("(into the misc folder)", text)
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        after = self.tree(d)
        for f in ("cover.jpg", "Album A.cue", "Album A.m3u", "covers/back.jpg", "covers/booklet.pdf",
                  "covers/Booklet/01.jpg", "covers/tray.png", "misc/Album A.log", "misc/info.txt",
                  "misc/scans.nfo", "01.flac"):
            self.assertIn(f, after, after)
        for f in ("back.jpg", "Scans", "Album A.log", "info.txt"):
            self.assertNotIn(f, after, after)
        # the matcher still reads them where they are now
        self.assertIn("Label: Label MB", ma.notes_text(d))
        self.assertEqual([Path(x["path"]).name for x in ma.rip_logs(d)], ["Album A.log"])
        self.assertTrue(any(Path(x).name == "tray.png" for x in ma.list_images(d, 20)[0]))
        # planned again: nothing to move
        p2 = self.plan_of(d)
        self.assertEqual(p2["albums"][0]["companions"], [], pl.preview(p2))
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(self.tree(d), before)

    def test_off_changes_nothing(self):
        d = self.single()
        p = self.plan_of(d, subfolders={})
        self.assertEqual(p["albums"][0]["companions"], [], pl.preview(p))
        dest = self.tmp / "dest"
        dest.mkdir()
        p = self.plan_of(d, subfolders={}, operation="move", destination=str(dest))
        self.assertFalse(any(c.get("tidy") for c in p["albums"][0]["companions"]))
        self.assertTrue(any(Path(c["dst"]).name == "back.jpg" and Path(c["dst"]).parent.name != "covers"
                            for c in p["albums"][0]["companions"]), pl.preview(p))

    def test_only_covers(self):
        d = self.single()
        p = self.plan_of(d, subfolders={"covers": True, "covers_name": "Artwork"})
        dsts = {str(Path(c["dst"]).relative_to(d)) for c in p["albums"][0]["companions"]}
        self.assertIn("Artwork/back.jpg", dsts)
        self.assertFalse(any(x.endswith((".log", ".txt", ".nfo")) for x in dsts), dsts)

    def test_a_name_taken_is_never_overwritten(self):
        d = self.single()
        (d / "covers").mkdir()
        (d / "covers" / "back.jpg").write_bytes(JPG + b"another back")
        p = self.plan_of(d)
        ce = next(c for c in p["albums"][0]["companions"] if Path(c["src"]).name == "back.jpg")
        self.assertEqual(Path(ce["dst"]).parent, d / "covers")
        self.assertNotEqual(Path(ce["dst"]).name, "back.jpg")
        self.assertEqual(executor.Executor(p).run()["status"], "done")
        self.assertEqual((d / "covers" / "back.jpg").read_bytes(), JPG + b"another back")
        self.assertEqual(self.plan_of(d)["albums"][0]["companions"], [])

    def test_a_file_or_link_named_as_the_folder_leaves_the_file_put(self):
        """A plain file named "misc", or a "covers" link out of the library: the log or scan
        stays where it is (a note says why); the album is tagged, not failed at apply."""
        d = self.album("Band A", "Album A")
        (d / "misc").write_text("not a folder\n")
        (d / "r.log").write_text("Exact Audio Copy V1.0\n")
        outside = Path(_tempfile.mkdtemp(prefix="tm-outside-"))
        (d / "covers").symlink_to(outside)
        (d / "back.jpg").write_bytes(JPG + b"back")
        p = self.plan_of(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready", pl.preview(p))
        self.assertFalse(any(c.get("tidy") for c in a["companions"]), a["companions"])
        self.assertTrue(any("r.log is not moved into the misc folder" in n for n in a["notes"]), a["notes"])
        self.assertTrue(any("back.jpg is not moved into the covers folder" in n for n in a["notes"]), a["notes"])
        r = executor.Executor(p).run()
        self.assertEqual(r["counts"], {"done": 1}, r)
        self.assertTrue((d / "r.log").exists() and (d / "back.jpg").exists())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertIsNone(tidy.blocked(d / "Scans" / "x" / "a.jpg", d))
        self.assertEqual(tidy.blocked(d / "misc" / "x" / "a.log", d), d / "misc")

    def test_move_profile_files_them_into_the_new_folder(self):
        d = self.single()
        dest = self.tmp / "dest"
        dest.mkdir()
        p = self.plan_of(d, operation="move", destination=str(dest))
        self.assertEqual(p["albums"][0]["status"], "ready", pl.preview(p))
        self.assertEqual(executor.Executor(p).run()["status"], "done")
        album = next(dest.rglob("*.flac")).parent
        got = self.tree(album)
        for f in ("cover.jpg", "Album A.cue", "covers/back.jpg", "covers/Booklet/01.jpg", "misc/Album A.log"):
            self.assertIn(f, got, got)
        self.assertFalse(d.exists())
        # filed already: planned again, nothing new
        p2 = self.plan_of(album, operation="move", destination=str(dest))
        self.assertEqual(p2["albums"][0]["companions"], [], pl.preview(p2))
        executor.undo(p["job"])
        self.assertTrue((d / "Scans" / "tray.png").exists())

    def test_an_album_nothing_identified_is_never_tidied(self):
        d = self.album("Band Z", "Nothing Known")
        (d / "my notes.txt").write_text("x")
        (d / "photo.jpg").write_bytes(JPG)
        p = self.plan_of(d, FakeHttp(mb=[]), operation="in_place")
        a = p["albums"][0]
        self.assertEqual(a["status"], "unresolved")
        self.assertEqual(a["companions"], [], pl.preview(p))
        aside = self.tmp / "aside"
        p = self.plan_of(d, FakeHttp(mb=[]), unresolved={"action": "move", "move_to": str(aside)})
        comps = p["albums"][0]["companions"]
        self.assertFalse(any(Path(c["dst"]).parent.name in ("covers", "misc") for c in comps), comps)

    def test_copy_profile_never_tidies_the_source(self):
        d = self.single()
        dest = self.tmp / "dest"
        dest.mkdir()
        p = self.plan_of(d, operation="copy", destination=str(dest))
        self.assertTrue(all(c["op"] == "copy" for c in p["albums"][0]["companions"]))
        executor.Executor(p).run()
        self.assertTrue((d / "back.jpg").exists() and (d / "Scans").exists())
        album = next(dest.rglob("*.flac")).parent
        self.assertTrue((album / "covers" / "back.jpg").exists())
        self.assertEqual(self.plan_of(d, operation="copy", destination=str(dest))["albums"][0]["companions"], [])

    def disc_set(self):
        d = self.lib / "Band S" / "2005 - Set S"
        for n in (1, 2):
            cd = d / f"CD{n}"
            cd.mkdir(parents=True)
            for i in range(2):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i + 7 * n}:duration={10 * (i + 1) + 20 * (n - 1)}",
                                "-ar", "8000", "-ac", "1", "-metadata", f"title=D{n}T{i}", "-metadata",
                                "album=Set S", "-metadata", "albumartist=Band S", "-metadata", f"track={i + 1}",
                                "-metadata", f"disc={n}", str(cd / f"{i + 1:02d}.flac"), "-y"], check=True)
            (cd / f"CD{n}.log").write_text(f"Exact Audio Copy disc {n}\n")
            (cd / f"CD{n}.cue").write_text('FILE "01.flac" WAVE\n')
            (cd / "cover.jpg").write_bytes(JPG + bytes([n]))
            (cd / "Scans").mkdir()
            (cd / "Scans" / "disc.jpg").write_bytes(JPG + b"disc" + bytes([n]))
        (d / "folder.jpg").write_bytes(JPG + b"set")
        (d / "Scans").mkdir()
        (d / "Scans" / "box back.jpg").write_bytes(JPG + b"box")
        return d

    def test_two_disc_set_each_disc_its_own(self):
        d = self.disc_set()
        before = self.tree(d)
        http = FakeHttp(mb=[two_discs("mb-s", "Band S", "Set S")])
        p = self.plan_of(d, http=http)
        self.assertEqual(p["albums"][0]["status"], "ready", pl.preview(p))
        self.assertEqual(executor.Executor(p).run()["status"], "done")
        got = self.tree(d)
        for f in ("folder.jpg", "covers/box back.jpg", "CD1/cover.jpg", "CD1/CD1.cue", "CD1/covers/disc.jpg",
                  "CD1/misc/CD1.log", "CD2/covers/disc.jpg", "CD2/misc/CD2.log"):
            self.assertIn(f, got, got)
        self.assertNotIn("CD1/Scans", got)
        self.assertEqual(self.plan_of(d, http=http)["albums"][0]["companions"], [])
        executor.undo(p["job"])
        self.assertEqual(self.tree(d), before)

    def test_two_disc_set_one_folder_for_the_album(self):
        d = self.disc_set()
        http = FakeHttp(mb=[two_discs("mb-s", "Band S", "Set S")])
        p = self.plan_of(d, http=http, subfolders=dict(ON, where="album"))
        self.assertEqual(executor.Executor(p).run()["status"], "done", pl.preview(p))
        got = self.tree(d)
        for f in ("covers/box back.jpg", "covers/CD1/disc.jpg", "covers/CD2/disc.jpg", "misc/CD1/CD1.log",
                  "CD1/cover.jpg", "CD2/CD2.cue"):
            self.assertIn(f, got, got)
        self.assertEqual(self.plan_of(d, http=http, subfolders=dict(ON, where="album"))["albums"][0]["companions"],
                         [])


class WithSplit(Folders):
    def test_each_version_gets_its_own_covers_and_misc(self):
        top = self.mixed_folder()
        (top / "Scans").mkdir()
        (top / "Scans" / "back.jpg").write_bytes(JPG + b"back")
        p = self.planned(top, subfolders=ON)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        r = executor.Executor(p).run()
        self.assertEqual(r["status"], "done", r)
        for name in ("1994 - Album M (Remaster 2024)", "1994 - Album M (Remix 2024)"):
            v = top.parent / name
            got = self.tree(v)
            for f in ("cover.jpg", "covers/back.jpg", "misc/rip.log"):
                self.assertIn(f, got, (name, got))
        self.assertFalse(top.exists())
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertTrue((top / "Scans" / "back.jpg").exists())


if __name__ == "__main__":
    unittest.main()
