# VIN-10, CAT-10 (docs/MATCHING_RULES.md): a vinyl rip told by its own evidence whatever its folder
# is called - a record player named in the rip notes ("TT: ...", "Cartridge: ..."), a source tag
# naming the record ("SCOURCE=VINYL"), sides beside another sign - is a vinyl rip: named so, never
# "[HD]", its sides making the record count. A catalogue number in another tag name ("SERIAL_NO")
# with a format word after it is read with and without it. (Tool - Lateralus, a 24/96 rip of the
# 2005 US 2LP in a folder "Tool - Lateralus": planned as an "[HD]" download, "disc count 1 vs 2".)
# Synthetic files and releases only: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

RIP_LOG = ("Ripping Info / Technical Log\r\n  Hardware:\r\n\r\n    TT: Clearaudio Emotion modifeyd with “magnet bearing”\r\n"
           "\r\n    Cartridge: Clearaudio Maestro Wood MM\r\n\r\n     Phonopreamplifier: Clearaudio Smart Phono\r\n"
           "  Recording:\r\n    Sony PCM-M10 – 24bit/96kHz\r\nRecord shape\r\nSide A \tEX / NM\r\nSide B \tEX / NM\r\n"
           "Side C \tEX / NM\r\nSide D \tVG\r\n")


def touch_scans(d, names):
    (d / "artwork").mkdir(exist_ok=True)
    for n in names:
        (d / "artwork" / n).write_bytes(b"\xff\xd8\xff\xd9")


class VIN10OwnEvidence(Base):
    def read(self, d):
        return ma.read_album(d, d.parent.name, d.name)

    def test_VIN10_a_record_player_in_the_rip_log_makes_a_vinyl_rip(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        a = self.read(d)
        self.assertEqual(a["media_hint"], "vinyl")
        self.assertEqual(a["media_hint_from"], "the rip notes name the turntable and cartridge")
        self.assertEqual(ma.copy_source(a), "vinyl")
        self.assertEqual(a["vinyl_sides"], 4)            # (the notes' "Side A" .. "Side D")

    def test_VIN10_a_source_tag_naming_the_record_misspelt_too(self):
        for key, value in (("scource", "VINYL"), ("source", "Vinyl LP"), ("RIP SOURCE", "2LP"), ("sorce", "vinyl")):
            with self.subTest(key=key):
                d = self.album("Band S", "Album S", folder=f"Band S - Album S {key}", **{key: value})
                a = self.read(d)
                self.assertEqual(a["media_hint"], "vinyl", key)
                self.assertTrue(a["media_hint_from"].startswith("the files' source tag says"))

    def test_VIN10_no_source_tag_or_value_says_nothing(self):
        for n, (key, value) in enumerate((("course", "vinyl"), ("source", "WEB (vinyl master)"), ("source", "CD"),
                                          ("comment", "vinyl"))):
            with self.subTest(key=key, value=value):
                d = self.album("Band N", "Album N", folder=f"Band N - Album N {n}", **{key: value})
                self.assertEqual(self.read(d)["media_hint"], "")

    def test_VIN10_a_weak_sign_alone_says_nothing(self):
        cases = {"disc A": dict(extra={"disc": "A"}),
                 "side pictures": dict(scans=["side A.JPG", "side B.JPG", "side C.JPG", "side D.JPG"]),
                 "a tape deck": dict(notes="Deck: Nakamichi Dragon\nTape: TDK SA90"),
                 "a deck and two sides": dict(notes="Deck: Revox B215\nSide A\n1. Foo\nSide B\n2. Bar"),
                 "a total time": dict(notes="TT: 44:12\nCartridge: n/a"),
                 "the media tag": dict(extra={"media": "12\" Vinyl"})}
        for name, case in cases.items():
            with self.subTest(name):
                d = self.album("Band W", "Album W", folder=f"Band W - Album W {name}", **case.get("extra", {}))
                touch_scans(d, case.get("scans", ()))
                if case.get("notes"):
                    (d / "info.txt").write_text(case["notes"])
                self.assertEqual(self.read(d)["media_hint"], "", name)

    def test_VIN10_two_weak_signs_make_a_vinyl_rip(self):
        d = self.album("Band T", "Album T", folder="Band T - Album T", disc="A")
        touch_scans(d, ["side A.JPG", "_side B.jpg", "front.JPG"])
        a = self.read(d)
        self.assertEqual(a["media_hint"], "vinyl")
        self.assertIn("pictures of 2 vinyl sides", a["media_hint_from"])
        self.assertIn("disc numbers that are vinyl sides", a["media_hint_from"])

    def test_VIN10_a_tapes_word_vetoes_the_copys_own_evidence(self):
        for name, notes, scans in (("deck", "Deck: Nakamichi ZX-9\nTT: Technics SL-1200\nCartridge: Shure M97", ()),
                                   ("cassette", RIP_LOG + "\nTransferred from the cassette", ()),
                                   ("j-card", RIP_LOG, ("J-card.jpg",)),
                                   ("dolby", "Dolby B off\nside A\nside B", ("side A.jpg", "side B.jpg"))):
            with self.subTest(name):
                d = self.album("Band C", "Album C", folder=f"Band C - Album C {name}", disc="A")
                (d / "info.txt").write_text(notes)
                touch_scans(d, scans)
                self.assertEqual(self.read(d)["media_hint"], "", name)

    def test_VIN10_a_web_folder_name_outranks_the_copys_own_evidence(self):
        d = self.album("Band C", "Album C", folder="Band C - Album C [WEB]")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        self.assertEqual(self.read(d)["media_hint"], "digital")

    def test_VIN10_the_folder_name_still_comes_first(self):
        d = self.album("Band C", "Album C", folder="Band C - Album C [CD]")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        self.assertEqual(self.read(d)["media_hint"], "cd")

    def test_VIN10_whole_cd_sectors_outrank_the_record_player(self):
        import subprocess
        d = self.lib / "Band Q" / "Band Q - Album Q"
        d.mkdir(parents=True)
        for i in range(2):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 50 * i}:duration=1",
                            "-ar", "44100", "-ac", "2", "-sample_fmt", "s16", "-metadata", f"title=T{i}",
                            "-metadata", "album=Album Q", "-metadata", "albumartist=Band Q", "-metadata", f"track={i + 1}",
                            str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        self.assertEqual(self.read(d)["media_hint"], "")


class CAT10TagNames(Base):
    def test_CAT10_serial_no_with_its_format_word_and_the_country_tag(self):
        d = self.album("Band K", "Album K", folder="Band K - Album K", serial_no="61422-3116-1 LP", country="U.S.A.",
                       organization="Volcano Entertainment / Zomba Recording")
        a = ma.read_album(d, d.parent.name, d.name)
        # (compared as written; searched without its format word)
        self.assertEqual(a["tag_catnos"], {"6142231161lp"})
        self.assertEqual(a["catno_search"], ["61422-3116-1"])
        self.assertEqual(a["tag_label"], "Volcano Entertainment / Zomba Recording")
        # (a plain COUNTRY tag: never the release country, and read only on a vinyl copy)
        self.assertEqual(a["tag_country"], "")
        self.assertEqual(a["tag_country_plain"], "")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual(mc.country_code(a["tag_country_plain"]), "US")

    def test_CAT10_the_format_word_is_left_out_whole_only(self):
        for c, bare in (("SRM-1-1184 LP", "SRM-1-1184"), ("XYZ 1 (2LP)", "XYZ 1"), ("ABC 1 CD", "ABC 1"),
                        ("4AD CAD 3CD", "4AD CAD 3CD"), ("ABC-123LP", "ABC-123LP"), ("TOCP 1 MC", "TOCP 1")):
            with self.subTest(c):
                self.assertEqual(ma._CATNO_FORMAT_TAIL.sub("", c).strip(), bare)
        self.assertFalse(mc.catno_same("SPV 79690 CD", "SPV 79690 LP"))

    def test_CAT10_a_plain_country_tag_is_never_against_a_pressing(self):
        # (a Swedish band's tags say COUNTRY=Sweden on a rip of the US pressing: nothing against
        # the US pressing, and the Swedish one's agreement weighs nothing)
        d = self.album("Band K", "Album K", folder="Band K - Album K", country="Sweden")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        a = ma.read_album(d, d.parent.name, d.name)
        dm = ma.load_plugin(_discogs())
        us = mc.score_candidate(a, ma.discogs_candidate(dg_release(5138, "Band K", "Album K", country="US"), dm),
                                dict(mc.DEFAULT_CFG))
        se = mc.score_candidate(a, ma.discogs_candidate(dg_release(5139, "Band K", "Album K", country="Sweden"), dm),
                                dict(mc.DEFAULT_CFG))
        self.assertFalse(any("Sweden" in x or "SE" in x for x in us["clues_against"]), us["clues_against"])
        self.assertIn("tag country", se["clues"])
        self.assertEqual(mc.CLUE_WEIGHTS["tag country"], 0)
        self.assertEqual(us["confidence"], se["confidence"])

    def test_CAT10_the_number_agrees_as_written(self):
        d = self.album("Band K", "Album K", folder="Band K - Album K", serial_no="61422-3116-1 LP")
        a = ma.read_album(d, d.parent.name, d.name)
        for listed in ("61422-3116-1 LP",):
            with self.subTest(listed=listed):
                rel = dg_release(5136, "Band K", "Album K", catno=listed)
                s = mc.score_candidate(a, ma.discogs_candidate(rel, ma.load_plugin(_discogs())), dict(mc.DEFAULT_CFG))
                self.assertIn("catalogue number agrees", s["notes"])
        # (a number a digit longer is another number: never agreement)
        rel = dg_release(5137, "Band K", "Album K", catno="61422-31160-1")
        s = mc.score_candidate(a, ma.discogs_candidate(rel, ma.load_plugin(_discogs())), dict(mc.DEFAULT_CFG))
        self.assertNotIn("catalogue number agrees", s["notes"])


def _discogs():
    import companions
    return str(companions.discogs_dir())


def two_lps(rid=5135, artist="Band L", title="Album L"):
    r = dg_release(rid, artist, title)
    for t, pos in zip(r["tracklist"], ("A1", "B1", "C1")):
        t["position"] = pos
    r["formats"] = [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}]
    return r


class VIN10Sides(Base):
    def scored(self, d, rel):
        a = ma.read_album(d, d.parent.name, d.name)
        return a, mc.score_candidate(a, ma.discogs_candidate(rel, ma.load_plugin(_discogs())), dict(mc.DEFAULT_CFG))

    def test_VIN10_four_sides_make_the_two_records(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        a, s = self.scored(d, two_lps())
        self.assertEqual(a["discs"], 1)
        self.assertIn("2 records (4 vinyl sides), like the release", s["notes"])
        self.assertFalse(any(n.startswith("disc count") for n in s["notes"]), s["notes"])
        self.assertFalse(s["discs_off"])

    def test_VIN10_a_rip_numbered_as_one_disc_says_nothing_of_its_records(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L [Vinyl Rip]")
        a, s = self.scored(d, two_lps())
        self.assertEqual(a["vinyl_sides"], 0)
        self.assertFalse(any("disc count" in n or "records" in n for n in s["notes"]), s["notes"])
        self.assertFalse(s["discs_off"])

    def test_VIN10_sides_against_another_record_count(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        rel = two_lps()
        rel["formats"] = [{"name": "Vinyl", "qty": "3", "descriptions": ["LP"]}]
        _a, s = self.scored(d, rel)
        self.assertTrue(s["discs_off"])
        self.assertIn("2 records (4 vinyl sides) vs 3", s["notes"])

    def test_VIN10_a_2lp_tagged_by_disc_with_sides_on_each(self):
        # (disc tags 1 and 2, sides A/B on each: 2 records, never 1 - against a 2LP and a 1LP)
        d = self.lib / "Band L" / "Band L - Album L [Vinyl Rip]"
        d.mkdir(parents=True)
        import subprocess
        for k, (disc, name) in enumerate(((1, "A1 T0"), (1, "B1 T1"), (2, "A1 T2"), (2, "B1 T3"))):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 50 * k}:duration=2",
                            "-ar", "8000", "-ac", "1", "-metadata", f"title=T{k}", "-metadata", "album=Album L",
                            "-metadata", "albumartist=Band L", "-metadata", f"track={k % 2 + 1}",
                            "-metadata", f"disc={disc}", str(d / f"{name}.flac"), "-y"], check=True)
        for disc in ("Disc 1", "Disc 2"):
            (d / disc).mkdir()
            for side in "AB":
                (d / disc / f"side {side}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        a = ma.read_album(d, d.parent.name, d.name)
        self.assertEqual((a["discs"], a["vinyl_sides"]), (2, 4))
        self.assertEqual(mc.vinyl_records(a), 2)
        self.assertIs(mc.vinyl_discs_fit(a, {"disc_count": 2, "formats": ["vinyl"]}), True)
        self.assertIs(mc.vinyl_discs_fit(a, {"disc_count": 1, "formats": ["vinyl"]}), False)
        # (no sides shown, two disc tags: still 2 records)
        self.assertIs(mc.vinyl_discs_fit(dict(a, vinyl_sides=0), {"disc_count": 2, "formats": ["vinyl"]}), True)

    def test_VIN10_a_cd_rip_counts_discs_as_before(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        rel = two_lps()
        rel["formats"] = [{"name": "CD", "qty": "2"}]
        _a, s = self.scored(d, rel)
        self.assertIn("disc count 1 vs 2", s["notes"])


class VIN10Named(Base):
    def test_VIN10_the_engine_passes_the_copys_media_to_the_plan(self):
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        eng = self.engine(FakeHttp(dg=[two_lps()]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.copy_media, "vinyl")
        # (a release you chose, a split's version, an album in parts: read alone)
        self.assertEqual(ma.copy_vinyl(d, sorted(d.glob("*.flac"))), "vinyl")
        self.assertEqual(ma.copy_vinyl(self.album("Band H", "Album H", folder="Band H - Album H"),
                                       []), "")

    def test_VIN10_an_album_known_by_its_own_evidence_is_a_vinyl_rip(self):
        import plan as planmod
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        (d / "rip log.txt").write_text(RIP_LOG, encoding="utf-8")
        kept = sorted(d.glob("*.flac"))
        targets = {f: {"album": ["Album L"]} for f in kept}
        self.assertEqual(planmod.Planner._vinyl_rip(NS(source_dir=str(d)), kept, targets, {}, True), {})
        rip = planmod.Planner._vinyl_rip(NS(source_dir=str(d), copy_media="vinyl"), kept, targets, {}, True)
        self.assertEqual(rip["equipment"], "Clearaudio Emotion modifeyd, Clearaudio Maestro Wood MM")
        import naming
        tags = {"~edition_hd": ["1"], "~edition_unproven": ["1"], "~rip_vinyl": ["1"]}
        self.assertEqual(naming.edition(tags), "")
        self.assertEqual(naming.folder_edition(tags), "Vinyl Rip")

    def test_VIN10_the_grouping_mark_of_a_vinyl_rip_by_its_evidence(self):
        import marks
        self.assertEqual(marks.of_album([{}], [], "/x/Band L - Album L", vinyl=True), marks.VINYL)
        self.assertEqual(marks.of_album([{}], [], "/x/Band L - Album L"), "")

    def test_VIN3_a_format_word_after_the_number_stays_out_of_the_folder(self):
        import vinylrip
        d = self.album("Band L", "Album L", folder="Band L - Album L")
        r = vinylrip.describe(d, sorted(d.glob("*.flac")), "61422-3116-1 LP", notes="")
        self.assertEqual(r["catno"], "61422-3116-1")
        self.assertEqual(vinylrip.describe(d, [], "832-464-1 Q-1", notes="")["catno"], "832-464-1 Q-1")

    def test_VIN4_a_tt_line_names_the_turntable(self):
        import vinylrip
        self.assertEqual(vinylrip.equipment("TT: Technics SL-1210\nCartridge: Ortofon 2M Blue"),
                         "Technics SL-1210, Ortofon 2M Blue")


def _scored(src, rid, country, year, code="catalogue number", fmt="vinyl", title="Album L"):
    cand = {"source": src, "id": rid, "country": country, "year": year, "formats": [fmt], "title": title,
            "tracks": [{"title": f"T{i}", "disc": 1 + i // 2} for i in range(4)], "catnos": {"6142231161lp"},
            "barcodes": set(), "label": "Volcano", "url": "", "disc_count": 2}
    return {"cand": cand, "record_copy": "vinyl", "tracks_ok": True, "code": code, "clues": {},
            "confidence": 93.2, "notes": [], "artist_sim": 1.0, "title_sim": 1.0}


class VIN11SharedNumber(unittest.TestCase):
    def combined(self, best, *rest):
        mb = {"status": "auto", "best": best, "scored": [best]}
        dg = {"status": "auto", "best": rest[0] if rest else best, "scored": list(rest)}
        return mc.shared_record_number({"status": "auto", "best": best, "scored": [best]}, mb, dg)

    def test_VIN11_a_number_two_pressings_carry_is_a_guess(self):
        # (Tool - Lateralus: MusicBrainz's 2005 US and Discogs's 2020 Worldwide both list 61422-3116-1 LP)
        out = self.combined(_scored("mb", "ccc3", "US", 2005), _scored("discogs", "16280930", "Worldwide", 2020))
        guess = out["best"]["tie_guess"]
        self.assertTrue(guess["by"].startswith("catalogue number shared by 2 pressings"), guess)
        self.assertEqual([o["id"] for o in guess["others"]], ["16280930"])

    def test_VIN11_one_pressing_in_both_databases_is_one(self):
        # (the 2005 US in MusicBrainz and Discogs, and an undated US entry: one pressing - proof)
        out = self.combined(_scored("mb", "ccc3", "US", 2005), _scored("discogs", "513594", "US", 2005),
                            _scored("discogs", "10346704", "US", None))
        self.assertNotIn("tie_guess", out["best"])

    def test_VIN11_a_cd_carrying_the_number_or_other_proof_changes_nothing(self):
        out = self.combined(_scored("mb", "ccc3", "US", 2005), _scored("discogs", "9533089", "US", 2001, fmt="cd"))
        self.assertNotIn("tie_guess", out["best"])
        best = _scored("mb", "ccc3", "US", 2005, code="barcode")
        self.assertNotIn("tie_guess", self.combined(best, _scored("discogs", "16280930", "Worldwide", 2020))["best"])
        best = _scored("mb", "ccc3", "US", 2005)
        best["clues"] = {"matrix": "matrix 61422-31160-1-A on the scans"}
        self.assertNotIn("tie_guess", self.combined(best, _scored("discogs", "16280930", "Worldwide", 2020))["best"])

    def test_VIN11_a_cd_copy_is_never_guessed_so(self):
        best = _scored("mb", "ccc3", "US", 2005)
        best["record_copy"] = ""
        best["copy_kind"] = "cd"
        self.assertNotIn("tie_guess", self.combined(best, _scored("discogs", "16280930", "Worldwide", 2020))["best"])

    def test_VIN11_the_year_after_the_albums_bare_year_is_the_pressings(self):
        self.assertEqual(mc.press_year("Tool - 2001 - Lateralus (2020) [2LP 24-192]"), 2020)
        self.assertEqual(mc.press_year("2001 - Lateralus (2020)"), 2020)
        self.assertIsNone(mc.press_year("Lateralus (2020)"))
        self.assertIsNone(mc.press_year("Band - 2001 - Album (1999)"))
        self.assertIsNone(mc.press_year("Band - 2001 Odyssey (2020)"))

    def test_VIN11_a_barcode_is_no_catalogue_number_in_the_folder(self):
        import plan as planmod
        import tempfile
        d = Path(tempfile.mkdtemp()) / "Band L - Album L [Vinyl Rip]"
        d.mkdir()
        f = d / "01.flac"
        f.write_bytes(b"")
        for listed, want in ((["0614223116013"], ""), (["0614223116013", "61422-31160-1"], "61422-31160-1"),
                             (["SRM-1-1079"], "SRM-1-1079")):
            with self.subTest(listed=listed):
                targets = {f: {"media": ["12\" Vinyl"], "catalognumber": listed}}
                rip = planmod.Planner._vinyl_rip(NS(source_dir=str(d)), [f], targets, {"media": ["12\" Vinyl"]}, False)
                self.assertEqual(rip["catno"], want)


class VIN11Twins(unittest.TestCase):

    def test_VIN11_records_of_other_years_are_no_twins(self):
        a = {"source": "mb", "id": "ac12", "country": "US", "year": 2005, "formats": {"vinyl"},
             "catnos": {"6142231161lp"}, "barcodes": {"614223116013"}, "tracks": [], "label": "Volcano"}
        b = dict(a, source="discogs", id="16280930", country="Worldwide", year=2020)
        self.assertFalse(mc.same_pressing(a, b))
        self.assertTrue(mc.same_pressing(a, dict(b, year=2005)))


class VIN11OnePressing(unittest.TestCase):
    def n(self, best, *rest):
        out = mc.shared_record_number({"status": "auto", "best": best, "scored": [best]},
                                      {"status": "auto", "best": best, "scored": [best]},
                                      {"status": "auto", "best": rest[0], "scored": list(rest)})
        g = out["best"].get("tie_guess")
        return int(g["by"].split("shared by ")[1].split()[0]) if g else 1

    def test_VIN11_a_blank_country_or_a_region_is_any_country(self):
        self.assertEqual(self.n(_scored("mb", "a", "", 2005), _scored("discogs", "1", "US", 2005)), 1)
        self.assertEqual(self.n(_scored("mb", "a", "XE", 2005), _scored("discogs", "1", "Germany", 2005)), 1)
        self.assertEqual(self.n(_scored("mb", "a", "GB", 1976), _scored("discogs", "1", "UK", 1976)), 1)

    def test_VIN11_a_year_apart_is_one_pressing_ten_years_two(self):
        self.assertEqual(self.n(_scored("mb", "a", "US", 1976), _scored("discogs", "1", "US", 1977)), 1)
        self.assertEqual(self.n(_scored("mb", "a", "US", "1976"), _scored("discogs", "1", "US", None)), 1)
        self.assertEqual(self.n(_scored("mb", "a", "US", 1976), _scored("discogs", "1", "US", 1985)), 2)
        self.assertEqual(self.n(_scored("mb", "a", "US", 1976), _scored("discogs", "1", "Germany", None)), 2)

    def test_VIN11_the_release_you_chose_stays_yours(self):
        best = _scored("mb", "a", "US", 1976)
        best["own_decided"] = mc.CHOSEN_BY_YOU
        self.assertEqual(self.n(best, _scored("discogs", "1", "US", 1985)), 1)

    def test_VIN11_twins_a_year_apart_need_their_number_and_barcode_or_label(self):
        a = {"source": "mb", "id": "x", "country": "US", "year": 1976, "formats": {"vinyl"}, "catnos": {"srm11079"},
             "barcodes": set(), "tracks": [], "label": "Mercury"}
        self.assertTrue(mc.same_pressing(a, dict(a, source="discogs", id="1", year=1977)))
        self.assertFalse(mc.same_pressing(a, dict(a, source="discogs", id="1", year=1977, label="Anthem")))
        self.assertFalse(mc.same_pressing(a, dict(a, source="discogs", id="1", year=1978)))


class VIN10RecordCopy(unittest.TestCase):
    def test_VIN10_a_cd_rip_of_a_cd_and_lp_set_is_no_vinyl_rip(self):
        import marks
        for name in ("Album (Deluxe 2CD + LP)", "Album [CD+LP]", "The Live LP (1990) [CD]",
                     "Album (Vinyl Master) [Qobuz 24-96]"):
            with self.subTest(name):
                self.assertFalse(mc.folder_says_vinyl(name))
                self.assertEqual(marks.of_album([{}], [], f"/x/{name}"), "")
        self.assertTrue(mc.folder_says_vinyl("Album [Vinyl Rip]"))
        self.assertEqual(mc.record_copy({"media_hint": "vinyl", "folder": "Album (Deluxe 2CD + LP)"}), "")


class VIN11PressYearFetch(Base):
    class Http:
        def __init__(self, rels):
            self.rels, self.calls = {r["id"]: r for r in rels}, []

        def get_json(self, url, host):
            self.calls.append(url)
            if "/database/search" in url:
                return {"results": [{"id": 1, "master_id": 99, "title": "Band M - Album M", "year": "2005"}]}
            if "/masters/99/versions" in url:
                return {"versions": [{"id": i, "released": str(r["year"]), "country": "US", "format": "LP, Album",
                                      "catno": "", "title": "Album M"} for i, r in self.rels.items() if i != 1]}
            tail = url.rsplit("/", 1)[1].split("?")[0]
            return self.rels.get(int(tail)) if tail.isdigit() else None

    def fetched(self, folder, fmt="Vinyl"):
        d = self.album("Band M", "Album M", folder=folder)
        a = ma.read_album(d, d.parent.name, d.name)
        rels = []
        for rid, year in ((1, 2005), (5, 2020), (2, 2020), (3, 2020), (4, 2011)):
            r = dg_release(rid, "Band M", "Album M")
            if rid == 5:                          # (an unofficial 2020 pressing listed first: read past it)
                r["formats"] = [{"name": fmt, "qty": "1", "descriptions": ["Unofficial Release"]}]
            r["year"], r["released"] = year, str(year)
            if rid != 5:
                r["formats"] = [{"name": fmt, "qty": "1", "descriptions": ["LP" if fmt == "Vinyl" else "Album"]}]
            r["master_id"] = 99
            rels.append(r)
        http = self.Http(rels)
        cfg = dict(mc.DEFAULT_CFG)
        cfg.update(getattr(ma, "DEFAULT_SEARCH_CFG", {}))
        try:
            ma.discogs_search(http, a, cfg, ma.load_plugin(_discogs()))
        except Exception:
            pass
        return a, http.calls

    def test_VIN11_a_record_copys_press_year_reads_the_masters_versions_until_found(self):
        a, calls = self.fetched("Band M - 2001 - Album M (2020) [Vinyl Rip]")
        self.assertEqual(a["press_year"], 2020)
        self.assertTrue(any("/masters/99/versions" in c for c in calls), calls)
        self.assertTrue(any(c.endswith("/releases/2") for c in calls), calls)
        self.assertFalse(any(c.endswith("/releases/3") for c in calls), calls)      # (stopped at the first official 2020)

    def test_VIN11_a_cds_press_year_costs_no_fetch(self):
        # (the CD found by its search, of its track count and format: only the year is missing)
        _a, calls = self.fetched("Band M - 2001 - Album M (2020) [CD]", fmt="CD")
        self.assertFalse(any("/masters/99/versions" in c for c in calls), calls)


if __name__ == "__main__":
    unittest.main()
