# Twenty-ninth independent review (A): the Opus covers the previous release embedded - with a
# width and height but no colour depth, which libopusfile (and the players built on it)
# refuses, so they show no cover - are never repaired. This round writes new Opus covers
# without a size (executor.py:731-732, 800-801), but a library tagged by the previous release
# keeps every broken picture: the planner compares the picture's image data only, finds the
# cover "already embedded" and plans nothing, on every run.
# Not a regression (the previous release made them and does not repair them either).
# Fix: in the planner, treat an Opus METADATA_BLOCK_PICTURE with width/height but depth 0 as
# needing a rewrite (re-embed the same image without a size - Picard's form), shown in the
# preview as a cover rewritten; or a one-off repair in `tagmender doctor --fix`.
import base64
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
from mutagen.flac import Picture  # noqa: E402
from mutagen.oggopus import OggOpus  # noqa: E402


class OpusCoverRepair(unittest.TestCase):
    def test_previous_release_opus_cover_is_repaired(self):
        root = Path(tempfile.mkdtemp(prefix="tm29A-opus-"))
        self.addCleanup(shutil.rmtree, root, True)
        ex.CONFIG_DIR, ex.STATE_DIR = root / "config", root / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        album = root / "lib" / "Band" / "Album"
        data = c.jpeg_bytes((120, 30, 30), 600)
        front = root / "front.jpg"
        front.write_bytes(data)
        files = []
        for n in (1, 2):
            f = c.encode(album / f"0{n} Song.opus", 400 + n * 50, 1)
            o = OggOpus(str(f))
            o["title"], o["artist"], o["album"] = [f"Song {n}"], ["Band"], ["Album"]
            o["albumartist"], o["tracknumber"], o["tracktotal"], o["totaltracks"] = ["Band"], [str(n)], ["2"], ["2"]
            q = Picture()                     # as the previous release embedded it
            q.type, q.mime, q.desc, q.data = 3, "image/jpeg", "", data
            q.width, q.height = 600, 600
            o["metadata_block_picture"] = [base64.b64encode(q.write()).decode()]
            o.save()
            files.append(f)
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(root / "lib")}], "mark_resolved": False,
                             "tag_level": "basic"})
        targets = [{"title": [f"Song {n}"], "artist": ["Band"], "album": ["Album"], "albumartist": ["Band"],
                    "tracknumber": [str(n)], "totaltracks": ["2"]} for n in (1, 2)]
        p = pl.build([pl.Proposal(source_dir=str(album), files=[str(f) for f in files], targets=targets,
                                  provider="musicbrainz", release_id="x", confidence=100.0, margin=50.0,
                                  evidence=["x"], release={"album": ["Album"], "albumartist": ["Band"]},
                                  artwork={"front": str(front), "source": "t"})], prof)
        executor.Executor(p).run()
        for f in files:
            for b in OggOpus(str(f))["metadata_block_picture"]:
                q = Picture(base64.b64decode(b))
                self.assertFalse((q.width or q.height) and not q.depth,
                                 f"{f.name}: cover still has a size but no depth ({q.width}x{q.height}, depth "
                                 f"{q.depth}) - libopusfile players show no cover")


if __name__ == "__main__":
    unittest.main()
