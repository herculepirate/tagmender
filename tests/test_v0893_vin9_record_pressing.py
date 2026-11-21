"""VIN-9 and CTY-4 (docs/MATCHING_RULES.md, decided 2026-11-21).

VIN-9: every pressing of one record master carries the same audio, so the track lengths each
entry lists (typed in per entry, often off the sleeve) never tell its pressings apart, and a
record or tape copy whose pick nothing of the copy names - no barcode or catalogue number of
yours on it, no matrix, no label or country on your scans, no disc ID, link or release ID in the
files - is TIE-1's guess: written in full, marked Partial "pressing guessed". A country your
folder name gives picks among the pressings, but even when it leaves exactly one listed pressing
it is no proof. CDs and downloads keep their rules (a disc ID, a barcode).

CTY-4: a country's code in capitals in a folder name's brackets - a bracket of its own "(US)",
"[JP]", "(1984)(US)", a field "(1984, US)", or before a pressing word "US Press" - is the copy's
country (before, "(1984)(US)[LP]" was read as one field and its US ignored).

Rush - Grace Under Pressure (1984)(US)[LP][24-96][FLAC]: Discogs 1851295 (Germany), whose
lengths were typed off the sleeve, scored 100 against 83.6 for every other pressing and was
filed Resolved, exact, as "[1984 Vinyl Rip 818 476-1 Q]". Synthetic albums and candidates,
mocked providers: no library, no network."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import test_engine as te  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=["CA"])     # (no pressing below is Canadian)
# the audio of the record (an LP side's eight tracks, as ripped)
SECS = [299.0, 306.0, 312.0, 276.0, 302.0, 279.0, 283.0, 345.0]
SLEEVE = [s - 3 for s in SECS]                              # typed off the sleeve: each a few s short
OFF = [s - 3 if i < 3 else s + 25 for i, s in enumerate(SECS)]   # another entry's typing: 3 of 8
FOLDER = "Band - Album (1984)(US)[LP][24-96][FLAC]"
DE, US, US2, BR, GB, FR = "1851295", "9073857", "4767728", "2278816", "1960001", "3715784"


def album(folder=FOLDER, hint="vinyl", **kw):
    a = {"artist": "Band", "title": "Album", "year": 1984, "discs": 1, "folder": folder,
         "media_hint": hint, "media_hint_from": "folder name",
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(SECS)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(cid, country, secs=SECS, formats=("vinyl",), source="discogs", **kw):
    c = {"source": source, "id": cid, "url": f"https://example.invalid/{cid}", "artist": "Band",
         "title": "Album", "year": 1984, "date": "1984", "country": country, "label": "Mercury",
         "catnos": {"818 476-1"}, "barcodes": set(), "disc_count": 1, "formats": set(formats),
         "master_id": "m-1", "release_group": "rg-1",
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)]}
    c.update(kw)
    return c


def pick(a, cands, cfg=CFG, ocr=""):
    return mc.classify([mc.score_candidate(a, c, cfg, ocr) for c in cands], cfg)


class CTY4_FolderCountryCode(unittest.TestCase):
    """match_core.code_countries: the country codes a folder name's brackets give."""

    def codes(self, folder):
        return mc.code_countries(mc._bracket_parts(folder, pressing_only=True))

    def test_CTY4_a_bracket_of_its_own_is_the_copys_country(self):
        for folder in ("Rush - Grace Under Pressure (1984)(US)[LP][24-96][FLAC]", "Band - Album [US]",
                       "Band - Album (1984, US)", "Band - Album (USA)", "Band - Album {US Press 1984}",
                       "Band - Album (Original US Pressing)", "Band - Album [1984 US LP]"):
            self.assertEqual(self.codes(folder), {"US"}, folder)
        self.assertEqual(self.codes("Band - Album (1981)(JP)[LP]"), {"JP"})
        self.assertEqual(self.codes("Band - Album (UK Pressing)"), {"GB"})
        self.assertEqual(self.codes("Band - Album (1987, Re2015, EU)"), {"XE"})

    def test_CTY4_a_titles_word_or_another_bracket_names_none(self):
        for folder in ("Band - Talk to Us (us)", "Band - Album [Us Press]", "Band - Album [24-96][FLAC]",
                       "Band - Album (Live in US 1984)", "Band - Album (Bonus US Tracks)"):
            self.assertEqual(self.codes(folder), set(), folder)

    def test_CTY4_the_folder_country_is_a_clue_and_against_the_others(self):
        a = album()
        us = mc.score_candidate(a, cand(US, "US"), CFG)
        de = mc.score_candidate(a, cand(DE, "Germany"), CFG)
        self.assertIn("in the folder name", us["clues"].get("country", ""))
        self.assertIn("folder name says US, this pressing is from Germany", de["clues_against"])

    def test_CTY4_the_folder_country_picks_among_pressings(self):
        r = pick(album(), [cand(DE, "Germany", SLEEVE), cand(US, "US", OFF), cand(US2, "US", OFF),
                           cand(GB, "UK", OFF)])
        self.assertIn(r["best"]["cand"]["id"], (US, US2), (r["why"], r["best"]["notes"]))

    def test_CTY4_versions_of_the_folders_country_are_fetched_first(self):
        first = ma.version_priority(album(), country="US")
        other = ma.version_priority(album(), country="Germany")
        self.assertLess(first, other)


class VIN9_Matcher(unittest.TestCase):
    """match_core.classify: a record pick nothing of your copy names is a guess."""

    def test_VIN9_lengths_typed_otherwise_tell_no_pressing_apart(self):
        """The Grace Under Pressure case without the folder's country: the entry whose lengths
        were typed off the sleeve is no more your pressing than the others."""
        a = album(folder="Band - Album [LP][24-96][FLAC]")
        r = pick(a, [cand(DE, "Germany", SLEEVE), cand(US, "US", OFF), cand(BR, "Brazil", OFF)])
        self.assertEqual(r["status"], "auto", r["why"])
        g = r["best"].get("tie_guess")
        self.assertTrue(g, (r["margin"], r["best"]["notes"]))
        self.assertEqual(sorted(x["id"] for x in [g["pick"]] + g["others"]), sorted([BR, DE, US]))
        self.assertLess(r["margin"], CFG["auto_min_margin"])

    def test_VIN9_the_folders_country_picks_but_proves_nothing(self):
        r = pick(album(), [cand(DE, "Germany", SLEEVE), cand(US, "US", OFF, date="1984-04-16"),
                           cand(US2, "US", OFF, catnos={"818 476-1 M-1"})])
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn(r["best"]["cand"]["id"], (US, US2))
        g = r["best"]["tie_guess"]
        self.assertEqual(len(g["others"]), 1, g)
        self.assertIn(g["others"][0]["id"], (US, US2))

    def test_VIN9_one_pressing_left_by_the_country_is_still_a_guess(self):
        """Decided 2026-11-21: the databases list a fraction of a record's pressings."""
        r = pick(album(), [cand(DE, "Germany", SLEEVE), cand(US, "US", OFF), cand(GB, "UK", OFF)])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", US), r["why"])
        g = r["best"]["tie_guess"]
        self.assertIn("in the folder name", g["by"])
        self.assertIn("the one listed pressing from there", g["by"])
        self.assertEqual(g["others"], [])

    def test_VIN9_the_only_listed_record_pressing_is_a_guess(self):
        r = pick(album(folder="Band - Album [LP]"), [cand(DE, "Germany")])
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("only listed pressing", r["best"]["tie_guess"]["by"])

    def test_VIN9_a_release_id_in_your_tags_proves_no_pressing(self):
        """Another tagger's ID is a lead (TAG-2), TagMender's own may be its earlier guess: a plan
        made again on a guessed LP whose tags carry the pick's ID stays a guess."""
        for by_tagger in (True, False):
            a = album(folder="Band - Album [LP]", tag_release_discogs={DE}, tags_by_tagger=by_tagger)
            r = pick(a, [cand(DE, "Germany")])
            self.assertEqual(r["status"], "auto", r["why"])
            self.assertTrue(r["best"].get("tie_guess"), (by_tagger, r["best"]["notes"]))
            self.assertEqual(mc.record_proof(r["best"]), "")

    def test_VIN9_a_cd_carrying_the_folders_number_never_leads_a_record_tie(self):
        a = album(folder="Band - Album [LP] [818 476-1]", catnos={mc.norm_code("818 476-1")},
                  catnos_from_folder={mc.norm_code("818 476-1")})
        r = pick(a, [cand(DE, "Germany", SLEEVE, catnos={"818 476-1 Q"}), cand(BR, "Brazil", OFF, catnos={"X-1"}),
                     cand(FR, "France", SLEEVE, formats=("cd",), catnos={"818 476-1"})])
        self.assertNotEqual(r["best"]["cand"]["id"], FR, (r["status"], r["why"]))
        self.assertGreaterEqual(r["margin"], 0)

    def test_VIN9_your_catalogue_number_on_one_stays_exact(self):
        a = album(folder="Band - Album [LP] [818 476-1 M-1]", catnos={mc.norm_code("818 476-1 M-1")},
                  catnos_from_folder={mc.norm_code("818 476-1 M-1")})
        r = pick(a, [cand(DE, "Germany", SLEEVE), cand(US, "US", OFF, catnos={"818 476-1 M-1"})])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", US), (r["why"], r["best"]["notes"]))
        self.assertNotIn("tie_guess", r["best"])

    def test_VIN9_a_catalogue_number_every_pressing_shares_proves_none(self):
        a = album(folder="Band - Album [LP] [818 476-1]", catnos={mc.norm_code("818 476-1")},
                  catnos_from_folder={mc.norm_code("818 476-1")})
        r = pick(a, [cand(DE, "Germany", SLEEVE), cand(BR, "Brazil", OFF), cand(FR, "France", OFF)])
        self.assertTrue(r["best"].get("tie_guess"), (r["status"], r["why"], r["best"]["notes"]))

    def test_VIN9_your_barcode_on_one_stays_exact(self):
        a = album(folder="Band - Album [LP]", barcodes={"042281847615"})
        r = pick(a, [cand(DE, "Germany", OFF, barcodes={"042281847615"}), cand(US, "US", SLEEVE)])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", DE), r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_VIN9_the_country_on_your_scans_names_the_pressing(self):
        r = pick(album(folder="Band - Album [LP]"), [cand(DE, "Germany", OFF), cand(US, "US", OFF)],
                 ocr="Band Album Mercury Made in Germany")
        self.assertEqual(r["best"]["cand"]["id"], DE, r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_VIN9_a_cd_keeps_its_lengths(self):
        """Not a record: lengths still tell two CDs apart (another master), no guess made."""
        a = album(folder="Band - Album [CD]", hint="cd")
        r = pick(a, [cand(DE, "Germany", SECS, formats=("cd",)), cand(US, "US", OFF, formats=("cd",))])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", DE), r["why"])
        self.assertNotIn("tie_guess", r["best"])
        self.assertGreaterEqual(r["margin"], CFG["auto_min_margin"])

    def test_VIN9_a_cd_proven_by_its_disc_id_stays_exact(self):
        toc = "1 8 0 150 1 2 3 4 5 6 7"
        a = album(folder="Band - Album [CD]", hint="cd", tocs={1: toc}, tocs_fit=[1], toc_releases={DE},
                  exact_toc_releases={DE}, exact_toc_media={DE: {1}}, exact_toc_on={DE: {toc: {1}}})
        r = pick(a, [cand(DE, "Germany", formats=("cd",), source="mb"),
                     cand(US, "US", formats=("cd",), source="mb")])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", DE), r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_VIN9_record_proof_names_what_of_your_copy_counts(self):
        s = mc.score_candidate(album(folder="Band - Album [LP]"), cand(DE, "Germany"), CFG)
        self.assertEqual(mc.record_proof(s), "")
        self.assertEqual(mc.record_kind(s), "vinyl")
        self.assertEqual(mc.record_proof(dict(s, clues={"matrix": "matrix 818476-1 on the scans"})), "matrix")
        self.assertEqual(mc.record_proof(dict(s, code="catalogue number (folder name)")),
                         "catalogue number (folder name)")
        self.assertEqual(mc.record_proof(dict(s, code=mc.NOTES_BARCODE)), "")
        self.assertEqual(mc.record_proof(dict(s, clues={"country": "made in Germany on the scans"})), "country")
        self.assertEqual(mc.record_proof(dict(s, clues={"country": "the US in the folder name"})), "")


class Review893(unittest.TestCase):
    """The review of 0.8.93's first cut: your own choice, CD+LP folders, a CD's folder country,
    word-like codes, the margin after a lead, the pressing's year in a bracket of its own."""

    def test_TAG1_the_release_you_chose_is_kept_never_guessed(self):
        for cands in ([cand(US, "US")], [cand(US, "US"), cand(DE, "Germany", OFF)],
                      [cand(US, "US"), cand(DE, "Germany")], [cand(US, "US", OFF), cand(DE, "Germany")]):
            a = album(folder="Band - Album [LP]", tag_release_discogs={US}, tags_by_tagger=False,
                      own_decided=mc.CHOSEN_BY_YOU)
            r = pick(a, cands)
            self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", US), r["why"])
            self.assertNotIn("tie_guess", r["best"])
            self.assertIn("kept the release your files are tagged with (TAG-1)", r["best"]["notes"])

    def test_VIN9_tagmenders_own_guess_replanned_stays_a_guess(self):
        a = album(tag_release_discogs={US}, tags_by_tagger=False, own_decided="")
        r = pick(a, [cand(US, "US", OFF), cand(DE, "Germany", SLEEVE)])
        self.assertEqual(r["best"]["cand"]["id"], US)
        self.assertTrue(r["best"].get("tie_guess"))

    def test_VIN9_a_cd_rip_of_a_cd_and_lp_box_keeps_its_lengths(self):
        for folder, audio in (("Band - Album [CD+LP]", {}), ("Band - Album (Deluxe 2CD + LP)", {}),
                              ("Band - The Live LP (1990) [CD] [FLAC]", {}),
                              ("Band - Album (Vinyl Master) [Qobuz 24-96]", {}),
                              ("Band - Album [LP]", {"cd_frames": True})):
            a = album(folder=folder, hint=mc.media_hint(folder), audio=audio)
            self.assertEqual(mc.record_copy(a), "", folder)
            r = pick(a, [cand(DE, "Germany", formats=("cd", "vinyl")), cand(US, "US", OFF, formats=("cd", "vinyl"))])
            self.assertEqual(r["best"]["cand"]["id"], DE, folder)
            self.assertNotIn("tie_guess", r["best"], folder)
            self.assertGreaterEqual(r["margin"], CFG["auto_min_margin"], folder)
        self.assertEqual(mc.record_copy(album(audio={"not_cd": True})), "vinyl")   # (a 16/44.1 vinyl rip)

    def test_CTY4_a_cds_folder_country_alone_is_a_guess(self):
        a = album(folder="Band - Album (1984)(US)[CD][FLAC]", hint="cd")
        r = pick(a, [cand(US, "US", formats=("cd",)), cand(DE, "Germany", formats=("cd",))])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", US), r["why"])
        self.assertEqual(r["best"]["tie_guess"]["by"], "the US in the folder name")
        self.assertEqual([x["id"] for x in r["best"]["tie_guess"]["others"]], [DE])
        self.assertIn(f"pressing chosen by the US in the folder name (not on {DE})", r["best"]["notes"])

    def test_CTY4_a_cds_folder_country_with_its_barcode_or_other_lengths_stays_exact(self):
        a = album(folder="Band - Album (1984)(US)[CD][FLAC]", hint="cd", barcodes={"042281847615"})
        r = pick(a, [cand(US, "US", formats=("cd",), barcodes={"042281847615"}), cand(DE, "Germany", formats=("cd",))])
        self.assertNotIn("tie_guess", r["best"])
        a = album(folder="Band - Album (1984)(US)[CD][FLAC]", hint="cd")
        r = pick(a, [cand(US, "US", formats=("cd",)), cand(DE, "Germany", OFF, formats=("cd",))])
        self.assertEqual(r["best"]["cand"]["id"], US)
        self.assertNotIn("tie_guess", r["best"])
        r = pick(a, [cand(US, "US", formats=("cd",))])
        self.assertNotIn("tie_guess", r["best"])               # (a CD the folder alone names: as before)

    def test_CTY4_word_like_codes_only_alone_or_before_a_press_word(self):
        codes = lambda f: mc.code_countries(mc._bracket_parts(f, pressing_only=True))  # noqa: E731
        for f in ("X - Y (Remastered, IT)", "X - Y [BE Vinyl]", "X - Y (AT LP)", "X - Y (Live, BE)"):
            self.assertEqual(codes(f), set(), f)
        self.assertEqual(codes("X - Y (IT)"), {"IT"})
        self.assertEqual(codes("X - Y (BE Press)"), {"BE"})
        self.assertEqual(codes("X - Y (IT Pressing)"), {"IT"})
        self.assertEqual(codes("X - Y (1984, US)"), {"US"})

    def test_CTY4_version_priority_skips_a_bracket_of_the_title(self):
        a = album(folder="Band - Tour Diary (JP)")
        self.assertEqual(ma.version_priority(a, country="JP", title="Tour Diary (JP)")[1], 1)
        self.assertEqual(ma.version_priority(a, country="JP", title="Tour Diary")[1], 0)

    def test_VIN9_the_margin_after_your_numbers_lead_is_never_negative(self):
        a = album(folder="Band - Album [LP]", barcodes={"042281847615"})
        r = pick(a, [cand(DE, "Germany", OFF, barcodes={"042281847615"}), cand(US, "US", SLEEVE),
                     cand(BR, "Brazil", SLEEVE)])
        self.assertEqual(r["best"]["cand"]["id"], DE)
        self.assertGreaterEqual(r["margin"], 0)

    def test_CTY3_a_second_year_in_its_own_bracket_is_the_pressings(self):
        self.assertEqual(mc.press_year("Rush - Grace Under Pressure (1984)(2015)(US)[LP][24-96][FLAC]"), 2015)
        self.assertEqual(mc.press_year("Rush - Grace Under Pressure (1984)(US)[LP]"), None)
        self.assertEqual(mc.press_year("Band - Album (2015)(1984)"), None)
        a = album(folder="Band - Album (1984)(2015)(US)[LP][24-96][FLAC]", press_year=2015)
        r = pick(a, [cand(US, "US", OFF, date="1984-04-16"), cand(US2, "US", OFF, year=2015, date="2015-12-04",
                                                                   catnos={"B0022384-01"})])
        self.assertEqual(r["best"]["cand"]["id"], US2, (r["why"], r["best"]["notes"]))
        self.assertTrue(r["best"].get("tie_guess"))
        self.assertLess(ma.version_priority(a, country="US", year="2015-12-04"),
                        ma.version_priority(a, country="US", year="1984"))

    def test_CTY3_the_press_year_picks_between_the_databases_guesses(self):
        """Rush - Grace Under Pressure (1984)(2015)(US): MusicBrainz's guess is the 2015 US
        reissue, Discogs (preferred by Sources) lists only 1984 pressings: the 2015 one is taken."""
        cfg = dict(CFG, prefer_source="discogs")
        a = album(folder="Band - Album (1984)(2015)(US)[LP][24-96][FLAC]", press_year=2015)
        # (MusicBrainz's 1984 entry is not the Discogs one's twin: no pair to carry it across)
        ms = [mc.score_candidate(a, cand("mb-84", "US", source="mb", date="1984-04-12", label="Vertigo",
                                         catnos={"VERH 12"}), cfg),
              mc.score_candidate(a, cand("mb-15", "US", source="mb", year=2015, date="2015-12-04",
                                         catnos={"B0022384-01"}), cfg)]
        ds = [mc.score_candidate(a, cand(US, "US", OFF, date="1984-04-16"), cfg)]
        mb, dg = mc.classify(ms, cfg), mc.classify(ds, cfg)
        mb["scored"], dg["scored"] = ms, ds
        self.assertEqual(mb["best"]["cand"]["id"], "mb-15")
        out = mc.combine(mb, dg, ["CA"])
        self.assertEqual(out["best"]["cand"]["id"], "mb-15", out.get("why"))
        self.assertTrue(out["best"].get("tie_guess"))
        self.assertIn("2015 edition", out["best"]["tie_guess"]["by"])


LP_LENS = [10000, 20000, 30000, 40000]


class VIN9_Engine(te.Base):
    """engine + planner: the Grace Under Pressure case written in full, marked Partial."""

    def rel(self, rid, country, lens, date="1984"):
        r = te.mb_release(rid, "Band V", "Album V", lens=lens, country=country, catno="818 476-1")
        r["label-info"] = [{"label": {"name": "Mercury"}, "catalog-number": "818 476-1"}]
        r["barcode"] = ""
        r["date"] = date
        r["release-group"]["id"] = "rg-lp"
        r["release-group"]["first-release-date"] = "1984"
        r["release-group"]["secondary-types"] = []
        r["media"] = [dict(r["media"][0], format='12" Vinyl')]
        return r

    def eng(self, http):
        e = self.engine(http)
        e.cfg["preferred_countries"] = ["CA"]
        e.cfg["prefer_source"] = ""
        e.use_discogs = False
        return e

    def test_VIN9_the_sleeves_lengths_make_no_exact_pressing(self):
        d = self.album("Band V", "Album V", n=4, folder="Band V - Album V (1984)[LP][24-96][FLAC]")
        sleeve = [ms - 600 for ms in LP_LENS]                         # (each 0.6 s short: agrees)
        off = [LP_LENS[0], LP_LENS[1] + 9000, LP_LENS[2] + 9000, LP_LENS[3] + 9000]
        e = self.eng(te.FakeHttp(mb=[self.rel("mb-de", "DE", sleeve), self.rel("mb-us", "US", off, "1984-04-12")]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "exact"), (p.why, p.evidence))
        self.assertTrue(p.guessed, (p.why, p.evidence))
        self.assertEqual(sorted([p.guessed["ref"]] + [x["ref"] for x in p.guessed["others"]]), ["mb-de", "mb-us"])
        built = pl.build([p], e.profile, "job", extra_roots=[self.lib])
        a = built["albums"][0]
        self.assertTrue(a["identification"]["reason"].startswith("ambiguous_release: pressing guessed: "),
                        a["identification"]["reason"])
        ch = a["files"][0]["changes"]
        self.assertEqual(ch.get("tagmender"), ["Partial"])
        self.assertEqual(ch.get("catalognumber"), ["818 476-1"])          # (written in full)

    def test_VIN9_the_folders_country_picks_the_pressing_still_partial(self):
        d = self.album("Band V", "Album V", n=4, folder="Band V - Album V (1984)(US)[LP][24-96][FLAC]")
        sleeve = [ms - 600 for ms in LP_LENS]
        off = [LP_LENS[0], LP_LENS[1] + 9000, LP_LENS[2] + 9000, LP_LENS[3] + 9000]
        e = self.eng(te.FakeHttp(mb=[self.rel("mb-de", "DE", sleeve), self.rel("mb-us", "US", off, "1984-04-12")]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-us"), (p.why, p.evidence))
        self.assertIn("in the folder name", p.guessed["by"])
        self.assertIn("nothing of your copy proves this pressing", " ".join(p.why))
        built = pl.build([p], e.profile, "job", extra_roots=[self.lib])
        ch = built["albums"][0]["files"][0]["changes"]
        self.assertEqual((ch.get("tagmender"), ch.get("releasecountry")), (["Partial"], ["US"]))


if __name__ == "__main__":
    unittest.main()
