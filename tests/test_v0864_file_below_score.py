"""MOV-5 (docs/MATCHING_RULES.md): an identified album scoring below organize_min_confidence is
tagged where it is (or not copied) unasked, as before; when you ask - the app's "Move into the
Library Layout Anyway" / "Copy into the Library Anyway" on its row, the CLI's
--file-below-score - it is filed into the profile's layout, for the albums you asked for only.
Every other rule that keeps an album in place still does; the watcher and unattended runs never
ask. The app shows such an album's state ("not moved (score 91 < 95)") so the ask can be found.
Synthetic files only."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import test_executor as tx  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # the app is optional
    QApplication = None


class Base(tx.Base):
    def setUp(self):
        super().setUp()
        self.album2 = self.lib / "Other Artist" / "2003 - Other Album"
        self.g1 = tx.audio(self.album2 / "01 c.flac", 660, album="Other Album", title="c")
        self.g2 = tx.audio(self.album2 / "02 d.flac", 770, album="Other Album", title="d")

    def proposal2(self, conf=91.0):
        return pl.Proposal(source_dir=str(self.album2), files=[str(self.g1), str(self.g2)],
                           targets=[tx.target(1, "Third", album="Second Album", artist="Second Artist",
                                              discogs_release_id=["888"]),
                                    tx.target(2, "Fourth", album="Second Album", artist="Second Artist",
                                              discogs_release_id=["888"])],
                           provider="discogs", release_id="888", confidence=conf, margin=20.0,
                           evidence=["artist", "title"],
                           release={"album": ["Second Album"], "albumartist": ["Second Artist"]})

    def ops(self, a):
        return {fe["op"] for fe in a["files"]}

    def mine(self):
        return {str(self.f1), str(self.f2)}


class MOV5_Planner(Base):
    def test_MOV5_unasked_below_the_floor_is_tagged_in_place_as_before(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        a = pl.build([self.proposal(conf=91)], prof)["albums"][0]
        self.assertEqual(self.ops(a), {"tag"})
        self.assertIn("tagged in place, not moved", " ".join(a["notes"]))
        self.assertEqual(a["below_floor"], {"confidence": 91.0, "floor": 95.0, "op": "move"})
        self.assertNotIn("filed_on_request", a)

    def test_MOV5_asked_for_one_folder_files_that_album_only(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(conf=91), self.proposal2(conf=91)], prof, file_anyway=self.mine())
        mine, other = p["albums"]
        self.assertEqual(self.ops(mine), {"move"})
        self.assertIn("confidence 91.0 is below organize_min_confidence 95; moved because you asked",
                      mine["notes"])
        self.assertEqual(mine["filed_on_request"]["op"], "move")
        self.assertNotIn("below_floor", mine)
        self.assertEqual(self.ops(other), {"tag"})              # (not asked: as before)
        self.assertIn("below_floor", other)
        executor.Executor(p).run()
        self.assertFalse(self.f1.exists())
        moved = sorted(self.dest.rglob("*.flac"))
        self.assertEqual(len(moved), 2)
        self.assertEqual(tagio.read(moved[0])["album"], ["Real Album"])
        self.assertTrue(self.g1.exists())                            # (the other album stayed)

    def test_MOV5_asked_by_a_path_spelled_otherwise_is_the_same_album(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        link = self.tmp / "link"
        os.symlink(self.album, link)
        a = pl.build([self.proposal(conf=91)], prof, file_anyway={str(link / self.f1.name)})["albums"][0]
        self.assertEqual(self.ops(a), {"move"})

    def test_MOV5_two_albums_of_one_folder_are_two_asks(self):
        # 1-A: loose tracks of two releases in one folder; you ask for the first only
        c = tx.audio(self.album / "05 c.flac", 660, album="Other", title="c", artist="X")
        d = tx.audio(self.album / "06 d.flac", 770, album="Other", title="d", artist="X")
        b = self.proposal2(conf=91)
        b.source_dir, b.files = str(self.album), [str(c), str(d)]
        prof = self.profile(operation="move", destination=str(self.dest))
        mine, other = pl.build([self.proposal(conf=91), b], prof, file_anyway=self.mine())["albums"]
        self.assertEqual(self.ops(mine), {"move"})
        self.assertIn("filed_on_request", mine)
        self.assertEqual(self.ops(other), {"tag"})
        self.assertNotIn("filed_on_request", other)
        self.assertIn("below_floor", other)

    def test_MOV5_seeded_claims_keep_a_row_planned_alone_off_another_rows_folder(self):
        # 1-B: A (asked, 91) and B (97) are two rips of release 777; A is planned again alone
        other = self.lib / "Some Artist" / "copy"
        g1 = tx.audio(other / "01 a.flac", 440, album="Some Album", title="a", artist="Some Artist")
        g2 = tx.audio(other / "02 b.flac", 550, album="Some Album", title="b", artist="Some Artist")
        prof = self.profile(operation="move", destination=str(self.dest))
        B = lambda: self.proposal(conf=97, source_dir=str(other), files=[str(g1), str(g2)])  # noqa: E731
        first = pl.build([self.proposal(conf=91), B()], prof)
        b_row = first["albums"][1]
        self.assertEqual(self.ops(b_row), {"move"})
        self.assertTrue(b_row.get("claims"))
        alone = pl.build([self.proposal(conf=91)], prof, file_anyway=self.mine(),
                         seed_claims=b_row["claims"])["albums"][0]
        self.assertEqual(self.ops(alone), {"tag"})                     # (MOV-4: B files it there)
        self.assertIn("planned to be filed", " ".join(alone["notes"]))
        # control: unseeded, the row planned alone targets B's files (what Apply then fails on)
        bare = pl.build([self.proposal(conf=91)], prof, file_anyway=self.mine())["albums"][0]
        self.assertEqual({fe["dst"] for fe in bare["files"]}, {fe["dst"] for fe in b_row["files"]})
        res = executor.Executor(dict(first, albums=[alone, b_row])).run()
        self.assertEqual(sorted(a["status"] for a in res["albums"]), ["done", "done"], res["albums"])

    def test_MOV5_nothing_offered_when_filing_would_move_nothing(self):
        # an album below the floor already where the naming puts it: no "Move … Anyway", and
        # asked, no "moved because you asked"
        prof = self.profile(operation="move", destination=str(self.dest))
        executor.Executor(pl.build([self.proposal(conf=97)], prof)).run()
        filed = sorted(self.dest.rglob("*.flac"))
        prop = lambda: self.proposal(conf=91, source_dir=str(filed[0].parent), files=[str(f) for f in filed])  # noqa: E731
        a = pl.build([prop()], prof)["albums"][0]
        self.assertNotIn("below_floor", a)
        b = pl.build([prop()], prof, file_anyway={str(f) for f in filed})["albums"][0]
        self.assertNotIn("filed_on_request", b)
        self.assertFalse([n for n in b["notes"] if "because you asked" in n])

    def test_MOV5_copy_profile_copies_when_asked(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        a = pl.build([self.proposal(conf=91)], prof)["albums"][0]
        self.assertEqual(a["status"], "skipped")
        self.assertEqual(a["below_floor"]["op"], "copy")
        a = pl.build([self.proposal(conf=91)], prof, file_anyway=True)["albums"][0]
        self.assertEqual(self.ops(a), {"copy"})
        self.assertIn("copied because you asked", " ".join(a["notes"]))

    def test_MOV5_partial_identification_stays_in_place_when_asked(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        a = pl.build([self.proposal(conf=91, tier="partial")], prof, file_anyway=True)["albums"][0]
        self.assertEqual(self.ops(a), {"tag"})
        self.assertIn("Partial identification", " ".join(a["notes"]))
        self.assertNotIn("below_floor", a)                          # (not offered: another rule's)

    def test_MOV5_extra_careful_matching_yields_to_your_ask(self):
        prof = self.profile(operation="move", destination=str(self.dest), matching={"extra_careful": True})
        a = pl.build([self.proposal(conf=96)], prof)["albums"][0]
        self.assertEqual(self.ops(a), {"tag"})                 # (the raised floor: unasked)
        a = pl.build([self.proposal(conf=96)], prof, file_anyway=True)["albums"][0]
        self.assertEqual(self.ops(a), {"move"})
        self.assertIn("(Extra-careful matching); moved because you asked", " ".join(a["notes"]))

    def test_MOV5_an_album_above_the_floor_is_planned_the_same_asked_or_not(self):
        # (an album already filed keeps its tag refresh: nothing about it changes)
        prof = self.profile(operation="move", destination=str(self.dest))
        plain = pl.build([self.proposal(conf=97)], prof, job_id="j")["albums"][0]
        asked = pl.build([self.proposal(conf=97)], prof, job_id="j", file_anyway=True)["albums"][0]
        self.assertEqual(plain["files"], asked["files"])
        self.assertEqual(plain["notes"], asked["notes"])
        self.assertNotIn("filed_on_request", asked)

    def test_MOV5_refresh_of_an_album_already_filed_is_unchanged(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        executor.Executor(pl.build([self.proposal(conf=97)], prof)).run()
        filed = sorted(self.dest.rglob("*.flac"))
        prop = self.proposal(conf=91, source_dir=str(filed[0].parent), files=[str(f) for f in filed])
        plain = pl.build([prop], prof, job_id="j")["albums"][0]
        asked = pl.build([prop], prof, job_id="j", file_anyway={str(f) for f in filed})["albums"][0]
        self.assertEqual((plain["status"], plain["files"]), (asked["status"], asked["files"]))

    def test_MOV5_unasked_album_claims_no_folder(self):
        # (MOV-4: an album not moved claims no album folder, so blocks no other album)
        prof = self.profile(operation="move", destination=str(self.dest))
        planner = pl.Planner(prof)
        planner.album(self.proposal(conf=91))
        self.assertFalse(any(planner.__dict__.get("_album_claims", {}).values()))
        planner = pl.Planner(prof, file_anyway=True)
        planner.album(self.proposal(conf=91))
        self.assertTrue(any(planner.__dict__.get("_album_claims", {}).values()))

    def test_MOV5_unattended_profile_tags_in_place(self):
        prof = self.profile(operation="move", destination=str(self.dest), unattended=True)
        a = pl.build([self.proposal(conf=91)], prof)["albums"][0]
        self.assertEqual(self.ops(a), {"tag"})

    def test_MOV5_the_watcher_never_asks(self):
        src = (HERE / "watcher.py").read_text(encoding="utf-8")
        self.assertNotIn("file_anyway", src)
        self.assertNotIn("file_below_score", src)


class MOV5_Cli(Base):
    @staticmethod
    def parse(*argv):
        import argparse
        import tagmender
        ap = argparse.ArgumentParser()
        tagmender.work_parsers(ap.add_subparsers(dest="cmd"))
        return ap.parse_args(list(argv))

    def test_MOV5_plan_and_run_take_file_below_score(self):
        import tagmender
        import engine
        seen = []

        def fake_plan(e, path, **kw):
            seen.append(getattr(e, "file_anyway", None))
            return {"albums": [], "provider_notes": {}}
        prof = self.profile(operation="move", destination=str(self.dest))
        for argv in (("plan", str(self.album), "--file-below-score"), ("run", str(self.album), "--file-below-score"),
                     ("plan", str(self.album))):
            args = self.parse(*argv)
            with mock.patch.object(engine.Engine, "plan", fake_plan), \
                    mock.patch.object(tagmender, "_profile", lambda a: prof), \
                    mock.patch.object(engine.Engine, "__init__", lambda e, *a, **k: setattr(e, "notes", {})):
                tagmender._make_plan(args)
        self.assertEqual(seen, [True, True, None])

    def test_MOV5_the_flag_files_the_album_end_to_end(self):
        # (what _make_plan's engine plans with: every album of the run)
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(conf=91), self.proposal2(conf=91)], prof, file_anyway=True)
        self.assertEqual([self.ops(a) for a in p["albums"]], [{"move"}, {"move"}])


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class MOV5_App(Base):
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

    def show(self, plan):
        ov = self.win.pages["Overview"]
        ov.path = str(self.lib)
        ov.plan = plan
        ov.model.set_plan(plan)
        self.win.go("Overview")
        ov.table.selectRow(0)
        self.app.processEvents()
        self.win.update_album_actions()
        return ov

    def test_MOV5_row_says_not_moved_and_offers_the_move(self):
        from gui.models import PlanModel
        prof = self.profile(operation="move", destination=str(self.dest))
        executor.Executor(pl.build([self.proposal(conf=91)], prof)).run()      # (tagged in place)
        plan = pl.build([self.proposal(conf=91)], prof)
        a = plan["albums"][0]
        self.assertEqual(a["status"], "unchanged")                  # (would read "Up to date" alone)
        ov = self.show(plan)
        text = ov.model.data(ov.model.index(0, 4))
        self.assertIn("not moved (score 91 < 95)", text)
        act = self.win.commands["file_anyway"]
        self.assertTrue(act.isEnabled())
        self.assertEqual(act.text().replace("&", ""), "Move into the Library Layout Anyway")
        self.assertIs(PlanModel, type(ov.model))

    def test_MOV5_the_action_replans_the_row_and_the_move_applies(self):
        # the real look-up path: its engine (a stand-in, no network) gets your ask and the other
        # rows' claims; the row's new plan replaces it; Apply files it
        import engine
        from gui import overview as govw
        prof = self.profile(operation="move", destination=str(self.dest))
        plan = pl.build([self.proposal(conf=91), self.proposal2(conf=91)], prof)
        ov = self.show(plan)
        seen = []

        def fake_plan_each(e, path, keys, emit, **kw):
            seen.append((set(e.file_anyway), list(e.seed_claims), list(keys)))
            props = [self.proposal(conf=91)]
            out = pl.build(props, prof, file_anyway=e.file_anyway, seed_claims=e.seed_claims)
            out["albums"][0]["key"] = keys[0]
            emit(keys[0], out)
            return len(keys)
        with mock.patch.object(engine, "engine_for_thread", lambda *a, **k: type("E", (), {})()), \
                mock.patch.object(govw, "plan_each", fake_plan_each), \
                mock.patch.object(ov, "planning_profile", lambda *a, **k: prof):
            self.win.commands["file_anyway"].trigger()
            self.assertIn("Looking up 1 album again", self.win.statusBar().currentMessage())
            end = time.time() + 30
            while time.time() < end and (ov.running or not seen):
                self.app.processEvents()
                time.sleep(0.02)
        self.assertEqual(seen[0][0], {os.path.realpath(str(self.f1)), os.path.realpath(str(self.f2))})
        self.assertEqual(len(seen[0][2]), 1)                         # (that row alone)
        rows = ov.model.albums
        self.assertEqual(self.ops(rows[0]), {"move"})
        self.assertEqual(self.ops(rows[1]), {"tag"})                 # (the other row as it was)
        executor.Executor(dict(plan, albums=rows)).run()
        self.assertEqual(len(list(self.dest.rglob("*.flac"))), 2)

    def test_MOV5_a_profile_change_drops_the_ask(self):
        ov = self.win.pages["Overview"]
        ov.file_anyway = {os.path.realpath(str(self.f1)): "a move profile"}
        ov.drop_asks_of_other_profiles()
        self.assertEqual(ov.file_anyway, {})

    def test_MOV5_copy_profile_words_and_no_offer_above_the_floor(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        self.show(pl.build([self.proposal(conf=91)], prof))
        self.assertEqual(self.win.commands["file_anyway"].text().replace("&", ""), "Copy into the Library Anyway")
        self.show(pl.build([self.proposal(conf=97)], self.profile(operation="move", destination=str(self.dest))))
        self.assertFalse(self.win.commands["file_anyway"].isEnabled())

    def test_MOV5_a_new_folder_forgets_the_ask(self):
        ov = self.win.pages["Overview"]
        ov.path = str(self.lib)
        asked = {os.path.realpath(str(self.f1)): ov.profile.currentText()}
        ov.file_anyway = dict(asked)
        with mock.patch.object(ov, "list_albums", lambda **k: None), mock.patch.object(ov, "preview", lambda: None):
            ov.set_path(str(self.lib))
            self.assertEqual(ov.file_anyway, asked)                   # (the same folder)
            ov.set_path(str(self.tmp))
        self.assertEqual(ov.file_anyway, {})


if __name__ == "__main__":
    unittest.main()
