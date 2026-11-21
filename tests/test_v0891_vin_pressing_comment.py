# CTY-3, VIN-2, VIN-4, VIN-8 (docs/MATCHING_RULES.md): two vinyl-rip defects found on real folders.
#  - A rip whose folder name says "{South Korean Press 1986}" was filed as the Dutch 1981 pressing
#    that only shares its catalogue number: the country's adjective and "Press 1986" were not read.
#  - A rip whose comment is "Mercury - <catalogue number>, Re2015, EU (omp30)" got the rip line as a
#    comment of its own (the preview showed the comment empty): the comment reads as the described
#    comment comment:omp30. The line now goes below the text, kept whole, and "omp30" - a handle
#    signing the release's description - is the ripper.
# Synthetic data only.
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0868_med22c_layer_copies import medium  # noqa: E402
from test_v0872_vin_vinyl_rips import CAT, LENS, RID, TITLES, VinBase, vinyl_release  # noqa: E402
from test_version_folders import SetHttp, release  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import vinylrip as vr  # noqa: E402

NL = "0a0b0c0d-1111-4888-9999-aaaabbbbcccc"
KR = "0a0b0c0d-2222-4888-9999-aaaabbbbcccc"


class CTY3_FolderWords(unittest.TestCase):
    def test_CTY3_a_country_adjective_before_a_pressing_word_names_the_country(self):
        name = "Rush - Moving Pictures (1981) [VINYL] {South Korean Press 1986}"
        self.assertEqual(mc.demonym_countries(mc._bracket_text(name, pressing_only=True)), {"KR"})
        self.assertEqual(mc.demonym_countries("[Japanese Pressing]"), {"JP"})
        self.assertEqual(mc.demonym_countries("(Original West German Press)"), {"DE"})
        self.assertEqual(mc.demonym_countries("(German Edition)"), {"DE"})
        # (an adjective alone names no pressing)
        for text in ("(German Version)", "(Dutch Courage)", "[Live in Japan]", "(French Kiss)", "(Korean)"):
            self.assertEqual(mc.demonym_countries(text), set(), text)

        self.assertEqual(mc.demonym_countries("(New Zealand Pressing)"), {"NZ"})

    def test_CTY3_version_priority_reads_the_brackets_only(self):
        import match_albums as ma
        # (a title's words name no pressing: "The Swiss Release Party", "English Release")
        self.assertEqual(ma.version_priority({"folder": "The Swiss Release Party"}, country="CH")[1], 1)
        self.assertEqual(ma.version_priority({"folder": "English Release"}, country="GB")[1], 1)
        self.assertEqual(ma.version_priority({"folder": "X (1981) {South Korean Press 1986}"}, country="KR")[1], 0)

    def test_CTY3_press_year_beside_its_press_word_only(self):
        for name, year in (("Rush - Moving Pictures (1981) [VINYL] {South Korean Press 1986}", 1986),
                           ("Rush - Hold Your Fire (1987, Re2015, EU)", 2015), ("X (1977 UK Pressing)", 1977),
                           ("X (Pressed 1999)", 1999), ("X (Repress 2015)", 2015), ("X (Repressed 2015)", 2015),
                           ("X (2015 Repress)", 2015), ("X [1981 Original, 2015 Pressing]", 2015),
                           ("X (1975 Recording, 2010 Press)", 2010)):
            self.assertEqual(mc.press_year(name), year, name)
        # ("press" as a noun; no year beside it; no "re" word)
        for name in ("X (1981)", "X [Press Kit]", "X (1998 Press Kit)", "X (1975 Press Conference)",
                     "X (Live at the Press Club 1975)", "X (Hot Off the Press 1999)", "X (Live 1978)",
                     "X [Record 2015]", "X (Rex2015)", "X [Pre2015]", "X (Remaster2015)",
                     "X (1977 TP Press)", "X (1977 LTD Press)", "X (1977 THE Press)", "X (Live 1975 Press)",
                     "X (Press 1975 Interview)", "X (1977 Pressed Flowers)", "X (1979 Pressing Matters)"):
            self.assertIsNone(mc.press_year(name), name)
        # (the new forms are no pressing_year rule: CTY-3 decision 2026-11-21; the older forms as before)
        self.assertIsNone(mc.pressing_year("X {South Korean Press 1986}"))
        self.assertIsNone(mc.pressing_year("X [Re2015]"))
        # (round 2: "Re2015" is no catalogue number, so "(1987, Re2015, EU)" names no 1987 edition)
        for name in ("X (1987, Re2015, EU)", "X (1987, RE-2015, EU)", "X (Rm2011)"):
            self.assertEqual(mc.catno_hints(name), [], name)
            self.assertIsNone(mc.pressing_year(name), name)
        self.assertEqual(mc.press_year("X (1987, Re2015, EU)"), 2015)
        self.assertEqual(mc.pressing_year("X (1987, Mercury, 832 464-1)"), 1987)       # (as before)
        self.assertEqual(mc.pressing_year("X (Remastered 2011)"), 2011)


class CTY3_PressingByFolderCountry(VinBase):
    """Two pressings sharing one catalogue number: the Dutch 1981 and the South Korean 1986; the
    folder names the number and "{South Korean Press 1986}", the files carry the Korean entry's ID
    (another tagger's)."""

    def releases(self):
        nl = release(NL, [medium(1, '12" Vinyl', TITLES, LENS)], date="1981", country="NL",
                     catno="6337 160", label="Mercury", barcode="")
        kr = release(KR, [medium(1, '12" Vinyl', TITLES, LENS)], date="1986-11-22", country="KR",
                     catno="6337 160", label="Mercury", barcode="")
        return [nl, kr]

    def test_CTY3_VIN2_the_folders_country_and_press_year_pick_the_pressing(self):
        top = self.rip("1973 - Piano Man (1973) [Vinyl Rip 6337 160] {South Korean Press 1986}", 96000)
        for f in sorted(top.glob("*.flac")):
            tagio.write(f, {"musicbrainz_albumid": [KR], "releasecountry": ["KR"]})
        plan = self.moving(SetHttp(mb=self.releases())).plan(top)
        [a] = plan["albums"]
        ident = a["identification"]
        self.assertEqual((a["status"], ident.get("release_id")), ("ready", KR), pl.preview(plan))
        self.assertNotIn(NL, str(ident.get("suggested") or ""))
        # (ED-2: the pressing's year in the folder's edition, VIN-3 its catalogue number)
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1986 Vinyl Rip 6337 160]"])


class CTY3_PressYearDecidesLast(unittest.TestCase):
    @staticmethod
    def cand(i, year, clues, **kw):
        return dict(cand={"id": i, "source": "musicbrainz", "year": year, "country": "US", "title": "X",
                          "tracks": [1] * 3}, clues=clues, confidence=90, year_ok=False, year_off=None, **kw)

    def test_CTY3_a_stronger_clue_beats_the_press_year_in_a_tie(self):
        for clue in ({"barcode": "0123456789012"}, {"disc TOC": "exact"}, {"catalogue number": "ABC 1"}):
            a = self.cand("A", 1990, clue)
            b = self.cand("B", 2016, {}, press_year=2016, press_year_ok=True)
            self.assertEqual(mc._choose_pressing([a, b])[0]["cand"]["id"], "A", clue)
        # (alike otherwise: the press year decides)
        a = self.cand("A", 1990, {"catalogue number": "ABC 1"})
        b = self.cand("B", 2016, {"catalogue number": "ABC 1"}, press_year=2016, press_year_ok=True)
        pick, why = mc._choose_pressing([a, b])
        self.assertEqual((pick["cand"]["id"], why), ("B", {"date": "the 2016 edition your folder names"}))


class CTY3_PressYearOnlyAPreference(VinBase):
    def test_CTY3_re2015_with_only_the_original_listed_files_as_before(self):
        # (decided 2026-11-21: "Re2015" never holds on its own an album main would file, nor takes
        # YR-1's original-year credit away)
        top = self.rip("1973 - Piano Man (1977, Re2015, US) [Vinyl Rip]", 96000)
        plan = self.moving(SetHttp(mb=[vinyl_release()])).plan(top)
        [a] = plan["albums"]
        self.assertEqual((a["status"], a["identification"].get("release_id")), ("ready", RID), pl.preview(plan))

    def test_CTY3_press_year_breaks_a_tie_between_two_pressings_of_one_number(self):
        a1 = release(NL, [medium(1, '12" Vinyl', TITLES, LENS)], date="1981", country="NL",
                     catno="6337 160", label="Mercury", barcode="")
        a2 = release(KR, [medium(1, '12" Vinyl', TITLES, LENS)], date="1986-11-22", country="NL",
                     catno="6337 160", label="Mercury", barcode="")
        top = self.rip("1973 - Piano Man [Vinyl Rip 6337 160] {Press 1986}", 96000)
        plan = self.moving(SetHttp(mb=[a1, a2])).plan(top)
        [a] = plan["albums"]
        ident = a["identification"]
        got = ident.get("release_id") or str((ident.get("suggested") or {}).get("id") or "")
        self.assertEqual(got, KR, pl.preview(plan))


class VIN4_DescribedComment(VinBase):
    COMMENT = f"Mercury - {CAT}, Re2015, EU (omp30)"

    def http(self):
        rel = vinyl_release()
        rel["date"] = "2015-12-11"                   # (the 2015 reissue "Re2015" names, CTY-3)
        return SetHttp(mb=[rel])

    def test_VIN4_VIN8_the_comment_kept_whole_with_the_line_below_and_its_signature_the_ripper(self):
        import executor
        top = self.rip("1973 - Piano Man (1977, Re2015, US)", 96000, comment=self.COMMENT)
        # (a Vorbis comment ending in brackets reads as the described comment comment:omp30)
        self.assertEqual(tagio.read(next(top.glob("*.flac"))).get("comment:omp30"), [self.COMMENT[:-8]])
        plan = self.moving(self.http()).plan(top)
        [a] = plan["albums"]
        want = self.COMMENT + "\nVinyl rip: omp30 · 24-bit/96 kHz"
        for fe in a["files"]:
            self.assertEqual(fe["changes"].get("comment"), [want], pl.preview(plan))
            self.assertIsNone(fe["changes"].get("comment:omp30", "missing"))
            [row] = [r for r in fe["diff"] if r["field"].startswith("comment")]
            self.assertEqual((row["before"], row["after"]), ([self.COMMENT], [want]))   # (the preview shows it)
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [2015 Vinyl Rip omp30 SRM-1-1184]"])
        executor.Executor(plan).run()
        for fe in a["files"]:
            got = tagio.read(fe["dst"])
            self.assertEqual(got.get("comment"), [want])
            self.assertNotIn("comment:omp30", got)
        # (planned again: nothing to write for it, the folder the same)
        again = self.moving(self.http()).plan(Path(a["files"][0]["dst"]).parent)
        self.assertTrue(all("comment" not in fe["changes"] for b in again["albums"] for fe in b["files"]))
        self.assertEqual(self.folders(again), ["1973 - Piano Man [2015 Vinyl Rip omp30 SRM-1-1184]"])


class VIN4_SeveralDescribedComments(VinBase):
    """Several values ending in brackets: the line goes below one of them, kept byte for byte;
    the others stay exactly as stored (before: "did not read back as written; album put back")."""

    def go(self, comments):
        import executor
        import mutagen.flac
        top = self.rip("1973 - Piano Man [Vinyl Rip 24bit 96khz]", 96000)
        for f in sorted(top.glob("*.flac")):
            tagio.write(f, {"comment": comments})
        stored = list(mutagen.flac.FLAC(next(top.glob("*.flac"))).get("comment"))
        plan = self.moving(self.http()).plan(top)
        [a] = plan["albums"]
        res = executor.Executor(plan).run()
        self.assertEqual(res["status"], "done", res)
        out = []
        for fe in a["files"]:
            out.append(list(mutagen.flac.FLAC(fe["dst"]).get("comment")))
        again = self.moving(self.http()).plan(Path(a["files"][0]["dst"]).parent)
        self.assertTrue(all(not any(k.startswith("comment") for k in fe["changes"])
                            for b in again["albums"] for fe in b["files"]), pl.preview(again))
        return stored, out[0]

    def test_VIN4_two_described_comments(self):
        stored, got = self.go(["foo (a)", "bar (b)"])
        line = "Vinyl rip: 24-bit/96 kHz"
        self.assertEqual(sorted(got), sorted(["foo (a)\n" + line, "bar (b)"]))

    def test_VIN4_one_description_twice(self):
        stored, got = self.go(["foo (a)", "baz(a)"])
        self.assertEqual(sorted(got), sorted(["foo (a)\nVinyl rip: 24-bit/96 kHz", "baz(a)"]))

    def test_VIN4_text_without_a_space_before_its_bracket_byte_for_byte(self):
        stored, got = self.go(["text(desc)"])
        self.assertEqual(got, ["text(desc)\nVinyl rip: 24-bit/96 kHz"])
        stored, got = self.go(["two  spaces   (d)"])
        self.assertEqual(got, ["two  spaces   (d)\nVinyl rip: 24-bit/96 kHz"])


class VIN4_StoredValueReusedOnlyWhenExact(VinBase):
    def test_VIN4_a_role_in_another_case_is_written_anew(self):
        import mutagen.flac
        top = self.rip("1973 - Piano Man [Vinyl Rip 24bit 96khz]", 96000)
        f = next(top.glob("*.flac"))
        for changes in ({"performer:piano": ["Joe"], "performer:Piano": None}, {"performer:piano": ["Joe"]}):
            m = mutagen.flac.FLAC(f)
            m["PERFORMER"] = ["Joe (Piano)"]
            m.save()
            tagio.write(f, changes)
            self.assertEqual(list(mutagen.flac.FLAC(f)["PERFORMER"]), ["Joe (piano)"])
            got = tagio.read(f)
            self.assertEqual({k: got.get(k) or None for k in changes}, {k: v for k, v in changes.items()})
        # (the same description in the same case: the stored text, byte for byte)
        m = mutagen.flac.FLAC(f)
        m["COMMENT"] = ["foo(a)"]
        m.save()
        tagio.write(f, {"comment:a": ["fo"]})
        self.assertEqual(list(mutagen.flac.FLAC(f)["COMMENT"]), ["foo(a)"])


class VIN8_CommentSignature(unittest.TestCase):
    def test_VIN8_a_handle_signing_the_releases_description(self):
        cat = "00602547118295"
        self.assertEqual(vr.comment_signature(["Mercury - 00602547118295, Re2015, EU (omp30)"], cat), "omp30")
        self.assertEqual(vr.comment_signature(["Mercury 00602547118295 (pbthal)"], cat), "PBTHAL")
        for c in ("Mercury - 00602547118295 (180g)", "Mercury 00602547118295 (Remastered)",
                  "Mercury 00602547118295 (EAC)", "Mercury 00602547118295 (rp748)", "Mercury 00602547118295 (CD2)",
                  "Mercury 00602547118295 (24-96)", "Mercury 00602547118295 (uaoa)", "Mercury 00602547118295 (2LP)",
                  "Mercury 00602547118295 (EU)", "Mercury 00602547118295 (45rpm)",
                  "Ripped on my deck (omp30)",                      # (no description of this release)
                  "Mercury - 00602547118295 (omp30) more words"):  # (not at its end)
            self.assertEqual(vr.comment_signature([c], cat), "", c)
        self.assertEqual(vr.comment_signature(["Mercury - 00602547118295 (omp30)"], ""), "")
        for h in ("dsd64", "alac24", "wav24", "fl24", "hd24", "mp3v0", "rip2", "Rip1", "CD2b", "LP1a", "sideA2",
                  "Disc1of2", "Audacity2", "SoX14", "dbpoweramp17", "flac24", "v0"):
            self.assertEqual(vr.comment_signature([f"Mercury - 00602547118295 ({h})"], cat), "", h)
        for h in ("flux9", "spud1", "ripley3", "webby7", "epo1"):   # (a handle merely starting so)
            self.assertEqual(vr.comment_signature([f"Mercury - 00602547118295 ({h})"], cat), h, h)
        # (two different handles: which one ripped it cannot be told; one handle on every file: it)
        self.assertEqual(vr.comment_signature(["Mercury 00602547118295 (omp30)", "Mercury 00602547118295 (zz9x)"],
                                              cat), "")
        self.assertEqual(vr.comment_signature(["Mercury 00602547118295 (omp30)"] * 3, cat), "omp30")


if __name__ == "__main__":
    unittest.main()
