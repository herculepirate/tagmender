# Match Files to Tracks (gui/manual_match.py), offscreen: the automatic match by track number,
# title and length, the plan's own pairing as the starting point, dragging files onto tracks and
# matching from the keyboard, unmatched files and tracks and length differences, the pairs turned
# into your edits of the album (plan.apply_edits' file_tags, edit["match"]), a new plan keeping
# them, Apply writing them through the executor and Undo putting the files back; a release picked
# in the search sheet handed back to the match sheet; the main window's drop adding only folders
# the preview does not hold; a release still loading or failed never used, the pasted link, the
# picker's wiring, Backspace and Alt+M, a track's file dragged back. Generated audio in temporary
# folders, a stubbed lookup (no network), no real config or keys, nothing under /media.
# Patch sites: gui.versions.make_lookup (the lookup the sheet's worker uses).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import os
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
PLUGIN = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
              else Path("plugins_builtin/discogs")).expanduser()
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

try:
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from gui import manual_match as mm
except ImportError:                                           # GUI is optional
    QApplication = None

FFMPEG = shutil.which("ffmpeg")
MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
OTHER = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"
TWIN = "2c0a19bf-6b8c-4df9-a06d-8f5c2d4cf4b3"          # (another release with as many tracks as files)
GONE = "3d1b2ac0-7c9d-4e0a-b17e-9a6d3e5d05c4"          # (a release MusicBrainz does not answer for)


def audio(p, secs=1, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                    "-c:a", "flac", *meta, "-y", str(p)], check=True)
    return p


def mb_rel(rid, titles, lens):
    return {"id": rid, "title": "Alpha", "date": "2005-03-01", "country": "GB", "status": "Official",
            "artist-credit": [{"name": "Band", "joinphrase": "", "artist": {"id": "a-1", "name": "Band",
                                                                            "sort-name": "Band"}}],
            "media": [{"format": "CD", "position": 1, "track-count": len(titles), "tracks": [
                {"id": f"{rid}-t{i}", "position": i + 1, "number": str(i + 1), "title": t, "length": s * 1000,
                 "recording": {"id": f"{rid}-r{i}", "title": t, "length": s * 1000}}
                for i, (t, s) in enumerate(zip(titles, lens))]}]}


RELEASES = {MBID: mb_rel(MBID, ["Opening", "Closing"], [1, 1]),
            OTHER: mb_rel(OTHER, ["Opening", "Middle", "Closing"], [1, 40, 1]),
            TWIN: mb_rel(TWIN, ["Opening", "Closing"], [1, 1])}


class FakeHttp:
    """MusicBrainz releases by ID; anything else is recorded (the test fails) and refused."""

    def __init__(self):
        self.calls, self.unexpected = [], []

    def get_json(self, url, host, *a, **kw):
        self.calls.append(url)
        if url.startswith(f"https://musicbrainz.org/ws/2/release/{GONE}?"):
            raise RuntimeError("MusicBrainz has no such release (404).")      # (refused on purpose)
        for rid, rel in RELEASES.items():
            if url.startswith(f"https://musicbrainz.org/ws/2/release/{rid}?"):
                return rel
        self.unexpected.append(url)
        raise RuntimeError(f"no network in tests: {url}")


class FakeLookup:
    def __init__(self, http):
        self.http, self.use_mb, self.use_discogs, self.dm, self.notes = http, True, False, None, {}

    def close(self):
        pass


def settle(app, n=8):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


# --------------------------------------------------------------------------
# the matching itself
# --------------------------------------------------------------------------
@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Matching(unittest.TestCase):
    def f(self, path, title="", number=0, secs=0.0, disc=0):
        return {"path": path, "name": path, "title": title, "number": number, "disc": disc, "secs": secs,
                "planned_number": number, "planned_disc": disc}

    def t(self, number, title, secs=0.0, disc=1):
        return {"disc": disc, "number": number, "title": title, "secs": secs, "artist": "",
                "tags": {"title": [title], "tracknumber": [str(number)], "discnumber": [str(disc)]}}

    def test_auto_match_by_number_title_and_length_each_once(self):
        tracks = [self.t(1, "Opening", 200), self.t(2, "Middle Part", 300), self.t(3, "Closing", 250)]
        files = [self.f("c", "closing", 0, 251), self.f("a", "", 1, 199), self.f("b", "Midle Part", 0, 0),
                 self.f("x", "Something Else", 0, 999)]
        self.assertEqual(mm.auto_match(files, tracks), {"c": 2, "a": 0, "b": 1})   # (x fits nothing)

    def test_auto_match_keeps_your_pairs_and_numbers_on_another_disc_do_not_count(self):
        tracks = [self.t(1, "A", disc=1), self.t(1, "B", disc=2)]
        files = [self.f("one", "", 1, disc=2), self.f("two", "A", 1, disc=1)]
        self.assertEqual(mm.auto_match(files, tracks), {"one": 1, "two": 0})
        self.assertEqual(mm.auto_match(files, tracks, keep={"one": 0}), {"one": 0})    # (track 0 is taken)

    def test_planned_pairs_and_length_words(self):
        tracks = [self.t(1, "A", 100), self.t(2, "B", 100)]
        files = [self.f("p", "B", 2, 102.6), self.f("q", "A", 0)]
        self.assertEqual(mm.planned_pairs(files, tracks), {"p": 1})
        d, ok = mm.length_diff(files[0], tracks[1])
        self.assertTrue(ok)                                    # (3 s: within the matcher's tolerance)
        self.assertEqual(mm.diff_words(d), "+0:03")
        d, ok = mm.length_diff(self.f("r", secs=70), tracks[0])
        self.assertEqual((mm.diff_words(d), ok), ("−0:30", False))
        self.assertEqual(mm.length_diff(files[1], tracks[0]), (None, True))
        self.assertEqual(mm.diff_words(0.2), "same length")
        self.assertEqual((mm.clock(3725), mm._secs("1:02:05"), mm._secs("3:25")), ("1:02:05", 3725.0, 205.0))

    def album(self, changes=({}, {})):
        return {"source_dir": "/x/Album", "status": "ready",
                "identification": {"status": "identified", "provider": "musicbrainz", "release_id": MBID},
                "files": [{"src": "a", "op": "tag", "changes": dict(changes[0])},
                          {"src": "b", "op": "tag", "changes": dict(changes[1])},
                          {"src": "c", "op": "tag", "changes": {"title": ["Planned"], "tracknumber": ["3"]}}]}

    def test_as_edit_only_what_differs_from_the_plan_your_own_edits_win(self):
        tracks = [self.t(1, "One"), self.t(2, "Two"), self.t(3, "Three")]
        files = [self.f("a", number=1), self.f("b", number=2), self.f("c", number=3)]
        old = {"files": ["a", "b", "c"], "tags": {"label": ["Mine"]}, "file_tags": {"b": {"title": ["My Title"]}},
               "cover": None}
        e = mm.as_edit(self.album(), files, tracks, {"a": 0, "b": 2}, MBID, old)
        self.assertNotIn("a", e["file_tags"])                  # (the plan's own pair: nothing to change)
        self.assertEqual(e["file_tags"]["b"]["title"], ["My Title"])     # (your edit stays)
        self.assertEqual(e["file_tags"]["b"]["tracknumber"], ["3"])      # (the track's own fields)
        self.assertEqual(e["file_tags"]["c"], {"title": pl.KEEP, "tracknumber": pl.KEEP})   # (unmatched)
        self.assertEqual(e["tags"], {"label": ["Mine"]})
        self.assertEqual(e["match"]["pairs"], {"a": 0, "b": 2})
        self.assertEqual(old["file_tags"], {"b": {"title": ["My Title"]}})   # (never changed in place)
        again = mm.as_edit(self.album(), files, tracks, {"a": 0, "b": 1, "c": 2}, MBID, e)
        self.assertEqual(again["file_tags"], {"b": {"title": ["My Title"]}})   # (the earlier match taken back)
        other = mm.as_edit(self.album(), files, tracks, {"a": 0}, OTHER, None)
        self.assertEqual(other["file_tags"]["a"]["title"], ["One"])      # (another release: every pair)

    def test_a_move_takes_every_own_field_of_the_track_and_drops_the_old_tracks(self):
        a, b = self.t(1, "One"), self.t(2, "Two")
        a["tags"].update(isrc=["GB0001"], composer=["Ann"], **{"performer:guitar": ["Bo"]})
        b["tags"].update(composer=["Cy"], musicbrainz_workid=["w2"])
        files = [self.f("a", number=1), self.f("b", number=2), self.f("c", number=3)]
        e = mm.as_edit(self.album(), files, [a, b], {"a": 1, "b": 0}, MBID, None)
        got = e["file_tags"]["a"]
        self.assertEqual((got["title"], got["composer"], got["musicbrainz_workid"]), (["Two"], ["Cy"], ["w2"]))
        self.assertIsNone(got["isrc"])                         # (track one's ISRC and credits: removed)
        self.assertIsNone(got["performer:guitar"])
        self.assertIsNone(e["file_tags"]["b"]["musicbrainz_workid"])
        self.assertEqual(e["match"]["unmatched"], ["c"])
        # (the release's own fields: what differs between its tracks, and the known track fields)
        keys = mm.track_keys([{"album": ["X"], "genre": ["Rock"], "title": ["A"], "~length": ["1:00"],
                               "performer:guitar": ["Bo"]},
                              {"album": ["X"], "genre": ["Pop"], "title": ["A"], "performer:guitar": ["Bo"]}])
        self.assertEqual(keys, {"genre", "title", "performer:guitar"})   # (a credit alike on every track too)

    def test_taking_a_match_back_keeps_what_you_edited_since(self):
        tracks = [self.t(1, "One"), self.t(2, "Two"), self.t(3, "Three")]
        files = [self.f("a", number=1), self.f("b", number=2), self.f("c", number=3)]
        e = mm.as_edit(self.album(), files, tracks, {"a": 1, "b": 0, "c": 2}, MBID, None)
        self.assertEqual(e["match"]["set"]["a"]["title"], ["Two"])     # (the values the match wrote)
        e["file_tags"]["a"]["title"] = ["Typed After"]               # (your edit in the inspector, later)
        again = mm.as_edit(self.album(), files, tracks, {"a": 0, "b": 1, "c": 2}, MBID, e)
        self.assertEqual(again["file_tags"], {"a": {"title": ["Typed After"]}})
        older = dict(e, match=dict(e["match"], set={"a": list(e["match"]["set"]["a"])}))   # (an older edit's names)
        self.assertNotIn("a", mm.as_edit(self.album(), files, tracks, {"a": 0, "b": 1, "c": 2}, MBID,
                                         older)["file_tags"])

    def test_refs_and_what_can_be_matched(self):
        a = self.album()
        self.assertEqual(mm.planned_ref(a), MBID)
        self.assertEqual(mm.planned_ref(dict(a, identification={"status": "identified", "provider": "discogs",
                                                                 "release_id": "77"})), "discogs:77")
        dg = {"status": "identified", "provider": "discogs"}
        self.assertEqual(mm.planned_ref(dict(a, identification=dict(dg, release_id="abc"))), "")   # (refused)
        self.assertEqual(mm.planned_ref(dict(a, identification=dict(dg, release_id="discogs:77"))), "discogs:77")
        self.assertEqual(mm.planned_ref(dict(a, identification={"status": "unresolved"})), "")
        ident = {"status": "identified", "provider": "acoustid", "release_id": OTHER, "tier": "recording"}
        self.assertEqual(mm.planned_ref(dict(a, identification=ident)), "")         # (a recording, no release)
        self.assertEqual(mm.planned_ref(dict(a, identification=dict(ident, tier="exact"))), "")
        web = {"status": "identified", "provider": "web:discogs", "release_id": "88", "tier": "exact"}
        self.assertEqual(mm.planned_ref(dict(a, identification=web)), "discogs:88")
        self.assertTrue(mm.same_ref(mm.planned_ref(dict(a, identification=web)), "https://www.discogs.com/release/88"))
        self.assertEqual(mm.planned_ref(dict(a, identification=dict(web, provider="web:Deezer"))), "")
        self.assertEqual(mm.planned_ref(dict(a, identification=dict(web, provider="musicbrainz+discogs",
                                                                     release_id="not-an-mbid"))), "")
        self.assertTrue(mm.same_ref(f"https://musicbrainz.org/release/{MBID}", MBID))
        self.assertFalse(mm.same_ref("", ""))
        self.assertTrue(mm.can_match(a))
        self.assertFalse(mm.can_match(dict(a, status="blocked")))
        self.assertFalse(mm.can_match(None))

    def test_release_tracks_from_musicbrainz(self):
        http = FakeHttp()
        rel = mm.release_tracks(FakeLookup(http), f"https://musicbrainz.org/release/{OTHER}",
                                pf.from_dict({"name": "t", "sources": [{"path": "/nonexistent"}]}))
        self.assertEqual([(t["number"], t["title"], t["secs"]) for t in rel["tracks"]],
                         [(1, "Opening", 1.0), (2, "Middle", 40.0), (3, "Closing", 1.0)])
        self.assertEqual(rel["tracks"][1]["tags"]["musicbrainz_trackid"], [f"{OTHER}-t1"])
        self.assertEqual((rel["title"], rel["artist"]), ("Alpha", "Band"))
        self.assertFalse(http.unexpected)
        with self.assertRaises(LookupError):
            mm.release_tracks(FakeLookup(http), "not a release")
        off = FakeLookup(http)
        off.use_mb = False
        with self.assertRaises(LookupError):
            mm.release_tracks(off, MBID)

    def test_release_tracks_from_discogs(self):
        if not (PLUGIN / "discogs_mapping.py").exists():
            self.skipTest(f"Discogs Enrichment not found at {PLUGIN}")
        import match_albums as ma

        class Http:
            def get_json(self, url, host, *a, **kw):
                assert url == "https://api.discogs.com/releases/55", url
                return {"id": 55, "title": "Beta", "artists": [{"name": "Band", "id": 1}], "year": 2001,
                        "tracklist": [{"type_": "track", "position": p, "title": t, "duration": d}
                                      for p, t, d in (("1-1", "A", "1:00"), ("1-2", "B", "2:00"),
                                                      ("2-1", "C", "3:00"))],
                        "formats": [{"name": "CD", "qty": "2"}]}
        lk = FakeLookup(Http())
        lk.use_discogs, lk.dm = True, ma.load_plugin(str(PLUGIN))
        rel = mm.release_tracks(lk, "discogs:55")
        self.assertEqual([(t["disc"], t["number"], t["title"], t["secs"]) for t in rel["tracks"]],
                         [(1, 1, "A", 60.0), (1, 2, "B", 120.0), (2, 1, "C", 180.0)])
        self.assertEqual(rel["tracks"][2]["tags"]["totaldiscs"], ["2"])
        self.assertEqual(rel["tracks"][0]["tags"]["artist"], ["Band"])     # (the engine's per-track Discogs fields)
        self.assertEqual(rel["ref"], "discogs:55")
        # the engine's per-track fields failing, or not lining up with the tracklist: the numbers only
        import engine
        real = engine.discogs_parts

        def short(*a, **kw):
            album_f, per_track, n = real(*a, **kw)
            return album_f, list(per_track)[:-1], n
        numbers = [{"title": ["A"], "tracknumber": ["1"], "discnumber": ["1"], "totaltracks": ["2"], "totaldiscs": ["2"]},
                   {"title": ["B"], "tracknumber": ["2"], "discnumber": ["1"], "totaltracks": ["2"], "totaldiscs": ["2"]},
                   {"title": ["C"], "tracknumber": ["1"], "discnumber": ["2"], "totaltracks": ["1"], "totaldiscs": ["2"]}]
        for how in ({"side_effect": RuntimeError("no parts")}, {"side_effect": short}):
            with mock.patch("engine.discogs_parts", **how):
                got = mm.release_tracks(lk, "discogs:55")
            self.assertEqual([t["tags"] for t in got["tracks"]], numbers, how)
            self.assertEqual([t["artist"] for t in got["tracks"]], ["", "", ""])


# --------------------------------------------------------------------------
# the main window's drop
# --------------------------------------------------------------------------
@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Dropping(unittest.TestCase):
    class Bar:
        def __init__(self):
            self.said = []

        def showMessage(self, text, *_):
            self.said.append(text)

    class Page:
        def __init__(self, path, plan=None, running=False):
            self.path, self.plan, self.running, self.set = path, plan, running, []

        def set_path(self, p):
            self.set.append(p)

    class Win:
        def __init__(self, page):
            self.pages, self.bar = {"Overview": page}, Dropping.Bar()

        def statusBar(self):
            return self.bar

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-mmdrop-"))
        for d in ("lib/A", "lib/B", "other/C", "loose"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "loose" / "x.flac").write_bytes(b"")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_new_folders_are_added_and_the_rest_said(self):
        plan = {"albums": [{"files": [{"src": str(self.tmp / "loose" / "x.flac")}]}]}
        page = self.Page(str(self.tmp / "lib"), plan)
        win = self.Win(page)
        got = mm.add_dropped(win, [str(self.tmp / "lib/A"), str(self.tmp / "loose/x.flac"), str(self.tmp / "other/C"),
                                   str(self.tmp / "other/C")])
        self.assertEqual(got, [str(self.tmp / "other/C")])
        self.assertEqual(page.set, [str(self.tmp / "other/C")])
        self.assertIn("already in the preview: A, x.flac", win.bar.said[-1])
        page.set.clear()
        self.assertEqual(mm.add_dropped(win, [str(self.tmp / "lib/B")]), [])
        self.assertEqual(page.set, [])
        self.assertEqual(win.bar.said[-1], "Already in the preview: B.")

    def test_first_new_previewed_none_while_running_and_without_a_preview(self):
        page = self.Page(None)
        win = self.Win(page)
        got = mm.add_dropped(win, [str(self.tmp / "lib/A"), str(self.tmp / "other/C")])
        self.assertEqual((got, page.set), ([str(self.tmp / "lib/A"), str(self.tmp / "other/C")],
                                           [str(self.tmp / "lib/A")]))
        self.assertIn("drop C again", win.bar.said[-1])
        busy = self.Page(None, running=True)
        win = self.Win(busy)
        self.assertEqual(mm.add_dropped(win, [str(self.tmp / "lib/A")]), [])
        self.assertEqual((busy.set, win.bar.said), ([], [mm.BUSY]))   # (a page that cannot hold it)
        busy.add_later = lambda paths: list(paths)                         # (the Overview: held for later)
        self.assertEqual(mm.add_dropped(win, [str(self.tmp / "lib/A")]), [str(self.tmp / "lib/A")])
        self.assertEqual(busy.set, [])


# --------------------------------------------------------------------------
# the sheet, on the real window and inspector
# --------------------------------------------------------------------------
@unittest.skipIf(QApplication is None or FFMPEG is None, "PySide6 and ffmpeg are needed")
class Sheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-mm-"))
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        __import__("app_settings").set_value("add_folder", "look_up")   # (these drop a folder to look it up)
        self.lib = self.tmp / "lib"
        self.album = self.lib / "Band" / "Alpha"
        # (the files' own numbers are the wrong way round: the plan pairs them so)
        self.f1 = audio(self.album / "01 opening.flac", title="Closing", tracknumber="2", artist="Band")
        self.f2 = audio(self.album / "02 closing.flac", title="Opening", tracknumber="1", artist="Band")
        self.http = FakeHttp()
        self.patch = mock.patch("gui.versions.make_lookup", lambda *_a, **_k: FakeLookup(self.http))
        self.patch.start()
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.gui.sheets.shown.clear()
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()
        settle(self.app)
        self.ov = self.win.pages["Overview"]
        self.win.go("Overview")
        self.ov.path = str(self.lib)
        self.plan = pl.build([self.proposal()], self.profile())
        self.ov.planned(self.plan)
        settle(self.app)
        self.a = self.plan["albums"][0]
        self.previews = []
        self.ov.preview = lambda: self.previews.append(dict(self.ov.forced))   # (no engine run here)
        # (a release chosen for an album of the list: that album alone looked up again - counted
        #  as its new preview)
        self.ov.look_up = lambda *_a, **_k: self.previews.append(dict(self.ov.forced))

    def tearDown(self):
        self.idle()
        self.patch.stop()
        self.win.close()
        self.win.deleteLater()
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        logsetup.reset()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertFalse(self.http.unexpected)

    def profile(self):
        return pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}],
                             "thresholds": {"organize_min_confidence": 100}})

    def targets(self):
        import mb_tags
        return [{k: v for k, v in t.items() if not k.startswith("~")}
                for t in mb_tags.release_tags(RELEASES[MBID], self.profile().picard_tags())]

    def proposal(self, mapping=(1, 0)):
        t = self.targets()
        return pl.Proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)],
                           targets=[t[mapping[0]], t[mapping[1]]], provider="musicbrainz", release_id=MBID,
                           confidence=97.0, margin=20.0, evidence=["artist"], release={"album": ["Alpha"]})

    def idle(self, dlg=None, wait=30):
        from gui.work import _alive
        end = time.time() + wait
        settle(self.app, 3)
        owners = [dlg] if dlg is not None else []
        while time.time() < end and (self.win.threads() or any(
                _alive(t) for o in owners for t, _w in getattr(o, "_threads", []))):
            settle(self.app, 2)
        settle(self.app, 3)

    def opened(self, ref=None):
        dlg = mm.open_manual_match(self.ov, self.a, ref)
        self.assertIsNotNone(dlg)
        self.idle(dlg)
        return dlg

    def row_of(self, table, path):
        return next(r for r in range(table.rowCount()) if table.item(r, 0).data(Qt.UserRole) == path)

    def test_opens_with_the_plans_pairs_both_panes_and_what_is_left(self):
        dlg = self.opened()
        self.assertTrue(dlg.isWindowModal() if hasattr(dlg, "isWindowModal") else
                        dlg.windowModality() == Qt.WindowModal)
        self.assertEqual(dlg.pairs, {str(self.f1): 1, str(self.f2): 0})    # (as the plan paired them)
        self.assertEqual(dlg.file_table.rowCount(), 2)
        self.assertEqual(dlg.track_table.rowCount(), 2)
        self.assertEqual(dlg.track_table.item(0, 3).text(), "02 closing.flac")
        self.assertEqual(dlg.track_table.item(0, 4).text(), "same length")
        self.assertEqual(dlg.release_label.text(), "Alpha — Band")
        self.assertIn("2 of 2 files matched", dlg.status.text())
        self.assertTrue(dlg.ok.isEnabled())
        self.assertEqual(len(self.http.calls), 1)                           # (one lookup, on a worker)

    def test_drag_files_onto_a_track_and_unmatch_from_the_keyboard(self):
        dlg = self.opened()
        tt = dlg.track_table
        mime = QMimeData()
        mime.setData(mm.MIME, f"{self.f1}\n{self.f2}".encode())
        pos = tt.visualItemRect(tt.item(0, 1)).center()
        ev = QDropEvent(QPointF(pos), Qt.LinkAction, mime, Qt.LeftButton, Qt.NoModifier)
        tt.dropEvent(ev)
        self.assertEqual(dlg.pairs, {str(self.f1): 0, str(self.f2): 1})    # (in order, from the track dropped on)
        self.assertEqual(tt.item(0, 3).text(), "01 opening.flac")
        tt.setFocus()
        tt.selectRow(1)
        dlg.activateWindow()
        settle(self.app)
        QTest.keyClick(tt, Qt.Key_Delete)
        settle(self.app)
        self.assertEqual(dlg.pairs, {str(self.f1): 0})
        self.assertEqual(tt.item(1, 3).text(), "No file")
        self.assertIn("1 file not matched", dlg.status.text())
        self.assertIn("1 track without a file", dlg.status.text())
        ft = dlg.file_table
        ft.selectRow(self.row_of(ft, str(self.f2)))
        tt.selectRow(1)
        self.assertTrue(dlg.match_btn.isEnabled())
        dlg.match_btn.click()
        self.assertEqual(dlg.pairs, {str(self.f1): 0, str(self.f2): 1})
        dlg.auto_btn.click()                                   # (by number, title and length again)
        self.assertEqual(dlg.pairs, {str(self.f1): 1, str(self.f2): 0})

    def test_use_makes_edits_a_new_plan_keeps_apply_writes_undo_restores(self):
        dlg = self.opened()
        dlg.match([str(self.f1), str(self.f2)], 0)             # (the files in their real order)
        dlg.use()
        self.assertFalse(dlg.isVisible())
        self.assertEqual(len(self.previews), 1)                # (re-planned)
        self.assertEqual(self.ov.forced, {})                   # (the plan's own release: none forced)
        insp = self.win.inspector
        e = pl.edit_for(insp.edits, os.path.realpath(self.album), [str(self.f1), str(self.f2)])
        self.assertEqual(e["match"]["pairs"], {str(self.f1): 0, str(self.f2): 1})
        self.assertEqual(e["file_tags"][str(self.f1)]["title"], ["Opening"])
        self.assertEqual(e["file_tags"][str(self.f1)]["musicbrainz_trackid"], [f"{MBID}-t0"])
        self.assertTrue(insp.can_undo_edit())                 # (Edit > Undo Edit takes them back)
        # the next preview keeps them (the planner applies your edits), Apply writes them
        p = pl.build([self.proposal()], self.profile(), edits=insp.edits_for_preview())
        f1, f2 = p["albums"][0]["files"]
        self.assertEqual((f1["changes"]["title"], f1["changes"]["tracknumber"]), (["Opening"], ["1"]))
        self.assertEqual(f2["changes"]["title"], ["Closing"])
        self.assertEqual(tagio.read(self.f1)["title"], ["Closing"])       # (a plan writes nothing)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual((tagio.read(self.f1)["title"], tagio.read(self.f1)["tracknumber"]), (["Opening"], ["1"]))
        self.assertEqual(tagio.read(self.f2)["title"], ["Closing"])
        executor.undo(res["job"])
        self.assertEqual(tagio.read(self.f1)["title"], ["Closing"])

    def test_reopened_with_your_matches_and_unmatched_files_keep_their_tags(self):
        dlg = self.opened()
        dlg.pairs = {str(self.f1): 0}
        dlg.use()
        again = self.opened()
        self.assertEqual(again.pairs, {str(self.f1): 0})
        e = self.win.inspector.edit_of(self.a)
        kept = e["file_tags"][str(self.f2)]
        self.assertTrue(kept and all(v == pl.KEEP for v in kept.values()))
        self.assertEqual(set(kept), set(self.a["files"][1]["changes"]))   # (what the plan would have changed)
        p = pl.build([self.proposal()], self.profile(), edits=self.win.inspector.edits_for_preview())
        f2 = p["albums"][0]["files"][1]
        self.assertEqual(f2["changes"], {})                      # (left with its own tags)
        again.reject()

    def test_another_release_chosen_in_the_search_sheet_is_forced(self):
        dlg = self.opened()
        with mock.patch("gui.versions.search", return_value={"rows": []}):
            pick = dlg.choose_release()
            self.idle(pick)
        pick.stop()
        pick.reject()
        dlg.choose(OTHER)                                      # (as the picker hands its row's ref back)
        self.idle(dlg)
        self.assertEqual(dlg.track_table.rowCount(), 3)
        self.assertIn("3 tracks and the album 2 files", dlg.status.text())
        self.assertFalse(dlg.ok.isEnabled())                  # (the planner cannot line them up yet)
        with mock.patch.object(mm, "ENGINE_TAKES_MATCHES", True):
            dlg.update_buttons()
            self.assertTrue(dlg.ok.isEnabled())
            self.assertIn("1 track without a file", dlg.counts())
            dlg.use()
        self.assertEqual(len(self.previews), 1)
        key = os.path.realpath(str(self.album))
        self.assertEqual(self.ov.forced[key][0]["ref"], OTHER)
        e = self.win.inspector.edit_of(self.a)
        self.assertEqual(e["match"]["ref"], OTHER)
        self.assertTrue(all("title" in v for v in e["file_tags"].values()))

    def test_a_release_that_fails_or_is_still_loading_cannot_be_used(self):
        dlg = self.opened()
        dlg.choose(OTHER)
        self.assertFalse(dlg.ok.isEnabled())                  # (still reading the new release)
        self.assertIn("Still reading", dlg.blocked())
        self.idle(dlg)
        self.assertEqual(dlg.track_table.rowCount(), 3)
        dlg.choose(MBID)
        self.idle(dlg)
        before = (dlg.ref, list(dlg.tracks), dict(dlg.pairs))
        dlg.choose(GONE)
        self.idle(dlg)
        self.assertIn("no such release", dlg.status.text())
        self.assertEqual((dlg.ref, dlg.tracks, dlg.pairs), before)   # (the release shown before stays)
        self.assertTrue(dlg.ok.isEnabled())
        dlg.use()                                              # (with the plan's release, not the failed one)
        self.assertEqual(self.ov.forced, {})
        self.assertEqual(self.win.inspector.edit_of(self.a)["match"]["ref"], MBID)

    def test_a_failed_first_release_leaves_nothing_to_use(self):
        dlg = self.opened(GONE)
        self.assertEqual(dlg.track_table.rowCount(), 0)
        self.assertFalse(dlg.ok.isEnabled())
        dlg.use()
        self.assertEqual((self.previews, self.ov.forced), ([], {}))
        self.assertIsNone(self.win.inspector.edit_of(self.a))

    def test_a_failed_first_lookup_still_lists_the_albums_files(self):
        dlg = self.opened(GONE)
        self.assertEqual(dlg.file_table.rowCount(), 2)         # (the files read stay, to select and see)
        self.assertEqual({dlg.file_table.item(r, 0).data(Qt.UserRole) for r in range(2)},
                         {str(self.f1), str(self.f2)})
        self.assertIn("no such release", dlg.status.text())
        self.assertIn("Choose another release, or try again.", dlg.status.text())
        self.assertFalse(dlg.loading)
        dlg.choose(MBID)                                       # (another release: the files are not read again)
        self.idle(dlg)
        self.assertEqual(dlg.pairs, {str(self.f1): 1, str(self.f2): 0})
        self.assertTrue(dlg.ok.isEnabled())

    def test_a_late_answer_from_an_older_lookup_is_dropped(self):
        import threading
        gate = threading.Event()
        http = self.http

        class Slow(FakeHttp):
            def get_json(self, url, host, *a, **kw):
                if OTHER in url:
                    gate.wait(20)                              # (release A answers only after B)
                return http.get_json(url, host, *a, **kw)
        dlg = self.opened()
        with mock.patch("gui.versions.make_lookup", lambda *_a, **_k: FakeLookup(Slow())):
            dlg.choose(OTHER)
            settle(self.app)
            dlg.choose(TWIN)
            end = time.time() + 20
            while time.time() < end and dlg.ref != TWIN:
                settle(self.app, 2)
            gate.set()
            self.idle(dlg)
        self.assertEqual(dlg.ref, TWIN)
        self.assertEqual(dlg.track_table.rowCount(), 2)
        self.assertEqual(sorted(dlg.pairs.values()), [0, 1])
        self.assertEqual([t["title"] for t in dlg.tracks], ["Opening", "Closing"])
        self.assertFalse(dlg.loading)

    def test_an_album_the_plan_renames_can_be_matched_differently(self):
        self.a["files"][0]["dst"] = str(self.album / "02 Closing.flac")     # (the plan renames f1)
        self.assertTrue(mm.renames(self.a))
        dlg = self.opened()
        self.assertTrue(dlg.ok.isEnabled())                    # (the plan's own pairs)
        dlg.match([str(self.f1), str(self.f2)], 0)             # (swapped: plan.apply_edits names f1 after track one)
        self.assertTrue(dlg.ok.isEnabled())
        self.assertEqual(dlg.blocked(), "")
        dlg.unmatch_paths([str(self.f2)])                      # (a file left unmatched stays where it is)
        self.assertNotIn("renames or moves", dlg.blocked())
        dlg.reject()

    def test_a_swapped_pair_is_named_after_its_new_track(self):
        (self.tmp / "dest").mkdir()
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}], "operation": "move",
                             "destination": str(self.tmp / "dest")})
        p = pl.build([self.proposal()], prof)
        a = p["albums"][0]
        self.assertTrue(mm.renames(a))
        planned = {fe["src"]: fe["dst"] for fe in a["files"]}
        dlg = mm.open_manual_match(self.ov, a)
        self.idle(dlg)
        dlg.match([str(self.f1), str(self.f2)], 0)
        dlg.use()
        e = self.win.inspector.edit_of(a)
        pl.apply_edits(a, e, prof)
        got = {fe["src"]: fe["dst"] for fe in a["files"]}
        self.assertEqual(got[str(self.f1)], planned[str(self.f2)])       # (f1 is now track one: its name)
        self.assertEqual(got[str(self.f2)], planned[str(self.f1)])
        res = executor.Executor(p).run()                                   # (Apply: both moved, then Undo)
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(tagio.read(got[str(self.f1)])["title"], ["Opening"])
        self.assertEqual(tagio.read(got[str(self.f2)])["title"], ["Closing"])
        self.assertFalse(self.f1.exists() or self.f2.exists())
        executor.undo(res["job"])
        self.assertEqual(tagio.read(self.f1)["title"], ["Closing"])        # (both back, with their own tags)
        self.assertEqual(tagio.read(self.f2)["title"], ["Opening"])
        self.assertFalse(any(Path(d).exists() for d in got.values()))

    def test_a_file_left_unmatched_in_a_moved_album_stays_where_it_is(self):
        (self.tmp / "dest").mkdir()
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}], "operation": "move",
                             "destination": str(self.tmp / "dest")})
        p = pl.build([self.proposal()], prof)
        a = p["albums"][0]
        dlg = mm.open_manual_match(self.ov, a)
        self.idle(dlg)
        dlg.pairs = {str(self.f1): 0}
        dlg.fill()
        dlg.use()
        pl.apply_edits(a, self.win.inspector.edit_of(a), prof)
        f1 = next(fe for fe in a["files"] if fe["src"] == str(self.f1))
        f2 = next(fe for fe in a["files"] if fe["src"] == str(self.f2))
        self.assertEqual(f2["dst"], str(self.f2))
        self.assertIn(f2["op"], ("tag", "none"))
        self.assertNotEqual(f1["dst"], str(self.f1))
        before = {f: tagio.read(f) for f in (self.f1, self.f2)}
        res = executor.Executor(p).run()                                   # (Apply: f1 moved, f2 tagged in place)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(self.f2.exists() and Path(f1["dst"]).exists())
        self.assertFalse(self.f1.exists())
        got = tagio.read(self.f2)
        self.assertEqual({k: got.get(k) for k, v in f2["changes"].items() if v is not None},
                         {k: v for k, v in f2["changes"].items() if v is not None})
        self.assertEqual(tagio.read(f1["dst"])["title"], ["Opening"])
        out = executor.undo(res["job"])                                    # (Undo: both back, with their own tags)
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertFalse(Path(f1["dst"]).exists())
        self.assertEqual({f: tagio.read(f) for f in (self.f1, self.f2)}, before)

    def test_another_release_needs_every_file_matched_and_undo_keeps_the_release_said(self):
        key = os.path.realpath(str(self.album))
        before = [{"ref": OTHER, "files": [str(self.f1), str(self.f2)]}]
        self.ov.forced[key] = before                           # (a release chosen earlier)
        dlg = self.opened()
        dlg.choose(TWIN)
        self.idle(dlg)
        self.assertEqual(len(dlg.pairs), 2)
        dlg.unmatch_paths([str(self.f2)])
        self.assertFalse(dlg.ok.isEnabled())                  # (f2 would be given track two's tags)
        self.assertIn("Match every file", dlg.status.text())
        dlg.use()
        self.assertEqual(self.previews, [])
        dlg.auto()
        self.assertTrue(dlg.ok.isEnabled())
        dlg.use()
        self.assertEqual(self.ov.forced[key][-1]["ref"], TWIN)
        e = self.win.inspector.edit_of(self.a)
        was = e["match"]["was_forced"]
        self.assertEqual(was, [{"ref": OTHER, "files": [str(self.f1), str(self.f2)]}])   # (for the undo)
        self.assertIsNot(was, before)                          # (a copy, not the page's own list)
        self.assertIsNot(was[0], before[0])
        self.assertEqual(e["match"]["unmatched"], [])
        self.assertIn("release stays chosen", self.win.statusBar().currentMessage())

    def test_a_pasted_link(self):
        dlg = self.opened()
        dlg.link.setText("https://example.com/not/a/release")
        dlg.use_link()
        self.assertEqual(dlg.status.text(), "That is not a MusicBrainz or Discogs release link.")
        self.assertEqual(dlg.track_table.rowCount(), 2)
        dlg.link.setText(f"https://musicbrainz.org/release/{OTHER}")
        QTest.keyClick(dlg.link, Qt.Key_Return)
        self.idle(dlg)
        self.assertEqual(dlg.track_table.rowCount(), 3)
        self.assertEqual(dlg.link.text(), "")
        self.assertTrue(mm.same_ref(dlg.ref, OTHER))

    def test_return_in_an_empty_or_bad_link_never_uses_the_matches(self):
        dlg = self.opened()
        dlg.show()
        dlg.activateWindow()
        dlg.link.setFocus()
        settle(self.app)
        self.assertTrue(dlg.ok.isEnabled() and dlg.ok.isDefault())
        for text in ("", "not a link"):
            dlg.link.setText(text)
            QTest.keyClick(dlg.link, Qt.Key_Return)
            settle(self.app)
            self.assertTrue(dlg.isVisible())                              # (the sheet stays open)
            self.assertEqual(dlg.result(), 0)
        self.assertEqual(dlg.status.text(), "That is not a MusicBrainz or Discogs release link.")
        self.assertEqual(self.previews, [])                               # (no edit, no new preview)
        self.assertIsNone(self.win.inspector.edit_of(self.a))
        dlg.reject()

    def test_choose_release_hands_the_picked_release_to_the_sheet(self):
        dlg = self.opened()
        with mock.patch("gui.versions.search", return_value={"rows": [
                {"ref": OTHER, "title": "Alpha", "artist": "Band", "url": ""}]}):
            pick = dlg.choose_release()
            self.idle(pick)
            pick.table.selectRow(0)
            pick.use()
        self.idle(dlg)
        self.assertEqual(dlg.track_table.rowCount(), 3)
        self.assertEqual(dlg.ref, OTHER)
        self.assertEqual(self.previews, [])

    def test_keyboard_backspace_in_the_files_and_alt_m(self):
        dlg = self.opened()
        ft, tt = dlg.file_table, dlg.track_table
        dlg.activateWindow()
        ft.setFocus()
        ft.selectRow(self.row_of(ft, str(self.f1)))
        settle(self.app)
        QTest.keyClick(ft, Qt.Key_Backspace)
        settle(self.app)
        self.assertNotIn(str(self.f1), dlg.pairs)
        self.assertIn(str(self.f2), dlg.pairs)
        tt.selectRow(0)                                         # (track one: f2's; f1 takes it)
        ft.selectRow(self.row_of(ft, str(self.f1)))
        self.assertTrue(dlg.match_btn.isEnabled())
        QTest.keyClick(ft, Qt.Key_M, Qt.AltModifier)    # (the button's mnemonic: an animated click)
        settle(self.app, 30)
        self.assertEqual(dlg.pairs, {str(self.f1): 0})
        QTest.keyClick(ft, Qt.Key_A, Qt.AltModifier)
        settle(self.app, 30)
        self.assertEqual(dlg.pairs, {str(self.f1): 1, str(self.f2): 0})

    def test_drags_carry_the_files_and_a_tracks_file_dragged_back_is_unmatched(self):
        dlg = self.opened()
        ft, tt = dlg.file_table, dlg.track_table
        sent = []

        class Drag:
            def __init__(self, _w):
                pass

            def setMimeData(self, m):
                self.m = m

            def setPixmap(self, _p):
                pass

            def exec(self, _a):
                sent.append({k: bytes(self.m.data(k)).decode() for k in self.m.formats()})
        with mock.patch.object(mm, "QDrag", Drag):
            ft.selectRow(self.row_of(ft, str(self.f1)))
            ft.startDrag(Qt.LinkAction)
            tt.selectRow(0)
            tt.startDrag(Qt.LinkAction)
        self.assertEqual(sent, [{mm.MIME: str(self.f1)}, {mm.MOVE_MIME: str(self.f2)}])
        mime = QMimeData()
        mime.setData(mm.MOVE_MIME, str(self.f2).encode())
        ev = QDropEvent(QPointF(ft.viewport().rect().center()), Qt.LinkAction, mime, Qt.LeftButton, Qt.NoModifier)
        ft.dropEvent(ev)
        self.assertEqual(dlg.pairs, {str(self.f1): 1})         # (f2 back among the files)
        self.assertEqual(tt.item(0, 3).text(), "No file")
        mime = QMimeData()
        mime.setData(mm.MOVE_MIME, str(self.f1).encode())       # (a track's file onto another track: moved)
        pos = tt.visualItemRect(tt.item(0, 1)).center()
        tt.dropEvent(QDropEvent(QPointF(pos), Qt.LinkAction, mime, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(dlg.pairs, {str(self.f1): 0})
        own = QMimeData()
        own.setData(mm.MIME, str(self.f1).encode())             # (the pane's own files: nothing changes)
        ft.dropEvent(QDropEvent(QPointF(ft.viewport().rect().center()), Qt.LinkAction, own, Qt.LeftButton,
                                Qt.NoModifier))
        self.assertEqual(dlg.pairs, {str(self.f1): 0})

    def test_picker_hands_the_release_back(self):
        dlg = self.opened()
        got = []
        with mock.patch("gui.versions.search", return_value={"rows": [
                {"ref": OTHER, "title": "Alpha", "artist": "Band", "url": ""}]}):
            pick = mm.PickReleaseSheet(dlg, self.a, got.append)
            pick.open()
            self.idle(pick)
            self.assertEqual(pick.ok.text(), "Match to This Release")
            pick.table.selectRow(0)
            pick.use()
        self.assertEqual(got, [OTHER])
        self.assertEqual(self.previews, [])                    # (nothing planned by the picker)
        self.idle(dlg)

    def test_files_from_elsewhere_are_not_taken_and_busy_says_so(self):
        dlg = self.opened()
        dlg.add_files([str(self.tmp / "elsewhere.flac")])
        self.assertIn("not part of this album's preview", dlg.status.text())
        self.ov.running = True
        dlg.use()
        self.assertEqual(dlg.status.text(), mm.BUSY)
        self.assertEqual(self.previews, [])
        self.ov.running = False

    def test_command_enabled_for_an_editable_selected_album(self):
        from PySide6.QtGui import QAction
        cmds = {"match_files": QAction()}

        class P:
            def __init__(self, a):
                self.a = a

            def selected(self):
                return self.a
        mm.update_commands(cmds, P(self.a), True)
        self.assertTrue(cmds["match_files"].isEnabled())
        mm.update_commands(cmds, P(None), True)
        self.assertFalse(cmds["match_files"].isEnabled())
        mm.update_commands(cmds, P(self.a), False)
        self.assertFalse(cmds["match_files"].isEnabled())
        self.assertEqual(mm.COMMANDS["match_files"][1], "Ctrl+Shift+M")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
