# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""The naming script now gets Picard's hidden variables of the TRACK (plan.py `hidden`), but
none of the FILE's: Picard's file naming script reads %_extension%, %_format%, %_bitrate%,
%_sample_rate%, %_bits_per_sample%, %_channels%, %_filename%, %_dirname% from the file's own
metadata (picard/file.py _info, kept through copy_metadata as file-info tags). The common Picard
recipe that keeps lossless and lossy copies apart - $upper(%_extension%)/... - files everything
under the same folder in TagMender ("Album/01 T0" where Picard gives "FLAC/Album/01 T0"), and a
FLAC and an MP3 of one track then collide. Not new in this version."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class FileVariables(Base):
    def test_extension_names_the_folder(self):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir()
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), operation="move", destination=str(dest),
                        sources=[{"path": str(self.lib)}],
                        naming={"preset": "tagmender", "template": "$upper(%_extension%)/%album%/$num(%tracknumber%,2) %title%"})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        got = Path(a["files"][0]["dst"]).relative_to(dest).as_posix()
        self.assertEqual(got, "FLAC/Album/01 T0.flac")


if __name__ == "__main__":
    unittest.main()
