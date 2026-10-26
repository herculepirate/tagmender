# Eighteenth independent review (A): a fragmented MP4 (moof boxes) is never written to.
# mutagen moves only the first fragment's audio offset when the tags grow (and undo could not
# put the others back): such a file is skipped, untouched, and said so.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review18a_common import Base, decoded, encode, executor, pl, target  # noqa: E402

import tagio  # noqa: E402

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class FragmentedM4aRefused(Base):
    def test_never_written_to(self):
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, *FRAGS, duration=3) for n in (1, 2)]
        orig = [f.read_bytes() for f in fs]
        self.assertTrue(all(tagio.fragmented(f) for f in fs))
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop], self.profile())
        a = p["albums"][0]
        # (review 19A: never written to, but it goes with its album as it is - never left behind)
        self.assertTrue(all(fe.get("as_is") and not fe["changes"] for fe in a["files"]), a["files"])
        self.assertIn("fragmented MP4", a["notes"][0])
        executor.Executor(p).run()
        self.assertEqual([f.read_bytes() for f in fs], orig)
        with self.assertRaises(tagio.Unsupported):
            tagio.write(fs[0], {"title": ["x"]})
        self.assertTrue(decoded(fs[0]))


if __name__ == "__main__":
    unittest.main()
