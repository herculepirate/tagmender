# LEN-1 (docs/MATCHING_RULES.md): a release listing EVERY track a few seconds short of your files
# (times copied off a sleeve) agrees on lengths; another edit, longer and shorter by turns, does
# not. MED-11: a DVD or Blu-ray in a set is no disc of a CD rip (Popol Vuh - Kailash, Soul Jazz:
# 2 CDs and a DVD). Synthetic candidates: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
FILES = [234, 249, 240, 254, 344, 233, 180, 232, 259, 161]
SLEEVE = [232, 243, 235, 250, 339, 229, 174, 228, 254, 161]   # (Kailash's CD 2, as Discogs lists it)


def album(secs):
    return {"artist": "Popol Vuh", "title": "Kailash", "year": 2015, "discs": 1,
            "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": float(s)} for i, s in enumerate(secs)],
            "barcodes": set(), "catnos": set()}


def cand(secs):
    return {"source": "discogs", "id": "1", "url": "u", "artist": "Popol Vuh", "title": "Kailash", "year": 2015,
            "country": "UK", "label": "Soul Jazz", "catnos": set(), "barcodes": set(), "disc_count": 1,
            "tracks": [{"disc": 1, "title": f"T{i}", "secs": float(s)} for i, s in enumerate(secs)]}


class LEN1(unittest.TestCase):
    def test_LEN1_sleeve_times_short_on_every_track_agree(self):
        r = mc.score_candidate(album(FILES), cand(SLEEVE), CFG)
        self.assertEqual(r["dur_frac"], 1.0)
        self.assertIn("printed on the sleeve", " ".join(r["notes"]))

    def test_LEN1_another_edit_longer_and_shorter_by_turns_does_not(self):
        other = [s + (6 if i % 2 else -6) for i, s in enumerate(FILES)]
        self.assertFalse(mc.sleeve_times(list(zip(FILES, other))))
        r = mc.score_candidate(album(FILES), cand(other), CFG)
        self.assertLess(r["dur_frac"], 1.0)

    def test_LEN1_too_far_off_or_too_few_tracks_does_not(self):
        self.assertFalse(mc.sleeve_times([(f, f - 12) for f in FILES]))            # (12 s: another edit)
        self.assertFalse(mc.sleeve_times(list(zip(FILES[:3], SLEEVE[:3]))))
        self.assertFalse(mc.sleeve_times([(f, f) for f in FILES]))                 # (agreeing already)
        short = [(45.0, 39.0), (40.0, 34.0), (50.0, 44.0), (42.0, 36.0)]           # (6 s on 40 s: another edit)
        self.assertFalse(mc.sleeve_times(short))


class MED11(unittest.TestCase):
    def test_MED11_the_dvd_of_a_cd_set_is_no_disc_of_the_rip(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = {"id": 1, "title": "Kailash", "artists": [{"name": "Popol Vuh"}], "year": 2015, "country": "UK",
               "formats": [{"name": "CD", "qty": "2"}, {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video"]}],
               "labels": [{"name": "Soul Jazz Records", "catno": "SJR CD/DVD 302"}], "identifiers": [],
               "tracklist": [{"position": "CD1-1", "title": "A", "duration": "3:23", "type_": "track"},
                             {"position": "CD1-2", "title": "B", "duration": "7:22", "type_": "track"},
                             {"position": "CD2-1", "title": "C", "duration": "3:52", "type_": "track"},
                             {"position": "CD2-2", "title": "D", "duration": "4:03", "type_": "track"},
                             {"position": "DVD-1", "title": "Kailash", "duration": "", "type_": "track"}]}
        c = ma.discogs_candidate(rel, dm)
        self.assertEqual(c["disc_count"], 2)
        rel["formats"] = [{"name": "CD", "qty": "3"}]                              # (3 CDs: all three count)
        rel["tracklist"][-1] = {"position": "CD3-1", "title": "E", "duration": "5:00", "type_": "track"}
        self.assertEqual(ma.discogs_candidate(rel, dm)["disc_count"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
