# Twenty-seventh independent review (A): enrich.py --apply writes ReplayGain, genres and
# lyrics straight into the user's files (enrich.put: mutagen's f.save() on the file itself) -
# no staging copy, no check of the audio after the write, no atomic rename. When the new tags
# do not fit the tag's padding (an MP3's ID3v2 tag as ffmpeg/LAME/most rippers leave it, an
# Ogg's comment header, an MP4's moov), mutagen grows the tag in place by moving every byte of
# the audio further into the file (mutagen._util.insert_bytes -> move_bytes, 1 MiB at a time).
# A crash while it does that (power cut, OOM kill, the machine shut down during a long
# library-wide --replaygain run) leaves the track with part of its audio moved and part not:
# the music itself is damaged. `enrich.py --undo <run>` cannot repair it - restore_tags puts the
# old tag head back over a file whose audio no longer is the audio it had.
# The executor (tagmender apply) never writes this way: a staging copy is written, its audio
# compared with the source's (payload.digest), and only then renamed over the track.
# This test makes the crash happen half way through mutagen's move (the same move it does,
# stopped at the half, then SIGKILL) on an album's first track and checks that, after the
# documented undo, every track plays the audio it had.
# Not a regression (the same in the previous version).
# Fix: write through a staging copy as the executor does (copy, put() on the copy, compare
# payload.digest with the original, fsync, os.replace) - for enrich.put, and for the same
# reason coverup_batch.embed_front / restore_embedded and tag_albums.restore_tags.
import os
import shutil
import signal
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review27a_common as c  # noqa: E402

CHILD = r'''
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import _review27a_common as c
import socket
def _no(*a, **k):
    raise OSError("network off in tests")
socket.create_connection = _no
import mutagen._util as mu
import enrich as en
real = mu.move_bytes
def move_bytes(fobj, dest, src, count, BUFFER_SIZE=mu._DEFAULT_BUFFER_SIZE):
    half = count // 2                      # mutagen moves from the end backwards when the tag grows:
    real(fobj, dest + half, src + half, count - half, BUFFER_SIZE)   # its second half is moved...
    fobj.flush()
    os.fsync(fobj.fileno())
    os.kill(os.getpid(), signal.SIGKILL)   # ... and the power goes out
mu.move_bytes = move_bytes
class Web:
    def get_json(self, *a, **k):
        return {}
en.ma.Http = lambda db, cfg, tok: Web()
en.CONFIG = __import__("pathlib").Path(sys.argv[3]) / "config"
en.main([sys.argv[2], "--replaygain", "--apply", "--out-dir", sys.argv[3], "--cache-db", sys.argv[3] + "/c.db"])
'''


class EnrichCrash(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm27A-en-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_tracks_play_as_before_after_a_crash_and_the_undo(self):
        import enrich as en
        album = self.tmp / "lib" / "Band" / "2001 - Album"
        files = [c.encode(album / f"0{k} Song {k}.mp3", 300 + 100 * k, 3) for k in (1, 2)]
        audio = {f.name: c.audio_md5(f) for f in files}
        out = self.tmp / "out"
        r = c.run_child(CHILD, self.tmp / "lib", out)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stdout[-2000:] + r.stderr[-2000:])
        runs = sorted(p.name for p in out.glob("run-enrich-*"))
        self.assertTrue(runs and (out / runs[-1] / "journal.json").exists(), "no run journal (test setup)")
        saved, en.CONFIG = en.CONFIG, self.tmp / "config"
        try:
            en.main(["--undo", runs[-1][len("run-"):], "--out-dir", str(out)])      # the documented undo
        finally:
            en.CONFIG = saved
        now = {f.name: c.audio_md5(f) for f in files}
        broken = sorted(k for k in now if now[k] != audio[k])
        self.assertEqual(broken, [], f"the audio of {broken} is damaged (and stays damaged after --undo)")


if __name__ == "__main__":
    unittest.main()
