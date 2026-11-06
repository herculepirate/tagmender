# 0.8.9: the plugin API's review - argument counts checked, a non-text result, a dotted requires,
# a processor failing part-way through an album, check() of a plugin in several files, a damaged
# zip, Python 3.10's tomli, profiles commands knowing plugin functions, ids already taken, a
# quoted name, updates keeping the switch, set-aside twice a second, retry and secrets forgotten.
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugins import EXAMPLE, HERE, Base, run, write_plugin  # noqa: E402  (isolated XDG dirs)
import naming  # noqa: E402
import plugins  # noqa: E402


def on(pid):
    plugins.set_enabled(pid, True)
    plugins.load_enabled()


class Api(Base):
    def test_a_wrong_number_of_arguments_is_the_scripts_error_not_the_plugins(self):
        plugins.install(EXAMPLE)
        on("roman-numerals")
        self.assertIn("Wrong number of arguments", " ".join(naming.check("$roman()/%title%")))
        self.assertEqual(plugins.status("roman-numerals"), "on")
        self.assertEqual(naming.render("$roman(%discnumber%)", {"discnumber": ["3"]}), ["III"])

    def test_a_function_that_gives_no_text_is_switched_off_and_the_script_gets_nothing(self):
        write_plugin(plugins.plugins_dir(), "counts", body=(
            "def register(api):\n    api.naming_function('mynum', lambda p, t: len(t))\n"))
        on("counts")
        self.assertEqual(naming.render("x$mynum(abc)", {}), ["x"])
        self.assertIn("not text", plugins.status("counts"))

    def test_a_dotted_requires_is_a_missing_module_and_the_others_load(self):
        write_plugin(plugins.plugins_dir(), "aaa-dotted", requires=["nonexistent_pkg_xyz.sub"])
        plugins.install(EXAMPLE)
        for pid in ("aaa-dotted", "roman-numerals"):
            plugins.set_enabled(pid, True)
        plugins.load_enabled()
        self.assertIn("nonexistent_pkg_xyz.sub", plugins.status("aaa-dotted"))
        self.assertEqual(plugins.status("roman-numerals"), "on")
        self.assertEqual(plugins.check(plugins.plugins_dir() / "aaa-dotted"),
                         ["needs the Python module(s) nonexistent_pkg_xyz.sub"])

    def test_a_processor_failing_part_way_leaves_no_album_half_changed(self):
        sys.path.insert(0, str(HERE / "tests"))
        import plan as planmod
        import profiles as pf
        from test_settings_read import album, prop_for
        write_plugin(plugins.plugins_dir(), "second-breaks", body=(
            "seen = []\n"
            "def register(api):\n"
            "    def up(tags, info):\n"
            "        seen.append(1)\n"
            "        if len(seen) == 2:\n"
            "            raise RuntimeError('track two')\n"
            "        tags['title'] = [t.upper() for t in tags.get('title', [])]\n"
            "        return tags\n"
            "    api.tag_processor(up)\n"))
        on("second-breaks")
        files = album(self.tmp / "lib" / "Band A" / "Album A", 3)
        prof = pf.from_dict({"name": "x", "operation": "in_place", "artwork": {"enabled": False},
                             "follow_tagger_settings": False})
        p = planmod.build([prop_for(files)], prof)
        a = p["albums"][0]
        titles = [f["changes"].get("title") for f in a["files"]]
        self.assertEqual(titles, [None, None, None])                    # (none upper-cased: all as without it)
        self.assertTrue(any("second-breaks" in n and "switched off" in n for n in a["notes"]), a["notes"])

    def test_a_naming_function_failing_part_way_blocks_the_album_not_mixes_its_names(self):
        sys.path.insert(0, str(HERE / "tests"))
        import plan as planmod
        import profiles as pf
        from test_settings_read import album, prop_for
        write_plugin(plugins.plugins_dir(), "names-break", body=(
            "seen = []\n"
            "def register(api):\n"
            "    def boom(parser, text):\n"
            "        seen.append(1)\n"
            "        if len(seen) == 2:\n"
            "            raise RuntimeError('second name')\n"
            "        return text.upper()\n"
            "    api.naming_function('names_boom', boom)\n"))
        on("names-break")
        files = album(self.tmp / "in" / "Band A" / "Album A", 3)
        lib = self.tmp / "lib"
        lib.mkdir()
        prof = pf.from_dict({"name": "x", "operation": "copy", "destination": str(lib),
                             "artwork": {"enabled": False}, "follow_tagger_settings": False,
                             "naming": {"template": "%albumartist%/%album%/$names_boom(%title%)"}})
        a = planmod.build([prop_for(files)], prof)["albums"][0]
        self.assertEqual(a["status"], "blocked")
        self.assertTrue(any("names-break" in c for c in a["conflicts"]), a["conflicts"])

    def test_a_plugin_failing_on_another_thread_mid_album_is_caught_too(self):
        sys.path.insert(0, str(HERE / "tests"))
        import plan as planmod
        import profiles as pf
        from test_settings_read import album, prop_for
        write_plugin(plugins.plugins_dir(), "b-upper", body=(
            "def register(api):\n"
            "    def up(tags, info):\n"
            "        tags['title'] = [t.upper() for t in tags.get('title', [])]\n"
            "        return tags\n"
            "    api.tag_processor(up)\n"))
        write_plugin(plugins.plugins_dir(), "a-trigger", body=(
            "import threading\n"
            "seen = []\n"
            "def register(api):\n"
            "    def tick(tags, info):\n"
            "        seen.append(1)\n"
            "        if len(seen) == 2:                      # (another thread's album: b-upper fails there)\n"
            "            import plugins\n"
            "            t = threading.Thread(target=plugins._fail, args=(plugins._LOADED['b-upper'], 'elsewhere'))\n"
            "            t.start(); t.join()\n"
            "        return tags\n"
            "    api.tag_processor(tick)\n"))
        for pid in ("a-trigger", "b-upper"):
            plugins.set_enabled(pid, True)
        plugins.load_enabled()
        files = album(self.tmp / "lib" / "Band A" / "Album A", 3)
        prof = pf.from_dict({"name": "x", "operation": "in_place", "artwork": {"enabled": False},
                             "follow_tagger_settings": False})
        a = planmod.build([prop_for(files)], prof)["albums"][0]
        self.assertEqual([f["changes"].get("title") for f in a["files"]], [None, None, None])
        self.assertTrue(any("b-upper" in n for n in a["notes"]), a["notes"])

    def test_check_takes_a_plugin_in_several_files(self):
        d = write_plugin(self.tmp, "two-files", body="from . import helper\n\ndef register(api):\n    helper.x()\n")
        (d / "helper.py").write_text("def x():\n    return 1\n")
        self.assertEqual(plugins.check(d), [])

    def test_check_refuses_a_built_in_id(self):
        d = write_plugin(self.tmp, "discogs")
        self.assertIn("comes with TagMender", plugins.check(d)[0])

    def test_a_damaged_zip_is_said_not_a_crash(self):
        z = self.tmp / "bad.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("crcp/plugin.toml", 'id = "crcp"\n' * 40)
            zf.writestr("crcp/__init__.py", "def register(api):\n    pass\n" * 40)
        raw = bytearray(z.read_bytes())
        raw[60] ^= 0xFF                                                    # (one byte of the data changed)
        z.write_bytes(bytes(raw))
        with self.assertRaises(plugins.PluginError):
            plugins.install(z)
        self.assertFalse(list(plugins.plugins_dir().glob(".unpack-*")))

    def test_python_3_10_says_what_to_install_and_keeps_the_switch(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        with mock.patch.object(plugins, "tomllib", None):
            row = next(p for p in plugins.installed() if p.id == "roman-numerals")
        self.assertTrue(row.enabled)                                       # (on, and why it cannot load said)
        self.assertIn("tomli", row.error)

    def test_profiles_commands_know_a_plugins_functions(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        code, _out, err = run("profiles", "new", "rn")
        self.assertEqual(code, 0, err)
        code, _out, err = run("profiles", "set", "rn",
                              "naming.template=$roman(%discnumber%)/%title%")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Unknown function", err)

    def test_no_plugins_and_profile_in_either_order(self):
        code, _out, err = run("--profile", "nothere", "--no-plugins", "plugins", "list")
        self.assertEqual(code, 0, err)

    def test_the_scaffold_names_its_function_after_the_plugin_and_quotes_its_name(self):
        a, b = plugins.new('Say "hi"', self.tmp), plugins.new("Other One", self.tmp)
        self.assertEqual(plugins.check(a), [])
        plugins.install(a)
        plugins.install(b)
        for pid in (a.name, b.name):
            plugins.set_enabled(pid, True)
        plugins.load_enabled()
        self.assertEqual(plugins.status(a.name), "on")
        self.assertEqual(plugins.status(b.name), "on")                    # (no shared $shout)

    def test_an_update_keeps_the_switch_and_two_set_asides_a_second_both_kept(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        self.assertTrue(plugins.install(EXAMPLE).enabled)
        plugins.install(EXAMPLE)
        kept = sorted(p.name for p in (plugins.plugins_dir() / ".removed").iterdir())
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(p.startswith("roman-numerals-") for p in kept))
        self.assertEqual(plugins.status("roman-numerals"), "on")

    def test_a_failed_plugin_is_tried_again_and_a_secret_forgotten(self):
        d = write_plugin(plugins.plugins_dir(), "flaky", body="raise RuntimeError('first')\n")
        (d / "__pycache__").mkdir(exist_ok=True)                           # (a cached copy: forget clears it)
        on("flaky")
        self.assertIn("first", plugins.status("flaky"))
        (d / "__init__.py").write_text("def register(api):\n    pass\n")
        plugins.forget("flaky")
        plugins.load_enabled()
        self.assertEqual(plugins.status("flaky"), "on")
        plugins.store_secret("flaky", "token", "x")
        self.assertTrue(plugins.forget_secret("flaky", "token"))
        self.assertFalse(plugins.forget_secret("flaky", "token"))

    def test_installing_the_installed_folder_itself_is_refused_not_lost(self):
        plugins.install(EXAMPLE)
        plugins.set_enabled("roman-numerals", True)
        with self.assertRaises(plugins.PluginError):
            plugins.install(plugins.plugins_dir() / "roman-numerals")
        self.assertTrue((plugins.plugins_dir() / "roman-numerals" / "plugin.toml").exists())

    def test_a_switch_default_must_be_true_or_false(self):
        p = plugins.check_manifest({"id": "ab", "name": "a", "version": "1", "api": 1, "description": "d",
                                    "settings": [{"key": "on", "label": "On", "kind": "switch",
                                                  "default": "false"}]})
        self.assertTrue(any("true or false" in x for x in p))


if __name__ == "__main__":
    unittest.main()
