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

OLD_STRIP = pf.OLD_STRIP_LINE
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
        self.assertIn(pf.NEW_STRIP_LINE, new.split("\n"))
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


class ED8OnlyWhatItShould(unittest.TestCase):
    def up(self, template):
        return pf.rename_legacy({"name": "x", "version": 3, "naming": {"preset": "tagmender",
                                                                        "template": template}})["naming"]["template"]

    def test_ED8_a_line_merely_holding_the_old_text_stays(self):
        for line in (f"$noop(was {pf.OLD_EDKIND})", f"$if(%x%,{pf.OLD_EDKIND})"):
            with self.subTest(line=line):
                t = TEMPLATE.replace(pf.OLD_EDKIND, line)
                self.assertEqual(self.up(t), t)

    def test_ED8_a_strip_line_of_other_words_leaves_both(self):
        t = TEMPLATE.replace("|Tape\\)", "|Tape|Mini\\)")
        self.assertNotEqual(t, TEMPLATE)
        self.assertEqual(self.up(t), t)

    def test_ED8_the_edition_line_alone_is_upgraded_alone(self):
        t = "\n".join(line for line in TEMPLATE.split("\n") if line != OLD_STRIP)
        new = self.up(t)
        self.assertIn(pf.NEW_EDKIND, new)
        self.assertEqual(len(new.split("\n")), len(t.split("\n")))

    def test_ED8_windows_line_ends_and_indents_kept(self):
        t = TEMPLATE.replace(pf.OLD_EDKIND, "  " + pf.OLD_EDKIND).replace("\n", "\r\n")
        new = self.up(t)
        self.assertIn("  " + pf.NEW_EDKIND + "\r", new.split("\n"))
        self.assertEqual(new.count("\r\n"), t.count("\r\n"))

    def test_ED8_media_named_as_the_presets_name_them(self):
        new = self.up(TEMPLATE)
        rows = (("DVD-Audio", "Piano Song", "1973 - Piano Song [2015 DVD-A]"),
                ("Blu-ray", "Piano Song", "1973 - Piano Song [2015 Blu-ray]"),
                ("Hybrid SACD (CD layer)", "Piano Song", "1973 - Piano Song [2015 CD]"),
                ("DualDisc (CD side)", "Piano Song", "1973 - Piano Song [2015 CD]"),
                ("Blu-spec CD", "Piano Song", "1973 - Piano Song [2015 CD]"))
        for media, album, want in rows:
            with self.subTest(media=media):
                self.assertEqual(folder(new, albumartist="Band P", album=album, date="2015", originaldate="1973",
                                        tracknumber="1", media=media), want)

    def test_ED8_migrate_writes_it_back_once_with_a_backup(self):
        name = "ed8-migrate"
        path = pf.path_of(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps({"name": name, "version": pf.SCHEMA_VERSION,
                                    "naming": {"preset": "tagmender", "template": TEMPLATE}}), encoding="utf-8")
        try:
            changed, backup = pf.migrate(name)
            self.assertTrue(changed)
            self.assertTrue(backup and backup.exists())
            self.assertIn(pf.OLD_EDKIND, backup.read_text(encoding="utf-8").replace("\\\\", "\\"))
            written = json.loads(path.read_text(encoding="utf-8"))["naming"]["template"]
            self.assertIn(pf.NEW_EDKIND, written)
            self.assertEqual(pf.migrate(name), (False, None))
        finally:
            for f in path.parent.glob(path.name + "*"):
                f.unlink()


if __name__ == "__main__":
    unittest.main()
