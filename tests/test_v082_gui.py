# 0.8.2 app fixes (offscreen Qt, fake providers, generated audio only):
# - 3: Refresh of a set planned as a row per disc keeps both rows (Look Up sends the album's key);
# - 4: a cancelled Look Up replaces only the rows of the albums it reached; the others stay;
# - 5: Needs attention never shows an older folder's answer under the folder it looks in now,
#      and a look asked for while another runs follows it;
# - 6: Save Changes in the watched-folder dialog keeps an interval and settle time not touched;
# - 9: the Overview's and Watched folders' sentences go through tr() whole (no English pieces).
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_web_partial as twp  # noqa: E402
import profiles as pf  # noqa: E402
from test_gui import wait  # noqa: E402


class Marked:
    """A translator that marks every text it translates: <text>."""
    def translate(self, _ctx, text, _dis=None):
        return f"<{text}>"


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V082Gui(tg.Gui):
    def overview(self):
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        return ov

    # ---- 3 -----------------------------------------------------------------------
    def test_refresh_of_a_set_planned_per_disc_keeps_both_rows(self):
        top = self.lib / "Band P" / "1991 - Album P [2024 RM + RMX 2CD]"
        for disc, lens in (("1", twp.VERSION_A), ("2", twp.VERSION_B)):
            for i, (t, s) in enumerate(zip(twp.TITLES, lens)):
                twp.tone(top / f"Disc 0{disc}" / f"{disc}0{i + 1}.{t[0]}.flac", s,
                         album="Album P [2024 RM + RMX 2CD]", albumartist="Band P", artist="Band P", title=t,
                         track=str(i + 1), disc=disc, date="1991")
        self.http = twp.WebHttp([twp.deezer_album(31, "Band P", "Album P", twp.VERSION_A, titles=twp.TITLES,
                                                  date="1991-06-01"),
                                 twp.deezer_album(32, "Band P", "Album P (Remix 2024)", twp.VERSION_B,
                                                  titles=twp.TITLES, date="2024-03-01")])
        pf.save(pf.from_dict({"name": "Web", "providers": {"web": {"enabled": True, "shops": False, "gnudb": False}},
                              "artwork": {"enabled": False}}))
        ov = self.overview()
        ov.profile.setCurrentText("Web")
        ov.set_path(str(top))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app))
        rows = sorted(Path(a["source_dir"]).name for a in ov.plan["albums"])
        self.assertEqual(rows, ["Disc 01", "Disc 02"])
        ov.reload()
        self.assertTrue(wait(lambda: not ov.running, self.app))
        self.assertEqual(sorted(Path(a["source_dir"]).name for a in ov.plan["albums"]), rows)

    # ---- 4 -----------------------------------------------------------------------
    def test_a_cancelled_look_up_keeps_the_rows_it_did_not_reach(self):
        import app_settings
        import engine
        app_settings.set_value("add_folder", "list")
        ov = self.overview()
        ov.profile.setCurrentText("Auto")
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app))
        before = sorted(a["source_dir"] for a in ov.plan["albums"])
        self.assertEqual(len(before), 2)
        real = engine.Engine.proposals

        def proposals(self_, path, progress=None, cancel=None, only=None):
            def prog(i, n, w):                       # (Cancel pressed while the first album is looked up)
                ov.worker.cancelled = True
                if progress:
                    progress(i, n, w)
            return real(self_, path, prog, cancel, only=only)
        with mock.patch.object(engine.Engine, "proposals", proposals):
            ov.look_up("profile", list(ov.plan["albums"]))
            self.assertTrue(wait(lambda: not ov.running, self.app))
        self.assertEqual(sorted(a["source_dir"] for a in ov.plan["albums"]), before)
        self.assertEqual(sorted(a["status"] == "listed" for a in ov.plan["albums"]), [False, True])

    # ---- 5 -----------------------------------------------------------------------
    def test_needs_attention_shows_the_folder_it_looks_in_not_an_older_answer(self):
        import tagmender
        att = self.win.pages["Needs attention"]
        gate = threading.Event()
        seen = []

        def slow(path):
            seen.append(path)
            if path == "/lib/A":
                gate.wait(10)                         # (a big folder on a slow disk)
                return [{"folder": "/lib/A/x", "codes": ["no_candidate"], "reason": "a"}] * 5
            return []
        with mock.patch.object(tagmender, "_unresolved_albums", slow):
            self.win.refresh_attention_badge("/lib/A")
            self.win.refresh_attention_badge("/lib/B")       # (B previewed while A is still counted)
            gate.set()
            self.assertTrue(wait(lambda: not att.scanning and "/lib/B" in seen, self.app))
            self.assertTrue(wait(lambda: not att.scanning, self.app))
        self.assertEqual(att.root.text(), "/lib/B")
        self.assertEqual(att.rows, [])
        self.assertEqual(seen, ["/lib/A", "/lib/B"])

    # ---- 6 -----------------------------------------------------------------------
    def watch_dialog(self, **watch):
        from gui import watched
        inbox = self.tmp / "inbox"
        inbox.mkdir(exist_ok=True)
        pf.save(pf.from_dict({"name": "Inbox", "providers": {"web": {"enabled": False}},
                              "artwork": {"enabled": False}, "sources": [{"path": str(inbox), "watch": True}],
                              "watch": dict({"settle_minutes": 0.25, "interval_seconds": 30, "workers": 1},
                                            **watch)}))
        return watched.WatchDialog(self.win.pages["Watched folders"], str(inbox), "Inbox")

    def test_saving_the_watch_dialog_untouched_keeps_interval_and_settle(self):
        dlg = self.watch_dialog()
        dlg.save()
        w = pf.load("Inbox")["watch"]
        self.assertEqual((w["settle_minutes"], w["interval_seconds"]), (0.25, 30))

    def test_a_changed_interval_and_settle_are_saved(self):
        dlg = self.watch_dialog()
        dlg.settle.setValue(2)
        dlg.every.setCurrentIndex(dlg.every.count() - 1)
        dlg.save()
        w = pf.load("Inbox")["watch"]
        last = int(dlg.every.itemData(dlg.every.count() - 1))
        self.assertEqual((w["settle_minutes"], w["interval_seconds"]), (2, last))

    # ---- 9 -----------------------------------------------------------------------
    def test_overview_and_watched_sentences_are_translated_whole(self):
        from gui import i18n, overview, watched
        ov = self.overview()
        albums = [{"source_dir": f"/x/a{i}", "files": []} for i in range(overview.APPLY_LISTED + 2)]
        with mock.patch.object(i18n, "_own", Marked()):
            # the Apply sheet: "and N more" and its button
            with mock.patch.object(ov, "to_apply", return_value=albums):
                _head, body, _undo, button = ov.apply_words()
            self.assertIn("&lt;and 2 more&gt;", body)
            self.assertEqual(button, f"<Apply {len(albums)} <Albums>>")
            # "Stopped: ..." under the list
            with mock.patch.object(self.gui.sheets, "warning"):
                ov.oops("boom\nmore")
            self.assertEqual(ov.summary.text(), "<Stopped: boom>")
            # "Discard your edits of 1 album?"
            ov.path = str(self.lib)
            self.win.inspector.edits = {"x": [1]}
            with mock.patch.object(self.gui.sheets, "confirm", return_value=False) as asked:
                self.assertFalse(ov.may_drop_edits(str(self.tmp)))
            self.assertEqual(asked.call_args[0][1], "<Discard your edits of 1 <album>?>")
            self.win.inspector.edits = {}
            # Watched folders: an outcome's "and N more", and the Keep watching sheet
            pairs = [(f"/w/a{i}", "done") for i in range(8)]
            self.assertIn("<and 2 more>", watched.outcome_text(pairs))
            page = self.win.pages["Watched folders"]
            p = pf.from_dict({"name": "Auto", "unattended": True})
            for rows, want in (([("/w/x", p)], "<1 watched folder is checked every"),
                               ([(f"/w/x{i}", p) for i in range(5)], "<5 watched folders are checked every")):
                with mock.patch.object(page, "rows", return_value=rows), \
                        mock.patch.object(self.gui.sheets, "confirm", return_value=False) as asked:
                    page.ask_live()
                text = asked.call_args[0][2]
                self.assertTrue(text.startswith(want), text)
            self.assertIn("<{0} and {1} more>".format("x0, x1, x2", 2), text)


for _n in dir(tg.Gui):                 # (only this file's tests: not test_gui's again)
    if _n.startswith("test_") and _n not in V082Gui.__dict__:
        setattr(V082Gui, _n, None)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
