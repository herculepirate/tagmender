# Albums found but not finished: an exact match below the move score whose every track length
# agrees is filed; an album tied only among entries that list no lengths is "album known" and
# filed; the timed check keeps coming back to what is unfinished, quietly, and leaves
# unresolved albums on their own schedule. Generated audio, temporary folders, no network.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import logging
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402

CFG = {"auto_min_confidence": 90.0, "auto_min_margin": 8.0}
TIE = "runner-up 1002 within 0.0 points (likely another pressing)"
WHY = ["durations unavailable", "confidence 66.7 < 90.0", TIE]


def scored(cid, title="Some Album", tracks=10, **kw):
    s = {"confidence": 66.7, "artist_sim": 1.0, "title_sim": 1.0, "tracks_ok": True, "dur_frac": None, "dur_cov": 0,
         "conflict": None, "titles_agree": tracks,
         "cand": {"id": cid, "source": "discogs", "title": title, "tracks": [{"title": "t"}] * tracks}}
    s.update(kw)
    return s


def final(*others, best=None, why=WHY):
    b = best or scored(1)
    return {"status": "review", "best": b, "why": list(why), "scored": [b, *others]}


class NoLengthsTie(unittest.TestCase):
    def test_tied_entries_without_lengths_are_album_known(self):
        out = mc.album_only(final(scored(2)), CFG)
        self.assertTrue(out["no_lengths"])
        self.assertEqual([t["id"] for t in out["tied"]], ["1", "2"])

    def test_not_when_it_is_not_a_perfect_match(self):
        for kw in ({"artist_sim": 0.95}, {"title_sim": 0.95}, {"tracks_ok": False}, {"titles_agree": 9},
                   {"conflict": "a conflict"}, {"dur_frac": 0.5}):
            self.assertIsNone(mc.album_only(final(scored(2), best=scored(1, **kw)), CFG), kw)

    def test_not_when_any_entry_lists_lengths(self):
        self.assertIsNone(mc.album_only(final(scored(2, dur_frac=0.0, dur_cov=1.0)), CFG))    # (all of them wrong)
        self.assertIsNone(mc.album_only(final(scored(2), best=scored(1, dur_cov=0.5)), CFG))  # (half listed)

    def test_not_when_another_album_is_as_close(self):
        self.assertIsNone(mc.album_only(final(scored(2), scored(3, title="Something Else")), CFG))

    def test_not_for_another_doubt(self):
        self.assertIsNone(mc.album_only(final(scored(2), why=WHY + ["barcode differs"]), CFG))

    def test_not_with_nothing_tied(self):
        self.assertIsNone(mc.album_only(final(why=["durations unavailable", "confidence 66.7 < 90.0"]), CFG))


class Filing(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="tm-file-"))
        self.src = self.top / "in" / "Album"
        self.src.mkdir(parents=True)
        self.lib = self.top / "lib"
        self.lib.mkdir()
        self.f = self.src / "01.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "flac",
                        "-y", str(self.f)], check=True)

    def tearDown(self):
        shutil.rmtree(self.top, ignore_errors=True)

    def entry(self, careful=False, organize=None, **kw):
        prof = pf.from_dict(dict({"name": "t", "operation": "move", "destination": str(self.lib),
                                  "sources": [{"path": str(self.top / "in"), "watch": True}],
                                  "matching": {"extra_careful": careful}},
                                 **({"thresholds": {"organize_min_confidence": organize}} if organize else {})))
        tags = {"album": ["Album"], "albumartist": ["Band"], "artist": ["Band"], "title": ["One"],
                "tracknumber": ["1"], "date": ["2021"]}
        p = pl.Proposal(source_dir=str(self.src), files=[str(self.f)], targets=[tags], status="identified",
                        provider="discogs", **kw)
        return pl.build([p], prof, "job")["albums"][0]

    def moved(self, a):
        return a["files"][0]["dst"] != str(self.f)

    def test_exact_below_the_score_with_every_length_is_filed(self):
        a = self.entry(tier="exact", confidence=91.5, margin=25.8, all_lengths=True)
        self.assertTrue(self.moved(a))
        self.assertEqual(a["status"], "ready")
        self.assertTrue(any("every track length agrees" in n for n in a["notes"]))

    def test_without_the_proof_it_stays(self):
        for kw in ({"confidence": 91.5, "margin": 25.8, "all_lengths": False},
                   {"confidence": 89.9, "margin": 25.8, "all_lengths": True},
                   {"confidence": 91.5, "margin": 3.0, "all_lengths": True},
                   {"confidence": 91.5, "margin": None, "all_lengths": True}):
            self.assertFalse(self.moved(self.entry(tier="exact", **kw)), kw)

    def test_proven_by_the_scans_with_every_length_is_filed_on_a_low_score(self):
        a = self.entry(tier="exact", confidence=86.9, margin=0.0, all_lengths=True,
                       scans={"state": "proven", "printed": True, "found": ["catalogue_number"], "against": []})
        self.assertTrue(self.moved(a))
        self.assertTrue(any("your scans show" in n for n in a["notes"]))

    def test_proven_by_the_scans_replaces_the_old_album_title(self):            # BAR-1 / EVI-1
        # (Mercurial Balm: filed on its scans' catalogue number at 88, its album tag "Mercurial
        # Balm [ECM 2269]" was kept below the replace threshold, and the folder named from it)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "flac",
                        "-metadata", "album=Album [ECM 2269]", "-y", str(self.f)], check=True)
        a = self.entry(tier="exact", confidence=88.0, margin=0.0, all_lengths=True,
                       scans={"state": "proven", "printed": True, "found": ["catalogue_number"], "against": []})
        self.assertTrue(self.moved(a))
        self.assertEqual(a["files"][0]["changes"].get("album"), ["Album"])
        self.assertNotIn("ECM", a["files"][0]["dst"])

    def test_scans_alone_or_weak_scans_do_not_file_it(self):
        proven = {"state": "proven", "printed": True, "found": ["barcode"], "against": []}
        for kw in ({"confidence": 86.9, "all_lengths": False, "scans": proven},
                   {"confidence": 79.9, "all_lengths": True, "scans": proven},
                   {"confidence": 86.9, "all_lengths": True, "scans": {"state": "agrees", "found": ["label"], "against": []}},
                   {"confidence": 86.9, "all_lengths": True, "scans": dict(proven, printed=False)},   # (typed in a note)
                   {"confidence": 86.9, "all_lengths": True, "scans": None}):
            self.assertFalse(self.moved(self.entry(tier="exact", margin=0.0, **kw)), kw)

    def test_extra_careful_ignores_the_scans_too(self):
        self.assertFalse(self.moved(self.entry(careful=True, tier="exact", confidence=86.9, margin=0.0, all_lengths=True,
                                               scans={"state": "proven", "printed": True, "found": ["barcode"], "against": []})))

    def test_a_closed_terminal_does_not_end_the_work(self):
        import logsetup

        class Gone:
            encoding = "utf-8"

            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

            def flush(self):
                raise BrokenPipeError(32, "Broken pipe")
        quiet = logsetup.QuietStream(Gone())
        self.assertEqual(quiet.write("waiting"), 7)
        quiet.flush()
        self.assertEqual(quiet.encoding, "utf-8")

    def test_album_known_with_no_lengths_is_filed_and_tagged(self):
        a = self.entry(tier="album_only", confidence=66.7, no_lengths_tie=True)
        self.assertTrue(self.moved(a))
        self.assertEqual(a["status"], "ready")
        self.assertEqual(a["files"][0]["changes"]["album"], ["Album"])

    def test_other_album_only_is_not_filed_below_the_score(self):
        self.assertFalse(self.moved(self.entry(tier="album_only", confidence=92.0)))

    def test_extra_careful_never_files_on_evidence(self):
        self.assertFalse(self.moved(self.entry(careful=True, tier="exact", confidence=91.5, margin=25.8,
                                               all_lengths=True)))
        self.assertFalse(self.moved(self.entry(careful=True, organize=99.0, tier="album_only", confidence=66.7,
                                               no_lengths_tie=True)))


class Due(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT, status TEXT,"
                        " attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")

    def due(self, status, operation="move", next_try=0.0, retry=None):
        self.db.execute("INSERT OR REPLACE INTO processed VALUES ('/a','t','sig',?,1,?,1,'')", (status, next_try))
        data = {"name": "t", "operation": operation, "destination": "/nonexistent/library"}
        if retry:
            data["retry"] = retry
        prof = pf.from_dict(data)
        w = watcher.Watcher.__new__(watcher.Watcher)
        with mock.patch.object(watcher, "snapshot", lambda *_a: ("sig", 0, False)), \
                mock.patch.object(watcher.ex, "load_rules", lambda *_a: []), \
                mock.patch.object(watcher.ex, "State", lambda: mock.Mock(ignored_item=lambda _f: None)):
            return w.due(Path("/a"), prof, self.db, "/a")

    def test_left_behind_by_a_move_profile_comes_back_every_pass(self):
        self.assertEqual(self.due("unchanged"), (True, "retry"))
        self.assertEqual(self.due("done"), (True, "retry"))

    def test_partial_comes_back_every_pass_unless_never(self):
        self.assertEqual(self.due("partial", next_try=time.time() + 9999), (True, "retry"))
        self.assertEqual(self.due("partial", next_try=time.time() + 9999, retry={"after_days": 0, "max_attempts": 6})[0], False)

    def test_unresolved_keeps_its_schedule(self):
        self.assertEqual(self.due("unresolved", next_try=time.time() + 9999), (False, "unresolved"))
        self.assertEqual(self.due("unresolved", next_try=1.0), (True, "retry"))

    def test_finished_in_place_stays_finished(self):
        self.assertEqual(self.due("unchanged", operation="in_place")[0], False)

    def test_partial_outside_a_move_profile_keeps_its_schedule(self):
        self.assertEqual(self.due("partial", operation="copy", next_try=time.time() + 9999)[0], False)


TAGS = {"album": ["Album"], "albumartist": ["Band"], "artist": ["Band"], "title": ["One"], "tracknumber": ["1"],
        "date": ["2021"]}


class TimedPasses(unittest.TestCase):
    """Whole passes of the watcher (Watcher._pass) with identification stubbed."""

    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="tm-pass-")).resolve()
        (self.top / "lib").mkdir()
        self.d = self.top / "in" / "Album"
        self.d.mkdir(parents=True)
        self.f = self.d / "01.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "flac",
                        "-y", str(self.f)], check=True)
        self.looked = 0

    def tearDown(self):
        shutil.rmtree(self.top, ignore_errors=True)

    def proposal(self, **kw):
        return pl.Proposal(source_dir=str(self.d), files=[str(self.f)], **kw)

    def passes(self, n, make, unattended, after_first=None):
        """Runs N passes; returns what each one reported."""
        prof = pf.from_dict({"name": "t", "operation": "move", "destination": str(self.top / "lib"),
                             "unattended": unattended, "sources": [{"path": str(self.top / "in"), "watch": True}]})
        g = types.SimpleNamespace(folder=self.d, files=[self.f], others=False, single=False)
        test = self

        class Stub:
            job = "j" + uuid.uuid4().hex[:8]

            def __init__(self, *_a, **_k):
                pass

            def albums_in(self, _root, skip=None):
                return [g]

            def identify_all(self, _g):
                test.looked += 1
                return [make()]
        w = watcher.Watcher.__new__(watcher.Watcher)
        w.profiles, w.fixed, w.log, w.stop, w.only, w.once = [prof], True, logging.getLogger("t"), False, None, True
        real = watcher.snapshot
        self.key = watcher.Watcher.key(g)
        db = watcher._db()
        said = []
        with mock.patch.object(watcher.eng_mod, "Engine", Stub), \
                mock.patch.object(watcher, "snapshot", lambda *a, **k: (real(*a, **k)[0], 0, False)):
            for i in range(n):
                done = {}
                w._pass(db, done)
                said.append(done)
                if i == 0 and after_first:
                    after_first(db)
        self.row = db.execute("SELECT status, attempts, next_try FROM processed WHERE folder=?", (self.key,)).fetchone()
        db.close()
        return said

    def plans(self):
        return [p for p in watcher.plans_dir().glob("*.json") if self.d.name in p.read_text(encoding="utf8")]

    def test_an_unresolved_album_is_tried_once_and_rescheduled(self):
        def overdue(db):
            db.execute("UPDATE processed SET next_try=1 WHERE folder=?", (self.key,))
            db.commit()
        make = lambda: self.proposal(targets=[{}], status="unresolved", tier="unresolved", reason="x")   # noqa: E731
        self.passes(4, make, unattended=True, after_first=overdue)
        self.assertEqual(self.looked, 2)                                 # (first look, the one overdue retry)
        self.assertEqual(self.row[:2], ("unresolved", 2))
        self.assertGreater(self.row[2], time.time())

    def test_held_back_is_looked_at_every_pass_without_noise(self):
        make = lambda: self.proposal(targets=[dict(TAGS)], status="identified", tier="exact", provider="discogs",   # noqa: E731
                                     confidence=91.5, margin=3.0)
        said = self.passes(4, make, unattended=True)
        self.assertEqual(self.looked, 4)
        self.assertTrue(said[0])
        self.assertEqual([x for x in said[2:] if x], [])                 # (nothing new: nothing reported)

    def test_held_back_is_filed_once_it_can_be(self):
        sure = [False]
        def make():                                                      # noqa: E306
            return self.proposal(targets=[dict(TAGS)], status="identified", tier="exact", provider="discogs",
                                 confidence=91.5, margin=25.8, all_lengths=sure[0])
        def now_sure(_db):                                               # noqa: E306
            sure[0] = True
        self.passes(2, make, unattended=True, after_first=now_sure)
        self.assertFalse(self.f.exists())                                # (moved into the library)
        self.assertEqual(len(list((self.top / "lib").rglob("*.flac"))), 1)

    def test_a_partial_album_to_approve_gets_one_plan_not_one_per_pass(self):
        make = lambda: self.proposal(targets=[dict(TAGS)], status="identified", tier="partial", provider="web",   # noqa: E731
                                     confidence=70)
        self.passes(1, make, unattended=False)
        self.row and self.assertEqual(self.row[0], "partial")
        first = {p.name for p in self.plans()}
        self.assertEqual(len(first), 1)
        self.passes(3, make, unattended=False)
        self.assertEqual({p.name for p in self.plans()}, first)


if __name__ == "__main__":
    unittest.main()
