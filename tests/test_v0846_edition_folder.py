"""EDN-2: an edition your copy names by its year and label - the folder's "(2017 AP ISO)", an
SACD-Ripper log's "Analogue Productions - <Title>.iso" - is not a pressing of another label and
year: such a pick's album is certain, its pressing is not (Album only, no label, no pressing date).
Versions of one disc tagged apart ("[SACD 2ch DSD64]" beside "[HD]") are still one album's versions.
Synthetic FLACs made by ffmpeg, mocked Discogs responses."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402
from types import SimpleNamespace  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

AP_FOLDER = "1969 - Album E (1968) [SACD] (2017 AP ISO)"
RIPPER_LOG = ("16908363[0x102004b]: SACD-Ripper Version 0.21\n"
              "16908363[0x102004b]: Queuing raw: /dev_usb000/{iso}.iso, start_lsn: 0, length_lsn: 1664245\n"
              "16908363[0x102004b]: disc ejected..\n")


def labelled(rid, title, label, year, artist="Band E"):
    r = dg_release(rid, artist, title)
    r["labels"] = [{"name": label, "catno": "LBL 1"}]
    r["year"], r["released"] = year, f"{year}-03-01"
    return r


class EDI_FolderWords(unittest.TestCase):
    def test_EDI_year_beside_an_audiophile_labels_short_form_is_the_edition_year(self):
        self.assertEqual(mc.edition_year(AP_FOLDER), 2017)
        self.assertIsNone(mc.pressing_year(AP_FOLDER))             # (EDN-1's edition year is unchanged)
        self.assertEqual(mc.edition_labels(AP_FOLDER), ["Analogue Productions"])
        self.assertEqual(mc.edition_labels("X [SACD] (2015 AF Remaster ISO)"), ["Audio Fidelity"])
        self.assertEqual(mc.edition_year("X [2013 MFSL UDSACD]"), 2013)
        self.assertEqual(mc.edition_labels("X [2013 MFSL UDSACD]"), ["Mobile Fidelity Sound Lab"])
        self.assertEqual(mc.edition_labels("X [Hybrid SACD 2016 Audio Fidelity AFZ241]"), ["Audio Fidelity"])

    def test_EDI_mixed_case_mofi_and_the_year_beside_the_label(self):
        self.assertEqual(mc.edition_labels("X (2019 MoFi)"), ["Mobile Fidelity Sound Lab"])
        self.assertEqual(mc.edition_year("X (2019 MoFi)"), 2019)
        self.assertEqual(mc.edition_labels("X (2019 mofi)"), [])
        self.assertEqual(mc.edition_year("X [1969/2017 AP ISO]"), 2017)

    def test_EDI_a_rip_logs_hex_dump_is_no_barcode(self):
        log = ("16908363[0x102004b]: device_info: 80 00 00 00 10 13 de 70 80 00 00 00 00 5d 94 00\n"
               "16908363[0x102004b]: config: 00 00 00 0c 00 00 00 10 ff 41 01 04 00 00 00 00\n")
        self.assertEqual(mc.codes_from_ocr_text(log)["barcodes"], [])
        self.assertEqual(mc.codes_from_ocr_text("Barcode 5 013929 020160")["barcodes"], ["5013929020160"])

    def test_EDI_no_year_or_no_label_names_no_edition(self):
        self.assertIsNone(mc.edition_year("X [MFSL Vinyl Rip 24bit 96khz]"))
        self.assertEqual(mc.edition_labels("X [MFSL Vinyl Rip 24bit 96khz]"), [])     # (no year beside it)
        self.assertIsNone(mc.edition_year("X [2014 SACD]"))
        self.assertEqual(mc.edition_labels("Map Of The World (2017 ap iso)"), [])     # (capitals only)
        self.assertEqual(mc.edition_labels("X (1968)"), [])

    def test_EDI_label_names_are_whole(self):
        self.assertFalse(mc.label_named("Audio Fidelity", "Mobile Fidelity Sound Lab"))
        self.assertTrue(mc.label_named("Analogue Productions", "Analogue Productions (2)"))
        self.assertTrue(mc.label_named("Audio Fidelity", "Sony Music; Audio Fidelity"))
        self.assertFalse(mc.label_named("Analogue Productions", ""))


class EDI_Album(Base):
    def make(self, folder, sub=None, album="Album E", log_iso=None):
        top = self.lib / "Band E" / folder
        d = top / sub if sub else top
        d.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            meta = {"title": f"T{i}", "album": album, "albumartist": "Band E", "track": str(i + 1)}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *args, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        if log_iso:
            (top / "sacd_log.txt").write_text(RIPPER_LOG.format(iso=log_iso))
        return top, d

    # ---- the SACD-Ripper log's disc image -------------------------------------------------
    def test_EDI_rip_log_names_the_label_also_for_a_version_folder_read_with_the_sets_name(self):
        top, d = self.make("2017 - Album E", "2ch_SACD", log_iso="Analogue Productions - Album E")
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), ["Analogue Productions"])
        self.assertEqual(ma.read_album(d, "Band E", top.name)["copy_labels"], ["Analogue Productions"])
        self.assertEqual(ma.iso_labels(d, d.name, ["Album E"]), [])   # (read alone: its own folder only)

    def test_EDI_rip_log_named_after_the_artist_or_another_title_names_no_label(self):
        top, d = self.make("2017 - Album E", log_iso="Band E - Album E")
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), [])
        (top / "sacd_log.txt").write_text(RIPPER_LOG.format(iso="Analogue Productions - Another Album"))
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), [])
        (top / "sacd_log.txt").write_text("Queuing raw: /dev_usb000/Analogue Productions - Album E.iso\n")
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), [])  # (no SACD tool's log)

    def test_EDI_rip_log_short_forms_map_to_their_label_and_nothing_else_is_one(self):
        top, d = self.make("2017 - Album E", log_iso="MFSL - Album E")
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), ["Mobile Fidelity Sound Lab"])
        for iso in ("Various - Album E", "BS&T - Album E", "CAPP 085 SA - Album E", "ap - Album E"):
            (top / "sacd_log.txt").write_text(RIPPER_LOG.format(iso=iso))
            self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), [], iso)

    def test_EDI_rip_log_of_any_name_and_a_windows_path_with_spaces(self):
        top, d = self.make("2017 - Album E")
        (top / "rip.log").write_text("SACD-Ripper Version 0.21\nQueuing raw: D:\\My Rips\\Analogue Productions - "
                                     "Album E.iso, start_lsn: 0\n")
        self.assertEqual(ma.iso_labels(d, top.name, ["Album E"]), ["Analogue Productions"])

    def test_EDI_rip_log_short_form_of_the_picks_own_label_stays_exact(self):
        # ("MFSL - Album E.iso" against Mobile Fidelity's entry of another year)
        _top, d = self.make("2017 - Album E [2017 Remaster]", log_iso="MFSL - Album E")
        eng = self.engine(FakeHttp(dg=[labelled(917, "Album E", "Mobile Fidelity Sound Lab", 2016)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.tier, "album_only", p.why)
        self.assertNotIn("your copy names", " ".join(p.why))

    # ---- EDN-2: the pick's label and year are another pressing's -----------------------------
    def test_EDI_another_labels_pressing_of_another_year_is_album_only(self):
        _top, d = self.make("2017 - Album E (2017 AP ISO)")
        eng = self.engine(FakeHttp(dg=[labelled(911, "Album E", "Audio Fidelity", 2014)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "album_only"), p.why)
        self.assertFalse(p.release_id)
        self.assertTrue(all("label" not in t and "catalognumber" not in t for t in p.targets))
        self.assertTrue(all("2014" not in "".join(t.get("date") or []) for t in p.targets))
        self.assertIn("your copy names the 2017 Analogue Productions edition", " ".join(p.why))

    def test_EDI_another_labels_pressing_two_years_off_is_album_only_too(self):
        # (pressing_year_tolerance is no excuse beside a label that differs: AF 2015 vs "(2017 AP ISO)")
        _top, d = self.make("1968 - Album E (1968) [SACD] (2017 AP ISO)")
        eng = self.engine(FakeHttp(dg=[labelled(916, "Album E", "Audio Fidelity", 2015)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "album_only", p.why)

    def test_EDI_the_rip_logs_label_with_the_folders_year_is_album_only(self):
        _top, d = self.make("2017 - Album E [2017 Remaster]", log_iso="Analogue Productions - Album E")
        eng = self.engine(FakeHttp(dg=[labelled(912, "Album E", "Audio Fidelity", 2014)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "album_only", p.why)

    def test_EDI_the_named_labels_own_pressing_stays_exact(self):
        # the trap: "[2013 MFSL UDSACD]" names the label the pick is on - whatever year it is dated
        _top, d = self.make("2005 - Album E [2013 MFSL UDSACD]")
        eng = self.engine(FakeHttp(dg=[labelled(913, "Album E", "Mobile Fidelity Sound Lab", 2005)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "exact", p.why)

    def test_EDI_the_named_editions_year_stays_exact(self):
        _top, d = self.make("2005 - Album E (2005 AP ISO)")
        eng = self.engine(FakeHttp(dg=[labelled(914, "Album E", "Audio Fidelity", 2005)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "exact", p.why)

    def test_EDI_a_year_without_a_label_changes_nothing(self):
        _top, d = self.make("2005 - Album E [2017 Remaster]")
        eng = self.engine(FakeHttp(dg=[labelled(915, "Album E", "Audio Fidelity", 2014)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertNotEqual(p.tier, "album_only", p.why)
        self.assertNotIn("your copy names", " ".join(p.why))

    def test_EDI_album_only_keeps_the_albums_original_date(self):
        # (the tied pressings include the 1968 original: its year is the album's, not the pick's)
        p = SimpleNamespace(targets=[{"album": ["A"], "date": ["2014-03-01"], "originaldate": ["1968"],
                                      "label": ["Audio Fidelity"], "releasecountry": ["US"]}],
                            source_dir=str(self.tmp), provenance={}, why=[], release_id="x", url="")
        tied = [{"source": "discogs", "id": "1", "label": "Audio Fidelity", "country": "US", "formats": ["sacd"],
                 "year": 2014, "layout": [3]},
                {"source": "discogs", "id": "2", "label": "Columbia", "country": "US", "formats": ["vinyl"],
                 "year": 1968, "layout": [3]}]
        eng = engine.Engine.__new__(engine.Engine)
        eng._tied_recordings_agree = lambda *a: False
        out = eng._album_only(p, {"tied": tied, "pick_year": 2014,
                                  "edition": "your copy names the 2017 AP edition; AF 2014 is another pressing"})
        self.assertEqual(out.targets[0].get("date"), ["1968"])
        self.assertNotIn("label", out.targets[0])
        self.assertNotIn("releasecountry", out.targets[0])

    def test_EDI_album_only_of_another_media_writes_none_of_their_media(self):
        _top, d = self.make("2017 - Album E (2017 AP ISO)")
        eng = self.engine(FakeHttp(dg=[labelled(918, "Album E", "Audio Fidelity", 2014)]))
        with mock.patch.object(ma, "tie_source", return_value="sacd"):
            p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "album_only", p.why)
        self.assertTrue(all("media" not in t and "tracknumber" not in t for t in p.targets), p.targets[0])

    # ---- LEN-3: lengths a number excuses -------------------------------------------------
    def test_EDI_LEN3_seventy_percent_under_your_catalogue_number_is_not_held_where_fifty_was_not(self):
        d = self.lib / "Band E" / "1990 - Album L [LBL 51234]"
        d.mkdir(parents=True)
        for i in range(10):
            meta = {"title": f"T{i}", "album": "Album L", "albumartist": "Band E", "track": str(i + 1)}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 20 * i}:duration={10 + i}", "-ar", "8000", "-ac", "1",
                            *args, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        for agree, want in ((7, "exact"), (5, "exact")):         # (70% and 50% of the lengths agree)
            durs = tuple(f"0:{10 + i:02d}" if i < agree else "4:00" for i in range(10))
            r = dg_release(919 + agree, "Band E", "Album L", durs=durs, catno="LBL 51234")
            r["tracklist"] = [dict(t, title=f"T{i}") for i, t in enumerate(r["tracklist"])]
            eng = self.engine(FakeHttp(dg=[r]))
            p = eng.identify(eng.albums_in(d)[0])
            self.assertEqual(p.tier, want, (agree, p.why, p.confidence))
            self.assertLess(p.confidence, 90, agree)

    # ---- the album's own scans report after its discs were read alone ----------------------
    def test_EDI_a_sets_scans_report_is_its_own_not_its_last_discs(self):
        top = self.lib / "Band E" / "2005 - Album S"
        for disc in ("CD1", "CD2"):
            self.make(f"2005 - Album S", disc, album="Album S")
        best = {"source": "mb", "id": "a", "mb_id": "a", "url": "u", "confidence": 88.5, "notes": []}
        whole = {"status": "review", "why": ["confidence 88.5 < 90.0"], "best": best,
                 "scans": {"state": "proven", "found": ["catalogue_number"], "against": []}, "via": "scans"}
        disc = {"status": "review", "why": ["confidence 70.0 < 90.0"], "best": dict(best, id="b", mb_id="b"),
                "scans": {"state": "unread", "found": [], "against": []}, "via": ""}
        answers = iter([whole] + [disc] * 10)
        eng = self.engine(FakeHttp(mb=[{"id": "x", "title": "none", "media": [{"tracks": []}]}]))
        with mock.patch.object(ma, "process", side_effect=lambda *a, **k: next(answers)):
            p = eng.identify(eng.albums_in(top)[0])
        self.assertEqual(p.tier, "unresolved")
        self.assertEqual((p.scans or {}).get("state"), "proven", p.scans)

    # ---- version folders tagged apart -----------------------------------------------------
    def test_EDI_version_folders_tagged_apart_by_their_marks_are_one_album(self):
        top, _d = self.make(AP_FOLDER.replace("Album E", "Album V"), "2ch_SACD", album="Album V [SACD 2ch DSD64]")
        self.make(top.name, "6ch_SACD", album="Album V [HD]")
        self.assertEqual([x.name for x in engine.version_dirs(top)], ["2ch_SACD", "6ch_SACD"])
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [(top.name, 6)])

    def test_EDI_a_version_tagged_by_an_earlier_run_as_a_vinyl_rip_is_still_a_version(self):
        top, _d = self.make("1968 - Album V (2017 AP ISO)", "2ch_SACD", album="Album V [1980 Vinyl Rip]")
        self.make(top.name, "5ch_SACD", album="Album V [SACD DST64 5ch 24bit 352khz]")
        self.assertEqual([x.name for x in engine.version_dirs(top)], ["2ch_SACD", "5ch_SACD"])

    def test_EDI_a_mark_with_a_catalogue_number_is_still_a_version_mark(self):
        top, _d = self.make("1968 - Album V (2015 AF Remaster ISO)", "2ch_SACD",
                            album="Album V [SACD AFZ5 198 2ch DSD64 24bit 352khz]")
        self.make(top.name, "6ch_SACD", album="Album V [HD]")
        self.assertEqual([x.name for x in engine.version_dirs(top)], ["2ch_SACD", "6ch_SACD"])

    def test_EDI_version_folders_of_two_albums_stay_apart(self):
        top, _d = self.make("2001 - Two", "2ch_SACD", album="Album V [SACD]")
        self.make(top.name, "6ch_SACD", album="Album W [HD]")
        self.assertEqual(engine.version_dirs(top), [])

    def test_EDI_version_folders_whose_marks_are_titles_stay_apart(self):
        for n, (a, b) in enumerate((("Album V [Remastered]", "Album V [Live]"), ("Live (Volume 1)", "Live (Volume 2)"),
                                    ("Album V (Mono)", "Album V (Stereo)"), ("Live (2)", "Live (3)"))):
            top, _d = self.make(f"200{n} - Set {n}", "2ch", album=a)
            self.make(top.name, "6ch", album=b)
            self.assertEqual(engine.version_dirs(top), [], (a, b))


class LEN3_OnlyYourOwnNumber(unittest.TestCase):
    """LEN-3 (review G): a catalogue number OCR found only in typed notes, inside a longer printed
    number or as the start of a range excuses no lengths; the folder's or the scans' own does."""
    N = 10

    def album(self, **kw):
        a = {"artist": "Band E", "title": "Album L", "year": 1990, "discs": 1, "folder": "Album L",
             "tracks": [{"disc": 1, "num": i + 1, "title": f"Song {i}", "secs": 100.0 + i * 10} for i in range(self.N)],
             "barcodes": set(), "catnos": set()}
        a.update(kw)
        return a

    def cand(self, catnos=("lbl51234",)):
        return {"source": "dg", "id": "r1", "title": "Album L", "artist": "Band E", "year": 2005, "label": "Lbl",
                "barcodes": [], "catnos": set(catnos), "disc_count": 1, "formats": ["cd"],
                "tracks": [{"disc": 1, "title": f"Song {i}", "secs": (100.0 + i * 10) if i < 6 else 400.0}
                           for i in range(self.N)]}

    def run_(self, album, cand, ocr=""):
        cfg = dict(mc.DEFAULT_CFG)
        return mc.classify([mc.score_candidate(album, cand, cfg, ocr)], cfg)

    def test_EDI_LEN3_numbers_not_on_your_copy_excuse_no_lengths(self):
        for printed, typed, catnos in (("", "Cat#: LBL51234", ("lbl51234",)),
                                       ("LBL 51234", "", ("lbl5123",)),
                                       ("LBL 51234~5", "", ("lbl51234",))):
            out = self.run_(self.album(printed_text=printed, typed_text=typed), self.cand(catnos), printed or typed)
            self.assertNotEqual(out["status"], "auto", (printed, typed))

    def test_EDI_LEN3_your_folders_number_still_excuses_them(self):
        out = self.run_(self.album(catnos={"lbl51234"}, catnos_from_folder={"lbl51234"}), self.cand())
        self.assertEqual(out["status"], "auto", out["why"])


if __name__ == "__main__":
    unittest.main()
