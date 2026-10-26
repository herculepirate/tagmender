# Thirteenth independent review (Picard code): this test failed before its fix.
"""ID3v2.3 files: what TagMender writes must read back as written, or every run rewrites it.

tagio.write now runs mutagen's update_to_v23() on a v2.3 tag (as Picard does): TDOR becomes
TORY (year only), TDRC becomes TYER+TDAT (a "YYYY-MM" date keeps only its year), and
multi-valued text frames are joined with "/". tagio.read hands those back as they are stored,
and plan.diff_tags compares them with the full target - so the plan never settles."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review13b_common import make  # noqa: E402

import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)


class V23Album(Base):
    def setUp(self):
        super().setUp()
        self.alb = self.lib / "V23 Artist" / "1999 - V23 Album"
        self.m1 = make(self.alb / "01 a.mp3", 440, v23=True)
        self.m2 = make(self.alb / "02 b.mp3", 550, v23=True)

    def prop(self, **extra):
        return self.proposal(source_dir=str(self.alb), files=[str(self.m1), str(self.m2)],
                             targets=[target(1, "First", **extra), target(2, "Second", **extra)])

    def run_twice(self, **extra):
        prof = self.profile()
        p = pl.build([self.prop(**extra)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, [a.get("detail") for a in res["albums"]])
        again = pl.build([self.prop(**extra)], prof)
        return again["albums"][0], tagio.read(self.m1)

    def test_original_date_settles(self):
        """MusicBrainz always gives the release group's full first-release date."""
        entry, back = self.run_twice(originaldate=["1999-05-01"])
        self.assertEqual(entry["status"], "unchanged",
                         f"rewritten again: {[r for fe in entry['files'] for r in fe['diff']]}")

    def test_month_only_date_settles(self):
        """A release date of "YYYY-MM" (common on MusicBrainz)."""
        entry, back = self.run_twice(date=["2001-05"])
        self.assertEqual(entry["status"], "unchanged",
                         f"rewritten again: {[r for fe in entry['files'] for r in fe['diff']]}")

    def test_multi_valued_fields_settle(self):
        """releasetype ["album", "live"], artists of a "A feat. B" credit, two genres: every
        such track of a v2.3 library is rewritten on every run."""
        entry, back = self.run_twice(releasetype=["album", "live"], artists=["A", "B"])
        self.assertEqual(entry["status"], "unchanged",
                         f"rewritten again: {[r for fe in entry['files'] for r in fe['diff']]}")


class V23OtherFieldsKept(unittest.TestCase):
    def test_writing_the_title_keeps_the_dates(self):
        """tagio.write "changes ONLY the fields named": a v2.3 file whose tag holds a full
        original date and a month date (as TagMender before f4778b5 wrote them, TDOR/TDRC)
        loses both when only the title is written."""
        import tempfile
        from mutagen.id3 import ID3, TDOR, TDRC
        p = make(Path(tempfile.mkdtemp()) / "t.mp3")
        i = ID3()
        i.add(TDOR(encoding=3, text=["1999-05-01"]))
        i.add(TDRC(encoding=3, text=["2001-05"]))
        i.save(str(p), v2_version=3)
        self.assertEqual((tagio.read(p)["originaldate"], tagio.read(p)["date"]), (["1999-05-01"], ["2001-05"]))
        tagio.write(p, {"title": ["New title"]})
        back = tagio.read(p)
        self.assertEqual((back.get("originaldate"), back.get("date")), (["1999-05-01"], ["2001-05"]))


class V23CoverEmbed(unittest.TestCase):
    def test_front_cover_keeps_the_tag_valid_v23(self):
        """tagio.write converts a v2.3 tag (TDRC -> TYER/TDAT, TIPL/TMCL -> IPLS); embedding the
        front cover afterwards (executor.embed_front) saves it again as v2.3 WITHOUT
        update_to_v23, putting the v2.4-only TDRC and TIPL frames back - players that read
        v2.3 strictly lose the year and every credit."""
        import tempfile
        p = make(Path(tempfile.mkdtemp()) / "t.mp3", v23=True)
        tagio.write(p, {"date": ["2001-05-01"], "producer": ["P"], "performer:guitar": ["G"]})
        executor.embed_front(p, b"\xff\xd8\xff\xe0" + b"0" * 200)
        from mutagen.id3 import ID3
        tag = p.read_bytes()[:ID3(p).size + 10]
        self.assertEqual(ID3(p).version[:2], (2, 3))
        found = [x.decode() for x in (b"TDRC", b"TIPL", b"TMCL", b"TYER", b"IPLS") if x in tag]
        self.assertEqual(found, ["TYER", "IPLS"], "frames in the ID3v2.3 tag after the cover was embedded")


if __name__ == "__main__":
    unittest.main()
