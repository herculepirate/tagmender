# 0.8.72 EDT-2: TagMender as a plain tag editor. Folders added without a look-up (List first)
# list their albums "Not looked up"; the Overview's Tags pane edits them (a file, a disc, an
# album; "different in N files", set for all; multi-value fields; a cover), and Save Tags writes
# only those edits through the executor as one job - staged, audio verified, tags read back,
# journaled, undoable - with no look-up, rename, move, companion or TagMender mark. The watcher
# is not set off by the save; a later Look Up plans the album afresh (the database's values
# win); edits not saved before a look-up are kept by it as any edit of yours; leaving with edits
# not saved asks Save / Don't Save / Cancel. Synthetic files (ffmpeg sine tones), no network.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None
import test_engine as te  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import payload  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".m4a": ["-c:a", "aac"]}


def wait(cond, app, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make(path, tags, seconds=1, freq=440):
    """A short sine file, tagged with TAGS by tagio (an MP3 gets an ID3v2.3 tag first)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "8000", "-ac", "1", *CODECS[path.suffix], "-map_metadata", "-1", "-y", str(path)],
                   check=True)
    if path.suffix == ".mp3":
        from mutagen.id3 import ID3
        ID3().save(str(path), v2_version=3)
    tagio.write(path, {k: list(v) for k, v in tags.items()}, names={"mytag": "MyTag"})
    return path


class NoNetwork:
    """The providers' HTTP: any request fails the test (a plain edit asks nothing of anyone)."""
    calls = []

    def __init__(self, *_a, **_k):
        self.db = None

    def get_json(self, url, host):
        NoNetwork.calls.append((host, url))
        raise AssertionError(f"a request to {host} during a plain edit: {url}")


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class PlainEdits(te.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.addCleanup(app_settings.set_value, "add_folder", "look_up")
        self.mp3 = self.lib / "Band A" / "Alpha"
        self.flac = self.lib / "Band B" / "Beta"
        self.m4a = self.lib / "Band C" / "Gamma"
        for d, ext in ((self.mp3, ".mp3"), (self.flac, ".flac"), (self.m4a, ".m4a")):
            for i, genre in enumerate(("Rock", "Pop")):
                make(d / f"{i + 1:02d} Song{ext}", {"title": [f"Song {i + 1}"], "artist": [d.parent.name],
                                                    "album": [d.name], "albumartist": [d.parent.name],
                                                    "tracknumber": [str(i + 1)], "genre": [genre],
                                                    "comment": ["kept as it is"], "mytag": ["custom value"]},
                     freq=300 + 100 * i)
        NoNetwork.calls = []
        p = mock.patch.object(ma, "Http", NoNetwork)
        p.start()
        self.addCleanup(p.stop)
        pf.save(pf.from_dict({"name": "Auto", "unattended": True, "providers": {"web": {"enabled": False}},
                              "artwork": {"enabled": False}}))
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Auto")
        self.panes = self.ov.panes
        self.insp = self.win.inspector

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and self.win.threads():
            self.app.processEvents()
            time.sleep(0.02)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Don't Save"):
            self.win.close()
        super().tearDown()

    # ---- helpers --------------------------------------------------------------------------
    def open_list(self):
        with mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")):
            self.assertTrue(self.ov.set_path(str(self.lib)))
            self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app),
                            self.ov.summary.text())
        self.assertEqual({a["status"] for a in self.ov.plan["albums"]}, {"listed"})

    def album_of(self, folder):
        return next(a for a in self.ov.plan["albums"] if Path(a["source_dir"]) == folder)

    def show(self, folder, kind="album", key=None):
        a = self.album_of(folder)
        self.panes.show_scope(a, kind, key)
        self.assertTrue(wait(lambda: self.panes.tag_worker is None and self.panes.current, self.app))
        return a

    def row(self, field_):
        return next(r for r in self.panes.tag_model.rows if r["field"] == field_)

    def settle_edits(self):
        self.assertTrue(wait(lambda: not self.insp._wanted, self.app))
        self.app.processEvents()

    def read(self, folder):
        return {f.name: tagio.read(f) for f in sorted(folder.iterdir())}

    def digests(self, *folders):
        return {f: payload.digest(f) for d in folders for f in sorted(d.iterdir())}

    def edit_all(self):
        """Title of one MP3 file, genre of every file of three albums (theirs differ), and two
        artists in one FLAC file's artist (a multi-value field)."""
        first = str(self.mp3 / "01 Song.mp3")
        self.show(self.mp3, "file", first)
        self.panes.typed(self.row("title"), "A New Title")
        self.settle_edits()
        for d in (self.mp3, self.flac, self.m4a):
            self.show(d)
            r = self.row("genre")
            self.assertTrue(r["mixed_after"])
            self.assertEqual(r["after"], "different in 2 files")
            with mock.patch.object(self.gui.sheets, "confirm", return_value=True) as asked, \
                    mock.patch.object(self.gui.inspector, "tag_sheet", return_value=("genre", ["Jazz"])):
                self.panes.tag_view.selectRow(self.panes.tag_model.rows.index(r))
                self.panes.edit_selected()
            asked.assert_called_once()                               # (files that differ: asked first)
            self.settle_edits()
        self.show(self.flac, "file", str(self.flac / "02 Song.flac"))
        self.panes._edit("artist", ["Band B", "Guest"])
        self.settle_edits()

    def save(self):
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")), \
                mock.patch.object(type(self.ov), "look_up", side_effect=AssertionError("looked up")):
            self.win.act("save_tags").trigger()
            self.assertTrue(wait(lambda: not self.ov.running, self.app), self.ov.summary.text())

    # ---- the tests ------------------------------------------------------------------------
    def test_edit_without_look_up_then_save_writes_only_the_edits_and_undo_restores(self):
        self.open_list()
        before, digests = {d: self.read(d) for d in (self.mp3, self.flac, self.m4a)}, \
            self.digests(self.mp3, self.flac, self.m4a)
        self.edit_all()
        a = self.album_of(self.mp3)
        self.assertEqual(a["status"], "listed")                       # (still not looked up)
        r = self.ov.model.albums.index(a)
        self.assertIn("Edited, not saved", self.ov.model.data(self.ov.model.index(r, 0)))
        self.show(self.mp3)
        self.assertEqual(self.row("genre")["after"], "Jazz")          # (set for all)
        self.win.update_album_actions()
        self.assertTrue(self.win.act("apply").isEnabled())
        self.assertEqual(self.win.act("apply").iconText(), "Save 3 Albums")
        self.assertTrue(self.win.act("save_tags").isEnabled())
        self.save()
        self.assertEqual(NoNetwork.calls, [])
        after = {d: self.read(d) for d in (self.mp3, self.flac, self.m4a)}
        want = {d: {n: dict(t, genre=["Jazz"]) for n, t in before[d].items()} for d in before}
        want[self.mp3]["01 Song.mp3"]["title"] = ["A New Title"]
        want[self.flac]["02 Song.flac"]["artist"] = ["Band B", "Guest"]
        self.assertEqual(after, want)                                  # (nothing else changed, MyTag kept)
        self.assertEqual(self.digests(self.mp3, self.flac, self.m4a), digests)    # (the audio untouched)
        from mutagen.id3 import ID3
        self.assertEqual(ID3(str(self.mp3 / "01 Song.mp3")).version[:2], (2, 3))  # (its own ID3 version)
        self.assertEqual(ID3(str(self.mp3 / "01 Song.mp3")).getall("TXXX:MyTag")[0].text, ["custom value"])
        self.assertTrue(all(not k.lower().startswith("tagmender") for t in after.values() for v in t.values()
                            for k in v))                               # (no identification marks)
        # one job, of every album, in History (Jobs) - the plan only the edited files
        jobs = executor.jobs()
        self.assertEqual(len(jobs), 1)
        job = jobs[0][0] if isinstance(jobs[0], (list, tuple)) else jobs[0]["job"]
        plan = json.loads((executor.job_dir(job) / "plan.json").read_text())
        self.assertTrue(plan["plain"])
        self.assertEqual({fe["op"] for x in plan["albums"] for fe in x["files"]}, {"tag"})
        self.assertEqual(sorted(Path(x["source_dir"]).name for x in plan["albums"]), ["Alpha", "Beta", "Gamma"])
        # the albums are shown as they are now, their edits forgotten
        self.assertEqual({x["status"] for x in self.ov.plan["albums"]}, {"listed"})
        self.assertEqual(self.insp.edits, {})
        self.assertFalse(self.win.act("save_tags").isEnabled())
        self.show(self.mp3, "file", str(self.mp3 / "01 Song.mp3"))
        self.assertEqual(self.row("title")["after"], "A New Title")
        # undo puts every file back as it was
        said = executor.undo(job)
        self.assertEqual(sorted(said), sorted(f"put back {n}" for d in before for n in before[d]))
        self.assertEqual({d: self.read(d) for d in before}, before)
        self.assertEqual(self.digests(self.mp3, self.flac, self.m4a), digests)

    def test_a_cover_chosen_for_an_album_not_looked_up_is_embedded_by_save(self):
        import io

        import artwork
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (200, 30, 30)).save(buf, "PNG")
        self.open_list()
        a = self.show(self.flac)
        digests = self.digests(self.flac)
        tags = self.read(self.flac)
        self.insp.show_album(a, self.ov.plan)
        cover = self.gui.inspector.stage_cover(buf.getvalue(), "test.png")
        self.insp.set_cover(cover, a)
        self.settle_edits()
        self.assertTrue(self.ov.plain_albums())
        self.save()
        for f in sorted(self.flac.iterdir()):
            self.assertTrue(artwork.has_front(f), f)
        got = self.read(self.flac)
        self.assertEqual({n: t.pop("~pictures") for n, t in got.items()}, {n: ["1"] for n in got})
        self.assertEqual(got, tags)                                     # (the tags as they were)
        self.assertEqual(self.digests(self.flac), digests)
        self.assertEqual(sorted(p.name for p in self.flac.iterdir()), ["01 Song.flac", "02 Song.flac"])  # (no file)

    def test_saving_unchanged_values_writes_nothing(self):
        self.open_list()
        self.show(self.mp3, "file", str(self.mp3 / "01 Song.mp3"))
        self.panes.typed(self.row("title"), "Song 1")                 # (as it is: no edit)
        self.settle_edits()
        self.assertEqual(self.ov.plain_albums(), [])
        self.win.update_album_actions()
        self.assertFalse(self.win.act("save_tags").isEnabled())
        self.assertFalse(self.win.act("apply").isEnabled())

    def test_a_file_changed_after_the_edit_is_not_written(self):
        self.open_list()
        f = self.mp3 / "01 Song.mp3"
        self.show(self.mp3, "file", str(f))
        self.panes.typed(self.row("title"), "Mine")
        self.settle_edits()
        time.sleep(0.01)
        tagio.write(f, {"comment": ["another program"]})              # (written by someone else meanwhile)
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertEqual(tagio.read(f)["title"], ["Song 1"])
        self.assertEqual(tagio.read(f)["comment"], ["another program"])
        self.assertTrue(wait(lambda: not self.insp._wanted, self.app))
        self.assertEqual(len(self.ov.plain_albums()), 1)                # (your edit kept, to save again)

    def test_the_watcher_is_not_set_off_by_the_save(self):
        import engine
        import watcher
        prof = pf.from_dict({"name": "Watch", "sources": [{"path": str(self.lib), "watch": True}],
                             "watch": {"settle_minutes": 0}, "providers": {"web": {"enabled": False}}})
        pf.save(prof)
        prof = pf.load("Watch")
        w = watcher.Watcher([prof], once=True)
        db = watcher._db()
        groups = {Path(g.folder): g for g in engine.Engine(prof).albums_in(self.lib)}
        g = groups[self.flac]                                         # (looked at before: a record of it)
        w.record(db, g.folder, prof, "done", 0, key=w.key(g), files=g.files)
        self.assertEqual(w.due(g.folder, prof, db, w.key(g), g.files, g.files), (False, "done"))
        self.open_list()
        self.edit_all()
        self.save()
        groups = {Path(g.folder): g for g in engine.Engine(prof).albums_in(self.lib)}
        for d, status in ((self.flac, "done"), (self.mp3, "edited"), (self.m4a, "edited")):
            g = groups[d]
            self.assertEqual(w.due(g.folder, prof, db, w.key(g), g.files, g.files), (False, status), d)
        db.close()
        # a change by someone else afterwards: looked at again
        time.sleep(1.1)
        tagio.write(self.mp3 / "02 Song.mp3", {"comment": ["another program"]})
        g = {Path(g.folder): g for g in engine.Engine(prof).albums_in(self.lib)}[self.mp3]
        db = watcher._db()
        self.assertEqual(w.due(g.folder, prof, db, w.key(g), g.files, g.files), (True, "changed"))
        db.close()
        self.assertEqual(NoNetwork.calls, [])

    def look_up_delta(self):
        """Album Delta (three tracks Discogs has, titled T0 to T2) looked up in the list, the
        provider answered by a stand-in: its plan entry for the first track."""
        http = te.FakeHttp(dg=[te.dg_release(940, "Band D", "Delta")])
        with mock.patch.object(ma, "Http", lambda *a, **k: http):
            self.ov.look_up("profile", [self.album_of(self.delta)])
            self.assertTrue(wait(lambda: not self.ov.running, self.app), self.ov.summary.text())
        a = self.album_of(self.delta)
        self.assertEqual(a["status"], "ready", a.get("identification"))
        return next(fe for fe in a["files"] if fe["src"].endswith("01.flac"))

    def title_after(self, fe):
        return next(r for r in fe["diff"] if r["field"] == "title")

    def test_a_look_up_after_the_save_takes_the_databases_values(self):
        self.delta = self.album("Band D", "Delta")                    # (titles T0, T1, T2, as Discogs has)
        f = self.delta / "01.flac"
        self.open_list()
        self.show(self.delta, "file", str(f))
        self.panes.typed(self.row("title"), "My Title")
        self.settle_edits()
        self.save()
        self.assertEqual(tagio.read(f)["title"], ["My Title"])
        self.assertEqual(self.insp.edits, {})
        fe = self.look_up_delta()                                    # (the database's value wins)
        row = self.title_after(fe)
        self.assertEqual((row["before"], row["after"]), (["My Title"], ["T0"]))
        self.assertNotEqual(row.get("by"), "you")

    def test_edits_not_saved_are_kept_by_a_look_up_as_today(self):
        self.delta = self.album("Band D", "Delta")
        f = self.delta / "01.flac"
        self.open_list()
        self.show(self.delta, "file", str(f))
        self.panes.typed(self.row("title"), "Pending Title")
        self.settle_edits()
        fe = self.look_up_delta()                                    # (your edit, kept by the new preview)
        row = self.title_after(fe)
        self.assertEqual((row["after"], row.get("by")), (["Pending Title"], "you"))
        self.assertEqual(tagio.read(f)["title"], ["T0"])             # (nothing written)
        self.assertEqual(self.ov.plain_albums(), [])                 # (an edit of the preview now: Apply)

    def test_leaving_with_edits_not_saved_asks(self):
        self.open_list()
        self.show(self.mp3, "file", str(self.mp3 / "01 Song.mp3"))
        self.panes.typed(self.row("title"), "Unsaved")
        self.settle_edits()
        other = self.tmp / "other"
        other.mkdir()
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel") as asked:
            self.assertFalse(self.ov.set_path(str(other)))
        self.assertIn("Save your edits of 1 album?", asked.call_args.args[1])
        self.assertEqual(len(self.ov.plain_albums()), 1)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):
            self.win.close()
        self.assertTrue(self.win.isVisible())                         # (Cancel: the window stays)
        # Save: saved, then the other folder is shown
        with mock.patch.object(self.gui.sheets, "ask", return_value="Save"), \
                mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")):
            self.assertFalse(self.ov.set_path(str(other)))           # (waits for the save)
            self.assertTrue(wait(lambda: self.ov.path == str(other) and not self.ov.running, self.app))
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Unsaved"])
        self.assertEqual(self.insp.edits, {})

    def test_dont_save_discards(self):
        self.open_list()
        self.show(self.mp3, "file", str(self.mp3 / "01 Song.mp3"))
        self.panes.typed(self.row("title"), "Dropped")
        self.settle_edits()
        other = self.tmp / "other"
        other.mkdir()
        with mock.patch.object(self.gui.sheets, "ask", return_value="Don't Save"):
            self.assertTrue(self.ov.set_path(str(other)))
        self.assertEqual(self.insp.edits, {})
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Song 1"])
        self.assertEqual(executor.jobs(), [])

    # ---- review of 0.8.72 (M1-M4, m1-m8) ----------------------------------------------------
    def edit_title(self, folder=None, name="01 Song.mp3", title="Mine"):
        folder = folder or self.mp3
        self.show(folder, "file", str(folder / name))
        self.panes.typed(self.row("title"), title)
        self.settle_edits()

    def watch_profile(self, path=None):
        prof = pf.from_dict({"name": "Watch", "sources": [{"path": str(path or self.lib), "watch": True}],
                             "watch": {"settle_minutes": 0}, "providers": {"web": {"enabled": False}}})
        pf.save(prof)
        return pf.load("Watch")

    def due(self, prof, folder, root=None):
        import engine
        import watcher
        w = watcher.Watcher([prof], once=True)
        g = {Path(g.folder): g for g in engine.Engine(prof).albums_in(root or self.lib)}[folder]
        db = watcher._db()
        try:
            return w.due(g.folder, prof, db, w.key(g), g.files, g.files)
        finally:
            db.close()

    def test_apply_leaves_plain_edits_for_save_tags(self):                    # M1
        self.delta = self.album("Band D", "Delta")
        self.open_list()
        self.edit_title()
        self.look_up_delta()
        self.win.update_album_actions()
        self.assertTrue(self.win.act("save_tags").isEnabled())
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True) as asked:
            self.ov.apply()
            self.assertTrue(wait(lambda: not self.ov.running, self.app))
        self.assertIn("not applied", asked.call_args.args[2])                   # (said in the Apply sheet)
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Song 1"])
        self.assertEqual([Path(a["source_dir"]) for a in self.ov.plain_albums()], [self.mp3])
        self.win.update_album_actions()
        self.assertTrue(self.win.act("save_tags").isEnabled())
        self.assertEqual(self.win.act("apply").iconText(), "Save Tags")
        self.save()
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Mine"])

    def test_listing_the_same_folder_again_keeps_the_edits_shown(self):      # M2
        self.open_list()
        self.edit_title()
        with mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")):
            self.ov.set_path(str(self.lib))
            self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app))
        self.assertTrue(wait(lambda: len(self.ov.plain_albums()) == 1 and not self.insp._wanted, self.app))
        self.win.update_album_actions()
        self.assertTrue(self.win.act("save_tags").isEnabled())
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel") as asked:
            self.win.close()
        asked.assert_called_once()
        self.assertTrue(self.win.isVisible())

    def test_undoing_a_save_keeps_the_watcher_off(self):                     # M3
        prof = self.watch_profile()
        self.open_list()
        self.edit_title()
        self.save()
        self.assertEqual(self.due(prof, self.mp3), (False, "edited"))
        time.sleep(1.1)
        job = executor.jobs()[0]
        job = job[0] if isinstance(job, (list, tuple)) else job["job"]
        executor.undo(job)
        self.assertEqual(self.due(prof, self.mp3), (False, "undone"))

    def test_saved_through_another_spelling_of_the_watched_folder(self):     # M4
        link = self.tmp / "linklib"
        _os.symlink(self.lib, link)
        prof = self.watch_profile(self.lib)
        with mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")):
            self.assertTrue(self.ov.set_path(str(link)))
            self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app))
        self.edit_title(link / "Band A" / "Alpha")
        self.save()
        self.assertEqual(self.due(prof, self.mp3), (False, "edited"))
        import watcher
        self.assertEqual(watcher.remember_saved([(str(self.tmp / "elsewhere"), [str(self.tmp / "elsewhere" / "x.mp3")])],
                                                [prof]), 0)                   # (no watched folder: no row)

    def test_frames_other_taggers_rename_stay_as_the_file_has_them(self):    # m1
        from mutagen.id3 import ID3, TXXX
        f = self.mp3 / "01 Song.mp3"
        t = ID3(str(f))
        t.add(TXXX(encoding=3, desc="Artists", text=["X"]))
        t.add(TXXX(encoding=3, desc="ALBUMARTISTSORT", text=["S"]))
        t.save(str(f), v2_version=3)
        before = sorted(k for k in ID3(str(f)).keys() if k != "TIT2")
        self.open_list()
        self.edit_title()
        self.save()
        tags = ID3(str(f))
        self.assertEqual(sorted(k for k in tags.keys() if k != "TIT2"), before)
        self.assertEqual(tags["TIT2"].text, ["Mine"])

    def test_a_change_after_the_first_edit_is_caught_though_another_file_is_edited_later(self):   # m2
        self.open_list()
        f = self.mp3 / "01 Song.mp3"
        self.edit_title()
        time.sleep(0.01)
        tagio.write(f, {"comment": ["another program"]})
        self.edit_title(name="02 Song.mp3", title="Other")                      # (the album edited again)
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertEqual(tagio.read(f)["title"], ["Song 1"])
        self.assertEqual(tagio.read(f)["comment"], ["another program"])
        self.assertEqual(tagio.read(self.mp3 / "02 Song.mp3")["title"], ["Song 2"])   # (its album left alone)
        self.assertTrue(wait(lambda: not self.insp._wanted, self.app))
        self.assertEqual(len(self.ov.plain_albums()), 1)
        self.save()                                                              # (read again: saved now)
        self.assertEqual(tagio.read(f)["title"], ["Mine"])
        self.assertEqual(tagio.read(f)["comment"], ["another program"])

    def test_files_your_rules_exclude_are_reported_and_keep_their_edits(self):     # m3
        self.open_list()
        self.show(self.mp3)
        self.panes._edit("genre", ["Jazz"])
        self.settle_edits()
        ex._write(ex.rules_file(), {"rules": [{"id": "s2", "name": "skip 2", "action": "skip", "target": "file",
                                               "when": {"all": [{"field": "filename", "op": "contains",
                                                                 "value": "02 Song"}]}}]})
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertIn("rules exclude", warned.call_args.args[2])
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["genre"], ["Jazz"])
        self.assertEqual(tagio.read(self.mp3 / "02 Song.mp3")["genre"], ["Pop"])
        self.assertTrue(wait(lambda: not self.insp._wanted, self.app))
        self.assertEqual(len(self.ov.plain_albums()), 1)                         # (its edit kept)
        a = self.album_of(self.mp3)
        self.assertEqual([Path(fe["src"]).name for fe in a["files"] if fe["op"] != "none"], ["02 Song.mp3"])

    def test_undo_edit_is_off_after_the_save(self):                          # m4
        self.open_list()
        self.edit_title()
        self.save()
        self.assertFalse(self.insp.can_undo_edit())
        self.win.update_tag_actions()
        self.assertFalse(self.win.act("undo_edit").isEnabled())

    def test_one_album_failing_keeps_its_edits_the_others_are_saved(self):   # multi-album, one fails
        if _os.geteuid() == 0:
            self.skipTest("root writes into read-only folders")
        self.open_list()
        self.edit_title()
        self.edit_title(self.flac, "01 Song.flac", "FlacMine")
        _os.chmod(self.flac, 0o555)
        try:
            with mock.patch.object(self.gui.sheets, "warning") as warned:
                self.save()
        finally:
            _os.chmod(self.flac, 0o755)
        warned.assert_called_once()
        self.assertIn("Beta", warned.call_args.args[2])
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Mine"])
        self.assertEqual(tagio.read(self.flac / "01 Song.flac")["title"], ["Song 1"])
        self.assertEqual([Path(a["source_dir"]) for a in self.ov.plain_albums()], [self.flac])
        self.save()
        self.assertEqual(tagio.read(self.flac / "01 Song.flac")["title"], ["FlacMine"])

    def test_closing_during_a_save_closes_once_it_is_done(self):            # m5
        self.open_list()
        self.edit_title()
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
            self.win.act("save_tags").trigger()
            self.assertTrue(self.ov.running)
            self.win.close()
            self.assertTrue(self.win.isVisible())                               # (not while it saves)
            self.assertTrue(wait(lambda: not self.win.isVisible(), self.app))
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Mine"])

    def test_closing_during_a_save_that_fails_stays_open(self):             # m5
        if _os.geteuid() == 0:
            self.skipTest("root writes into read-only folders")
        self.open_list()
        self.edit_title()
        _os.chmod(self.mp3, 0o555)
        try:
            with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                    mock.patch.object(self.gui.sheets, "warning") as warned:
                self.win.act("save_tags").trigger()
                self.win.close()
                self.assertTrue(wait(lambda: not self.ov.running, self.app))
                self.app.processEvents()
        finally:
            _os.chmod(self.mp3, 0o755)
        warned.assert_called_once()
        self.assertTrue(self.win.isVisible())
        self.assertEqual(len(self.ov.plain_albums()), 1)

    def test_taking_an_edited_album_out_of_the_list_asks(self):              # m6
        self.open_list()
        self.edit_title()
        a = self.album_of(self.mp3)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):
            self.ov._take_out([a])
        self.assertIn(a, self.ov.plan["albums"])
        with mock.patch.object(self.gui.sheets, "ask", return_value="Don't Save"):
            self.ov._take_out([a])
        self.assertNotIn(a, self.ov.plan["albums"])
        self.assertEqual(self.insp.edits, {})
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Song 1"])

    def test_taking_out_with_save_saves_first(self):                         # m6
        self.open_list()
        self.edit_title()
        a = self.album_of(self.mp3)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Save"):
            self.ov._take_out([a])
            self.assertTrue(wait(lambda: not self.ov.running, self.app))
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Mine"])
        self.assertTrue(wait(lambda: not any(x is a for x in self.ov.plan["albums"]), self.app))

    def test_a_folder_dropped_during_a_save_waits_and_joins_the_list_after_it(self):   # m7
        self.open_list()
        self.edit_title()
        new = self.tmp / "more" / "Band E" / "Epsilon"
        make(new / "01 E.flac", {"title": ["E"], "album": ["Epsilon"], "artist": ["Band E"]})
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch("engine.Engine.proposals", side_effect=AssertionError("looked up")):
            self.win.act("save_tags").trigger()
            self.assertTrue(self.ov.running)
            self.win.dropped([str(new.parent.parent)])
            self.assertIn("added to the list when the current work ends", self.win.statusBar().currentMessage())
            self.assertFalse(any(Path(a["source_dir"]) == new for a in self.ov.plan["albums"]))   # (not read now)
            self.assertTrue(wait(lambda: not self.ov.running and any(
                Path(a["source_dir"]) == new for a in self.ov.plan["albums"]), self.app))
        self.assertEqual(tagio.read(self.mp3 / "01 Song.mp3")["title"], ["Mine"])
        self.assertEqual(next(a for a in self.ov.plan["albums"] if Path(a["source_dir"]) == new)["status"], "listed")

    def test_an_album_wide_edit_after_an_outside_change_is_still_refused(self):   # m2 (residual)
        self.open_list()
        f = self.mp3 / "01 Song.mp3"
        self.edit_title()
        time.sleep(0.01)
        tagio.write(f, {"comment": ["another program"]})
        self.show(self.mp3)
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
            self.panes._edit("genre", ["Jazz"])                                  # (genre for all, afterwards)
        self.settle_edits()
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertIn("changed", warned.call_args.args[2].lower())
        self.assertEqual(tagio.read(f)["title"], ["Song 1"])
        self.assertEqual(tagio.read(f)["genre"], ["Rock"])

    def test_a_journal_that_cannot_be_read_keeps_the_edits(self):            # (1)
        self.open_list()
        self.edit_title()
        real = executor.Journal.read

        def flaky(j):
            got = real(j)
            if any(e.get("ev") == "end" for e in got):
                raise OSError("unreadable")
            return got
        with mock.patch.object(executor.Journal, "read", flaky), \
                mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertIn("could not be read", warned.call_args.args[2])
        self.assertTrue(wait(lambda: not self.insp._wanted, self.app))
        self.assertIn(_os.path.realpath(str(self.mp3)), self.insp.edits)       # (kept)

    def test_a_cover_image_gone_before_saving_is_said_so(self):              # (3)
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (20, 30, 200)).save(buf, "PNG")
        self.open_list()
        a = self.show(self.flac)
        self.insp.show_album(a, self.ov.plan)
        self.insp.set_cover(self.gui.inspector.stage_cover(buf.getvalue(), "t.png"), a)
        self.settle_edits()
        art = a["artwork"]
        for p in {art.get("front"), art.get("embed")} - {None}:
            Path(p).unlink()
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.save()
        warned.assert_called_once()
        self.assertIn("cover image you chose is gone", warned.call_args.args[2])
        self.assertNotIn("rules", warned.call_args.args[2])

    def test_taking_out_while_work_runs_keeps_the_edits_of_albums_left(self):    # (5)
        self.open_list()
        self.edit_title()
        a = self.album_of(self.mp3)
        self.ov.running = True
        try:
            self.ov._take_out([a])
        finally:
            self.ov.running = False
        self.assertIn(a, self.ov.plan["albums"])
        self.assertIn(_os.path.realpath(str(self.mp3)), self.insp.edits)


class PlainPlan(unittest.TestCase):
    """plan.plain_plan / editable / plain_pending without the app."""

    def test_only_listed_albums_with_work_are_saved(self):
        import plan as planmod
        listed = {"source_dir": "/m/A", "status": "listed", "files": [], "album_files": ["/m/A/1.flac"]}
        self.assertFalse(planmod.editable(listed))                     # (no files known yet)
        planmod.plain_files(listed)
        self.assertTrue(planmod.editable(listed))
        self.assertFalse(planmod.plain_pending(listed))
        listed["edited"] = {"tags": {"genre": ["Jazz"]}, "file_tags": {}, "cover": None}
        listed["files"][0].update(op="tag", changes={"genre": ["Jazz"]}, fingerprint={"size": 1, "mtime_ns": 1})
        self.assertTrue(planmod.plain_pending(listed))
        ready = dict(listed, status="ready")
        p = planmod.plain_plan([listed, ready], "j1", "Auto")
        self.assertEqual([a["source_dir"] for a in p["albums"]], ["/m/A"])
        a = p["albums"][0]
        self.assertEqual((a["status"], a["companions"], a["files"][0]["dst"], p["operation"]),
                         ("ready", [], "/m/A/1.flac", "in_place"))
        self.assertEqual(p["roots"], [_os.path.realpath("/m/A")])


if __name__ == "__main__":
    unittest.main()
