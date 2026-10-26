"""MP3/AIFF/WAV in ID3v2.3 with a join character other than "/" (Picard's "ID3v2.3 join
character", e.g. "; "): a release with several license URLs gets them joined into one WCOP
frame - "https://a/; https://b/", not a URL - where Picard writes TXXX:LICENSE.

tagio.py:1057 joins the values for v2.3 before the field is written; tagio.py:1179-1185 then
sees one value, `_is_url("https://a/; https://b/")` is true and only a "/" join is caught
(`re.search(r"/https?://", ...)`), so WCOP is written. Picard 3.0.2
(ID3File._save_standard_tag, picard/formats/id3.py:855-861) chooses by the number of license
values first ("Only add WCOP if there is only one license URL, otherwise use TXXX:LICENSE")
and joins the TXXX for v2.3 afterwards. Checked end to end with Picard's code on a release
with two/three license relationships (settings write_id3v23, id3v23_join_with "; "): Picard
TXXX:LICENSE "url1; url2", TagMender WCOP "url1; url2". Players and tag editors that follow
the WCOP link get a broken URL. Fails with TM_REPO=prev too (not a regression).
"""
import unittest

from mutagen.id3 import ID3

import _review28b_common as c

URLS = ["https://creativecommons.org/licenses/by-sa/3.0/", "https://creativecommons.org/licenses/by/4.0/"]


class JoinedLicenses(unittest.TestCase):
    def test_two_licenses_v23_semicolon(self):
        files = c.album(".mp3", 2)
        targets = []
        for i in range(2):
            t = {k: list(v) for k, v in c.TARGET.items()}
            t.update(tracknumber=[str(i + 1)], totaltracks=["2"], title=["Track %d" % (i + 1)], license=list(URLS))
            targets.append(t)
        p, res = c.run(files, picard={"write_id3v23": True, "id3v23_join_with": "; "}, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        tags = ID3(str(files[0]))
        self.assertEqual(tags.getall("WCOP"), [], f"Picard writes no WCOP for two licenses: {tags.getall('WCOP')}")
        self.assertEqual([str(x) for x in tags.getall("TXXX:LICENSE")], ["; ".join(URLS)])


if __name__ == "__main__":
    unittest.main()
