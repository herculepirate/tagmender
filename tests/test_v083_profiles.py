# 0.8.3: a profile per album in the Overview (offscreen Qt, fake providers, generated audio).
# Several albums listed: each has a Profile drop-down in its row; by default the one chosen
# above, or another of your choice. Look Up plans each album with its own profile, and Apply
# runs one job per profile; a second Apply of albums left out of the first is a new job.
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
from test_gui import wait  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class ProfilePerAlbum(tg.Gui):
    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.d3 = self.album("Band H", "Album H")
        self.http.dg[922] = te.dg_release(922, "Band H", "Album H")
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        pf.save(pf.from_dict({"name": "Mover", "operation": "copy", "destination": str(self.dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Auto")

    def row(self, name):
        return next(a for a in self.ov.model.albums if name in a["source_dir"])

    def listed(self):
        ov = self.ov
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        self.assertEqual(len(ov.model.albums), 3)

    def test_the_profile_column_shows_each_albums_profile(self):
        from gui.models import PlanModel
        ov = self.ov
        self.listed()
        self.assertFalse(ov.table.isColumnHidden(PlanModel.PROFILE))
        g = ov.model.albums.index(self.row("Album G"))
        idx = ov.model.index(g, PlanModel.PROFILE)
        self.assertEqual(ov.model.data(idx), "Auto")                     # (the one chosen above)
        self.assertTrue(ov.model.flags(idx) & tg.Qt.ItemIsEditable)
        ov.model.setData(idx, "Mover")                                   # (its drop-down)
        self.assertEqual(ov.model.data(idx), "Mover")
        self.assertEqual(ov.model.data(idx, tg.Qt.EditRole), "Mover")
        self.assertFalse(ov.running)                                     # (listed: nothing looked up)
        ov.profile.setCurrentText("Mover")
        ov.profile.setCurrentText("Auto")
        self.assertEqual(ov.model.data(idx), "Mover")                    # (its own stays its own)
        ov.model.setData(idx, "")                                        # (As Above)
        self.assertEqual(ov.model.data(idx), "Auto")

    def test_each_album_is_looked_up_and_applied_with_its_own_profile(self):
        ov = self.ov
        self.listed()
        ov.set_album_profile([self.row("Album H")], "Mover")
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        h, g = self.row("Album H"), self.row("Album G")
        self.assertEqual((ov.planned_with[ov.album_key(h)], ov.planned_with[ov.album_key(g)]), ("Mover", "Auto"))
        self.assertTrue(all(f["dst"].startswith(str(self.dest)) for f in h["files"]), h["files"])
        self.assertTrue(all(f["dst"].startswith(str(self.d)) for f in g["files"]), g["files"])
        self.assertEqual(ov.plan_for([h])["profile"], "Mover")
        self.assertEqual(h["status"], "ready", (h["status"], h.get("conflicts"), h["identification"]))
        self.assertIn("profile Mover", ov.apply_words()[1])
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertIn("Jobs ", ov.summary.text())                        # (one job per profile)
        self.assertEqual(tagio.read(self.d / "01.flac")["discogs_release_id"], ["920"])
        copied = list(self.dest.rglob("*.flac"))
        self.assertEqual(len(copied), 3, copied)
        self.assertEqual(tagio.read(copied[0])["discogs_release_id"], ["922"])
        self.assertNotIn("discogs_release_id", tagio.read(self.d3 / "01.flac"))   # (copied, not tagged)

    def test_a_changed_profile_looks_the_album_up_again(self):
        ov = self.ov
        self.listed()
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app))
        h = self.row("Album H")
        self.assertEqual(ov.planned_with[ov.album_key(h)], "Auto")
        ov.set_album_profile([h], "Mover")
        self.assertTrue(wait(lambda: not ov.running, self.app))
        h = self.row("Album H")
        self.assertEqual(ov.planned_with[ov.album_key(h)], "Mover")
        self.assertTrue(all(f["dst"].startswith(str(self.dest)) for f in h["files"]))
        self.assertEqual(ov.stale_profiles(ov.to_apply()), [])

    def test_apply_refuses_a_preview_made_with_another_profile(self):
        ov = self.ov
        self.listed()
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app))
        ov.profile.setCurrentText("Mover")                               # (the albums follow it; not previewed)
        self.assertTrue(ov.stale_profiles(ov.to_apply()))
        with mock.patch.object(self.gui.sheets, "information") as told, \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True) as asked:
            ov.apply()
        told.assert_called_once()
        asked.assert_not_called()

    def test_albums_left_out_are_applied_later_as_a_new_job(self):
        ov = self.ov
        self.listed()
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app))
        g, h = self.row("Album G"), self.row("Album H")
        ov.model.set_left_out([h], True)
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
            first = ov.summary.text()
            ov.model.set_left_out([h], False)
            ov.apply()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertNotIn("already run", ov.summary.text())
        self.assertEqual(h["status"], "done", ov.summary.text())
        self.assertEqual(tagio.read(self.d3 / "01.flac")["discogs_release_id"], ["922"])
        self.assertNotEqual(first.split(":")[0], ov.summary.text().split(":")[0])

    def test_a_fresh_preview_of_the_folder_uses_the_one_above_again(self):
        ov = self.ov
        self.listed()
        ov.set_album_profile([self.row("Album H")], "Mover")
        ov.plan = None                                    # (as after an Apply of every album)
        ov.preview()
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app))
        h = self.row("Album H")
        self.assertEqual(ov.profile_of(h), "Auto")
        self.assertEqual(ov.stale_profiles(ov.to_apply()), [])

    def test_merged_results_count_on_and_a_run_that_cannot_start_fails_its_albums(self):
        from gui.overview import merge_results
        p1 = {"job": "a", "albums": [{"source_dir": "/x/1"}, {"source_dir": "/x/2"}]}
        p2 = {"job": "b", "albums": [{"source_dir": "/x/3"}]}

        def run(p):
            if p["job"] == "b":
                raise RuntimeError("job z is running in another TagMender process")
            return {"job": "a", "status": "done", "counts": {"done": 2},
                    "albums": [{"index": 0, "album": "/x/1", "status": "done"},
                               {"index": 1, "album": "/x/2", "status": "done"}]}
        res = merge_results([p1, p2], run)
        self.assertEqual(res["job"], "a")
        self.assertEqual(res["counts"], {"done": 2, "failed": 1})
        self.assertEqual([r["index"] for r in res["albums"]], [0, 1, 2])
        self.assertEqual(res["status"], "partial")
        with self.assertRaises(RuntimeError):                           # (one profile: as before)
            merge_results([p2], run)


for _n in dir(tg.Gui):                                  # (the base class's own tests run there)
    if _n.startswith("test_"):
        setattr(ProfilePerAlbum, _n, None)


if __name__ == "__main__":
    unittest.main()
