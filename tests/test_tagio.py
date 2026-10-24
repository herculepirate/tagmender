# Tests for tagio.py (tag mappings) and payload.py (audio-data fingerprints), on real files
# of every format ffmpeg can make here.
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import mutagen  # noqa: E402
import payload  # noqa: E402
import tagio  # noqa: E402

# name: (extension, ffmpeg codec options)
FORMATS = {
    "MP3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "128k"]),
    "FLAC": (".flac", ["-c:a", "flac"]),
    "M4A (AAC)": (".m4a", ["-c:a", "aac", "-b:a", "96k"]),
    "M4A (ALAC)": (".m4a", ["-c:a", "alac"]),
    "Ogg Vorbis": (".ogg", ["-c:a", "libvorbis"]),
    "Opus": (".opus", ["-c:a", "libopus"]),
    "WAV": (".wav", ["-c:a", "pcm_s16le"]),
    "AIFF": (".aiff", ["-c:a", "pcm_s16be"]),
    "WavPack": (".wv", ["-c:a", "wavpack"]),
    "WMA": (".wma", ["-c:a", "wmav2"]),
}


def make(path, ext_opts, freq=440, seconds=2):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ac", "2", *ext_opts, "-y", str(path)], check=True)
    return path


FULL = {
    "title": ["Ångström — 夜"], "artist": ["A & B"], "artists": ["A", "B"], "album": ["Album [no vox]"],
    "albumartist": ["A & B"], "date": ["2001-02-03"], "originaldate": ["1999"], "tracknumber": ["3"],
    "totaltracks": ["12"], "discnumber": ["1"], "totaldiscs": ["2"], "genre": ["Ambient", "Drone"],
    "label": ["Label X"], "catalognumber": ["CAT-001"], "barcode": ["0123456789012"],
    "releasecountry": ["DE"], "musicbrainz_albumid": ["11111111-1111-1111-1111-111111111111"],
    "musicbrainz_recordingid": ["22222222-2222-2222-2222-222222222222"],
    "musicbrainz_trackid": ["33333333-3333-3333-3333-333333333333"],
    "musicbrainz_artistid": ["44444444-4444-4444-4444-444444444444", "55555555-5555-5555-5555-555555555555"],
    "discogs_release_id": ["123456"], "tagmender": ["Resolved"], "composer": ["Комп"],
}


class Formats(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roundtrip(self, name):
        ext, opts = FORMATS[name]
        p = make(self.tmp / f"t{ext}", opts)
        before = payload.digest(p)
        self.assertIsNotNone(before, f"{name}: no payload digest")
        want = dict(FULL)
        if name == "WMA":                      # ASF: no custom fields, no total-tracks slot (as in Picard)
            for k in ("discogs_release_id", "tagmender", "totaltracks"):
                want.pop(k)
        tagio.write(p, want)
        self.assertEqual(payload.digest(p), before, f"{name}: audio data changed by a tag write")
        got = tagio.read(p)
        for k, v in want.items():
            self.assertEqual(got.get(k), v, f"{name}: {k}")
        return p, before

    def test_every_format_round_trips_and_keeps_its_audio(self):
        for name in FORMATS:
            with self.subTest(name):
                self.roundtrip(name)

    def test_only_named_fields_change_and_unknown_tags_survive(self):
        for name in ("MP3", "FLAC", "M4A (AAC)", "Opus", "WavPack"):
            with self.subTest(name):
                p, before = self.roundtrip(name)
                f = mutagen.File(p)                        # a tag TagMender knows nothing about
                if name == "MP3":
                    from mutagen.id3 import TXXX
                    f.tags.add(TXXX(encoding=3, desc="MY_OWN", text=["keep me"]))
                elif name.startswith("M4A"):
                    from mutagen.mp4 import MP4FreeForm
                    f.tags["----:com.apple.iTunes:MY_OWN"] = [MP4FreeForm(b"keep me")]
                else:
                    f.tags["MY_OWN"] = ["keep me"]
                f.save()
                tagio.write(p, {"album": ["New"], "genre": None, "tracknumber": ["4"]})
                got = tagio.read(p)
                self.assertEqual(got["album"], ["New"])
                self.assertNotIn("genre", got)
                self.assertEqual((got["tracknumber"], got["totaltracks"]), (["4"], ["12"]))
                self.assertEqual(got["my_own"], ["keep me"])
                self.assertEqual(got["title"], FULL["title"])
                self.assertEqual(payload.digest(p), before)

    def test_existing_custom_tag_case_is_kept(self):
        p = make(self.tmp / "c.mp3", FORMATS["MP3"][1])
        from mutagen.id3 import TXXX
        f = mutagen.File(p)
        if f.tags is None:
            f.add_tags()
        f.tags.add(TXXX(encoding=3, desc="DISCOGS_RELEASE_ID", text=["1"]))
        f.save()
        tagio.write(p, {"discogs_release_id": ["2"]})
        keys = [k for k in mutagen.File(p).tags if k.startswith("TXXX:")]
        self.assertEqual(keys, ["TXXX:DISCOGS_RELEASE_ID"])
        self.assertEqual(tagio.read(p)["discogs_release_id"], ["2"])

    def test_picard_names_on_disk(self):
        p = make(self.tmp / "p.flac", FORMATS["FLAC"][1])
        tagio.write(p, {"musicbrainz_recordingid": ["r"], "musicbrainz_trackid": ["t"], "totaltracks": ["9"],
                        "tracknumber": ["1"]})
        raw = mutagen.File(p).tags
        self.assertEqual(raw["MUSICBRAINZ_TRACKID"], ["r"])               # recording, as Picard writes it
        self.assertEqual(raw["MUSICBRAINZ_RELEASETRACKID"], ["t"])
        self.assertEqual((raw["TOTALTRACKS"], raw["TRACKTOTAL"]), (["9"], ["9"]))
        m = make(self.tmp / "p.mp3", FORMATS["MP3"][1])
        tagio.write(m, {"musicbrainz_recordingid": ["r"], "catalognumber": ["C"]})
        t = mutagen.File(m).tags
        self.assertEqual(t["UFID:http://musicbrainz.org"].data, b"r")
        self.assertEqual(t["TXXX:CATALOGNUMBER"].text, ["C"])

    def test_wma_refuses_custom_fields_without_writing(self):
        p = make(self.tmp / "w.wma", FORMATS["WMA"][1])
        raw = p.read_bytes()
        with self.assertRaises(tagio.Unsupported):
            tagio.write(p, {"album": ["X"], "discogs_release_id": ["1"]})
        self.assertEqual(p.read_bytes(), raw)

    def test_id3v23_files_stay_v23(self):
        p = make(self.tmp / "v23.mp3", FORMATS["MP3"][1])
        f = mutagen.File(p)
        if f.tags is None:
            f.add_tags()
        f.tags.add(mutagen.id3.TALB(encoding=1, text=["x"]))
        f.save(v2_version=3)
        tagio.write(p, {"album": ["y"], "artists": ["A", "B"]})
        self.assertEqual(mutagen.File(p).tags.version[:2], (2, 3))
        self.assertEqual(tagio.read(p)["album"], ["y"])

    def test_not_audio(self):
        p = self.tmp / "x.mp3"
        p.write_bytes(b"not audio at all")
        with self.assertRaises(tagio.Unsupported):
            tagio.read(p)


class Payload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_changed_sample_is_noticed(self):
        for name in ("FLAC", "WAV", "MP3", "Ogg Vorbis", "Opus", "M4A (ALAC)", "WavPack", "AIFF"):
            with self.subTest(name):
                ext, opts = FORMATS[name]
                a = make(self.tmp / f"a{ext}", opts, 440)
                b = make(self.tmp / f"b{ext}", opts, 441)
                self.assertNotEqual(payload.digest(a), payload.digest(b))
                data = bytearray(a.read_bytes())
                data[len(data) // 2] ^= 0xFF                      # flip bits inside the audio
                a.write_bytes(bytes(data))
                d1 = payload.digest(a)
                self.assertNotEqual(d1, payload.digest(make(self.tmp / f"c{ext}", opts, 440)))

    def test_native_methods_used(self):
        kinds = {}
        for name, (ext, opts) in FORMATS.items():
            p = make(self.tmp / f"k{abs(hash(name))}{ext}", opts)
            kinds[name] = payload.method(p)
        self.assertEqual(kinds["FLAC"], "flac")
        self.assertEqual(kinds["MP3"], "mpeg")
        self.assertEqual(kinds["M4A (AAC)"], "mp4")
        self.assertEqual(kinds["Opus"], "ogg")
        self.assertEqual(kinds["WAV"], "wav")
        self.assertEqual(kinds["AIFF"], "aiff")
        self.assertEqual(kinds["WavPack"], "apev2")
        self.assertEqual(kinds["WMA"], "ffmpeg")              # no native reader: packets via ffmpeg -c copy


if __name__ == "__main__":
    unittest.main()
