# Thirteenth review (Picard code): plans settle and writes read back as written in every format.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import subprocess, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import tagio  # noqa: E402

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
          ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"], ".aiff": []}
TARGET = {"performer": ["Prince (The Artist)", "Bob"], "performer:Hammond organ": ["Jon"],
          "performer:guitar": ["Ann"], "releasetype": ["album", "live"], "artists": ["A", "B"],
          "originaldate": ["1999-05-01"], "date": ["2001-05"], "genre": ["Rock", "Pop"], "title": ["T"]}


def blank(tmp, ext, v23=False):
    p = tmp / f"t{ext}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", *CODECS[ext],
                    "-map_metadata", "-1", "-y", str(p)], check=True)
    if v23:
        from mutagen.id3 import ID3
        ID3().save(str(p), v2_version=3)
    return p


class Settles(unittest.TestCase):
    def test_every_format(self):
        tmp = Path(_tempfile.mkdtemp())
        for ext, v23 in [(e, False) for e in CODECS] + [(".mp3", True)]:
            p = blank(tmp, ext, v23)
            want = tagio.stored(p, {k: v for k, v in TARGET.items() if tagio.holds(p, k)})
            tagio.write(p, want)
            back = tagio.read(p)
            with self.subTest(format=ext, v23=v23):
                self.assertEqual({k: back.get(k) for k in want}, want)
                self.assertEqual(tagio.stored(p, want), want)            # (stable: the next plan is empty)

    def test_role_keeps_its_case(self):
        tmp = Path(_tempfile.mkdtemp())
        for ext in (".mp3", ".flac"):
            p = blank(tmp, ext)
            tagio.write(p, {"performer:Hammond organ": ["Jon"], "performer:EWI": ["Mo"]})
            self.assertEqual({k: v for k, v in tagio.read(p).items() if k.startswith("perf")},
                             {"performer:Hammond organ": ["Jon"], "performer:EWI": ["Mo"]})

    def test_v23_title_change_keeps_dates(self):
        from mutagen.id3 import ID3, TDOR, TDRC
        tmp = Path(_tempfile.mkdtemp())
        p = blank(tmp, ".mp3")
        i = ID3()
        i.add(TDOR(encoding=3, text=["1999-05-01"]))
        i.add(TDRC(encoding=3, text=["2001-05"]))
        i.save(str(p), v2_version=3)
        tagio.write(p, {"title": ["x"]})
        b = tagio.read(p)
        self.assertEqual((b["originaldate"], b["date"]), (["1999-05-01"], ["2001-05"]))


if __name__ == "__main__":
    unittest.main()
