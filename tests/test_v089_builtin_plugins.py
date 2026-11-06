# 0.8.9: Discogs and CoverUp come with TagMender as built-in plugins (plugins_builtin/), listed in
# Preferences > Plugins, on unless switched off; no outside copy of their code is needed.
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="tm-data-")     # (never this computer's plugins)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp  # noqa: E402  (isolated settings and state)
import app_settings  # noqa: E402
import companions  # noqa: E402
import plugins  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class Builtin(Base):
    def setUp(self):
        super().setUp()
        self.env = {k: os.environ.pop(k, None) for k in ("TAGMENDER_DISCOGS_PLUGIN", "TAGMENDER_COVERUP_DIR")}
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")        # (each test its own plugins folder)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_the_code_comes_with_tagmender(self):
        self.assertEqual(companions.discogs_dir(), REPO / "plugins_builtin" / "discogs")
        self.assertEqual(companions.coverup_dir(), REPO / "plugins_builtin" / "coverup")
        self.assertTrue((companions.discogs_dir() / "discogs_mapping.py").exists())
        self.assertTrue((companions.coverup_dir() / "pipeline.py").exists())
        os.environ["TAGMENDER_DISCOGS_PLUGIN"] = str(self.tmp / "nowhere")     # (not a copy: the built-in one)
        self.assertEqual(companions.discogs_dir(), REPO / "plugins_builtin" / "discogs")

    def test_the_built_in_code_loads(self):
        import coverup_loader
        import match_albums as ma
        self.assertTrue(hasattr(ma.load_plugin(str(companions.discogs_dir())), "extract_credits"))
        coverup_loader.load_coverup(str(companions.coverup_dir()))
        import coverup.pipeline  # noqa: F401
        from coverup.creative import generate  # noqa: F401   (the Qt compositor: a stand-in)
        import coverup.imaging
        self.assertTrue(coverup.imaging.__file__.endswith("coverup_pil_imaging.py"))

    def test_listed_on_and_switched_off_not_removed(self):
        rows = plugins.installed()
        self.assertEqual([p.id for p in rows[:2]], ["discogs", "coverup"])
        self.assertTrue(all(p.builtin and p.enabled and not p.error for p in rows[:2]))
        plugins.set_enabled("coverup", False)
        self.assertEqual(app_settings.get("plugins.builtin_off"), ["coverup"])
        self.assertFalse(companions.coverup_on())
        self.assertTrue(companions.discogs_on())
        self.assertEqual(plugins.status("coverup"), "off")
        plugins.set_enabled("coverup", True)
        self.assertEqual(app_settings.get("plugins.builtin_off"), [])
        with self.assertRaises(plugins.PluginError):
            plugins.remove("discogs")
        self.assertEqual(plugins.load_enabled(), [])                 # (not run as an API plugin)

    def test_an_installed_plugin_cannot_take_a_built_in_id(self):
        d = plugins.plugins_dir() / "discogs"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text('id = "discogs"\nname = "X"\nversion = "1"\napi = 1\ndescription = "x"\n')
        (d / "__init__.py").write_text("def register(api):\n    pass\n")
        mine = [p for p in plugins.installed() if not p.builtin]
        self.assertEqual(len(mine), 1)
        if plugins.tomllib is not None:
            self.assertIn("comes with TagMender", mine[0].error)
            with self.assertRaises(plugins.PluginError):
                plugins.install(d)

    def test_discogs_off_identifies_with_musicbrainz_only_and_no_album_error(self):
        self.assertTrue(self.engine(FakeHttp()).use_discogs)
        plugins.set_enabled("discogs", False)
        eng = self.engine(FakeHttp())
        self.assertFalse(eng.use_discogs)
        self.assertNotIn("discogs", eng.notes)

    def test_coverup_off_asks_no_coverup_source(self):
        import artwork
        import profiles as pf
        prof = pf.from_dict({"name": "default", "artwork": {"enabled": True,
                                                            "sources": ["coverup", "web", "local", "discogs"]}})
        plugins.set_enabled("coverup", False)
        plugins.set_enabled("discogs", False)
        self.assertEqual(artwork.chain(prof, self.tmp / "staging"), [])
        self.assertEqual(artwork.embed_cap(prof),
                         artwork.EMBED_MAX_SIDE)

    def test_the_doctor_says_switched_off(self):
        import doctor
        plugins.set_enabled("coverup", False)
        got = {c.name: c for c in doctor.checks()}
        self.assertEqual(got["CoverUp plugin"].status, "warn")
        self.assertEqual(got["Discogs plugin"].status, "ok")

    def test_discogs_off_keeps_coverup_and_the_band_lookup_from_discogs(self):
        import artwork
        import engine
        plugins.set_enabled("discogs", False)
        cu = artwork.CoverUp(self.tmp / "staging")
        self.assertNotIn("discogs", cu.s["providers_enabled"] or [])
        eng = self.engine(FakeHttp())
        with unittest.mock.patch.object(engine.album_artist, "discogs_members") as asked:
            self.assertEqual(eng._discogs_band_members("123"), [])
        self.assertFalse(asked.called)

    def test_coverup_off_looks_for_no_artist_photo(self):
        plugins.set_enabled("coverup", False)
        self.assertFalse(companions.coverup_on())
        src = (REPO / "engine.py").read_text()                       # (the artist photo search asks first)
        self.assertIn("if not known and companions.coverup_on():", src)

    def test_an_installed_plugin_with_a_built_in_id_can_be_removed(self):
        d = plugins.plugins_dir() / "coverup"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text('id = "coverup"\nname = "X"\nversion = "1"\napi = 1\ndescription = "x"\n')
        (d / "__init__.py").write_text("def register(api):\n    pass\n")
        plugins.remove("coverup")
        self.assertFalse(d.exists())
        self.assertTrue(companions.coverup_on())                      # (the built-in one stays on)

    def test_a_built_in_plugin_whose_code_is_missing_says_so(self):
        os.environ["TAGMENDER_COVERUP_DIR"] = str(self.tmp)            # (no pipeline.py: the built-in one)
        with unittest.mock.patch.object(companions, "COVERUP", ("TAGMENDER_COVERUP_DIR", self.tmp / "gone",
                                                                "pipeline.py")):
            row = next(p for p in plugins.installed() if p.id == "coverup")
        self.assertIn("missing", row.error)
        self.assertFalse(row.loaded)

    def test_the_discogs_tools_stop_and_the_others_leave_discogs_out(self):
        import enrich
        import inbox
        import match_albums as ma
        plugins.set_enabled("discogs", False)
        with self.assertRaises(SystemExit) as stop:
            inbox.main(["--inbox", str(self.tmp), "--library", str(self.tmp), "--out-dir", str(self.tmp / "o")])
        self.assertIn("switched off", str(stop.exception))
        with unittest.mock.patch.object(sys, "argv", ["match_albums", str(self.tmp / "a.csv"),
                                                      "--out-dir", str(self.tmp / "m")]):
            with self.assertRaises(SystemExit) as stop:
                ma.main()
        self.assertIn("switched off", str(stop.exception))
        asked = []

        class Http:
            def get_json(self, url, kind):
                asked.append(url)
                return {}
        with unittest.mock.patch.object(enrich, "get", side_effect=lambda f, k: "123" if "DISCOGS" in k else ""):
            enrich.genres_for(Http(), [str(self.tmp / "x.flac")]) if hasattr(enrich, "genres_for") else None
        self.assertFalse([u for u in asked if "discogs" in u])

    def test_the_web_tool_leaves_discogs_matches_out_when_off(self):
        import sqlite3
        import web_tag
        db = self.tmp / "m.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE results (path TEXT, result TEXT)")
        c.execute("INSERT INTO results VALUES (?, ?)", (str(self.tmp), json.dumps(
            {"path": str(self.tmp), "status": "auto", "best": {"source": "discogs", "id": "1"}})))
        c.commit()
        c.close()
        plugins.set_enabled("discogs", False)
        with unittest.mock.patch.object(web_tag, "unresolved", wraps=web_tag.unresolved) as un:
            web_tag.main(["--matches-db", str(db), "--out-dir", str(self.tmp / "w")])
        self.assertFalse(un.call_args.args[3])                    # (include_discogs: off)
        self.assertEqual(web_tag.unresolved(db, include_discogs=False), [])

    def test_every_built_in_file_is_tracked_by_git(self):
        try:
            out = subprocess.run(["git", "-C", str(REPO), "check-ignore", "--no-index", "--stdin"],
                                 input="\n".join(str(p.relative_to(REPO)) for p in
                                                 (REPO / "plugins_builtin").rglob("*.py")),
                                 capture_output=True, text=True)
        except OSError:
            self.skipTest("git not installed")
        self.assertEqual(out.stdout.strip(), "")                      # (nothing in it git-ignored)


try:
    from PySide6.QtWidgets import QApplication, QPushButton
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Pane(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_built_in_rows_switch_and_have_no_remove(self):
        from gui import preferences
        pane = preferences.PluginsPane()
        groups = [pane.holder.itemAt(i).widget() for i in range(pane.holder.count())]
        built_in = groups[:2]
        for g in built_in:
            self.assertFalse([b for b in g.findChildren(QPushButton) if b.text().startswith("Remove")])
        pane.switch("discogs", False)
        self.assertFalse(companions.discogs_on())
        self.assertIn("next look-up", pane.said.text())

    def test_an_installed_plugin_using_a_built_in_id_has_no_live_switch(self):
        from gui import preferences
        from gui.rows import Switch
        d = plugins.plugins_dir() / "discogs"
        d.mkdir(parents=True)
        (d / "plugin.toml").write_text('id = "discogs"\nname = "X"\nversion = "1"\napi = 1\ndescription = "x"\n')
        (d / "__init__.py").write_text("def register(api):\n    pass\n")
        app_settings.set_value("plugins.enabled", ["discogs"])           # (from before 0.8.9)
        pane = preferences.PluginsPane()
        groups = [pane.holder.itemAt(i).widget() for i in range(pane.holder.count())]
        mine = groups[2]                                                   # (after the two built-in ones)
        self.assertFalse(any(sw.isEnabled() for sw in mine.findChildren(Switch)))
        self.assertTrue(companions.discogs_on())

    def test_the_inspector_can_be_dragged_wide(self):
        import gui
        win = gui.Main()
        try:
            self.assertGreaterEqual(gui.style.INSPECTOR[2], 900)
            self.assertGreaterEqual(win.inspector.maximumWidth(), 900)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
