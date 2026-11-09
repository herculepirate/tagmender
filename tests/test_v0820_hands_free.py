#!/usr/bin/env python3
"""0.8.20: what a copy is decides its release and its Grouping; albums are filed without asking."""
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402,F401  (isolated settings and state)

import engine as eng  # noqa: E402
import marks  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher as w  # noqa: E402

HIRES = {"hires": True, "cd_frames": False, "not_cd": False}
CODE = "707723007025"


class ADiscAndItsDownload(unittest.TestCase):
    def pick(self, **kw):
        from test_match import album, cand, CFG
        a = album(audio=HIRES, **kw)
        c = [cand(source="discogs", id="sacd", country="US", formats={"cd"}, hires_media=True,
                  barcodes={mc.norm_code(CODE)}),
             cand(source="discogs", id="web", country="US", formats={"digital"})]
        return mc.classify([mc.score_candidate(a, x, CFG) for x in c], CFG)["best"]["cand"]["id"]

    def test_a_barcode_with_a_shops_letters_is_the_barcode(self):
        self.assertEqual(self.pick(barcodes={mc.norm_code("HD" + CODE)}), "sacd")

    def test_your_barcode_names_the_disc(self):
        self.assertEqual(self.pick(barcodes={mc.norm_code(CODE)}), "sacd")

    def test_no_barcode_and_hi_res_audio_is_the_download(self):
        self.assertEqual(self.pick(), "web")


class Marks(unittest.TestCase):
    def info(self, suffix, rate, bits, codec=""):
        with mock.patch("mutagen.File", return_value=NS(info=NS(sample_rate=rate, bits_per_sample=bits, codec=codec))):
            return marks.is_hd(f"/x/a{suffix}")

    def test_above_a_cd_in_bits_or_rate(self):
        self.assertTrue(self.info(".flac", 96000, 24))
        self.assertTrue(self.info(".flac", 44100, 24))
        self.assertTrue(self.info(".flac", 48000, 16))
        self.assertTrue(self.info(".dsf", 2822400, 1))

    def test_a_cd_and_lossy_are_not(self):
        self.assertFalse(self.info(".flac", 44100, 16))
        self.assertFalse(self.info(".mp3", 48000, 0))
        self.assertFalse(self.info(".m4a", 96000, 24, "mp4a.40.2"))
        self.assertTrue(self.info(".m4a", 96000, 24, "alac"))

    def apply(self, targets, folder="/x/Album", on=True, hd=True, read=True):
        patches = [mock.patch.object(marks, "is_hd", return_value=hd)]
        if read:
            patches.append(mock.patch.object(marks.tagio, "read", return_value={}))
        for x in patches:
            x.start()
            self.addCleanup(x.stop)
        return marks.apply(targets, ["/x/1.flac"], folder, {"marks": {"grouping": on}})

    def test_written_where_grouping_is_empty(self):
        self.assertEqual(self.apply([{"title": ["t"]}])[0]["grouping"], ["HD Audio"])

    def test_never_replaces_yours(self):
        self.assertEqual(self.apply([{"grouping": ["Mine"]}])[0]["grouping"], ["Mine"])

    def test_vinyl_by_the_release_or_the_folder_before_hd(self):
        self.assertEqual(self.apply([{"media": ['12" Vinyl']}])[0]["grouping"], ["Vinyl Rip"])
        self.assertEqual(self.apply([{}], "/x/Album [Vinyl]")[0]["grouping"], ["Vinyl Rip"])

    def test_off_and_a_cd_write_nothing(self):
        self.assertNotIn("grouping", self.apply([{}], on=False)[0])
        self.assertNotIn("grouping", self.apply([{}], hd=False)[0])

    def test_a_grouping_in_the_file_is_kept(self):
        with mock.patch.object(marks.tagio, "read", return_value={"grouping": ["Mine"]}):
            self.assertNotIn("grouping", self.apply([{}], read=False)[0])

    def test_vinyl_is_seen_though_the_level_cut_the_media(self):
        with mock.patch.object(marks, "_has_grouping", return_value=False):
            out = marks.apply([{}], ["/x/1.flac"], "/x/A", {"marks": {"grouping": True}}, [{"media": ["Vinyl"]}])
        self.assertEqual(out[0]["grouping"], ["Vinyl Rip"])

    def test_a_profile_that_drops_grouping_gets_none(self):
        prof = {"marks": {"grouping": True}, "level_fields": {"drop": ["Grouping"]}}
        self.assertNotIn("grouping", marks.apply([{}], ["/x/1.flac"], "/x/A", prof)[0])

    def test_the_mark_leaves_the_grouping_policy_as_it_was(self):
        self.assertEqual(pf.from_dict(dict(pf.DEFAULTS, name="t")).policy("grouping"),
                         pf.DEFAULTS["field_policy"].get("default", "replace"))


class ScansNameAnother(unittest.TestCase):
    def final(self, **kw):
        from test_match import cand
        c = cand(source="discogs", id="a", title="First Light")
        best = dict({"cand": c, "clues": {}, "conflict": False}, **kw)
        return {"best": best, "scored": [best]}

    def test_nothing_of_yours_on_the_pick_is_album_known(self):
        ao = mc.scans_name_another(self.final(), {"state": "differs", "found": [], "against": ["catalogue_number"]})
        self.assertTrue(ao["scans"])
        self.assertEqual([t["id"] for t in ao["tied"]], ["a"])

    def test_a_number_read_on_the_scans_keeps_the_pick(self):
        self.assertIsNone(mc.scans_name_another(self.final(), {"state": "differs", "found": ["catalogue_number"]}))

    def test_your_barcode_keeps_the_pick(self):
        self.assertIsNone(mc.scans_name_another(self.final(code="barcode"), {"state": "differs", "found": []}))

    def test_your_link_or_your_own_earlier_choice_keeps_the_pick(self):
        report = {"state": "differs", "found": []}
        self.assertIsNone(mc.scans_name_another(self.final(clues={"source link": "x"}), report))
        self.assertIsNone(mc.scans_name_another(self.final(tagged=True), report))
        self.assertTrue(mc.scans_name_another(self.final(tagged=True, tagger_tagged=True), report))

    def test_scans_that_agree_change_nothing(self):
        self.assertIsNone(mc.scans_name_another(self.final(), {"state": "agrees", "found": []}))


class KnownFromYourTags(unittest.TestCase):
    def run_it(self, tags, artist=1.0, title=0.9, why=None):
        e = eng.Engine.__new__(eng.Engine)
        e.profile = pf.from_dict(dict(pf.DEFAULTS, name="t"))
        out = {"status": "review", "why": ["durations unavailable"],
               "best": {"artist_sim": artist, "title_sim": title, "confidence": 70.0, "notes": []}}
        with mock.patch.object(eng.tagio, "read", return_value=tags), \
                mock.patch.object(eng, "evidence_of", return_value=[]):
            two = ["/x/1.flac", "/x/2.flac"]
            return e._known_from_own_tags(dict(out, why=why or out["why"]), two, {"source_dir": "/x", "files": two})

    def test_a_promo_named_by_its_tags_is_known_and_nothing_is_written(self):
        p = self.run_it({"album": ["Promo"], "artist": ["A"]})
        self.assertEqual((p.status, p.tier, p.targets, p.release_id), ("identified", "album_only", [{}, {}], ""))

    def test_anything_else_in_the_way_stays_unresolved(self):
        tags = {"album": ["Promo"], "artist": ["A"]}
        self.assertIsNone(self.run_it(tags, why=["disc lookup failed (RuntimeError: boom)"]))
        self.assertIsNone(self.run_it(tags, why=["confidence 70.0 < 90.0", "runner-up 1 within 0.0 points"]))

    def test_a_format_preference_for_vinyl_keeps_the_vinyl(self):
        self.assertTrue(mc.prefers_vinyl_or_tape(['12" Vinyl']))
        self.assertTrue(mc.prefers_vinyl_or_tape(["Vinyl", "CD"]))
        self.assertFalse(mc.prefers_vinyl_or_tape(["CD", "Vinyl"]))
        self.assertFalse(mc.prefers_vinyl_or_tape([]))

    def test_no_album_in_the_tags_stays_unresolved(self):
        self.assertIsNone(self.run_it({"artist": ["A"]}))

    def test_it_is_filed_though_its_score_is_low(self):
        p = self.run_it({"album": ["Promo"], "artist": ["A"]})
        self.assertTrue(pl._files_on_evidence(p, pf.from_dict(dict(pf.DEFAULTS, name="t")), 70.0))

    def test_the_summary_carries_what_the_rule_reads(self):
        import match_albums as ma
        from test_match import album, cand, CFG
        res = {"best": mc.score_candidate(album(), cand(source="discogs", id="a", url="u", country="US"), CFG)}
        s = ma.summarize(res)
        self.assertEqual((s["artist_sim"], s["title_sim"]), (1.0, 1.0))

    def test_a_listing_of_another_artist_or_album_is_no_evidence(self):
        self.assertIsNone(self.run_it({"album": ["P"], "artist": ["A"]}, artist=0.4))
        self.assertIsNone(self.run_it({"album": ["P"], "artist": ["A"]}, title=0.5))


class Prof:
    name = "P"
    on = True

    operation = "move"

    def __getitem__(self, key):
        return {"watch": {"recheck_after_update": self.on}, "operation": self.operation,
                "unattended": True}[key]


class Recheck(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT, status TEXT, "
                        "attempts INT, next_try REAL, ts REAL, detail TEXT)")
        self.w = w.Watcher.__new__(w.Watcher)
        self.w._rechecked = set()
        self.prof = Prof()
        self.ready = True

    def row(self, name, status="done", signature="s", ts=None):
        (self.d / name).mkdir()
        self.db.execute("INSERT INTO processed VALUES (?,?,?,?,1,0,?,'')",
                        (str(self.d / name), "P", signature, status, 100.0 if ts is None else ts))

    def groups(self, signature="s"):
        e = NS(albums_in=lambda p: [NS(folder=p, files=[p / "1.flac"])])
        with mock.patch.object(w, "snapshot", return_value=(signature, 0, False)), \
                mock.patch.object(w.Watcher, "destination_ready", staticmethod(lambda p: self.ready)), \
                mock.patch.object(w.Watcher, "key", staticmethod(lambda g: str(g.folder))):
            return self.w.recheck_groups(e, self.prof, self.db)

    def test_albums_filed_before_this_version_are_looked_at(self):
        self.row("a")
        self.assertEqual([g.folder.name for g in self.groups()], ["a"])

    def test_off_by_default_and_off_means_none(self):
        self.assertFalse(pf.DEFAULTS["watch"]["recheck_after_update"])
        self.prof.on = False
        self.row("a")
        self.assertEqual(self.groups(), [])

    def test_undone_and_filed_since_are_left_alone(self):
        self.row("a", status="undone")
        self.row("b", ts=time.time() + 60)
        self.assertEqual(self.groups(), [])

    def test_changed_since_filing_is_yours_and_not_asked_again(self):
        self.row("a", signature="old")
        self.assertEqual(self.groups(signature="new"), [])
        self.assertGreater(self.db.execute("SELECT ts FROM processed").fetchone()[0], 100.0)

    def test_a_few_each_pass(self):
        for i in range(w.RECHECK_PER_PASS + 3):
            self.row(f"a{i}")
        self.assertEqual(len(self.groups()), w.RECHECK_PER_PASS)

    def test_a_share_that_is_out_keeps_its_turn(self):
        self.row("a")
        self.ready = False
        self.assertEqual(self.groups(), [])
        self.assertEqual(self.db.execute("SELECT ts FROM processed").fetchone()[0], 100.0)

    def test_folders_gone_never_hold_up_the_queue(self):
        for i in range(w.RECHECK_PER_PASS):
            self.row(f"gone{i}", ts=50.0)
            (self.d / f"gone{i}").rmdir()
        self.row("real")
        self.assertEqual(self.groups(), [])                     # (they had their turn)
        self.w._rechecked = set()
        self.assertEqual([g.folder.name for g in self.groups()], ["real"])

    def test_a_copy_profile_is_left_out(self):
        self.prof.operation = "copy"
        self.row("a")
        self.assertEqual(self.groups(), [])

    def run_pass(self, tier, has_work=True):
        """One _pass over an album the watched folder also holds, already seen by the scan."""
        self.row("a")
        folder = self.d / "a"
        g, again = NS(folder=folder, files=[folder / "1.flac"]), NS(folder=folder, files=[folder / "1.flac"])
        engine = mock.Mock(job="j")
        engine.albums_in.return_value = [g]
        engine.identify_all.return_value = [NS(source_dir=str(folder), provider_errors={}, status="identified")]
        plan = {"albums": [{"status": "ready", "files": [], "identification": {"tier": tier}, "source_dir": str(folder)}]}
        wt = self.w
        wt.stop, wt.log = False, mock.Mock()
        wt.roots = lambda: [(self.d, self.prof)]
        applied = mock.Mock()
        applied.return_value.run.return_value = {"albums": []}
        with mock.patch.object(w.Watcher, "is_library", staticmethod(lambda *a: False)), \
                mock.patch.object(w.Watcher, "library_inside", staticmethod(lambda *a: [])), \
                mock.patch.object(w.Watcher, "shallow", staticmethod(lambda *a: False)), \
                mock.patch.object(w.Watcher, "key", staticmethod(lambda x: str(x.folder))), \
                mock.patch.object(w.Watcher, "due", return_value=(False, "done")), \
                mock.patch.object(w.Watcher, "recheck_groups", return_value=[again]), \
                mock.patch.object(w.eng_mod, "Engine", return_value=engine), \
                mock.patch.object(w.executor, "job_running", return_value=False), \
                mock.patch.object(w.executor, "Executor", applied), \
                mock.patch.object(w.ex, "load_rules", return_value=[]), \
                mock.patch.object(w.Watcher, "record"), mock.patch.object(w.Watcher, "filed"), \
                mock.patch.object(w.Watcher, "_status_of", return_value="done"), \
                mock.patch.object(w.planmod, "build", return_value=plan), \
                mock.patch.object(w.planmod, "has_work", return_value=has_work):
            wt._pass(self.db, {})
        return engine, applied

    def test_an_album_still_in_the_watched_folder_is_looked_at_too(self):
        engine, _ = self.run_pass("exact", has_work=False)
        engine.identify_all.assert_called_once()

    def test_a_proven_release_is_applied(self):
        _, applied = self.run_pass("exact")
        applied.assert_called_once()

    def test_an_album_known_result_never_replaces_a_filed_album(self):
        _, applied = self.run_pass("album_only")
        applied.assert_not_called()
        self.assertGreater(self.db.execute("SELECT ts FROM processed").fetchone()[0], 100.0)


class OneWindow(unittest.TestCase):
    def test_a_second_launch_hands_over_and_the_window_comes_forward(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication, QWidget
        from gui import window as gw
        app = QApplication.instance() or QApplication(["t"])
        name = gw.instance_name() + "-test"
        win = QWidget()
        win.isMinimized, win.showNormal, win.raise_, win.activateWindow = (lambda: True), mock.Mock(), mock.Mock(), mock.Mock()
        self.assertFalse(gw.hand_over_to_running(name))
        server = gw.listen_for_second_launch(name, win)
        self.assertTrue(gw.hand_over_to_running(name))
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        win.showNormal.assert_called_once()
        win.raise_.assert_called_once()
        server.close()
        self.assertIs(app, QApplication.instance())


if __name__ == "__main__":
    unittest.main()
