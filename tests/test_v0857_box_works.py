# MED-16 and TRK-5 (docs/MATCHING_RULES.md): a box of CDs and video Blu-rays ripped as its CDs
# (Soft Machine - Slightly All The Time: 14 CDs and 3 Blu-rays, "BD1-1".."BD3-n"), and works
# the release lists without lengths, their parts lettered as one track's ("CD2-3a".."CD2-3c"
# under one index entry), where the rip holds one file per work. Synthetic releases: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
DM = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))


def tr(pos, title, dur=""):
    return {"position": pos, "title": title, "duration": dur, "type_": "track"}


def work(title, subs, dur=""):
    return {"position": "", "title": title, "duration": dur, "type_": "index", "sub_tracks": subs}


def box(bd_format=None, lengths=False):
    """2 CDs and a Blu-ray of TV footage. CD 2's track 3 is a work in three lettered parts."""
    d = "4:00" if lengths else ""
    rel = {"id": 7, "title": "Recordings", "artists": [{"name": "The Band"}], "year": 2026, "country": "Europe",
           "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]},
                       {"name": "CD", "qty": "1", "descriptions": ["Album"]},
                       bd_format or {"name": "Blu-ray", "qty": "1", "descriptions": ["Compilation", "Stereo"]},
                       {"name": "Box Set", "qty": "1"}],
           "labels": [{"name": "Label", "catno": "123"}], "identifiers": [],
           "tracklist": [tr("CD1-1", "One", d), tr("CD1-2", "Two", d), tr("CD1-3", "Three", d),
                         tr("CD2-1", "Four", d), tr("CD2-2", "Five", d),
                         work("Suite", [tr("CD2-3a", "Pig", d), tr("CD2-3b", "Skin", d), tr("CD2-3c", "Door", d)]),
                         {"position": "", "title": "The Video Vaults", "type_": "heading"},
                         tr("BD1-1", "Facelift (TV)"), tr("BD1-2", "Teeth (TV)"), tr("BD1-3", "Noisette (TV)"),
                         tr("BD1-4", "Mousetrap (TV)")]}
    return rel


def album(layout):
    """Files per disc, e.g. [3, 3]."""
    tracks = [{"disc": d + 1, "num": i + 1, "title": f"T{d}{i}", "secs": 240.0}
              for d, n in enumerate(layout) for i in range(n)]
    return {"artist": "The Band", "title": "Recordings", "year": 2026, "discs": len(layout),
            "tracks": tracks, "barcodes": set(), "catnos": set()}


class MED16(unittest.TestCase):
    def test_MED16_the_blu_ray_of_a_cd_box_is_not_the_rip_nor_its_discs(self):
        c = ma.discogs_candidate(box(), DM)
        self.assertFalse([t for t in c["tracks"] if "TV" in t["title"]])
        self.assertEqual(c["disc_count"], 2)
        self.assertEqual({t["disc"] for t in c["tracks"]}, {1, 2})
        # (as listed: 3 + 5 tracks, the work's parts each counted)
        self.assertEqual(len(c["tracks"]), 8)

    def test_MED16_a_blu_ray_audio_keeps_its_tracks(self):
        rel = box(bd_format={"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio", "Stereo"]})
        c = ma.discogs_candidate(rel, DM)
        self.assertEqual(len([t for t in c["tracks"] if "TV" in t["title"]]), 4)

    def test_MED16_a_video_only_release_keeps_its_chapters(self):
        rel = box()
        rel["formats"] = [{"name": "Blu-ray", "qty": "1", "descriptions": ["Compilation"]}]
        rel["tracklist"] = [t for t in rel["tracklist"] if str(t.get("position", "")).startswith("BD")]
        c = ma.discogs_candidate(rel, DM)
        self.assertEqual(len(c["tracks"]), 4)
        self.assertTrue(c["video_only"])


class TRK5(unittest.TestCase):
    def test_TRK5_one_file_per_work_on_every_disc_compares_the_works(self):
        c = ma.discogs_candidate(box(), DM)
        self.assertEqual(len(c["works_view"]), 6)
        r = mc.score_candidate(album([3, 3]), c, CFG)
        self.assertTrue(r["tracks_ok"])
        self.assertTrue(r["cand"].get("whole_works"))
        self.assertIn("lettered parts", " ".join(r["notes"]))
        self.assertTrue(ma.summarize({"best": r}).get("whole_works"))

    def test_TRK5_parts_that_are_files_stay_tracks(self):
        c = ma.discogs_candidate(box(), DM)
        r = mc.score_candidate(album([3, 5]), c, CFG)
        self.assertTrue(r["tracks_ok"])
        self.assertFalse(r["cand"].get("whole_works"))
        self.assertNotIn("whole_works", ma.summarize({"best": r}))

    def test_TRK5_the_same_total_on_other_discs_is_not_one_file_per_work(self):
        c = ma.discogs_candidate(box(), DM)
        r = mc.score_candidate(album([4, 2]), c, CFG)       # (6 files, but not 3 + 3)
        self.assertFalse(r["tracks_ok"])
        self.assertIn("track count differs", " ".join(r["notes"]))

    def test_TRK5_files_that_fit_neither_reading_still_differ(self):
        r = mc.score_candidate(album([3, 2]), ma.discogs_candidate(box(), DM), CFG)
        self.assertFalse(r["tracks_ok"])

    def test_TRK5_parts_listing_lengths_are_never_one_work(self):
        c = ma.discogs_candidate(box(lengths=True), DM)
        self.assertNotIn("works_view", c)
        self.assertFalse(mc.score_candidate(album([3, 3]), c, CFG)["tracks_ok"])

    def test_TRK5_tagged_one_track_per_work_only_when_matched_so(self):
        rel = box()
        _a, whole, n = engine.discogs_parts(copy.deepcopy(rel), DM, None, None, whole_works=True)
        self.assertEqual(n, 2)
        self.assertEqual([(t["discnumber"], t["tracknumber"], t["title"]) for t in whole if t["discnumber"] == "2"],
                         [("2", "1", "Four"), ("2", "2", "Five"), ("2", "3", "Suite")])
        self.assertEqual({t["totaltracks"] for t in whole if t["discnumber"] == "2"}, {"3"})
        _a, listed, _n = engine.discogs_parts(copy.deepcopy(rel), DM, None, None)
        self.assertEqual(len(listed), 8)
        self.assertEqual([t["title"] for t in listed if t["discnumber"] == "2"][2:], ["Pig", "Skin", "Door"])

    def test_TRK5_a_work_with_its_own_length_is_trk1_unchanged(self):
        rel = box()
        rel["tracklist"][5] = work("Suite", rel["tracklist"][5]["sub_tracks"], dur="12:00")
        c = ma.discogs_candidate(rel, DM)
        self.assertEqual(len(c["tracks"]), 6)               # (TRK-1 already: one track)
        self.assertNotIn("works_view", c)


sys.path.insert(0, str(HERE / "tests"))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402


class TRK5Chosen(Base):
    """The release chosen by hand (plan --release): read one track per work as the matcher
    scored it, never placed as slots of the as-listed parts (no part "missing")."""

    def rel(self, roman=False):
        r = dg_release(4243, "Band", "Album", durs=("0:10", "0:20"))
        parts = ("3-I", "3-II") if roman else ("3a", "3b")
        r["tracklist"].append(work("Suite", [tr(parts[0], "Pig"), tr(parts[1], "Door")]))
        return r

    def test_TRK5_a_single_disc_release_chosen_by_hand_is_tagged_one_track_per_work(self):
        for roman in (False, True):
            d = self.album("Band", "Album", folder=f"2005 - Album {roman}")
            e = self.engine(FakeHttp(dg=[self.rel(roman)]))
            e.force = {os.path.realpath(d): "https://www.discogs.com/release/4243-Band-Album"}
            a = e.plan(d)["albums"][0]
            self.assertEqual(a["identification"]["status"], "identified", a["identification"])
            titles = [((f.get("changes") or {}).get("title") or [""])[0] for f in a["files"]]
            self.assertEqual(titles[-1], "Suite", titles)
            self.assertFalse(any("missing" in str(w) for w in a["identification"]["why"]), a["identification"])


if __name__ == "__main__":
    unittest.main()
