# QA: the AppStream metainfo (shown by software centres for the Flatpak and AppImage) names the
# version being built: 0.8.11 and 0.8.12 were released without their <release> entries.
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import version  # noqa: E402

METAINFO = REPO / "packaging" / "io.github.herculepirate.TagMender.metainfo.xml"


class ReleaseMetadata(unittest.TestCase):
    def releases(self):
        return re.findall(r'<release version="([^"]+)" date="(\d{4}-\d\d-\d\d)"', METAINFO.read_text(encoding="utf-8"))

    def test_this_version_is_the_newest_release_listed(self):
        got = self.releases()
        self.assertTrue(got, "no <release> entries")
        self.assertEqual(got[0][0], version.VERSION)

    def test_releases_newest_first_and_dated_in_order(self):
        got = self.releases()
        key = [tuple(int(x) for x in v.split(".")) for v, _d in got]
        self.assertEqual(key, sorted(key, reverse=True))
        dates = [d for _v, d in got]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_release_notes_have_this_version(self):
        notes = (REPO / "docs" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"^## (\S+)", notes, re.M).group(1), version.VERSION)


if __name__ == "__main__":
    unittest.main()
