# The script editor (gui/script_editor.py), offscreen: the language helpers (function help,
# error positions, bracket matching, the function under the cursor), the preview run on a plan
# entry's planned tags or the sample track, colouring, completion, inline errors, and loading and
# saving a profile's naming and tagger scripts (the profile file only, in a temporary config folder).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ["TAGMENDER_TAGGER_INI"] = _os.path.join(_tempfile.mkdtemp(prefix="tm-se-ini-"), "no-Picard.ini")

import json
import pickle
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def settle(app, n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def picard_ini(folder, scripts) -> Path:
    """A Picard.ini with tagger scripts on: each [position, name, enabled, text] as PyQt keeps it
    (@Variant of a pickled Python value)."""
    def variant(value):
        data = pickle.dumps(tuple(value), protocol=4)
        name = b"PyQt_PyObject\0"
        b = struct.pack(">I", 127) + struct.pack(">I", len(name)) + name + struct.pack(">I", len(data)) + data
        return "@Variant(" + "".join(f"\\x{x:x}" for x in b) + ")"
    path = Path(folder) / "Picard.ini"
    path.write_text("[application]\nversion=3.0.2\n[setting]\nenable_tagger_scripts=true\n"
                    f"list_of_scripts={', '.join(variant(x) for x in scripts)}\n")
    return path


def failing_render(name):
    """naming.render that fails at $name as it runs (as Picard reports it: the column in the
    script without its line breaks and tabs), else renders."""
    import naming
    real = naming.render

    def render(t, *a, **k):
        flat = t.replace("\n", "").replace("\t", "")
        if "$" + name in flat:
            raise naming.TemplateError(f"1:{flat.index('$' + name)}:${name}: it failed")
        return real(t, *a, **k)
    return render


def broken_datetime():
    """A real $datetime that fails as it runs (strftime raising ValueError, as an unsupported
    format code does on some systems): Picard's own ScriptRuntimeError, at its real position."""
    import tagscript.functions as functions

    class Now:
        def astimezone(self):
            return self

        def strftime(self, fmt):
            raise ValueError("bad format")

    class DateTime:
        @staticmethod
        def now():
            return Now()
    return mock.patch.object(functions.datetime, "datetime", DateTime)


ALBUM = {"status": "ready", "source_dir": "/tmp/in/Some Album", "files": [
    {"src": "/tmp/in/Some Album/a.flac", "dst": "/tmp/in/Some Album/a.flac", "op": "tag",
     "changes": {"title": ["First Song"]},
     "diff": [{"field": "title", "before": ["a"], "after": ["First Song"], "action": "changed"},
              {"field": "artist", "before": [], "after": ["The Band"], "action": "added"},
              {"field": "albumartist", "before": [], "after": ["The Band"], "action": "added"},
              {"field": "album", "before": ["Old"], "after": ["Real Album"], "action": "changed"},
              {"field": "tracknumber", "before": ["1"], "after": ["1"], "action": "preserved"},
              {"field": "comment", "before": ["rip"], "after": [], "action": "removed"}]},
    {"src": "/tmp/in/Some Album/b.flac", "dst": "/tmp/in/Some Album/b.flac", "op": "tag", "changes": {},
     "diff": [{"field": "title", "before": ["b"], "after": ["Second Song"], "action": "changed"},
              {"field": "albumartist", "before": [], "after": ["The Band"], "action": "added"},
              {"field": "album", "before": [], "after": ["Real Album"], "action": "added"},
              {"field": "tracknumber", "before": ["2"], "after": ["2"], "action": "preserved"}]}]}


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Language(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from gui import script_editor
        cls.se = script_editor

    def test_functions_have_their_help(self):
        docs = self.se.functions()
        self.assertIn("if", docs)
        self.assertEqual(docs["if"][0], "$if(if,then[,else])")
        self.assertIn("returns `then`", docs["if"][1])
        self.assertIn("words", docs)                           # (TagMender's own)
        self.assertTrue(docs["words"][0].startswith("$words("))
        self.assertIn("noop", docs)

    def test_function_help_names_no_other_program(self):
        docs = self.se.functions()
        for name, (sig, text) in docs.items():
            self.assertNotIn("picard", (sig + text).lower(), name)
        self.assertTrue(docs["find"][1].endswith("was not found."))     # (the help itself kept)
        self.assertIn("$title(%album%)", docs["title"][1])

    def test_variables_cover_tags_and_hidden_ones(self):
        v = self.se.variables(["~mine", "custom"])
        for name in ("album", "albumartist", "_year", "_extension", "_mine", "custom"):
            self.assertIn(name, v)

    def test_error_positions(self):
        self.assertIsNone(self.se.check("%album%/$left(%title%,3)"))
        idx, line, col, msg = self.se.check("ab\n  $left(x)")
        self.assertEqual((idx, line, col), (5, 2, 2))
        self.assertIn("Wrong number of arguments for $left", msg)
        self.assertEqual(self.se.check("%a b%")[:3], (2, 1, 2))            # the space
        self.assertEqual(self.se.check("$if(")[:3], (4, 1, 4))             # the end
        self.assertIn("ends before", self.se.check("$if(")[3])
        self.assertEqual(self.se.check("x $nosuch(1)")[:3], (2, 1, 2))
        self.assertIn("Unknown function", self.se.check("x $nosuch(1)")[3])

    def test_error_in_the_script_as_picard_runs_it(self):
        class ScriptRuntimeError(Exception):
            pass
        # (the naming script runs without its line breaks and tabs: column 4 there is the "$" here)
        p = self.se.problem(ScriptRuntimeError("1:4:$x: it failed"), "ab\n\tcd$x()", prepped=True)
        self.assertEqual(p, (6, 2, 3, "it failed"))
        self.assertEqual(self.se.problem(ValueError("TypeError: no"), "x")[0], None)

    def test_brackets_and_escapes(self):
        t = "$if(a\\),$lower(b))"
        self.assertEqual(self.se.match_bracket(t, 3), len(t) - 1)
        self.assertEqual(self.se.match_bracket(t, len(t) - 1), 3)
        self.assertIsNone(self.se.match_bracket(t, 6))                     # an escaped bracket is text
        self.assertIsNone(self.se.match_bracket("$if(a", 3))
        self.assertTrue(self.se.escaped("a\\(", 2))
        self.assertFalse(self.se.escaped("a\\\\(", 3))

    def test_function_under_the_cursor(self):
        t = "$if($lower(%title%),b,c)"
        self.assertEqual(self.se.function_at(t, 2), "if")                  # on the name
        self.assertEqual(self.se.function_at(t, 12), "lower")              # inside $lower(
        self.assertEqual(self.se.function_at(t, 20), "if")                 # back in $if(
        self.assertEqual(self.se.function_at(t, len(t)), "")
        self.assertEqual(self.se.function_at("$noop( \\) )x", 8), "noop")

    def test_planned_files_and_sample(self):
        files = self.se.planned_files(ALBUM)
        self.assertEqual([f[0] for f in files], ["a.flac", "b.flac"])
        tags = files[0][2]
        self.assertEqual(tags["title"], ["First Song"])
        self.assertEqual(tags["tracknumber"], ["1"])
        self.assertNotIn("comment", tags)                                  # (the plan removes it)
        self.assertEqual(self.se.planned_files(None)[0][0], "Sample track")
        self.assertEqual(self.se.planned_files({"files": []})[0][1], self.se.SAMPLE_PATH)

    def test_a_plain_string_value_is_one_value_never_its_letters(self):
        album = {"files": [{"src": "/m/A/B/01 Track 1.flac", "diff": [
            {"field": "title", "before": "Old", "after": "Track 1", "action": "changed"},
            {"field": "tracknumber", "before": None, "after": "1", "action": "added"}],
            "changes": {"album": "Northern Lights"}}]}
        tags = self.se.planned_files(album)[0][2]
        self.assertEqual(tags, {"title": ["Track 1"], "tracknumber": ["1"], "album": ["Northern Lights"]})
        items = [{"kind": "naming", "enabled": True, "text": "$num(%tracknumber%,2) %title%"}]
        out = self.se.run_preview(items, False, {"title": "Track 1", "tracknumber": "1"}, "/x/1.flac")
        self.assertEqual(out["name"], "01 Track 1.flac")
        self.assertEqual(out["before"]["title"], ["Track 1"])

    def test_preview_runs_tagger_then_naming(self):
        items = [{"kind": "naming", "enabled": True,
                  "text": "%albumartist%/%album%/$num(%tracknumber%,2) %title% %_shelf%"},
                 {"kind": "tagger", "enabled": True, "text": "$set(genre,Jazz)$delete(label)$set(_shelf,S)"},
                 {"kind": "tagger", "enabled": False, "text": "$set(genre,Off)"}]
        out = self.se.run_preview(items, True, dict(self.se.SAMPLE), self.se.SAMPLE_PATH)
        self.assertEqual(out["errors"], {})
        self.assertEqual(out["after"]["genre"], ["Jazz"])
        self.assertEqual(out["removed"], ["label"])
        self.assertEqual(out["name"], "Sample Artist/Sample Album/01 Sample Title S.flac")
        off = self.se.run_preview(items, False, dict(self.se.SAMPLE), self.se.SAMPLE_PATH)
        self.assertEqual(off["after"]["genre"], ["Rock"])                  # (tagger scripts not run)

    def test_preview_names_each_scripts_error(self):
        items = [{"kind": "naming", "enabled": True, "text": "%album%/\n$left(%title%)"},
                 {"kind": "tagger", "enabled": True, "text": "$set(genre,Jazz"}]
        out = self.se.run_preview(items, True, {"title": ["T"], "album": ["A"]}, "/x/1.flac")
        self.assertEqual(out["errors"][0][1:3], (2, 0))
        self.assertIn(1, out["errors"])
        self.assertEqual(out["name"], "")
        self.assertNotIn("genre", out["after"])

    def test_naming_script_failing_as_it_runs(self):
        import naming
        items = [{"kind": "naming", "enabled": True, "text": "%album%/\n\t$upper(%title%)"}]
        with mock.patch.object(naming, "render", failing_render("upper")):
            out = self.se.run_preview(items, True, {"title": ["T"], "album": ["A"]}, "/x/1.flac")
        self.assertEqual(out["errors"][0], (len("%album%/\n\t"), 2, 1, "it failed"))
        self.assertEqual(out["name"], "")

    def test_real_runtime_error_in_the_naming_script(self):
        # (Picard's own error, through the real naming.render: tabs and line breaks dropped as it
        # runs, the position found again in the script as written)
        text = "%album%/\n\t$upper($datetime(x))"
        items = [{"kind": "naming", "enabled": True, "text": text}]
        tags = {"title": ["T"], "album": ["A"]}
        with broken_datetime():
            for editions in ("off", "edition"):
                out = self.se.run_preview(items, True, tags, "/x/1.flac", editions=editions)
                self.assertEqual(out["errors"][0][:3], (text.index("$datetime"), 2, 8), editions)
                self.assertEqual(out["name"], "")
            one = "%album%/$upper($datetime(x))"
            out = self.se.run_preview([{"kind": "naming", "enabled": True, "text": one}], True, tags, "/x/1.flac",
                                      editions="edition")
        self.assertEqual(out["errors"][0][:3], (one.index("$datetime"), 1, one.index("$datetime")))

    def test_real_runtime_error_in_a_tagger_script(self):
        text = "$noop()\n  $set(genre,$datetime(x))"
        items = [{"kind": "naming", "enabled": True, "text": "%title%"},
                 {"kind": "tagger", "enabled": True, "text": text}]
        with broken_datetime():
            out = self.se.run_preview(items, True, {"title": ["T"]}, "/x/1.flac")
        self.assertEqual(out["errors"][1][:3], (text.index("$datetime"), 2, 13))
        self.assertNotIn(0, out["errors"])
        self.assertNotIn("genre", out["after"])

    def test_only_the_edition_piece_failing_is_placed_in_the_written_script(self):
        import naming
        text = "%album%/x"
        ran = naming.with_editions(text, "edition")
        real = naming.render

        def render(t, *a, **k):                       # (the script as written runs; the piece fails)
            flat = t.replace("\n", "").replace("\t", "")
            if "%_edition_kind%" in flat:
                raise naming.TemplateError(f"1:{flat.rindex('[')}:$if: it failed")
            return real(t, *a, **k)
        self.assertGreater(ran.rindex("["), len(text))
        items = [{"kind": "naming", "enabled": True, "text": text}]
        with mock.patch.object(naming, "render", render):
            out = self.se.run_preview(items, True, {"title": ["T"], "album": ["A"]}, "/x/1.flac",
                                      editions="edition")
        self.assertEqual(out["errors"][0], (len(text), 1, len(text), "it failed"))

    def test_a_preset_failing_as_it_runs_is_said(self):
        items = [{"kind": "naming", "enabled": True, "text": ""}]
        with broken_datetime():
            out = self.se.run_preview(items, True, {"title": ["T"]}, "/x/1.flac", fallback="$datetime(x)")
        self.assertIsNone(out["errors"][0][0])
        self.assertIn("preset", out["errors"][0][3])
        self.assertEqual(out["name"], "")

    def test_tagger_scripts_run_once_on_a_planned_album(self):
        # the plan's "after" already holds what the profile's tagger scripts made; the preview runs
        # them on what they were given ("unscripted"), so an appending script appends once
        script = "$set(album,%album% [Deluxe])"
        album = {"files": [{"src": "/x/a.flac", "dst": "/x/a.flac", "op": "tag",
                            "changes": {"album": ["X [Deluxe]"]},
                            "diff": [{"field": "album", "before": ["Old"], "after": ["X [Deluxe]"]},
                                     {"field": "title", "before": ["t"], "after": ["T"]}],
                            "unscripted": {"album": ["X"], "title": ["T"], "comment": ["rip"]}}]}
        (label, path, tags), = self.se.planned_files(album)
        self.assertEqual((label, tags["album"]), ("a.flac", ["X"]))
        items = [{"kind": "naming", "enabled": True, "text": "%album%/%title%"},
                 {"kind": "tagger", "enabled": True, "text": script}]
        out = self.se.run_preview(items, True, tags, path)
        self.assertEqual(out["after"]["album"], ["X [Deluxe]"])
        self.assertEqual(out["name"], "X [Deluxe]/T.flac")

    def test_the_plan_keeps_what_the_tagger_scripts_were_given(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        import plan as pl
        import profiles as pf
        import tagio
        d = Path(tempfile.mkdtemp(prefix="tm-se-plan-"))
        f = d / "01.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1",
                        "-ar", "8000", "-ac", "1", "-metadata", "album=Old", "-metadata", "title=t", str(f), "-y"],
                       check=True)
        prof = pf.from_dict({"name": "default", "picard": {"enable_tagger_scripts": True,
                                                           "list_of_scripts": ["$set(album,%album% [Deluxe])"]}})
        planner = pl.Planner(prof, rules=[], overrides={})
        out = planner._scripted(f, tagio.read(f), {"album": ["X"], "title": ["T"]})
        self.assertEqual(out["album"], ["X [Deluxe]"])
        given = planner._unscripted[str(f)]
        self.assertEqual(given["album"], ["X"])
        album = {"files": [{"src": str(f), "dst": str(f), "diff": [{"field": "album", "after": out["album"]}],
                            "unscripted": given}]}
        tags = self.se.planned_files(album)[0][2]
        items = [{"kind": "naming", "enabled": True, "text": "%album%"},
                 {"kind": "tagger", "enabled": True, "text": "$set(album,%album% [Deluxe])"}]
        self.assertEqual(self.se.run_preview(items, True, tags, str(f))["after"]["album"], ["X [Deluxe]"])
        shutil.rmtree(d, ignore_errors=True)

    def test_the_preset_follows_max_album_words(self):
        import naming
        import profiles as pf
        prof = pf.from_dict({"name": "words", "filenames": {"max_album_words": 3}})
        got = self.se.profile_scripts(prof)
        self.assertIn("$words(%album%,3)", got["naming"])
        self.assertEqual(got["naming"], got["preset"])
        # (the very template the plan runs, without the edition piece the preview adds itself)
        self.assertEqual(naming.effective_template(prof["naming"], prof["filenames"], editions=False), got["naming"])
        self.assertEqual(naming.effective_template(prof["naming"], prof["filenames"]),
                         naming.with_editions(got["naming"], prof["naming"].get("editions", "off")))
        own = pf.from_dict({"name": "own", "naming": {"template": "$words(%album%,5)"},
                            "filenames": {"max_album_words": 3}})
        self.assertEqual(self.se.profile_scripts(own)["naming"], "$words(%album%,5)")   # (its own: as written)
        items = [{"kind": "naming", "enabled": True, "text": got["naming"]}]
        tags = {"album": ["One Two Three Four Five Six"], "albumartist": ["B"], "title": ["t"], "tracknumber": ["1"]}
        out = self.se.run_preview(items, True, tags, "/x/1.flac")
        self.assertIn("/One Two Three/", out["name"])

    def test_runtime_error_found_in_the_script_as_written_with_editions(self):
        import naming
        text = "%album%/$upper(%title%)"
        self.assertNotEqual(naming.with_editions(text, "edition"), text)      # (a piece added after %album%)
        items = [{"kind": "naming", "enabled": True, "text": text}]
        with mock.patch.object(naming, "render", failing_render("upper")):
            out = self.se.run_preview(items, True, {"title": ["T"], "album": ["A"]}, "/x/1.flac",
                                      editions="edition")
        self.assertEqual(out["errors"][0][:3], (text.index("$upper"), 1, text.index("$upper")))

    def test_tagger_script_failing_as_it_runs(self):
        import engine
        from tagscript.parser import ScriptRuntimeError

        class Item:
            def __str__(self):
                return "1:4:$set"

        def run(scripts, tags, hidden, log=None, strip=True):
            log.warning("tagger script failed: %s", ScriptRuntimeError(Item(), "it failed"))
            return dict(tags), []
        items = [{"kind": "naming", "enabled": True, "text": "%title%"},
                 {"kind": "tagger", "enabled": True, "text": "$noop()$set(genre,Jazz)"}]
        with mock.patch.object(engine, "run_tagger_scripts", run):
            out = self.se.run_preview(items, True, {"title": ["T"]}, "/x/1.flac")
        self.assertEqual(out["errors"][1], (4, 1, 4, "it failed"))
        self.assertNotIn(0, out["errors"])
        self.assertNotIn("genre", out["after"])

    def test_empty_naming_script_previews_the_preset(self):
        items = [{"kind": "naming", "enabled": True, "text": "  "}]
        out = self.se.run_preview(items, True, {"title": ["T"], "album": ["A"]}, "/x/1.flac",
                                  fallback="%album%/x %title%")
        self.assertEqual(out["name"], "A/x T.flac")
        self.assertEqual(self.se.run_preview(items, True, {"title": ["T"]}, "/x/1.flac")["name"], "T.flac")


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Editor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import gui
        from gui import script_editor
        cls.gui, cls.se = gui, script_editor
        gui.common._set_gui_pref("reduce_motion", True)

    def setUp(self):
        import profiles as pf
        self.pf = pf
        self.gui.sheets.shown.clear()
        pf.save(pf.from_dict({"name": "scripted", "naming": {"template": "%albumartist%/%album%/%title%"},
                              "picard": {"enable_tagger_scripts": True,
                                         "list_of_scripts": [[0, "Genre", True, "$set(genre,Jazz)"],
                                                             [1, "Unused", False, "$set(mood,Calm)"]]}}))
        pf.save(pf.from_dict({"name": "plain"}))
        self.eds = []

    def tearDown(self):
        from shiboken6 import isValid
        for ed in self.eds:
            if not isValid(ed):                                   # (closed with its window: deleted)
                continue
            end = time.time() + 20
            while time.time() < end and any(t.isRunning() for t, _w in getattr(ed, "_threads", [])
                                            if self.gui.work._alive(t)):
                settle(self.app, 2)
            ed.dirty = False
            ed.close()
            ed.deleteLater()
        settle(self.app, 2)

    def editor(self, profile, album=None):
        ed = self.se.ScriptEditor(None, profile, album)
        self.eds.append(ed)
        ed.show()
        self.wait(ed)
        return ed

    def wait(self, ed, run=True):
        if run:
            ed.timer.stop()
            ed.result = None
            ed.run()
        end = time.time() + 20
        while time.time() < end and (ed.result is None or ed.timer.isActive()):
            settle(self.app, 2)
        self.assertIsNotNone(ed.result)

    def saved(self, name):
        return json.loads(self.pf.path_of(name).read_text(encoding="utf-8"))

    def test_loads_the_profiles_scripts(self):
        ed = self.editor("scripted")
        self.assertEqual([it["name"] for it in ed.items], ["File naming script", "Genre", "Unused"])
        self.assertEqual(ed.edit.toPlainText(), "%albumartist%/%album%/%title%")
        self.assertTrue(ed.run_box.isChecked())
        self.assertFalse(ed.save_btn.isEnabled())                        # nothing changed yet
        self.assertEqual(ed.name_label.text(), "Sample Artist/Sample Album/Sample Title.flac")
        rows = {ed.tags.item(r, 0).toolTip(): ed.tags.item(r, 2).text() for r in range(ed.tags.rowCount())}
        self.assertEqual(rows, {"genre": "Jazz"})                         # (the script that is off: not run)
        self.assertIn("sample track", ed.preview_note.text())

    def test_preset_shown_and_kept_as_preset(self):
        ed = self.editor("plain")
        import naming
        self.assertEqual(ed.items[0]["text"], naming.template_for({"preset": "tagmender"}))
        self.assertEqual(ed.changes(), {})
        ed.run_box.setChecked(True)
        self.assertEqual(ed.changes(), {"enable_tagger_scripts": True})
        self.assertTrue(ed.save())
        data = self.saved("plain")
        self.assertNotIn("template", data.get("naming", {}))             # (still the preset)
        self.assertNotIn("list_of_scripts", data.get("tagger", {}))      # (still Picard's tagger scripts)
        self.assertTrue(data["tagger"]["enable_tagger_scripts"])

    def test_cleared_naming_script_previews_the_preset(self):
        ed = self.editor("plain")
        shown = ed.name_label.text()                                     # (the preset, as loaded)
        ed.edit.setPlainText("")
        self.wait(ed)
        self.assertEqual(ed.name_label.text(), shown)                    # (saved empty: the preset again)
        self.assertNotEqual(shown, "Sample Title.flac")

    def test_album_preview_uses_planned_tags(self):
        ed = self.editor("scripted", ALBUM)
        self.assertEqual(ed.file_box.count(), 2)
        self.assertEqual(ed.name_label.text(), "The Band/Real Album/First Song.flac")
        ed.file_box.setCurrentIndex(1)
        self.wait(ed)
        self.assertEqual(ed.name_label.text(), "The Band/Real Album/Second Song.flac")
        self.assertIn("planned tags", ed.preview_note.text())

    def test_edit_preview_and_save(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("$upper(%album%)/%title%")
        self.assertTrue(ed.dirty and ed.save_btn.isEnabled())
        self.wait(ed)
        self.assertEqual(ed.name_label.text(), "SAMPLE ALBUM/Sample Title.flac")
        ed.list.setCurrentRow(1)
        self.assertEqual(ed.edit.toPlainText(), "$set(genre,Jazz)")
        ed.edit.setPlainText("$set(genre,Blues)")
        ed.add_btn.click()
        self.assertEqual(ed.list.count(), 4)
        ed.edit.setPlainText("$set(mood,Happy)")
        self.wait(ed)
        rows = {ed.tags.item(r, 0).toolTip(): ed.tags.item(r, 2).text() for r in range(ed.tags.rowCount())}
        self.assertEqual(rows, {"genre": "Blues", "mood": "Happy"})
        self.assertTrue(ed.save())
        data = self.saved("scripted")
        self.assertEqual(data["naming"]["template"], "$upper(%album%)/%title%")
        self.assertEqual(data["tagger"]["list_of_scripts"],
                         [[0, "Genre", True, "$set(genre,Blues)"], [1, "Unused", False, "$set(mood,Calm)"],
                          [2, "Script 3", True, "$set(mood,Happy)"]])
        prof = self.pf.load("scripted")
        import tagger_settings
        self.assertEqual(tagger_settings.tagger_scripts(prof.picard_tags()),
                         ["$set(genre,Blues)", "$set(mood,Happy)"])

    def test_remove_a_tagger_script(self):
        ed = self.editor("scripted")
        ed.list.setCurrentRow(0)
        self.assertFalse(ed.remove_btn.isEnabled())                      # (the naming script stays)
        ed.list.setCurrentRow(2)
        self.gui.sheets.shown.clear()
        ed.remove_btn.click()                                            # (it has text: asked first; offscreen: no)
        self.assertEqual(self.gui.sheets.shown[-1][1], "Remove “Unused”?")
        self.assertEqual(len(ed.items), 3)
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
            ed.remove_btn.click()
        self.assertEqual([it["name"] for it in ed.items], ["File naming script", "Genre"])
        self.assertTrue(ed.save())
        self.assertEqual(self.saved("scripted")["tagger"]["list_of_scripts"], [[0, "Genre", True, "$set(genre,Jazz)"]])

    def test_an_empty_script_is_removed_with_delete_and_ctrl_n_adds_one(self):
        ed = self.editor("scripted")
        ed.list.setFocus()
        QTest.keyClick(ed, Qt.Key_N, Qt.ControlModifier)
        self.assertEqual([it["name"] for it in ed.items][-1], "Script 3")
        ed.list.setFocus()
        ed.list.setCurrentRow(3)
        QTest.keyClick(ed.list, Qt.Key_Delete)                          # (no text: removed without asking)
        self.assertEqual(len(ed.items), 3)

    def test_a_script_turned_off_says_so_in_words(self):
        ed = self.editor("scripted")
        li = ed.list.item(2)
        self.assertEqual(li.text(), "Unused (off)")
        self.assertEqual(li.data(Qt.AccessibleTextRole), "Unused, off")
        ed.list.setCurrentRow(2)
        ed.enabled_box.setChecked(True)
        self.assertEqual(li.text(), "Unused")
        self.assertEqual(li.data(Qt.AccessibleTextRole), "Unused")

    def test_the_script_keeps_its_room_and_the_preview_goes_under_it_when_narrow(self):
        from PySide6.QtWidgets import QWidget
        win = QWidget()
        win.resize(960, 600)
        self.eds.append(win)
        ed = self.se.ScriptEditor(win, "scripted")
        self.eds.insert(0, ed)
        ed.adjustSize()                                                  # (as sheets.place sizes a sheet)
        self.assertEqual((ed.width(), ed.height()), (912, 504))          # (the window's room, not the content's)
        ed.show()
        self.wait(ed)
        ed.fit_panes(force=True)
        settle(self.app, 4)
        self.assertEqual(ed.work.orientation(), Qt.Horizontal)
        self.assertGreaterEqual(ed.work.widget(0).width(), ed.SCRIPT_MIN)
        self.assertLessEqual(ed.list.width(), 200)
        ed.resize(730, 504)
        settle(self.app, 4)
        self.assertEqual(ed.work.orientation(), Qt.Vertical)
        self.assertGreaterEqual(ed.work.widget(0).width(), ed.SCRIPT_MIN)

    def test_no_tag_changed_is_said_in_the_table_area(self):
        ed = self.editor("plain")
        self.assertIs(ed.tag_stack.currentWidget(), ed.no_tags)
        self.assertEqual(ed.no_tags.head.text(), "No tagger scripts")
        ed = self.editor("scripted")
        self.assertIs(ed.tag_stack.currentWidget(), ed.tags)            # ($set(genre,Jazz) changes the genre)
        ed.run_box.setChecked(False)
        self.wait(ed)
        self.assertIs(ed.tag_stack.currentWidget(), ed.no_tags)
        self.assertEqual(ed.no_tags.head.text(), "Tagger scripts are off")

    def test_error_inline_and_save_refused(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("%album%/\n  $left(%title%)")
        self.wait(ed)
        self.assertIn("Line 2, column 3", ed.error_label.text())
        self.assertIn("$left", ed.error_label.text())
        self.assertFalse(ed.error_icon.pixmap().isNull())
        waves = [s for s in ed.edit.extraSelections()
                 if s.format.underlineStyle() == QTextCharFormat.WaveUnderline]
        self.assertEqual(len(waves), 1)
        self.assertEqual(waves[0].cursor.selectionStart(), len("%album%/\n  "))
        before = self.saved("scripted")
        self.assertFalse(ed.save())
        self.assertEqual(self.gui.sheets.shown[-1][0], "warning")
        self.assertIn("Line 2, column 3", self.gui.sheets.shown[-1][2])
        self.assertEqual(self.saved("scripted"), before)                 # nothing written
        ed.edit.setPlainText("%album%/$left(%title%,3)")
        self.wait(ed)
        self.assertEqual(ed.error_label.text(), "")
        self.assertEqual(ed.name_label.text(), "Sample Album/Sam.flac")

    def test_default_profile_cannot_be_saved(self):
        ed = self.editor("default")
        ed.edit.setPlainText("%title%")
        self.assertFalse(ed.save_btn.isEnabled())
        self.assertFalse(ed.save())
        self.assertIn("cannot be changed", ed.source_note.text())
        self.assertFalse(self.pf.path_of("default").exists())
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtTest import QTest as _QTest
        self.gui.sheets.shown.clear()
        _QTest.keyClick(ed.edit, _Qt.Key_S, _Qt.ControlModifier)      # (Ctrl+S answers: why it cannot save)
        self.assertEqual(self.gui.sheets.shown[-1][0], "information")
        self.assertIn("Ctrl+N", self.gui.sheets.shown[-1][2])
        self.assertFalse(self.pf.path_of("default").exists())

    def test_naming_script_shows_its_name_as_a_headline_and_wraps(self):
        from PySide6.QtWidgets import QPlainTextEdit
        ed = self.editor("default")
        ed.list.setCurrentRow(0)                                       # (the file naming script)
        self.assertTrue(ed.name_edit.isHidden())
        self.assertTrue(ed.enabled_box.isHidden())
        self.assertFalse(ed.name_head.isHidden())
        self.assertEqual(ed.name_head.text(), "File naming script")
        self.assertTrue(ed.wrap_box.isChecked())
        self.assertEqual(ed.edit.lineWrapMode(), QPlainTextEdit.WidgetWidth)
        ed.wrap_box.setChecked(False)
        self.assertEqual(ed.edit.lineWrapMode(), QPlainTextEdit.NoWrap)
        self.assertLess(ed.list.maximumHeight(), 200)                 # (as tall as its scripts)
        self.assertEqual(ed.split.objectName(), "scriptSplit")
        self.assertEqual(ed.split.handleWidth(), 1)

    def follows_picard(self):
        ini = picard_ini(_tempfile.mkdtemp(prefix="tm-se-picard-"), [[0, "From Picard", True, "$set(genre,Picard)"]])
        p = mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini)})
        p.start()
        self.addCleanup(p.stop)
        self.pf.save(self.pf.from_dict({"name": "follows", "naming": {"template": "%album%/%title%"}}))
        return self.editor("follows")

    def test_tagger_scripts_from_picard_are_kept_following(self):
        ed = self.follows_picard()
        self.assertEqual([it["name"] for it in ed.items], ["File naming script", "From Picard"])
        self.assertTrue(ed.run_box.isChecked())
        self.assertIn("your tagger settings", ed.source_note.text())
        rows = {ed.tags.item(r, 0).toolTip(): ed.tags.item(r, 2).text() for r in range(ed.tags.rowCount())}
        self.assertEqual(rows, {"genre": "Picard"})
        ed.edit.setPlainText("%title%")
        self.assertEqual(set(ed.changes()), {"template"})
        self.assertTrue(ed.save())
        data = self.saved("follows")
        self.assertEqual(data["naming"]["template"], "%title%")
        self.assertNotIn("list_of_scripts", data.get("tagger") or {})    # (still Picard's)

    def test_editing_picards_tagger_script_keeps_it_in_the_profile(self):
        ed = self.follows_picard()
        ed.list.setCurrentRow(1)
        ed.edit.setPlainText("$set(genre,Mine)")
        self.assertEqual(set(ed.changes()), {"list_of_scripts"})
        self.assertTrue(ed.save())
        self.assertEqual(self.saved("follows")["tagger"]["list_of_scripts"],
                         [[0, "From Picard", True, "$set(genre,Mine)"]])

    def test_closing_with_changes_asks(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("%title%")
        ed.reject()
        self.assertEqual(self.gui.sheets.shown[-1][0], "question")       # offscreen: "keep editing"
        self.assertTrue(ed.isVisible())

    def test_discarding_changes_writes_nothing(self):
        ed = self.editor("scripted")
        before = self.saved("scripted")
        ed.edit.setPlainText("%title%")
        with mock.patch.object(self.gui.sheets, "question", return_value=True) as asked:
            ed.reject()
        self.assertEqual(asked.call_count, 1)
        self.assertFalse(ed.isVisible())
        self.assertEqual(self.saved("scripted"), before)

    def test_closing_unchanged_asks_nothing(self):
        ed = self.editor("scripted")
        ed.reject()
        self.assertNotIn("question", [x[0] for x in self.gui.sheets.shown])
        self.assertFalse(ed.isVisible())

    def test_typing_waits_before_the_preview(self):
        ed = self.editor("scripted")
        ed.edit.setFocus()
        ed.edit.moveCursor(QTextCursor.End)
        QTest.keyClicks(ed.edit, "x")
        self.assertTrue(ed.timer.isActive())
        self.assertEqual(ed.timer.interval(), self.se.ScriptEditor.DEBOUNCE)
        ed.result = None
        self.wait(ed, run=False)                                         # (the timer runs it)
        self.assertEqual(ed.name_label.text(), "Sample Artist/Sample Album/Sample Titlex.flac")

    def test_only_the_newest_preview_is_shown(self):
        ed = self.editor("scripted")
        shown = ed.result
        stale = dict(shown, name="Stale/Name.flac", errors={})
        ed.shown(ed.generation - 1, stale)
        self.assertIs(ed.result, shown)
        self.assertEqual(ed.name_label.text(), "Sample Artist/Sample Album/Sample Title.flac")
        ed.edit.setPlainText("%title%")
        ed.timer.stop()
        ed.run()
        first = ed.generation
        ed.run()
        ed.result = None
        self.wait(ed, run=False)
        self.assertGreater(ed.generation, first)
        self.assertEqual(ed.name_label.text(), "Sample Title.flac")
        ed.shown(first, stale)                                           # (the first run answering last)
        self.assertEqual(ed.name_label.text(), "Sample Title.flac")

    def test_closing_while_a_preview_runs(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from shiboken6 import isValid
        from PySide6.QtWidgets import QMainWindow
        win = QMainWindow()
        win.show()
        real = self.se.run_preview

        def slow(*a, **k):
            time.sleep(0.4)
            return real(*a, **k)
        with mock.patch.object(self.se, "run_preview", slow):
            ed = self.se.open_editor(win, "scripted")
            ed.timer.stop()
            ed.run()
            threads = list(ed._threads)
            self.assertTrue(any(self.gui.work._alive(t) for t, _w in threads))
            t0 = time.monotonic()
            ed.reject()                                                   # (clean: closes at once)
            self.assertLess(time.monotonic() - t0, 0.2)                  # (never waits for the preview)
            self.assertFalse(ed.isVisible())
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(isValid(ed))                                 # (kept while its preview runs)
            end = time.time() + 20
            while time.time() < end and isValid(ed):
                settle(self.app, 2)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(ed))                                    # (deleted after, the app still running)
        self.assertFalse(any(self.gui.work._alive(t) for t, _w in threads))
        win.close()

    def test_a_preview_answering_after_closing_is_not_shown(self):
        real = self.se.run_preview

        def slow(*a, **k):
            time.sleep(0.3)
            return dict(real(*a, **k), name="Late/Name.flac")
        ed = self.editor("scripted")
        shown, name = ed.result, ed.name_label.text()
        with mock.patch.object(self.se, "run_preview", slow):
            ed.timer.stop()
            ed.run()
            before = ed.generation
            ed.reject()
            self.assertGreater(ed.generation, before)
            end = time.time() + 20
            while time.time() < end and any(self.gui.work._alive(t) for t, _w in ed._threads):
                settle(self.app, 2)
            settle(self.app, 5)
        self.assertIs(ed.result, shown)
        self.assertEqual(ed.name_label.text(), name)

    def test_the_scripts_command_opens_the_editor(self):
        from shiboken6 import isValid
        win = self.gui.Main()
        win.show()
        settle(self.app)
        try:
            cmd = win.commands["edit_scripts"]
            self.assertIn("Script Editor", cmd.text())
            self.assertIn(cmd, [a for m in win.menuBar().actions() if m.menu() for a in m.menu().actions()])
            win.go("Jobs")
            win.update_album_actions()
            self.assertFalse(cmd.isEnabled())                            # (no profile to edit there)
            win.go("Profiles")
            pr = win.pages["Profiles"]
            pr.reload()
            row = next(i for i in range(pr.list.count()) if pr.list.item(i).data(Qt.UserRole) == "scripted")
            pr.list.setCurrentRow(row)
            win.update_album_actions()
            self.assertTrue(cmd.isEnabled())
            cmd.trigger()
            settle(self.app)
            eds = [e for e in win.findChildren(self.se.ScriptEditor) if isValid(e) and e.isVisible()]
            self.assertEqual(len(eds), 1)
            ed = eds[0]
            self.eds.append(ed)
            self.wait(ed, run=False)
            self.assertEqual(ed.profile_name, "scripted")
            self.assertIs(ed.parentWidget(), win)
            self.assertEqual([it["name"] for it in ed.items], ["File naming script", "Genre", "Unused"])
        finally:
            end = time.time() + 20
            while time.time() < end and win.threads():
                settle(self.app, 2)
            for e in win.findChildren(self.se.ScriptEditor):
                e.dirty = False
                e.close()
            win._closing = True
            win.close()
            win.deleteLater()
            settle(self.app)

    def test_colours(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("$if(%album%,x\\,y,$nosuch(1))$noop(a\n(b) c)z")
        settle(self.app)
        style = self.gui.style
        block = ed.edit.document().firstBlock()

        def colour_at(blk, pos):
            for r in blk.layout().formats():
                if r.start <= pos < r.start + r.length:
                    return r.format.foreground().color().name(), r.format.fontItalic()
            return None, False
        self.assertEqual(colour_at(block, 1)[0], QColor(style.color("accent")).name())       # $if
        self.assertEqual(colour_at(block, 5)[0], QColor(style.color("identified")).name())   # %album%
        self.assertEqual(colour_at(block, 13)[0], QColor(style.color("attention")).name())   # \,
        self.assertEqual(colour_at(block, 18)[0], QColor(style.color("error")).name())       # $nosuch
        self.assertTrue(colour_at(block, 32)[1])                                             # $noop( ...
        second = block.next()
        self.assertTrue(colour_at(second, 0)[1])                                             # ... (b) c)
        self.assertTrue(colour_at(second, 5)[1])
        self.assertIsNone(colour_at(second, 6)[0])                                           # z

    def test_colours_follow_dark_mode(self):
        style = self.gui.style
        style.apply(self.app, "light")
        try:
            ed = self.editor("scripted")
            style.apply(self.app, "dark")
            settle(self.app)
            self.assertFalse(ed.dirty)                                   # (colours redone are no edit)
            ed.edit.setPlainText("$if(a,b)")
            settle(self.app)
            ranges = ed.edit.document().firstBlock().layout().formats()
            self.assertEqual(ranges[0].format.foreground().color().name(), QColor(style.DARK["accent"]).name())
        finally:
            style.apply(self.app, "light")

    def test_matching_bracket_marked(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("$if(a,b)")
        c = ed.edit.textCursor()
        c.setPosition(4)                                                  # just after "("
        ed.edit.setTextCursor(c)
        starts = sorted(s.cursor.selectionStart() for s in ed.edit.extraSelections())
        self.assertEqual(starts, [3, 7])

    def test_help_for_the_function_under_the_cursor(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("$if(%a%,$lower(x),y)")
        c = ed.edit.textCursor()
        c.setPosition(15)
        ed.edit.setTextCursor(c)
        self.assertEqual(ed.help_name, "lower")
        self.assertIn("$lower(text)", ed.help.toPlainText())
        c.setPosition(1)
        ed.edit.setTextCursor(c)
        self.assertIn("$if(if,then[,else])", ed.help.toPlainText())
        self.assertIn("Line 1, column 2", ed.cursor_label.text())

    def test_completion(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("")
        ed.edit.setFocus()
        QTest.keyClicks(ed.edit, "$low")
        self.assertEqual(ed.edit.completer.completionPrefix(), "$low")
        self.assertTrue(ed.edit.completer.popup().isVisible())
        QTest.keyClick(ed.edit.completer.popup(), Qt.Key_Return)
        settle(self.app)
        self.assertEqual(ed.edit.toPlainText(), "$lower(")
        QTest.keyClicks(ed.edit, "%albuma")
        self.assertTrue(ed.edit.completer.popup().isVisible())
        QTest.keyClick(ed.edit.completer.popup(), Qt.Key_Return)
        settle(self.app)
        self.assertTrue(ed.edit.toPlainText().startswith("$lower(%albumartist"))
        self.assertTrue(ed.edit.toPlainText().endswith("%"))
        # (after a closed variable, a % opens nothing to complete with what follows)
        ed.edit.setPlainText("%album%")
        ed.edit.moveCursor(QTextCursor.End)
        self.assertIsNone(ed.edit.word_before())
        ed.edit.setPlainText("\\$lo")
        ed.edit.moveCursor(QTextCursor.End)
        self.assertIsNone(ed.edit.word_before())                         # (an escaped $ is text)

    def test_completion_keys(self):
        ed = self.editor("scripted")
        popup = ed.edit.completer.popup()
        ed.edit.setPlainText("")
        ed.edit.setFocus()
        QTest.keyClicks(ed.edit, "$lowe")
        self.assertTrue(popup.isVisible())
        QTest.keyClick(ed.edit, Qt.Key_Escape)
        self.assertFalse(popup.isVisible())
        self.assertTrue(ed.isVisible())                                  # (Esc closed the list only)
        QTest.keyClick(ed.edit, Qt.Key_Backspace)
        self.assertTrue(popup.isVisible())                               # (suggested again)
        self.assertEqual(ed.edit.completer.completionPrefix(), "$low")
        QTest.keyClick(ed.edit, Qt.Key_Escape)
        ed.edit.setPlainText("")
        QTest.keyClicks(ed.edit, "$")
        self.assertFalse(popup.isVisible())                              # (nothing typed after the $ yet)
        QTest.keyClick(ed.edit, Qt.Key_Space, Qt.ControlModifier)        # (asked for: every function)
        self.assertTrue(popup.isVisible())
        self.assertEqual(ed.edit.completer.completionCount(), len(ed.edit.function_names))
        self.assertGreater(len(ed.edit.function_names), 50)
        self.assertEqual(ed.edit.toPlainText(), "$")
        QTest.keyClick(ed.edit, Qt.Key_Escape)
        self.assertFalse(popup.isVisible())

    def test_ctrl_s_saves(self):
        ed = self.editor("scripted")
        ed.edit.setPlainText("%album%/%title% x")
        QTest.keyClick(ed, Qt.Key_S, Qt.ControlModifier)
        self.assertEqual(self.saved("scripted")["naming"]["template"], "%album%/%title% x")
        self.assertFalse(ed.isVisible())

    def test_open_editor_is_a_sheet_of_the_window(self):
        from PySide6.QtWidgets import QMainWindow
        win = QMainWindow()
        win.show()
        ed = self.se.open_editor(win, "scripted", ALBUM)
        self.eds.append(ed)
        self.wait(ed, run=False)
        self.assertIs(ed.parentWidget(), win)
        self.assertEqual(ed.windowModality(), Qt.WindowModal)
        self.assertTrue(ed.isVisible())
        self.assertTrue(ed.accessibleName())
        for w in (ed.list, ed.edit, ed.help, ed.tags, ed.file_box, ed.add_btn, ed.remove_btn, ed.save_btn):
            self.assertTrue(w.accessibleName(), w)
        ed.resize(960, 600)
        settle(self.app)
        self.assertLessEqual(ed.minimumSizeHint().width(), 960)
        win.close()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + [a for a in sys.argv[1:] if not _os.path.isdir(_os.path.expanduser(a))])
