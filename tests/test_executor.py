# Tests for plan.py and executor.py: previews, safe writes, rules before writing, organisation,
# collisions, interruption and recovery, rollback, undo. Real audio files; /dev/shm (tmpfs)
# serves as a second filesystem for cross-filesystem moves.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

SHM = Path("/dev/shm")
CROSS_FS = SHM.is_dir() and os.access(SHM, os.W_OK) and os.stat(SHM).st_dev != os.stat(tempfile.gettempdir()).st_dev


def audio(p, freq=440, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    codec = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame"], ".opus": ["-c:a", "libopus"],
             ".m4a": ["-c:a", "aac"]}[p.suffix]
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    *codec, *meta, "-y", str(p)], check=True)
    return p


def target(n, title, album="Real Album", artist="Real Artist", **extra):
    t = {"album": [album], "albumartist": [artist], "artist": [artist], "title": [title],
         "tracknumber": [str(n)], "totaltracks": ["2"], "discnumber": ["1"], "totaldiscs": ["1"],
         "date": ["2001-05-01"], "discogs_release_id": ["777"], "label": ["Label"], "catalognumber": ["CAT 1"]}
    t.update(extra)
    return t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})         # no default rules unless a test adds them
        logsetup.reset()
        self.lib = self.tmp / "lib"
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1 = audio(self.album / "01 a.flac", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = audio(self.album / "02 b.flac", 550, album="Some Album", title="b", artist="Some Artist")
        (self.album / "rip.cue").write_text('FILE "01 a.flac" WAVE\n  TRACK 01 AUDIO\nFILE "02 b.flac" WAVE\n'
                                            '  TRACK 02 AUDIO\n')
        (self.album / "rip.log").write_text("EAC log")
        self.dest = self.tmp / "dest"
        self.dest.mkdir()

    def tearDown(self):
        logsetup.reset()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def profile(self, **kw):
        d = {"name": "t", "sources": [{"path": str(self.lib)}], "thresholds": {"organize_min_confidence": 95}}
        d.update(kw)
        return pf.from_dict(d)

    def proposal(self, conf=97.0, **kw):
        d = dict(source_dir=str(self.album), files=[str(self.f1), str(self.f2)],
                 targets=[target(1, "First"), target(2, "Second")], provider="discogs", release_id="777",
                 confidence=conf, margin=20.0, evidence=["artist", "title", "2/2 durations"],
                 release={"album": ["Real Album"], "albumartist": ["Real Artist"]})
        d.update(kw)
        return pl.Proposal(**d)

    def run_plan(self, prof, *props):
        p = pl.build(list(props), prof)
        return p, executor.Executor(p).run()


class InPlace(Base):
    def test_preview_then_apply_then_nothing_to_do(self):
        prof = self.profile()
        d1 = payload.digest(self.f1)
        p = pl.build([self.proposal()], prof)
        text = pl.preview(p)
        self.assertIn("changed   album: Some Album => Real Album", text)
        self.assertIn("added     discogs_release_id", text)
        self.assertEqual(self.f1.read_bytes()[:4], b"fLaC")
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])          # a plan changes nothing
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        t = tagio.read(self.f1)
        self.assertEqual((t["album"], t["title"], t["tagmender"]), (["Real Album"], ["First"], ["Resolved"]))
        self.assertEqual(payload.digest(self.f1), d1)
        self.assertFalse(list(self.album.glob(".*.part*")))
        again = pl.build([self.proposal()], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged")              # no rewrites on a repeat run

    def test_field_policies(self):
        prof = self.profile(field_policy={"default": "replace", "fields": {
            "album": "lock", "title": "fill_missing", "label": "preserve", "comment": "remove"}})
        tagio.write(self.f1, {"comment": ["old note"], "label": ["Mine"]})
        p = pl.build([self.proposal()], prof)
        rows = {r["field"]: r["action"] for r in p["albums"][0]["files"][0]["diff"]}
        self.assertEqual(rows["album"], "locked")
        self.assertEqual(rows["title"], "preserved")
        self.assertEqual(rows["label"], "preserved")
        self.assertEqual(rows["comment"], "removed")
        executor.Executor(p).run()
        t = tagio.read(self.f1)
        self.assertEqual((t["album"], t["title"], t["label"]), (["Some Album"], ["a"], ["Mine"]))
        self.assertNotIn("comment", t)

    def test_low_confidence_adds_missing_but_does_not_replace(self):
        prof = self.profile(thresholds={"replace_min_confidence": 95, "auto_min_confidence": 80,
                                        "organize_min_confidence": 95})
        p = pl.build([self.proposal(conf=85)], prof)
        rows = {r["field"]: r["action"] for r in p["albums"][0]["files"][0]["diff"]}
        self.assertTrue(rows["album"].startswith("kept"))
        self.assertEqual(rows["discogs_release_id"], "added")

    def test_discogs_only_release_is_resolved_without_musicbrainz_ids(self):
        prof = self.profile()
        self.run_plan(prof, self.proposal())
        t = tagio.read(self.f1)
        self.assertEqual(t["tagmender"], ["Resolved"])
        self.assertNotIn("musicbrainz_albumid", t)
        self.assertEqual(t["discogs_release_id"], ["777"])

    def test_unresolved_is_marked_with_codes(self):
        prof = self.profile()
        self.run_plan(prof, self.proposal(status="unresolved", targets=[], confidence=0,
                                          reason="runner-up 12 within 1.0 points - likely another pressing"))
        t = tagio.read(self.f2)
        self.assertEqual(t["tagmender"], ["Unresolved"])
        self.assertTrue(t["tagmender_reason"][0].startswith("ambiguous_release: "))
        self.assertEqual(t["album"], ["Some Album"])

    def test_resolving_later_clears_the_old_reason(self):
        prof = self.profile()
        tagio.write(self.f1, {"tagmender": ["Unresolved"], "tagmender_reason": ["no_candidate"]})
        tagio.write(self.f2, {"tagmender": ["Unresolved"], "tagmender_reason": ["no_candidate"]})
        self.run_plan(prof, self.proposal())
        self.assertNotIn("tagmender_reason", tagio.read(self.f1))


    def test_disc_set_cover_goes_in_the_album_folder_not_disc_01(self):
        from PIL import Image
        box = self.lib / "Band" / "2005 - Box"
        f1 = audio(box / "Disc 01" / "101.flac", 500)
        f2 = audio(box / "Disc 02" / "201.flac", 600)
        front = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (10, 20, 30)).save(front)
        prop = self.proposal(source_dir=str(box), files=[str(f1), str(f2)],
                             targets=[target(1, "A", discnumber=["1"], totaldiscs=["2"]),
                                      target(1, "B", discnumber=["2"], totaldiscs=["2"])],
                             artwork={"front": str(front), "source": "test"})
        entry = pl.build([prop], self.profile())["albums"][0]
        self.assertEqual(entry["artwork"]["file"], str(box / "cover.jpg"))


class Rules(Base):
    def add_rule(self, value="[no vox]", field="album", action="ignore", target="album"):
        ex.save_rule(ex.Rule.from_dict({"id": "r", "name": "No vox", "action": action, "target": target,
                                        "when": {"all": [{"field": field, "op": "contains", "value": value}]}}))

    def test_rule_on_the_identified_title_excludes_before_any_write(self):
        self.add_rule()
        prof = self.profile()
        before = self.f1.read_bytes()
        p, res = self.run_plan(prof, self.proposal(release={"album": ["Real Album [No Vox]"]}))
        a = p["albums"][0]
        self.assertEqual((a["status"], a["label"], a["rule"]["id"]), ("excluded", "Ignored", "r"))
        self.assertIn("Ignored by rule 'No vox'", pl.preview(p))
        self.assertEqual(self.f1.read_bytes(), before)
        self.assertEqual(res["albums"][0]["label"], "Ignored")
        self.assertTrue(any(h[3] == "Ignored" for h in ex.State().history()))

    def test_rule_added_after_planning_cancels_pending_operations(self):
        prof = self.profile()
        p = pl.build([self.proposal(release={"album": ["Real Album [no vox]"]})], prof)
        self.assertEqual(p["albums"][0]["status"], "ready")
        self.add_rule(action="skip")
        before = self.f1.read_bytes()
        res = executor.Executor(p).run()
        self.assertEqual((res["albums"][0]["status"], res["albums"][0]["label"]), ("excluded", "Skipped"))
        self.assertEqual(self.f1.read_bytes(), before)

    def test_file_rule_in_a_mixed_folder_spares_the_other_album(self):
        other = audio(self.album / "03 other.flac", 660, album="Unrelated", title="x")
        self.add_rule(value="Unrelated", target="album")
        prof = self.profile()
        p, _ = self.run_plan(prof, self.proposal(release={}),
                             pl.Proposal(source_dir=str(self.album), files=[str(other)], targets=[target(1, "X")],
                                         confidence=97, provider="discogs", release={"album": ["Unrelated"]}))
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "excluded"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
        self.assertEqual(tagio.read(other)["album"], ["Unrelated"])

    def test_process_anyway_override(self):
        self.add_rule(value="Some Album")
        ex.State().allow(self.album)
        p, _ = self.run_plan(self.profile(), self.proposal(release={}))
        self.assertEqual(p["albums"][0]["status"], "ready")


class Organise(Base):
    def test_move_with_naming_companions_and_cue_rewrite(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        d1 = payload.digest(self.f1)
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(res["counts"], {"done": 1}, res)
        new_dir = self.dest / "Real Artist" / "2001 - Real Album"
        self.assertEqual(sorted(x.name for x in new_dir.iterdir()), ["01.F.flac", "02.S.flac", "rip.cue", "rip.log"])
        self.assertEqual(payload.digest(new_dir / "01.F.flac"), d1)
        self.assertIn('FILE "01.F.flac" WAVE', (new_dir / "rip.cue").read_text())
        self.assertFalse(self.album.exists())                      # emptied folders removed
        self.assertTrue(self.lib.exists())                         # never a profile root

    def test_reorganising_a_library_within_itself(self):
        prof = self.profile(operation="move", destination=str(self.lib))       # destination = the source root
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((self.lib / "Real Artist" / "2001 - Real Album" / "01.F.flac").exists())
        self.assertFalse(self.album.exists())
        self.assertTrue(self.lib.exists())
        again = pl.build([self.proposal(files=[str(self.lib / "Real Artist" / "2001 - Real Album" / n)
                                               for n in ("01.F.flac", "02.S.flac")],
                                        source_dir=str(self.lib / "Real Artist" / "2001 - Real Album"))], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged")              # already in place: nothing to do

    def test_file_already_at_its_destination_is_tagged_not_moved(self):
        final = self.lib / "Real Artist" / "2001 - Real Album"
        final.mkdir(parents=True)
        f1 = final / "01.F.flac"
        shutil.move(str(self.f1), f1)                                  # already named as the template says
        prof = self.profile(operation="move", destination=str(self.lib))
        prop = self.proposal(files=[str(f1), str(self.f2)], source_dir=str(final))
        p = pl.build([prop], prof)
        ops = {Path(fe["src"]).name: fe["op"] for fe in p["albums"][0]["files"]}
        self.assertEqual(ops["01.F.flac"], "tag")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(tagio.read(f1)["album"], ["Real Album"])
        self.assertTrue((final / "02.S.flac").exists())

    def test_low_confidence_move_tags_in_place_and_says_why(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p, _ = self.run_plan(prof, self.proposal(conf=91))
        self.assertTrue(self.f1.exists())
        self.assertIn("tagged in place, not moved", " ".join(p["albums"][0]["notes"]))
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])

    def test_lossy_profile_copies_lossy_only_and_leaves_sources_alone(self):
        mp3 = audio(self.album / "03 c.mp3", 700, album="Some Album", title="c")
        prof = pf.from_dict(dict(pf.EXAMPLES["Lossy"], destination=str(self.dest),
                                 sources=[{"path": str(self.lib)}]))
        before = {f: f.read_bytes() for f in (self.f1, mp3)}
        prop = self.proposal(files=[str(self.f1), str(mp3)], targets=[target(1, "First"), target(2, "Third")])
        p, res = self.run_plan(prof, prop)
        skipped = p["albums"][0]["skipped"]
        self.assertEqual([(Path(s["file"]).name, s["rule"]) for s in skipped], [("01 a.flac", "profile formats")])
        copies = list(self.dest.rglob("*.mp3"))
        self.assertEqual([c.name for c in copies], ["02.T.mp3"])
        self.assertEqual(tagio.read(copies[0])["title"], ["Third"])
        self.assertFalse(list(self.dest.rglob("*.flac")))
        for f, b in before.items():
            self.assertEqual(f.read_bytes(), b)                     # copy never changes the original

    def test_missing_destination_is_never_created(self):
        gone = self.tmp / "unmounted"
        prof = self.profile(operation="copy", destination=str(gone))
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(p["albums"][0]["status"], "blocked")
        self.assertIn("not available", p["albums"][0]["conflicts"][0])
        self.assertFalse(gone.exists())

    def test_collisions_get_stable_suffixes_and_nothing_is_overwritten(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        squatter = self.dest / "Real Artist" / "2001 - Real Album" / "01.F.flac"
        squatter.parent.mkdir(parents=True)
        squatter.write_bytes(b"someone else's file")
        prop = self.proposal(targets=[target(1, "First"), target(1, "Fine")])    # both -> 01.F.flac
        p1 = pl.build([prop], prof)
        names = [Path(f["dst"]).name for f in p1["albums"][0]["files"]]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(" ~" in n for n in names))
        p2 = pl.build([prop], prof)
        self.assertEqual(names, [Path(f["dst"]).name for f in p2["albums"][0]["files"]])   # stable
        executor.Executor(p1).run()
        self.assertEqual(squatter.read_bytes(), b"someone else's file")

    def test_case_insensitive_collision_inside_a_plan(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        prop = self.proposal(targets=[target(1, "abc"), target(1, "ABC")])
        names = [Path(f["dst"]).name.casefold() for f in pl.build([prop], prof)["albums"][0]["files"]]
        self.assertEqual(len(set(names)), 2)

    def test_long_names_are_shortened_within_limits(self):
        prof = self.profile(operation="copy", destination=str(self.dest),
                            filenames={"max_name_bytes": 40, "max_dir_bytes": 30, "max_path_chars": 4096,
                                       "abbreviations": {"Anniversary": "Anniv."}})
        long_album = "The Twentieth Anniversary Edition Of An Extraordinarily Long Title"
        prop = self.proposal(targets=[target(1, "First", album=long_album), target(2, "Second", album=long_album)])
        p = pl.build([prop], prof)
        for fe in p["albums"][0]["files"]:
            for part in Path(fe["dst"]).relative_to(self.dest).parts:
                self.assertLessEqual(len(part.encode()), 40)
        self.assertTrue(any("shortened" in n or "abbreviated" in n for n in p["albums"][0]["notes"]))
        executor.Executor(p).run()
        self.assertEqual(tagio.read(next(self.dest.rglob("01*.flac")))["album"], [long_album])  # tag keeps it all

    def test_symlink_escaping_the_destination_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.dest / "Real Artist").symlink_to(outside)
        prof = self.profile(operation="copy", destination=str(self.dest))
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertIn("outside the folders", res["albums"][0]["detail"])
        self.assertEqual(list(outside.rglob("*")), [])

    @unittest.skipUnless(CROSS_FS, "needs /dev/shm on another filesystem")
    def test_cross_filesystem_move_verifies_then_removes_source(self):
        shm_dest = Path(tempfile.mkdtemp(dir=SHM))
        self.addCleanup(shutil.rmtree, shm_dest, True)
        prof = self.profile(operation="move", destination=str(shm_dest))
        d1 = payload.digest(self.f1)
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = next(shm_dest.rglob("01.F.flac"))
        self.assertEqual(payload.digest(moved), d1)
        self.assertFalse(self.f1.exists())
        out = executor.undo(p["job"])
        self.assertTrue(self.f1.exists(), out)
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
        self.assertFalse(moved.exists())


class Safety(Base):
    def test_file_changed_after_planning_is_not_touched(self):
        p = pl.build([self.proposal()], self.profile())
        tagio.write(self.f2, {"comment": ["edited by hand"]})
        os.utime(self.f2, ns=(1, 1))
        res = executor.Executor(p).run()
        self.assertEqual(res["albums"][0]["status"], "conflict")
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])

    def test_failure_mid_album_puts_the_album_back(self):
        p = pl.build([self.proposal()], self.profile())
        before = {f: tagio.read(f) for f in (self.f1, self.f2)}
        real = tagio.write
        calls = []

        def flaky(path, changes):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk went away")
            return real(path, changes)
        with mock.patch.object(tagio, "write", flaky):
            res = executor.Executor(p).run()
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertIn("put back", res["albums"][0]["detail"])
        for f in (self.f1, self.f2):
            self.assertEqual(tagio.read(f), before[f])
        self.assertFalse(list(self.album.glob(".*.part*")))

    def test_insufficient_space(self):
        p = pl.build([self.proposal()], self.profile())
        fake = shutil._ntuple_diskusage(10, 10, 0)
        with mock.patch.object(shutil, "disk_usage", return_value=fake):
            res = executor.Executor(p).run()
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertIn("not enough free space", res["albums"][0]["detail"])

    def test_audio_change_during_tag_write_is_caught(self):
        p = pl.build([self.proposal()], self.profile())
        real = tagio.write

        def corrupting(path, changes):
            real(path, changes)
            data = bytearray(Path(path).read_bytes())
            data[-100] ^= 0xFF
            Path(path).write_bytes(bytes(data))
        before = self.f1.read_bytes()
        with mock.patch.object(tagio, "write", corrupting):
            res = executor.Executor(p).run()
        self.assertIn("audio data differed", res["albums"][0]["detail"])
        self.assertEqual(self.f1.read_bytes(), before)

    def test_another_process_holding_the_file(self):
        p = pl.build([self.proposal()], self.profile())
        with executor.file_lock(self.f1):
            res = executor.Executor(p).run()
        self.assertIn("another TagMender process", res["albums"][0]["detail"])

    def test_plan_cannot_run_twice(self):
        p = pl.build([self.proposal()], self.profile())
        executor.Executor(p).run()
        with self.assertRaises(executor.StepError):
            executor.Executor(p).run()


class Interrupted(Base):
    def test_crash_while_staging_leaves_original_and_recover_cleans_up(self):
        p = pl.build([self.proposal()], self.profile())
        before = self.f1.read_bytes()
        with mock.patch.object(tagio, "write", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        self.assertEqual(executor.pending_jobs(), [p["job"]])
        self.assertFalse(list(self.album.glob(".*.part*")))          # Ctrl-C: the staging copy is removed
        self.assertEqual(self.f1.read_bytes(), before)
        done = executor.recover(p["job"])
        self.assertTrue(any("put back as it was" in d for d in done), done)
        self.assertFalse(list(self.album.glob(".*.part*")))
        self.assertEqual(executor.pending_jobs(), [])

    def test_crash_between_commit_and_source_removal_puts_the_album_back(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        real_unlink = os.unlink

        def crash_on_source_unlink(path, *a, **k):
            if str(path) == str(self.f1):
                raise KeyboardInterrupt
            return real_unlink(path, *a, **k)
        with mock.patch.object(executor.os, "unlink", crash_on_source_unlink):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        new = self.dest / "Real Artist" / "2001 - Real Album" / "01.F.flac"
        self.assertTrue(new.exists() and self.f1.exists())           # both copies: nothing lost
        done = executor.recover(p["job"])
        self.assertTrue(any("put back as it was" in d for d in done), done)
        self.assertFalse(new.exists())                                # albums are never left half-moved
        self.assertTrue(self.f1.exists() and self.f2.exists())
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])


class Undo(Base):
    def test_undo_in_place_restores_tags_and_keeps_audio(self):
        before = {f: (tagio.read(f), payload.digest(f)) for f in (self.f1, self.f2)}
        p, _ = self.run_plan(self.profile(), self.proposal())
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        for f in (self.f1, self.f2):
            self.assertEqual((tagio.read(f), payload.digest(f)), before[f])
        with self.assertRaises(executor.StepError):
            executor.undo(p["job"])

    def test_undo_move_puts_files_companions_and_folders_back(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        cue = (self.album / "rip.cue").read_text()
        p, _ = self.run_plan(prof, self.proposal())
        executor.undo(p["job"])
        self.assertEqual(sorted(x.name for x in self.album.iterdir()), ["01 a.flac", "02 b.flac", "rip.cue", "rip.log"])
        self.assertEqual((self.album / "rip.cue").read_text(), cue)
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_undo_leaves_a_copy_someone_changed(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        p, _ = self.run_plan(prof, self.proposal())
        copy = next(self.dest.rglob("01.F.flac"))
        tagio.write(copy, {"comment": ["mine now"]})
        out = executor.undo(p["job"])
        self.assertTrue(copy.exists())
        self.assertTrue(any("changed after the job" in o for o in out))

    def test_jobs_are_listed_with_history(self):
        p, _ = self.run_plan(self.profile(), self.proposal())
        rows = executor.jobs()
        self.assertEqual((rows[0][0], rows[0][4]), (p["job"], "done"))
        self.assertEqual(executor.job_albums(p["job"])[0][1], "done")


if __name__ == "__main__":
    unittest.main()
