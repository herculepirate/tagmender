# The Profiles page's merged rows (settings consolidation, phases 1-3): each reads the saved keys it covers
# and writes the same keys back, a saved combination it cannot say shows as "As saved" and is never
# rewritten, levels.items() is unchanged, and a profile opened and saved without edits comes out the same.
import os as _os
import tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/tagger.ini"
_os.environ.pop("TAGMENDER_PICARD_INI", None)

import copy
import itertools
import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None

import levels  # noqa: E402
import profiles as pf  # noqa: E402

REAL = Path("~/.config/tagmender/profiles").expanduser()


class LyricsLevels(unittest.TestCase):
    """levels.items() for every saved combination at every level: below Audiophile only lyrics.enabled
    counts, at it only audiophile.lyrics (default on)."""

    def test_the_table_is_unchanged(self):
        for enabled, audio, level in itertools.product((False, True), (False, True), levels.LEVELS):
            p = {"tag_level": level, "lyrics": {"enabled": enabled}, "audiophile": {"lyrics": audio}}
            got = bool(levels.items(p).get("lyrics"))
            want = audio if level == "audiophile" else enabled
            self.assertEqual(got, want, (enabled, audio, level))


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Merged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from gui import profile_editor as pe
        cls.pe = pe

    def full(self, **data):
        return pf.from_dict(dict({"name": "x", "version": pf.SCHEMA_VERSION}, **data), check=False).data

    def editor(self, **data):
        ed = self.pe.ProfileEditor()
        full = self.full(**data)
        ed.load(copy.deepcopy(data), full, self.full())
        return ed

    def written(self, ed, data=None):
        return ed.write(copy.deepcopy(data) if data is not None else {"name": "x"})

    def effective(self, data):
        return pf.from_dict(dict(data, name="x", version=pf.SCHEMA_VERSION), check=False).data

    # ---- Lyrics ----------------------------------------------------------------------------
    def test_lyrics_reads_each_saved_combination_honestly(self):
        pe = self.pe
        table = {(False, True): "audiophile", (True, True): "always", (False, False): "off"}
        for (enabled, audio), want in table.items():
            ed = self.editor(lyrics={"enabled": enabled}, audiophile={"lyrics": audio})
            self.assertEqual(ed.value("_lyrics"), want)
        ed = self.editor(lyrics={"enabled": True}, audiophile={"lyrics": False})   # (below the level, not at it)
        self.assertIsInstance(ed.value("_lyrics"), pe.Saved)
        self.assertTrue(ed.controls["_lyrics"].currentText().startswith("As saved"))
        self.assertEqual(self.written(ed, {"name": "x", "lyrics": {"enabled": True},
                                           "audiophile": {"lyrics": False}}),
                         {"name": "x", "lyrics": {"enabled": True}, "audiophile": {"lyrics": False}})

    def test_choosing_lyrics_writes_what_each_level_does(self):
        want = {"off": (False, False, False), "audiophile": (False, False, True), "always": (True, True, True)}
        # (below Audiophile: Basic and Enthusiast, then at Audiophile)
        for choice, (basic, enthusiast, audiophile) in want.items():
            ed = self.editor()
            ed.set_value("_lyrics", choice)
            data = self.effective(self.written(ed))
            for level, on in (("basic", basic), ("enthusiast", enthusiast), ("audiophile", audiophile)):
                self.assertEqual(bool(levels.items(dict(data, tag_level=level)).get("lyrics")), on, (choice, level))

    def test_the_lyrics_places_show_unless_lyrics_are_off(self):
        ed = self.editor(lyrics={"enabled": False}, audiophile={"lyrics": False})
        self.assertTrue(ed.rows["lyrics.embed"].isHidden())
        ed.set_value("_lyrics", "audiophile")
        self.assertFalse(ed.rows["lyrics.embed"].isHidden())

    # ---- Sources ---------------------------------------------------------------------------
    def test_sources_reads_and_writes_the_same_keys(self):
        cases = [({}, "musicbrainz_preferred"),
                 ({"source": "discogs_only"}, "discogs_only"),
                 ({"source": "discogs_preferred", "providers": {"musicbrainz": {"enabled": False}}}, "discogs_only"),
                 ({"source": "musicbrainz_preferred", "providers": {"discogs": {"enabled": False}}},
                  "musicbrainz_only"),
                 ({"providers": {"musicbrainz": {"identify": False}, "discogs": {"identify": False}}}, "web_only")]
        for data, want in cases:
            self.assertEqual(self.editor(**data).value("source"), want, data)
        ed = self.editor(source="discogs_preferred", providers={"discogs": {"enabled": False}})   # (cannot be said)
        self.assertIsInstance(ed.value("source"), self.pe.Saved)
        for choice in ("musicbrainz_only", "discogs_only", "musicbrainz_preferred", "discogs_preferred"):
            e = self.editor()
            e.set_value("source", choice)
            got = self.effective(self.written(e))
            self.assertEqual(got["source"], choice)
            self.assertEqual(pf.Profile(got).identifies_with("musicbrainz"), choice != "discogs_only")
            self.assertEqual(pf.Profile(got).identifies_with("discogs"), choice != "musicbrainz_only")
            self.assertTrue(pf.Profile(got).provider_on("musicbrainz") or choice == "discogs_only")
        e = self.editor()
        e.set_value("source", "web_only")                  # (what Look Up's web mode sets, as a profile)
        got = self.effective(self.written(e))
        self.assertFalse(got["providers"]["musicbrainz"]["identify"])
        self.assertFalse(got["providers"]["discogs"]["identify"])
        self.assertTrue(got["providers"]["web"]["enabled"])
        self.assertTrue(e.controls["providers.web.enabled"].isChecked())
        self.assertFalse(e.rows["genres.discogs_styles"].control.isEnabled())     # (Discogs is not asked)

    def test_accept_with_store_pages(self):
        self.assertEqual(self.editor(providers={"web": {"partial": True}}).value("identification.accept"),
                         "partial_web")
        self.assertEqual(self.editor(identification={"accept": "exact"}, providers={"web": {"partial": True}})
                         .value("identification.accept"), "exact")        # (store pages count only with Partial)
        ed = self.editor(providers={"web": {"partial": True}})
        ed.set_value("identification.accept", "partial")
        got = self.written(ed, {"name": "x", "providers": {"web": {"partial": True}}})
        self.assertEqual((got["identification"]["accept"], got["providers"]["web"]["partial"]), ("partial", False))
        ed.set_value("identification.accept", "exact")
        got = self.written(ed, {"name": "x", "providers": {"web": {"partial": True}}})
        self.assertEqual((got["identification"]["accept"], got["providers"]["web"]["partial"]), ("exact", True))

    # ---- the other merges ------------------------------------------------------------------
    def test_operation_and_rename_in_place(self):
        for data, want in (({}, "in_place"), ({"rename_in_place": True}, "in_place_rename"),
                           ({"operation": "copy", "rename_in_place": True}, "copy")):
            self.assertEqual(self.editor(**data).value("operation"), want, data)
        for choice, op, rename in (("in_place", "in_place", False), ("in_place_rename", "in_place", True),
                                   ("copy", "copy", None), ("move", "move", None)):
            ed = self.editor(rename_in_place=True)
            ed.set_value("operation", choice)
            got = self.written(ed, {"name": "x", "rename_in_place": True})
            self.assertEqual(got.get("operation", "in_place"), op)
            if rename is not None:
                self.assertEqual(got["rename_in_place"], rename)

    def test_on_existing_and_the_second_copys_name(self):
        for data, want in (({}, "keep_number"), ({"naming": {"duplicate_suffix": "code"}}, "keep_code"),
                           ({"on_existing": "ask", "naming": {"duplicate_suffix": "code"}}, "ask"),
                           ({"on_existing": "skip"}, "skip")):
            self.assertEqual(self.editor(**data).value("on_existing"), want, data)
        ed = self.editor()
        ed.set_value("on_existing", "keep_code")
        got = self.written(ed)
        self.assertEqual((got["on_existing"], got["naming"]["duplicate_suffix"]), ("keep_both", "code"))
        ed.set_value("on_existing", "ask")                  # (the suffix is left as it was saved)
        got = self.written(ed, {"name": "x", "naming": {"duplicate_suffix": "code"}})
        self.assertEqual((got["on_existing"], got["naming"]["duplicate_suffix"]), ("ask", "code"))

    def test_skip_folders_and_paths_in_one_list(self):
        ed = self.editor(skip_folders=["a", "b/c"], exclude_paths=["*/x/*", "plain"])
        self.assertEqual(ed.value("_skip"), ["a", "b/c", "*/x/*", "plain"])
        ed.set_value("_skip", ["a", "b/c", "*/x/*", "plain", "new", "*/new/*"])
        got = self.written(ed, {"name": "x", "skip_folders": ["a", "b/c"], "exclude_paths": ["*/x/*", "plain"]})
        self.assertEqual(got["skip_folders"], ["a", "b/c", "new"])       # (each kept in the list it came from)
        self.assertEqual(got["exclude_paths"], ["*/x/*", "plain", "*/new/*"])

    def test_checklists(self):
        ed = self.editor(audiophile={"quality": False}, subfolders={"covers": False, "misc": True})
        self.assertEqual(ed.value("_audiophile"), ["replaygain", "acoustid", "rip_info"])
        self.assertEqual(ed.value("_subfolders"), ["misc"])
        ed.set_value("_audiophile", ["replaygain"])
        ed.set_value("_subfolders", ["covers", "misc"])
        got = self.written(ed)
        self.assertEqual((got["audiophile"], got["subfolders"]),
                         ({"acoustid": False, "rip_info": False}, {"covers": True}))

    def test_discogs_details_checklist_and_as_saved(self):
        ed = self.editor(discogs_details={"performers": True, "companies": True, "identifiers": False})
        self.assertEqual(ed.value("_discogs_details"), ["performers", "companies"])
        ed.set_value("_discogs_details", ["notes"])
        got = self.written(ed)
        self.assertEqual(got["discogs_details"], {"performers": False, "companies": False, "identifiers": False,
                                                  "marketplace": False, "notes": True})
        ed = self.editor(discogs_details={"companies": "tagger"})        # (the default's "audiophile" too)
        self.assertIsInstance(ed.value("_discogs_details"), self.pe.Saved)
        self.assertEqual(self.written(ed, {"name": "x"}), {"name": "x"})

    def test_credits_artist_photos_and_the_cover_size(self):
        for data, want in (({}, "auto"), ({"tagger": {"release_ars": True, "track_ars": False}}, "album"),
                           ({"tagger": {"release_ars": False, "track_ars": False}}, "off")):
            self.assertEqual(self.editor(**data).value("_credits"), want)
        self.assertIsInstance(self.editor(tagger={"release_ars": True}).value("_credits"), self.pe.Saved)
        ed = self.editor()
        ed.set_value("_credits", "tracks")
        self.assertEqual(self.written(ed)["tagger"], {"release_ars": False, "track_ars": True})
        ed = self.editor(tagger={"release_ars": True})                      # (as saved: not one of the choices)
        ed.set_value("_credits", "auto")
        self.assertEqual(self.written(ed, {"name": "x", "tagger": {"release_ars": True}}).get("tagger"), {})
        # artist photos: the custom file name is kept
        art = {"artist": {"enabled": True, "file": "folder.jpg", "embed": False}}
        ed = self.editor(artwork=art)
        self.assertEqual(ed.value("artwork.artist.enabled"), "file")
        ed.set_value("artwork.artist.enabled", "both")
        got = self.written(ed, {"name": "x", "artwork": art})
        self.assertEqual(got["artwork"]["artist"], {"enabled": True, "file": "folder.jpg", "embed": True})
        ed.set_value("artwork.artist.enabled", "embed")
        got = self.written(ed, {"name": "x", "artwork": art})
        self.assertEqual(got["artwork"]["artist"], {"enabled": True, "file": "", "embed": True})
        # the cover size: off, or the number of pixels
        self.assertEqual(self.editor().value("artwork.embed_front"), 1200)
        self.assertEqual(self.editor(artwork={"embed_front": False}).value("artwork.embed_front"), "off")
        self.assertIsInstance(self.editor(artwork={"embed_max_side": 2000}).value("artwork.embed_front"),
                              self.pe.Saved)
        ed = self.editor()
        ed.set_value("artwork.embed_front", 0)
        got = self.written(ed)
        self.assertEqual((got["artwork"]["embed_front"], got["artwork"]["embed_max_side"]), (True, 0))
        ed.set_value("artwork.embed_front", "off")
        self.assertEqual(self.written(ed)["artwork"], {"embed_front": False})

    # ---- rows that do nothing now ----------------------------------------------------------
    def test_rows_that_do_nothing_say_why(self):
        ed = self.editor(tag_level="basic", operation="copy", artwork={"enabled": False},
                         providers={"web": {"enabled": False}})
        for key in ("_credits", "_discogs_details", "versions.extra_tags", "artwork.artist.enabled"):
            self.assertIn("Basic", ed.rows[key].reason.text(), key)
        self.assertIn("copy leaves your originals", ed.rows["unresolved.action"].reason.text())
        self.assertIn("Covers are off", ed.rows["artwork.sources"].reason.text())
        for key in ("providers.web.shops", "providers.web.gnudb", "_engines"):
            self.assertIn("web fallback", ed.rows[key].reason.text(), key)
        ed = self.editor(source="musicbrainz_only")
        self.assertIn("Discogs is not asked", ed.rows["discogs_enrichment.merge_mode"].reason.text())
        ed = self.editor(source="discogs_only")
        self.assertIn("both databases", ed.rows["discogs_enrichment.merge_mode"].reason.text())
        ed = self.editor()                                  # (in place without renaming keeps the names)
        self.assertIn("renamed", ed.rows["naming.preset"].reason.text())
        ed.set_value("operation", "in_place_rename")
        self.assertEqual(ed.rows["naming.preset"].reason.text(), "")

    def test_a_copy_says_a_move_aside_holds_instead(self):
        ed = self.editor(operation="copy", quality_action="move", tag_level="audiophile")
        self.assertIn("copy leaves your originals", ed.rows["quality_action"].reason.text())

    def test_sources_note_for_a_folders_own_destination_in_place(self):
        ed = self.editor(sources=[{"path": "/srv/a", "destination": "/srv/lib"}])
        self.assertIn("not used when fixing in place", ed.rows["_sources"].reason.text())
        ed.set_value("operation", "copy")
        self.assertEqual(ed.rows["_sources"].reason.text(), "")

    def test_following_a_tagger_is_explained_when_it_is_off(self):
        ed = self.editor()
        self.assertIn("not used", ed.rows["tagger.write_id3v23"].reason.text())
        ed = self.editor(follow_tagger_settings=True)
        self.assertEqual(ed.rows["tagger.write_id3v23"].reason.text(), "")

    def test_rows_shown_only_when_they_differ(self):
        ed = self.editor()
        for key in ("grouping.join_deluxe_folders", "split_versions", "mark_resolved", "companions_rewrite_cues",
                    "remove_empty_dirs"):
            self.assertTrue(ed.rows[key].isHidden(), key)
        for key in ("_credits", "preserve_timestamps", "dates.date_tag"):   # (TagMender's own choices: always)
            self.assertFalse(ed.rows[key].isHidden(), key)
        ed = self.editor(mark_resolved=False, split_versions=False, tagger={"track_ars": False, "release_ars": False})
        for key in ("mark_resolved", "split_versions", "_credits"):
            self.assertFalse(ed.rows[key].isHidden(), key)
        # and a hidden row's value is still honoured: nothing is rewritten on a save without edits
        self.assertEqual(self.written(ed, {"name": "x", "mark_resolved": False}), {"name": "x", "mark_resolved": False})

    def test_the_tagger_rows_show_when_a_tagger_file_is_found(self):
        ini = Path(_tempfile.mkdtemp()) / "tagger.ini"
        ini.write_text("[setting]\n")
        old = _os.environ["TAGMENDER_TAGGER_INI"]
        _os.environ["TAGMENDER_TAGGER_INI"] = str(ini)
        try:
            ed = self.editor()
            self.assertFalse(ed.rows["_credits"].isHidden())
            self.assertFalse(ed.rows["preserve_timestamps"].isHidden())
        finally:
            _os.environ["TAGMENDER_TAGGER_INI"] = old

    def test_the_watching_rows_are_on_the_watched_folders_page(self):
        ed = self.editor()
        for key in ("unattended", "watch.settle_minutes", "watch.interval_seconds"):
            self.assertNotIn(key, ed.rows)
        for key in ("retry.after_days", "retry.max_attempts"):
            self.assertIn(key, ed.rows)

    # ---- presets and migration ---------------------------------------------------------------
    def test_presets_make_the_same_profiles_as_before(self):
        pe = self.pe
        want = {"typical": {"name": "P"},
                "collector": {"artwork": {"artist": {"embed": True, "enabled": True}, "enlarge": True,
                                          "replace": "larger_only", "when": "needed"},
                              "discogs_details": {"companies": True, "identifiers": True}, "keep_versions": True,
                              "mark_resolved": True, "name": "P", "naming": {"editions": "edition_year"},
                              "on_existing": "ask", "source": "discogs_preferred", "tag_level": "audiophile"},
                "keep": {"artwork": {"artist": {"enabled": False}, "enlarge": False, "replace": "never",
                                     "when": "needed"}, "cleanup": {"comments": "keep"},
                         "field_policy": {"default": "fill_missing"}, "name": "P", "operation": "in_place",
                         "rename_in_place": False,
                         "tagger": {"remove_ape_from_mp3": False, "remove_id3_from_flac": False},
                         "split_versions": False}}
        for key, data in want.items():
            self.assertEqual(pe.preset_data("P", key), data, key)

    def test_applying_a_preset_in_the_editor_means_what_the_preset_data_means(self):
        pe = self.pe
        for key in ("collector", "keep", "typical"):
            ed = self.editor()
            ed.apply_preset(key)
            got = self.effective(self.written(ed))
            want = self.effective(pe.preset_data("P", key))
            for k in ("source", "tag_level", "keep_versions", "mark_resolved", "split_versions", "on_existing",
                      "field_policy", "discogs_details", "cleanup"):
                self.assertEqual(got[k], want[k], (key, k))
            self.assertEqual(got["artwork"], want["artwork"], key)
            self.assertEqual(got["naming"], want["naming"], key)
            self.assertEqual(got["tagger"], want["tagger"], key)
            self.assertEqual(got["operation"], "in_place")

    def corpus(self):
        out = {}
        for i, (e, a) in enumerate(itertools.product((False, True), (False, True))):
            out[f"lyrics{i}"] = {"lyrics": {"enabled": e}, "audiophile": {"lyrics": a}, "tag_level": "audiophile"}
        out.update({
            "v2": {"version": 2},
            "v1": {},
            "web": {"providers": {"musicbrainz": {"identify": False}, "discogs": {"identify": False}}},
            "discogs_off": {"source": "discogs_preferred", "providers": {"discogs": {"enabled": False}}},
            "details": {"discogs_details": {"performers": "tagger", "identifiers": "audiophile", "notes": True}},
            "credits": {"tagger": {"release_ars": True, "va_name": "V.A."}},
            "artist": {"artwork": {"artist": {"enabled": True, "file": "", "embed": False}}},
            "cover": {"artwork": {"embed_front": True, "embed_max_side": 2000, "enabled": False}},
            "paths": {"skip_folders": ["a/b", "_*"], "exclude_paths": ["plain", "*/x/*"]},
            "rename": {"operation": "copy", "rename_in_place": True, "destination": "/tmp/lib",
                       "on_existing": "ask", "naming": {"duplicate_suffix": "code"}},
            "subfolders": {"subfolders": {"covers": True, "misc": True, "where": "disc"}},
            "partial": {"identification": {"accept": "exact"}, "providers": {"web": {"partial": True}}},
            "lossy": {"destinations": {"lossy": "/tmp/lossy"}, "operation": "copy", "destination": "/tmp/lib"},
            "watch": {"unattended": True, "watch": {"settle_minutes": 3, "interval_seconds": 90, "workers": 2}},
            "dead": {"tradition": "folk", "cleanup": {"clear_other_tags": True, "preserved_tags": ["x"]}}})
        return out

    def check_unchanged(self, name, raw):
        pe = self.pe
        raw = pf.rename_legacy(pf.older_behaviour(copy.deepcopy(dict(raw, name=name))))
        before = pf.minimal(pf.from_dict(copy.deepcopy(raw), check=False))
        ed = pe.ProfileEditor()
        ed.load(raw, pf.from_dict(copy.deepcopy(raw), check=False).data, pf.from_dict({"name": name}, check=False).data)
        self.assertEqual(ed.changed_keys(), [], name)
        after_raw = ed.write(copy.deepcopy(raw))
        self.assertEqual(after_raw, raw, name)
        after = pf.minimal(pf.from_dict(after_raw, check=False))
        self.assertEqual(json.dumps(after, sort_keys=True), json.dumps(before, sort_keys=True), name)
        self.assertEqual(pf.from_dict(after_raw, check=False).data, pf.from_dict(raw, check=False).data, name)

    def test_opening_and_saving_without_edits_changes_nothing(self):
        for name, raw in self.corpus().items():
            self.check_unchanged(name, raw)

    @unittest.skipUnless(REAL.is_dir(), "no profiles of this person to read")
    def test_real_profiles_open_and_save_unchanged(self):
        # (read-only: each file is read, never written)
        for path in sorted(REAL.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.check_unchanged(path.stem, raw)

    def test_a_merged_choice_written_then_read_again_is_that_choice(self):
        pe = self.pe
        for s in pe.SETTINGS:
            if not s.covers or s.custom == "check" and s.kind == "list" or s.kind != "popup":
                continue
            for value, _words in s.choices:
                ed = self.editor()
                ed.set_value(s.key, value)
                data = self.written(ed)
                got = s.value_of(self.effective(data))
                self.assertTrue(pe.same(got, value) or isinstance(got, pe.Saved), (s.key, value, got))


if __name__ == "__main__":
    unittest.main()
