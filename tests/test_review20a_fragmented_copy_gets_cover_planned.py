# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New code (plan.py's "as_is"); the previous version skipped
# the fragmented copy's album differently and fails this test too.
# Copy profile, the copy made by an earlier run is a fragmented MP4 (remuxed since - e.g. by a
# streaming server's or a phone sync's "optimise" - same audio packets, so payload.digest still
# finds it as the earlier copy), its source is not. plan.Planner.album's earlier-copy branch
# drops the copy's tag changes (`if fe.get("as_is") or tagio.fragmented(dst)`) but does not mark
# the entry "as_is": the cover step (and the artist photo for other extensions) only looks at
# fe.get("as_is") and has_front(fe["src"]) - fe["src"] is the copy, it has no front cover - so
# the copy is put into artwork.embed_into and its op turned from "none" into "tag":
#  - the plan says the cover is embedded into it and nothing about it being fragmented;
#  - executor.embed_front then refuses it (tagio.Unsupported), the album fails and is rolled
#    back - the other tracks' new tags and cover too - and the next plan is the same: the album
#    never settles, failing on every run.
# The reverse (fragmented source, ordinary copy) never gets its copy's tags brought up to date,
# though the copy could be written.
# Fix: in the earlier-copy branch set fe["as_is"] from tagio.fragmented(dst) (the file written
# is the copy, not the source), and reset it from the copy alone (not fe.get("as_is") of the
# source).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import Base, decoded, encode, executor, payload, pl, remux, tagio, target  # noqa: E402


class FragmentedCopy(Base):
    def test_copy_fragmented_since(self):
        from PIL import Image
        d = self.lib / "Some Artist" / "2001 - M"
        fs = [encode(d / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac", duration=3) for n in (1, 2)]
        prof = self.profile(operation="copy", destination=str(self.dest))
        targets = [target(1, "First"), target(2, "Second")]
        prop = self.proposal(source_dir=str(d), files=[str(f) for f in fs], targets=targets)
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        copies = sorted(self.dest.rglob("*.m4a"))
        self.assertEqual(len(copies), 2, copies)
        tmp = remux(copies[0], self.tmp / "frag.m4a", "-movflags", "frag_keyframe+empty_moov",
                    "-frag_duration", "500000")
        tmp.replace(copies[0])                            # (the copy, remuxed fragmented since)
        self.assertTrue(tagio.fragmented(copies[0]))
        self.assertEqual(payload.digest(copies[0]), payload.digest(fs[0]), "(not found as the earlier copy)")
        played = decoded(copies[0])
        img = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (200, 10, 10)).save(img)
        art = {"front": str(img), "source": "test", "width": 600, "height": 600}
        runs = []
        for run in (1, 2):
            prop = self.proposal(source_dir=str(d), files=[str(f) for f in fs], targets=targets, artwork=art)
            p = pl.build([prop], prof)
            a = p["albums"][0]
            embed = (a.get("artwork") or {}).get("embed_into") or []
            res = executor.Executor(p).run()
            runs.append((str(copies[0]) in embed, res["counts"], [r.get("detail") for r in res.get("albums", [])]))
        for run, (planned, counts, detail) in enumerate(runs, 1):
            self.assertNotIn("failed", counts, f"run {run}: the album fails: {detail}; all runs: {runs}")
            self.assertFalse(planned, f"run {run}: the cover is planned into the fragmented copy; all runs: {runs}")
        self.assertEqual(decoded(copies[0]), played)


if __name__ == "__main__":
    unittest.main()
