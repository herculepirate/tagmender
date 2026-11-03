# Twenty-ninth independent review (A): the ID3 license frame is still chosen from the joined
# text, not from the number of license values as Picard chooses it
# (picard/formats/id3.py: "Only add WCOP if there is only one license URL, otherwise use
# TXXX:LICENSE").
#  1. ID3v2.3, two licenses where the second is not a URL (a URL from the release and a
#     "CC-BY-SA" a script or the Discogs merge adds): stored() joins them into one value
#     "https://.../4.0//CC-BY-SA" before write(); tagio.write's new license branch
#     (tagio.py:1079-1084) only recognises a join when the second value starts with a URL
#     scheme, so _set_id3 writes WCOP "https://creativecommons.org/licenses/by/4.0//CC-BY-SA" -
#     a broken link, where Picard writes TXXX:LICENSE. Same with the "; " join.
#  2. One license URL that contains "/https://" (a Wayback Machine link,
#     "https://web.archive.org/web/2020/https://creativecommons.org/...") is taken for several
#     joined (_set_id3's `re.search(r"/https?://", ...)`, tagio.py:1215) - TXXX:LICENSE even
#     in ID3v2.4, where Picard writes WCOP.
# Neither is a regression (both fail with TM_REPO=prev); the round's fix covers only a second
# URL.
# Fix: decide in the planner, where the values are still separate: carry the license count (or
# the unjoined list) to write() - e.g. fe["names"]-like fe["license_values"] - and write WCOP
# only for exactly one value that is a URL; never infer it from the joined text.
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
from mutagen.id3 import ID3, TIT2  # noqa: E402

CC = "https://creativecommons.org/licenses/by/4.0/"
WAYBACK = "https://web.archive.org/web/2020/https://creativecommons.org/licenses/by/4.0/"


class LicenseFrame(unittest.TestCase):
    def tag(self, licenses, v23=True, join="/"):
        root = Path(tempfile.mkdtemp(prefix="tm29A-lic-"))
        self.addCleanup(shutil.rmtree, root, True)
        ex.CONFIG_DIR, ex.STATE_DIR = root / "config", root / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        f = c.encode(root / "lib" / "album" / "01.mp3", 440, 1)
        t = ID3()
        t.add(TIT2(encoding=1, text=["x"]))
        t.save(str(f), v2_version=3 if v23 else 4)
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(root / "lib")}], "artwork": {"enabled": False},
                             "mark_resolved": False,
                             "picard": {"write_id3v23": v23, "id3v23_join_with": join, "write_id3v1": False}})
        tg = {"title": ["T"], "album": ["A"], "artist": ["B"], "albumartist": ["B"], "tracknumber": ["1"],
              "totaltracks": ["1"], "license": list(licenses),
              "musicbrainz_albumid": ["b84ee12a-09ef-421b-82de-0441a926375b"]}
        p = pl.build([pl.Proposal(source_dir=str(f.parent), files=[str(f)], targets=[tg], provider="musicbrainz",
                                  release_id="x", confidence=100.0, margin=50.0, evidence=["x"],
                                  release={"album": ["A"], "albumartist": ["B"]})], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        i = ID3(str(f))
        return [x.url for x in i.getall("WCOP")], [str(x) for x in i.getall("TXXX:LICENSE")]

    def test_v23_url_and_text_license_no_wcop(self):
        wcop, txxx = self.tag([CC, "CC-BY-SA"])
        self.assertEqual(wcop, [], "two licenses joined into one WCOP 'URL' (Picard: TXXX:LICENSE)")
        self.assertEqual(txxx, [CC + "/CC-BY-SA"])

    def test_v23_url_and_text_license_semicolon_join_no_wcop(self):
        wcop, _txxx = self.tag([CC, "CC-BY-SA"], join="; ")
        self.assertEqual(wcop, [], "two licenses joined into one WCOP 'URL' (Picard: TXXX:LICENSE)")

    def test_one_wayback_license_url_is_wcop_v24(self):
        wcop, txxx = self.tag([WAYBACK], v23=False)
        self.assertEqual((wcop, txxx), ([WAYBACK], []), "one license URL not written as WCOP (Picard: WCOP)")


if __name__ == "__main__":
    unittest.main()
