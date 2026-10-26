# Fourteenth independent review (Picard code): this test failed before its fix.
"""engine.identify / gui "Use another release...": the chosen release is keyed by the album's
FOLDER (os.path.realpath(g.folder)), but a mixed folder holds several albums (one Group per
album, all with the same folder; untagged loose tracks are one Group each). The release the
user chose for one album is then forced onto every other album of that folder."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

CHOSEN = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
OTHER = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"


class MixedFolder(Base):
    def test_release_chosen_for_one_album_is_not_forced_on_the_other(self):
        d = self.lib / "Band" / "Mixed"
        for i in range(3):
            make(d / f"a{i + 1:02d}.flac", 300 + 50 * i, 10 * (i + 1),
                 {"title": f"T{i}", "album": "Alpha", "albumartist": "Band", "track": str(i + 1)})
            make(d / f"b{i + 1:02d}.flac", 700 + 50 * i, 10 * (i + 1),
                 {"title": f"T{i}", "album": "Beta", "albumartist": "Band", "track": str(i + 1)})
        http = FakeHttp(mb=[mb_release(CHOSEN, "Band", "Alpha"), mb_release(OTHER, "Band", "Beta")])
        e = self.engine(http)
        groups = e.albums_in(d)
        self.assertEqual(len(groups), 2, "the folder holds two albums")
        # the GUI's choose_release(): self.forced[os.path.realpath(a["source_dir"])] = ref, for the
        # album selected (Alpha)
        e.force = {os.path.realpath(d): f"https://musicbrainz.org/release/{CHOSEN}"}
        p = e.plan(d)
        beta = next(a for a in p["albums"] if any(Path(f["src"]).name.startswith("b") for f in a.get("files") or []))
        ident = beta["identification"]
        self.assertNotEqual(ident.get("release_id"), CHOSEN,
                            f"the release chosen for Alpha was forced on Beta: {ident}")
        self.assertFalse(any("the release you chose" in w for w in ident.get("why") or []), ident)


if __name__ == "__main__":
    unittest.main()
