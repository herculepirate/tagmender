# VIN-1..VIN-6 (docs/MATCHING_RULES.md, decided 2026-11-17): several rips of one record - by other
# rippers, at other resolutions, ripped in other years - and rips of other pressings of one album.
# Each rip is its own copy (never joined with another rip of its disc, DSC-11); its folder names
# the ripper and the pressing's catalogue number ("[Vinyl Rip PBTHAL SRM-1-1184]", never the
# resolution); its comment carries one line of TagMender's ("Vinyl rip: PBTHAL · 24-bit/96 kHz ·
# ripped 2012 · ..."); the folder name's catalogue number is read in a record's own forms; two
# or more copies of one release are measured (dynamic range, clipping) side by side. Modelled
# on a collector's shelf of one 1977 album: a ripper's 24/96 rip and another's 24/192 rip of the
# US pressing, each in its own folder. Synthetic data only.
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0868_med22c_layer_copies import medium  # noqa: E402
from test_version_folders import SetHttp, VersionsBase, release  # noqa: E402
import engine  # noqa: E402
import match_core as mc  # noqa: E402
import naming  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import vinylrip as vr  # noqa: E402

RID = "0a0b0c0d-7777-4888-9999-aaaabbbbcccc"
TITLES = ["Track 1", "Track 2", "Track 3"]
LENS = [10000, 20000, 30000]
CAT = "SRM-1-1184"
PB = "1973 - Piano Man [Vinyl Rip PBTHAL 24bit 96khz Mercury SRM-1-1184]"
OTHER = "1973 - Piano Man [Vinyl Rip 24bit 192khz Mercury SRM-1-1184]"
PB_NOTES = ("Vinyl Ripping Process/Equipment\n\nTurntable Equipment Profile\n- VPI Scoutmaster with inverted "
            "bearing and 300 RPM motor\n- Zyx Airy 3X-SB cartridge\n")


def vinyl_release(fmt='12" Vinyl', discs=1):
    rel = release(RID, [medium(i, fmt, TITLES, LENS) for i in range(1, discs + 1)], date="1977-09-01",
                  country="US", catno=CAT, label="Mercury", barcode="")
    return rel


class VinBase(VersionsBase):
    def rip(self, folder, rate=96000, notes=None, comment=None, disc=None, shift=0):
        top = self.lib / "Billy Joel" / folder
        for i, t in enumerate(TITLES):
            meta = {"title": t, "album": "Piano Man", "albumartist": "Billy Joel", "artist": "Billy Joel",
                    "track": str(i + 1), "date": "1977", "ISRC": f"USSM1730050{i}",
                    **({"DISCNUMBER": str(disc)} if disc else {})}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            top.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i + shift}:duration={LENS[i] / 1000}", "-ar", str(rate),
                            "-sample_fmt", "s32", "-ac", "2", *args, str(top / f"{i + 1:02d}. {t}.flac"), "-y"],
                           check=True)
            if comment:
                tagio.write(top / f"{i + 1:02d}. {t}.flac", {"comment": [comment]})
        if notes:
            (top / "infa.txt").write_text(notes)
        return top

    def moving(self, http, **naming_):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        nm = {"preset": "tagmender", "template": "", "editions": "edition_year", "album_editions": "edition_year"}
        nm.update(naming_)
        return self.engine(http, operation="move", destination=str(self.dest), naming=nm)

    def http(self, **kw):
        return SetHttp(mb=[vinyl_release(**kw)])

    def folders(self, plan):
        return sorted({Path(fe["dst"]).parent.name for a in plan["albums"] for fe in a["files"]})


class VIN1_EachRipItsOwnCopy(VinBase):
    def test_VIN1_two_rips_of_one_record_are_read_apart_and_planned_apart(self):
        self.rip(PB, 96000, PB_NOTES)
        self.rip(OTHER, 192000, shift=1)
        eng = self.moving(self.http())
        gs = eng.albums_in(self.lib / "Billy Joel")
        # (read together as DSC-11 tries them - each still its own album, never one group)
        self.assertEqual(sorted((g.folder.name, len(g.files)) for g in gs), sorted([(PB, 3), (OTHER, 3)]))
        props = eng.proposals(self.lib / "Billy Joel")
        self.assertEqual(len(props), 2, [(p.source_dir, p.status) for p in props])
        self.assertEqual({p.release_id for p in props}, {RID})

    def test_VIN1_same_disc_slots_overlap_at_any_resolution(self):
        a, b = {("1", "disc")}, {("1", "disc")}
        self.assertTrue(engine.Engine._slots_overlap(a, b))

    def joined(self, rate2):
        """DSC-11's join of two album folders matched for sure to one 2-LP release, disc 1 in one,
        disc 2 in the other (each sure: as a set's discs are found), the second ripped at RATE2."""
        import logging
        from types import SimpleNamespace as NS
        a = self.rip("1973 - Piano Man [Vinyl Rip 24bit 96khz] LP1", 96000)
        b = self.rip("1973 - Piano Man [Vinyl Rip 24bit 96khz] LP2", rate2, shift=2)
        eng = engine.Engine.__new__(engine.Engine)
        eng.log = logging.getLogger("test")
        eng._sure = lambda p: True
        eng._release_of = lambda p: ("mb", RID)
        members = []
        for i, (d, disc) in enumerate(((a, "1"), (b, "2"))):
            files = sorted(str(f) for f in d.glob("*.flac"))
            p = NS(files=files, targets=[{"discnumber": [disc], "totaldiscs": ["2"], "media": ['12" Vinyl']}] * 3,
                   notes=[], evidence=[], why=[], reason_codes=[], release_proven_over=[], release={}, status="identified", provider="mb", release_id=RID, url="", confidence=100.0, tier="exact")
            members.append((i, NS(folder=d, files=files), [p]))
        return eng._release_folders(self.lib / "Billy Joel", [m[1] for m in members], members, {"own": {}})

    def test_VIN1_other_discs_of_a_record_by_other_rips_are_not_joined(self):
        # (a 2-LP release: LP 1 ripped at 24/96 in one folder, LP 2 at 24/192 in another - two partial
        # rips, not one copy; the same rip's two folders are joined as DSC-11 joins any discs)
        self.assertNotIn("joined", self.joined(192000))
        self.tmp_lib_reset()
        self.assertIn("joined", self.joined(96000))

    def tmp_lib_reset(self):
        import shutil
        shutil.rmtree(self.lib / "Billy Joel")

    def test_VIN1_vinyl_rips_function_reads_resolution_and_ripper(self):
        a = self.rip(PB, 96000, PB_NOTES)
        b = self.rip(OTHER, 192000, shift=1)

        class P:
            def __init__(self, d):
                self.files = sorted(d.glob("*.flac"))
                self.targets = [{"media": ['12" Vinyl']}] * 3

        class Part:
            def __init__(self, d):
                self.folder = d
        got = engine.Engine._vinyl_rips([(0, Part(a), P(a)), (1, Part(b), P(b))])
        self.assertEqual(got, [("24-96", "PBTHAL"), ("24-192", "")])


class VIN3_FolderNames(VinBase):
    def test_VIN3_each_rip_is_named_by_its_ripper_and_catalogue_number_never_resolution(self):
        self.rip(PB, 96000, PB_NOTES)
        self.rip(OTHER, 192000, shift=1)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(len(plan["albums"]), 2, pl.preview(plan))
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [Vinyl Rip PBTHAL SRM-1-1184]",
                                              "1973 - Piano Man [Vinyl Rip SRM-1-1184]"])
        # (the Album tag keeps ED-2's edition alone: no rip details in it)
        albums = {v for a in plan["albums"] for fe in a["files"] for v in (fe["changes"].get("album") or [])}
        self.assertEqual(albums, {"Piano Man [1977 Vinyl Rip]"})
        for a in plan["albums"]:
            for fe in a["files"]:
                self.assertNotIn("grouping", {k for k, v in fe["changes"].items() if v and "PBTHAL" in str(v)})

    def test_VIN3_the_year_ripped_the_folder_name_gives_is_kept(self):
        self.rip("1973 - Piano Man [2022 Vinyl Rip 24bit 192khz Mercury SRM-1-1184]", 192000)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [2022 Vinyl Rip SRM-1-1184]"])

    def test_VIN3_two_copies_one_name_the_year_ripped_from_the_notes_names_the_second_apart(self):
        self.rip("1973 - Piano Man [Vinyl Rip 24bit 192khz Mercury SRM-1-1184]", 192000)
        self.rip("1973 - Piano Man [Vinyl Rip 32bit 192khz Mercury SRM-1-1184]", 96000, shift=1,
                 notes="Rip details:\nVinyl rip recorded February 1, 2012\n")
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [2012 Vinyl Rip SRM-1-1184]",
                                              "1973 - Piano Man [Vinyl Rip SRM-1-1184]"])

    def test_VIN3_naming_fields(self):
        t = {"album": ["Piano Man"], "albumartist": ["Billy Joel"], "title": ["t"], "tracknumber": ["1"],
             "originaldate": ["1973"], "date": ["1977"], "media": ['12" Vinyl'],
             "~ripper": ["PBTHAL"], "~rip_catalog": [CAT], "~rip_resolution": ["24-96"]}
        tpl = "%_riptag%/%_ripper%/%_rip_catalog%/%_rip_resolution%/%_edition%/x"
        self.assertEqual(naming.render(tpl, t), ["PBTHAL SRM-1-1184", "PBTHAL", CAT, "24-96",
                                                 "Vinyl Rip PBTHAL SRM-1-1184", "x"])
        self.assertEqual(naming.folder_edition(t, "edition"), "Vinyl Rip PBTHAL SRM-1-1184")
        # (no ripper or catalogue number known: ED-2 as before)
        plain = {k: v for k, v in t.items() if not k.startswith("~")}
        self.assertEqual(naming.folder_edition(plain), "1977 Vinyl Rip")
        # (a CD is never given rip details)
        cd = dict(t, media=["CD"])
        self.assertEqual(naming.folder_edition(cd), naming.edition(cd))

    def test_VIN3_a_script_with_its_own_edition_lines_gets_the_rip_details(self):
        tpl = naming.PRESETS["all-music"]
        out = naming.with_editions(tpl, "edition_year")
        # (the album as the planner names it: without the edition its Album tag carries)
        t = {"album": ["Piano Man"], "albumartist": ["Billy Joel"], "title": ["t"],
             "tracknumber": ["1"], "originaldate": ["1973"], "date": ["1977"], "media": ['12" Vinyl'],
             "~ripper": ["PBTHAL"], "~rip_catalog": [CAT]}
        self.assertEqual(naming.render(out, t)[1], "1973 - Piano Man [Vinyl Rip PBTHAL SRM-1-1184]")


class VIN3_Ripper(unittest.TestCase):
    def test_VIN3_ripper_from_the_folder_name(self):
        self.assertEqual(vr.ripper_in_name(PB), "PBTHAL")
        self.assertEqual(vr.ripper_in_name("1975 - X [Vinyl Rip pbthal 24bit 96khz]"), "PBTHAL")
        self.assertEqual(vr.ripper_in_name("X [Vinyl Rip KRONOS 24bit 96khz Mercury 6337 160]"), "KRONOS")
        self.assertEqual(vr.ripper_in_name("X [Vinyl Rip, rip by Gorehound]"), "Gorehound")

    def test_VIN3_a_catalogue_number_label_or_country_is_no_ripper(self):
        for name in ("1975 - Fly By Night [Vinyl Rip 24bit 192khz Mercury Price19]",
                     "X [Vinyl Rip 24bit 96khz Mercury SRM-1-1184]", "X [Vinyl Rip MOFI 24bit 96khz]",
                     "X [Vinyl Rip JAPAN 24bit 96khz]", "X [2019 DMM Vinyl Rip 32bit 384khz]",
                     "X [Vinyl Rip 24bit 96khz SK Mercury 8207]", "X [Vinyl Rip EPIC 24bit 96khz]"):
            self.assertEqual(vr.ripper_in_name(name), "", name)

    def test_VIN3_ripper_from_the_rip_notes_signature(self):
        self.assertEqual(vr.ripper_in_notes("Rip details:\n Vinyl rip by Packgrog, recorded February 1, 2012"),
                         "Packgrog")
        self.assertEqual(vr.ripper_in_notes("Rush Presto\n\n Posted by pbthal @ May 16th, 2012"), "PBTHAL")
        self.assertEqual(vr.ripper_in_notes("PBTHAL's 24bit 192khz raw audio--no declicking"), "PBTHAL")
        self.assertEqual(vr.ripper_in_notes("Ripped by me on my deck"), "")
        # (the album's credits are no rip notes: an engineer "recorded by" is never a ripper)
        self.assertEqual(vr.ripper_in_notes("Recorded by Terry Brown at Le Studio"), "")
        self.assertEqual(vr.ripper_in_notes("Turntable: VPI\nCartridge: Zyx"), "")

    def test_VIN3_year_ripped(self):
        self.assertEqual(vr.rip_year("Vinyl rip by Packgrog, recorded February 1, 2012 (2/1/12!!)"), "2012")
        self.assertEqual(vr.rip_year("Recorded at Le Studio, Morin Heights, 1981"), "")
        self.assertEqual(vr.year_in_name("1989 - Presto [2022 Vinyl Rip 24bit 192khz Anthem R1-83736]", "2015"), "2022")
        # (a reissue's own year is the pressing's, not the year ripped)
        self.assertEqual(vr.year_in_name("X [2015 Vinyl Rip 24bit 96khz Mercury – B0022376-01]", "2015"), "")


class VIN4_CommentLine(VinBase):
    def test_VIN4_line_and_merge(self):
        d = {"ripper": "PBTHAL", "resolution": (24, 96000), "year": "2022",
             "equipment": vr.equipment(PB_NOTES)}
        line = vr.comment_line(d)
        self.assertEqual(line, "Vinyl rip: PBTHAL · 24-bit/96 kHz · ripped 2022 · VPI Scoutmaster, Zyx Airy 3X-SB")
        self.assertEqual(vr.comment_line({"resolution": (32, 192000)}), "Vinyl rip: 32-bit/192 kHz")
        self.assertEqual(vr.comment_line({}), "")
        self.assertEqual(vr.merged_comment("", line), line)
        self.assertEqual(vr.merged_comment("Final Master", line), "Final Master\n" + line)
        self.assertEqual(vr.merged_comment("Final Master\n" + line, line), "Final Master\n" + line)
        old = "Final Master\nVinyl rip: 24-bit/96 kHz\nmore"
        self.assertEqual(vr.merged_comment(old, line), f"Final Master\n{line}\nmore")
        self.assertEqual(vr.merged_comment("a\r\nb", line), "a\r\nb\r\n" + line)

    def test_VIN4_equipment_only_when_labelled(self):
        self.assertEqual(vr.equipment("Turntable: VPI Scoutmaster with Trans-Fi Terminator\n"
                                      "Cartridge: Audio-Technica AT33PTG\n"), "VPI Scoutmaster, Audio-Technica AT33PTG")
        self.assertEqual(vr.equipment(" Pioneer PL-200 with Denon DL-110 cartridge"), "Pioneer PL-200, Denon DL-110")
        self.assertEqual(vr.equipment("Denon DP - 30L\nAT 440 MLA\nNakamichi CA - 5AII"), "")

    def test_VIN4_comment_written_once_beside_what_is_there(self):
        top = self.rip(PB, 96000, PB_NOTES, comment="Final Master")
        eng = self.moving(self.http())
        plan = eng.plan(top)
        [a] = plan["albums"]
        want = "Final Master\nVinyl rip: PBTHAL · 24-bit/96 kHz · VPI Scoutmaster, Zyx Airy 3X-SB"
        self.assertEqual({tuple(fe["changes"].get("comment") or ()) for fe in a["files"]}, {(want,)})
        # (idempotent: a file already carrying it is not written for it again)
        for fe in a["files"]:
            tagio.write(fe["src"], {"comment": [want]})
        plan = self.moving(self.http()).plan(top)
        self.assertTrue(all("comment" not in fe["changes"] for a in plan["albums"] for fe in a["files"]))

    def test_VIN4_comment_policy_preserve_leaves_it_alone(self):
        top = self.rip(PB, 96000, PB_NOTES, comment="Final Master")
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        eng = self.engine(self.http(), operation="move", destination=str(self.dest),
                          naming={"preset": "tagmender", "template": "", "editions": "edition_year"},
                          field_policy={"default": "replace", "fields": {"comment": "preserve"}})
        plan = eng.plan(top)
        self.assertTrue(all("comment" not in fe["changes"] for a in plan["albums"] for fe in a["files"]))

    def test_VIN4_no_line_on_a_cd(self):
        top = self.rip("1973 - Piano Man [CD]", 44100)
        plan = self.moving(SetHttp(mb=[vinyl_release(fmt="CD")])).plan(top)
        self.assertTrue(all("Vinyl rip" not in str(fe["changes"].get("comment"))
                            for a in plan["albums"] for fe in a["files"]))


class VIN5_RecordCatalogueNumbers(unittest.TestCase):
    def test_VIN5_record_catalogue_numbers_in_folder_names(self):
        for name, want in (
                ("1977 - A Farewell To Kings [Vinyl Rip PBTHAL 24bit 96khz Mercury SRM-1-1184]", ["SRM-1-1184"]),
                ("1982 - Signals [Vinyl Rip 32bit 96khz JP Epic 25-3P-378]", ["25-3P-378"]),
                ("1978 - Hemispheres [Vinyl Rip 24bit 192khz Mercury 25·3P-269]", ["25·3P-269"]),
                ("1989 - Presto [2022 Vinyl Rip 24bit 192khz Anthem R1-83736]", ["R1-83736"]),
                ("1989 - Presto [2025 Vinyl Rip 32bit 96khz Anthem RD1 83736]", ["RD1 83736"]),
                ("1989 - Presto [Vinyl Rip PBTHAL 24bit 96khz Atlantic 7-82040-1]", ["7-82040-1"]),
                ("2012 - Clockwork Angels [Vinyl Rip 24bit 96khz RR 1686-176561]", ["RR 1686-176561"]),
                ("1984 - Grace Under Pressure [Vinyl Rip 24bit 96khz DE Mercury 818 476-1]", ["818 476-1"]),
                ("1985 - Power Windows [Vinyl Rip 24bit 192khz Mercury 422 826 098-1 M-1]", ["422 826 098-1"]),
                ("1976 - 2112 [2015 Vinyl Rip 32bit 384khz B0022371-01-9]", ["B0022371-01-9"]),
                ("1987 - Hold Your Fire [Vinyl Rip 24bit 96khz Mercury 832-464-1 Q-1]", ["832-464-1"])):
            self.assertEqual(mc.catno_hints(name), want, name)

    def test_VIN5_dates_and_resolutions_are_no_catalogue_numbers(self):
        for name in ("1992 - Mirrors [1992-01-30 Cleveland SBD]", "X [02-12-1974]", "Y [24-96]", "B [1974-1987]",
                     "C [CD1 12345]", "D [Vol 2 2004-2005]", "E [Vinyl Rip 24bit 96khz]"):
            self.assertEqual(mc.catno_hints(name), [], name)


class VIN6_CopiesMeasured(VinBase):
    def test_VIN6_dr_of_blocks_as_the_meter_reckons_it(self):
        import dynamics
        # (a steady sine: its block RMS (x sqrt 2) is its peak - DR 0; at a quarter of the loudest
        # blocks' level the second-highest peak stands 12 dB over: DR 12)
        self.assertAlmostEqual(dynamics.dr_of_blocks([0.5] * 10, [0.5] * 10), 0.0)
        self.assertAlmostEqual(dynamics.dr_of_blocks([0.25] * 10, [1.0, 1.0] + [0.3] * 8), 20 * __import__("math").log10(4))
        self.assertIsNone(dynamics.dr_of_blocks([], []))

    def pcm16(self, path, amp, secs=12, rate=44100):
        """A 16-bit stereo WAV of a 440 Hz sine at AMP, clipped at full scale."""
        import wave
        import numpy as np
        t = np.arange(int(secs * rate)) / rate
        x = np.clip(amp * np.sin(2 * np.pi * 440 * t), -1, 1)
        pcm = (np.repeat(x[:, None], 2, axis=1) * 32767).astype("<i2")
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return path

    def test_VIN6_measure_file_dr_and_clipping(self):
        import dynamics
        clean = dynamics.measure_file(self.pcm16(self.tmp / "a.wav", 0.5))
        self.assertAlmostEqual(clean["dr"], 0.0, places=1)
        self.assertEqual(clean["clipped"], 0)
        # (a wave flattened at full scale: its runs of full-scale samples are counted)
        clip = dynamics.measure_file(self.pcm16(self.tmp / "b.wav", 2.0))
        self.assertGreater(clip["clipped"], 1000)
        # (a sine whose crest just touches full scale: single samples, no run - no clipping)
        touch = dynamics.measure_file(self.pcm16(self.tmp / "c.wav", 1.0, rate=44000))
        self.assertEqual(touch["clipped"], 0)

    def test_VIN6_each_file_measured_once(self):
        import dynamics
        from unittest import mock
        f = self.pcm16(self.tmp / "c" / "01.wav", 0.5)
        cache = {}
        first = dynamics.measure_copy([f], cache=cache)
        with mock.patch.object(dynamics, "measure_file", side_effect=AssertionError("measured again")):
            self.assertEqual(dynamics.measure_copy([f], cache=cache), first)
        self.assertEqual(first["tracks"], 1)

    def test_VIN6_words(self):
        import dynamics
        rows = [{"label": "32-192", "dr": 12, "clipped": 3, "this": False},
                {"label": "24-96 PBTHAL", "dr": 13, "clipped": 0, "this": True}]
        self.assertEqual(dynamics.words(rows), "2 copies of this release: 24-96 PBTHAL DR 13, clipping none (this one); "
                                               "32-192 DR 12, 3 clipped samples")
        self.assertEqual(dynamics.words(rows[:1]), "")

    def test_VIN6_two_copies_of_one_release_in_a_plan_are_measured_side_by_side(self):
        import dynamics
        import tagmender
        self.rip(PB, 96000, PB_NOTES)
        self.rip(OTHER, 192000, shift=1)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        # (no copy is measured while planning: only after it, when asked)
        self.assertFalse(any(a.get("copies") for a in plan["albums"]))
        groups = dynamics.copies(plan, filed=False)
        self.assertEqual([(rid, len(cs)) for rid, cs in groups], [(RID, 2)])
        tagmender._measure_copies(plan)
        for a in plan["albums"]:
            rows = a["copies"]
            self.assertEqual(sorted(r["label"] for r in rows), ["24-192", "24-96 PBTHAL"])
            self.assertTrue(all(isinstance(r["dr"], int) and r["clipped"] == 0 for r in rows), rows)
            self.assertEqual(sum(r["this"] for r in rows), 1)
        self.assertIn("copies: 2 copies of this release: ", pl.preview(plan))
        # (one copy alone is never measured)
        self.assertEqual(dynamics.copies({"albums": plan["albums"][:1]}, filed=False), [])

    def test_VIN6_shown_in_why_this_release_and_the_album_tooltip(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from gui.models import PlanModel, R_TOOLTIP
            from gui.preview_panes import why_lines
        except ImportError as exc:
            self.skipTest(f"no Qt: {exc}")
        QApplication.instance() or QApplication([])
        a = {"identification": {"status": "identified", "evidence": [], "why": []}, "conflicts": [], "status": "ready",
             "source_dir": "/x/" + PB, "files": [], "copies": [
                 {"label": "24-96 PBTHAL", "dr": 13, "clipped": 0, "this": True},
                 {"label": "32-192", "dr": 12, "clipped": 3, "this": False}]}
        words = [w for _m, w in why_lines(a)[1]]
        self.assertIn("2 copies of this release: 24-96 PBTHAL DR 13, clipping none (this one); "
                      "32-192 DR 12, 3 clipped samples", words)
        m = PlanModel()
        m.set_plan({"albums": [a]})
        self.assertIn("32-192 DR 12, 3 clipped samples", m.data(m.index(0, 1), R_TOOLTIP))


if __name__ == "__main__":
    unittest.main()
