# Thirteenth independent review (Picard code): this test failed before its fix.
"""performer:<role> keeps its role as MusicBrainz spells it ("performer:EWI", "performer:Hammond
organ", "performer:Moog"), as Picard writes it.

tagio._norm lower-cases every field name, so "performer:Hammond organ" is written as TMCL
role "hammond organ" / PERFORMER "Joe (hammond organ)" and reads back as
"performer:hammond organ". The executor's read-back check then finds the field missing and
puts the whole album back; and were it written, the plan would add it again every run."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review13b_common import make  # noqa: E402

import executor  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)


def _hammond_release():
    """A release whose personnel is credited on the release (release_ars, on by default)."""
    joe = {"id": "a2", "name": "Joe Smith", "sort-name": "Smith, Joe"}
    band = {"id": "a1", "name": "The Band", "sort-name": "Band, The"}
    return {"id": "r1", "title": "Album", "artist-credit": [{"name": "The Band", "artist": band, "joinphrase": ""}],
            "relations": [{"target-type": "artist", "type": "instrument", "artist": joe,
                           "attributes": ["Hammond organ"], "direction": "backward"}],
            "media": [{"position": 1, "format": "CD", "tracks": [
                {"id": "t1", "position": 1, "title": "One", "recording": {"id": "rc1", "title": "One"}}]}]}


class TagioKeepsTheRole(unittest.TestCase):
    def test_role_case_round_trips(self):
        for ext in (".flac", ".mp3", ".wv"):
            with self.subTest(ext):
                p = make(Path(tempfile.mkdtemp()) / f"t{ext}")
                tagio.write(p, {"performer:EWI": ["Ann"], "performer:Hammond organ": ["Joe"]})
                back = {k: v for k, v in tagio.read(p).items() if k.startswith("performer")}
                self.assertEqual(back, {"performer:EWI": ["Ann"], "performer:Hammond organ": ["Joe"]})

    def test_flac_value_as_picard_writes_it(self):
        from mutagen.flac import FLAC
        p = make(Path(tempfile.mkdtemp()) / "t.flac")
        tagio.write(p, {"performer:Hammond organ": ["Joe"]})
        self.assertEqual(FLAC(p).get("PERFORMER"), ["Joe (Hammond organ)"])

    def test_same_role_in_two_cases_loses_nobody(self):
        """MusicBrainz's "performer:EWI" and a Discogs-style "performer:ewi" in one target:
        _norm folds both into one key and the first one's names are dropped."""
        p = make(Path(tempfile.mkdtemp()) / "t.flac")
        tagio.write(p, {"performer:EWI": ["Ann"], "performer:ewi": ["Bob"]})
        names = sorted(v for k, vs in tagio.read(p).items() if k.lower() == "performer:ewi" for v in vs)
        self.assertEqual(names, ["Ann", "Bob"])


class AlbumWithACapitalisedInstrument(Base):
    def test_tags_and_settles(self):
        tags = mb_tags.release_tags(_hammond_release(), {})
        self.assertEqual(tags[0].get("performer:Hammond organ"), ["Joe Smith"])   # (as Picard makes it)
        prof = self.profile()
        extra = {"performer:Hammond organ": ["Joe Smith"]}
        prop = lambda: self.proposal(targets=[target(1, "First", **extra), target(2, "Second", **extra)])  # noqa: E731
        res = executor.Executor(pl.build([prop()], prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, [a.get("detail") for a in res["albums"]])
        again = pl.build([prop()], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
