# HF-3 (docs/MATCHING_RULES.md): an Unresolved album in a watched folder is looked up again
# every 10 minutes for its first day, then daily for the rest of the week, then only when you
# ask. New albums always go before retries. HF-4 stays: an outage holds the album and retries it.
# Whole watcher passes (Watcher._pass) with identification stubbed and the clock mocked:
# no network, no sleeping, no audio decoding.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import logging  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402

MIN, HOUR, DAY = 60, 3600, 86400


class Schedule(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(watcher.UNRESOLVED_FAST_EVERY, 10 * MIN)
        self.assertEqual(watcher.UNRESOLVED_FAST_FOR, DAY)
        self.assertEqual(watcher.UNRESOLVED_SLOW_EVERY, DAY)
        self.assertEqual(watcher.UNRESOLVED_RETRY_FOR, 7 * DAY)

    def test_next_try(self):
        nt, s = watcher.unresolved_next_try, 1_000_000.0
        self.assertEqual(nt(s, s), s + 10 * MIN)
        self.assertEqual(nt(s, s + 23 * HOUR), s + 23 * HOUR + 10 * MIN)
        self.assertEqual(nt(s, s + DAY + 5), s + 2 * DAY + 5)
        self.assertEqual(nt(s, s + 5 * DAY), s + 6 * DAY)
        self.assertEqual(nt(s, s + 6 * DAY + 5), watcher.ASK_ONLY)       # (a day more is past the week)
        self.assertEqual(nt(s, s + 30 * DAY), watcher.ASK_ONLY)


class Passes(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="tm-hf3-")).resolve()
        (self.top / "lib").mkdir()
        (self.top / "in").mkdir()
        self.clock = [time.time()]
        self.t0 = self.clock[0]            # (the times passes() is given count from here)
        self.calls = []                    # album names in the order they were looked up
        self.answer = {}                   # album name -> "unresolved" | "outage" | "exact"
        self.groups = []
        self.cost = 0                      # seconds one lookup takes (on the mocked clock)
        self.prof = pf.from_dict({"name": "t" + uuid.uuid4().hex[:6], "operation": "move",
                                  "destination": str(self.top / "lib"), "unattended": True,
                                  "watch": {"settle_minutes": 0, "interval_seconds": 300},
                                  "sources": [{"path": str(self.top / "in"), "watch": True}]})
        self.db = watcher._db()
        self.db.execute("DELETE FROM processed")
        self.db.execute("DELETE FROM unresolved_since")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.top, ignore_errors=True)

    def drop(self, name, answer="unresolved"):
        d = self.top / "in" / name
        d.mkdir()
        f = d / "01.flac"
        f.write_bytes(b"fLaC" + name.encode())
        old = self.clock[0] - HOUR
        os.utime(f, (old, old))
        os.utime(d, (old, old))
        g = types.SimpleNamespace(folder=d, files=[f], others=False, single=False)
        self.groups.append(g)
        self.answer[name] = answer
        return g

    def key(self, g):
        return watcher.Watcher.key(g)

    def row(self, g):
        return self.db.execute("SELECT status, attempts, next_try FROM processed WHERE folder=?",
                               (self.key(g),)).fetchone()

    def passes(self, at):
        """One pass at each of the times AT (seconds after the start); returns the lookups made."""
        test, start = self, self.t0

        class Stub:
            job = "j" + uuid.uuid4().hex[:8]

            def __init__(self, *_a, **_k):
                pass

            def albums_in(self, _root, skip=None):
                return list(test.groups)

            def identify_all(self, g):
                name = g.folder.name
                test.calls.append(name)
                test.clock[0] += test.cost
                how = test.answer[name]
                if how == "outage":
                    return [pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=[{}],
                                        status="unresolved", tier="unresolved", reason="x",
                                        provider_errors={"musicbrainz": "outage"})]
                return [pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=[{}],
                                    status="unresolved", tier="unresolved", reason="x")]
        w = watcher.Watcher.__new__(watcher.Watcher)
        w.profiles, w.fixed, w.log, w.stop, w.only, w.once = [self.prof], True, logging.getLogger("t"), False, None, True
        before = len(self.calls)
        with mock.patch.object(watcher.eng_mod, "Engine", Stub), \
                mock.patch.object(watcher.time, "time", lambda: self.clock[0]):
            for t in at:
                self.clock[0] = max(self.clock[0], start + t)
                w._pass(self.db, {})
        self.clock[0] = max(self.clock[0], start + (at[-1] if at else 0))
        return self.calls[before:]

    # --- HF-3: the schedule over a week, passes every 5 minutes -----------------------------
    def test_ten_minutes_for_a_day_then_daily_for_the_week_then_never(self):
        g = self.drop("Nobody")
        day1 = self.passes(range(0, DAY, 5 * MIN))
        # first look, then one every 10 minutes (every other 5-minute pass) for the first day
        self.assertEqual(len(day1), 1 + DAY // (10 * MIN) - 1)
        week = self.passes(range(DAY, 7 * DAY, 5 * MIN))
        self.assertEqual(len(week), 6, "daily for the rest of the week")
        self.assertEqual(self.row(g)[0], "unresolved")
        self.assertEqual(self.row(g)[2], watcher.ASK_ONLY)
        after = self.passes(range(7 * DAY, 9 * DAY, HOUR))
        self.assertEqual(after, [], "after the week: only when you ask")

    def test_you_ask_and_it_is_looked_up_again(self):
        g = self.drop("Nobody")
        self.passes(range(0, 8 * DAY, HOUR))
        self.assertEqual(self.row(g)[2], watcher.ASK_ONLY)
        self.db.commit()
        with mock.patch.object(watcher.time, "time", lambda: self.clock[0]):
            self.assertEqual(watcher.forget(g.folder), 1)              # (Process Again / Look Up)
        self.assertEqual(self.passes([8 * DAY + HOUR]), ["Nobody"])
        self.assertEqual(self.row(g)[2], self.clock[0] + 10 * MIN)    # (a fresh first day)

    def test_new_files_restart_the_clock(self):
        g = self.drop("Nobody")
        self.passes(range(0, 8 * DAY, HOUR))
        (g.folder / "01.flac").write_bytes(b"fLaC other files")
        old = self.clock[0] - HOUR
        os.utime(g.folder / "01.flac", (old, old))
        self.assertEqual(self.passes([8 * DAY + HOUR]), ["Nobody"])
        self.assertEqual(self.row(g)[2], self.clock[0] + 10 * MIN)

    def test_never_leaves_it_for_you(self):
        self.prof["retry"]["after_days"] = 0
        g = self.drop("Nobody")
        self.assertEqual(self.passes(range(0, 3 * DAY, 5 * MIN)), ["Nobody"])
        self.assertEqual(self.row(g)[2], watcher.ASK_ONLY)

    def test_record_from_before_hf3_keeps_its_end(self):
        g = self.drop("Nobody")
        self.db.execute("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?)",
                        (self.key(g), self.prof.name, watcher.snapshot(g.folder, g.files)[0], "unresolved",
                         6, 1.0, self.clock[0] - 200 * DAY, ""))
        self.db.commit()
        self.assertEqual(self.passes([0, HOUR]), [], "its old tries were used up: left for you")

    def test_record_from_before_hf3_tried_once_more_then_its_week_is_over(self):
        g = self.drop("Nobody")
        self.db.execute("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?)",
                        (self.key(g), self.prof.name, watcher.snapshot(g.folder, g.files)[0], "unresolved",
                         2, 1.0, self.clock[0] - 30 * DAY, ""))
        self.db.commit()
        self.assertEqual(self.passes([0, HOUR, DAY]), ["Nobody"])
        self.assertEqual(self.row(g)[2], watcher.ASK_ONLY)

    # --- New albums go before retries -------------------------------------------------------
    def test_new_albums_go_before_retries(self):
        self.drop("A Old")
        self.drop("B Old")
        self.passes([0])
        self.drop("C New")
        self.groups.insert(0, self.groups.pop())       # (listed between the others too)
        self.groups.insert(1, self.groups.pop())
        self.assertEqual(self.passes([11 * MIN])[0], "C New")

    def test_retries_never_starve_new_albums(self):
        olds = [self.drop(f"Old {i:02}") for i in range(10)]
        self.passes([0])
        self.cost = 4 * MIN                            # (a slow lookup each)
        self.drop("New 1")
        first = self.passes([20 * MIN])
        self.assertEqual(first[0], "New 1")
        # the retries of the pass stop after its watch interval (RETRY_PASS_SECONDS at least)
        self.assertLess(len(first) - 1, len(olds))
        self.drop("New 2")
        second = self.passes([60 * MIN])
        self.assertEqual(second[0], "New 2", "the next pass starts with its new album, retries after")
        self.assertTrue(set(second[1:]) - set(first[1:]), "the retries left over are done next")

    # --- HF-4: an outage holds the album and retries it -------------------------------------
    def test_outage_during_the_week_is_held_and_retried_soon(self):
        g = self.drop("Nobody")
        self.passes(range(0, 2 * DAY, HOUR))                       # (unresolved, many tries)
        self.assertGreater(self.row(g)[1], watcher.PROVIDER_RETRY.__len__())
        self.answer["Nobody"] = "outage"
        self.assertEqual(self.passes([3 * DAY]), ["Nobody"])
        status, attempts, nxt = self.row(g)
        self.assertEqual((status, attempts), ("provider_error", 1))  # (outage tries counted afresh)
        self.assertEqual(nxt, self.clock[0] + watcher.PROVIDER_RETRY[1])
        # still out: retried on the outage schedule, with no cap (HF-4)
        self.passes(range(3 * DAY + 10 * MIN, 4 * DAY, 10 * MIN))
        self.assertEqual(self.row(g)[0], "provider_error")
        self.assertGreater(self.row(g)[1], 2)
        # back: unresolved again, on the week that started with the first look (not a new one)
        self.answer["Nobody"] = "unresolved"
        self.db.execute("UPDATE processed SET next_try=1 WHERE folder=?", (self.key(g),))
        self.db.commit()
        self.assertEqual(self.passes([4 * DAY + HOUR]), ["Nobody"])
        self.assertEqual(self.row(g)[0], "unresolved")
        self.assertEqual(self.row(g)[2], self.clock[0] + DAY)

    def test_outage_of_a_new_album_is_retried_not_decided(self):
        g = self.drop("Nobody", "outage")
        self.passes([0])
        self.assertEqual(self.row(g)[0], "provider_error")
        self.assertEqual(self.passes([5 * MIN, 31 * MIN]), ["Nobody"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM unresolved_since").fetchone()[0], 0)

    def test_an_outage_hold_never_runs_out_of_tries(self):
        # HF-4: held and retried until the database answers - past retry.max_attempts
        g = self.drop("Nobody", "outage")
        self.passes(range(0, 10 * DAY, HOUR))
        status, attempts, _n = self.row(g)
        self.assertEqual(status, "provider_error")
        self.assertGreater(attempts, 6)


class HF4_OutageOrFault(unittest.TestCase):
    def test_only_a_database_not_answering_is_an_outage(self):
        import socket
        import urllib.error
        self.assertTrue(watcher._network_error(urllib.error.HTTPError("u", 503, "x", {}, None)))
        self.assertTrue(watcher._network_error(urllib.error.HTTPError("u", 429, "x", {}, None)))
        self.assertTrue(watcher._network_error(urllib.error.URLError("down")))
        self.assertTrue(watcher._network_error(socket.timeout()))
        self.assertFalse(watcher._network_error(urllib.error.HTTPError("u", 404, "x", {}, None)))
        self.assertFalse(watcher._network_error(KeyError("a fault of ours")))
        self.assertFalse(watcher._network_error(PermissionError("a file")))


if __name__ == "__main__":
    unittest.main()
