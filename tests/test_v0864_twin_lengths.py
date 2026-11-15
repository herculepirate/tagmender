"""FIL-3: a Discogs pick whose lengths are typed off the sleeve is filed on its own catalogue
number when MusicBrainz's entry of the same pressing, written beside it, agrees on every
track length. Synthetic data only."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

DG = {"source": "discogs", "id": "2563329", "url": "https://www.discogs.com/release/2563329",
      "artist": "Band", "title": "Fifth", "year": 2007, "country": "Europe", "label": "Sony BMG",
      "tracks": [{"disc": 1, "title": f"t{i}", "secs": 300} for i in range(8)]}


def _best(**kw):
    b = {"cand": dict(DG), "confidence": 92.2, "notes": [], "tracks_ok": True, "dur_frac": 0.62,
         "dur_cov": 1.0, "own_number": True}
    b.update(kw)
    return b


def _twin(**kw):
    t = {"tracks_ok": True, "dur_frac": 1.0, "dur_cov": 1.0}
    t.update(kw)
    return t


class FIL3TwinLengths(unittest.TestCase):
    def test_FIL3_lengths_all_agree(self):
        self.assertTrue(mc.lengths_all_agree(_twin()))
        self.assertFalse(mc.lengths_all_agree(_twin(dur_frac=0.9)))
        self.assertFalse(mc.lengths_all_agree(_twin(missing={1: [3]})))
        self.assertFalse(mc.lengths_all_agree(_twin(conflict="barcode differs")))
        self.assertFalse(mc.lengths_all_agree(None))

    def test_FIL3_twin_agreeing_on_every_length_counts(self):
        out = ma.summarize({"best": _best(), "mb_twin": "mb-1", "mb_twin_lengths": True})
        self.assertTrue(out["twin_lengths"])
        self.assertFalse(out["all_lengths"])        # (never for the other filing reasons, TAG-3)

    def test_FIL3_discogs_lengths_mostly_off_never(self):
        out = ma.summarize({"best": _best(dur_frac=0.4), "mb_twin": "mb-1", "mb_twin_lengths": True})
        self.assertFalse(out["twin_lengths"])

    def test_FIL3_only_the_own_number_reason_reads_it(self):
        import plan as pl
        import profiles as pf
        prof = pf.from_dict({"name": "t", "sources": [{"path": "/x"}]})

        def prop(**kw):
            d = dict(source_dir="/x/a", files=[], targets=[], provider="discogs", release_id="1",
                     confidence=92.2, margin=10.0, status="identified", tier="exact", twin_lengths=True)
            d.update(kw)
            return pl.Proposal(**d)
        self.assertIn("MusicBrainz's entry", pl._files_on_evidence(prop(own_number=True), prof, 92.2))
        self.assertEqual(pl._files_on_evidence(prop(own_number=True, guessed_pick=True), prof, 92.2), "")
        self.assertEqual(pl._files_on_evidence(prop(), prof, 92.2), "")                  # (no own number)
        self.assertEqual(pl._files_on_evidence(prop(scans={"printed": True}), prof, 92.2), "")

    def test_FIL3_no_twin_keeps_discogs_lengths(self):
        self.assertFalse(ma.summarize({"best": _best()})["twin_lengths"])
        self.assertFalse(ma.summarize({"best": _best(), "mb_twin": "mb-1", "mb_twin_lengths": False})["twin_lengths"])

    def test_FIL3_pick_missing_tracks_or_in_conflict_never(self):
        for kw in ({"lacks": ["Guest"]}, {"conflict": "barcode differs"}, {"tracks_ok": False}):
            out = ma.summarize({"best": _best(**kw), "mb_twin": "mb-1", "mb_twin_lengths": True})
            self.assertFalse(out["twin_lengths"], kw)

    def test_FIL3_combine_carries_the_twins_lengths(self):
        mb = {"cand": {"source": "mb", "id": "mb-1", "discogs_ids": ["2563329"], "title": "Fifth",
                       "artist": "Band", "tracks": DG["tracks"], "barcodes": [], "catnos": ["82876872902"],
                       "country": "XE", "year": 2007},
              "confidence": 95.0, "notes": [], "clues": {}, **_twin()}
        dg = _best()
        res = mc.combine({"status": "auto", "best": mb, "scored": [mb], "margin": 10.0},
                         {"status": "auto", "best": dg, "scored": [dg], "margin": 10.0})
        self.assertEqual(res.get("mb_twin"), "mb-1")
        self.assertTrue(res.get("mb_twin_lengths"))
        self.assertTrue(ma.summarize(res)["twin_lengths"])


if __name__ == "__main__":
    unittest.main()
