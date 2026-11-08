# 0.8.4: the Scans and Source columns of Overview, and the Match column of Watched folders > Recently
# (offscreen Qt); a row waiting for approval stands out and is reviewed in Needs attention.
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)

SHOTS = os.environ.get("TAGMENDER_SHOTS", "")


def album(folder, **ident):
    return {"source_dir": folder, "status": "ready", "files": [], "conflicts": [],
            "identification": dict({"provider": "musicbrainz", "confidence": 90, "tier": "exact"}, **ident)}


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V084MatchColumns(tg.Gui):
    def cell(self, a, col, role=None):
        from gui.models import PlanModel
        m = PlanModel()
        m.albums = [a]
        return m.data(m.index(0, col), role if role is not None else tg.Qt.DisplayRole)

    def test_scans_column_for_each_state(self):
        from gui.models import PlanModel
        S = PlanModel.SCANS
        self.assertEqual(S, 7)
        for sc, text, tip in (
                ({"state": "proven", "found": ["barcode", "catalogue_number"], "against": []},
                 "✓ Barcode, Cat. no.", "pressing is proven"),
                ({"state": "agrees", "found": ["label"], "against": []}, "~ Label", "do not prove"),
                ({"state": "differs", "found": [], "against": ["catalogue_number"]},
                 "✗ Cat. no. differs", "another pressing"),
                ({"state": "unread", "found": [], "against": []}, "— Nothing read", "no barcode")):
            a = album("/m/x", scans=sc)
            self.assertEqual(self.cell(a, S), text)
            self.assertIn(tip, self.cell(a, S, tg.Qt.ToolTipRole))
            self.assertNotIn("%", text)
        self.assertEqual(self.cell(album("/m/x"), S), "")
        self.assertEqual(self.cell(album("/m/x"), S, tg.Qt.ToolTipRole), "")

    def test_source_names_how_it_was_reached(self):
        from gui.models import PlanModel
        for kw, text in (({}, "MusicBrainz"), ({"via": "disc_id"}, "MusicBrainz · disc ID"),
                         ({"via": "link"}, "MusicBrainz · your files' link"),
                         ({"via": "scan_search", "provider": "web"}, "Web · web search of your scans"),
                         ({"via": "barcode_lookup", "provider": "discogs+musicbrainz"},
                          "Discogs+MusicBrainz · barcode lookup"),
                         ({"via": "fingerprint"}, "MusicBrainz · fingerprint"),
                         ({"provider": "web"}, "Web")):
            self.assertEqual(self.cell(album("/m/x", **kw), 2), text)

    def test_overview_shows_scans_after_source_and_hides_it_first(self):
        from gui.models import PlanModel
        ov = self.win.pages["Overview"]
        h = ov.table.horizontalHeader()
        self.assertEqual(h.visualIndex(PlanModel.PROFILE), 2)
        self.assertEqual(h.visualIndex(PlanModel.SCANS), h.visualIndex(2) + 1)
        self.assertGreater(ov.NARROW[PlanModel.SCANS], ov.NARROW[2])

    def wide(self):
        self.win.resize(1500, 800)
        self.win.inspector.hide()
        self.app.processEvents()

    def make_recent(self, rows):
        self.wide()
        self.win.go("Watched folders")
        w = self.win.pages["Watched folders"]
        with mock.patch("watcher.recent", return_value=rows), mock.patch("watcher.pending_plans", return_value=[]):
            w.reload()
        self.app.processEvents()
        return w

    def test_recently_match_column_six_and_five_tuples(self):
        w = self.make_recent([
            ("/m/A", "Auto", "done", 1, "", {"tier": "exact", "confidence": 95, "provider": "musicbrainz",
                                             "scans": {"state": "proven", "found": ["barcode"], "against": []},
                                             "via": ""}),
            ("/m/B", "Auto", "planned", 2, "", {"tier": "partial", "scans": None, "via": ""}),
            ("/m/C", "Auto", "done", 3, "", {}),
            ("/m/D", "Auto", "done", 4, "")])
        t = w.recent
        self.assertEqual([t.horizontalHeaderItem(i).text() for i in range(5)],
                         ["When", "Album", "Outcome", "Match", "Profile"])
        self.assertEqual(t.item(0, 3).text(), "Exact · ✓ Barcode")
        self.assertTrue(t.item(0, 3).toolTip())
        self.assertEqual(t.item(1, 3).text(), "Partial")
        self.assertEqual(t.item(2, 3).text(), "")
        self.assertEqual(t.item(3, 3).text(), "")
        self.assertEqual(t.item(3, 4).text(), "Auto")
        self.shot("v084_watched")
        self.assertTrue(t.item(1, 2).font().bold())
        self.assertFalse(t.item(0, 2).font().bold())

    def test_review_chooses_the_waiting_plan_and_shows_its_changes(self):
        from gui import watched as gw
        w = self.make_recent([("/m/A", "Auto", "done", 1, "", {}), ("/m/B", "Auto", "planned", 2, "", {})])
        w.recent.selectRow(0)
        self.assertEqual(w.recent_folder(), "")                    # (only a waiting row is reviewed)
        w.recent.selectRow(1)
        self.assertEqual(w.recent_folder(), "/m/B")
        plans = [{"albums": [("/m/Z", "", "")]}, {"albums": [("/m/B", "", "")]}]
        gw._fill(w.pending, [("/m/Z", "", "Auto"), ("/m/B", "", "Auto")])
        for r, (src, *_x) in enumerate([("/m/Z",), ("/m/B",)]):
            w.pending.setItem(r, 0, gw.folder_item(src, album=True))
        w.prow_plans = plans
        with mock.patch.object(w, "show_plan") as shown:
            w.review_recent()
        shown.assert_called_once()
        self.assertIs(w.chosen_plan(), plans[1])
        gw._fill(w.pending, [])
        with mock.patch.object(w, "show_plan") as shown:
            w.review_recent()                                       # (applied meanwhile: not waiting now)
        shown.assert_not_called()
        self.assertIn("no longer waiting", self.win.statusBar().currentMessage())

    def test_process_again_on_an_undone_album(self):
        # an album you undid stays in the watched folder for good unless you ask for it again
        # (Mekong Delta - The Principle Of Doubt, undone after a wrong disc order in 0.8.13)
        import tempfile
        from gui import sheets
        d = tempfile.mkdtemp()
        w = self.make_recent([(d, "Auto", "undone", 1, "", {}), ("/m/gone", "Auto", "done", 2, "", {}),
                              ("/m/B", "Auto", "planned", 3, "", {})])
        self.assertIn("Process Again", w.recent.item(0, 2).toolTip())
        w.recent.selectRow(0)
        self.assertEqual(w.recent_album(), (d, "undone"))
        with mock.patch.object(sheets, "question", return_value=True) as asked, \
                mock.patch("watcher.forget", return_value=1) as forget, \
                mock.patch.object(w, "check_now") as check:
            w.process_album_again()
        asked.assert_called_once()                                  # (it was undone by you: asked first)
        forget.assert_called_once_with(d, undone=True)
        check.assert_called_once()
        with mock.patch.object(sheets, "question", return_value=False), \
                mock.patch("watcher.forget") as forget:
            w.process_album_again()
        forget.assert_not_called()                                  # (said no)
        # the menu: Process Again where the folder is still there; Review… for a waiting one
        shown = {}
        with mock.patch("gui.watched.popup_menu", lambda _s, _t, _p, acts: shown.update(
                {r: [a.text() for a in acts]})):
            for r in range(3):
                w.recent.selectRow(r)
                shown[r] = None
                w.recent_menu(None)
        self.assertEqual(shown[0], ["Process Again"])
        self.assertIsNone(shown[1])                                  # (moved away: nothing to process)
        self.assertEqual(shown[2], ["Review…"])

    def test_overview_shot(self):
        self.wide()
        ov = self.win.pages["Overview"]
        ov.model.set_plan({"albums": [
            album("/m/Band/One", via="disc_id", scans={"state": "proven", "found": ["barcode", "catalogue_number"],
                                                       "against": []}),
            album("/m/Band/Two", scans={"state": "differs", "found": [], "against": ["catalogue_number"]}),
            album("/m/Band/Three", provider="web", via="scan_search", scans={"state": "unread"})]})
        ov.views.setCurrentWidget(ov.results)
        self.win.go("Overview")
        self.shot("v084_overview")


if __name__ == "__main__":
    unittest.main()
