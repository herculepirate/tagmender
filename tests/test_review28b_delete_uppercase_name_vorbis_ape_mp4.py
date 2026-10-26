"""A tagger script's "$delete(CUSTOM)" (a field name in capitals, as the file spells it) does
not remove the field from FLAC, Ogg/Opus, WavPack (APEv2) or M4A files; Picard 3.0.2 removes
it.

plan.py:908 maps each "~remove" name with tagio.canonical(), which leaves "CUSTOM" as it is,
while tagio.read() keys these formats' fields in lower case (tagio.field_name,
tagio.py:549-556: "custom"), so the removal never meets the file's field and the user tag
stays. In Picard, $delete(CUSTOM) puts "CUSTOM" in metadata.deleted_tags and the format's
_remove_deleted_tags deletes it: VCommentFile (picard/formats/vorbis.py:400-419) and
APEv2File test `real_name in tags` on mutagen's case-insensitive dicts, MP4 removes the
freeform "----:com.apple.iTunes:CUSTOM". Checked end to end with Picard's code
(Track.update_file_metadata + _save): FLAC, Opus, WavPack and M4A lose CUSTOM in Picard,
keep it in TagMender ($delete(custom) agrees in both). review-24 B fixed the ID3 side of
names' case; docs/PICARD_COVERAGE.md says "$delete removes the tag; names are case-sensitive
as in Picard". A script cleaning out ENCODER / UPC / ITUNNORM ... written in capitals
silently does nothing. Fails with TM_REPO=prev too (not a regression).
"""
import unittest

import mutagen

import _review28b_common as c


def add_custom(path):
    f = mutagen.File(str(path))
    if f.tags is None:
        f.add_tags()
    ext = str(path)[-4:]
    if ext == ".m4a":
        from mutagen.mp4 import MP4FreeForm
        f.tags["----:com.apple.iTunes:CUSTOM"] = [MP4FreeForm(b"keep me")]
    else:
        f.tags["CUSTOM"] = ["keep me"] if not str(path).endswith(".wv") else "keep me"
    f.save()


def has_custom(path):
    f = mutagen.File(str(path))
    return any(str(k).upper().endswith("CUSTOM") for k in f.tags.keys())


class DeleteUppercaseName(unittest.TestCase):
    def check(self, ext):
        files = c.album(ext, 2)
        for f in files:
            add_custom(f)
            self.assertTrue(has_custom(f))
        p, res = c.run(files, scripts=["$delete(CUSTOM)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual([has_custom(f) for f in files], [False, False],
                         f"{ext}: Picard's $delete(CUSTOM) removes the file's CUSTOM field")

    def test_flac(self):
        self.check(".flac")

    def test_opus(self):
        self.check(".opus")

    def test_wavpack(self):
        self.check(".wv")

    def test_m4a(self):
        self.check(".m4a")


if __name__ == "__main__":
    unittest.main()
