# TRK-6 (docs/MATCHING_RULES.md): a work's parts listed with no position of their own on a video
# disc named with no number ("DVD-2.1") go with that disc, never onto the CD before it. A 2-CD +
# DVD edition listing the same suite as an index on CD 1 and on the DVD was read as 11 tracks on
# CD 1 for its 8, so the MusicBrainz release's link to it was taken for another pressing (TAG-4).
# Synthetic release, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "plugins_builtin" / "discogs"))
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def tr(pos, title, dur="5:00", kind="track", **kw):
    return dict({"position": pos, "title": title, "duration": dur, "type_": kind}, **kw)


def suite(prefix):
    return {"type_": "index", "position": "", "title": "Fading", "duration": "6:36",
            "sub_tracks": [tr(f"{prefix}-2.1", "i. After"), tr(f"{prefix}-2.2", "ii. Fading")]}


def cd_dvd(video_prefix="DVD", video_format="DVD"):
    return {"id": 9, "title": "Ever", "artists": [{"name": "Band Q"}], "year": 2018, "country": "UK",
            "labels": [], "identifiers": [],
            "formats": [{"name": "CD", "qty": "1"}, {"name": "CD", "qty": "1"},
                        {"name": video_format, "qty": "1", "descriptions": ["Multichannel"]},
                        {"name": "All Media", "qty": "1", "text": "Digipak"}],
            "tracklist": [tr("", "Ever Remix", "", "heading"), tr("CD1-1", "Dark"), suite("CD1")]
            + [tr(f"CD1-{k}", f"Song {k}") for k in range(3, 9)]
            + [tr("", "Live", "", "heading")] + [tr(f"CD2-{k}", f"Live {k}") for k in range(1, 7)]
            + [tr("", "5.1", "", "heading"), tr(f"{video_prefix}-1", "Dark"), suite(video_prefix)]
            + [tr(f"{video_prefix}-{k}", f"Song {k}") for k in range(3, 7)]}


class TRK6(unittest.TestCase):
    def setUp(self):
        self.dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))

    def test_TRK6_the_dvds_index_parts_stay_off_the_cd(self):
        tracks = ma.discogs_candidate(cd_dvd(), self.dm)["tracks"]
        self.assertEqual([t["disc"] for t in tracks].count(1), 9)      # (8 tracks, its index as 2 rows)
        self.assertNotIn("i. After", [t["title"] for t in tracks if t["disc"] == 1][3:])

    def test_TRK6_the_same_pressing_by_its_discs(self):
        det = cd_dvd()
        shape, unsure = engine._discogs_shape(det, ma.discogs_candidate(det, self.dm))
        self.assertEqual(shape, [9, 6])           # (TAG-4: 8 + 6 on MusicBrainz, within its tenth)
        self.assertTrue(unsure)

    def test_TRK6_a_blu_ray_too(self):
        tracks = ma.discogs_candidate(cd_dvd("BD", "Blu-ray"), self.dm)["tracks"]
        self.assertEqual([t["disc"] for t in tracks].count(1), 9)


class TRK6_Limits(unittest.TestCase):
    def test_TRK6_only_video_names_with_no_number(self):
        for pos in ("DVD-2.1", "BD-1.2", "Blu-ray-3.1", "dvd - 4.1", "CD1-2.1", "2-3"):
            with self.subTest(pos=pos):
                self.assertTrue(ma._HYPHEN_DISC.match(pos))
        for pos in ("CD-5.1", "A-1", "LP-2.1", "2.1", "2a", ""):
            with self.subTest(pos=pos):
                self.assertFalse(ma._HYPHEN_DISC.match(pos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
