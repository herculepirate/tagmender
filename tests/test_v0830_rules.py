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
import filenames as fnm  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

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


class NAM4(unittest.TestCase):
    def test_NAM4_a_bracket_the_word_limit_left_open_is_dropped(self):
        self.assertEqual(fnm.balance_brackets("1996 - Gold (Greatest Hits) [40th Anniversary [2014 Reissue]"),
                         "1996 - Gold (Greatest Hits) [2014 Reissue]")
        self.assertEqual(fnm.balance_brackets("1979 - Die Nacht Der Seele (Tantric [2005 Reissue]"),
                         "1979 - Die Nacht Der Seele [2005 Reissue]")
        self.assertEqual(fnm.balance_brackets("1994 - A (b) [c]"), "1994 - A (b) [c]")
        path, _notes = fnm.make_path("/lib", ["Band", "1996 - Gold (Greatest Hits) [40th Anniversary [2014 Reissue]",
                                               "01 Song (Live"], ".flac", {"compat": "linux"})
        self.assertEqual(str(path), "/lib/Band/1996 - Gold (Greatest Hits) [2014 Reissue]/01 Song (Live.flac")
        self.assertEqual(fnm.safe_component("Scans (2"), "Scans (2")            # (your own subfolder keeps it)


class FIL1(unittest.TestCase):
    def prop(self, **kw):
        return pl.Proposal(source_dir="/x", files=["/x/1.flac"], targets=[{}], status="identified", tier="exact",
                           confidence=87.4, margin=9.9, all_lengths=True, **kw)

    def test_FIL1_filed_on_your_own_number_and_every_length(self):
        prof = pf.default()
        self.assertIn("catalogue number", pl._files_on_evidence(self.prop(own_number=True), prof, 87.4))
        self.assertEqual(pl._files_on_evidence(self.prop(own_number=True, guessed_pick=True), prof, 87.4), "")
        self.assertEqual(pl._files_on_evidence(self.prop(own_number=False), prof, 87.4), "")
        p = self.prop(own_number=True)
        p.all_lengths = False
        self.assertEqual(pl._files_on_evidence(p, prof, 87.4), "")

    def test_FIL1_filed_when_the_disc_id_chose_the_pressing(self):
        prof = pf.default()
        p = self.prop(evidence=["pressing chosen by disc ID (track positions of the CD) matches"])
        p.margin, p.confidence = -5.3, 94.7
        self.assertIn("disc ID", pl._files_on_evidence(p, prof, 94.7))
        p.guessed_pick = True
        self.assertEqual(pl._files_on_evidence(p, prof, 94.7), "")


class TIE4(unittest.TestCase):
    def test_TIE4_an_official_release_beats_a_bootleg_that_fits_as_well(self):
        a = album(FILES)
        boot = dict(cand(FILES), id="boot", unofficial=True, country="Russia")
        official = dict(cand(FILES), id="official", country="US")
        r = mc.classify([mc.score_candidate(a, c, CFG) for c in (boot, official)], CFG)
        self.assertEqual(r["best"]["cand"]["id"], "official")
        r = mc.classify([mc.score_candidate(a, boot, CFG)], CFG)         # (the bootleg alone: doubted)
        self.assertTrue(any("unofficial" in w for w in r["why"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
