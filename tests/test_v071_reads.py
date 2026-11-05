# Fewer audio reads: the move preview hashes the album only when the destination holds
# music tagged as another release, and a moved track is not hashed again at the
# destination when that is the verified stage's own inode.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()


class Counting:
    def __init__(self):
        self.calls = []
        self.real = payload.digest

    def __call__(self, p, *a, **k):
        self.calls.append(str(p))
        return self.real(p, *a, **k)


class LazyDigests(Base):
    def prof(self):
        return self.profile(operation="move", destination=str(self.dest))

    def test_absent_destination_hashes_nothing_in_planning(self):
        c = Counting()
        with mock.patch.object(payload, "digest", c):
            p = pl.build([self.proposal()], self.prof())
        self.assertEqual(c.calls, [])
        self.assertTrue(p["albums"][0]["files"])

    def foreign_folder(self, prof, other_audio):
        first = pl.build([self.proposal()], prof)
        plain = Path(first["albums"][0]["files"][0]["dst"]).parent
        self.assertFalse(plain.exists())
        plain.mkdir(parents=True)
        other = plain / "01 other.flac"
        shutil.copy2(other_audio, other)
        tagio.write(other, {"musicbrainz_albumid": ["11111111-2222-3333-4444-555555555555"]})
        c = Counting()
        with mock.patch.object(payload, "digest", c):
            p = pl.build([self.proposal()], prof)
        return plain, Path(p["albums"][0]["files"][0]["dst"]).parent, c

    def test_foreign_release_folder_gets_the_clash_suffix(self):
        from test_executor import audio
        other = audio(self.tmp / "x" / "o.flac", 900, album="Other")
        plain, folder, c = self.foreign_folder(self.prof(), other)
        self.assertNotEqual(folder, plain)
        self.assertTrue(folder.name.startswith(plain.name))
        self.assertTrue(c.calls)                             # hashed once a foreign file was found

    def test_foreign_tagged_copy_of_this_audio_is_still_not_another_release(self):
        plain, folder, c = self.foreign_folder(self.prof(), self.f1)
        self.assertEqual(folder, plain)
        self.assertTrue(c.calls)


class MoveDigests(Base):
    def run_move(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        c = Counting()
        with mock.patch.object(payload, "digest", c):
            res = executor.Executor(p).run()
        return p, res, c

    def test_digests_per_track_drop_from_three_to_two(self):
        p, res, c = self.run_move()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(len(c.calls), 4, c.calls)
        self.assertFalse(self.f1.exists())

    def test_a_different_inode_at_the_destination_is_still_hashed(self):
        real = executor._place

        def place(stage, dst):
            real(stage, dst)
            data = Path(dst).read_bytes()
            os.unlink(dst)
            Path(dst).write_bytes(data)                  # same bytes, new inode
        with mock.patch.object(executor, "_place", place):
            p, res, c = self.run_move()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(len(c.calls), 6, c.calls)

    def test_a_filesystem_without_inode_numbers_is_still_hashed(self):
        real = os.stat

        def stat(path, *a, **k):
            st = real(path, *a, **k)
            return os.stat_result((st.st_mode, 0) + tuple(st)[2:]) if str(path).startswith(str(self.dest)) \
                or ".tagmender" in str(path) or "stag" in str(path) else st
        with mock.patch.object(executor.os, "stat", stat):
            p, res, c = self.run_move()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(len(c.calls), 6, c.calls)

    def test_a_corrupted_destination_keeps_the_source(self):
        real = executor._place

        def place(stage, dst):
            real(stage, dst)
            os.unlink(dst)
            Path(dst).write_bytes(b"garbage")
        with mock.patch.object(executor, "_place", place):
            p, res, c = self.run_move()
        self.assertTrue(self.f1.exists())
        self.assertNotEqual(res["counts"], {"done": 1})


if __name__ == "__main__":
    unittest.main()
