"""Tests for dedupe_twins.py. Run: python3 test_dedupe.py  (needs ffmpeg + mutagen)"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
SCRIPT = HERE / "dedupe_twins.py"
OLD = datetime(2022, 12, 1, 10, 0).timestamp()
NEW = datetime(2026, 7, 4, 10, 0).timestamp()


def mk(p, secs, freq=440, when=None, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={secs}", "-ar", "48000", *meta, str(p), "-y"],
                   check=True)
    if when:
        os.utime(p, (when, when))


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True)
    return r.stdout + r.stderr


def plan(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.music = Path(tempfile.mkdtemp())
        self.lib = self.music / "ambient_music"
        self.plan = self.music / "plan.csv"

    def tearDown(self):
        shutil.rmtree(self.music, ignore_errors=True)

    def album(self, artist, name):
        return self.lib / artist / name

    def listing(self, folder):
        f = self.music / "list.csv"
        with open(f, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["FullPath"])
            w.writerow([folder])
        return f

    def go(self, folder, *extra):
        return run("--list", self.listing(folder), "--music-root", self.music,
                   "--plan", self.plan, *extra)

    # ---- the real layout ---------------------------------------------------
    def make_twins(self, a, old_when=OLD, new_when=NEW):
        for n, (name, secs, freq) in enumerate((("ACAaM", 6, 300), ("TSAO", 7, 400), ("WRWFHW", 8, 500)), 1):
            mk(a / f"00{n}.{name}.flac", secs, freq, old_when, title=f"Track {name}", track=n)
            mk(a / f"0{n}.{name.upper()}.flac", secs, freq, new_when, title=f"Track {name}", track=n)

    def test_dry_run_changes_nothing_and_plans_the_older_group(self):
        a = self.album("Aaron Martin", "2018 - Touch Dissolves")
        self.make_twins(a)
        out = self.go(a)
        self.assertIn("ready to clean: 1 (3 older files)", out)
        self.assertIn("Dry run", out)
        self.assertEqual(len(list(a.glob("*.flac"))), 6)                 # untouched
        moves = sorted(r["move"] for r in plan(self.plan) if r["move"])
        self.assertEqual(moves, ["001.ACAaM.flac", "002.TSAO.flac", "003.WRWFHW.flac"])

    def test_apply_moves_to_2delete_and_undo_restores(self):
        a = self.album("Aaron Martin", "2018 - Touch Dissolves")
        self.make_twins(a)
        out = self.go(a, "--apply")
        self.assertIn("Moved 3 older file(s)", out)
        self.assertEqual(sorted(p.name for p in a.glob("*.flac")),
                         ["01.ACAAM.flac", "02.TSAO.flac", "03.WRWFHW.flac"])
        trash = self.music / "2delete" / "ambient_music" / "Aaron Martin" / "2018 - Touch Dissolves"
        self.assertEqual(len(list(trash.glob("*.flac"))), 3)
        log = next(self.plan.parent.glob("twin_moves_*.csv"))
        self.assertIn("Restored 3 file(s)", run("--undo", log))
        self.assertEqual(len(list(a.glob("*.flac"))), 6)

    def test_second_run_finds_nothing_left(self):
        a = self.album("A", "2000 - X")
        self.make_twins(a)
        self.go(a, "--apply")
        self.assertIn("ready to clean: 0", self.go(a))

    # ---- log / cue / other files ------------------------------------------------
    def stamp(self, p, when, text="x"):
        p.write_text(text)
        os.utime(p, (when, when))

    def test_log_and_cue_from_the_older_group_are_moved(self):
        a = self.album("A", "2018 - Old Rip")
        self.make_twins(a)                                        # old 2022, new 2026
        self.stamp(a / "Old Rip.log", OLD + 120)                  # 2 min after the old copies
        self.stamp(a / "Old Rip.cue", OLD - 300)                  # 5 min before
        self.stamp(a / "new.log", NEW + 60)                       # belongs to the new copy
        self.stamp(a / "notes.txt", OLD - 86400)                  # a day earlier: not in the group
        self.stamp(a / "cover.jpg", OLD + 60)                     # image: never moved
        out = self.go(a, "--apply")
        self.assertIn("Moved 5 older file(s)", out)               # 3 audio + log + cue
        left = sorted(p.name for p in a.iterdir())
        self.assertEqual(left, ["01.ACAAM.flac", "02.TSAO.flac", "03.WRWFHW.flac",
                                "cover.jpg", "new.log", "notes.txt"])
        trash = self.music / "2delete" / "ambient_music" / "A" / "2018 - Old Rip"
        self.assertTrue((trash / "Old Rip.log").exists() and (trash / "Old Rip.cue").exists())

    def test_files_between_two_close_groups_stay(self):
        """Real case (Exogenesis): both copies written seconds apart."""
        a = self.album("A", "2023 - Close")
        self.make_twins(a, old_when=NEW, new_when=NEW + 30)
        self.stamp(a / "rip.log", NEW + 15)
        out = self.go(a, "--apply")
        self.assertIn("Moved 3 older file(s)", out)               # audio only
        self.assertTrue((a / "rip.log").exists())
        self.assertIn("left in place (dated between the two groups): rip.log", self.plan.read_text())

    def test_undo_restores_companions_too(self):
        a = self.album("A", "2018 - Undo")
        self.make_twins(a)
        self.stamp(a / "rip.log", OLD + 60)
        self.go(a, "--apply")
        log = next(self.plan.parent.glob("twin_moves_*.csv"))
        self.assertIn("Restored 4 file(s)", run("--undo", log))
        self.assertTrue((a / "rip.log").exists())

    # ---- safety skips -------------------------------------------------------
    def test_dates_split_into_two_groups_even_if_names_mix(self):
        """The rule is by date: the older group goes, whatever the names say."""
        a = self.album("A", "2000 - Names")
        mk(a / "001.ONE.flac", 6, 300, OLD, title="One", track=1)
        mk(a / "01.ONE.flac", 6, 300, NEW, title="One", track=1)
        mk(a / "002.TWO.flac", 7, 400, NEW + 100, title="Two", track=2)   # old-style name, new date
        mk(a / "02.TWO.flac", 7, 400, OLD + 100, title="Two", track=2)
        self.go(a, "--apply")
        self.assertEqual(sorted(p.name for p in a.glob("*.flac")), ["002.TWO.flac", "01.ONE.flac"])

    def test_dates_that_do_not_form_two_groups_are_skipped(self):
        """One pair saved 2022/2026, the other 2026/later 2026: the 'older'
        copy of pair two is newer than the kept copy of pair one."""
        a = self.album("A", "2000 - Mixed")
        mk(a / "001.ONE.flac", 6, 300, OLD, title="One", track=1)
        mk(a / "01.ONE.flac", 6, 300, NEW, title="One", track=1)
        mk(a / "002.TWO.flac", 7, 400, NEW + 100, title="Two", track=2)
        mk(a / "02.TWO.flac", 7, 400, NEW + 200, title="Two", track=2)
        out = self.go(a, "--apply")
        self.assertIn("skipped for a human to look at: 1", out)
        self.assertIn("dates do not split", self.plan.read_text())
        self.assertEqual(len(list(a.glob("*.flac"))), 4)

    def test_older_copy_holding_the_ids_is_skipped(self):
        from mutagen.flac import FLAC
        a = self.album("A", "2000 - Ids")
        mk(a / "001.ONE.flac", 6, 300, OLD, title="One", track=1)
        mk(a / "01.ONE.flac", 6, 300, NEW, title="One", track=1)
        f = FLAC(a / "001.ONE.flac")
        f["musicbrainz_albumid"] = "x"
        f.save()
        os.utime(a / "001.ONE.flac", (OLD, OLD))
        out = self.go(a, "--apply")
        self.assertIn("skipped for a human to look at: 1", out)
        self.assertIn("older copy has IDs", self.plan.read_text())
        self.assertEqual(len(list(a.glob("*.flac"))), 2)

    # ---- --prefer-tagged --------------------------------------------------------
    def tag(self, p, when):
        from mutagen.flac import FLAC
        f = FLAC(p)
        f["musicbrainz_albumid"] = "x"
        f.save()
        os.utime(p, (when, when))

    def test_prefer_tagged_resolves_interleaved_dates(self):
        """Real case (Nicolas Jaar - Sirens): copies written seconds apart, interleaved."""
        a = self.album("Nicolas Jaar", "2016 - Sirens")
        mk(a / "001.ONE.flac", 6, 300, NEW + 2, title="One", track=1)
        mk(a / "01.ONE.flac", 6, 300, NEW + 0, title="One", track=1)
        mk(a / "002.TWO.flac", 7, 400, NEW + 1, title="Two", track=2)
        mk(a / "02.TWO.flac", 7, 400, NEW + 3, title="Two", track=2)
        self.tag(a / "01.ONE.flac", NEW + 0)
        self.tag(a / "02.TWO.flac", NEW + 3)
        self.assertIn("skipped for a human to look at: 1", self.go(a))           # dates alone: skip
        out = self.go(a, "--prefer-tagged", "--apply")
        self.assertIn("Moved 2 older file(s)", out)
        self.assertEqual(sorted(p.name for p in a.glob("*.flac")), ["01.ONE.flac", "02.TWO.flac"])
        self.assertIn("untagged copy (tagged twin kept)", self.plan.read_text())

    def test_prefer_tagged_keeps_an_older_tagged_copy(self):
        """Real case (Remote Vision - The Architecture of Time): the OLDER copy holds the IDs."""
        a = self.album("Remote Vision", "2022 - The Architecture of Time")
        mk(a / "01.TTS.flac", 6, 300, OLD, title="TTS", track=1)
        mk(a / "001.TTS.flac", 6, 300, NEW, title="TTS", track=1)
        self.tag(a / "01.TTS.flac", OLD)
        self.go(a, "--prefer-tagged", "--apply")
        self.assertEqual([p.name for p in a.glob("*.flac")], ["01.TTS.flac"])

    def test_prefer_tagged_does_not_guess_when_tags_are_mixed(self):
        a = self.album("A", "2000 - Both")
        mk(a / "001.ONE.flac", 6, 300, NEW + 0, title="One", track=1)
        mk(a / "01.ONE.flac", 6, 300, NEW + 5, title="One", track=1)
        self.tag(a / "001.ONE.flac", NEW + 0)
        self.tag(a / "01.ONE.flac", NEW + 5)                     # both copies tagged
        mk(a / "002.TWO.flac", 7, 400, NEW + 6, title="Two", track=2)   # pair two's older copy
        mk(a / "02.TWO.flac", 7, 400, NEW + 7, title="Two", track=2)    # is newer than pair one's kept
        out = self.go(a, "--prefer-tagged", "--apply")
        self.assertIn("skipped for a human to look at: 1", out)   # falls back to dates, which don't split
        self.assertEqual(len(list(a.glob("*.flac"))), 4)

    def test_prefer_tagged_still_keeps_flac_over_tagged_opus(self):
        a = self.album("A", "2000 - Fmt")
        mk(a / "01.flac", 6, 300, OLD, title="One", track=1)
        mk(a / "01.opus", 6, 300, NEW, title="One", track=1)
        from mutagen.oggopus import OggOpus
        o = OggOpus(a / "01.opus")
        o["musicbrainz_albumid"] = "x"
        o.save()
        self.go(a, "--prefer-tagged", "--apply")
        self.assertEqual([p.name for p in a.iterdir() if p.is_file()], ["01.flac"])
        self.assertIn("this copy had IDs - tag the kept FLAC", self.plan.read_text())

    def test_different_tracks_are_never_paired(self):
        a = self.album("A", "2000 - Clean")
        mk(a / "CD1" / "01.flac", 6, 300, OLD, title="Intro", track=1)    # same title and length,
        mk(a / "CD2" / "01.flac", 6, 700, NEW, title="Intro", track=1)    # different audio
        mk(a / "CD1" / "02.flac", 9, 500, OLD, title="Main", track=2)
        self.assertIn("no duplicates: 1", self.go(a, "--apply"))
        self.assertEqual(len(list(a.rglob("*.flac"))), 3)

    # ---- FLAC over Opus --------------------------------------------------------
    def test_flac_kept_over_newer_opus_side_by_side(self):
        a = self.album("Geir Jenssen", "2013 - Stromboli")
        for n, (secs, freq) in enumerate(((6, 300), (8, 500)), 1):
            mk(a / f"0{n}.flac", secs, freq, OLD, title=f"Part {n}", track=n)
            mk(a / f"0{n}.opus", secs, freq, NEW, title=f"Part {n}", track=n)  # opus is NEWER
        out = self.go(a, "--apply")
        self.assertIn("Moved 2 older file(s)", out)
        self.assertEqual(sorted(p.name for p in a.iterdir() if p.is_file()), ["01.flac", "02.flac"])
        self.assertIn("lossless kept over opus", self.plan.read_text())

    def test_flac_and_opus_in_separate_subfolders(self):
        a = self.album("Jake Muir", "2018 - Lady's Mantle")
        for n, (secs, freq) in enumerate(((6, 300), (8, 500)), 1):
            mk(a / "FLAC" / f"0{n}.flac", secs, freq, NEW, title=f"Part {n}", track=n)
            mk(a / "Opus" / f"0{n}.opus", secs, freq, OLD, title=f"Part {n}", track=n)
        self.go(a, "--apply")
        self.assertEqual(len(list(a.rglob("*.flac"))), 2)
        self.assertEqual(len(list(a.rglob("*.opus"))), 0)

    def test_root_scan_uses_artist_album_layout(self):
        a = self.album("Alder & Ash", "2017 - Psalms for the Sunder")
        self.make_twins(a)
        b = self.album("Other", "2001 - Fine")
        mk(b / "01.flac", 5, 300, OLD, title="Fine", track=1)
        out = run("--root", self.lib, "--music-root", self.music, "--plan", self.plan)
        self.assertIn("Checking 2 folder(s)", out)
        self.assertIn("ready to clean: 1 (3 older files)", out)


class RealPair(unittest.TestCase):
    """The real files from Touch Dissolves, with their real dates."""
    SRC = Path("/mnt/user-data/uploads/ambient_music/Aaron Martin/2018 - Touch Dissolves")

    def test_real_twins(self):
        old, new = self.SRC / "005.TSUtPC.flac", self.SRC / "05.TSUTPC.flac"
        if not (old.exists() and new.exists()):
            self.skipTest("real files not staged")
        music = Path(tempfile.mkdtemp())
        try:
            a = music / "ambient_music" / "Aaron Martin" / "2018 - Touch Dissolves"
            a.mkdir(parents=True)
            shutil.copy2(old, a / old.name)
            shutil.copy2(new, a / new.name)
            os.utime(a / old.name, (1669883006, 1669883006))        # 2022-12-01, as on the server
            os.utime(a / new.name, (1783142059.968, 1783142059.968))  # 2026-07-04
            lst = music / "l.csv"
            lst.write_text(f"FullPath\n{a}\n")
            out = run("--list", lst, "--music-root", music, "--plan", music / "p.csv", "--apply")
            self.assertIn("Moved 1 older file(s)", out)
            self.assertEqual([p.name for p in a.iterdir()], ["05.TSUTPC.flac"])   # tagged copy kept
            rows = plan(music / "p.csv")
            self.assertEqual([r["same_audio"] for r in rows if r["move"]], ["checksum"])
        finally:
            shutil.rmtree(music, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
