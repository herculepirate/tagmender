# Twenty-seventh independent review (A): coverup_batch.py --apply replaces the front cover
# embedded in every track (embed_front: the old front pictures are dropped, FLAC
# clear_pictures / ID3 APIC type 3 deleted / MP4 covr overwritten) and keeps the only copy of
# the pictures it removed in memory (ctx["embed_backup"]). That backup reaches the disk -
# applied_<batch>.json, the record `--undo <batch>` reads - only every tenth album
# (coverup_batch.py main: `if args.apply and i % 10 == 0: save_record()`) and at the very end.
# A batch that is killed (power cut, OOM killer, the machine shut down, a closed SSH session
# that sends SIGKILL/SIGHUP) before its tenth album leaves no record at all: the original
# covers of every album it already embedded into are gone for good, and `--undo` says "No
# record of batch". (The record is also written with Path.write_text, not through a synced
# temporary file: a crash during the write leaves a truncated JSON that undo cannot read.)
# Not a regression (the same in the previous version).
# Fix: write the album's embed backup to disk (atomically: temp file + fsync + os.replace,
# or an append-only journal line) BEFORE embed_front writes the first track of the album.
import json
import os
import signal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review27a_common as c  # noqa: E402

CHILD = r'''
import os, signal, sys, io, types
sys.path.insert(0, sys.argv[1])
import _review27a_common as c
import socket
def _no(*a, **k):
    raise OSError("network off in tests")
socket.create_connection = _no
import companions, coverup_loader
coverup_loader.load_coverup(str(companions.coverup_dir()))
import coverup_batch as cb
from coverup.providers import REGISTRY
from coverup.model import ArtType, Candidate, Tier
from coverup.settings import Settings
from PIL import Image

class FakeOnline:
    ID = "caa"
    NAME = "stand-in"
    def __init__(self, http, settings): pass
    def available(self): return True, ""
    def wants_artist_art(self, q): return False
    def search(self, q):
        data = c.jpeg_bytes((200, 40, 40), 1400)
        return [Candidate("caa", "fake-front", "https://example.invalid/front.jpg", ArtType.FRONT, Tier.A,
                          width=1400, height=1400, bytes=len(data), data=data, thumb_data=data, dims_known=True)]
REGISTRY["caa"] = FakeOnline

def settings(ini, pass_name):
    s = Settings.from_mapping({"providers_enabled": ["caa", "local"], "provider_order": ["caa", "local"]})
    s = s.with_(embed_types=["front"], save_artist_art=False, artist_art=False, creative_triggers=[],
                apply_types=["front"], naming_preset="kodi", folder_mode="subfolder", folder_name="covers",
                front_in_album_folder=False, embed_max_side=1200, jpeg_quality=95, collision_policy="if_better",
                backups=True, record_source_in_comment=True, write_sidecar=False, conflict_min_sources=3)
    return s, {"x": "1"}, {}
cb.batch_settings = settings

real = cb.process_album
n = {"done": 0, "kill": int(sys.argv[3])}
def process_album(d, ctx, args):
    out = real(d, ctx, args)
    n["done"] += 1
    if n["done"] == n["kill"]:
        os.kill(os.getpid(), signal.SIGKILL)       # the power goes out
    return out
cb.process_album = process_album
sys.argv = ["coverup_batch.py", "--root", sys.argv[2], "--apply", "--pass", "full", "--no-generate",
            "--out-dir", sys.argv[4], "--matches-db", os.path.join(sys.argv[4], "none.db"),
            "--picard-ini", os.path.join(sys.argv[4], "none.ini"), "--upscale-to", "0"]
cb.main()
'''


class CoverupBatchCrash(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="tm27A-cb-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "music"
        self.out = self.tmp / "out"
        self.out.mkdir()
        from mutagen.flac import FLAC, Picture
        self.files, self.orig = [], {}
        for k in range(1, 4):
            f = c.encode(self.root / "Artist" / f"200{k} - Album {k}" / "01 Track.flac", 300 + 50 * k)
            fl = FLAC(f)
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", c.jpeg_bytes((10 * k, 90, 160), 300)
            pic.width = pic.height = 300
            pic.depth = 24
            fl.add_picture(pic)
            fl["ALBUM"], fl["ARTIST"], fl["TITLE"] = [f"Album {k}"], ["Artist"], ["Track"]
            fl.save()
            self.files.append(f)
            self.orig[str(f)] = pic.data

    def fronts(self, f):
        from mutagen.flac import FLAC
        return [p.data for p in FLAC(f).pictures if p.type == 3]

    def test_original_embedded_covers_can_be_put_back_after_a_crash(self):
        r = c.run_child(CHILD, self.root, 2, self.out)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stdout[-2000:] + r.stderr[-2000:])
        replaced = [f for f in self.files if self.fronts(f) != [self.orig[str(f)]]]
        self.assertTrue(replaced, "the batch embedded nothing before the crash (test setup)")
        records = sorted(self.out.glob("applied_*.json"))
        import companions
        import coverup_loader
        coverup_loader.load_coverup(str(companions.coverup_dir()))      # (as the batch's own --undo has it)
        for rec in records:                          # every record there is: undo them all
            import coverup_batch as cb
            cb.undo(json.loads(rec.read_text())["batch"], self.out)
        lost = [str(f.relative_to(self.root)) for f in replaced if self.fronts(f) != [self.orig[str(f)]]]
        self.assertEqual(lost, [], f"the original embedded front cover of {len(lost)} album(s) is gone for good: "
                                   f"no undo record survived the crash (records: {[p.name for p in records]})")


if __name__ == "__main__":
    unittest.main()
