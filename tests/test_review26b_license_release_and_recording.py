"""A license URL on the release AND on its recordings (common for Creative Commons releases on
MusicBrainz) with release and track relationships on: Picard 3.0.2 adds the recording's license
to the release's with Metadata.add - no de-duplication (mbjson
_relations_to_metadata_target_type_url: `m.add('license', url)`), so the track's LICENSE is
[release license, recording license...] in full. TagMender de-duplicates: mb_tags.release_tags
merges the track's tags into the release's with _add (add_unique).

User-visible in MP3: Picard writes the two values to TXXX:LICENSE (its ID3 code writes WCOP
only for a single URL); TagMender writes one value, as a WCOP frame. In FLAC/Ogg/MP4 TagMender
writes one LICENSE where Picard writes it twice; a recording with licenses A and B on a release
with A gets [A, B] instead of Picard's [A, B, A]. Checked end to end with Picard's own code.
"""
import unittest

import mutagen

import _review26b_common as c
import mb_tags

CC = "https://creativecommons.org/licenses/by-sa/3.0/"
SETTINGS = {"release_ars": True, "track_ars": True}


def lic_release():
    a = c.artist("Kevin MacLeod", "MacLeod, Kevin")
    ac = c.credit(("Kevin MacLeod", a, ""))
    rec1 = c.recording("Song A", ac, relations=[c.url_rel("license", CC)])
    rec2 = c.recording("Song B", ac, relations=[])
    return c.release("license", "Free Tunes", ac, [c.track(1, "Song A", ac, rec1), c.track(2, "Song B", ac, rec2)],
                     relations=[c.url_rel("license", CC)])


class License(unittest.TestCase):
    def test_release_tags_keep_both_values(self):
        t = mb_tags.release_tags(lic_release(), SETTINGS)
        self.assertEqual(t[0].get("license"), [CC, CC], "Picard: the release's license, then the recording's")
        self.assertEqual(t[1].get("license"), [CC])

    def test_mp3_txxx_license_not_wcop(self):
        files = c.album(".mp3", 2)
        tg = c.targets_of(lic_release(), SETTINGS)
        p, res = c.run(files, picard=SETTINGS, targets=tg)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        f = mutagen.File(str(files[0]))
        self.assertEqual(f.tags.getall("WCOP"), [], "Picard writes no WCOP for two license values")
        self.assertEqual([list(x.text) for x in f.tags.getall("TXXX:LICENSE")], [[CC, CC]])


if __name__ == "__main__":
    unittest.main()
