# MED-22c / DSC-11 (docs/MATCHING_RULES.md): an SACD ISO's stereo and multichannel layers ripped
# into "Stereo/" and "Multichannel/" and tagged apart by their marks ("Kodo One Earth Tour Special
# [Stereo DSD Rip]" beside "... [Multichannel 5.1]") are version folders of one album - one plan
# entry, never two albums filed in two places. "Stereo" beside a mark word is the rip's layer; alone,
# "(Stereo)", it stays a title's word (MIX-3). Synthetic files: no network.
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp  # noqa: E402
import engine  # noqa: E402


class MED22c_LayerMarks(Base):
    def make(self, folder, sub, album):
        top = self.lib / "Band E" / folder
        d = top / sub
        d.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            meta = {"title": f"T{i}", "album": album, "albumartist": "Band E", "track": str(i + 1)}
            args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *args, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return top, d

    def test_MED22c_stereo_dsd_rip_beside_multichannel_is_one_album(self):
        top, _d = self.make("KODO - Album K (2004) [SACD] (ISO)", "Multichannel", album="Album K [Multichannel 5.1]")
        self.make(top.name, "Stereo", album="Album K [Stereo DSD Rip]")
        self.assertEqual([x.name for x in engine.version_dirs(top)], ["Multichannel", "Stereo"])
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [(top.name, 6)])

    def test_MED22c_stereo_alone_stays_a_title_word(self):
        self.assertEqual(engine._without_version_marks("Pet Sounds (Stereo)"), "pet sounds stereo")
        self.assertEqual(engine._without_version_marks("Album K [Stereo DSD Rip]"), "album k")
        self.assertEqual(engine._without_version_marks("Album K [Mono 24bit]"), "album k")


if __name__ == "__main__":
    unittest.main()
