# Picard's small dialogs (gui/dialogs), offscreen: Tags from File Names (Picard's matcher, the
# preview, the tags handed on as your edits and written only by Apply - then undone), File Info
# (format, tags as stored, pictures, read on a worker), the log viewer (levels, search, follow,
# rotation) and a job's details with Undo. Generated audio in temporary folders; no network.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import io
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None


def wait(app, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def idle(app, *owners):
    """Wait until the owners' worker threads have ended (a dialog dropped while one runs would
    take a running QThread with it)."""
    from gui.work import _alive
    return wait(app, lambda: not any(_alive(t) for o in owners for t, _w in getattr(o, "_threads", [])))


def flac(path, **tags):
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000", *meta,
                    str(path), "-y"], check=True)
    return path


def png(size=(30, 20)) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


@unittest.skipIf(QApplication is None or not shutil.which("ffmpeg"), "PySide6 or ffmpeg missing")
class Dialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        from gui import dialogs, sheets
        cls.gui, cls.dialogs, cls.sheets = gui, dialogs, sheets
        cls.app = QApplication.instance() or QApplication([])
        from PySide6.QtGui import QShortcut
        from PySide6.QtWidgets import QPushButton
        from gui import style
        cls.gui_style, cls.QShortcut, cls.QPushButton = style, QShortcut, QPushButton

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-dialogs-"))
        self.sheets.shown.clear()
        self.owners = []

    def tearDown(self):
        for o in self.owners:
            try:
                o.close()
            except RuntimeError:
                pass
        self.assertTrue(idle(self.app, *self.owners))
        # (deleted here, on the UI thread: left to the garbage collector, a dialog could be freed on
        # whatever thread runs it next - a worker's - and take the process down)
        import gc
        from PySide6.QtCore import QCoreApplication, QEvent
        for o in self.owners:
            try:
                o.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.owners.clear()
        gc.collect()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def album(self, name="Moss - Ferns", n=2):
        d = self.tmp / name
        d.mkdir()
        return [flac(d / f"0{i} Song_{i}.flac", title=f"old{i}") for i in range(1, n + 1)]

    def plan_of(self, files, status=None):
        import plan as pl
        import profiles as pf
        prop = pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], status="unresolved",
                           tier="unresolved", reason="not found")
        p = pl.build([prop], pf.default())
        if status:
            p["albums"][0]["status"] = status
        return p

    # ---- Tags from File Names: Picard's matcher -------------------------------
    def test_matcher_follows_picard(self):
        T = self.dialogs.TagMatchExpression
        e = T("%artist% - %album%/%tracknumber% %title%")
        self.assertEqual(e.matched_tags, ["artist", "album", "tracknumber", "title"])
        self.assertEqual(e.match_file("/music/Moss - Ferns/007 Green Song.flac"),
                         {"artist": ["Moss"], "album": ["Ferns"], "tracknumber": ["7"], "title": ["Green Song"]})
        self.assertEqual(e.match_file("/music/Moss/Ferns/Green Song.flac"), {})      # (no number: no match)
        u = T("%artist%/%title%", replace_underscores=True)
        self.assertEqual(u.match_file("x/Big_Moss/A_B.mp3"), {"artist": ["Big Moss"], "title": ["A B"]})
        d = T("%date% %title%")
        self.assertEqual(d.match_file("/x/1999-05 Song.ogg")["date"], ["1999-05"])
        self.assertEqual(T("%artist% - %_hidden%").match_file("/a/B - c.flac"),
                         {"artist": ["B"], "~hidden": ["c"]})
        got = self.dialogs.parse(["/a/B - c.flac", "/a/nothing.flac"], "%artist% - %_hidden%")
        self.assertEqual(got, {"/a/B - c.flac": {"artist": ["B"]}})                  # (no ~variable is a tag)
        self.assertIn("%artist% - %album%/%tracknumber% %title%", self.dialogs.PRESETS)

    def test_preview_table_and_use_these_tags(self):
        files = [str(f) for f in self.album()] + [str(self.tmp / "loose.flac")]
        got, sent = [], []
        dlg = self.dialogs.TagsFromFileNames(None, files, on_apply=got.append)
        dlg.chosen.connect(sent.append)
        self.owners.append(dlg)
        self.assertEqual(dlg.windowModality(), Qt.WindowModal)
        self.assertIn("%artist%", dlg.fields.text())                   # (the fields: a caption under Pattern)
        self.assertIn("replace the preview's values", dlg.replaces.text())
        self.assertIn("Undo Edit (Ctrl+Z)", dlg.replaces.text())
        dlg.pattern.setEditText("%artist% - %album%/%tracknumber% %title%")
        dlg.preview()
        heads = [dlg.table.horizontalHeaderItem(c).text() for c in range(dlg.table.columnCount())]
        self.assertEqual(heads, ["File", "Artist", "Album", "Track Number", "Title"])
        self.assertEqual(dlg.table.rowCount(), 3)
        self.assertEqual(dlg.table.item(0, 4).text(), "Song_1")
        self.assertEqual(dlg.table.item(2, 1).text(), "Does not match the pattern")
        self.assertEqual(dlg.summary.text(), "2 files of 3 match.")
        self.assertEqual(dlg.table.item(0, 0).text(), "/".join(Path(files[0]).parts[-2:]))   # (what the pattern reads)
        self.assertEqual(dlg.table.item(0, 0).toolTip(), files[0])
        self.assertEqual(dlg.pattern.lineEdit().font().family(), self.gui_style.mono().family())
        dlg.underscores.setChecked(True)
        self.assertEqual(dlg.table.item(0, 4).text(), "Song 1")
        self.assertTrue(dlg.ok.isEnabled())
        dlg.ok.click()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][files[1]], {"artist": ["Moss"], "album": ["Ferns"], "tracknumber": ["2"],
                                            "title": ["Song 2"]})
        self.assertNotIn(files[2], got[0])
        self.assertEqual(sent, got)                                 # (chosen(dict) says the same)
        prefs = self.gui.common._gui_prefs()                       # (the pattern is offered again next time)
        self.assertEqual(prefs["tags_from_filenames_format"], "%artist% - %album%/%tracknumber% %title%")
        again = self.dialogs.TagsFromFileNames(None, files)
        self.owners.append(again)
        self.assertEqual(again.text(), "%artist% - %album%/%tracknumber% %title%")
        self.assertTrue(again.underscores.isChecked())
        again.pattern.setEditText("no fields here")
        again.preview()
        self.assertFalse(again.ok.isEnabled())
        self.assertIn("names no field", again.summary.text())

    def test_show_offscreen_never_blocks(self):
        files = [str(f) for f in self.album()]
        self.owners.append(self.dialogs.show_tags_from_filenames(None, files))
        self.assertIsNotNone(self.owners[-1])
        self.assertEqual(self.sheets.shown[-1][0], "tags_from_filenames")
        self.assertIsNone(self.dialogs.show_tags_from_filenames(None, []))
        self.assertEqual(self.sheets.shown[-1][:2], ("information", "Select an album first."))

    def test_tags_become_edits_written_only_by_apply_then_undone(self):
        import executor
        import tagio
        files = self.album()
        p = self.plan_of(files)
        over = self.dialogs.parse([str(f) for f in files], "%artist% - %album%/%tracknumber% %title%", True)
        before = [f.read_bytes() for f in files]
        n, left, edits = self.dialogs.apply_to_plan(p, over)
        self.assertEqual((n, left), (1, []))
        self.assertEqual([f.read_bytes() for f in files], before)   # (nothing written yet)
        key = _os.path.realpath(str(files[0].parent))
        self.assertEqual(len(edits[key]), 1)                         # ({folder: [edit, ...]}: the app's store)
        self.assertEqual(edits[key][0]["file_tags"][_os.path.realpath(str(files[0]))]["title"], ["Song 1"])
        fe = p["albums"][0]["files"][0]
        self.assertEqual(fe["op"], "tag")
        self.assertEqual(fe["changes"]["title"], ["Song 1"])
        row = next(r for r in fe["diff"] if r["field"] == "title")
        self.assertEqual((row["before"], row["after"], row.get("by")), (["old1"], ["Song 1"], "you"))
        # applied: staged, verified and journaled by the executor
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual([tagio.read(f)["title"] for f in files], [["Song 1"], ["Song 2"]])
        self.assertEqual(tagio.read(files[1])["artist"], ["Moss"])
        d = self.dialogs.job_details(p["job"])
        self.assertTrue(d["undoable"])
        self.assertEqual(len(d["albums"]), 1)
        self.assertIn(("title", "old1", "Song 1"), d["albums"][0]["files"][0]["changes"])
        # undone from the job's details sheet, through executor.undo, on a worker
        dlg = self.dialogs.JobDetails(None, p["job"])
        self.owners.append(dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        self.assertTrue(dlg.undo_btn.isEnabled())
        item = dlg.tree.topLevelItem(0).child(0)
        rows = [tuple(item.child(i).text(c) for c in range(3)) for i in range(item.childCount())]
        from gui.tagnames import display
        self.assertIn((display("title"), "old1", "Song 1"), rows)
        with mock.patch.object(self.sheets, "confirm", return_value=True) as conf:
            dlg.undo_btn.click()
        self.assertIn("Undo", conf.call_args[0][1])
        self.assertTrue(conf.call_args[1]["destructive"])
        self.assertTrue(wait(self.app, lambda: tagio.read(files[0])["title"] == ["old1"] and dlg.data
                             and dlg.data["undo"], 30))
        self.assertEqual(tagio.read(files[1])["title"], ["old2"])
        self.assertTrue(wait(self.app, lambda: not dlg.undo_btn.isEnabled() and dlg.data["status"] == "undone"))
        self.assertIn("already undone", dlg.undo_btn.toolTip())

    def test_apply_to_plan_leaves_out_what_cannot_be_written(self):
        files = self.album()
        p = self.plan_of(files, status="blocked")
        over = {str(files[0]): {"title": ["X"]}, str(self.tmp / "elsewhere.flac"): {"title": ["Y"]}}
        n, left, edits = self.dialogs.apply_to_plan(p, over)
        self.assertEqual((n, edits), (0, {}))
        self.assertEqual(sorted(w.split(" ")[0] for _f, w in left), ["its", "not"])
        self.assertNotIn("title", p["albums"][0]["files"][0]["changes"])
        # an edit made before (in the inspector) stays beside the file name's tags
        p = self.plan_of(files)
        real0 = _os.path.realpath(str(files[0]))
        p["albums"][0]["edited"] = {"tags": {"genre": ["Folk"]},
                                    "file_tags": {real0: {"title": ["Old"], "genre": ["Rock"]}}, "cover": None}
        import copy
        kept = copy.deepcopy(p["albums"][0]["edited"])
        e = self.dialogs.merged_edit(p["albums"][0], {str(files[0]): {"title": ["X"]}})
        self.assertEqual(e["tags"], {"genre": ["Folk"]})
        self.assertEqual(e["file_tags"], {real0: {"title": ["X"], "genre": ["Rock"]}})   # (the file name wins)
        self.assertEqual(p["albums"][0]["edited"], kept)                # (a new dict: the old edit as it was)
        self.assertEqual(p["albums"][0]["edited"]["file_tags"][real0]["title"], ["Old"])
        # an older store, one edit per folder: made a list, the new album's edit beside it
        key = _os.path.realpath(str(files[0].parent))
        old = {"files": [str(self.tmp / "other.flac")], "tags": {}, "file_tags": {}, "cover": None}
        store = self.dialogs.apply_to_plan(self.plan_of(files), {str(files[0]): {"title": ["X"]}}, {key: old})[2]
        self.assertIsInstance(store[key], list)
        self.assertEqual(len(store[key]), 2)
        self.assertIs(store[key][0], old)

    def test_merged_edit_keeps_the_keys_through_a_symlink(self):
        files = self.album()
        link = self.tmp / "link"
        link.symlink_to(files[0].parent, target_is_directory=True)
        srcs = [str(link / f.name) for f in files]
        entry = {"files": [{"src": s} for s in srcs]}
        e = self.dialogs.merged_edit(entry, {srcs[0]: {"title": ["X"]}}, {"files": [], "tags": {}, "file_tags": {}})
        self.assertEqual(list(e["file_tags"]), [srcs[0]])               # (the plan's src, as the inspector keys it)
        real1 = _os.path.realpath(srcs[1])
        e = self.dialogs.merged_edit(entry, {srcs[0]: {"title": ["Y"]}, srcs[1]: {"title": ["Z"]}},
                                     {"files": [], "tags": {}, "file_tags": {srcs[0]: {"genre": ["Folk"]},
                                                                             real1: {"genre": ["Rock"]}}})
        self.assertEqual(e["file_tags"], {srcs[0]: {"genre": ["Folk"], "title": ["Y"]},
                                          real1: {"genre": ["Rock"], "title": ["Z"]}})   # (one entry per file)

    def test_apply_in_background_swaps_copies_in_on_the_ui_thread(self):
        import copy
        import plan as pl
        files = self.album()
        p = self.plan_of(files)
        before = copy.deepcopy(p)
        key = _os.path.realpath(str(files[0].parent))
        mine = {"files": [str(f) for f in files], "tags": {"genre": ["Folk"]}, "file_tags": {}, "cover": None}
        store = {key: [mine]}                                        # (the inspector's edit of the album)
        held, got = [], []
        T = self.dialogs.tags_from_filenames
        with mock.patch("gui.work.start", lambda owner, fn, done, failed=None: held.append((fn, done))):
            T.apply_in_background(self.app, p, {str(files[0]): {"title": ["X"]}},
                                  lambda plan, res: got.append(res), store=store)
        self.assertEqual(len(held), 1)
        self.assertEqual(p, before)                                  # (the plan on screen: untouched so far)
        self.assertIs(store[key][0], mine)                           # (the same edit, now with the file's tags)
        self.assertEqual(mine["file_tags"][_os.path.realpath(str(files[0]))], {"title": ["X"]})
        self.assertEqual(mine["tags"], {"genre": ["Folk"]})
        news = held[0][0](None)                                      # (the worker's part)
        self.assertEqual(p, before)
        held[0][1](news)                                             # (done, on the UI thread)
        self.assertEqual(p["albums"][0]["files"][0]["changes"]["title"], ["X"])
        self.assertEqual(p["albums"][0]["files"][1]["changes"]["genre"], ["Folk"])
        self.assertEqual(got[0][:2], (1, []))
        self.assertIs(got[0][2], store)
        # a result overtaken by a later one is dropped
        with mock.patch("gui.work.start", lambda owner, fn, done, failed=None: held.append((fn, done))):
            T.apply_in_background(self.app, p, {str(files[1]): {"title": ["Y"]}}, lambda *a: None, store=store)
            T.apply_in_background(self.app, p, {str(files[1]): {"title": ["Z"]}}, lambda *a: None, store=store)
        late, first = held[2], held[1]
        late[1](late[0](None))
        first[1](first[0](None))
        self.assertEqual(p["albums"][0]["files"][1]["changes"]["title"], ["Z"])
        # handed to the inspector's reapply: each album goes through it, at once
        asked = []
        T.apply_in_background(self.app, p, {str(files[0]): {"artist": ["Moss"]}}, lambda *a: got.append(a),
                              store=store, reapply=asked.append)
        self.assertEqual(asked, [p["albums"][0]])
        self.assertEqual(pl.edit_for(store, key, files)["file_tags"][_os.path.realpath(str(files[0]))],
                         {"title": ["X"], "artist": ["Moss"]})
        # and on a real worker
        got.clear()
        T.apply_in_background(self.app, p, {str(files[0]): {"title": ["W"]}}, lambda plan, res: got.append(res),
                              store=store)
        self.assertTrue(wait(self.app, lambda: got))
        self.assertTrue(idle(self.app, self.app))
        self.assertEqual(p["albums"][0]["files"][0]["changes"]["title"], ["W"])

    def test_apply_in_background_recounts_fails_and_gives_way(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QWidget
        files = self.album()
        T = self.dialogs.tags_from_filenames
        held = []

        def capture(owner, fn, done, failed=None):
            held.append((fn, done, failed))
        # an unchanged album made ready: the summary is counted again
        p = self.plan_of(files, status="unchanged")
        p["summary"] = {"unchanged": 1}
        with mock.patch("gui.work.start", capture):
            T.apply_in_background(self.app, p, {str(files[0]): {"title": ["X"]}}, lambda *a: None)
        held[0][1](held[0][0](None))
        self.assertEqual(p["albums"][0]["status"], "ready")
        self.assertEqual(p["summary"], {"ready": 1})
        # it fails: a warning, and its album is no longer waited for
        album = p["albums"][0]
        with mock.patch("gui.work.start", capture):
            T.apply_in_background(self.app, p, {str(files[0]): {"title": ["Y"]}}, lambda *a: None)
        self.assertIn(id(album), T._WANTED)
        held[1][2]("boom\n\ndetail")
        self.assertEqual(self.sheets.shown[-1][:2], ("warning", "The tags from the file names could not be put "
                                                                "into the preview."))
        self.assertNotIn(id(album), T._WANTED)
        # the inspector's reapply after it: the earlier result on its way is dropped
        with mock.patch("gui.work.start", capture):
            T.apply_in_background(self.app, p, {str(files[0]): {"title": ["Old"]}}, lambda *a: None)
        T.apply_in_background(self.app, p, {str(files[0]): {"title": ["New"]}}, lambda *a: None,
                              reapply=lambda a: None)
        held[2][1](held[2][0](None))
        self.assertIs(p["albums"][0], album)
        self.assertEqual(album["files"][0]["changes"]["title"], ["X"])     # (not replaced by the older one)
        # while the Overview applies (or once its plan is another), nothing is swapped in; done still runs
        win = QWidget()
        self.owners.append(win)
        win.pages = {"Overview": SimpleNamespace(running=True, plan=p)}
        for ov, busy in ((SimpleNamespace(running=True, plan=p), None),
                         (SimpleNamespace(running=False, plan={}), None),
                         (SimpleNamespace(running=False, plan=p), lambda: True)):
            win.pages["Overview"] = ov
            got = []
            with mock.patch("gui.work.start", capture):
                T.apply_in_background(win, p, {str(files[0]): {"title": ["Late"]}},
                                      lambda plan, res: got.append(res), busy=busy)
            held[-1][1](held[-1][0](None))
            self.assertIs(p["albums"][0], album)
            self.assertEqual(album["files"][0]["changes"]["title"], ["X"])
            self.assertEqual(got[0][0], 1)
        win.pages["Overview"] = SimpleNamespace(running=False, plan=p)   # (idle, the same plan: swapped in)
        with mock.patch("gui.work.start", capture):
            T.apply_in_background(win, p, {str(files[0]): {"title": ["Now"]}}, lambda *a: None)
        held[-1][1](held[-1][0](None))
        self.assertEqual(album["files"][0]["changes"]["title"], ["Now"])

    def test_two_albums_in_one_folder_keep_their_own_edits(self):
        import plan as pl
        import profiles as pf
        files = self.album(n=4)
        src = str(files[0].parent)
        props = [pl.Proposal(source_dir=src, files=[str(f) for f in files[:2]], status="unresolved",
                             tier="unresolved", reason="not found"),
                 pl.Proposal(source_dir=src, files=[str(f) for f in files[2:]], status="unresolved",
                             tier="unresolved", reason="not found")]
        p = pl.build(props, pf.default())
        over = self.dialogs.parse([str(f) for f in files], "%artist% - %album%/%tracknumber% %title%", True)
        n, left, edits = self.dialogs.apply_to_plan(p, over)
        self.assertEqual((n, left), (2, []))
        key = _os.path.realpath(src)
        self.assertEqual(len(edits[key]), 2)
        for part in (files[:2], files[2:]):
            e = pl.edit_for(edits, key, part)
            self.assertEqual(sorted(e["file_tags"]), sorted(_os.path.realpath(str(f)) for f in part))
            for f in part:
                self.assertEqual(e["file_tags"][_os.path.realpath(str(f))]["title"],
                                 [f"Song {f.name[1]}"])
        # again: the same two edits are updated, none added
        again = self.dialogs.apply_to_plan(p, {str(files[3]): {"genre": ["Folk"]}}, edits)[2]
        self.assertIs(again, edits)
        self.assertEqual(len(edits[key]), 2)
        e = pl.edit_for(edits, key, files[2:])
        self.assertEqual(e["file_tags"][_os.path.realpath(str(files[3]))]["genre"], ["Folk"])
        self.assertEqual(e["file_tags"][_os.path.realpath(str(files[3]))]["title"], ["Song 4"])

    # ---- File Info ------------------------------------------------------------
    def test_file_info_reads_format_tags_and_pictures(self):
        from mutagen.flac import FLAC, Picture
        f = flac(self.tmp / "a.flac", title="Green", artist="Moss")
        m = FLAC(f)
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, "image/png", "front", png((30, 20))
        m.add_picture(pic)
        m.save()
        d = self.dialogs.read_info(f)
        self.assertEqual((d["format"], d["codec"], d["sample_rate"], d["channels"], d["error"]),
                         ("FLAC", "FLAC", 8000, 1, ""))
        self.assertEqual(d["bits"], 16)
        self.assertAlmostEqual(d["length"], 1.0, places=1)
        self.assertIn(("TITLE", "Green"), [(k.upper(), v) for k, v in d["tags"]])
        self.assertEqual(len(d["pictures"]), 1)
        p = d["pictures"][0]
        self.assertEqual((p["type"], p["mime"], p["dimensions"], p["description"]), ("Front cover", "image/png",
                                                                                      (30, 20), "front"))
        rows = dict(self.dialogs.info.info_rows(d))
        self.assertEqual(rows["Channels"], "1 (mono)")
        self.assertEqual(rows["Length"], "0:01")
        self.assertIn("bytes", rows["File size"])
        self.assertEqual(rows["Bit depth"], "16 bits")
        # an MP3 with an ID3 picture: its frames by their raw keys
        mp3 = self.tmp / "b.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                        "-metadata", "title=Blue", str(mp3), "-y"], check=True)
        from mutagen.id3 import APIC, ID3
        t = ID3(mp3)
        t.add(APIC(encoding=3, mime="image/png", type=4, desc="back", data=png((8, 8))))
        t.save()
        d = self.dialogs.read_info(mp3)
        self.assertEqual(d["format"], "MP3")
        self.assertTrue(d["codec"].startswith("MPEG-1 Layer 3"))
        self.assertIn(("TIT2", "Blue"), d["tags"])
        self.assertTrue(any(k.startswith("APIC") and v == "(picture)" for k, v in d["tags"]))
        self.assertEqual((d["pictures"][0]["type"], d["pictures"][0]["dimensions"]), ("Back cover", (8, 8)))
        self.assertRegex(dict(self.dialogs.info.info_rows(d))["Bitrate"], r"^\d+ kbps$")
        # a WAV: its RIFF INFO entries (ffmpeg writes title as INAM)
        wav = self.tmp / "c.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000",
                        "-metadata", "title=X", str(wav), "-y"], check=True)
        d = self.dialogs.read_info(wav)
        self.assertEqual(d["error"], "")
        self.assertIn(("INFO title", "X"), d["tags"])
        # the sheet: read on a worker, then three tabs
        before = f.read_bytes()
        dlg = self.dialogs.FileInfo(None, f)
        self.owners.append(dlg)
        self.assertEqual(dlg.windowModality(), Qt.WindowModal)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        self.assertIs(dlg.stack.currentWidget(), dlg.tabs)
        self.assertEqual(dlg.tabs.tabText(2), "Pictures (1)")
        self.assertEqual(dlg.pics.item(0, 3).text(), "30 × 20")
        self.assertFalse(dlg.pics.item(0, 0).icon().isNull())
        self.assertGreater(dlg.tags.rowCount(), 1)
        self.assertIn("kHz", dlg.hint.text())                        # (the format at a glance, not the folder again)
        self.assertNotIn(str(Path(f).parent), dlg.hint.text())
        self.assertLessEqual(dlg.height(), self.dialogs.info.MAX_HEIGHT)
        self.assertEqual(self.dialogs.info.summary({"format": "WAV", "sample_rate": 44100, "bits": 16, "channels": 2,
                                                    "length": 1.0}), "WAV · 44.1 kHz · 16-bit · stereo · 0:01")
        self.assertEqual(f.read_bytes(), before)                     # (read-only)
        self.owners.append(self.dialogs.show_file_info(None, f))
        self.assertEqual(self.sheets.shown[-1][0], "file_info")

    def test_file_info_of_a_file_it_cannot_read(self):
        bad = self.tmp / "not.flac"
        bad.write_bytes(b"nothing here")
        d = self.dialogs.read_info(bad)
        self.assertTrue(d["error"])
        dlg = self.dialogs.FileInfo(None, bad)
        self.owners.append(dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        self.assertIs(dlg.stack.currentWidget(), dlg.status)
        self.assertIn("cannot be read", dlg.status.text())

    # ---- the log ------------------------------------------------------------
    def log(self):
        import logsetup
        d = logsetup.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "tagmender.log"

    def test_read_log_entries_and_filters(self):
        L = self.dialogs.logs
        p = self.tmp / "t.log"
        p.write_text("2026-10-24 09:12:03,114 INFO  job=- a provider=-  hello\n"
                     "2026-10-24 09:12:04,114 ERROR job=- a provider=-  broke\nTraceback (most recent call last):\n"
                     "2026-10-24 09:12:05,114 DEBUG job=- a provider=-  detail\n")
        r = L.read_log(p)
        items = L.entries(r["text"])
        self.assertEqual([lv for lv, _ in items], ["INFO", "ERROR", "ERROR", "DEBUG"])
        self.assertEqual(len(L.shown(items, "WARNING")), 2)                 # (the traceback goes with its error)
        self.assertEqual(L.shown(items, "DEBUG", "HELLO"), [items[0][1]])
        with open(p, "a") as fh:
            fh.write("2026-10-24 09:12:06,114 INFO  job=- a provider=-  more\npartial")
        r2 = L.read_log(p, r["end"])
        self.assertFalse(r2["whole"])
        self.assertTrue(r2["text"].endswith("more\n"))                      # (a line being written waits)
        with open(p, "a") as fh:
            fh.write(" still")
        r3 = L.read_log(p, r2["end"])                                       # (only half a line so far)
        self.assertEqual((r3["text"], r3["end"]), ("", r2["end"]))
        p.write_text("2026-10-24 09:13:00,000 INFO  job=- a provider=-  new\n")   # (rotated: shorter)
        self.assertTrue(L.read_log(p, r2["end"])["whole"])
        half = self.tmp / "half.log"                                     # (read whole mid-line)
        half.write_text("2026-10-24 09:12:03,114 ERROR job=- a provider=-  one\n"
                        "2026-10-24 09:12:04,114 INFO  job=- a provider=-  tw")
        w = L.read_log(half)
        self.assertTrue(w["whole"])
        self.assertTrue(w["text"].endswith("one\n"))
        with open(half, "a") as fh:
            fh.write("o\n")
        w2 = L.read_log(half, w["end"])
        self.assertEqual(L.entries(w2["text"], "ERROR"), [("INFO", "2026-10-24 09:12:04,114 INFO  job=- a "
                                                                   "provider=-  two")])
        big = self.tmp / "big.log"
        big.write_text("2026-10-24 09:12:03,114 INFO  x\n" * 1000)
        cut = L.read_log(big, tail=1000)
        self.assertTrue(cut["cut"])
        self.assertTrue(cut["text"].startswith("2026-"))
        self.assertTrue(L.read_log(self.tmp / "none.log")["missing"])

    def test_log_viewer_levels_search_follow_and_folder(self):
        log = self.log()
        log.write_text("2026-10-24 09:12:03,114 INFO  job=- gui provider=-  started\n"
                       "2026-10-24 09:12:04,114 DEBUG job=- gui provider=-  noise\n"
                       "2026-10-24 09:12:05,114 WARNING job=- gui provider=-  careful\n")
        v = self.dialogs.LogViewer()
        self.owners.append(v)
        self.assertFalse(v.isModal())
        self.assertTrue(wait(self.app, lambda: len(v.items) == 3))
        self.assertEqual(v.text.toPlainText().count("\n"), 1)              # (Information and above: 2 lines)
        v.level.setCurrentIndex(0)
        self.assertIn("noise", v.text.toPlainText())
        v.level.setCurrentIndex(2)
        self.assertEqual(v.text.toPlainText().strip().splitlines()[0][-7:], "careful")
        v.level.setCurrentIndex(0)
        v.search.setText("STARTED")
        self.assertTrue(wait(self.app, lambda: v.text.toPlainText().strip().endswith("started")))
        v.search.clear()
        self.assertTrue(wait(self.app, lambda: "noise" in v.text.toPlainText()))
        self.assertTrue(v.timer.isActive())
        with open(log, "a") as fh:
            fh.write("2026-10-24 09:13:00,000 ERROR job=- gui provider=-  it broke\n")
        self.assertTrue(wait(self.app, lambda: "it broke" in v.text.toPlainText(), 10))
        self.assertIn("4 of 4 lines shown", v.caption.text())
        log.rename(log.with_name("tagmender.log.1"))                    # (rotated, as logging does it: read
        old_end = v.end                                                  #  again whole)
        fresh = [f"2026-10-24 09:14:0{i},000 INFO  job=- gui provider=-  fresh start {i}" for i in range(6)]
        log.write_text("\n".join(fresh) + "\n")
        self.assertGreater(log.stat().st_size, old_end)              # (longer: only the new inode tells)
        self.assertTrue(wait(self.app, lambda: len(v.items) == len(fresh)
                             and v.items[-1][1].endswith("fresh start 5"), 10))
        self.assertEqual(v.items[0][1], fresh[0])
        v.follow.setChecked(False)
        self.assertFalse(v.timer.isActive())
        # a read overtaken by a later one is dropped (a follow read landing after Reload)
        held = []
        shown_now = list(v.items)
        with mock.patch.object(self.dialogs.logs, "start", lambda o, fn, done, failed=None: held.append((fn, done,
                                                                                                    failed))):
            v.read(v.end)
            v.reload()
        stale = {"text": "2026-10-24 09:15:00,000 INFO  job=- gui provider=-  stale\n", "end": 10 ** 6,
                 "ino": 1, "cut": False, "missing": False, "whole": False}
        held[1][1](held[1][0](None))
        end = v.end
        held[0][1](stale)
        held[0][2]("broken")
        self.assertEqual(v.items, shown_now)
        self.assertEqual(v.end, end)
        self.assertNotIn("cannot be read", v.caption.text())
        with mock.patch.object(self.dialogs.logs, "open_folder") as op:
            v.open_btn.click()
        op.assert_called_once_with(log.parent)
        v.close()
        log.unlink()
        w = self.dialogs.LogViewer()
        self.owners.append(w)
        self.assertTrue(wait(self.app, lambda: not w.reading))
        self.assertIn("No log yet", w.text.placeholderText())
        w.close()

    def test_log_viewer_keys_and_a_search_that_matches_nothing(self):
        log = self.log()
        log.write_text("2026-10-24 09:12:03,114 INFO  job=- gui provider=-  started\n")
        v = self.dialogs.LogViewer()
        self.owners.append(v)
        self.assertTrue(wait(self.app, lambda: len(v.items) == 1))
        close = next(b for b in v.findChildren(self.QPushButton) if b.text() == "Close")
        self.assertNotEqual(close.property("role"), "primary")              # (not the log's main action)
        self.assertFalse(close.isDefault())
        v.search.setText("nowhere")
        v.render()
        self.assertEqual(v.text.toPlainText(), "")
        self.assertIn("No lines match “nowhere”", v.text.placeholderText())
        keys = {sc.key().toString() for sc in v.findChildren(self.QShortcut)}
        self.assertTrue({"Ctrl+F", "Ctrl+W", "Ctrl+R"} <= keys, keys)
        self.assertEqual(v.reload_btn.text(), "Reload")                       # (no underlined letters, as sheets)
        self.assertFalse(any("&" in b.text() for b in v.findChildren(self.QPushButton)))
        v.resize(420, 300)
        v.fit_caption()
        self.assertEqual(v.caption.toolTip(), str(v.path()))                   # (the whole path, the caption fits)
        self.assertLessEqual(v.caption.fontMetrics().horizontalAdvance(v.caption.text()), v.caption.width() + 4)
        v.close()

    def test_job_details_says_where_each_file_went_and_why_an_album_failed(self):
        d = {"job": "20261031-101500-ab12", "profile": "Library", "started": 1793441700.0, "status": "done",
             "undoable": True, "why": "",
             "albums": [{"source": "/m/A/Album", "status": "done", "detail": "", "other": [],
                         "files": [{"src": "/m/A/Album/01 x.flac", "dst": "/lib/A/Album (1998)/01 x.flac", "op": "move",
                                    "changes": [("album", "Old", "Album")]}]},
                        {"source": "/m/B/Other", "status": "failed", "detail": "could not write 05 y.flac: denied",
                         "files": [], "other": []}], "undo": []}
        with mock.patch.object(self.dialogs.logs, "job_details", return_value=d):
            dlg = self.dialogs.JobDetails(None, d["job"])
            self.owners.append(dlg)
            self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        self.assertEqual(dlg.head.property("role"), "title")
        import time as _t
        self.assertEqual(dlg.head.text(), "Job Details: " + _t.strftime("%-d %b %Y, %H:%M", _t.localtime(d["started"])))
        self.assertIn("Done · Library · 2 albums", dlg.hint.text())
        self.assertNotIn(d["job"], dlg.hint.text())                          # (the ID: a caption at the bottom)
        self.assertEqual(dlg.job_id.text(), f"Job {d['job']}")
        f = dlg.tree.topLevelItem(0).child(0)
        self.assertEqual([f.text(c) for c in range(3)], ["01 x.flac — moved", "A/Album", "A/Album (1998)"])
        self.assertEqual(f.toolTip(2), "/lib/A/Album (1998)/01 x.flac")
        bad = dlg.tree.topLevelItem(1)
        self.assertEqual(bad.text(0), "Other · Failed")                     # (the status after the name)
        self.assertEqual(bad.text(2), "")
        self.assertFalse(bad.icon(0).isNull())
        why = bad.child(bad.childCount() - 1)
        self.assertEqual(why.text(0), "Could not write 05 y.flac: denied")
        self.assertTrue(why.isFirstColumnSpanned())

    def test_show_logs_one_per_window(self):
        from PySide6.QtWidgets import QMainWindow
        self.log().write_text("2026-10-24 09:12:03,114 INFO  job=- gui provider=-  hi\n")
        win = QMainWindow()
        a = self.dialogs.show_logs(win)
        self.owners.append(a)
        b = self.dialogs.show_logs(win)
        self.assertIs(a, b)
        self.assertTrue(wait(self.app, lambda: not a.reading))
        a.close()
        win.deleteLater()

    def test_job_details_hands_undo_to_the_jobs_page(self):
        import executor
        files = self.album()
        p = self.plan_of(files)
        executor.Executor(p).run()
        asked = []
        dlg = self.dialogs.JobDetails(None, p["job"], undo=asked.append)
        self.owners.append(dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        from gui.tagnames import display
        item = dlg.tree.topLevelItem(0).child(0)                     # (an unresolved album: its marker tag)
        rows = [tuple(item.child(i).text(c) for c in range(3)) for i in range(item.childCount())]
        self.assertIn((display("tagmender"), "(none)", "Unresolved"), rows)
        self.assertEqual(item.child(0).toolTip(0), "tagmender")
        dlg.undo_btn.click()
        self.assertEqual(asked, [p["job"]])
        self.assertTrue(self.dialogs.job_details(p["job"])["undoable"])
        with mock.patch.object(executor, "jobs", return_value=[]):   # (an old job: looked up by its ID)
            self.assertTrue(self.dialogs.job_details(p["job"])["undoable"])
        db = executor._db()
        for status, words in (("running", "still running"), ("interrupted", "interrupted")):
            db.execute("UPDATE jobs SET status=? WHERE job=?", (status, p["job"]))
            db.commit()
            d = self.dialogs.job_details(p["job"])
            self.assertFalse(d["undoable"])
            self.assertIn(words, d["why"])
            dlg = self.dialogs.JobDetails(None, p["job"])
            self.owners.append(dlg)
            self.assertTrue(wait(self.app, lambda: dlg.data is not None))
            self.assertFalse(dlg.undo_btn.isEnabled())
            self.assertIn(words, dlg.undo_btn.toolTip())
        db.execute("UPDATE jobs SET status='done' WHERE job=?", (p["job"],))
        db.commit()
        db.close()
        (executor.job_dir(p["job"]) / "PRUNED").touch()
        d = self.dialogs.job_details(p["job"])
        self.assertFalse(d["undoable"])
        self.assertIn("backups were removed", d["why"])
        (executor.job_dir(p["job"]) / "PRUNED").unlink()
        # no undo handed over: the window's Jobs page undoes it; without one, never beside an Apply
        from types import SimpleNamespace
        win = SimpleNamespace(pages={"Overview": SimpleNamespace(running=True),
                                     "Jobs": SimpleNamespace(undo=asked.append)})
        dlg = self.dialogs.JobDetails(None, p["job"])
        self.owners.append(dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        with mock.patch.object(self.dialogs.logs, "_pages", return_value=win.pages):
            dlg.undo_btn.click()
        self.assertEqual(asked, [p["job"]] * 2)
        del win.pages["Jobs"]
        dlg = self.dialogs.JobDetails(None, p["job"])
        self.owners.append(dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        with mock.patch.object(executor, "undo") as und, mock.patch.object(self.sheets, "confirm") as conf, \
                mock.patch.object(self.sheets, "information") as info, \
                mock.patch.object(self.dialogs.logs, "_pages", return_value=win.pages):
            dlg.undo_btn.click()
        und.assert_not_called()
        conf.assert_not_called()
        self.assertEqual(info.call_args[0][1], "Wait for the work in progress to finish.")
        # the window's pages, looked up for real: its Jobs page undoes it
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        self.owners.append(w)
        w.pages = {"Jobs": SimpleNamespace(undo=asked.append)}
        dlg = self.dialogs.JobDetails(w, p["job"])
        self.owners.insert(0, dlg)
        self.assertTrue(wait(self.app, lambda: dlg.data is not None))
        dlg.undo_btn.click()
        self.assertEqual(asked, [p["job"]] * 3)
        none = self.dialogs.job_details("20000101-000000-dead")
        self.assertEqual(none["albums"], [])
        self.assertFalse(none["undoable"])
        self.owners.append(self.dialogs.show_job_details(None, p["job"]))
        self.assertEqual(self.sheets.shown[-1][0], "job_details")


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
