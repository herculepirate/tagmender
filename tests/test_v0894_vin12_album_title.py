# 0.8.94 VIN-12 (decided 2026-11-21): a vinyl rip's Album tag names the rip as its folder does -
# "Lateralus [2005 Vinyl Rip 61422-3116-1]" - so LMS, which takes tracks with one title, release
# ID, disc tags and album artist for one album whatever their folder, lists two rips of one
# pressing as two albums. A second copy whose bracket is the same (no ripper, no year ripped)
# adds its own resolution. Synthetic data only.
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0872_vin_vinyl_rips as vt  # noqa: E402
import test_editions as ted  # noqa: E402
import executor  # noqa: E402
import naming  # noqa: E402
import tagio  # noqa: E402

A = "1973 - Piano Man [Vinyl Rip 24bit 96khz Mercury SRM-1-1184]"
B = "1973 - Piano Man [Vinyl Rip 24bit 192khz Mercury SRM-1-1184]"


def albums_of(plan):
    return {Path(a["source_dir"]).name: {v for fe in a["files"] for v in (fe["changes"].get("album") or [])}
            for a in plan["albums"]}


class VIN12_AlbumTitle(vt.VinBase):
    def test_VIN12_two_rips_named_apart_have_two_titles(self):
        self.rip(vt.PB, 96000, vt.PB_NOTES)
        self.rip(vt.OTHER, 192000, shift=1)
        got = albums_of(self.moving(self.http()).plan(self.lib / "Billy Joel"))
        self.assertEqual(got, {vt.PB: {"Piano Man [1977 Vinyl Rip PBTHAL SRM-1-1184]"},
                               vt.OTHER: {"Piano Man [1977 Vinyl Rip SRM-1-1184]"}})

    def test_VIN12_two_rips_with_the_same_details_never_share_a_title_and_stay_so(self):
        self.rip(A, 96000)
        self.rip(B, 192000, shift=1)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        got = albums_of(plan)
        titles = [t for ts in got.values() for t in ts]
        self.assertEqual(len(titles), 2, got)
        self.assertEqual(len(set(titles)), 2, got)                 # (LMS: two albums)
        self.assertIn("Piano Man [1977 Vinyl Rip SRM-1-1184]", titles)
        other = next(t for t in titles if t != "Piano Man [1977 Vinyl Rip SRM-1-1184]")
        self.assertRegex(other, r"^Piano Man \[1977 Vinyl Rip SRM-1-1184 \d+-\d+(\.\d)?\]$")
        # (its folder never takes the resolution: VIN-3)
        self.assertFalse(any(re.search(r"\d+-(96|192)\b", f) for f in self.folders(plan) if "Vinyl Rip SRM" in f
                             and f.startswith("1973 - Piano Man [1977")), self.folders(plan))
        self.assertEqual(executor.Executor(plan).run()["counts"].get("done"), 2)
        again = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual([fe["op"] for a in again["albums"] for fe in a["files"]], ["none"] * 3, again)
        self.assertEqual({tagio.read(fe["src"])["album"][0] for a in again["albums"] for fe in a["files"]}, {other})

    def test_VIN12_off_with_the_setting(self):
        self.rip(vt.PB, 96000, vt.PB_NOTES)
        plan = vt.VIN_Settings.moving_with(self, self.http(), vinyl_rip_folders=False).plan(self.lib / "Billy Joel")
        self.assertEqual(albums_of(plan), {vt.PB: {"Piano Man [1977 Vinyl Rip]"}})


class VIN12_FolderFromTheTag(unittest.TestCase):
    def test_VIN12_a_tag_carrying_the_rip_bracket_names_the_folder_once(self):
        t = ted.tags("I [2019 Vinyl Rip CAT 1]", "2004", "2019-03-01", '12" Vinyl')
        self.assertEqual(ted.folder(t, "edition_year"), "2004 - I [2019 Vinyl Rip CAT 1]")   # (no details known now)
        t = dict(t, **{"~rip_catalog": ["CAT 1"], "~ripper": ["PBTHAL"]})
        self.assertEqual(ted.folder(t, "edition_year"), "2004 - I [2019 Vinyl Rip PBTHAL CAT 1]")
        # (a catalogue number shaped like a resolution is kept; a second copy's resolution never named)
        for tag, want in (("I [2019 Vinyl Rip MFSL 1-023]", "2004 - I [2019 Vinyl Rip MFSL 1-023]"),
                          ("I [2019 Vinyl Rip PBTHAL MFSL 1-023]", "2004 - I [2019 Vinyl Rip PBTHAL MFSL 1-023]"),
                          ("I [2019 Vinyl Rip LSP 1-1234 32-192]", "2004 - I [2019 Vinyl Rip LSP 1-1234]"),
                          ("I [2019 Vinyl Rip CAT 1 copy 2]", "2004 - I [2019 Vinyl Rip CAT 1]")):
            self.assertEqual(ted.folder(ted.tags(tag, "2004", "2019-03-01", '12" Vinyl'), "edition_year"), want, tag)
        # (a bracket of another's, with the resolution: not TagMender's - left as the title's)
        self.assertFalse(naming._own_rip_bracket("2019 Vinyl Rip 24bit 96khz CAT 1"))
        self.assertTrue(naming._own_rip_bracket("2019 Vinyl Rip SRM-1-1184 32-192"))


if __name__ == "__main__":
    unittest.main()
