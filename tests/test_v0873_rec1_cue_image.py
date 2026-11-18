# REC-1 (docs/MATCHING_RULES.md): a lone file is tagged as one recording from its fingerprint only
# when it is that recording - never a disc image whose cue sheet splits it into tracks (Dr. Jeffrey
# Thompson - Delta Sleep System (CD3): one 60:40 file, its cue 2 tracks, tagged as "Part 1" with
# Part 1's recording ID), nor a file much longer than the recording. Synthetic files, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import subprocess  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_partial import Base, FakeHttp  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402

CUE = """REM DISCID 0F0E3802
PERFORMER "Dr. Jeffrey Thompson"
TITLE "Delta Sleep System (CD3)"
FILE "{name}" WAVE
  TRACK 01 AUDIO
    TITLE "Delta Sleep Track 1"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Delta Sleep Track 2"
    INDEX 01 00:03:00
"""


class REC1CueImage(Base):
    def _file(self, cue_name=None, tracks=2):
        d = self.lib / "Delta Sleep System (CD3)"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "Delta Sleep System (CD3).flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                        str(f), "-y"], check=True)
        if cue_name is not None:
            text = CUE.format(name=cue_name)
            if tracks == 1:
                text = text.split("  TRACK 02")[0]
            (d / "Delta Sleep System (CD3).cue").write_text(text)
        return d, f

    def _single(self, d, f, rec_secs=None):
        http = FakeHttp(recordings={"r1": {"releases": [{"release-group": {"id": "rg-1"}}]}},
                        groups={"rg-1": {"id": "rg-1", "title": "Delta Sync Sleep System", "first-release-date": "1995"}})
        rec = {"id": "r1", "title": "Delta Sync Sleep System, Part 1", "artists": [{"id": "a1", "name": "Dr. Jeffrey Thompson"}]}
        if rec_secs:
            rec["duration"] = rec_secs
        acoustid = {"results": [{"id": "aid", "score": 0.97, "recordings": [rec]}]}
        get = http.get_json
        http.get_json = lambda url, host: acoustid if host == "acoustid" else get(url, host)
        with mock.patch.object(ma, "fingerprint", return_value={"duration": 6, "fingerprint": "x"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"):
            return self.engine(http)._single(f, dict(source_dir=str(d), files=[str(f)], provider_errors={}))

    def test_REC1_a_cue_image_of_two_tracks_with_no_length_is_held(self):
        d, f = self._file(cue_name="Delta Sleep System (CD3).flac")
        self.assertEqual(engine._cue_tracks_of(f), 2)
        self.assertIsNone(self._single(d, f))

    def test_REC1_the_cue_naming_the_image_as_ripped(self):
        # (the cue names the WAV EAC wrote; the image was converted to FLAC since)
        d, f = self._file(cue_name="Delta Sleep System (CD3).wav")
        self.assertEqual(engine._cue_tracks_of(f), 2)
        self.assertIsNone(self._single(d, f))

    def test_REC1_a_cue_named_after_the_image_with_its_suffix(self):
        # (review: "CDImage.flac.cue" naming the WAV ripped first, "CDImage.wav")
        d, f = self._file()
        (d / f"{f.name}.cue").write_text(CUE.format(name="CDImage.wav"))
        self.assertEqual(engine._cue_tracks_of(f), 2)

    def test_REC1_a_file_line_naming_no_type(self):
        d, f = self._file()
        (d / "other.cue").write_text(CUE.format(name=f.name).replace(" WAVE", ""))
        self.assertEqual(engine._cue_tracks_of(f), 2)

    def test_REC1_the_cue_sheet_the_flac_carries(self):
        # (an image with its cue embedded as a CUESHEET tag, no .cue file beside it)
        d, f = self._file()
        from mutagen.flac import FLAC
        tagged = FLAC(str(f))
        tagged["CUESHEET"] = CUE.format(name=f.name)
        tagged.save()
        self.assertEqual(engine._cue_tracks_of(f), 2)
        self.assertIsNone(self._single(d, f))

    def test_REC1_a_cue_split_file_as_long_as_the_recording_is_that_recording(self):
        # (re-validation 0.8.72: MusicBrainz's Delta Sync Sleep System, Part 1 is one 60-min track,
        # 3642.9 s, the file 3640.3 s; the box disc's cue only indexes it at 30:20 - the
        # recording tier stands. The recording far shorter than the file: held)
        d, f = self._file(cue_name="Delta Sleep System (CD3).flac")
        self.assertEqual(self._single(d, f, rec_secs=6).tier, "recording")
        self.assertIsNone(self._single(d, f, rec_secs=40))     # (past 10 s and a tenth of the file)
        self.assertIsNone(self._single(d, f))                   # (no length to compare: held)

    def test_REC1_one_track_cue_or_none_stays_a_recording(self):
        d, f = self._file()
        self.assertEqual(engine._cue_tracks_of(f), 0)
        self.assertEqual(self._single(d, f).tier, "recording")
        d, f = self._file(cue_name="Delta Sleep System (CD3).flac", tracks=1)
        self.assertEqual(engine._cue_tracks_of(f), 1)
        self.assertEqual(self._single(d, f).tier, "recording")

    def test_REC1_a_recording_much_shorter_than_the_file_is_not_it(self):
        d, f = self._file()
        self.assertIsNone(self._single(d, f, rec_secs=60 * 30 + 20))      # (fingerprint duration 6 s here)
        self.assertEqual(self._single(d, f, rec_secs=7).tier, "recording")


if __name__ == "__main__":
    unittest.main()
