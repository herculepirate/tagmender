# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). Not a regression in the narrow sense (the previous version
# wrote to the file and broke its audio); a gap in today's guard.
# tagio.fragmented looks at the file only when its extension is one of .m4a .m4b .m4p .mp4 .m4r
# .3gp (or it is a staging copy, ".tm-....part"). TagMender's library extensions
# (exclusions.AUDIO_EXT) also hold ".alac" (and ".aac"), and mutagen opens an MP4 by its content
# whatever its name. So for a fragmented ALAC/AAC file named "01 t.alac":
#  - the plan does not see it (plan.Planner.album: tagio.fragmented(f) is False): the preview,
#    the GUI and the watcher show the album READY with the file to be tagged;
#  - at apply, tagio.write is called on the staging copy, whose name matches _STAGE, and
#    refuses: the whole album fails and is rolled back, with a message naming the staging file
#    (".tm-20261026-...-7dcf1bebd56c.alac.part is a fragmented MP4 ...") instead of the user's
#    file; the watcher records it failed and tries again later, failing every time.
# Fix: decide by content, not by name - in tagio.fragmented, check isinstance(mutagen file, MP4)
# or the "ftyp" box (any extension), and name the original file in the message.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, encode, pl, tagio, target  # noqa: E402

FRAGS = ["-c:a", "alac", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class FragmentedAlacExtensionNotDetected(Base):
    def test_plan_reports_it(self):
        fs = [encode(self.lib / "A" / "x" / f"0{n} t.alac", 440 + 100 * n, *FRAGS, duration=3) for n in (1, 2)]
        self.assertEqual(type(tagio.open_file(fs[0])).__name__, "MP4")
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        a = pl.build([prop], self.profile())["albums"][0]
        self.assertTrue(all(fe.get("as_is") and not fe["changes"] for fe in a["files"]),
                        f"a fragmented MP4 named .alac is planned for writing: {a['files']}")


if __name__ == "__main__":
    unittest.main()
