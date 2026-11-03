# The inspector's editing (Picard's metadata box and cover art box), offscreen: an edit of a tag
# is kept as one of your edits of the album's plan (plan.apply_edits), a new preview keeps it
# (plan.build(edits=...), engine.Engine.edits), Apply writes it through the executor and Undo puts
# the file back; Revert to Original, Add Tag, Remove Tag; a cover from a dropped file or an image
# link (the download stubbed: no network) replaces the new one, and is checked for size and type.
# Real (generated) audio in temporary folders; nothing under /media, no real config.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import artwork  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

try:
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
except ImportError:                                           # GUI is optional
    QApplication = None

FFMPEG = shutil.which("ffmpeg")


def audio(p, freq=440, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    "-c:a", "flac", *meta, "-y", str(p)], check=True)
    return p


def image(size=(300, 300), kind="PNG") -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, kind)
    return buf.getvalue()


def target(n, title):
    return {"album": ["Real Album"], "albumartist": ["Real Artist"], "artist": ["Real Artist"], "title": [title],
            "tracknumber": [str(n)], "totaltracks": ["2"], "discnumber": ["1"], "totaldiscs": ["1"],
            "date": ["2001-05-01"], "discogs_release_id": ["777"], "label": ["Label"]}


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-insp-"))
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        self.lib = self.tmp / "lib"
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1 = audio(self.album / "01 a.flac", 440, album="Some Album", title="a", artist="Some Artist",
                        genre="Rock")
        self.f2 = audio(self.album / "02 b.flac", 550, album="Some Album", title="b", artist="Some Artist",
                        genre="Rock")
        for f in (self.f1, self.f2):                       # (ffmpeg writes a FLAC comment as DESCRIPTION)
            tagio.write(f, {"comment": ["ripped 2004"]})

    def tearDown(self):
        logsetup.reset()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def profile(self):
        return pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}],
                             "thresholds": {"organize_min_confidence": 95}})

    def proposal(self):
        return pl.Proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)],
                           targets=[target(1, "First"), target(2, "Second")], provider="discogs",
                           release_id="777", confidence=97.0, margin=20.0, evidence=["artist"],
                           provenance={"album": "discogs", "label": "discogs", "title": "discogs"},
                           release={"album": ["Real Album"], "albumartist": ["Real Artist"]})

    def edit(self, **kw):
        e = {"files": [str(self.f1), str(self.f2)], "tags": {}, "file_tags": {}, "cover": None}
        e.update(kw)
        return e

    @staticmethod
    def rows(fe):
        return {r["field"]: r for r in fe["diff"]}


class Overrides(Base):
    """plan.apply_edits and the planner's edits= hook."""

    def test_edit_is_kept_by_a_new_plan_written_by_apply_and_undone(self):
        prof = self.profile()
        edit = self.edit(tags={"label": ["My Label"], "mood": ["Calm", "Warm"], "comment": None},
                         file_tags={str(self.f2): {"title": ["Second (live)"]}})
        p = pl.build([self.proposal()], prof, edits={os.path.realpath(self.album): [edit]})
        a = p["albums"][0]
        f1, f2 = a["files"]
        self.assertEqual(f1["changes"]["label"], ["My Label"])
        self.assertEqual(f1["changes"]["mood"], ["Calm", "Warm"])            # (several values, in order)
        self.assertIsNone(f1["changes"]["comment"])                          # (removed)
        self.assertEqual(f1["changes"]["title"], ["First"])                  # (the release's, not edited)
        self.assertEqual(f2["changes"]["title"], ["Second (live)"])          # (this file only)
        self.assertEqual(self.rows(f1)["label"]["by"], pl.BY_YOU)
        self.assertEqual(self.rows(f1)["comment"]["action"], "removed")
        self.assertEqual(a["edited"]["tags"]["label"], ["My Label"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])       # (a plan writes nothing)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        t1, t2 = tagio.read(self.f1), tagio.read(self.f2)
        self.assertEqual((t1["label"], t1["mood"], t1["album"]), (["My Label"], ["Calm", "Warm"], ["Real Album"]))
        self.assertNotIn("comment", t1)
        self.assertEqual(t2["title"], ["Second (live)"])
        again = pl.build([self.proposal()], prof, edits={os.path.realpath(self.album): [edit]})
        self.assertEqual(again["albums"][0]["status"], "unchanged")          # (settled: nothing to write again)
        executor.undo(res["job"])
        t1 = tagio.read(self.f1)
        self.assertEqual((t1["label"] if "label" in t1 else None, t1["album"], t1["comment"]),
                         (None, ["Some Album"], ["ripped 2004"]))

    def test_revert_keeps_the_files_own_value_and_no_edit_is_the_plan_as_planned(self):
        prof = self.profile()
        p = pl.build([self.proposal()], prof)
        a = p["albums"][0]
        planned = {k: a["files"][0][k] for k in ("changes", "diff", "op")}
        pl.apply_edits(a, self.edit(tags={"album": dict(pl.KEEP)}), prof)
        fe = a["files"][0]
        self.assertNotIn("album", fe["changes"])                             # (Revert to Original)
        self.assertEqual(self.rows(fe)["album"]["action"], "kept (your edit)")
        self.assertEqual(fe["changes"]["title"], ["First"])
        pl.apply_edits(a, self.edit(tags={"label": ["X"]}), prof)            # (a new edit replaces the last)
        self.assertEqual(a["files"][0]["changes"]["album"], ["Real Album"])
        pl.apply_edits(a, None, prof)
        self.assertEqual({k: a["files"][0][k] for k in ("changes", "diff", "op")}, planned)
        self.assertNotIn("auto", a["files"][0])
        self.assertNotIn("edited", a)

    def test_an_edit_makes_an_unchanged_album_ready_and_a_blocked_one_is_left_alone(self):
        prof = self.profile()
        executor.Executor(pl.build([self.proposal()], prof)).run()
        p = pl.build([self.proposal()], prof)
        a = p["albums"][0]
        self.assertEqual(a["status"], "unchanged")
        pl.apply_edits(a, self.edit(tags={"genre": ["Jazz"]}), prof)
        self.assertEqual((a["status"], a["files"][0]["op"]), ("ready", "tag"))
        self.assertEqual(pl.recount(p)["summary"], {"ready": 1})
        pl.apply_edits(a, None, prof)
        self.assertEqual(a["status"], "unchanged")
        a["status"] = "blocked"
        pl.apply_edits(a, self.edit(tags={"genre": ["Jazz"]}), prof)
        self.assertNotIn("genre", a["files"][0]["changes"])

    def test_engine_passes_its_edits_to_the_plan(self):
        import engine
        e = engine.Engine(self.profile(), http=mock.Mock())
        e.edits = {os.path.realpath(self.album): [self.edit(tags={"label": ["Via Engine"]})]}
        with mock.patch.object(engine.Engine, "proposals", lambda *_a, **_k: [self.proposal()]), \
                mock.patch.object(engine, "outage_notes", lambda _h: {}):
            p = e.plan(str(self.album))
        self.assertEqual(p["albums"][0]["files"][0]["changes"]["label"], ["Via Engine"])

    def test_a_cover_edit_is_embedded_in_place_of_the_tracks_own(self):
        prof = self.profile()
        staged, w, h = artwork._stage(image((400, 400)), self.tmp / "img")
        p = pl.build([self.proposal()], prof,
                     edits={os.path.realpath(self.album): [self.edit(cover={"image": str(staged), "source": "c.png",
                                                                             "width": w, "height": h})]})
        art = p["albums"][0]["artwork"]
        self.assertEqual((art["by"], art["front"], sorted(art["embed_into"])),
                         (pl.BY_YOU, str(staged), sorted([str(self.f1), str(self.f2)])))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertTrue(artwork.has_front(self.f1))
        executor.undo(res["job"])
        self.assertFalse(artwork.has_front(self.f1))

    def test_a_cover_edit_replaces_a_cover_the_track_already_has(self):
        prof = self.profile()
        red = image((200, 200), "JPEG")
        executor.embed_front(self.f1, red)
        staged, w, h = artwork._stage(image((400, 400)), self.tmp / "img")
        p = pl.build([self.proposal()], prof,
                     edits={os.path.realpath(self.album): [self.edit(cover={"image": str(staged), "width": w,
                                                                             "height": h})]})
        art = p["albums"][0]["artwork"]
        self.assertIn(str(self.f1), art["embed_replace"])
        self.assertNotIn(str(self.f2), art["embed_replace"])            # (no cover of its own)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        from PIL import Image
        with Image.open(io.BytesIO(artwork.embedded_front(self.f1))) as im:
            self.assertEqual(im.size, (400, 400))                          # (yours, the red one gone)
        self.assertEqual([t for t, _d in artwork._pictures(self.f1)].count(3), 1)
        executor.undo(res["job"])
        self.assertEqual(artwork.embedded_front(self.f1), red)

    def provider_art(self, **kw):
        staged, w, h = artwork._stage(image((600, 600), "JPEG"), self.tmp / "provider")
        prop = self.proposal()
        prop.artwork = dict({"front": str(staged), "source": "caa", "width": w, "height": h}, **kw)
        return prop

    def test_a_cover_edit_goes_into_the_cover_file_the_plan_writes(self):
        prof = self.profile()
        p = pl.build([self.provider_art()], prof)
        a = p["albums"][0]
        self.assertTrue(a["artwork"]["file"])                             # (the folder has no cover file)
        staged, w, h = artwork._stage(image((500, 500)), self.tmp / "img")
        pl.apply_edits(a, self.edit(cover={"image": str(staged), "width": w, "height": h}), prof)
        self.assertEqual(a["artwork"]["file"], a["auto_artwork"]["file"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(Path(a["artwork"]["file"]).read_bytes(), staged.read_bytes())

    def test_a_cover_edit_replaces_the_small_cover_file_the_plan_was_to_rewrite(self):
        prof = self.profile()
        small = self.album / "cover.jpg"
        small.write_bytes(image((200, 200), "JPEG"))
        p = pl.build([self.provider_art(replace={"file": str(small)})], prof)
        a = p["albums"][0]
        ce = next(c for c in a["companions"] if c["src"] == str(small))
        planned = dict(ce["rewrite"])
        self.assertTrue(planned.get("cover"))
        staged, w, h = artwork._stage(image((500, 500)), self.tmp / "img")
        pl.apply_edits(a, self.edit(cover={"image": str(staged), "width": w, "height": h}), prof)
        from PIL import Image
        self.assertNotEqual(ce["rewrite"]["from"], planned["from"])
        with Image.open(ce["rewrite"]["from"]) as im:
            self.assertEqual(im.size, (500, 500))                          # (yours, not the provider's 600)
        pl.apply_edits(a, None, prof)
        self.assertEqual(ce["rewrite"], planned)
        self.assertNotIn("auto_rewrite", ce)

    def test_an_edit_that_undoes_the_tag_changes_keeps_the_artist_photo_to_embed(self):
        prof = self.profile()
        p = pl.build([self.proposal()], prof)
        a = p["albums"][0]
        fe = a["files"][0]
        keep = {str(self.f1): {k: dict(pl.KEEP) for k in fe["changes"]}}
        pl.apply_edits(a, self.edit(file_tags=keep), prof)
        self.assertEqual((a["files"][0]["changes"], a["files"][0]["op"]), ({}, "none"))
        a["artist_photo"] = {"image": "x.jpg", "embed": "x.jpg", "embed_into": [fe["dst"]]}
        pl.apply_edits(a, self.edit(file_tags=keep), prof)
        self.assertEqual(a["files"][0]["op"], "tag")                       # (the photo still goes in)

    def test_an_album_edit_and_one_files_edit(self):
        prof = self.profile()
        p = pl.build([self.proposal()], prof,
                     edits={os.path.realpath(self.album): [self.edit(tags={"title": ["All"]},
                                                                     file_tags={str(self.f2): {"title": ["Mine"]}})]})
        f1, f2 = p["albums"][0]["files"]
        self.assertEqual((f1["changes"]["title"], f2["changes"]["title"]), (["All"], ["Mine"]))

    def test_a_custom_tag_keeps_its_spelling_when_written(self):
        p = pl.build([self.proposal()], self.profile(),
                     edits={os.path.realpath(self.album): [self.edit(tags={"MyTag": ["v"]})]})
        fe = p["albums"][0]["files"][0]
        self.assertEqual(fe["names"]["mytag"], "MyTag")
        seen, real = [], tagio.write

        def write(path, changes, *a, **k):
            seen.append(k.get("names"))
            return real(path, changes, *a, **k)
        with mock.patch.object(tagio, "write", write):            # (a FLAC comment is upper case: an ID3 TXXX
            executor.Executor(p).run()                            # or MP4 atom keeps the spelling)
        self.assertIn({"mytag": "MyTag"}, seen)
        self.assertEqual(tagio.read(self.f1)["mytag"], ["v"])

    def test_the_id3v1_first_values_follow_your_edit(self):
        fe = {"src": str(self.f1), "changes": {}, "diff": [], "first": {"title": ["A"], "artist": ["B"]}}
        pl._edit_file(fe, {"title": ["X"]}, None)                        # (one value: no first value)
        self.assertEqual(fe["first"], {"artist": ["B"]})
        pl._edit_file(fe, {"artist": None}, None)                         # (removed)
        self.assertNotIn("first", fe)
        pl._edit_file(fe, {"artist": ["A", "B"]}, None)
        self.assertEqual(fe["first"], {"artist": ["A"]})
        pl._edit_file(fe, {"artist": dict(pl.KEEP)}, None)                 # (reverted)
        self.assertNotIn("first", fe)


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None or FFMPEG is None, "PySide6 and ffmpeg are needed")
class InspectorEditing(Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
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
        self.insp = self.win.inspector
        self.a = self.plan["albums"][0]
        self.insp.show_album(self.a, self.plan)                  # (as Overview's selection does)
        self.idle()
        self.fired = []
        self.insp.edited.connect(self.fired.append)

    def tearDown(self):
        self.idle()
        self.win.close()
        self.win.deleteLater()
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        super().tearDown()

    def idle(self, wait=30):
        end = time.time() + wait
        settle(self.app, 3)
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        settle(self.app, 3)

    def row(self, field_):
        return next((r for r in self.insp.tags.shown if r["field"] == field_), None)

    # ---- Tags ---------------------------------------------------------------
    def test_the_table_lists_every_tag_changes_first_with_where_the_new_value_comes_from(self):
        tags = self.insp.tags
        self.assertIsNotNone(self.row("genre"))                     # (the file's own, staying as it is)
        self.assertEqual(self.row("genre")["action"], "same")
        acts = [r["action"] for r in tags.shown]
        n = sum(1 for x in acts if x in ("added", "changed", "removed"))
        self.assertTrue(n)
        self.assertTrue(all(x in ("added", "changed", "removed") for x in acts[:n]))
        self.assertTrue(tags.select_field("label"))
        self.assertEqual(tags.source.text(), "Added · from Discogs")
        r = tags.table.currentRow()
        self.assertIn("From Discogs", tags.table.item(r, 3).toolTip())

    def test_edit_saves_an_override_on_the_plan_and_revert_and_use_suggested(self):
        self.insp.set_tag("label", ["My Label"])
        self.idle()
        self.assertEqual(self.insp.edits[os.path.realpath(self.album)][0]["tags"]["label"], ["My Label"])
        self.assertIs(self.plan["albums"][0], self.a)                # (the plan's own entry, changed in place)
        for fe in self.a["files"]:
            self.assertEqual(fe["changes"]["label"], ["My Label"])
        self.assertEqual(self.fired, [self.a])
        tags = self.insp.tags
        self.assertEqual(self.row("label")["by"], pl.BY_YOU)
        tags.select_field("label")
        self.assertEqual(tags.source.text(), "Added · your edit")
        self.assertTrue(tags.table.item(tags.table.currentRow(), 3).font().italic())
        tags.revert_selected()                                       # (the file's own value: none)
        self.idle()
        self.assertNotIn("label", self.a["files"][0]["changes"])
        tags.select_field("album")
        tags.revert_selected()
        self.idle()
        self.assertNotIn("album", self.a["files"][0]["changes"])
        self.assertEqual(self.row("album")["after"], ["Some Album"])
        tags.select_field("album")
        tags.use_suggested()                                         # (your edit dropped: the release's again)
        self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["album"], ["Real Album"])
        self.insp.clear_tag("label")
        self.idle()
        self.assertEqual(self.insp.edits, {})
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Label"])
        self.assertNotIn("edited", self.a)

    def test_tags_buttons_one_style_distinct_icons_and_your_edit_in_words(self):
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QToolButton
        insp = self.gui.inspector
        tags = self.insp.tags
        self.assertTrue(all(b.toolButtonStyle() == _Qt.ToolButtonIconOnly for b in tags.buttons.values()))
        icons_of = {k: i for k, _t, i, _f in insp.BUTTONS}
        self.assertNotEqual(icons_of["revert"], icons_of["suggested"])
        self.assertNotIn(icons_of["suggested"], ("undo-2", "rotate-ccw"))      # (Undo Edit's icon is its own)
        self.assertNotEqual(self.win.act("suggested_cover").property("icon_name"),
                            self.win.act("revert_tag").property("icon_name"))
        self.assertIn("Ctrl+Alt+U", tags.buttons["suggested"].toolTip())
        self.assertNotIn("Drop", self.win.act("use_suggested").statusTip())
        hdr = tags.table.horizontalHeaderItem(0)
        self.assertEqual(hdr.data(_Qt.AccessibleTextRole), "Change")
        self.insp.set_tag("label", ["My Label"])
        self.idle()
        tags.select_field("label")
        r = tags.table.currentRow()
        col = 2 if tags.stacked else 3
        self.assertIn(insp.YOURS_WORDS, tags.table.item(r, col).text())      # (said on the row, not only a pencil)
        self.assertIn("Undo Edit (Ctrl+Z)", tags.note.text())
        tags.remove_selected()
        self.assertIn("Ctrl+Z", self.win.statusBar().currentMessage())
        self.assertIn("nothing is written", self.win.statusBar().currentMessage())
        self.idle()
        # the inspector's Change Release: one control, the Album menu's four ways to choose the release
        rel = self.insp.buttons["release"]
        self.assertIsInstance(rel, QToolButton)
        self.assertEqual([a.objectName() for a in rel.menu().actions()],
                         list(self.gui.actions.RELEASE_MENU))

    def test_tag_keys_work_with_focus_in_the_album_table(self):
        from PySide6.QtCore import Qt as _Qt
        for key in ("revert_tag", "add_tag", "use_suggested"):
            self.assertEqual(self.win.act(key).shortcutContext(), _Qt.WindowShortcut, key)
            self.assertIn(self.win.act(key), self.win.actions(), key)
        for key in ("edit_tag", "remove_tag"):                       # (F2 and Delete: theirs elsewhere)
            self.assertEqual(self.win.act(key).shortcutContext(), _Qt.WidgetWithChildrenShortcut, key)
        self.insp.set_tag("album", ["Mine"])
        self.idle()
        self.insp.tags.select_field("album")
        self.ov.table.setFocus()
        settle(self.app)
        self.win.update_tag_actions()
        self.assertTrue(self.win.act("revert_tag").isEnabled())
        QTest.keyClick(self.ov.table, _Qt.Key_R, _Qt.ControlModifier | _Qt.AltModifier)
        self.idle()
        self.assertNotIn("album", self.a["files"][0]["changes"])         # (back to the file's own value)

    def test_an_album_wide_edit_replaces_each_files_own_edit_of_that_tag(self):
        self.insp.set_tag("title", ["Mine"], file=str(self.f2))
        self.idle()
        self.assertEqual(self.a["files"][1]["changes"]["title"], ["Mine"])
        self.insp.set_tag("title", ["X"])
        self.idle()
        e = self.insp.edits[os.path.realpath(self.album)][0]
        self.assertNotIn("title", e["file_tags"].get(str(self.f2)) or {})
        self.assertEqual([fe["changes"]["title"] for fe in self.a["files"]], [["X"], ["X"]])

    def test_the_source_of_a_value_the_file_keeps(self):
        tags = self.insp.tags
        self.assertTrue(tags.select_field("genre"))
        self.assertEqual(tags.source.text(), "Unchanged · the file's own value")

    def test_a_new_preview_keeps_a_copy_of_your_edits_and_forget_drops_them(self):
        self.insp.set_tag("label", ["Kept Label"])
        self.idle()
        d = self.insp.edits_for_preview()
        key = os.path.realpath(self.album)
        d[key][0]["tags"]["label"] = ["Changed"]
        self.assertEqual(self.insp.edits[key][0]["tags"]["label"], ["Kept Label"])
        d = self.insp.edits_for_preview()
        p = pl.build([self.proposal()], self.profile(), edits=d)
        self.assertEqual(p["albums"][0]["files"][0]["changes"]["label"], ["Kept Label"])
        self.insp.forget_edits()
        self.assertEqual(self.insp.edits, {})

    def test_only_the_last_of_two_quick_edits_is_in_the_plan(self):
        real = pl.apply_edits

        def slow(entry, edit, *a, **k):
            if (edit or {}).get("tags", {}).get("label") == ["First"]:
                time.sleep(0.3)                                      # (the older edit lands last)
            return real(entry, edit, *a, **k)
        with mock.patch.object(pl, "apply_edits", slow):
            self.insp.set_tag("label", ["First"])
            self.insp.set_tag("label", ["Second"])
            self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Second"])
        self.assertEqual(len(self.fired), 1)

    def test_an_older_edit_that_fails_does_not_drop_the_newer_one(self):
        real = pl.apply_edits

        def flaky(entry, edit, *a, **k):
            if (edit or {}).get("tags", {}).get("title") == ["Bad"]:
                raise RuntimeError("could not read the tags")
            time.sleep(0.3)                                          # (the failure lands first)
            return real(entry, edit, *a, **k)
        with mock.patch.object(pl, "apply_edits", flaky):
            self.insp.set_tag("title", ["Bad"])
            self.insp.set_tag("title", ["Good"])
            self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["title"], ["Good"])
        self.assertFalse(any(x[1] == "Your edit could not be applied to the preview." for x in self.gui.sheets.shown))

    def test_no_edit_while_apply_runs(self):
        self.ov.running = True
        try:
            self.assertFalse(self.insp.tags.editable())
            self.assertFalse(self.insp.art.editable())
            self.insp.set_tag("label", ["X"])
            self.insp.set_cover({"image": "x.jpg"})
            self.idle()
            self.assertEqual(self.insp.edits, {})
            self.assertEqual(self.a["files"][0]["changes"]["label"], ["Label"])
        finally:
            self.ov.running = False
        real = pl.apply_edits

        def slow(*a, **k):
            time.sleep(0.2)
            return real(*a, **k)
        with mock.patch.object(pl, "apply_edits", slow):              # (Apply starts while the edit is on its way)
            self.insp.set_tag("label", ["Late"])
            self.ov.running = True
            try:
                self.idle()
            finally:
                self.ov.running = False
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Label"])   # (the plan Apply reads: untouched)
        self.assertEqual(self.a["status"], "ready")
        with mock.patch.object(pl, "apply_edits", slow):              # (applied: the album is done)
            self.insp.set_tag("label", ["Late"])
            self.a["status"] = "done"
            self.idle()
        self.assertEqual((self.a["status"], self.a["files"][0]["changes"]["label"]), ("done", ["Label"]))

    def test_an_edit_of_a_plan_no_longer_shown_is_dropped(self):
        real = pl.apply_edits

        def slow(*a, **k):
            time.sleep(0.2)
            return real(*a, **k)
        with mock.patch.object(pl, "apply_edits", slow):
            self.insp.set_tag("label", ["Late"])
            self.ov.plan = None                                       # (a new preview replaces it)
            self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Label"])

    def test_typing_a_value_in_the_new_column_and_one_file_only(self):
        tags = self.insp.tags
        tags.resize(420, 400)                                        # (wide: Tag | Original | New)
        tags.files.setCurrentIndex(2)                                # (02 b.flac)
        self.assertTrue(tags.select_field("title"))
        it = tags.table.item(tags.table.currentRow(), 3)
        self.assertTrue(it.flags() & Qt.ItemIsEditable)
        it.setText("Second (live)")
        self.idle()
        self.assertEqual(self.a["files"][1]["changes"]["title"], ["Second (live)"])
        self.assertEqual(self.a["files"][0]["changes"]["title"], ["First"])
        self.assertEqual(tags.files.currentIndex(), 2)               # (still the file you edited)

    def test_add_and_remove_a_tag_through_the_sheet(self):
        tags = self.insp.tags
        with mock.patch.object(self.gui.inspector, "tag_sheet", return_value=("Mood", ["Calm", "Warm"])):
            tags.buttons["add"].click()
        self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["mood"], ["Calm", "Warm"])
        self.assertEqual(self.row("mood")["action"], "added")
        tags.select_field("comment")
        tags.buttons["remove"].click()
        self.idle()
        self.assertIsNone(self.a["files"][0]["changes"]["comment"])
        with mock.patch.object(self.gui.inspector, "tag_sheet", return_value=("~hidden", ["x"])):
            tags.add_tag()
        self.assertEqual(self.gui.sheets.shown[-1][1], "This tag cannot be added.")
        self.assertEqual(self.gui.inspector.internal_name("Catalog Number"), "catalognumber")
        tags.select_field("genre")
        tags.edit_selected()                                         # (offscreen: the sheet is recorded)
        self.assertEqual(self.gui.sheets.shown[-1][:2], ("tag", "Edit Genre"))

    def test_apply_writes_the_edit_and_undo_puts_it_back(self):
        self.insp.set_tag("genre", ["Jazz", "Blues"])
        self.idle()
        res = executor.Executor(self.plan).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(tagio.read(self.f1)["genre"], ["Jazz", "Blues"])
        executor.undo(res["job"])
        self.assertEqual(tagio.read(self.f1)["genre"], ["Rock"])

    def test_your_stars_are_written_into_the_file_by_apply_and_undone(self):
        import mb_account as mba
        self.assertIn("written into the files", self.gui.inspector.RATING_TIP)
        self.insp.set_tag("~rating", ["4"], file=str(self.f1))       # (as clicking the fourth star does)
        self.idle()
        fe = next(fe for fe in self.a["files"] if fe["src"] == str(self.f1))
        self.assertEqual(fe["changes"]["~rating"], ["4"])
        res = executor.Executor(self.plan).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(mba.read_rating(self.f1), 4)
        self.assertEqual(tagio.read(self.f1)["genre"], ["Rock"])     # (the file's other tags as planned)
        executor.undo(res["job"])
        self.assertIsNone(mba.read_rating(self.f1))

    def test_a_blocked_album_cannot_be_edited(self):
        self.a["status"] = "blocked"
        self.insp.show_album(self.a, self.plan)
        self.assertFalse(self.insp.tags.buttons["add"].isEnabled())
        self.insp.set_tag("label", ["X"])
        self.idle()
        self.assertEqual(self.insp.edits, {})

    # ---- Artwork -------------------------------------------------------------
    def drop(self, mime):
        ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.insp.art.dropEvent(ev)

    def test_a_dropped_image_file_becomes_the_new_cover_and_can_be_dropped_again(self):
        self.insp.show_tab("Artwork")
        pic = self.tmp / "mine.png"
        pic.write_bytes(image((500, 400)))
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(str(pic))])
        self.drop(m)
        self.idle()
        art = self.a["artwork"]
        self.assertEqual((art["by"], art["width"], art["height"], art["source"]), (pl.BY_YOU, 500, 400, "mine.png"))
        self.assertTrue(Path(art["front"]).is_file())
        self.assertNotEqual(Path(art["front"]), pic)                 # (a staged copy, never the file itself)
        from PIL import Image
        self.assertEqual(Path(art["front"]).suffix, ".jpg")
        with Image.open(art["front"]) as im:
            self.assertEqual(im.format, "JPEG")
        self.assertTrue(Path(art["front"]).is_relative_to(ex.STATE_DIR))
        self.assertEqual(sorted(art["embed_into"]), sorted([str(self.f1), str(self.f2)]))
        self.idle()
        self.assertIn("500 × 400 px", self.insp.art.sides["new"][1].text())
        self.assertIn("Your image: mine.png", self.insp.art.sides["new"][1].text())
        self.assertFalse(self.insp.art.buttons["clear"].isHidden())
        self.insp.art.buttons["clear"].click()
        self.idle()
        self.assertIsNone(self.a["artwork"])
        self.assertEqual(self.insp.edits, {})

    def test_an_image_link_is_downloaded_with_the_http_helper_and_checked(self):
        self.insp.show_tab("Artwork")
        got = []
        with mock.patch.object(artwork, "_get", lambda url, *a, **k: got.append(url) or image((320, 320), "JPEG")):
            m = QMimeData()
            m.setText("https://img.example.org/front.jpg")
            self.drop(m)
            self.idle()
        self.assertEqual(got, ["https://img.example.org/front.jpg"])
        self.assertEqual((self.a["artwork"]["source"], self.a["artwork"]["width"]), ("img.example.org", 320))
        with mock.patch.object(artwork, "_get", lambda *a, **k: b"<html>not an image</html>"):
            self.insp.cover_from_url("https://img.example.org/page")
            self.idle()
        self.assertEqual(self.gui.sheets.shown[-1][1], "This image cannot be used as the cover.")
        self.assertIn("not a picture", self.gui.sheets.shown[-1][2])
        with mock.patch.object(artwork, "_get", lambda *a, **k: image((40, 40))):
            self.insp.cover_from_url("https://img.example.org/tiny.png")
            self.idle()
        self.assertIn("too small", self.gui.sheets.shown[-1][2])
        with mock.patch.object(artwork, "_get", side_effect=ValueError("image larger than 20000000 bytes")):
            self.insp.cover_from_url("https://img.example.org/huge.png")
            self.idle()
        self.assertIn("larger than", self.gui.sheets.shown[-1][2])
        self.assertEqual(self.a["artwork"]["width"], 320)            # (a refused image changes nothing)
        n = len(self.gui.sheets.shown)
        with mock.patch.object(artwork, "_get") as fetch:
            self.insp.cover_from_url("file:///etc/passwd")
        fetch.assert_not_called()
        self.assertEqual(self.gui.sheets.shown[n][1], "That is not an image link.")

    def test_an_oversized_download_is_refused(self):
        big = image((300, 300)) + b"\0" * 2000
        with mock.patch.object(artwork, "MAX_BYTES", 1000):
            with mock.patch.object(artwork, "_get", lambda *a, **k: big):     # (the helper let it through)
                self.insp.cover_from_url("https://img.example.org/big.png")
                self.idle()
            self.assertEqual(self.gui.sheets.shown[-1][1], "This image cannot be used as the cover.")
            self.assertIn("larger than", self.gui.sheets.shown[-1][2])
            n = len(self.gui.sheets.shown)
            read = []

            class Response(io.BytesIO):
                def read(self, n=-1):
                    read.append(n)
                    return super().read(n)
            with mock.patch("urllib.request.urlopen", lambda *a, **k: Response(big)):   # (no network)
                self.insp.cover_from_url("https://img.example.org/big.png")
                self.idle()
            self.assertEqual(read, [1001])                           # (never more than the limit is read)
            self.assertIn("larger than", self.gui.sheets.shown[n][2])
        self.assertIsNone(self.a["artwork"])

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        pic = self.tmp / "big.png"
        pic.write_bytes(image((200, 200)))
        with mock.patch.object(artwork, "MAX_BYTES", 10):
            self.insp.cover_from_file(str(pic))
            self.idle()
        self.assertIn("larger than", self.gui.sheets.shown[-1][2])
        self.assertIsNone(self.a["artwork"])


    # ---- the app's own preview and Apply ----------------------------------------
    def test_the_apps_preview_keeps_your_edits_and_a_new_folder_forgets_them(self):
        import engine
        self.insp.set_tag("label", ["My Label"])
        self.idle()
        seen = []
        real = engine.Engine.plan

        def plan(e, *a, **k):
            seen.append(getattr(e, "edits", None))
            return real(e, *a, **k)
        with mock.patch.object(self.gui.overview, "load_profile", lambda _n: self.profile()), \
                mock.patch.object(engine.Engine, "proposals", lambda *_a, **_k: [self.proposal()]), \
                mock.patch.object(engine.Engine, "plan", plan), \
                mock.patch.object(engine, "outage_notes", lambda _h: {}):
            self.ov.preview()
            self.idle()
            key = os.path.realpath(self.album)
            self.assertEqual(seen[0][key][0]["tags"]["label"], ["My Label"])
            self.assertIsNot(self.ov.plan, self.plan)                    # (a new plan, with your edit)
            a = self.ov.plan["albums"][0]
            self.assertEqual(a["files"][0]["changes"]["label"], ["My Label"])
            self.assertEqual(a["edited"]["tags"]["label"], ["My Label"])
            other = self.tmp / "other"
            other.mkdir()
            self.gui.sheets.shown.clear()
            self.ov.set_path(str(other))                                 # (another folder: asked first; no)
            self.assertEqual(self.gui.sheets.shown[-1][1], "Discard your edits of 1 album?")
            self.assertEqual(self.ov.path, str(self.lib))
            self.assertTrue(self.insp.edits)
            self.ov.set_path(str(self.lib))                              # (the same folder again: kept)
            self.idle()
            self.assertTrue(self.insp.edits)
            with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
                self.ov.set_path(str(other))                             # (Discard Edits: forgotten)
            self.idle()
        self.assertEqual(self.insp.edits, {})
        self.assertEqual(seen[-1], {})

    def test_undo_edit_takes_back_the_last_edit(self):
        a = self.win.act("undo_edit")
        self.assertFalse(a.isEnabled())
        self.assertEqual(a.shortcut().toString(), "Ctrl+Z")
        self.assertEqual(self.win.act("undo_job").shortcut().toString(), "Ctrl+Alt+Z")
        self.insp.set_tag("label", ["One"])
        self.idle()
        self.insp.set_tag("label", ["Two"])
        self.idle()
        self.assertTrue(a.isEnabled())
        a.trigger()
        self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["One"])
        a.trigger()
        self.idle()
        self.assertEqual(self.insp.edits, {})
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Label"])     # (as the preview planned it)
        self.assertFalse(a.isEnabled())
        self.assertIn("taken back", self.win.statusBar().currentMessage())

    def test_the_keyboard_edits_removes_and_your_edit_is_marked(self):
        tags = self.insp.tags
        tags.resize(300, 400)                                        # (narrow: stacked, still editable)
        tags.fill()
        self.assertTrue(tags.stacked)
        self.assertTrue(tags.select_field("title"))
        r = tags.table.currentRow()
        it = tags.table.item(r, 2)
        self.assertTrue(it.flags() & Qt.ItemIsEditable)
        tags.table.setFocus()
        QTest.keyClick(tags.table, Qt.Key_Return)                    # (Return: the New value typed in place)
        ed = tags.table.findChild(QLineEdit)
        self.assertIsNotNone(ed)
        self.assertEqual(ed.text(), self.row("title")["after"][0])
        ed.setText("Typed")
        QTest.keyClick(ed, Qt.Key_Return)
        self.idle()
        self.assertEqual(self.a["files"][0]["changes"]["title"], ["Typed"])
        self.assertTrue(tags.select_field("title"))
        r = tags.table.currentRow()
        self.assertEqual(tags.table.item(r, 0).data(Qt.AccessibleTextRole).split(", ")[-1], "your edit")
        self.assertFalse(tags.table.item(r, 0).icon().isNull())
        self.assertIn("your edit", tags.source.text())
        tags.table.setFocus()
        QTest.keyClick(tags.table, Qt.Key_Delete)                    # (Delete: the tag removed)
        self.idle()
        self.assertIsNone(self.row("title")["after"] or None)
        # the Album > Tag commands, with their keys, and the table's menu built from them
        self.assertEqual(self.win.act("edit_tag").shortcut().toString(), "F2")
        self.assertIn(self.win.act("edit_tag"), self.win.menus["Tag"].actions())
        self.assertIn(self.win.act("choose_cover"), self.win.menus["Artwork"].actions())
        self.assertTrue(tags.select_field("album"))
        with mock.patch.object(self.gui.inspector, "popup_menu") as pm:
            tags.context_menu(tags.table.visualRect(tags.table.currentIndex()).center())
        keys = [x.objectName() if x is not None else None for x in pm.call_args[0][3]]
        self.assertEqual(keys, ["edit_tag", "revert_tag", "use_suggested", "remove_tag", None, "add_tag"])
        self.assertTrue(self.win.act("edit_tag").isEnabled())

    def test_the_apply_sheet_names_your_edits(self):
        self.insp.set_tag("label", ["Mine"])
        self.idle()
        _head, body, _undo, _act = self.ov.apply_words()
        self.assertIn("Includes your own edits of 1 album", body)

    def test_an_edit_of_an_unchanged_album_enables_apply_and_its_row(self):
        prof = self.profile()
        executor.Executor(pl.build([self.proposal()], prof)).run()
        p = pl.build([self.proposal()], prof)
        self.ov.planned(p)
        self.idle()
        a = p["albums"][0]
        self.insp.show_album(a, p)
        self.assertEqual(a["status"], "unchanged")
        self.assertFalse(self.win.act("apply").isEnabled())
        self.insp.set_tag("genre", ["Jazz"])
        self.idle()
        self.assertEqual(a["status"], "ready")
        self.assertTrue(self.win.act("apply").isEnabled())
        status = self.ov.model.data(self.ov.model.index(0, 0))
        self.assertNotIn("nchanged", str(status))
        self.assertNotIn("nchanged", self.ov.summary.text())

    def test_apply_waits_for_an_edit_on_its_way(self):
        real = pl.apply_edits
        gate = threading.Event()

        def slow(*a, **k):
            gate.wait(5)
            return real(*a, **k)
        with mock.patch.object(pl, "apply_edits", slow), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True) as confirm:
            self.insp.set_tag("label", ["Typed"])
            settle(self.app)
            self.assertTrue(self.insp.pending())
            self.ov.apply()
            confirm.assert_not_called()
            self.assertFalse(self.ov.running)
            self.assertIn("still being applied", self.win.statusBar().currentMessage())
            gate.set()
            self.idle()
        self.assertFalse(self.insp.pending())
        self.assertEqual(self.a["files"][0]["changes"]["label"], ["Typed"])

    # ---- background work -----------------------------------------------------
    def test_edits_and_covers_are_worked_out_off_the_ui_thread(self):
        on_main = []
        real = pl.apply_edits

        def apply_edits(*a, **k):
            on_main.append(threading.current_thread() is threading.main_thread())
            return real(*a, **k)

        def get(*_a, **_k):
            on_main.append(threading.current_thread() is threading.main_thread())
            return image((320, 320), "JPEG")
        pic = self.tmp / "mine.png"
        pic.write_bytes(image((300, 300)))
        real_read = Path.read_bytes

        def read_bytes(p):
            if p == pic:
                on_main.append(threading.current_thread() is threading.main_thread())
            return real_read(p)
        with mock.patch.object(pl, "apply_edits", apply_edits), mock.patch.object(artwork, "_get", get), \
                mock.patch.object(Path, "read_bytes", read_bytes):
            self.insp.set_tag("label", ["X"])
            self.idle()
            self.insp.cover_from_url("https://img.example.org/front.jpg")
            self.idle()
            self.insp.cover_from_file(str(pic))
            self.idle()
        self.assertGreaterEqual(len(on_main), 5)                     # (3 edits, a download and a read)
        self.assertFalse(any(on_main))

    def test_no_cover_is_fetched_while_work_runs_and_a_late_one_is_dropped(self):
        self.ov.running = True
        try:
            with mock.patch.object(artwork, "_get") as fetch:
                self.insp.cover_from_url("https://img.example.org/front.jpg")
                self.idle()
            fetch.assert_not_called()
        finally:
            self.ov.running = False
        gate = threading.Event()

        def slow(*_a, **_k):
            gate.wait(5)
            return image((320, 320), "JPEG")
        with mock.patch.object(artwork, "_get", slow):               # (Apply starts while it downloads)
            self.insp.cover_from_url("https://img.example.org/front.jpg")
            settle(self.app)
            self.ov.running = True
            try:
                gate.set()
                self.idle()
            finally:
                self.ov.running = False
        self.assertIsNone(self.a["artwork"])
        self.assertEqual(self.insp.edits, {})
        gate = threading.Event()
        with mock.patch.object(artwork, "_get", slow):               # (a new preview while it downloads)
            self.insp.cover_from_url("https://img.example.org/front.jpg")
            settle(self.app)
            new = pl.build([self.proposal()], self.profile())
            self.ov.planned(new)
            self.insp.show_album(new["albums"][0], new)
            gate.set()
            self.idle()
        self.assertIsNone(self.a["artwork"])
        self.assertIsNone(new["albums"][0]["artwork"])
        self.assertEqual(self.insp.edits, {})
        self.assertIn("not used", self.win.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
