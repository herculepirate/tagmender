# DSC-12 / MED-17 (docs/MATCHING_RULES.md): a "disc N missing" note counts audio discs only - a video
# disc before the CD (Gojira - The Flesh Alive: the Blu-ray, then the CD ripped alone, "2 of 2") is
# never missing from an audio rip. Synthetic release and files: no network.
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import test_v0872_dsc12_partial_sets as d12  # noqa: E402
import plan as pl  # noqa: E402


def video_first(rid=d12.RID):
    """A DVD-Video, then the CD (MusicBrainz positions 1 and 2), as The Flesh Alive lists its
    Blu-ray before its CD."""
    r = d12.the_release(rid, discs=1, dvd=False)
    cd = d12.medium(rid, 1)
    r["media"] = [dict(d12.medium(rid, 1, "DVD-Video"), position=1), dict(cd, position=2)]
    return r


class DSC12VideoNotMissing(d12.Base):
    rels = None

    def http(self):
        return d12.SetHttp(mb=[video_first()])

    def test_DSC12_a_video_disc_is_never_missing(self):
        plan = self.plan_of(self.disc(self.lib / "Billy Joel" / "Double CD1", 1))
        [a] = plan["albums"]
        self.assertEqual(a["status"], "ready", pl.preview(plan))
        self.assertEqual({((fe["changes"].get("discnumber") or ["?"])[0], (fe["changes"].get("totaldiscs") or ["?"])[0])
                          for fe in a["files"]}, {("2", "2")})
        self.assertFalse([n for n in a.get("notes") or [] if "missing" in n], a.get("notes"))
        self.assertFalse(a.get("attention"))


if __name__ == "__main__":
    unittest.main()
