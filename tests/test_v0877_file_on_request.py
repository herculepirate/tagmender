"""MOV-6 (docs/MATCHING_RULES.md, amending MOV-5): what you ask for is done. A Partial album (no
release proven) is tagged where it is unasked, as before, but offered for filing; asked, it is
moved or copied into the layout from the tags it has, its mark kept Partial. In the app an
offered row with nothing to apply where it is can be ticked - the tick is the ask; its status
tooltip, the bar over its tags and Apply's tooltip say why it is not filed. Synthetic files only."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0864_file_below_score as t864  # noqa: E402  (its isolated config, its fixtures)
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import test_engine as te  # noqa: E402

QApplication = t864.QApplication


class Dirs:
    def unresolved_dir(self):
        d = self.tmp / "unresolved"
        d.mkdir(exist_ok=True)
        return d


class MOV6_Engine(te.Base):
    """The real look-up path: an Unresolved album looked up again with its closest release as
    your choice and your ask to file it - filed from that release, recorded as your choice."""
    def test_MOV6_a_release_you_chose_is_recorded_as_yours_and_filed(self):
        d = self.album("Band U", "Zzz Nothing")
        dest, unres = self.tmp / "filed", self.tmp / "unres"
        dest.mkdir()
        unres.mkdir()
        eng = self.engine(te.FakeHttp(dg=[te.dg_release(950, "Real Band", "Real Album")]), operation="move",
                          destination=str(dest), unresolved={"action": "move", "move_to": str(unres)})
        eng.offer_file_anyway = True
        self.assertEqual(eng.plan(d)["albums"][0]["status"], "unresolved")
        files = sorted(str(f) for f in d.glob("*.flac"))
        eng.force = {os.path.realpath(str(d)): [{"ref": "discogs:950", "files": files}]}
        eng.file_anyway = {os.path.realpath(f) for f in files}
        p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready")
        self.assertIn("pressing chosen by you", a["identification"]["evidence"])
        self.assertTrue(any(n.startswith("release chosen by you (score ") for n in a["notes"]), a["notes"])
        executor.Executor(p).run()
        out = sorted(dest.rglob("*.flac"))
        self.assertEqual(len(out), 3)
        self.assertEqual(list(unres.rglob("*.flac")), [])
        self.assertEqual(tagio.read(out[0])["discogs_release_id"], ["950"])


class MOV6_Planner(Dirs, t864.Base):
    def partial(self, **kw):
        return self.proposal(conf=99, tier="partial", provider="web", **kw)

    def test_MOV6_a_partial_album_is_offered_on_a_move_profile(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        a = pl.build([self.partial()], prof, offer_file_anyway=True)["albums"][0]
        self.assertEqual(self.ops(a), {"tag"})                           # (unasked: in place, as before)
        self.assertEqual(a["below_floor"]["reason"], "partial")
        self.assertEqual(a["below_floor"]["op"], "move")
        self.assertIn("Partial identification (no release proven): tagged in place, not moved", a["notes"])

    def test_MOV6_asked_a_partial_album_is_moved_and_keeps_its_mark(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.partial()], prof, offer_file_anyway=True, file_anyway=self.mine())
        a = p["albums"][0]
        self.assertEqual(self.ops(a), {"move"})
        self.assertIn("moved because you asked (no release proven)", a["notes"])
        self.assertEqual(a["filed_on_request"]["reason"], "partial")
        self.assertNotIn("below_floor", a)
        executor.Executor(p).run()
        moved = sorted(self.dest.rglob("*.flac"))
        self.assertEqual(len(moved), 2)
        self.assertFalse(self.f1.exists())
        self.assertEqual(tagio.read(moved[0])["tagmender"], ["Partial"])

    def test_MOV6_unasked_a_partial_album_stays_where_it_is(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.partial()], prof, offer_file_anyway=True)
        executor.Executor(p).run()
        self.assertTrue(self.f1.exists())
        self.assertEqual(list(self.dest.rglob("*.flac")), [])

    def test_MOV6_on_a_copy_profile_it_is_offered_as_a_copy(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        a = pl.build([self.partial()], prof, offer_file_anyway=True)["albums"][0]
        self.assertEqual(a["status"], "skipped")
        self.assertEqual(a["below_floor"]["op"], "copy")
        a = pl.build([self.partial()], prof, file_anyway=self.mine())["albums"][0]
        self.assertEqual(self.ops(a), {"copy"})
        self.assertIn("copied because you asked (no release proven)", a["notes"])

    def test_MOV6_asked_but_still_unresolved_is_not_sent_to_the_unresolved_folder(self):
        prof = self.profile(operation="move", destination=str(self.dest),
                            unresolved={"action": "move", "move_to": str(self.unresolved_dir())})
        un = self.proposal(status="unresolved", targets=[], confidence=0, reason="no match")
        a = pl.build([un], prof)["albums"][0]
        self.assertEqual(self.ops(a), {"move"})                         # (unasked: as before)
        a = pl.build([un], prof, file_anyway=self.mine())["albums"][0]
        self.assertNotIn("move", self.ops(a))
        self.assertIn("not moved to the unresolved folder", " ".join(a["notes"]))

    def test_MOV6_nobody_asks_where_nobody_can(self):
        # (the watcher, unattended and command-line runs make no trial: nothing offered)
        prof = self.profile(operation="move", destination=str(self.dest))
        a = pl.build([self.partial()], prof)["albums"][0]
        self.assertNotIn("below_floor", a)
        self.assertEqual(self.ops(a), {"tag"})


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class MOV6_App(Dirs, t864.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and any(self.gui._alive(th) for pg in self.win.pages.values()
                                        for th, _w in getattr(pg, "_threads", [])):
            self.app.processEvents()
            time.sleep(0.02)
        self.win.close()
        super().tearDown()

    show = t864.MOV5_App.show

    def up_to_date(self, prof, prop):
        """PROP tagged in place first, then planned again: Up to date, its filing offered."""
        executor.Executor(pl.build([prop()], prof)).run()
        plan = pl.build([prop()], prof, offer_file_anyway=True)
        self.assertEqual(plan["albums"][0]["status"], "unchanged")
        self.assertIn("below_floor", plan["albums"][0])
        return plan

    def test_MOV6_an_up_to_date_partial_row_can_be_ticked_and_ticking_files_it(self):
        import engine
        from PySide6.QtCore import Qt
        from gui import overview as govw
        from gui.models import PlanModel
        prof = self.profile(operation="move", destination=str(self.dest))
        partial = lambda: self.proposal(conf=99, tier="partial", provider="web")  # noqa: E731
        plan = self.up_to_date(prof, partial)
        ov = self.show(plan)
        a = ov.model.albums[0]
        box = ov.model.index(0, 1)
        self.assertTrue(ov.model.flags(box) & Qt.ItemIsUserCheckable)
        self.assertEqual(ov.model.data(box, Qt.CheckStateRole), Qt.Unchecked)
        tip = ov.model.data(ov.model.index(0, 0), Qt.ToolTipRole)
        self.assertIn("not moved into your library because it isn't on MusicBrainz or Discogs", tip)
        self.assertIn("not moved (no release proven)", ov.model.data(ov.model.index(0, 4)))
        # Apply is off, and says why; the bar over its tags says why and offers the move
        self.assertFalse(self.win.commands["apply"].isEnabled())
        self.assertEqual(self.win.commands["apply"].toolTip(),
                         "Nothing to apply: not moved into your library: use Move into Library Anyway")
        self.assertTrue(ov.panes.offer.isVisibleTo(ov.panes))
        self.assertIn("isn't on MusicBrainz or Discogs", ov.panes.offer_words.text())
        self.assertEqual(ov.panes.offer_btn.text(), "Move into Library Anyway")
        self.assertTrue(ov.panes.offer_btn.isEnabled())
        self.assertTrue(self.win.commands["file_anyway"].isEnabled())
        insp = self.win.inspector                       # (the Inspector says it too, and offers it)
        insp.show_album(a)
        self.assertIn("isn't on MusicBrainz or Discogs", insp.offer_words.text())
        self.assertEqual(insp.offer_btn.text(), "Move into Library Anyway")
        self.assertTrue(insp.offer_btn.isEnabled())
        shots = os.environ.get("TAGMENDER_SHOTS877")
        if shots:                                       # (the bar over its tags, light and dark)
            Path(shots).mkdir(parents=True, exist_ok=True)
            ov.views.setCurrentWidget(ov.results)
            for scheme in ("light", "dark"):
                self.gui.style.apply(self.app, scheme)
                self.app.processEvents()
                self.win.grab().save(str(Path(shots) / f"file_anyway_offer_{scheme}.png"))
            self.gui.style.apply(self.app, "light")
        # ticked: the ask - looked up again with it, the row then moves
        seen = []

        def fake_plan_each(e, path, keys, emit, **kw):
            seen.append(set(e.file_anyway))
            out = pl.build([partial()], prof, file_anyway=e.file_anyway, seed_claims=e.seed_claims,
                           offer_file_anyway=e.offer_file_anyway)
            out["albums"][0]["key"] = keys[0]
            emit(keys[0], out)
            return len(keys)
        with mock.patch.object(engine, "engine_for_thread", lambda *a, **k: type("E", (), {})()), \
                mock.patch.object(govw, "plan_each", fake_plan_each), \
                mock.patch.object(ov, "planning_profile", lambda *a, **k: prof):
            ov.model.setData(box, Qt.Checked, Qt.CheckStateRole)
            self.assertIn("Looking up 1 album again", self.win.statusBar().currentMessage())
            self.assertEqual(ov.model.data(box, Qt.CheckStateRole), Qt.Checked)   # (shown ticked meanwhile)
            end = time.time() + 30
            while time.time() < end and (ov.running or not seen):
                self.app.processEvents()
                time.sleep(0.02)
        self.assertEqual(seen[0], {os.path.realpath(str(self.f1)), os.path.realpath(str(self.f2))})
        row = ov.model.albums[0]
        self.assertIsNot(row, a)
        self.assertEqual(self.ops(row), {"move"})
        self.assertFalse(PlanModel.left_out(row))                     # (ticked: Apply files it)
        self.assertIn(row, ov.to_apply())
        executor.Executor(dict(plan, albums=[row])).run()
        self.assertEqual(len(list(self.dest.rglob("*.flac"))), 2)
        self.assertEqual(tagio.read(sorted(self.dest.rglob("*.flac"))[0])["tagmender"], ["Partial"])

    def test_MOV6_a_row_below_the_floor_is_tickable_and_unticked_stays(self):
        from PySide6.QtCore import Qt
        prof = self.profile(operation="move", destination=str(self.dest))
        plan = self.up_to_date(prof, lambda: self.proposal(conf=91))
        ov = self.show(plan)
        box = ov.model.index(0, 1)
        self.assertTrue(ov.model.flags(box) & Qt.ItemIsUserCheckable)
        self.assertEqual(ov.model.data(box, Qt.CheckStateRole), Qt.Unchecked)
        self.assertIn("its score 91 is below the 95", ov.model.data(ov.model.index(0, 0), Qt.ToolTipRole))
        self.assertEqual(ov.to_apply(), [])                            # (unticked: untouched)
        # the albums pane's row has the box too
        t = ov.tree
        idx = t.index_of(t.node_of(ov.model.albums[0]), 0)
        self.assertTrue(t.flags(idx) & Qt.ItemIsUserCheckable)

    def test_MOV6_a_ready_row_below_the_floor_stays_ticked_to_tag_in_place(self):
        # (its box already says Apply tags it where it is; the command files it)
        from PySide6.QtCore import Qt
        prof = self.profile(operation="move", destination=str(self.dest))
        plan = pl.build([self.proposal(conf=91)], prof, offer_file_anyway=True)
        ov = self.show(plan)
        self.assertEqual(ov.model.data(ov.model.index(0, 1), Qt.CheckStateRole), Qt.Checked)
        self.assertIn("Choose Move into Library Anyway", ov.model.data(ov.model.index(0, 0), Qt.ToolTipRole))
        self.assertTrue(self.win.commands["apply"].isEnabled())

    def test_MOV6_an_unresolved_row_with_changes_is_tickable_one_without_is_not(self):
        from PySide6.QtCore import Qt
        prof = self.profile(operation="move", destination=str(self.dest), unresolved={"action": "mark"})
        un = self.proposal(status="unresolved", targets=[], confidence=0, reason="no match")
        plan = pl.build([un], prof)
        self.assertEqual(plan["albums"][0]["status"], "unresolved")
        ov = self.show(plan)
        self.assertTrue(ov.model.flags(ov.model.index(0, 1)) & Qt.ItemIsUserCheckable)
        # (one with nothing to write - left where it is - has no box)
        prof = self.profile(operation="copy", destination=str(self.dest))
        plan = pl.build([un], prof)
        ov = self.show(plan)
        self.assertFalse(ov.model.flags(ov.model.index(0, 1)) & Qt.ItemIsUserCheckable)

    CANDIDATE = {"source": "discogs", "id": "777", "ref": "discogs:777", "score": 62.0,
                 "title": "Real Artist - Real Album (2001)", "url": "https://www.discogs.com/release/777"}

    def unresolved(self):
        return self.proposal(status="unresolved", tier="unresolved", targets=[], confidence=0, reason="no match",
                             candidates=[dict(self.CANDIDATE)])

    CANDIDATE["details"] = "CD · Germany · CAT-1"
    CANDIDATE["title"] = "Real Artist - Real Album (2001)"

    def unresolved_row(self):
        prof = self.profile(operation="move", destination=str(self.dest),
                            unresolved={"action": "move", "move_to": str(self.unresolved_dir())})
        plan = pl.build([self.unresolved()], prof)
        self.assertEqual(plan["albums"][0]["status"], "unresolved")
        return prof, plan, self.show(plan)

    def fake_look_up(self, prof, seen, found=None):
        """plan_each as the look-up's: what it was given (force, file_anyway), and FOUND (a
        proposal maker; none: the album Unresolved again) planned with it."""
        def fake_plan_each(e, path, keys, emit, **kw):
            seen.append((dict(e.force), set(e.file_anyway)))
            props = [found()] if found and e.force else [self.unresolved()]
            out = pl.build(props, prof, file_anyway=e.file_anyway, seed_claims=e.seed_claims,
                           offer_file_anyway=e.offer_file_anyway)
            out["albums"][0]["key"] = keys[0]
            emit(keys[0], out)
            return len(keys)
        return fake_plan_each

    def wait_idle(self, ov):
        end = time.time() + 30
        while time.time() < end and (ov.running or getattr(ov, "apply_worker", None) is not None):
            self.app.processEvents()
            time.sleep(0.02)

    def patched(self, ov, prof, seen, found=None):
        import contextlib
        import engine
        from gui import overview as govw
        st = contextlib.ExitStack()
        st.enter_context(mock.patch.object(engine, "engine_for_thread", lambda *a, **k: type("E", (), {})()))
        st.enter_context(mock.patch.object(govw, "plan_each", self.fake_look_up(prof, seen, found)))
        st.enter_context(mock.patch.object(ov, "planning_profile", lambda *a, **k: prof))
        st.enter_context(mock.patch.object(self.gui.sheets, "confirm", return_value=True))
        st.enter_context(mock.patch.object(self.win, "show_run_report"))
        return st

    def test_MOV6_an_unresolved_rows_box_keeps_its_meaning(self):
        from PySide6.QtCore import Qt
        prof, plan, ov = self.unresolved_row()
        a = ov.model.albums[0]
        box = ov.model.index(0, 1)
        self.assertEqual(ov.model.data(box, Qt.CheckStateRole), Qt.Checked)      # (its unresolved action)
        with mock.patch.object(ov, "planning_profile", lambda *a, **k: prof):
            tip = ov.model.data(ov.model.index(0, 0), Qt.ToolTipRole)
        self.assertIn("the closest found is Real Artist – Real Album (2001, CD, Germany, CAT-1) (score 62)", tip)
        self.assertIn("Apply moves it to your unresolved folder", tip)
        self.assertIn("“Use … and File It…”", tip)
        seen = []
        with self.patched(ov, prof, seen):
            # tick then untick, Include in Apply: no release chosen, no look-up
            ov.model.setData(box, Qt.Unchecked, Qt.CheckStateRole)
            self.assertNotIn(a, ov.to_apply())                                    # (unticked: left out)
            ov.model.setData(box, Qt.Checked, Qt.CheckStateRole)
            ov.model.set_left_out([a], True)
            ov.model.set_left_out([a], False)                                     # (Include in Apply)
            self.assertIn(a, ov.to_apply())
            self.assertEqual(seen, [])
            self.assertEqual(ov.forced, {})
            # Apply Selected: its unresolved action, never a release
            ov.apply_selected([a])
            self.wait_idle(ov)
        self.assertEqual(seen, [])
        self.assertEqual(list(self.dest.rglob("*.flac")), [])
        self.assertEqual(len(list((self.tmp / "unresolved").rglob("*.flac"))), 2)

    def test_MOV6_an_unticked_unresolved_row_is_never_applied(self):
        from PySide6.QtCore import Qt
        prof, plan, ov = self.unresolved_row()
        a = ov.model.albums[0]
        ov.model.setData(ov.model.index(0, 1), Qt.Unchecked, Qt.CheckStateRole)
        self.assertEqual(ov.to_apply(), [])
        self.assertFalse(self.win.commands["apply"].isEnabled())
        self.assertTrue(self.f1.exists())
        self.assertNotIn(a, ov.applicable())

    def test_MOV6_use_the_closest_release_shows_its_tags_then_a_tick_files_it(self):
        from PySide6.QtCore import Qt
        prof, plan, ov = self.unresolved_row()
        a = ov.model.albums[0]
        self.win.update_album_actions()
        cmd = self.win.commands["use_candidate"]
        self.assertTrue(cmd.isEnabled())
        self.assertEqual(self.gui.actions.plain(cmd.text()),
                         "Use Real Artist – Real Album (2001, CD, Germany, CAT-1) and File It…")
        self.assertIn("Use Real Artist – Real Album", ov.panes.offer_btn.text())   # (the bar over its tags)
        self.assertTrue(ov.panes.offer_btn.isEnabled())
        self.win.inspector.show_album(a)
        self.assertIn("Use Real Artist – Real Album", self.win.inspector.offer_btn.text())
        seen = []
        with self.patched(ov, prof, seen, found=lambda: self.proposal(conf=62)):
            cmd.trigger()
            self.assertEqual(self.win.statusBar().currentMessage(),
                             "Showing Real Artist – Real Album (2001, CD, Germany, CAT-1) for 2001 - Some Album: "
                             "tick it to apply.")
            self.wait_idle(ov)
        force, files = seen[0]
        self.assertEqual([c["ref"] for c in force[os.path.realpath(str(self.album))]], ["discogs:777"])
        self.assertEqual(files, {os.path.realpath(str(self.f1)), os.path.realpath(str(self.f2))})
        row = ov.model.albums[0]
        self.assertEqual(row["status"], "ready")
        self.assertEqual(self.ops(row), {"move"})
        # (shown, not applied: unticked until you tick it; its tags are the row's)
        self.assertEqual(ov.model.data(ov.model.index(0, 1), Qt.CheckStateRole), Qt.Unchecked)
        self.assertEqual(ov.to_apply(), [])
        self.assertTrue(any(fe.get("changes") for fe in row["files"]))
        self.assertEqual(self.win.commands["undo_release_choice"].isEnabled() or ov.chosen_release(row) != {}, True)
        ov.model.setData(ov.model.index(0, 1), Qt.Checked, Qt.CheckStateRole)
        self.assertEqual(ov.to_apply(), [row])
        with self.patched(ov, prof, seen):
            ov.apply()
            self.wait_idle(ov)
        self.assertEqual(len(seen), 1)                                  # (the tick chose nothing more)
        self.assertEqual(len(list(self.dest.rglob("*.flac"))), 2)
        self.assertEqual(list((self.tmp / "unresolved").rglob("*.flac")), [])

    def test_MOV6_undo_release_choice_returns_it_to_unresolved(self):
        from PySide6.QtCore import Qt
        prof, plan, ov = self.unresolved_row()
        seen = []
        with self.patched(ov, prof, seen, found=lambda: self.proposal(conf=62)):
            ov.use_candidate(ov.model.albums[0])
            self.wait_idle(ov)
            row = ov.model.albums[0]
            self.assertEqual(row["status"], "ready")
            self.assertTrue(ov.chosen_release(row))
            ov.table.selectRow(0)
            self.app.processEvents()
            self.win.update_album_actions()
            self.assertTrue(self.win.commands["undo_release_choice"].isEnabled())
            self.win.commands["undo_release_choice"].trigger()
            self.wait_idle(ov)
        self.assertEqual(ov.forced, {})
        self.assertFalse(any(os.path.realpath(str(self.f1)) == f for f in ov.file_anyway))
        self.assertEqual(seen[-1], ({}, set()))
        self.assertEqual(ov.model.albums[0]["status"], "unresolved")
        self.assertEqual(ov.model.data(ov.model.index(0, 1), Qt.CheckStateRole), Qt.Unchecked)    # (not unasked)

    def test_MOV6_undo_release_choice_finds_a_pasted_link_too(self):
        # (Change Release from a link for the whole folder is kept as the link alone)
        prof, plan, ov = self.unresolved_row()
        a = ov.model.albums[0]
        ov.forced[os.path.realpath(a["source_dir"])] = "discogs:777"
        self.assertEqual(ov.chosen_release(a), {"ref": "discogs:777"})
        seen = []
        with self.patched(ov, prof, seen):
            ov.undo_release_choice([a])
            self.wait_idle(ov)
        self.assertEqual(ov.forced, {})
        self.assertIn("History › Undo", self.win.commands["undo_release_choice"].statusTip())

    def test_MOV6_an_unresolved_row_without_a_release_says_why(self):
        from PySide6.QtCore import Qt
        prof = self.profile(operation="move", destination=str(self.dest), unresolved={"action": "mark"})
        un = self.proposal(status="unresolved", targets=[], confidence=0, reason="no match")
        ov = self.show(pl.build([un], prof))
        self.assertIn("no release the sources found fits it", ov.model.data(ov.model.index(0, 0), Qt.ToolTipRole))
        self.assertEqual(ov.model.data(ov.model.index(0, 1), Qt.CheckStateRole), Qt.Checked)   # (marked, as before)

    def test_MOV6_up_to_date_rows_say_so_on_apply(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        executor.Executor(pl.build([self.proposal(conf=97)], prof)).run()
        filed = sorted(self.dest.rglob("*.flac"))
        prop = self.proposal(conf=97, source_dir=str(filed[0].parent), files=[str(f) for f in filed])
        plan = pl.build([prop], prof, offer_file_anyway=True)
        self.assertEqual(plan["albums"][0]["status"], "unchanged")
        self.show(plan)
        self.assertFalse(self.win.commands["apply"].isEnabled())
        self.assertEqual(self.win.commands["apply"].toolTip(), "Nothing to apply: the selected albums are up to date")


if __name__ == "__main__":
    unittest.main()
