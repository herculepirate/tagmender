# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). Not a regression (the previous version fails the same
# way); the new TMOO/TDRL conversion in tagio.save_id3 was meant to cover this change of version.
# An ID3v2.3 file holds its mood / release date as TXXX:mood / TXXX:RELEASEDATE (as Picard
# writes v2.3). With Picard's "write ID3v2.4" (write_id3v23 off) the job makes the tag v2.4,
# and the profile removes the field (field policy "remove"; a tagger script's $delete or the
# release's "~remove" go the same way). tagio.write takes the v2.4 path for the field
# (v23 False): _set_id3("mood", []) deletes only TMOO - the TXXX:mood stays - and then
# save_id3's new conversion (v2 == 4, own == 3, a TXXX:mood there, no TMOO) turns that
# TXXX:mood back into TMOO. The value the job removes is written right back: the read-back
# check fails, the whole album is rolled back ("tag mood did not read back as written") - on
# every run: the album is never tagged and the plan never settles.
# The same failure, with no version change, when the file holds its mood in a TXXX frame
# another tagger named differently: TXXX:MOOD in a v2.3 tag (Mp3tag, foobar2000) - _set_id3's
# "~v23:" path deletes only "TXXX:mood" (case-sensitive HashKey) - or TXXX:mood in a v2.4 tag -
# the v2.4 path writes TMOO and leaves the TXXX. read() then gives the old and the new mood.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, bare_mp3, executor, pl, tagio, target  # noqa: E402

from mutagen.id3 import ID3, TIT2, TXXX  # noqa: E402


class V24UpgradeCannotRemoveMood(Base):
    def go(self, field, desc, value):
        alb = self.lib / "Artist" / field
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            i = ID3()
            i.add(TIT2(encoding=3, text=["Old"]))
            i.add(TXXX(encoding=3, desc=desc, text=[value]))
            i.save(str(f), v2_version=3)
        self.assertEqual(tagio.read(fs[0])[field], [value])
        prof = self.profile(follow_picard=False, picard={"write_id3v23": False},
                            field_policy={"default": "replace", "fields": {field: "remove"}})
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop], prof)
        fe = p["albums"][0]["files"][0]
        self.assertEqual(fe.get("format"), {"v2": 4})
        self.assertIn({"field": field, "before": [value], "after": [], "action": "removed"}, fe["diff"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1},
                         f"the album failed: {[a.get('detail') for a in res['albums']]}")
        self.assertNotIn(field, tagio.read(fs[0]))
        self.assertEqual(ID3(str(fs[0])).version[:2], (2, 4))
        again = pl.build([prop], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged")

    def change(self, ver, desc, picard):
        alb = self.lib / "Artist" / f"{ver}{desc}"
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            i = ID3()
            i.add(TIT2(encoding=3, text=["Old"]))
            i.add(TXXX(encoding=3, desc=desc, text=["Calm"]))
            i.save(str(f), v2_version=ver)
        self.assertEqual(tagio.read(fs[0])["mood"], ["Calm"])
        prof = self.profile(follow_picard=False, picard=picard)
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First", mood=["Happy"]), target(2, "Second", mood=["Happy"])])
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1},
                         f"the album failed: {[a.get('detail') for a in res['albums']]}")
        self.assertEqual(tagio.read(fs[0])["mood"], ["Happy"])
        self.assertEqual(pl.build([prop], prof)["albums"][0]["status"], "unchanged")

    def test_v23_txxx_MOOD_changed(self):
        self.change(3, "MOOD", {})

    def test_v24_txxx_mood_changed(self):
        self.change(4, "mood", {})

    def test_mood(self):
        self.go("mood", "mood", "Calm")

    def test_releasedate(self):
        self.go("releasedate", "RELEASEDATE", "2001-05-01")


if __name__ == "__main__":
    unittest.main()
