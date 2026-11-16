# ED-8 (docs/MATCHING_RULES.md): a profile whose naming script still holds the edition lines
# TagMender's scripts carried before 0.8.12 named every SACD, DVD-Audio and Blu-ray "CD" (a rip of
# an SACD's stereo and quadraphonic layers filed as "1973 - Piano Man ... [2024 CD]"), and left an
# album tag's " [2024 SACD]" in the folder name. Those exact lines are brought up to date when the
# profile is read; a line the user changed is left alone. Synthetic template only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import naming  # noqa: E402
import profiles as pf  # noqa: E402

OLD_STRIP = "$set(_tm_album,$rreplace(%album%,\\\\s\\\\[\\(?:\\\\d{4} \\)?" + pf.OLD_STRIP_WORDS + "\\\\]\\$,))"
TEMPLATE = "\n".join([
    OLD_STRIP,
    "$set(usedate,$if(%originaldate%,%originaldate%,%date%))",
    pf.OLD_EDKIND,
    "$set(_edyear,$if($and(%date%,$ne($left(%date%,4),$left(%usedate%,4))),$left(%date%,4),))",
    "$set(_edition,$if(%_edkind%,$if($or($ne(%_edkind%,CD),%_edyear%),$trim(%_edyear% %_edkind%)),))",
    "%albumartist%/$left(%usedate%,4) - %_tm_album%$if(%_edition%, [%_edition%])/",
    "$num(%tracknumber%,2)",
])


def folder(template, **tags):
    tags = {k: [v] for k, v in tags.items()}
    return naming.render(template, tags)[1]


class ED8OldEditionLines(unittest.TestCase):
    def profile(self, template):
        return {"name": "x", "version": 3, "naming": {"preset": "tagmender", "template": template}}

    def test_ED8_old_lines_are_found_and_upgraded_once(self):
        data = self.profile(TEMPLATE)
        self.assertTrue(pf.has_legacy(data))
        new = pf.rename_legacy(data)["naming"]["template"]
        self.assertIn(pf.NEW_EDKIND, new)
        self.assertNotIn(pf.OLD_EDKIND, new)
        self.assertIn(pf.NEW_STRIP_WORDS, new)
        self.assertFalse(pf.has_legacy(self.profile(new)))
        self.assertEqual(pf.rename_legacy(self.profile(new))["naming"]["template"], new)

    def test_ED8_everything_else_in_the_script_stays(self):
        new = pf.rename_legacy(self.profile(TEMPLATE))["naming"]["template"]
        old_lines, new_lines = TEMPLATE.split("\n"), new.split("\n")
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        self.assertEqual(changed, [0, 2])

    def test_ED8_a_line_you_changed_is_yours(self):
        mine = TEMPLATE.replace("$if(%media%,CD,)", "$if(%media%,Disc,)")
        data = self.profile(mine.replace(OLD_STRIP, "$set(_tm_album,%album%)"))
        self.assertFalse(pf.has_legacy(data))
        self.assertEqual(pf.rename_legacy(data)["naming"]["template"], data["naming"]["template"])

    def test_ED8_an_sacd_rip_is_named_sacd_once(self):
        new = pf.rename_legacy(self.profile(TEMPLATE))["naming"]["template"]
        tags = dict(albumartist="Band P", album="Piano Song [2024 SACD]", date="2024-02-28", originaldate="1973",
                    tracknumber="1")
        for media in ("Hybrid SACD (SACD layer, 2 channels)", "SACD, Hybrid, Multichannel, Stereo, Album, Reissue"):
            with self.subTest(media=media):
                self.assertEqual(folder(TEMPLATE, media=media, **tags), "1973 - Piano Song [2024 SACD] [2024 CD]")
                self.assertEqual(folder(new, media=media, **tags), "1973 - Piano Song [2024 SACD]")

    def test_ED8_cd_vinyl_and_web_name_as_before(self):
        new = pf.rename_legacy(self.profile(TEMPLATE))["naming"]["template"]
        for media, album in (("CD", "Piano Song [2015 CD]"), ("12\" Vinyl", "Piano Song [2015 Vinyl]"),
                             ("Digital Media", "Piano Song")):
            tags = dict(albumartist="Band P", album=album, date="2015", originaldate="1973", tracknumber="1",
                        media=media)
            with self.subTest(media=media):
                self.assertEqual(folder(new, **tags), folder(TEMPLATE, **tags))

    def test_ED8_with_editions_takes_the_new_words_as_the_old(self):
        new = pf.rename_legacy(self.profile(TEMPLATE))["naming"]["template"]
        both = [naming.with_editions(t + "\n$if(%_edition%, [%_edition%])", "edition_year") for t in (TEMPLATE, new)]
        self.assertIn(naming._RULE_SUFFIX_WORDS, both[0])
        self.assertIn(naming._RULE_SUFFIX_WORDS, both[1])
        self.assertNotIn(pf.NEW_STRIP_WORDS, both[1])


if __name__ == "__main__":
    unittest.main()
