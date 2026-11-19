# 0.8.77 an applied album is unmistakable, and Apply shows its progress (offscreen Qt, fake providers):
# - once an album's files are done its row in the albums pane has a filled check badge where its
#   include box was (no box to tick), reads "Applied · moved" (its files went to another folder) or
#   "Applied" (tagged in place), is on the applied wash in light and dark, and its status tooltip
#   names the folder it lives in now; a Ready row keeps its box, its dot and its word;
# - while its files are written a row reads "Applying…" with the loader icon;
# - the status bar's Apply progress shows while Apply runs, fills by files, ends "Applied N albums"
#   and hides; Cancel ends it "Stopped after N of M"; Save Tags shows it too.
# TAGMENDER_SHOTS877=<dir> saves screenshots of the rows and the bar, light and dark.
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
import profiles as pf  # noqa: E402
from test_gui import wait  # noqa: E402

SHOTS = os.environ.get("TAGMENDER_SHOTS877", "")


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V0877AppliedRows(tg.Gui):
    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.album("Band H", "Album H")
        self.http.dg[922] = te.dg_release(922, "Band H", "Album H")
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        pf.save(pf.from_dict({"name": "Mover", "operation": "move", "destination": str(self.dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Auto")
        p = mock.patch.object(self.ov.apply_bar, "LINGER", 50)    # (the end shown 50 ms, not 4 s)
        p.start()
        self.addCleanup(p.stop)

    def row(self, name):
        return next(a for a in self.ov.model.albums if name in a["source_dir"])

    def looked_up(self):
        ov = self.ov
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(self.row("Album G")["status"], "ready")
        self.assertEqual(self.row("Album H")["status"], "ready")

    def apply_only(self, *albums):
        ov = self.ov
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply_selected(list(albums))
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())

    def cell(self, album, col):
        t = self.ov.tree
        return t.index_of(t.node_of(album), col)

    def look(self, album):
        from PySide6.QtCore import Qt
        from gui.preview_panes import AlbumsModel
        t = self.ov.tree
        name, status = self.cell(album, AlbumsModel.NAME), self.cell(album, AlbumsModel.STATUS)
        return {"check": t.data(name, Qt.CheckStateRole), "checkable": bool(t.flags(name) & Qt.ItemIsUserCheckable),
                "badge": t.data(name, Qt.DecorationRole), "word": t.data(status, Qt.DisplayRole),
                "tip": t.data(status, Qt.ToolTipRole), "bg": t.data(name, Qt.BackgroundRole),
                "status_bg": t.data(status, Qt.BackgroundRole), "icon": t.data(status, Qt.DecorationRole)}

    def shot(self, name):
        if not SHOTS:
            return
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        self.app.processEvents()
        self.ov.panes.album_view.viewport().repaint()
        self.app.processEvents()
        self.win.grab().save(str(Path(SHOTS) / f"{name}.png"))

    def test_applied_albums_read_applied_with_their_badge_and_wash_in_place_or_moved(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from gui import icons, style
        self.looked_up()
        g, h = self.row("Album G"), self.row("Album H")
        before = self.look(h)
        self.assertEqual(before["word"], "Ready")
        self.assertTrue(before["checkable"])
        self.apply_only(g)
        self.assertEqual(g["status"], "done")
        a = self.look(g)
        self.assertIsNone(a["check"])                                  # (no include box: not clickable)
        self.assertFalse(a["checkable"])
        self.assertFalse(a["badge"].isNull())
        self.assertEqual(a["badge"].cacheKey(), icons.badge("circle-check", "applied", "on_applied", 16).cacheKey())
        self.assertEqual(a["word"], "Applied")                          # (Auto tags in place)
        self.assertTrue(self.ov.tree.data(self.cell(g, 0), Qt.AccessibleTextRole).endswith(", Applied"))
        self.assertIn(g["source_dir"], a["tip"])
        self.assertIn("in place", a["tip"])
        self.assertEqual(a["bg"], QColor(style.tokens(False)["applied_bg"]))
        self.assertEqual(a["status_bg"], a["bg"])                       # (the whole row)
        self.assertEqual(self.ov.tree.data(self.cell(g, 1), Qt.ForegroundRole), QColor(style.color("applied")))
        # the Ready row is as it was: its box, its word, its dot, no wash
        r = self.look(h)
        self.assertEqual(r["word"], "Ready")
        self.assertTrue(r["checkable"])
        self.assertIsNotNone(r["check"])
        self.assertIsNone(r["badge"])
        self.assertNotEqual(r["bg"], a["bg"])
        self.assertEqual(r["icon"].cacheKey(), icons.icon("circle-dot", "ready", 14).cacheKey())
        self.shot("applied_rows_light")
        # dark: the dark wash
        self.gui.style.apply(self.app, "dark")
        self.assertEqual(self.look(g)["bg"], QColor(style.tokens(True)["applied_bg"]))
        self.assertNotEqual(self.look(h)["bg"], QColor(style.tokens(True)["applied_bg"]))
        self.shot("applied_rows_dark")
        # Clear Done takes it out, as before
        self.gui.style.apply(self.app, "light")
        self.ov.clear_done()
        self.assertTrue(wait(lambda: all(x is not g for x in self.ov.model.albums), self.app))
        self.assertIn(h, self.ov.model.albums)
        # an album moved: "Applied · moved", its tooltip and name cell say where and what
        ov = self.ov
        ov.set_album_profile([h], "Mover")
        self.assertTrue(wait(lambda: not ov.running and self.row("Album H")["status"] == "ready", self.app),
                        ov.summary.text())
        h = self.row("Album H")
        self.assertTrue(all(f["dst"].startswith(str(self.dest)) for f in h["files"]), h["files"])
        self.apply_only(h)
        self.assertEqual(h["status"], "done")
        m = self.look(h)
        self.assertEqual(m["word"], "Applied · moved")
        self.assertIsNone(m["check"])
        self.assertIn(str(self.dest), m["tip"])
        self.assertNotIn(h["source_dir"], m["tip"])
        from gui.models import applied_folder
        self.assertTrue(os.path.isdir(applied_folder(h)), applied_folder(h))
        self.assertTrue(ov.tree.data(self.cell(h, 0), Qt.AccessibleTextRole).endswith(", Applied, moved"))

    def test_apply_shows_its_progress_and_each_album_applying(self):
        import executor
        from gui import icons
        from gui.preview_panes import AlbumsModel
        self.looked_up()
        ov, bar = self.ov, self.ov.apply_bar
        self.assertFalse(bar.isVisible())
        gate, step, held = threading.Event(), executor.Executor.file_step, []
        self.addCleanup(gate.set)

        def slow(e, i, j, fe, a, done):
            if i == 1 and j == 1:                       # (the second album's second file waits)
                held.append(True)
                gate.wait(30)
            return step(e, i, j, fe, a, done)
        with mock.patch.object(executor.Executor, "file_step", slow), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply_selected([self.row("Album G"), self.row("Album H")])
            self.assertTrue(bar.isVisible())
            self.assertTrue(wait(lambda: held, self.app, timeout=30))
            self.assertTrue(wait(lambda: "Applying 2 of 2 albums" in bar.text(), self.app, timeout=10), bar.text())
            self.assertIn("4 of 6 files", bar.text())               # (3 of the first, 1 of the second)
            self.assertGreater(bar.bar.value(), 0)
            self.assertLess(bar.bar.value(), bar.SCALE)
            second = ov.applied_albums()[1]
            st = ov.tree.index_of(ov.tree.node_of(second), AlbumsModel.STATUS)
            from PySide6.QtCore import Qt
            self.assertEqual(ov.tree.data(st, Qt.DisplayRole), "Applying…")
            self.assertEqual(ov.tree.data(st, Qt.DecorationRole).cacheKey(),
                             icons.icon("loader-circle", "accent", 14).cacheKey())
            self.assertNotIn("Applying", ov.summary.text())       # (the counts said once: by the bar)
            if SHOTS:
                self.shot("apply_progress_light")
                self.gui.style.apply(self.app, "dark")
                self.shot("apply_progress_dark")
                self.gui.style.apply(self.app, "light")
            gate.set()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(bar.bar.value(), bar.SCALE)
        self.assertEqual(bar.text(), "Applied 2 albums")
        self.assertTrue(wait(lambda: not bar.isVisible(), self.app, timeout=5))
        self.assertEqual(self.look(second)["word"], "Applied")

    def test_cancel_ends_the_progress_stopped_after(self):
        import executor
        self.looked_up()
        ov, bar = self.ov, self.ov.apply_bar
        gate, step, held = threading.Event(), executor.Executor.file_step, []
        self.addCleanup(gate.set)

        def slow(e, i, j, fe, a, done):
            if i == 0 and j == 2:
                held.append(True)
                gate.wait(30)
            return step(e, i, j, fe, a, done)
        with mock.patch.object(executor.Executor, "file_step", slow), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply_selected([self.row("Album G"), self.row("Album H")])
            self.assertTrue(wait(lambda: held, self.app, timeout=30))
            ov.cancel()
            gate.set()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(bar.text(), "Stopped after 1 of 2")


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V0877Bar(unittest.TestCase):
    """The bar and the tokens alone: no window."""
    @classmethod
    def setUpClass(cls):
        cls.app = tg.QApplication.instance() or tg.QApplication([])

    def test_the_bar_alone_counts_albums_and_failures(self):
        from gui import style
        from gui.widgets import ApplyProgress
        b = ApplyProgress()
        b.start([None, None, None], kind="save")
        self.assertEqual(b.text(), "Saving 1 of 3 albums")
        b.album_started(1)
        self.assertEqual(b.text(), "Saving 2 of 3 albums")
        self.assertGreater(b.bar.value(), 0)
        b.finish(2, 1)
        self.assertEqual(b.text(), "Saved 2 albums · 1 failed")
        self.assertIn(style.color("error"), b.words.text())
        self.assertEqual(b.bar.accessibleName(), "Save progress")
        # Apply: a failed album is not finished; the job failing ends "Failed after N of M"
        b.start([2, 2, 2])
        self.assertEqual(b.bar.accessibleName(), "Apply progress")
        b.album_started(0)
        b.album_done(0, ok=False)
        b.album_started(1)
        b.album_done(1)
        self.assertEqual(b.finished, 1)
        self.assertEqual(b.text(), "Applying 3 of 3 albums · 4 of 6 files")
        b.finish(0, error=True)
        self.assertEqual(b.text(), "Failed after 1 of 3")
        self.assertIn(style.color("error"), b.words.text())

    def test_the_applied_badge_reads_on_a_selected_row(self):
        from PySide6.QtGui import QIcon
        from gui import icons, style
        ic = icons.badge("circle-check", "applied", "on_applied", 16)
        img = ic.pixmap(16, 16, QIcon.Selected).toImage()
        disc = img.pixelColor(8, 3)                     # (the disc's top, inside its edge)
        self.assertGreaterEqual(style.contrast(disc.name(), style.tokens()["selection"]), 3.0)

    def test_the_applied_and_progress_tokens_exist_and_read_in_both_schemes(self):
        from gui import style
        for dark in (False, True):
            t = style.tokens(dark)
            for k in ("applied", "on_applied", "applied_bg", "progress_track"):
                self.assertIn(k, t)
            for ink in ("ink", "muted", "applied"):
                self.assertGreaterEqual(style.contrast(t[ink], t["applied_bg"]), 4.5, (dark, ink))
            self.assertGreaterEqual(style.contrast(t["on_applied"], t["applied"]), 4.5, dark)
            # (the applied green is not Ready's: a deeper one in light, a brighter one in dark)
            self.assertNotEqual(t["applied"].lower(), t["accent"].lower())
            self.assertNotEqual(t["applied"].lower(), t["identified"].lower())
        self.assertNotEqual(style.LIGHT["applied_bg"], style.DARK["applied_bg"])
        self.assertIn("applyProgress", style.stylesheet())


for _n in dir(tg.Gui):                          # (test_gui's own tests are run there, not again here)
    if _n.startswith("test_"):
        setattr(V0877AppliedRows, _n, None)


if __name__ == "__main__":
    unittest.main()
