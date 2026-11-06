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
        self.assertNotIn("own_settings", json.loads(pf.path_of("B").read_text()))

    def test_only_shared_paths(self):
        with self.assertRaises(pf.ProfileError):
            pf.set_shared({"destination": "/x"})
        self.assertTrue(pf.is_shared("album_artist.lead"))
        self.assertFalse(pf.is_shared("operation"))


if __name__ == "__main__":
    unittest.main()
