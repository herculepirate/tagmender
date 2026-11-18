# NAM-7 (docs/MATCHING_RULES.md): artist folders whose spellings differ only by diacritics are one
# folder - the one already on disk holding the most ("Kodō" from MusicBrainz filed into the
# library's "Kodo"; Motörhead / Motorhead; Björk / Bjork). Only accents fold: "ø", "ß", "æ" are
# letters of their own. Album folders are never folded. Synthetic folders: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402


class NAM7Diacritics(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm-nam7d-"))
        for artist in ("Kodo", "Motorhead", "Björk"):
            for n in ("1990 - A", "1991 - B"):
                (self.root / artist / n).mkdir(parents=True)
        (self.root / "Mø" / "2014 - A").mkdir(parents=True)
        (self.root / "Strasse" / "2000 - A").mkdir(parents=True)
        self.planner = pl.Planner(pf.default())

    def spelled(self, *parts):
        return self.planner._spelled(self.root.joinpath(*parts), self.root, artist_level=True)

    def test_NAM7_diacritics_reuse_the_folder_on_disk(self):
        for name, there in (("Kodō", "Kodo"), ("Motörhead", "Motorhead"), ("Bjork", "Björk")):
            self.assertEqual(self.spelled(name, "2004 - X", "01.flac").parent.parent.name, there, name)

    def test_NAM7_only_accents_fold(self):
        # ("ø" and "ß" are letters, no accent on "o" or "s": other names - with Linux names; Windows-
        # compatible names fold letter case, and case folding writes "ß" as "ss", as before)
        prof = pf.default()
        prof["filenames"]["compat"] = "linux"
        self.planner = pl.Planner(prof)
        self.assertEqual(self.spelled("Mo", "A", "01.flac").parent.parent.name, "Mo")
        self.assertEqual(self.spelled("Straße", "A", "01.flac").parent.parent.name, "Straße")
        self.assertEqual(pl._loose_key("Kodō"), pl._loose_key("Kodo"))
        self.assertNotEqual(pl._loose_key("Mø"), pl._loose_key("Mo"))

    def test_NAM7_album_folders_keep_their_accents(self):
        got = self.spelled("Kodō", "2004 - One Earth Tōur", "01.flac")
        self.assertEqual((got.parent.parent.name, got.parent.name), ("Kodo", "2004 - One Earth Tōur"))


if __name__ == "__main__":
    unittest.main()
