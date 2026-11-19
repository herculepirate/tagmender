# 0.8.83 (ED-9): Discogs's "Mono" names the album's version only when nothing else in the
# release's formats says Stereo or a multichannel layout. Billy Joel - Piano Man 50th
# Anniversary (SICP 10151~4) was filed "[Remastered, Mono]": its stereo/quad SACD is the album,
# and only its bonus CD ("CD, Compilation, Stereo, Mono") holds a few mono demos.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine  # noqa: E402

PIANO_MAN = {"title": "Piano Man", "formats": [
    {"name": "SACD", "descriptions": ["Hybrid", "Multichannel", "Stereo", "Quadraphonic", "Album", "Reissue",
                                      "Remastered"]},
    {"name": "CD", "descriptions": ["Compilation", "Stereo", "Mono"]},
    {"name": "DVD", "descriptions": ["DVD-Video", "NTSC", "Stereo", "Mono"]},
    {"name": "All Media", "descriptions": ["Limited Edition"]}]}


class ED9MonoVersion(unittest.TestCase):
    def test_ED9_mono_beside_stereo_is_no_version(self):
        self.assertEqual(engine._dg_version(PIANO_MAN), ("remastered", "Remastered"))

    def test_ED9_a_mono_album_stays_mono(self):
        det = {"formats": [{"name": "CD", "descriptions": ["Album", "Mono", "Remastered"]}]}
        self.assertEqual(engine._dg_version(det), ("mono remastered", "Mono, Remastered"))

    def test_ED9_master_version_format_text(self):
        self.assertEqual(engine._dg_format_key("SACD, Hybrid, Stereo, Quadraphonic, Remastered, CD, Stereo, Mono"),
                         "remastered")
        self.assertEqual(engine._dg_format_key("Vinyl, LP, Album, Mono"), "mono")

    def test_ED9_free_text_mono_beside_stereo_is_dropped(self):
        det = {"formats": [{"name": "CD", "descriptions": ["Stereo", "Mono"], "text": "Mono, 2014 Remaster"}]}
        self.assertEqual(engine._dg_version(det)[1], "2014 Remaster")


if __name__ == "__main__":
    unittest.main()
