# 0.8.6: settings shared by all profiles (profiles.SHARED_SETTINGS in shared_settings.json); a
# profile keeps its own value only of those it says (own_settings). Isolated settings folders.
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)
import profiles as pf  # noqa: E402


def save(name, **data):
    return pf.save(pf.from_dict(dict({"name": name, "version": pf.SCHEMA_VERSION}, **data)))


class Shared(Base):
    def test_without_a_shared_file_nothing_changes(self):
        save("A", compilations={"va_name": "VA"})
        save("B")
        self.assertEqual(pf.load("A").data["compilations"]["va_name"], "VA")
        self.assertNotIn("va_name", pf.load("B").data["compilations"])          # (Automatic)
        self.assertFalse(pf.shared_file().exists())

    def test_adopted_from_what_every_profile_agrees_on(self):
        save("A", compilations={"va_name": "VA"}, artwork={"min_px": 300})
        save("B", compilations={"va_name": "VA"}, artwork={"min_px": 600})
        self.assertTrue(pf.adopt_shared())
        self.assertFalse(pf.adopt_shared())                          # (once)
        self.assertEqual(pf.shared_values(), {"compilations.va_name": "VA"})
        a = json.loads(pf.path_of("A").read_text())
        self.assertNotIn("va_name", a.get("compilations", {}))       # (follows the shared one now)
        self.assertEqual(a["own_settings"], ["artwork.min_px"])      # (differing: kept its own)
        self.assertEqual(pf.load("A").data["artwork"]["min_px"], 300)
        self.assertEqual(pf.load("B").data["artwork"]["min_px"], 600)
        # a shared value changed later reaches both; their own sizes stay
        pf.set_shared({"compilations.va_name": "Various", "artwork.min_px": 1000})
        for n, px in (("A", 300), ("B", 600)):
            d = pf.load(n).data
            self.assertEqual(d["compilations"]["va_name"], "Various")
            self.assertEqual(d["artwork"]["min_px"], px)
        save("C")                                                     # (a new profile: the shared values)
        self.assertEqual(pf.load("C").data["artwork"]["min_px"], 1000)
        self.assertEqual(pf.default().data["compilations"]["va_name"], "Various")

    def test_only_this_profile_and_back(self):
        save("A")
        save("B")
        pf.adopt_shared()
        pf.set_shared({"genres.max": 2})
        d = pf.raw("A")
        d["own_settings"] = ["genres.max"]
        pf._put(d, "genres.max", 5)
        pf.save(pf.from_dict(d))
        self.assertEqual(pf.load("A").data["genres"]["max"], 5)
        self.assertEqual(pf.load("B").data["genres"]["max"], 2)
        d = pf.raw("A")                                              # (back to All Profiles)
        d.pop("own_settings")
        pf._drop(d, "genres.max")
        pf.save(pf.from_dict(d))
        pf.set_shared({"genres.max": 3})
        self.assertEqual(pf.load("A").data["genres"]["max"], 3)

    def test_a_saved_value_unlike_the_shared_one_is_kept_not_lost(self):
        save("A")
        save("B")
        pf.adopt_shared()
        pf.set_shared({"genres.max": 2})
        d = pf.raw("A")
        pf._put(d, "genres.max", 7)                                   # (the CLI, a script)
        pf.save(pf.from_dict(d))
        self.assertEqual(pf.load("A").data["genres"]["max"], 7)
        self.assertIn("genres.max", json.loads(pf.path_of("A").read_text())["own_settings"])

    def test_a_plans_whole_profile_never_drifts(self):
        save("A", artwork={"min_px": 300})
        save("B")
        pf.adopt_shared()
        pf.set_shared({"genres.max": 7})
        plan_data = pf.load("A").to_dict()
        pf.set_shared({"genres.max": 2})
        self.assertEqual(pf.from_dict(plan_data).data["genres"]["max"], 7)
        self.assertEqual(pf.load("A").data["genres"]["max"], 2)

    def test_old_picard_names_and_version_2_files_keep_what_they_did(self):
        pf.profiles_dir().mkdir(parents=True, exist_ok=True)
        pf.path_of("Old").write_text(json.dumps({"version": 3, "name": "Old", "picard": {"write_id3v23": False},
                                                 "artwork": {"min_px": 123}}))
        pf.path_of("V2").write_text(json.dumps({"version": 2, "name": "V2", "artwork": {"min_px": 123}}))
        before = {n: pf.load(n).data for n in ("Old", "V2")}
        self.assertIs(before["Old"]["tagger"]["write_id3v23"], False)
        pf.adopt_shared()
        pf.set_shared({"genres.max": 2})
        for n in ("Old", "V2"):
            after = pf.load(n).data
            for p in pf.SHARED_SETTINGS:
                if p != "genres.max" or pf._get(before[n], p) != pf._get(pf._base_defaults(), p):
                    self.assertEqual(pf._get(after, p), pf._get(before[n], p), (n, p))
            self.assertTrue(pf.path_of(n).with_name(f"{n}.json.bak-shared").exists())

    def test_the_cli_sets_a_shared_key_of_one_profile(self):
        save("A", artwork={"min_px": 300})
        save("B")
        pf.adopt_shared()
        d = pf.raw("A")
        pf._put(d, "genres.max", 9)
        pf.save(pf.from_dict(d))
        self.assertEqual(pf.load("A").data["genres"]["max"], 9)

    def test_one_switch_of_a_group_is_shared_whole(self):
        save("A")
        save("B")
        pf.adopt_shared()
        pf.set_shared({"loudness": {"album_gain": False}})
        self.assertEqual(set(pf.shared_values()["loudness"]), set(pf.DEFAULTS["loudness"]))
        pf.save(pf.load("B"))
        self.assertEqual(json.loads(pf.path_of("B").read_text()).get("own_settings"), [])   # (marked, none own)

    def test_only_shared_paths(self):
        with self.assertRaises(pf.ProfileError):
            pf.set_shared({"destination": "/x"})
        self.assertTrue(pf.is_shared("album_artist.lead"))
        self.assertFalse(pf.is_shared("operation"))


class SharedReview(Base):
    """0.8.6 review: stamping keeps what a file leaves to the tagger's settings; a file put back
    unmarked works as it did; a shared change another profile cannot load with is found."""
    def write(self, name, data):
        pf.profiles_dir().mkdir(parents=True, exist_ok=True)
        pf.path_of(name).write_text(json.dumps(dict({"version": pf.SCHEMA_VERSION, "name": name}, **data)))

    def test_stamping_leaves_what_follows_the_tagger_settings(self):
        self.write("A", {"follow_tagger_settings": True})
        self.write("B", {})
        pf.adopt_shared()
        a = json.loads(pf.path_of("A").read_text())
        self.assertEqual(a.get("own_settings"), [])
        self.assertNotIn("compat", a.get("filenames", {}))                 # (still Picard's)
        self.assertNotIn("remove_empty_dirs", a)
        self.assertTrue(a["follow_tagger_settings"])

    def test_a_file_put_back_unmarked_works_as_it_did(self):
        save("A", artwork={"min_px": 1000})
        save("B", artwork={"min_px": 1000})
        pf.adopt_shared()
        self.assertEqual(pf.shared_values()["artwork.min_px"], 1000)
        self.write("C", {})                                                # (put back from elsewhere)
        default_px = pf.load("C").data["artwork"]["min_px"]
        self.assertNotEqual(default_px, 1000)                              # (no shared value yet)
        self.assertFalse(pf.adopt_shared())                                # (the next start: marked)
        c = json.loads(pf.path_of("C").read_text())
        self.assertEqual(c["own_settings"], ["artwork.min_px"])
        self.assertEqual(pf.load("C").data["artwork"]["min_px"], default_px)

    def test_an_unmarked_file_saved_elsewhere_keeps_what_it_did(self):
        save("A")
        save("B")
        pf.adopt_shared()
        pf.set_shared({"genres.max": 9})
        self.write("C", {})
        before = pf.load("C").data["genres"]["max"]
        self.assertNotEqual(before, 9)
        self.assertEqual(pf.from_dict(pf.raw("C")).data["genres"]["max"], before)   # (the CLI's way)
        d = pf.raw("C")
        d["description"] = "y"
        pf.save(pf.from_dict(d))                                           # (profiles set C description=y)
        self.assertEqual(pf.load("C").data["genres"]["max"], before)

    def test_an_unmarked_file_keeps_an_unset_setting_unset(self):
        save("A")
        save("B")
        pf.adopt_shared()
        pf.set_shared({"tagger.write_id3v23": True, "compilations.va_name": "VA"})
        self.write("R", {})
        before = pf.from_dict({"name": "R", "version": pf.SCHEMA_VERSION}, sv={}).data
        r = pf.load("R").data                                              # (loads; nothing unset is filled)
        self.assertEqual(r["tagger"].get("write_id3v23"), before["tagger"].get("write_id3v23"))
        self.assertEqual(r["compilations"].get("va_name"), before["compilations"].get("va_name"))
        json.dumps(pf.raw("R"))                                            # (profiles show)
        self.assertFalse(pf.adopt_shared())                                # (stamped at the next start)
        self.assertIn("tagger.write_id3v23", json.loads(pf.path_of("R").read_text())["own_settings"])
        r = pf.load("R").data
        self.assertEqual(r["compilations"].get("va_name"), before["compilations"].get("va_name"))

    def test_a_value_marked_before_the_shared_file_still_becomes_shared(self):
        d = pf.from_dict({"name": "A", "version": pf.SCHEMA_VERSION, "artwork": {"min_px": 700},
                          "own_settings": []})                            # (as the editor saves it)
        pf.save(d)
        self.assertEqual(json.loads(pf.path_of("A").read_text())["own_settings"], [])
        save("B", artwork={"min_px": 700})
        pf.adopt_shared()
        pf.set_shared({"artwork.min_px": 900})
        self.assertEqual(pf.load("A").data["artwork"]["min_px"], 900)
        self.assertEqual(pf.load("B").data["artwork"]["min_px"], 900)

    def test_set_up_stopped_half_way_loses_nothing(self):
        from unittest import mock
        save("A", genres={"max": 7})
        save("B", genres={"max": 7})
        real, n = pf._write_json, []

        def stop_after_one_profile(path, data):
            real(path, data)
            if path != pf.shared_file():
                n.append(path)
                raise KeyboardInterrupt                                   # (stopped after the first file)
        with mock.patch.object(pf, "_write_json", stop_after_one_profile):
            with self.assertRaises(KeyboardInterrupt):
                pf.adopt_shared()
        self.assertEqual(len(n), 1)
        for name in ("A", "B"):
            self.assertEqual(pf.load(name).data["genres"]["max"], 7)
        self.assertFalse(pf.adopt_shared())                                # (the next start finishes it)
        self.assertNotIn("stamping", json.loads(pf.shared_file().read_text()))
        pf.set_shared({"genres.max": 3})
        for name in ("A", "B"):
            self.assertNotIn("max", json.loads(pf.path_of(name).read_text()).get("genres", {}))
            self.assertEqual(pf.load(name).data["genres"]["max"], 3)

    def test_migrate_still_upgrades_an_old_file(self):
        save("A")
        save("B")
        pf.adopt_shared()
        self.write("O", {"version": 2})
        changed, backup = pf.migrate("O")
        self.assertTrue(changed)
        self.assertEqual(json.loads(pf.path_of("O").read_text())["version"], pf.SCHEMA_VERSION)

    def test_a_shared_change_another_profile_cannot_take_is_found(self):
        save("A")
        save("B", subfolders={"misc_name": "Notes"})
        pf.adopt_shared()
        self.assertEqual(pf.shared_problems({"subfolders.covers_name": "Scans"}, but="A"), [])
        broken = pf.shared_problems({"subfolders.covers_name": "Notes"}, but="A")
        self.assertEqual(len(broken), 1)
        self.assertIn("'B'", broken[0])


if __name__ == "__main__":
    unittest.main()
