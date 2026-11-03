# Fifteenth independent review (B): this test failed before its fix.
"""tagio.write() writes the RIFF INFO entries of the CHANGED fields only. Picard
(picard/formats/wav.py WAVFile._save, write_wave_riff_info on by default) writes an INFO entry
for every field of the file's metadata on each save. A WAV whose INFO chunk is stale (written
by a ripper or Windows Explorer: INAM "Track 01", IART "Unknown Artist") while its ID3 title and
artist are right keeps the stale INFO after TagMender tags it - which Explorer, Audacity and
foobar2000 show - where Picard's save puts the title and artist in INFO."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


def info(p):
    from mutagen._riff import RiffFile
    with open(p, "rb") as fh:
        r = RiffFile(fh)
        for c in r.root.subchunks():
            if c.id == "LIST" and getattr(c, "name", None) == "INFO":
                return {s.id: s.read().rstrip(b"\0").decode("windows-1252") for s in c.subchunks()}
    return {}


class WavInfo(Base):
    def test_info_carries_the_title_and_artist_after_tagging(self):
        album = self.lib / "In" / "wav"
        album.mkdir(parents=True)
        files = []
        for i in (1, 2):
            p = album / f"0{i} t.wav"
            # a ripper's WAV: INFO with placeholder title and artist
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-ar", "8000",
                            "-metadata", f"title=Track 0{i}", "-metadata", "artist=Unknown Artist", "-y", str(p)],
                           check=True)
            files.append(p)
        self.assertEqual(info(files[0]).get("IART"), "Unknown Artist")
        from mutagen.id3 import TIT2, TPE1
        from mutagen.wave import WAVE
        for i, f in enumerate(files, 1):                       # its ID3 title and artist are right already
            w = WAVE(str(f))
            w.add_tags()
            w.tags.add(TIT2(text=[f"T{i}"]))
            w.tags.add(TPE1(text=["Real Artist"]))
            w.save()
        self.assertEqual(info(files[0]).get("IART"), "Unknown Artist")
        p = pl.build([self.proposal(source_dir=str(album), files=[str(f) for f in files],
                                    targets=[target(1, "T1"), target(2, "T2")])], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        got = info(files[0])
        self.assertEqual((got.get("INAM"), got.get("IART")), ("T1", "Real Artist"), got)


if __name__ == "__main__":
    unittest.main()
