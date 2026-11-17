# tools/perf_check.py: how a run is judged against the targets and the stored baseline, and how a
# real log's "timing" lines are summarised (the measuring itself is the tool's own run).
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import perf_check as pc  # noqa: E402
import stagetimes as st  # noqa: E402


class Judge(unittest.TestCase):
    def test_a_missed_target_fails(self):
        bad = pc.judge({"first_row_s": 2.5, "ui_stall_max_ms": 40.0}, {})
        self.assertEqual(len(bad), 1)
        self.assertIn("first_row_s", bad[0])

    def test_more_than_20_percent_slower_than_the_baseline_fails_but_noise_does_not(self):
        base = {"all_looked_up_s": 10.0, "plan_s": 0.20, "ui_stall_max_ms": 50.0}
        self.assertEqual(pc.judge({"all_looked_up_s": 11.9, "plan_s": 0.44, "ui_stall_max_ms": 70.0}, base), [])
        bad = pc.judge({"all_looked_up_s": 12.5, "plan_s": 0.20, "ui_stall_max_ms": 50.0}, base)
        self.assertEqual(len(bad), 1)
        self.assertIn("all_looked_up_s", bad[0])
        self.assertIn("slower than the baseline", bad[0])

    def test_a_scan_scaled_target(self):
        bad = pc.judge({"scan_targets": {"scan_real_first_s": {"value": 9.0, "target": 6.0, "folders": 1000}}}, {})
        self.assertIn("1000 folders", bad[0])

    def test_ai_calls_on_proven_albums_fail_at_one(self):
        self.assertTrue(pc.judge({"ai_calls_proven": 1}, {}))
        self.assertFalse(pc.judge({"ai_calls_proven": 0}, {}))


class LogSummary(unittest.TestCase):
    def test_median_p95_and_the_stages(self):
        lines = ["x INFO  job=j engine provider=-  timing scan /m: 3 albums in 1.0 s (tags 0.5 s)"]
        for i, t in enumerate((10.0, 20.0, 30.0, 100.0)):
            lines.append("x INFO  " + st.line(f"/m/A{i}", t, {"musicbrainz": t / 2, "ocr": 1.0}))
        s = pc.log_summary(lines)
        self.assertEqual(s["albums"], 4)
        self.assertEqual(s["median_s"], 25.0)
        self.assertAlmostEqual(s["p95_s"], 89.5)
        self.assertEqual(s["stages_s"]["musicbrainz"], 80.0)
        self.assertEqual(s["stages_s"]["ocr"], 4.0)
        self.assertEqual(list(s["stages_s"])[0], "musicbrainz")


if __name__ == "__main__":
    unittest.main()
