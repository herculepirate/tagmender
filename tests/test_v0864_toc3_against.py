"""TOC-3: MusicBrainz's sure pick carrying your exact disc ID does not overrule Discogs's pick when
your folder name or scans say something against MusicBrainz's pick that they do not say against
Discogs's (a disc ID that several pressings share). The two are then disputed, as when Discogs's
pick has evidence of its own. Synthetic results only."""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402


def pick(source, id_, country, against=(), clues=None):
    return {"cand": {"source": source, "id": id_, "country": country, "formats": ["cd"], "barcodes": [],
                     "catnos": [], "tracks": [{"disc": 1, "title": "a", "secs": 100}]},
            "confidence": 100.0, "notes": [], "tracks_ok": True, "clues": dict(clues or {}),
            "clues_against": list(against)}


def res(best, status="auto"):
    return {"status": status, "best": best, "scored": [best], "why": [], "margin": 20.0, "cfg": {}}


GB_AGAINST = "folder name says MX/US, this pressing is from GB"


class TOC3(unittest.TestCase):
    def mb(self, against=(GB_AGAINST,)):
        return res(pick("mb", "mb-gb", "GB", against, {"disc TOC": mc.EXACT_TOC_CLUE, "log": "rip log"}))

    def dg(self, against=()):
        return res(pick("discogs", "3137117", "US", against))

    def test_TOC3_folder_against_mb_pick_disputes(self):
        out = mc.mb_overrules(self.mb(), self.dg(), [])
        self.assertIsNotNone(out)
        self.assertEqual(out["status"], "review")                   # (disputed: no database preferred)
        self.assertEqual(out["runner_up"]["cand"]["id"], "3137117")
        self.assertTrue(any("says otherwise" in w for w in out.get("why") or [])
                        or any("says otherwise" in n for n in out["best"].get("notes") or []), out)
        self.assertNotIn("MusicBrainz chosen: your disc ID is attached to its release, not to Discogs's pick",
                         out["best"].get("notes") or [])

    def test_TOC3_nothing_against_mb_keeps_the_overrule(self):
        out = mc.mb_overrules(self.mb(against=()), self.dg(), [])
        self.assertIn("MusicBrainz chosen: your disc ID is attached to its release, not to Discogs's pick",
                      out["best"]["notes"])

    def test_TOC3_the_same_against_both_keeps_the_overrule(self):
        out = mc.mb_overrules(self.mb(), self.dg(against=("folder name says MX/US, this pressing is from DE",)), [])
        self.assertIn("MusicBrainz chosen: your disc ID is attached to its release, not to Discogs's pick",
                      out["best"]["notes"])

    def test_TOC3_scans_against_mb_disputes(self):
        out = mc.mb_overrules(self.mb(against=("scans say made in USA, this pressing is from GB",)), self.dg(), [])
        self.assertNotIn("MusicBrainz chosen: your disc ID is attached to its release, not to Discogs's pick",
                         out["best"].get("notes") or [])

    def test_TOC3_discogs_preferred_takes_discogs_pick(self):
        # (HF-2: the E10 shape with Sources preferring Discogs - the pressing your copy names)
        mb, dg = self.mb(), self.dg()
        mb["cfg"] = dg["cfg"] = {"prefer_source": "discogs"}
        out = mc.mb_overrules(mb, dg, [])
        self.assertEqual(out["status"], "auto")
        self.assertEqual(out["best"]["cand"]["id"], "3137117")
        self.assertTrue(any("as Sources prefers (HF-2)" in n for n in out["best"]["notes"]))

    def test_TOC3_your_barcode_on_mb_still_wins(self):
        mb = self.mb()
        mb["best"]["code"], mb["best"]["cand"]["barcodes"] = "barcode", ["5099902943121"]
        with unittest.mock.patch.object(mc, "_carries_barcode", side_effect=lambda s: s is mb["best"]):
            out = mc.mb_overrules(mb, self.dg(), [])
        self.assertIn("MusicBrainz chosen: your barcode is on its release, not on Discogs's pick", out["best"]["notes"])

    def test_TOC3_against_only(self):
        self.assertEqual(mc._against_only({"clues_against": [GB_AGAINST, "credit lacks X"]}, {}), [GB_AGAINST])
        self.assertEqual(mc._against_only({"clues_against": [GB_AGAINST]}, {"clues_against": [GB_AGAINST]}), [])


if __name__ == "__main__":
    unittest.main()
