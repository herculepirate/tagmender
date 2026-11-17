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
    def rip(self, folder, rate=96000, notes=None, comment=None, disc=None, shift=0, extra=0.0):
        top = self.lib / "Billy Joel" / folder
        for i, t in enumerate(TITLES):
            meta = {"title": t, "album": "Piano Man", "albumartist": "Billy Joel", "artist": "Billy Joel",
                    "track": str(i + 1), "date": "1977", "ISRC": f"USSM1730050{i}",
                    **({"DISCNUMBER": str(disc)} if disc else {})}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            top.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i + shift}:duration={LENS[i] / 1000 + extra}", "-ar", str(rate),
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
        # (ED-2's pressing year kept - decided 2026-11-17 - then the ripper and catalogue number)
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip PBTHAL SRM-1-1184]",
                                              "1973 - Piano Man [1977 Vinyl Rip SRM-1-1184]"])
        # (the Album tag keeps ED-2's edition alone: no rip details in it)
        albums = {v for a in plan["albums"] for fe in a["files"] for v in (fe["changes"].get("album") or [])}
        self.assertEqual(albums, {"Piano Man [1977 Vinyl Rip]"})
        for a in plan["albums"]:
            for fe in a["files"]:
                self.assertNotIn("grouping", {k for k, v in fe["changes"].items() if v and "PBTHAL" in str(v)})

    def test_VIN3_the_year_ripped_the_folder_name_gives_is_kept(self):
        self.rip("1973 - Piano Man [2022 Vinyl Rip 24bit 192khz Mercury SRM-1-1184]", 192000)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip SRM-1-1184 ripped 2022]"])

    def test_VIN3_two_copies_one_name_the_year_ripped_from_the_notes_names_one_apart_in_any_order(self):
        self.rip("1973 - Piano Man [Vinyl Rip 24bit 192khz Mercury SRM-1-1184]", 192000)
        self.rip("1973 - Piano Man [Vinyl Rip 32bit 192khz Mercury SRM-1-1184]", 96000, shift=1,
                 notes="Rip details:\nVinyl rip recorded February 1, 2012\n")
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip SRM-1-1184 ripped 2012]",
                                              "1973 - Piano Man [1977 Vinyl Rip SRM-1-1184]"])

    def test_VIN3_naming_fields(self):
        t = {"album": ["Piano Man"], "albumartist": ["Billy Joel"], "title": ["t"], "tracknumber": ["1"],
             "originaldate": ["1973"], "date": ["1977"], "media": ['12" Vinyl'],
             "~ripper": ["PBTHAL"], "~rip_catalog": [CAT], "~rip_resolution": ["24-96"]}
        tpl = "%_riptag%/%_ripper%/%_rip_catalog%/%_rip_resolution%/%_edition%/x"
        self.assertEqual(naming.render(tpl, t), ["PBTHAL SRM-1-1184", "PBTHAL", CAT, "24-96",
                                                 "1977 Vinyl Rip PBTHAL SRM-1-1184", "x"])
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
        self.assertEqual(naming.render(out, t)[1], "1973 - Piano Man [1977 Vinyl Rip PBTHAL SRM-1-1184]")
        # (the Album tag carrying ED-2's "[1977 Vinyl Rip]": one bracket, never two)
        t["album"] = ["Piano Man [1977 Vinyl Rip]"]
        self.assertEqual(naming.render(out, t)[1], "1973 - Piano Man [1977 Vinyl Rip PBTHAL SRM-1-1184]")
        self.assertEqual(naming.render(naming.with_editions(naming.PRESETS["tagmender"], "edition_year"), t)[1],
                         "1973 - Piano Man [1977 Vinyl Rip PBTHAL SRM-1-1184]")

    def test_VIN3_editions_off_a_vinyl_rip_still_names_its_rip(self):
        t = {"album": ["Piano Man"], "albumartist": ["Billy Joel"], "title": ["t"], "tracknumber": ["1"],
             "originaldate": ["1973"], "date": ["1977"], "media": ['12" Vinyl'], "~rip_vinyl": ["1"],
             "~ripper": ["PBTHAL"], "~rip_catalog": [CAT]}
        for name in ("tagmender", "all-music"):
            out = naming.with_editions(naming.PRESETS[name], "off")
            self.assertEqual(naming.render(out, t)[1], "1973 - Piano Man [Vinyl Rip PBTHAL SRM-1-1184]", name)
            cd = {k: v for k, v in t.items() if not k.startswith("~")}
            self.assertEqual(naming.render(out, dict(cd, media=["CD"]))[1], "1973 - Piano Man", name)


class VIN3_Ripper(unittest.TestCase):
    def test_VIN3_ripper_from_the_folder_name(self):
        self.assertEqual(vr.ripper_in_name(PB), "PBTHAL")
        self.assertEqual(vr.ripper_in_name("1975 - X [Vinyl Rip pbthal 24bit 96khz]"), "PBTHAL")
        # (a word in capitals is only a candidate: a ripper where the notes name it too - describe)
        self.assertEqual(vr.ripper_in_name("X [Vinyl Rip KRONOS 24bit 96khz Mercury 6337 160]"), "")
        self.assertEqual(vr.caps_in_name("X [Vinyl Rip KRONOS 24bit 96khz Mercury 6337 160]"), "KRONOS")
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
        # (PBTHAL's comment "Final Master" is his own name for the transfer: part of the rip, VIN-3)
        want = "Final Master\nVinyl rip: PBTHAL Final Master · 24-bit/96 kHz · VPI Scoutmaster, Zyx Airy 3X-SB"
        self.assertEqual({tuple(fe["changes"].get("comment") or ()) for fe in a["files"]}, {(want,)})
        # (idempotent: a file already carrying it is not written for it again)
        for fe in a["files"]:
            tagio.write(fe["src"], {"comment": [want]})
        plan = self.moving(self.http()).plan(top)
        self.assertTrue(all("comment" not in fe["changes"] for a in plan["albums"] for fe in a["files"]))

    def test_VIN4_a_kept_comment_gets_the_line_below_it_lock_none(self):
        # (decided 2026-11-17: "add it below your text" - preserve and fill_missing included; lock: no write)
        import executor
        want = "Final Master\nVinyl rip: PBTHAL Final Master · 24-bit/96 kHz · VPI Scoutmaster, Zyx Airy 3X-SB"
        for pol, line in (("preserve", True), ("fill_missing", True), ("lock", False)):
            with self.subTest(policy=pol):
                import shutil
                shutil.rmtree(self.lib, ignore_errors=True)
                shutil.rmtree(self.tmp / "library", ignore_errors=True)
                top = self.rip(PB, 96000, PB_NOTES, comment="Final Master")
                self.dest = self.tmp / "library"
                self.dest.mkdir()
                eng = self.engine(self.http(), operation="move", destination=str(self.dest),
                                  naming={"preset": "tagmender", "template": "", "editions": "edition_year"},
                                  field_policy={"default": "replace", "fields": {"comment": pol}})
                plan = eng.plan(top)
                got = {tuple(fe["changes"].get("comment") or ()) for a in plan["albums"] for fe in a["files"]}
                self.assertEqual(got, {(want,)} if line else {()})
                if line:
                    executor.Executor(plan).run()
                    written = [tagio.read(fe["dst"]).get("comment") for a in plan["albums"] for fe in a["files"]]
                    self.assertEqual({tuple(x) for x in written}, {(want,)})   # (your text first, exactly)

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
                ("1985 - Power Windows [Vinyl Rip 24bit 192khz Mercury 422 826 098-1 M-1]", ["422 826 098-1 M-1"]),
                ("1976 - 2112 [2015 Vinyl Rip 32bit 384khz B0022371-01-9]", ["B0022371-01-9"]),
                ("1987 - Hold Your Fire [Vinyl Rip 24bit 96khz Mercury 832-464-1 Q-1]", ["832-464-1 Q-1"])):
            self.assertEqual(mc.catno_hints(name), want, name)
        # (the US format suffix read with the number, and the bare number too: a database listing
        # either agrees - catnos_agree both ways; a side or track label "B2" is never glued on)
        def folder(name):
            return {mc.norm_code(h) for h in mc.catno_hints(name, variants=True)}
        for name, bare in (("X [Vinyl Rip 832-464-1 Q-1]", "832 464-1"), ("X [422 826 098-1 M-1]", "422 826 098-1")):
            self.assertTrue(mc.catnos_agree(folder(name), [bare]), name)
            self.assertTrue(mc.catnos_agree(folder(name), [bare + (" Q-1" if "Q-1" in name else " M-1")]), name)
        self.assertEqual(mc.catno_hints("X [Vinyl Rip 832-464-1 B2]"), ["832-464-1"])
        # (a year before a number in groups: "[1999 818 476-1]" keeps it; "[1999 001 002-1]" none)
        self.assertEqual(mc.catno_hints("X [1999 818 476-1]"), ["818 476-1"])
        self.assertEqual(mc.catno_hints("X [Vinyl Rip 1984 818 476-1]"), ["818 476-1"])
        self.assertEqual(mc.catno_hints("X [1999 001 002-1]"), [])
        for name, want in ():
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
        self.rip(OTHER, 192000, shift=1, extra=0.4)
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        # (no copy is measured while planning: only after it, when asked)
        self.assertFalse(any(a.get("copies") for a in plan["albums"]))
        groups = dynamics.copies(plan, filed=False)
        self.assertEqual([(rid, len(cs)) for rid, cs in groups], [(RID, 2)])
        tagmender._measure_copies(plan)
        # (two rips of one record: their lengths differ - a transcode's would not: VIN-7 / VIN-6)
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


class VIN_Review(VinBase):
    """The independent review's findings (2026-11-17), each with its own test."""
    def apply(self, plan):
        import executor
        return executor.Executor(plan).run()

    def test_VIN3_VIN4_plan_apply_plan_again_changes_nothing(self):
        # C1: TagMender's own folder name and comment line are read back: the ripper and year stay
        for folder, notes in (("1973 - Piano Man [Vinyl Rip KRONOS 24bit 96khz Mercury SRM-1-1184]",
                               "Vinyl rip by KRONOS\nripped 2012-02-01 on my deck\n"),
                              ("1973 - Piano Man [Vinyl Rip, rip by Gorehound 24bit 96khz]", None)):
            with self.subTest(folder=folder):
                import shutil
                shutil.rmtree(self.lib, ignore_errors=True)
                shutil.rmtree(self.tmp / "library", ignore_errors=True)
                top = self.rip(folder, 96000, notes)
                plan = self.moving(self.http()).plan(top)
                first = self.folders(plan)
                self.apply(plan)
                there = Path(plan["albums"][0]["files"][0]["dst"]).parent
                again = self.moving(self.http()).plan(there)
                self.assertEqual(self.folders(again), first)
                [a] = again["albums"]
                self.assertTrue(all(fe["src"] == fe["dst"] and "comment" not in fe["changes"] for fe in a["files"]),
                                [(fe["src"], fe["dst"], fe["changes"]) for fe in a["files"]])

    def test_VIN4_a_line_of_yours_starting_vinyl_rip_is_kept(self):
        # C2: a line of the user's own that starts "Vinyl rip: " is never TagMender's
        mine = "Vinyl rip: my own notes, Technics SL-1200, cleaned with VPI 16.5"
        line = vr.comment_line({"ripper": "PBTHAL", "resolution": (24, 96000)})
        self.assertIsNone(vr.parse_own(mine))
        self.assertEqual(vr.merged_comment(mine, line), mine + "\n" + line)
        self.assertEqual(vr.merged_comment(f"{mine}\n{line}\nVinyl rip: more of mine", line),
                         f"{mine}\n{line}\nVinyl rip: more of mine")
        old = vr.comment_line({"resolution": (24, 192000)})
        self.assertEqual(vr.merged_comment(f"a\n{old}\nb", line), f"a\n{line}\nb")
        # (a Vorbis comment of two values: the line goes once, into the value holding it)
        self.assertEqual(vr.merged_values(["Final Master", line], line), ["Final Master", line])
        self.assertEqual(vr.merged_values(["Final Master", old], line), ["Final Master", line])
        self.assertEqual(vr.parse_own(line)["ripper"], "PBTHAL")

    def test_VIN4_policies_cleanup_and_a_cd_of_a_cd_and_lp_set(self):
        top = self.rip(PB, 96000, PB_NOTES, comment="Final Master")
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        for extra, line in (({"field_policy": {"default": "replace", "fields": {"comment": "fill_missing"}}}, True),
                            ({"field_policy": {"default": "fill_missing", "fields": {}}}, True),
                            ({"cleanup": {"comments": "remove"}}, False)):
            with self.subTest(extra=extra):
                eng = self.engine(self.http(), operation="move", destination=str(self.dest),
                                  naming={"preset": "tagmender", "template": "", "editions": "edition_year"}, **extra)
                plan = eng.plan(top)
                self.assertEqual(any("Vinyl rip" in str(fe["changes"].get("comment"))
                                     for a in plan["albums"] for fe in a["files"]), line)
        import plan as planmod
        self.assertFalse(planmod.Planner._vinyl_file({"media": ["CD"]}, False))
        self.assertTrue(planmod.Planner._vinyl_file({"media": ['12" Vinyl']}, False))

    def test_VIN4_picard_preserved_comment_gets_the_line_below_it(self):
        top = self.rip(PB, 96000, PB_NOTES, comment="Final Master")
        self.picard_ini(preserved="comment")
        plan = self.moving(self.http()).plan(top)
        self.assertEqual({tuple(fe["changes"].get("comment") or ()) for a in plan["albums"] for fe in a["files"]},
                         {("Final Master\nVinyl rip: PBTHAL Final Master · 24-bit/96 kHz · VPI Scoutmaster, Zyx Airy 3X-SB",)})

    def test_ED5_an_album_known_vinyl_rip_shows_no_hd(self):
        # (ED-5: vinyl never adds HD - an album known whose copy is a vinyl rip too)
        t = {"album": ["Hemispheres"], "media": ['12" Vinyl'], "date": ["1978"], "originaldate": ["1978"],
             "~edition_unproven": ["1"], "~edition_hd": ["1"]}
        self.assertEqual(naming.edition(t), "")
        self.assertEqual(naming.edition({k: v for k, v in t.items() if k != "media"}), "HD")
        self.assertEqual(naming.edition(dict({k: v for k, v in t.items() if k != "media"}, **{"~rip_vinyl": ["1"]})), "")

    def test_VIN1_a_cd_beside_an_lp_and_a_ripper_named_on_one_side_only_join(self):
        # M2: only vinyl members compared; one ripper named and one not is no difference
        self.assertFalse(engine.Engine._other_rips([("24-96", "Packgrog"), ("24-96", "")]))
        self.assertTrue(engine.Engine._other_rips([("24-96", "Packgrog"), ("24-96", "PBTHAL")]))
        self.assertTrue(engine.Engine._other_rips([("24-96", ""), ("24-192", "")]))
        from types import SimpleNamespace as NS
        a = self.rip("1973 - Piano Man CD", 44100)
        b = self.rip("1973 - Piano Man LP", 96000)
        mem = [(0, NS(folder=a), NS(files=sorted(a.glob("*.flac")), targets=[{"media": ["CD"]}])),
               (1, NS(folder=b), NS(files=sorted(b.glob("*.flac")), targets=[{"media": ['12" Vinyl']}]))]
        self.assertEqual(engine.Engine._vinyl_rips(mem), [("24-96", "")])

    def test_VIN3_ripper_false_positives(self):
        # M3
        for name in ("X [Vinyl Rip ABBA 24-96]", "X [Vinyl Rip LONDON 24bit 96khz]", "X [Vinyl Rip ALAC 24-96]",
                     "X [Vinyl Rip MINT 24-96]", "X [rip by john at 24-96]",
                     "X [ripped by EAC]", "X [Vinyl rip by me 24-96]"):
            self.assertEqual(vr.ripper_in_name(name), "", name)
        for text in ("Digitally transferred by Bob Ludwig at Gateway", "Remastered and transferred by Kevin Gray",
                     "ripped by EAC", "Ripped by Audacity 2.0", "Posted by: admin", "Uploaded by Torrentbot",
                     "Ripped by request", "ripped by Sunday", "Digitized by DBPOWERAMP", "Rip by the way",
                     "Converted and ripped by foobar2000"):
            self.assertEqual(vr.ripper_in_notes(text), "", text)
        self.assertEqual(vr.ripper_in_notes("rip by gorehound, vm760slc"), "gorehound")
        # (a word in capitals the notes name too is the ripper; never the album artist)
        top = self.rip("1973 - Piano Man [Vinyl Rip KRONOS 24bit 96khz]", 96000, "Vinyl rip by KRONOS\n")
        self.assertEqual(vr.describe(top, sorted(top.glob("*.flac")))["ripper"], "KRONOS")
        self.assertEqual(vr.describe(top, sorted(top.glob("*.flac")), notes="")["ripper"], "")
        self.assertEqual(vr.describe(top, sorted(top.glob("*.flac")), artist="Kronos")["ripper"], "")

    def test_VIN5_no_catalogue_number_in_dates_spans_rates_or_formats(self):
        # M4
        for name in ("[12-02-74]", "[2019-2020-2021]", "[24-192-Vinyl]", "[44100-16-2]", "[96000-24-2]",
                     "[H264-AAC-1080p]", "[mp3-v0-2008]", "[33-45-78]", "[Tracks 01-12-16]", "[DR 12-14-11]",
                     "[555-123-4567]", "[SN 123-45-6789]", "[1080-2160-4320]", "[0-60-120]", "[S1 123456]",
                     "[LP-33-12345]", "[2LP-SET-12345]", "[Remaster-2015-24-96]", "[1977-1978-1979]",
                     "[CD1 12345]", "[HD1 192000]", "MC5-1969"):
            self.assertEqual(mc.catno_hints(name), [], name)

    def test_VIN3_an_album_knowns_shared_year_is_no_year_ripped(self):
        # (Presto "[2025 Vinyl Rip 32bit 96khz Anthem RD1 83736]": two 2025 pressings tie, album known
        # with their date 2025 - the folder's 2025 is the pressing's, not the year ripped)
        import plan as planmod
        from types import SimpleNamespace as NS
        top = self.rip("1973 - Piano Man [2025 Vinyl Rip 32bit 96khz Anthem RD1 83736]", 96000)
        kept = sorted(top.glob("*.flac"))
        targets = {f: {"media": ['12" Vinyl'], "date": ["2025"], "originaldate": ["1973"]} for f in kept}
        rip = planmod.Planner._vinyl_rip(NS(source_dir=str(top)), kept, targets, {}, True)
        self.assertEqual(rip["year"], "")
        targets = {f: {"media": ['12" Vinyl'], "date": ["2015"], "originaldate": ["1973"]} for f in kept}
        self.assertEqual(planmod.Planner._vinyl_rip(NS(source_dir=str(top)), kept, targets, {}, True)["year"], "2025")

    def test_VIN3_year_ripped_never_a_pressing_year(self):
        # M5
        for text in ("Ripped from my 1985 UK first pressing", "Recorded live 1981, ripped from LP",
                     "Ripped at 24/96 from the 1989 reissue", "Ripped by X\nRecorded at Le Studio 1981"):
            self.assertEqual(vr.rip_year(text), "", text)
        self.assertEqual(vr.rip_year("Transferred in 2008 from a 1982 copy"), "2008")
        self.assertEqual(vr.rip_year("ripped 2012-02-01", not_years=("2012",)), "")
        for name, pressing in (("X [1985 Vinyl Rip]", ""), ("X [1985 UK Vinyl Rip]", "1977"),
                               ("X [1985 Vinyl Rip 24bit 96khz]", "1985"), ("X [1977 Vinyl Rip PBTHAL SRM-1-1184]", "")):
            self.assertEqual(vr.year_in_name(name, pressing), "", name)
        self.assertEqual(vr.year_in_name("X [1977 Vinyl Rip SRM-1-1184 ripped 2012]"), "2012")

    def test_VIN4_equipment_never_a_sentence(self):
        for text in ("Turntable\n\nThe album was cut by ...", "Track 3 has a click in the cartridge",
                     "This record was played with my old cartridge", "Turntable setup\nI used a Technics"):
            self.assertEqual(vr.equipment(text), "", text)
        self.assertEqual(vr.equipment("Turntable Equipment Profile\n- VPI Scoutmaster with SDS"), "VPI Scoutmaster")

    def test_VIN6_copies_hold_the_same_disc(self):
        import dynamics
        def album(i, disc):
            return {"status": "ready", "source_dir": f"/x/{i}", "identification": {
                "status": "identified", "release_id": RID, "tier": "exact"},
                "files": [{"src": f"/x/{i}/01.flac", "dst": f"/y/{i}/01.flac", "changes": {"discnumber": [disc]}}]}
        self.assertEqual(dynamics.copies({"albums": [album(1, "1"), album(2, "2")]}, filed=False), [])
        self.assertEqual(len(dynamics.copies({"albums": [album(1, "1"), album(2, "1")]}, filed=False)), 1)
        self.assertEqual(dynamics.half_up(12.5), 13)
        # (a run of full-scale samples across two blocks is counted whole)
        import numpy as np
        x = np.array([0, 1, 1, 0, 1, 0, 1], float)
        n1, st = dynamics._run_count(x[:2])
        n2, _st = dynamics._run_count(x[2:], st)
        self.assertEqual(n1 + n2, dynamics._run_count(x)[0])

    def test_VIN6_no_ffmpeg_measures_nothing(self):
        import dynamics
        from unittest import mock
        f = self.tmp / "a.flac"
        self.rip("x", 44100)
        f = next((self.lib / "Billy Joel" / "x").glob("*.flac"))
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(dynamics.measure_file(f))


class VIN7_TwoRipsInOneFolder(VinBase):
    FOLDER = "1973 - Piano Man [Vinyl Rip 24bit 96khz Mercury SRM-1-1184]"

    def two(self, b_comment, b_suffix=" (1)", b_titles=TITLES, b_lens=None):
        top = self.lib / "Billy Joel" / VIN7_TwoRipsInOneFolder.FOLDER
        top.mkdir(parents=True)
        sets = (("", TITLES, [x / 1000 for x in LENS], "US Pressing - Fran Solo, Ediciones 24bit-96kHz"),
                (b_suffix, b_titles, b_lens or [x / 1000 + 0.6 for x in LENS], b_comment))
        for suffix, titles, lens, com in sets:
            for i, (t, secs) in enumerate(zip(titles, lens)):
                f = top / (f"{i + 1:02d}. {t}{suffix}.flac" if suffix else f"{i + 1:02d}. {t}.flac")
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i}:duration={secs}", "-ar", "96000", "-sample_fmt", "s32",
                                "-ac", "2", "-metadata", f"title={t}", "-metadata", "album=Piano Man",
                                "-metadata", "albumartist=Billy Joel", "-metadata", "artist=Billy Joel",
                                "-metadata", f"track={i + 1}", "-metadata", f"ISRC=USSM1730050{i}", str(f), "-y"],
                               check=True)
                tagio.write(f, {"comment": [com]})
        (top / "Info.txt").write_text("notes both rips share\n")
        return top

    def test_VIN7_two_rips_saved_as_copies_are_split_into_a_copy_each(self):
        top = self.two("Vinyl rip by Packgrog, recorded February 1, 2012")
        plan = self.moving(self.http()).plan(top)
        self.assertEqual(len(plan["albums"]), 2, pl.preview(plan))
        self.assertTrue(all(a["status"] == "ready" and a["split"].get("rips") for a in plan["albums"]))
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip Packgrog SRM-1-1184 ripped 2012]",
                                              "1973 - Piano Man [1977 Vinyl Rip SRM-1-1184]"])
        # (the shared notes go with the first copy, moved - never copied; the other gets a note)
        first, other = sorted(plan["albums"], key=lambda a: a["split"]["index"])
        self.assertEqual([(Path(c["src"]).name, c["op"]) for c in first["companions"]], [("Info.txt", "move")])
        self.assertEqual(other["companions"], [])
        self.assertTrue(any("go with the first copy" in n for n in other["notes"]))
        # (nothing moved before Apply; Apply files both, Undo puts the one folder back as it was)
        before = sorted(p.name for p in top.iterdir())
        res = TestUndo.apply_and_undo(self, plan, top)
        self.assertEqual(res, before)

    def test_VIN7_two_rips_one_name_the_second_stays_where_it_was(self):
        top = self.two("other ripper's words")
        plan = self.moving(self.http()).plan(top)
        self.assertTrue(all(a["status"] == "ready" for a in plan["albums"]), pl.preview(plan))
        first, other = sorted(plan["albums"], key=lambda a: a["split"]["index"])
        self.assertEqual({str(Path(fe["dst"]).parent) for fe in other["files"]}, {str(top)})
        self.assertNotEqual({str(Path(fe["dst"]).parent) for fe in first["files"]}, {str(top)})
        self.assertTrue(any("MOV-4" in n for n in other["notes"]))

    def test_VIN7_two_rips_told_by_their_tags_alone(self):
        # (no copy names: two sets numbered 1..3 each, each rip's own comment and date)
        top = self.two("Vinyl rip by Packgrog, recorded February 1, 2012", b_suffix=" b")
        eng = self.moving(self.http())
        g = eng.albums_in(top)[0]
        sets = engine.rip_sets(g.files)
        self.assertEqual(sorted(len(x) for x in sets), [3, 3])
        plan = eng.plan(top)
        self.assertEqual(len(plan["albums"]), 2, pl.preview(plan))

    def test_VIN7_weak_evidence_holds_and_lists_two_rips(self):
        # (an 8-track rip beside a 4-track one of the same record, its suite as one track - here a
        # second set whose last track is no track of the release: it proves no release of its
        # own: held, both sets named, for Needs Attention)
        top = self.two("Vinyl rip by Packgrog", b_suffix=" b", b_titles=["Track 1", "Track 2", "Track 3 Suite"],
                       b_lens=[10.6, 20.6, 75.0])
        plan = self.moving(self.http()).plan(top)
        [a] = plan["albums"]
        self.assertEqual(a["identification"]["status"], "unresolved")
        self.assertIn("two_rips", a["identification"].get("reason_codes") or [])
        words = a["identification"]["why"][0]
        self.assertIn("two rips of one record are mixed in this folder", words)
        self.assertIn("01. Track 1.flac", words)
        self.assertIn("03. Track 3 Suite b.flac", words)
        self.assertTrue(all(fe["src"] == fe["dst"] for fe in a["files"]))
        import reasons
        from gui.attention import short_reason
        self.assertEqual(short_reason("two_rips"), "Two rips in one folder")
        self.assertIn("two_rips", reasons.CODES)


class VIN3_RipperVersionAndTwoWordRippers(VinBase):
    def test_VIN3_two_transfers_by_one_ripper_of_one_pressing_are_named_apart(self):
        # (Hemispheres 9100 059: PBTHAL's "Final Master" and his "Ultimate Master")
        self.rip("1973 - Piano Man [Vinyl Rip PBTHAL 24bit 96khz Mercury SRM-1-1184]", 96000, comment="Final Master")
        self.rip("1973 - Piano Man [Vinyl Rip PBTHAL 24bit 96khz Mercury SRM-1-1184] (2)", 96000, shift=1,
                 notes="Rush - Piano Man\n24-bit/96kHz Ultimate Master Vinyl Rip\n")
        plan = self.moving(self.http()).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip PBTHAL Final Master SRM-1-1184]",
                                              "1973 - Piano Man [1977 Vinyl Rip PBTHAL Ultimate Master SRM-1-1184]"])
        lines = {ln for a in plan["albums"] for fe in a["files"] for v in fe["changes"].get("comment") or ()
                 for ln in v.splitlines() if ln.startswith("Vinyl rip: ")}
        self.assertEqual(lines, {"Vinyl rip: PBTHAL Final Master · 24-bit/96 kHz",
                                 "Vinyl rip: PBTHAL Ultimate Master · 24-bit/96 kHz"})

    def test_VIN3_your_own_name_for_a_copy_plans_again_unchanged(self):
        import executor
        for name, comment in (("1973 - Piano Man [Vinyl Rip PBTHAL Ultimate Master SRM-1-1184]", None),
                              ("1973 - Piano Man [Vinyl Rip Fran Solo SRM-1-1184]", "US Pressing - Fran Solo, Ediciones MMXVII")):
            with self.subTest(name=name):
                import shutil
                shutil.rmtree(self.lib, ignore_errors=True)
                shutil.rmtree(self.tmp / "library", ignore_errors=True)
                top = self.rip(name, 96000, comment=comment)
                plan = self.moving(self.http()).plan(top)
                who = "PBTHAL Ultimate Master" if "PBTHAL" in name else "Fran Solo"
                self.assertEqual(self.folders(plan), [f"1973 - Piano Man [1977 Vinyl Rip {who} SRM-1-1184]"])
                executor.Executor(plan).run()
                there = Path(plan["albums"][0]["files"][0]["dst"]).parent
                again = self.moving(self.http()).plan(there)
                self.assertTrue(all(fe["src"] == fe["dst"] and "comment" not in fe["changes"]
                                    for a in again["albums"] for fe in a["files"]))

    def test_VIN3_a_two_word_name_after_vinyl_rip_needs_the_comment_or_scans(self):
        top = self.rip("1973 - Piano Man [Vinyl Rip Fran Solo 24bit 96khz]", 96000)
        self.assertEqual(vr.describe(top, [], comments=["Original US Pressing - Fran Solo, Ediciones MMXVI"])["ripper"],
                         "Fran Solo")
        self.assertEqual(vr.describe(top, [], comments=["something else"], scans="")["ripper"], "")
        # (a signature in the comment printed on the scans names the ripper: the watermark)
        other = self.rip("1973 - Piano Man [Vinyl Rip 24bit 96khz]", 96000)
        self.assertEqual(vr.describe(other, [], comments=["Original US Pressing - Fran Solo, Ediciones MMXVI"],
                                     scans="label side 1 SOLO Ediciones 832-464-1")["ripper"], "Fran Solo")
        self.assertEqual(vr.describe(other, [], comments=["Original US Pressing - Fran Solo, Ediciones MMXVI"],
                                     scans="label side 1 832-464-1")["ripper"], "")


class VIN7_ScansGoWithTheirRip(VIN7_TwoRipsInOneFolder):
    def test_VIN7_each_rips_scans_go_with_it(self):
        # (Hold Your Fire 832-464-1: "covers/" are Fran Solo's scans - "SOLO Ediciones" printed on the
        # labels - and "covers/covers/" with "spectr/" the second rip's own)
        from unittest import mock
        top = self.two("Vinyl rip by Packgrog, recorded February 1, 2012")
        for i in range(1, 4):
            tagio.write(top / f"0{i}. Track {i}.flac", {"comment": ["Original US Pressing - Fran Solo, Ediciones MMXVI"]})
        (top / "covers" / "covers" / "spectr").mkdir(parents=True)
        for rel in ("covers/1 PORTADA.jpg", "covers/5 LADO 1-2-DEADWAX.jpg", "covers/covers/rush 87 apple 1.JPG",
                    "covers/covers/back.JPG", "covers/covers/spectr/afc.png"):
            (top / rel).write_bytes(b"\xff\xd8\xff" + rel.encode() * 20)

        def ocr(paths):
            return " ".join("SOLO Ediciones Q-1" if Path(p).parent.name == "covers" and
                            Path(p).parent.parent == top else "Q-1" for p in paths)
        with mock.patch("vinylrip.scan_text", side_effect=ocr):
            plan = self.moving(self.http()).plan(top)
        self.assertEqual(len(plan["albums"]), 2, pl.preview(plan))
        by = {a["split"]["index"]: a for a in plan["albums"]}
        solo = next(a for a in plan["albums"] if "Fran Solo" in Path(a["files"][0]["dst"]).parent.name)
        other = next(a for a in plan["albums"] if a is not solo)
        rel = lambda a: sorted(str(Path(c["src"]).relative_to(top)) for c in a["companions"])
        self.assertEqual(rel(solo), ["Info.txt", "covers/1 PORTADA.jpg", "covers/5 LADO 1-2-DEADWAX.jpg"])
        self.assertEqual(rel(other), ["covers/covers/back.JPG", "covers/covers/rush 87 apple 1.JPG",
                                      "covers/covers/spectr/afc.png"])
        self.assertTrue(all(c["op"] == "move" for a in plan["albums"] for c in a["companions"]))
        self.assertEqual(Path(solo["files"][0]["dst"]).parent.name, "1973 - Piano Man [1977 Vinyl Rip Fran Solo SRM-1-1184]")
        self.assertTrue(by)


class VIN_Settings(VinBase):
    """vinyl_rip_folders, vinyl_rip_comment, compare_copies: profile settings, each on by default."""
    def moving_with(self, http, **prof):
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        return self.engine(http, operation="move", destination=str(self.dest),
                           naming={"preset": "tagmender", "template": "", "editions": "edition_year",
                                   "album_editions": "edition_year"}, **prof)

    def test_settings_default_on_and_an_older_profile_gets_them_untouched_otherwise(self):
        import profiles as pf
        old = {"name": "mine", "version": 3, "operation": "move", "destination": "/x", "split_versions": False,
               "naming": {"preset": "all-music", "editions": "edition"}}
        p = pf.from_dict(old)
        self.assertEqual([p.get(k) for k in ("vinyl_rip_folders", "vinyl_rip_comment", "compare_copies")],
                         [True, True, True])
        self.assertEqual((p["operation"], p["split_versions"], p["naming"]["preset"], p["naming"]["editions"]),
                         ("move", False, "all-music", "edition"))
        self.assertEqual(pf.validate(dict(old, compare_copies="yes")), ["compare_copies must be true or false"])

    def test_vinyl_rip_folders_off_names_as_any_album_keeps_fields_and_holds_two_rips(self):
        self.rip(PB, 96000, PB_NOTES)
        plan = self.moving_with(self.http(), vinyl_rip_folders=False).plan(self.lib / "Billy Joel")
        self.assertEqual(self.folders(plan), ["1973 - Piano Man [1977 Vinyl Rip]"])
        # (the naming fields stay for a script)
        nm = {"preset": "", "template": "$if2(%albumartist%,%artist%)/%_riptag%/$num(%tracknumber%,2)",
              "editions": "edition_year"}
        self.dest = self.tmp / "library2"
        self.dest.mkdir()
        eng = self.engine(self.http(), operation="move", destination=str(self.dest), naming=nm, vinyl_rip_folders=False)
        self.assertEqual(sorted({Path(fe["dst"]).parent.name for a in eng.plan(self.lib / "Billy Joel")["albums"]
                                 for fe in a["files"]}), ["PBTHAL SRM-1-1184"])
        import shutil
        shutil.rmtree(self.lib)
        top = VIN7_TwoRipsInOneFolder.two(self, "Vinyl rip by Packgrog, recorded February 1, 2012")
        plan = self.moving_with(self.http(), vinyl_rip_folders=False).plan(top)
        [a] = plan["albums"]
        self.assertIn("two_rips", a["identification"].get("reason_codes") or [])

    def test_vinyl_rip_comment_off_writes_no_line(self):
        top = self.rip(PB, 96000, PB_NOTES)
        on = self.moving_with(self.http()).plan(top)
        self.assertTrue(any("Vinyl rip: " in str(fe["changes"].get("comment")) for a in on["albums"] for fe in a["files"]))
        off = self.moving_with(self.http(), vinyl_rip_comment=False).plan(top)
        self.assertFalse(any("Vinyl rip: " in str(fe["changes"].get("comment")) for a in off["albums"] for fe in a["files"]))
        self.assertEqual(self.folders(off), self.folders(on))

    def test_compare_copies_off_measures_nothing(self):
        import argparse
        from unittest import mock
        import tagmender
        self.rip(PB, 96000, PB_NOTES)
        self.rip(OTHER, 192000, shift=1)
        for on in (True, False):
            with self.subTest(on=on):
                prof_ = {"compare_copies": on}
                eng = self.moving_with(self.http(), **prof_)
                plan = eng.plan(self.lib / "Billy Joel")
                args = argparse.Namespace(output=None, json=True, verbose=False, no_measure=False)
                with mock.patch.object(tagmender, "_make_plan", return_value=(eng.profile, plan)), \
                        mock.patch.object(tagmender, "_measure_copies") as measured, mock.patch("builtins.print"):
                    tagmender.cmd_plan(args)
                self.assertEqual(measured.called, on)


class VIN7_ReReview(VinBase):
    """The re-review's findings on VIN-7 (2026-11-17)."""
    FOLDER = VIN7_TwoRipsInOneFolder.FOLDER

    def mk(self, specs, folder=None):
        top = self.lib / "Billy Joel" / (folder or self.FOLDER)
        top.mkdir(parents=True)
        for name, i, secs, rate, fmt, com, freq in specs:
            f = top / name
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                            "-ar", str(rate), "-sample_fmt", fmt, "-ac", "2", "-metadata", f"title={TITLES[i]}",
                            "-metadata", "album=Piano Man", "-metadata", "albumartist=Billy Joel",
                            "-metadata", "artist=Billy Joel", "-metadata", f"track={i + 1}", str(f), "-y"], check=True)
            if com:
                tagio.write(f, {"comment": [com]})
        return top

    def held(self, top):
        plan = self.moving(self.http()).plan(top)
        self.assertEqual(len(plan["albums"]), 1, pl.preview(plan))
        [a] = plan["albums"]
        self.assertIn("two_rips", a["identification"].get("reason_codes") or [], pl.preview(plan))
        self.assertTrue(all(fe["src"] == fe["dst"] for fe in a["files"]))
        return a["identification"]["why"][0]

    def test_VIN7_copy_marks_on_both_rips_never_split(self):
        # N1: the " (1)" fell on the 192 kHz rip for tracks 1 and 3, on the 96 kHz rip for track 2
        L = [x / 1000 for x in LENS]
        specs = []
        for i, t in enumerate(TITLES):
            a_suf, b_suf = ("", " (1)") if i != 1 else (" (1)", "")
            specs.append((f"{i + 1:02d}. {t}{a_suf}.flac", i, L[i], 96000, "s32", "Rip A by Alpha", 300 + 50 * i))
            specs.append((f"{i + 1:02d}. {t}{b_suf}.flac", i, L[i] + 0.6, 192000, "s32",
                          "Vinyl rip by Packgrog, recorded February 1, 2012", 310 + 50 * i))
        self.held(self.mk(specs))
        self.assertFalse(engine.one_rip_each([[Path("/nonexistent")], [Path("/nonexistent2")]]))

    def test_VIN7_a_transcode_of_the_same_rip_is_no_second_copy(self):
        # N2: 24/96 and its own 16/44.1 transcode, every length the same to the sample
        L = [x / 1000 for x in LENS]
        specs = [(f"{i + 1:02d}. {t}.flac", i, L[i], 96000, "s32", "rip A", 300 + 50 * i) for i, t in enumerate(TITLES)]
        specs += [(f"{i + 1:02d}. {t} (1).flac", i, L[i], 44100, "s16", "rip A 16-44", 300 + 50 * i)
                  for i, t in enumerate(TITLES)]
        self.assertIn("a lower-resolution copy of the same rip", self.held(self.mk(specs)))

    def test_VIN7_two_rips_by_tags_only_in_a_vinyl_rip_folder(self):
        # (a CD set's two discs in one folder, numbered 1..n each and untagged, are no two rips)
        L = [x / 1000 for x in LENS]
        specs = [(f"A{i + 1} {t}.flac", i, L[i], 44100, "s16", "Disc one", 300 + 50 * i) for i, t in enumerate(TITLES)]
        specs += [(f"B{i + 1} {t} x.flac", i, L[i] + 0.6, 44100, "s16", "Disc two", 330 + 50 * i)
                  for i, t in enumerate(TITLES)]
        top = self.mk(specs, folder="1973 - Piano Man [CD]")
        eng = self.moving(self.http())
        g = eng.albums_in(top)[0]
        self.assertFalse(engine._vinylish(g))
        self.assertFalse(any("two_rips" in (a["identification"].get("reason_codes") or [])
                             for a in eng.plan(top)["albums"]))

    def test_VIN7_copy_suffix_x_2_only_in_a_vinyl_rip_folder(self):
        fs = [Path(f"/m/X/{i:02d}. T{i}{s}.flac") for i in (1, 2, 3) for s in ("", "_2")]
        self.assertTrue(engine._RIP_COPY_SUFFIX.search("01. T1_2"))
        self.assertFalse(engine._COPY_SUFFIX.search("01. T1_2"))     # (split_versions as before)
        self.assertTrue(engine._COPY_SUFFIX.search("01. T1 (1)"))
        self.assertEqual(len(fs), 6)


class VIN_ReReviewWords(unittest.TestCase):
    def test_VIN3_a_studio_sleeve_or_label_word_is_never_the_ripper(self):
        base = Path("/nonexistent")
        for name, said in (("X [Vinyl Rip Mobile Fidelity 24bit 96khz]", "Mobile Fidelity Sound Lab pressing"),
                           ("X [Vinyl Rip Abbey Road 24bit 96khz]", "Cut at Abbey Road, London"),
                           ("X [Vinyl Rip Gatefold SRM-1-1184]", "Gatefold sleeve")):
            self.assertEqual(vr.describe(base / name, [], comments=[said], scans="")["ripper"], "", name)
        self.assertEqual(vr.own_folder("X [1977 Vinyl Rip Gatefold SRM-1-1184]", "SRM-1-1184")["ripper"], "")
        # (signed, a name is taken: "Fran Solo, Ediciones", "rip by KRONOS")
        self.assertEqual(vr.describe(base / "X [Vinyl Rip Fran Solo SRM-1-3743]", [],
                                     comments=["US Pressing - Fran Solo, Ediciones MMXVII"], scans="")["ripper"],
                         "Fran Solo")
        # (the user's own form, a label after the name: "[Vinyl Rip Fran Solo Mercury SRM-1-1184]")
        self.assertEqual(vr.describe(base / "1977 - A Farewell To Kings [Vinyl Rip Fran Solo Mercury SRM-1-1184]", [],
                                     comments=["US Pressing - Fran Solo, Ediciones MMXIV (Digitalizacion de Vinilo)"],
                                     scans="")["ripper"], "Fran Solo")
        self.assertEqual(vr.caps_in_name("X [Vinyl Rip JP Epic 25-3P-378]"), "")
        self.assertEqual(vr.describe(base / "X [Vinyl Rip KRONOS 24bit 96khz]", [],
                                     comments=["Vinyl rip by KRONOS"], scans="")["ripper"], "KRONOS")

    def test_VIN3_the_year_and_name_bound_to_the_rip(self):
        self.assertEqual(vr.rip_year("Recorded 1981 at Le Studio; ripped 2010"), "2010")
        self.assertEqual(vr.ripper_in_notes("Ripped by Packgrog on a VPI"), "Packgrog")
        self.assertEqual(vr.ripper_in_notes("rip by john at home"), "")
        self.assertEqual(mc.catno_hints("[1999 001 002-1]"), [])

    def test_VIN6_a_blocked_decode_ends_at_its_deadline(self):
        import time
        import dynamics
        from unittest import mock
        import tempfile
        import subprocess as sp
        d = Path(tempfile.mkdtemp())
        f = d / "a.flac"
        sp.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", str(f), "-y"], check=True)
        real = sp.Popen

        def stuck(*a, **k):                         # (a decoder that never writes)
            return real(["sleep", "30"], stdout=sp.PIPE, stderr=sp.DEVNULL)
        t = time.monotonic()
        with mock.patch.object(dynamics.subprocess, "Popen", side_effect=stuck):
            self.assertIsNone(dynamics.measure_file(f, timeout=1.0))
        self.assertLess(time.monotonic() - t, 10)


class VIN2_FolderNumberOverAnotherTaggersTitle(VinBase):
    UK = "0c0b0c0d-7777-4888-9999-aaaabbbbcccc"

    def releases(self):
        us = vinyl_release()
        uk = release(self.UK, [medium(1, '12" Vinyl', TITLES, LENS)], date="1977-09-01", country="GB",
                     catno="9100 059", label="Mercury", barcode="")
        return [us, uk]

    def test_VIN5_the_suffixed_number_first_the_bare_one_only_without_it(self):
        import match_albums as ma
        top = self.rip("1987 - Hold Your Fire [Vinyl Rip 832-464-1 Q-1]", 96000)
        fresh = lambda: ma.read_album(top, "Billy Joel", top.name)          # noqa: E731
        us, eu = {"catnos": ["832 464-1 Q-1"]}, {"catnos": ["832 464-1"]}

        def agrees(album, cand):
            return mc.catnos_agree(album["catnos_from_folder"], cand["catnos"])

        def against(album, cand):
            return not mc.catnos_agree(set(album["catnos_from_folder"]) | set(album.get("catnos_folder_soft") or ()),
                                       cand["catnos"])
        # (a US entry with Q-1 and an EU entry with the bare number: only the US agrees; the EU is not against)
        a = fresh()
        mc.settle_title_numbers(a, [us, eu])
        self.assertEqual((agrees(a, us), agrees(a, eu), against(a, eu)), (True, False, False))
        # (only bare entries: they agree)
        a = fresh()
        mc.settle_title_numbers(a, [eu])
        self.assertTrue(agrees(a, eu))

    def test_VIN2_a_folder_number_no_candidate_carries_hides_nothing(self):
        # (a false folder hint - "[Unit 731 101-1]" - never hides a real number in the album tag)
        album = {"catnos_from_folder": {"731 1011"}, "catnos": {"731 1011"}, "catnos_title_held": ["srm11184"],
                 "catnos_folder_own": ["731 1011"]}
        mc.settle_title_numbers(album, [{"catnos": ["SRM-1-1184"]}])
        self.assertIn("srm11184", album["catnos_from_folder"])
        album = {"catnos_from_folder": {"srm13743"}, "catnos": {"srm13743"}, "catnos_title_held": ["9100059"],
                 "catnos_folder_own": ["srm13743"]}
        mc.settle_title_numbers(album, [{"catnos": ["9100 059"]}, {"catnos": ["SRM-1-3743"]}])
        self.assertNotIn("9100059", album["catnos_from_folder"])

    def test_VIN2_the_folders_catalogue_number_outranks_the_album_tags(self):
        # (Hemispheres "[Vinyl Rip Fran Solo SRM-1-3743]": its files tagged by another tagger with the
        # UK release - album "Hemispheres [Vinyl Rip 24bit 96khz Mercury 9100 059]", its release ID -
        # came out album known with the UK suggested: the album tag's "9100 059" was read as the
        # folder's own number. TAG-2: another tagger's tags are a lead, never your copy's evidence)
        top = self.lib / "Billy Joel" / "1973 - Piano Man [Vinyl Rip Fran Solo SRM-1-1184]"
        for i, t in enumerate(TITLES):
            top.mkdir(parents=True, exist_ok=True)
            f = top / f"{i + 1:02d}. {t}.flac"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={LENS[i] / 1000}", "-ar", "96000",
                            "-sample_fmt", "s32", "-ac", "2", "-metadata", f"title={t}",
                            "-metadata", "album=Piano Man [Vinyl Rip 24bit 96khz Mercury 9100 059]",
                            "-metadata", "albumartist=Billy Joel", "-metadata", "artist=Billy Joel",
                            "-metadata", f"track={i + 1}", "-metadata", f"ISRC=USSM1730050{i}",
                            "-metadata", f"MUSICBRAINZ_ALBUMID={self.UK}", "-metadata", "CATALOGNUMBER=9100 059",
                            "-metadata", "RELEASECOUNTRY=GB", str(f), "-y"], check=True)
        import match_albums as ma
        a = ma.read_album(top, "Billy Joel", top.name)
        self.assertEqual(a["catnos_from_folder"], {"srm11184"})
        plan = self.moving(SetHttp(mb=self.releases())).plan(top)
        [al] = plan["albums"]
        ident = al["identification"]
        picked = ident.get("release_id") or (ident.get("suggested") or {}).get("id") or ""
        self.assertIn(RID, picked + str((ident.get("suggested") or {}).get("ref") or ""), pl.preview(plan))
        self.assertNotIn(self.UK, picked)


class TestUndo:
    @staticmethod
    def apply_and_undo(case, plan, top):
        """Apply PLAN (every file where it said), then undo it: TOP's entries after the undo."""
        import executor
        res = executor.Executor(plan).run()
        case.assertTrue(all(Path(fe["dst"]).exists() for a in plan["albums"] for fe in a["files"]), res)
        executor.undo(plan["job"])
        return sorted(p.name for p in top.iterdir())

if __name__ == "__main__":
    unittest.main()
