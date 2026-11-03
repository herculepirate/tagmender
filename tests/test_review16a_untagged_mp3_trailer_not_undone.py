# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A).
# tag_albums.backup_tags records rec["format"] = type(f).__name__ if f else "" - but a mutagen
# FileType with no tags is falsy (len() of its tags is 0), so an MP3 with no ID3 tag at all
# (only an APEv2 tag, or nothing) is recorded with format "". Undo restores through a staging
# copy (".tm-undo-tags-<hash>.mp3.part"), whose name does not end in ".mp3", so restore_tags
# misses its MP3 branch: it adds an (empty) ID3v2 tag and never calls _put_ends - the trailer
# the job changed is not put back:
#  - remove_ape_from_mp3: the APEv2 tag (MP3Gain's ReplayGain, foobar2000's tags) is gone for good;
#  - write_id3v1: the ID3v1 tag the job added stays after undo.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review16a_common import Run, add_ape, bare_mp3, executor  # noqa: E402

from mutagen.apev2 import APEv2  # noqa: E402


def has_ape(p):
    try:
        APEv2(str(p))
        return True
    except Exception:
        return False


class UntaggedMp3TrailerNotUndone(Run):
    def files(self):
        self.fs = [bare_mp3(self.alb / f"0{n} x.mp3", 440 + 100 * n) for n in (1, 2)]
        return self.fs

    def test_apev2_removed_by_the_job_comes_back_on_undo(self):
        for f in self.files():
            add_ape(f, Title="Old", REPLAYGAIN_TRACK_GAIN="-6.20 dB")
        orig = [f.read_bytes() for f in self.fs]
        p, res, before, prof = self.run_plan({"remove_ape_from_mp3": True})
        self.assertFalse(has_ape(self.fs[0]))                       # (the job removed it, as Picard does)
        out = executor.undo(res["job"])
        self.assertTrue(has_ape(self.fs[0]), f"the MP3's APEv2 tag is lost after undo: {out}")
        self.assertEqual([f.read_bytes() for f in self.fs], orig)

    def test_id3v1_added_by_the_job_goes_on_undo(self):
        orig = [f.read_bytes() for f in self.files()]
        p, res, before, prof = self.run_plan({"write_id3v1": True})
        self.assertEqual(self.fs[0].read_bytes()[-128:-125], b"TAG")
        out = executor.undo(res["job"])
        self.assertNotEqual(self.fs[0].read_bytes()[-128:-125], b"TAG",
                            f"the ID3v1 tag the job added is still there after undo: {out}")
        self.assertEqual([f.read_bytes() for f in self.fs], orig)


if __name__ == "__main__":
    unittest.main()
