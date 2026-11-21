# 0.8.94 LKQ-1: an album queued to be looked up (its folder dropped while another was read, say)
# and then taken by a look-up with the same sources is no longer queued. It stayed queued: its
# result in, its row said "Waiting…" while the line under the list said "1 done" and counted it
# queued too ("Then 6 albums queued": Tool - Lateralus, Billy Joel, Peter Gabriel). No network.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0884_lookup_feed as lf  # noqa: E402
from test_gui import wait  # noqa: E402


def load_tests(loader, tests, pattern):
    """Only this file's own tests: LKP-1's set-up and helpers are inherited, never its tests."""
    names = [n for n in vars(LookUpQueueState) if n.startswith("test_")]
    return unittest.TestSuite(LookUpQueueState(n) for n in sorted(names))


class LookUpQueueState(lf.FeedOverview):
    def test_LKQ1_a_queued_album_looked_up_now_is_not_queued_too(self):
        self.cloning()                                        # (side by side: H found while G waits)
        ov = self.ov
        self.listed()
        g, h = self.row("2005 - Album G"), self.row("2005 - Album H")
        for a in (g, h):                                      # (as joined() queues them)
            ov.queue[ov.album_key(a)] = ("profile", False)
        gate = self.gate("2005 - Album G")
        ov.look_up("profile", [g, h])
        self.assertEqual(ov.queue, {})
        self.assertNotIn("queued", ov.summary.text())
        hk = ov.album_key(h)
        self.assertTrue(wait(lambda: hk not in ov.states, self.app, timeout=30), ov.summary.text())
        self.assertTrue(ov.running)
        self.assertNotEqual(self.status("2005 - Album H"), "Waiting…")     # (done: not waiting again)
        self.assertNotIn("queued", ov.summary.text())
        gate.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual((ov.queue, ov.states, ov.looking), ({}, {}, {}))

    def test_LKQ1_one_queued_with_other_sources_still_waits_for_them(self):
        self.cloning()
        ov = self.ov
        self.listed()
        h = self.row("2005 - Album H")
        hk = ov.album_key(h)
        ov.queue[hk] = ("discogs_only", False)
        gate = self.gate("2005 - Album G")
        ov.look_up("profile", [self.row("2005 - Album G"), h])
        self.assertEqual(ov.queue, {hk: ("discogs_only", False)})
        self.assertTrue(wait(lambda: hk not in ov.states, self.app, timeout=30), ov.summary.text())
        self.assertEqual(self.status("2005 - Album H"), "Waiting…")         # (it is: Discogs only next)
        gate.set()
        self.assertTrue(wait(lambda: not ov.running and not ov.queue, self.app), ov.summary.text())

    def test_LKQ1_a_folder_read_during_a_look_up_does_not_queue_an_album_it_looks_up(self):
        ov = self.ov
        self.listed()
        g, h = self.row("2005 - Album G"), self.row("2005 - Album H")
        gate = self.gate("2005 - Album G")
        ov.look_up("profile", [g, h])
        from unittest import mock
        from gui import overview
        with mock.patch.object(overview, "_list_first", lambda: False):   # (added folders: looked up)
            ov.joined([], [h["source_dir"]], streamed=[h])      # (its folder read again meanwhile)
        self.assertNotIn(ov.album_key(h), ov.queue)
        gate.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(ov.queue, {})


if __name__ == "__main__":
    sys.exit(not
             unittest.TextTestRunner(verbosity=2).run(load_tests(None, None, None)).wasSuccessful())
