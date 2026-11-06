# The settings in the app: the Profile editor's sections (each control saves to the profile, only
# what was changed is written), problems shown under their row, the presets, Restore Defaults,
# the change markers, the search field, the naming example, controls greyed out with a reason,
# New and Duplicate Profile, the one-time "save the old built-in settings" sheet, and the
# Preferences panes (General, Network & Privacy with Test Connection, File Safety, Advanced).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None

import credentials  # noqa: E402

for _var in credentials.ENV.values():                         # (no key of the person running the tests)
    _os.environ.pop(_var, None)


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.005)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import app_settings
        import profiles as pf
        self.pf = pf
        app_settings.save(dict(app_settings.DEFAULTS))
        for f in pf.profiles_dir().glob("*.json") if pf.profiles_dir().exists() else []:
            f.unlink()
        pf.shared_file().unlink(missing_ok=True)          # (and the settings shared by all profiles)
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.gui.sheets.shown.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-ui-"))
        self.win = self.gui.Main()
        self.win.show()
        settle(self.app)

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.win._closing = True
        self.win.close()
        if self.win.prefs_window is not None:
            self.win.prefs_window.deleteLater()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def page(self, name, **data):
        """The Profiles page showing a new profile NAME (with data)."""
        self.pf.save(self.pf.from_dict(dict({"name": name}, **data)))
        pr = self.win.pages["Profiles"]
        self.win.go("Profiles")
        pr.reload()
        rows = [pr.list.item(i).data(0x0100) for i in range(pr.list.count())]
        pr.list.setCurrentRow(rows.index(name))
        settle(self.app, 3)
        return pr


class Editor(Base):
    def test_each_section_saves_its_controls(self):
        pr = self.page("Sec")
        ed = pr.editor
        lib, aside = self.tmp / "lib", self.tmp / "aside"
        lib.mkdir()
        aside.mkdir()
        changes = {"_skip": ["#recycle", "Downloads", "*/Downloads/*"],       # Source
                   "formats": ["lossless"],
                   "identification.accept": "album",                         # Identification
                   "providers.web.shops": False,
                   "matching.preferred_countries": ["GB", "DE"],
                   "genres.max": 3,                                          # Tags
                   "genres.discogs_styles": "own_tag",
                   "_lyrics": "always",
                   "artwork.min_px": 1000,                                   # Artwork
                   "artwork.artist.enabled": "file",
                   "operation": "copy", "destination": str(lib),             # Target
                   "naming.preset": "standard",
                   "on_existing": "skip",
                   "unresolved.action": "move", "unresolved.move_to": str(aside),   # Nothing identified
                   "retry.after_days": 7}                                    # Automation
        for k, v in changes.items():
            ed.set_value(k, v)
        self.assertEqual(ed.problems(), {})
        self.assertTrue(pr.save_btn.isEnabled())
        self.assertTrue(pr.save())
        p = self.pf.load("Sec").data
        from gui.profile_editor import dig
        saved = dict(changes, skip_folders=["#recycle", "Downloads"], exclude_paths=["*/Downloads/*"])
        saved.update({"lyrics.enabled": True, "audiophile.lyrics": True, "artwork.artist.enabled": True})
        for k in ("_skip", "_lyrics"):
            del saved[k]
        for k, v in saved.items():
            self.assertEqual(dig(p, k), v, k)
        self.assertNotIn("unattended", ed.rows)                   # (set on the Watched folders page)
        self.assertNotIn("watch.settle_minutes", ed.rows)
        self.assertFalse(pr.dirty())
        self.assertFalse(pr.save_btn.isEnabled())

    def test_the_example_opens_and_asks_for_its_library_folder(self):
        self.pf.install_examples()
        pr = self.win.pages["Profiles"]
        self.win.go("Profiles")
        pr.reload()
        rows = [pr.list.item(i).data(0x0100) for i in range(pr.list.count())]
        pr.list.setCurrentRow(rows.index("Lossy"))
        settle(self.app, 3)
        self.assertEqual(pr.shown_name, "Lossy")                       # (no "cannot be read" sheet)
        self.assertIn("destination", pr.editor.problems())
        lib = self.tmp / "lossy"
        lib.mkdir()
        pr.editor.set_value("destination", str(lib))
        settle(self.app, 2)
        self.assertEqual(pr.editor.problems(), {})
        self.assertTrue(pr.save())
        self.assertEqual(self.pf.load("Lossy")["destination"], str(lib))

    def test_only_what_was_changed_is_written(self):
        pr = self.page("Hand", tagger={"va_name": "V.A."}, artwork={"sources": ["local", "caa"]})
        pr.editor.set_value("tag_level", "basic")
        pr.save()
        raw = self.pf.raw("Hand")
        self.assertEqual(raw["tag_level"], "basic")
        self.assertEqual(raw["tagger"], {"va_name": "V.A."})                # (a setting the app has no row for)
        self.assertEqual(raw["artwork"]["sources"], ["local", "caa"])        # (an order of its own, kept)
        self.assertEqual(pr.art_src.value(), ["local", "caa"])              # (shown as it is)

    def test_choose_opens_a_list_and_a_custom_value_stays_with_its_profile(self):
        from gui import profile_editor as pe
        pr = self.page("Fmt")
        box = pr.formats
        choose = box.findData(pe.CHOOSE)
        with mock.patch.object(self.gui.sheets, "run", return_value=1), \
                mock.patch.object(pe.ListSheet, "items", return_value=["flac", "mp3"]):
            box.setCurrentIndex(choose)
            pr.editor._picked(pe.BY_KEY["formats"], box, choose)
        self.assertEqual(pr.editor.value("formats"), ["flac", "mp3"])
        self.assertEqual(box.currentText(), "FLAC, MP3")
        with mock.patch.object(self.gui.sheets, "run", return_value=0):   # (Cancel: back to what it was)
            box.setCurrentIndex(choose)
            pr.editor._picked(pe.BY_KEY["formats"], box, choose)
        self.assertEqual(pr.editor.value("formats"), ["flac", "mp3"])
        pr.save()
        self.assertEqual(self.pf.raw("Fmt")["formats"], ["flac", "mp3"])
        self.page("Other")                                          # (another profile: no FLAC, MP3 item)
        self.assertEqual([box.itemData(i) for i in range(box.count())],
                         [[], ["lossless"], ["lossy"], pe.CHOOSE])

    def test_problems_show_under_their_row_and_save_shows_them(self):
        pr = self.page("Bad")
        ed = pr.editor
        ed.set_value("operation", "copy")
        self.assertIn("library folder", ed.rows["destination"].problem.text())
        self.assertFalse(ed.rows["destination"].problem.isHidden())
        self.assertTrue(pr.save_btn.isEnabled())           # (pressed, it shows the first problem; saves nothing)
        self.assertIn("1 problem", pr.dirty_note.text())
        ed.set_value("_skip", ["*"])
        self.assertIn("every folder", ed.rows["_skip"].problem.text())
        self.assertIn("2 problems", pr.dirty_note.text())
        pr.save_clicked()                                   # (scrolls to the first problem, saves nothing)
        self.assertNotEqual(self.pf.raw("Bad").get("operation"), "copy")
        ed.set_value("_skip", ["#recycle"])
        ed.set_value("destination", str(self.tmp))
        self.assertTrue(ed.rows["destination"].problem.isHidden())
        self.assertTrue(pr.save_btn.isEnabled())
        # the lossy folder: empty means with the rest; a typed one is saved as it is
        self.assertEqual(ed.value("destinations.lossy"), "")
        ed.set_value("destinations.lossy", str(self.tmp / "lossy"))
        self.assertEqual(ed.problems(), {})

    def test_presets_apply_and_keep_folders_and_what_happens_to_files(self):
        pr = self.page("Pre", operation="copy", destination=str(self.tmp), description="mine")
        ed = pr.editor
        ed.apply_preset("collector")
        self.assertEqual(ed.value("source"), "discogs_preferred")
        self.assertEqual(ed.value("artwork.artist.enabled"), "both")
        self.assertEqual(ed.value("on_existing"), "ask")
        self.assertEqual((ed.value("operation"), ed.value("destination"), ed.value("description")),
                         ("copy", str(self.tmp), "mine"))
        self.assertTrue(pr.dirty())
        ed.apply_preset("keep")
        self.assertEqual((ed.value("operation"), ed.value("field_policy.default"), ed.value("artwork.when")),
                         ("in_place", "fill_missing", "needed"))
        ed.apply_preset("typical")
        self.assertEqual(ed.value("source"), "musicbrainz_preferred")
        self.assertEqual(ed.value("field_policy.default"), "replace")
        # Start From… asks first, listing what changes (offscreen: the sheet says no)
        self.gui.sheets.shown.clear()
        target = ed.preset_values("collector")
        self.assertFalse(ed._ask_changes("Start from “Collector”?", target, "Use These Settings"))
        kind, head, body = self.gui.sheets.shown[-1]
        self.assertEqual(kind, "confirm")
        self.assertIn("Sources: MusicBrainz preferred → Discogs preferred", body)

    def test_restore_defaults_and_change_markers(self):
        pr = self.page("Rst", source="discogs_only", genres={"max": 9})
        ed = pr.editor
        self.assertTrue(ed.rows["source"].changed())
        self.assertEqual(ed.rows["source"].dot.accessibleName(), "changed from default")
        self.assertTrue(ed.rows["genres.max"].changed())
        self.assertFalse(ed.rows["tag_level"].changed())
        ed.restore_defaults("identification")                 # (one section: Tags keeps its change)
        self.assertEqual(ed.value("source"), "musicbrainz_preferred")
        self.assertFalse(ed.rows["source"].changed())
        self.assertEqual(ed.value("genres.max"), 9)
        ed.restore_defaults()
        self.assertEqual(ed.value("genres.max"), 5)
        pr.save()
        raw = self.pf.raw("Rst")
        self.assertNotIn("source", raw)
        self.assertNotIn("max", raw.get("genres", {}))

    def test_naming_example_follows_the_pickers(self):
        pr = self.page("Nm")
        ed = pr.editor
        ed.set_value("operation", "in_place_rename")                # (in place without renaming keeps the names)
        text = ed.preview.text()
        self.assertIn("Ada Lark &amp; Milo Fen/1991 - Songs From the Long Winter Evenings Under Northern Skies/"
                      "2-03 - The Lighthouse Keeper.flac", text)
        self.assertIn("The Lighthouse Keeper (2).flac", text)
        self.assertFalse(ed.controls["filenames.max_album_words"].isEnabled())   # (this layout: the whole title)
        self.assertIn("whole album title", ed.rows["filenames.max_album_words"].reason.text())
        ed.set_value("naming.preset", "tagmender")
        self.assertTrue(ed.controls["filenames.max_album_words"].isEnabled())
        ed.set_value("filenames.max_album_words", 5)
        self.assertIn("/1991 - Songs From the Long Winter/Disc 02/203.TLK.flac", ed.preview.text())
        ed.set_value("on_existing", "keep_code")
        self.assertIn("203.TLK ~1a2b.flac", ed.preview.text())
        ed.set_value("naming.editions", "edition_year")
        self.assertIn("[1994 CD]", ed.preview.text())
        self.assertTrue(pr.picard_btn.isEnabled())
        ed.set_value("filenames.max_name_bytes", 40)
        self.assertIn("Too long", ed.preview.text())

    def test_search_shows_matching_rows_and_opens_advanced(self):
        pr = self.page("Srch")
        ed = pr.editor
        pr.find.setText("fingerprint")
        settle(self.app, 3)
        shown = [k for k, r in ed.rows.items() if r.isVisibleTo(ed)]
        self.assertIn("acoustid", shown)
        self.assertNotIn("artwork.min_px", shown)
        self.assertFalse(ed.sections["artwork"].isVisibleTo(ed))
        pr.find.setText("MP3 tag")                      # (an Advanced row: its disclosure opens)
        settle(self.app, 3)
        self.assertTrue(ed.rows["tagger.write_id3v23"].isVisibleTo(ed))
        self.assertTrue(ed.advanced["tags"].isChecked())
        pr.find.setText("")
        settle(self.app, 3)
        self.assertTrue(ed.rows["tag_level"].isVisibleTo(ed))
        self.assertTrue(ed.sections["artwork"].isVisibleTo(ed))

    def test_rows_greyed_out_with_a_reason(self):
        import requirements as rq
        with mock.patch.object(rq, "ready", return_value=False):
            pr = self.page("Grey")
        row = pr.editor.rows["acoustid"]
        self.assertFalse(row.control.isEnabled())
        self.assertIn("Needs fpcalc and an AcoustID key", row.reason.text())
        self.assertIn('href="setup"', row.reason.text())
        self.assertFalse(pr.editor.rows["providers.web.gnudb"].control.isEnabled())
        with mock.patch.object(rq, "ready", return_value=True):
            pr.reload()
            settle(self.app, 3)
        self.assertTrue(pr.editor.rows["acoustid"].control.isEnabled())
        # a database the Sources choice leaves out: what only it uses is greyed, and says why
        pr.editor.set_value("source", "musicbrainz_only")
        self.assertFalse(pr.editor.rows["genres.discogs_styles"].control.isEnabled())
        self.assertIn("Discogs is not asked", pr.editor.rows["genres.discogs_styles"].reason.text())
        # rows that apply only to one choice appear only then
        self.assertTrue(pr.editor.rows["remove_empty_dirs"].isHidden())
        pr.editor.set_value("operation", "move")
        self.assertTrue(pr.editor.rows["remove_empty_dirs"].isHidden())       # (the default: nothing to show)
        pr = self.page("Keeps", remove_empty_dirs=False)                      # (a saved value that differs: shown)
        self.assertTrue(pr.editor.rows["remove_empty_dirs"].isHidden())       # (but only when moving)
        pr.editor.set_value("operation", "move")
        self.assertFalse(pr.editor.rows["remove_empty_dirs"].isHidden())

    def test_new_and_duplicate_profile(self):
        pr = self.win.pages["Profiles"]
        self.assertTrue(pr.create("Archive", "keep"))
        p = self.pf.load("Archive")
        self.assertEqual((p["field_policy"]["default"], p["artwork"]["replace"]), ("fill_missing", "never"))
        self.assertEqual(pr.shown_name, "Archive")
        self.assertFalse(pr.create("Archive"))                   # (never over one that is there)
        self.pf.save(self.pf.from_dict({"name": "Src", "sources": [{"path": str(self.tmp)}], "tag_level": "basic"}))
        self.assertTrue(pr.create("Src copy", base="Src"))
        raw = self.pf.raw("Src copy")
        self.assertEqual((raw["tag_level"], raw.get("sources")), ("basic", None))


class BuiltinSheet(Base):
    def test_the_old_built_in_settings_are_offered_once(self):
        import app_settings
        from gui.profiles import offer_previous_builtin
        app_settings.set_value("notices.builtin_defaults", False)          # (an earlier install)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Save the Old Settings") as asked:
            self.assertTrue(offer_previous_builtin(self.win))
        self.assertIn("new defaults", asked.call_args.args[1])
        self.assertTrue(self.pf.path_of(self.pf.PREVIOUS_BUILTIN_NAME).exists())
        self.assertEqual(self.pf.load(self.pf.PREVIOUS_BUILTIN_NAME).source(), "discogs_preferred")
        with mock.patch.object(self.gui.sheets, "ask") as again:
            self.assertFalse(offer_previous_builtin(self.win))
        again.assert_not_called()

    def test_a_new_install_is_not_asked(self):
        from gui.profiles import offer_previous_builtin
        with mock.patch.object(self.gui.sheets, "ask") as asked:
            self.assertFalse(offer_previous_builtin(self.win))
        asked.assert_not_called()

    def test_keeping_the_new_defaults_asks_no_more(self):
        import app_settings
        from gui.profiles import offer_previous_builtin
        app_settings.set_value("notices.builtin_defaults", False)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Use the New Defaults"):
            self.assertFalse(offer_previous_builtin(self.win))
        self.assertFalse(self.pf.path_of(self.pf.PREVIOUS_BUILTIN_NAME).exists())
        with mock.patch.object(self.gui.sheets, "ask") as again:
            self.assertFalse(offer_previous_builtin(self.win))
        again.assert_not_called()


class PreferencesPanes(Base):
    def test_general_saves_this_computers_settings(self):
        import app_settings
        from gui import player
        prefs = self.win.preferences()
        prefs.show_tab("General")
        g = prefs.general
        g.skip.setCurrentIndex(g.skip.findData(15))
        self.assertEqual(app_settings.get("player.skip_seconds"), 15)
        self.assertEqual(player.skip_ms(), 15000)
        g.music.setText(str(self.tmp))
        g.set_music()
        self.assertEqual(app_settings.get("library_folder"), str(self.tmp))
        self.assertEqual(self.gui.common.music_folder(), str(self.tmp))
        g.music.setText("relative/path")
        g.set_music()
        self.assertEqual(app_settings.get("library_folder"), str(self.tmp))   # (refused, put back)
        self.assertEqual(g.music.text(), str(self.tmp))
        g.notify_watch.setChecked(False)
        self.assertFalse(app_settings.get("notifications.watch"))

    def test_network_pane_tests_the_connection(self):
        import app_settings
        import connection
        prefs = self.win.preferences()
        prefs.show_tab("Network")
        self.assertEqual(prefs.windowTitle(), "Network & Privacy — TagMender Preferences")
        pane = prefs.network
        results = [connection.Result("musicbrainz", connection.OK, 95, 200, "answered in 95 ms"),
                   connection.Result("discogs", connection.BUSY, 0, 503, "busy"),
                   connection.Result("acoustid", connection.REFUSED, 50, 400, "key refused"),
                   connection.Result("coverartarchive", connection.UNREACHABLE, 0, 0, "unreachable")]
        with mock.patch.object(connection, "test_all", return_value=results) as checked:
            pane.connection.button.click()
            end = time.time() + 10
            while time.time() < end and not pane.connection.button.isEnabled():
                settle(self.app, 2)
        checked.assert_called_once()
        words = {n: w.text() for n, (_p, w) in pane.connection.rows.items()}
        self.assertEqual(words["musicbrainz"], "Answered in 95 ms")
        self.assertEqual(words["coverartarchive"], "Unreachable")
        self.assertTrue(any("http_proxy" in lb.text() for lb in pane.findChildren(type(pane.said))))
        pane.refresh.setCurrentIndex(pane.refresh.findData(0))
        self.assertEqual(app_settings.get("network.refresh_days"), 0)
        pane.effort.setCurrentIndex(pane.effort.findData("thorough"))
        self.assertEqual(app_settings.get("ocr.effort"), "thorough")
        pane.langs.set_value(["eng", "deu"])
        pane.langs.changed.emit()
        self.assertEqual(app_settings.get("ocr.languages"), ["eng", "deu"])

    def test_advanced_pane_names_in_the_export(self):
        import app_settings
        prefs = self.win.preferences()
        prefs.show_tab("Checks")                                    # (the older name opens Advanced)
        pane = prefs.checks_pane
        self.assertIs(prefs.stack.currentWidget(), pane)
        self.assertFalse(hasattr(pane, "plugins"))                  # (no code folder: the plugins come with it)
        self.assertTrue(pane.anon.isChecked())                      # (names hidden by default)
        pane.anon.setChecked(False)
        self.assertFalse(app_settings.get("diagnostics.hide_names"))

    def test_rows_switches_and_segments_have_names(self):
        from gui.rows import Row, Segmented, Switch
        prefs = self.win.preferences()
        for pane in (prefs.general, prefs.network, prefs.files, prefs.checks_pane):
            for w in pane.findChildren(Switch) + pane.findChildren(Segmented):
                self.assertTrue(w.accessibleName(), w)
        ed = self.win.pages["Profiles"].editor
        for row in ed.findChildren(Row):
            self.assertTrue(row.control.accessibleName(), row.label_text)



class ProfileManagement(Base):
    def test_duplicate_rename_and_delete_from_the_page(self):
        from unittest import mock
        pr = self.page("Profile 1", description="mine")
        with mock.patch.object(self.gui.sheets, "get_text", return_value=(self.pf.next_name(), True)) as ask:
            pr.duplicate()
        self.assertEqual(ask.call_args.kwargs["text"], "Profile 2")    # (the next free "Profile N")
        self.assertEqual(self.pf.raw("Profile 2")["description"], "mine")
        with mock.patch.object(self.gui.sheets, "get_text", return_value=("Ambient", True)):
            pr.rename()
        self.assertEqual(sorted(self.pf.names()), ["Ambient", "Profile 1"])
        self.assertEqual(pr.shown_name, "Ambient")
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
            pr.delete()
        self.assertEqual(self.pf.names(), ["Profile 1"])

    def test_no_more_than_the_most_a_computer_keeps(self):
        for i in range(self.pf.MAX_PROFILES):
            self.pf.save(self.pf.from_dict({"name": f"P{i}"}))
        pr = self.win.pages["Profiles"]
        self.assertFalse(pr.create("One more"))
        self.assertNotIn("One more", self.pf.names())
        self.assertIn("No more profiles can be made.", str(self.gui.sheets.shown))

    def test_sections_fold_and_stay_folded(self):
        pr = self.page("Folds")
        ed = pr.editor
        ed.folds["tags"].click()                                        # (folded by hand: remembered)
        settle(self.app)
        self.assertFalse(ed.folds["tags"].isChecked())
        self.assertIn("tags", self.gui.common._gui_prefs()["profile_sections_folded"])
        ed.search("artist photos")                                      # (a match opens its section)
        self.assertTrue(ed.folds["artwork"].isChecked())
        ed.search("")
        ed.fold_all(False)
        self.assertFalse(any(f.isChecked() for f in ed.folds.values()))
        ed.fold_all(True)
        self.assertEqual(self.gui.common._gui_prefs()["profile_sections_folded"], [])

    def test_a_folder_is_chosen_in_a_folder_list_sheet(self):
        from unittest import mock
        from gui.rows import ListSheet
        pr = self.page("Src")
        sheet = ListSheet(pr, "Folders", "", [], folders=True)
        with mock.patch.object(self.gui.sheets, "choose_folder", return_value=str(self.tmp)):
            sheet.add_chosen()
            sheet.add_chosen()                                          # (the same folder only once)
        self.assertEqual(sheet.items(), [str(self.tmp)])


if __name__ == "__main__":
    unittest.main()
