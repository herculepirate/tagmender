# DSC-12 (docs/MATCHING_RULES.md): an album listed for your attention only ("disc 2 missing",
# another pressing kept apart) counts as ready and is listed under Needs Attention too; one held for
# your decision (another copy of an album you have) needs you. The toolbar's Needs Attention count is
# what its filter lists; the files counted are the album's own (never the album it joins).
# Synthetic plan (tools/gui_screens.sample_plan), Qt offscreen.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0871_main_window as mw  # noqa: E402  (isolates XDG dirs; Qt offscreen)

for _n in dir(mw.MainWindow):
    if _n.startswith("test_"):
        setattr(mw.MainWindow, _n, None)


class DSC12Counts(mw.MainWindow):
    def test_DSC12_the_attention_count_is_what_its_filter_lists(self):
        import gui_screens
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.profile.setCurrentText("Plain")
        ov.path = str(self.tmp)
        plan = gui_screens.sample_plan(self.tmp)
        ready = [a for a in plan["albums"] if a["status"] == "ready" and not a.get("held")
                 and not (a.get("identification") or {}).get("suggested")
                 and ((a.get("identification") or {}).get("scans") or {}).get("state") != "differs"]
        self.assertGreaterEqual(len(ready), 2)
        ready[0]["attention"] = ["disc 2 missing"]                 # (for your attention only)
        ready[0]["files"] = list(ready[0]["files"]) + [dict(ready[0]["files"][0], src="/lib/x/101.flac",
                                                            dst="/lib/x/101.flac", op="tag", joined=True)]
        ready[1]["attention"] = ["another copy of an album you have (disc 1 already filed in 'x')"]
        ready[1]["attention_decide"] = True
        ov.planned(plan)
        mw.settle(self.app)
        albums = ov.model.albums
        listed = [a for a in albums if ov.listed_for_attention(a)]
        need = [a for a in albums if ov.needs_you(a)]
        a0 = next(a for a in albums if a.get("attention") == ["disc 2 missing"])
        a1 = next(a for a in albums if a.get("attention_decide"))
        self.assertTrue(ov.ready_now(a0) and a0 in listed and a0 not in need)
        self.assertTrue(a1 in need and not ov.ready_now(a1))
        seg = self.win.filter_seg
        self.assertEqual(seg.buttons["attention"].accessibleName(), f"Needs Attention, {len(listed)} albums")
        seg.buttons["attention"].click()
        mw.settle(self.app)
        self.assertEqual({id(ov.tree.album_at(r)) for r in range(ov.tree.rowCount())}, {id(a) for a in listed})
        own = sum(len([fe for fe in a.get("files") or [] if not fe.get("joined")]) for a in albums)
        self.assertIn(f"{own} files", ov.counts_line.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
