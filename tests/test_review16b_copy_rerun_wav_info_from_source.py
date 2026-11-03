# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""plan.py, copy profile, a WAV album copied by an earlier run: fe["riff"] - the RIFF INFO
written with the tags, "every field the file holds" - is worked out from the SOURCE file's
`merged` tag state (and only when the SOURCE has changes), then the entry is turned into a
"tag" of the earlier COPY (changes2 from the copy's own tags) without recomputing it. The
copy's INFO chunk is then rewritten from the source's values: a field the copy holds
differently (the user retagged the copy; a field the profile preserves) gets the source's
value in INFO while its ID3 tag keeps the copy's. Picard (WAVFile._save) writes INFO from the
metadata of the file it saves - the copy's. Before this version only the changed fields went
into INFO; now every field of the source does."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


def info(p):
    from mutagen._riff import RiffFile
    with open(p, "rb") as fh:
        r = RiffFile(fh)
        for c in r.root.subchunks():
            if c.id == "LIST" and getattr(c, "name", None) == "INFO":
                return {s.id: s.read().rstrip(b"\0").decode("windows-1252") for s in c.subchunks()}
    return {}


def wav(p, freq, **id3):
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:d=1", "-ar", "8000",
                    "-y", str(p)], check=True)
    from mutagen.wave import WAVE
    w = WAVE(str(p))
    w.add_tags()
    w.save()
    tagio.write(p, {k: [v] for k, v in id3.items()})
    return p


class CopyRerunWavInfo(Base):
    def test_copy_info_follows_the_copys_own_tags(self):
        album = self.lib / "In" / "wav"
        files = [wav(album / f"0{i} t.wav", 300 + 100 * i, genre="Rock", title=f"t{i}") for i in (1, 2)]
        prof = self.profile(operation="copy", destination=str(self.dest))
        prop = lambda: self.proposal(source_dir=str(album), files=[str(f) for f in files],  # noqa: E731
                                     targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop()], prof)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        copy = Path(p["albums"][0]["files"][0]["dst"])
        self.assertEqual(info(copy).get("IGNR"), "Rock")
        # the user retags the copy (genre, and a title TagMender will put right again)
        tagio.write(copy, {"genre": ["Jazz"], "title": ["oops"]})
        p2 = pl.build([prop()], prof)
        fe = p2["albums"][0]["files"][0]
        self.assertEqual(fe.get("copy_of"), str(files[0]), fe)
        self.assertIn("title", fe["changes"])
        self.assertEqual(executor.Executor(p2).run()["counts"], {"done": 1})
        self.assertEqual(tagio.read(copy).get("genre"), ["Jazz"])
        self.assertEqual(info(copy).get("IGNR"), "Jazz",
                         "the copy's INFO genre is rewritten from the source file, not from the copy's tags")


if __name__ == "__main__":
    unittest.main()
