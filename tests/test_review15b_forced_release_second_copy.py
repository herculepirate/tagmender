# Fifteenth independent review (B): this test failed before its fix.
"""gui.Library.choose_release now keys the chosen release by the album's folder AND its files:
{"ref": link, "files": [fe["src"] of the plan's files + its skipped files]}, and
engine.forced_for uses the choice only when that list equals the Group's files exactly. But a
folder holding the album twice (01.flac ... and 01.mp3 ...: one Group, read_album drops the
second copies) gives a plan listing only the kept copies - so the Group never equals the list,
the choice is silently dropped and the album is searched for again (here: "no candidates
found"). The same with a copy profile once the album was copied: the plan's files are then
the copies (fe["src"] is the earlier copy), and choosing another release for the album does
nothing. Before, choose_release stored the plain link and the release was used."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)
import executor  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
OTHER = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"


def gui_choice(a, ref):
    """What gui.py choose_release() stores for the selected album row a."""
    import engine
    return [{"ref": ref, "files": engine.plan_entry_files(a)}]


class ChosenReleaseSecondCopy(Base):
    def chosen(self, *exts):
        d = self.album("Band", "Nothing")
        for ext in exts:
            self.album("Band", "Nothing", _ext=ext)              # the same album again, in the same folder
        a = self.engine(FakeHttp()).plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "unresolved")
        # the user chooses the release (not one a search would find) and previews again
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Zilch")]))
        e.force = {os.path.realpath(d): gui_choice(a, f"https://musicbrainz.org/release/{MBID}")}
        return e.plan(d)["albums"][0]["identification"]

    def test_album_alone_in_its_folder(self):
        ident = self.chosen()
        self.assertEqual(ident["release_id"], MBID, ident)

    def test_album_with_an_mp3_copy_beside_it(self):
        ident = self.chosen(".mp3")
        self.assertEqual(ident["status"], "identified", f"the release chosen was ignored: {ident}")
        self.assertEqual(ident["release_id"], MBID, ident)

    def test_copied_album_takes_the_release_chosen_for_it(self):
        d = self.album("Band", "Album")
        dest = self.tmp / "dest"
        dest.mkdir()
        kw = dict(operation="copy", destination=str(dest), sources=[{"path": str(self.lib)}])
        http = FakeHttp(mb=[mb_release(MBID, "Band", "Album"), mb_release(OTHER, "Band", "Zilch", country="JP")])
        p = self.engine(http, **kw).plan(d)
        self.assertEqual(p["albums"][0]["identification"]["release_id"], MBID)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        a = self.engine(http, **kw).plan(d)["albums"][0]          # the next preview, the album copied
        e = self.engine(http, **kw)
        e.force = {os.path.realpath(d): gui_choice(a, OTHER)}     # "Use another release..."
        ident = e.plan(d)["albums"][0]["identification"]
        self.assertEqual(ident["release_id"], OTHER, f"the release chosen was ignored: {ident}")


if __name__ == "__main__":
    unittest.main()
