# Tests for plugins.py and `tagmender plugins`: install from a folder or a zip, switched off
# until enabled, loading, script functions, tag processors, settings, a failing plugin switched
# off, remove kept aside, and the author's new/check.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["XDG_DATA_HOME"] = _tempfile.mkdtemp(prefix="tm-data-")
_os.environ.pop("TAGMENDER_NO_PLUGINS", None)

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import app_settings  # noqa: E402
import naming  # noqa: E402
import plugins  # noqa: E402
import tagmender  # noqa: E402

EXAMPLE = HERE / "contrib" / "plugin-example" / "roman-numerals"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tagmender.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def write_plugin(where: Path, pid="bad-one", body="def register(api):\n    pass\n", **toml):
    d = where / pid
    d.mkdir(parents=True)
    fields = dict({"id": pid, "name": pid, "version": "1", "api": 1, "description": "test"}, **toml)
    (d / "plugin.toml").write_text("\n".join(f"{k} = {v!r}" if isinstance(v, str) else f"{k} = {v}"
                                             for k, v in fields.items()).replace("'", '"') + "\n")
    (d / "__init__.py").write_text(body)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-plug-"))
        shutil.rmtree(plugins.plugins_dir(), ignore_errors=True)
        app_settings.save(dict(app_settings.DEFAULTS))
        plugins._LOADED.clear()
        plugins._PROCESSORS.clear()


class Install(Base):
    def test_installed_from_a_zip_and_switched_off_until_enabled(self):
        z = self.tmp / "roman.zip"
        with zipfile.ZipFile(z, "w") as out:
            for f in EXAMPLE.iterdir():
                out.write(f, f"roman-numerals/{f.name}")
        p = plugins.install(z)
        self.assertEqual((p.id, plugins.status("roman-numerals")), ("roman-numerals", "off"))
        self.assertTrue((plugins.plugins_dir() / "roman-numerals" / "plugin.toml").is_file())
        self.assertEqual(plugins.load_enabled(), [])                    # (off: never run)
        plugins.set_enabled("roman-numerals", True)
        self.assertEqual([x.id for x in plugins.load_enabled()], ["roman-numerals"])
        self.assertEqual(plugins.status("roman-numerals"), "on")

    def test_a_zip_with_an_unsafe_path_is_refused(self):
        z = self.tmp / "evil.zip"
        with zipfile.ZipFile(z, "w") as out:
            out.writestr("../outside.py", "x")
            out.writestr("p/plugin.toml", "")
        with self.assertRaises(plugins.PluginError):
            plugins.install(z)
        self.assertFalse((plugins.plugins_dir().parent / "outside.py").exists())

    def test_a_wrong_manifest_says_what_is_wrong(self):
        d = write_plugin(self.tmp, "Bad Id", api=9)
        with self.assertRaises(plugins.PluginError) as e:
            plugins.install(d)
        self.assertIn("id must be", str(e.exception))
        self.assertIn("api must be 1", str(e.exception))

    def test_remove_keeps_it_aside(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        kept = plugins.remove("roman-numerals")
        self.assertEqual([p for p in plugins.installed() if not p.builtin], [])
        self.assertNotIn("roman-numerals", plugins.enabled_ids())
        self.assertEqual(kept.parent.name, ".removed")


class Running(Base):
    def test_a_script_function_and_a_tag_processor(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        plugins.load_enabled()
        self.assertIn("roman", naming.functions())
        self.assertEqual(naming.render("Disc $roman(%discnumber%)/%title%", {"discnumber": ["4"], "title": ["T"]}),
                         ["Disc IV", "T"])
        tags = {"discnumber": ["2"], "totaldiscs": ["3"], "title": ["x"]}
        self.assertEqual(plugins.process_tags(tags, {}), tags)          # (its setting is off)
        app_settings.set_value("plugins.settings", {"roman-numerals": {"disc_subtitle": True}})
        self.assertEqual(plugins.process_tags(tags, {})["discsubtitle"], ["Disc II"])

    def test_a_plugin_that_raises_is_switched_off_and_the_run_carries_on(self):
        write_plugin(plugins.plugins_dir(), "breaks", body=(
            "def register(api):\n"
            "    def boom(tags, info):\n"
            "        raise RuntimeError('oops')\n"
            "    api.tag_processor(boom)\n"))
        plugins.set_enabled("breaks", True)
        plugins.load_enabled()
        tags = {"title": ["x"]}
        self.assertEqual(plugins.process_tags(tags, {}), tags)
        self.assertEqual(plugins.status("breaks"), "error: RuntimeError: oops")
        self.assertEqual(plugins._PROCESSORS, [])

    def test_a_plugin_that_fails_to_load_and_a_name_taken(self):
        write_plugin(plugins.plugins_dir(), "takes-if", body=(
            "def register(api):\n    api.naming_function('if', lambda p, x: x)\n"))
        write_plugin(plugins.plugins_dir(), "needs-more", requires=["no_such_module_xyz"])
        for pid in ("takes-if", "needs-more"):
            plugins.set_enabled(pid, True)
        plugins.load_enabled()
        self.assertIn("already has", plugins.status("takes-if"))
        self.assertIn("no_such_module_xyz", plugins.status("needs-more"))

    def test_none_load_when_switched_off_for_the_run(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        _os.environ["TAGMENDER_NO_PLUGINS"] = "1"
        try:
            self.assertEqual(plugins.load_enabled(), [])
        finally:
            del _os.environ["TAGMENDER_NO_PLUGINS"]

    def test_a_secret_setting_is_a_file_only_you_can_read(self):
        f = plugins.store_secret("some-plugin", "token", "s3cret")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(plugins.setting_value("some-plugin", {"key": "token", "kind": "secret"}), "s3cret")
        f.chmod(0o644)
        with self.assertRaises(plugins.PluginError):
            plugins.get_secret("some-plugin", "token")


class InThePlan(Base):
    def test_a_tag_change_is_planned_like_any_other(self):
        sys.path.insert(0, str(HERE / "tests"))
        import plan as planmod
        import profiles as pf
        from test_settings_read import album, prop_for
        write_plugin(plugins.plugins_dir(), "upper-titles", body=(
            "def register(api):\n"
            "    def up(tags, info):\n"
            "        tags['title'] = [t.upper() for t in tags.get('title', [])]\n"
            "        return tags\n"
            "    api.tag_processor(up)\n"))
        plugins.set_enabled("upper-titles", True)
        plugins.load_enabled()
        files = album(self.tmp / "lib" / "Band A" / "Album A", 1)
        prof = pf.from_dict({"name": "x", "operation": "in_place", "artwork": {"enabled": False},
                             "follow_tagger_settings": False})
        p = planmod.build([prop_for(files)], prof)
        changes = p["albums"][0]["files"][0]["changes"]
        self.assertEqual(changes["title"], ["SONG NUMBER 0"], changes)


class ForAuthors(Base):
    def test_new_makes_a_plugin_that_checks_clean_and_installs(self):
        d = plugins.new("My Plugin", self.tmp)
        self.assertEqual(d.name, "my-plugin")
        self.assertEqual(plugins.check(d), [])
        self.assertEqual(plugins.install(d).id, "my-plugin")

    def test_the_command_line(self):
        code, out, _ = run("plugins", "install", str(EXAMPLE))
        self.assertEqual(code, 0, out)
        code, out, _ = run("plugins", "enable", "roman-numerals")
        self.assertEqual(code, 0, out)
        code, out, _ = run("plugins", "list")
        self.assertIn("roman-numerals", out)
        self.assertIn("on", out)
        code, out, _ = run("plugins", "check", str(EXAMPLE))
        self.assertEqual(code, 0, out)
        bad = write_plugin(self.tmp, "nope", body="x = 1\n")
        code, out, _ = run("plugins", "check", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("register", out)
        code, out, _ = run("plugins", "remove", "roman-numerals")
        self.assertEqual(code, 0, out)



class PreferencesPane(Base):
    def test_switch_on_off_a_setting_and_remove_from_preferences(self):
        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from gui import sheets
        from gui.preferences import PluginsPane
        plugins.install(EXAMPLE)
        pane = PluginsPane()
        pane.switch("roman-numerals", True)
        self.assertEqual(plugins.status("roman-numerals"), "on")
        self.assertIn("roman", naming.functions())                      # (on at once, no restart)
        pane.set_value("roman-numerals", "disc_subtitle", True)
        self.assertTrue(plugins.setting_value("roman-numerals", {"key": "disc_subtitle", "kind": "switch"}))
        pane.switch("roman-numerals", False)
        self.assertEqual(plugins.status("roman-numerals"), "off")
        with mock.patch.object(sheets, "confirm", return_value=True):
            pane.remove([p for p in plugins.installed() if not p.builtin][0])
        self.assertEqual([p for p in plugins.installed() if not p.builtin], [])


if __name__ == "__main__":
    unittest.main()
