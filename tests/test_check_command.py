# Tests for `tagmender check` (check.py): a read-only audit of the tags of a job's files, a saved
# plan's result, or the albums in a folder. Synthetic files only.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import check  # noqa: E402
import executor  # noqa: E402
import plan as planmod  # noqa: E402
import tagmender  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402

BASE = {}


def flac(path, **tags):
    """A one-second FLAC with these tags (a list value is several values)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "p" not in BASE:
        BASE["p"] = Path(tempfile.mkdtemp(prefix="tm-base-")) / "base.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        str(BASE["p"]), "-y"], check=True)
    shutil.copy(BASE["p"], path)
    t = FLAC(path)
    for k, v in tags.items():
        t[k] = v if isinstance(v, list) else [v]
    t.save()
    return path


def good(album="Album", n=1, **extra):
    tags = {"album": album, "albumartist": "Band", "artist": "Band", "title": f"Song {n}", "tracknumber": str(n)}
    tags.update(extra)
    return tags


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tagmender.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def entry(src, dst=None, op="tag", changes=None, diff=None):
    return {"src": str(src), "dst": str(dst or src), "op": op, "changes": changes or {}, "diff": diff or []}


def album(src_dir, files, tier="exact", status="ready", why=()):
    return {"source_dir": str(src_dir), "status": status, "files": files,
            "identification": {"tier": tier, "why": list(why)}}


def save_plan(path, *albums):
    plan = {"version": planmod.PLAN_VERSION, "job": "t1", "albums": list(albums)}
    planmod.save(plan, path)
    return plan


def fingerprint(root):
    return {str(p): hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-check-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kinds(self, res):
        return sorted(f["kind"] for a in res["albums"] for f in a["findings"])

    def plan_result(self, *albums, applied=None):
        return check.result("p", "plan", check.check_plan(save_plan(self.tmp / "plan.json", *albums), applied))


class Checks(Base):
    def test_clean_album_has_no_findings(self):
        d = self.tmp / "a"
        fs = [flac(d / f"{n}.flac", **good(n=n, musicbrainz_albumid="x")) for n in (1, 2, 3)]
        res = self.plan_result(album(d, [entry(f) for f in fs]))
        self.assertEqual(self.kinds(res), [])
        self.assertTrue(res["ok"])

    def test_missing_tags(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", album="Album", artist="Band", tracknumber="1")
        res = self.plan_result(album(d, [entry(f)], tier="partial"))
        missing = [x["detail"] for a in res["albums"] for x in a["findings"] if x["kind"] == "missing"]
        self.assertEqual(sorted(missing), ["has no albumartist", "has no title"])
        self.assertFalse(res["ok"])

    def test_the_plans_changes_fill_a_missing_tag(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", album="Album", artist="Band", tracknumber="1")
        fe = entry(f, changes={"albumartist": ["Band"]},
                   diff=[{"field": "title", "before": [], "after": ["Song"], "action": "added"}])
        self.assertEqual(self.kinds(self.plan_result(album(d, [fe], tier="partial"))), [])

    def test_repeated_and_gapped_numbers(self):
        d = self.tmp / "a"
        fs = [flac(d / "1.flac", **good(n=1)), flac(d / "2.flac", **good(n=1))]
        self.assertEqual(self.kinds(self.plan_result(album(d, [entry(f) for f in fs], tier="partial"))), ["numbers"])
        e = self.tmp / "b"
        gs = [flac(e / "1.flac", **good(n=1)), flac(e / "2.flac", **good(n=3))]
        self.assertEqual(self.kinds(self.plan_result(album(e, [entry(f) for f in gs], tier="partial"))), ["numbers"])
        note = "tracks 2 of the release are not in this folder: each file is tagged as its own"
        self.assertEqual(self.kinds(self.plan_result(album(e, [entry(f) for f in gs], tier="partial", why=[note]))), [])

    def test_numbers_are_per_disc(self):
        d = self.tmp / "a"
        fs = [flac(d / f"{c}{n}.flac", **good(n=n, discnumber=f"{c}/2")) for c in (1, 2) for n in (1, 2)]
        self.assertEqual(self.kinds(self.plan_result(album(d, [entry(f) for f in fs], tier="partial"))), [])

    def test_featured_guest_in_album_artist(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", **good(albumartist="Band feat. Guest"))
        self.assertIn("featured", self.kinds(self.plan_result(album(d, [entry(f)], tier="partial"))))

    def test_joint_credit_is_a_note_not_a_failure(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", **good(artist="Band & Friend"))
        res = self.plan_result(album(d, [entry(f)], tier="partial"))
        self.assertEqual(self.kinds(res), ["credit"])
        self.assertTrue(res["ok"])

    def test_exact_without_release_id(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", **good())
        self.assertEqual(self.kinds(self.plan_result(album(d, [entry(f)], tier="exact"))), ["ids"])
        g = flac(d / "2.flac", **good(n=2))
        fe = entry(g, changes={"discogs_release_id": ["12"]})
        self.assertEqual(self.kinds(self.plan_result(album(d, [fe], tier="exact"))), ["numbers"])

    def test_album_only_writing_pressing_details(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", **good())
        fe = entry(f, changes={"barcode": ["123"], "catalognumber": ["X-1"]})
        res = self.plan_result(album(d, [fe], tier="album_only"))
        self.assertEqual(self.kinds(res), ["pressing", "pressing"])
        self.assertEqual(self.kinds(self.plan_result(album(d, [fe], tier="exact"))), ["ids"])

    def test_held_albums_are_not_checked(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac")
        self.assertEqual(self.plan_result(album(d, [entry(f)], status="held"))["albums"], [])

    def test_not_applied_when_no_file_exists(self):
        d = self.tmp / "a"
        fe = entry(d / "1.flac", d / "new" / "1.flac", op="move")
        res = self.plan_result(album(d, [fe, entry(d / "2.flac", d / "new" / "2.flac", op="move")]))
        self.assertEqual(self.kinds(res), ["notapplied"])
        self.assertTrue(res["ok"])

    def test_leftover_files_in_the_old_folder(self):
        old, new = self.tmp / "old", self.tmp / "new"
        flac(old / "other.flac", **good(n=9))                    # not in the plan
        (old / "notes.txt").write_text("x")
        (old / ".hidden").write_text("x")
        f = flac(new / "1.flac", **good(musicbrainz_albumid="x"))
        fe = entry(old / "1.flac", f, op="move")
        res = self.plan_result(album(old, [fe]), applied=True)
        left = [x["detail"] for a in res["albums"] for x in a["findings"] if x["kind"] == "leftover"]
        self.assertEqual(len(left), 1)
        self.assertIn("notes.txt", left[0])
        self.assertIn("other.flac", left[0])
        self.assertNotIn(".hidden", left[0])

    def test_no_leftover_when_the_old_folder_is_a_destination(self):
        d = self.tmp / "a"
        f = flac(d / "1.flac", **good(musicbrainz_albumid="x"))
        flac(d / "other.flac", **good(n=9))
        g = d / "renamed.flac"
        shutil.move(f, g)
        res = self.plan_result(album(d, [entry(f, g, op="move")]), applied=True)
        self.assertEqual(self.kinds(res), [])

    def test_a_moved_plan_is_read_at_its_destination(self):
        old, new = self.tmp / "old", self.tmp / "new"
        f = flac(new / "1.flac", **good(albumartist="Band feat. Guest", musicbrainz_albumid="x"))
        fe = entry(old / "1.flac", f, op="move")
        res = self.plan_result(album(old, [fe]))                 # (no applied hint: found by the files)
        self.assertEqual(self.kinds(res), ["featured"])


class Command(Base):
    def setUp(self):
        super().setUp()
        self.old, self.new = self.tmp / "old", self.tmp / "new"
        self.f = flac(self.old / "1.flac", **good(musicbrainz_albumid="x"))
        self.plan = self.tmp / "plan.json"
        save_plan(self.plan, album(self.old, [entry(self.f, self.new / "1.flac", op="move")]))

    def test_plan_target_shows_the_tags_it_would_leave_and_writes_nothing(self):
        save_plan(self.plan, album(self.old, [entry(self.f, self.new / "1.flac", op="move",
                                                    changes={"albumartist": ["Band feat. Guest"]})]))
        before = fingerprint(self.tmp)
        code, out, _ = run("check", str(self.plan))
        self.assertEqual(code, 1)
        self.assertIn("featured", out)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual(fingerprint(self.tmp), before)

    def test_clean_plan_exits_0_and_a_note_alone_exits_0(self):
        code, out, _ = run("check", str(self.plan))
        self.assertEqual(code, 0, out)
        self.assertIn("nothing found", out)
        save_plan(self.plan, album(self.old, [entry(self.f, self.new / "1.flac", op="move",
                                                    changes={"artist": ["Band & Friend"]})]))
        code, out, _ = run("check", str(self.plan))
        self.assertEqual(code, 0)
        self.assertIn("credit", out)

    def test_job_target_reads_the_files_the_job_wrote(self):
        flac(self.new / "1.flac", **good(albumartist="Band ft. Guest"))
        jobs = self.tmp / "jobs"
        (jobs / "j1").mkdir(parents=True)
        shutil.copy(self.plan, jobs / "j1" / "plan.json")
        with mock.patch.object(executor, "jobs_dir", return_value=jobs):
            code, out, _ = run("check", "j1", "--json")
            self.assertEqual(code, 1)
            res = json.loads(out)
            self.assertEqual(res["mode"], "job")
            self.assertEqual(res["counts"]["featured"], 1)
            self.assertEqual(res["counts"]["leftover"], 0)
            code, _, err = run("check", "nope")
            self.assertEqual(code, 1)
            self.assertIn("Not checked", err)

    def test_folder_target_groups_like_scan(self):
        root = self.tmp / "lib"
        flac(root / "A" / "1.flac", **good())
        flac(root / "A" / "2.flac", **good(n=1))                 # repeats number 1
        flac(root / "B" / "1.flac", **good(album="B"))
        code, out, _ = run("check", str(root))
        self.assertEqual(code, 1)
        self.assertEqual(out.count("numbers"), 2)                # the heading line and the summary
        code, out, _ = run("check", str(root / "B"), "--json")
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["mode"], "folder")
        self.assertEqual([a["files"] for a in res["albums"]], [1])
        scan = json.loads(run("scan", str(root), "--json")[1])
        self.assertEqual(len(json.loads(run("check", str(root), "--json")[1])["albums"]), len(scan))

    def test_csv_rows(self):
        root = self.tmp / "lib"
        flac(root / "A" / "1.flac", album="A", artist="Band", albumartist="Band", tracknumber="1")
        out_csv = self.tmp / "out.csv"
        code, _, _ = run("check", str(root), "--csv", str(out_csv))
        self.assertEqual(code, 1)
        lines = out_csv.read_text().splitlines()
        self.assertEqual(lines[0], "album,file,kind,detail")
        self.assertIn("1.flac,missing,has no title", lines[1])

    def test_help_lists_the_command(self):
        self.assertIn("tagmender check", tagmender.__doc__)
        code, out, _ = run("check", "-h")
        self.assertEqual(code, 0)
        self.assertIn("JOB|PLAN|PATH", out)


if __name__ == "__main__":
    unittest.main()
