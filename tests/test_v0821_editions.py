#!/usr/bin/env python3
"""0.8.21: edition suffixes follow ED-1..ED-4 of docs/MATCHING_RULES.md, in TagMender's own
templates and in scripts that carry their own edition lines."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import naming  # noqa: E402

BASE = {"albumartist": ["Artist Alpha"], "artist": ["Artist Alpha"], "title": ["First"], "tracknumber": ["1"],
        "totaldiscs": ["1"]}


def tags(album, media, date, original):
    return dict(BASE, album=[album], media=[media], date=[date], originaldate=[original])


class ED_EditionText(unittest.TestCase):
    def test_sacd_always_marked(self):                                     # ED-1
        self.assertEqual(naming.edition(tags("A", "Hybrid SACD", "1999", "1999")), "1999 SACD")

    def test_vinyl_and_tape_rips_always_marked(self):                      # ED-2
        self.assertEqual(naming.edition(tags("A", '12" Vinyl', "1977", "1977")), "1977 Vinyl Rip")
        self.assertEqual(naming.edition(tags("A", "Cassette", "1984", "1984")), "1984 Tape Rip")

    def test_original_cd_or_download_unmarked(self):                       # ED-3
        self.assertEqual(naming.edition(tags("A", "Digital Media", "2015", "2015")), "")
        self.assertEqual(naming.edition(tags("A", "CD", "1987", "1987")), "")

    def test_newer_cd_or_download_is_a_remaster_or_reissue(self):          # ED-3
        self.assertEqual(naming.edition(tags("A", "Digital Media", "2015", "1987")), "2015 Remaster")
        self.assertEqual(naming.edition(tags("A", "CD, Album, Reissue", "2015", "1987")), "2015 Reissue")
        self.assertEqual(naming.edition(tags("A", "CD, Album, Reissue, Remastered", "2015", "1987")),
                         "2015 Remaster")

    def test_kind_only_mode(self):
        self.assertEqual(naming.edition(tags("A", "Hybrid SACD", "1999", "1999"), "edition"), "SACD")
        self.assertEqual(naming.edition(tags("A", "Digital Media", "2015", "2015"), "edition"), "")


class ED5_HiRes(unittest.TestCase):
    def hd(self, t, **more):
        return naming.edition(dict(t, **{"~edition_hd": ["1"]}, **more))

    def test_original_hi_res_download(self):
        self.assertEqual(self.hd(tags("A", "Digital Media", "2009", "2009")), "HD")

    def test_hi_res_remaster(self):
        self.assertEqual(self.hd(tags("A", "Digital Media", "2015", "1987")), "2015 Remaster HD")

    def test_sacd_and_vinyl_add_no_hd(self):
        self.assertEqual(self.hd(tags("A", "Hybrid SACD", "1999", "1999")), "1999 SACD")
        self.assertEqual(self.hd(tags("A", '12" Vinyl', "1977", "1977")), "1977 Vinyl Rip")

    def test_album_known_shows_only_hd(self):
        t = tags("A", "Digital Media", "2015", "1987")
        self.assertEqual(self.hd(t, **{"~edition_unproven": ["1"]}), "HD")
        self.assertEqual(naming.edition(dict(t, **{"~edition_unproven": ["1"]})), "")


class ED_ScriptWithItsOwnEditionLines(unittest.TestCase):
    """A script that builds its own edition (the all-music preset's lines) follows the rules."""

    def folder(self, t):
        tpl = naming.effective_template({"preset": "all-music", "editions": "edition_year"})
        return naming.render(tpl, t)[-2]

    def test_no_plain_web(self):
        self.assertEqual(self.folder(tags("Album", "Digital Media", "2015", "2015")), "2015 - Album")

    def test_sacd_and_reissue(self):
        self.assertEqual(self.folder(tags("Album", "Hybrid SACD", "1999", "1999")), "1999 - Album [1999 SACD]")
        self.assertEqual(self.folder(tags("Album", "Digital Media", "2015", "1987")), "1987 - Album [2015 Remaster]")

    def test_album_tag_suffix_not_doubled(self):
        t = dict(tags("Album [2015 Remaster HD]", "Digital Media", "2015", "1987"), **{"~edition_hd": ["1"]})
        self.assertEqual(self.folder(t), "1987 - Album [2015 Remaster HD]")


if __name__ == "__main__":
    unittest.main()
