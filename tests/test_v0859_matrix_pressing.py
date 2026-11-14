"""Two pressings sharing your barcode (MED-16, CPT-1 in docs/MATCHING_RULES.md), modelled on
Pink Floyd - The Dark Side Of The Moon Immersion: Discogs USA & Canada 3137117 and Europe
3142241 carry one barcode; MusicBrainz's CA 96a7097a links the first, its GB 12fb801c links the
second and carries your disc ID (the same disc in both boxes).

- MED-16: the USA & Canada entry numbers its Blu-ray "BR-1".."BR-7": chapters, never tracks of
  the CD rip (they made it 43 tracks for 36 files, so Europe won the barcode tie).
- CPT-1: the MusicBrainz entry written beside the Discogs pick is one MusicBrainz does not say
  is another pressing: the CA entry linking the pick, never the GB one linking 3142241 that
  only your shared disc ID admitted.
Controls: nothing tells the twins apart (no links) - the disc ID still decides, as before;
neither twin links the pick - unchanged; a matrix read on your scans that is neither
pressing's tells neither. Synthetic releases: no network, no library files.
"""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
DM = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
BARCODE = "5099902943121"
BR_TITLES = {"5.1 Surround Mix (2003)", "Original Stereo Mix", "Live In Brighton 1972"}
LENS = (67.0, 170.0, 225.0, 413.0, 284.0, 383.0, 469.0, 206.0)


def tr(pos, title, dur=""):
    return {"position": pos, "title": title, "duration": dur, "type_": "track"}


def immersion(bd_name="Blu-ray", bd_desc=("Album", "Stereo", "Multichannel")):
    """2 CDs, a DVD-Video and a Blu-ray; the Blu-ray's chapters numbered "BR-n", untimed."""
    return {"id": 3137117, "title": "The Dark Side Of The Moon", "artists": [{"name": "Pink Floyd"}],
            "year": 2011, "country": "USA & Canada",
            "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]},
                        {"name": "CD", "qty": "1", "descriptions": ["Stereo"]},
                        {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video", "NTSC"]},
                        {"name": bd_name, "qty": "1", "descriptions": list(bd_desc)},
                        {"name": "Box Set", "qty": "1"}],
            "labels": [{"name": "EMI", "catno": "50999 0 29431 2 1"}], "identifiers": [],
            "tracklist": [tr("CD1-1", "Speak To Me", "1:07"), tr("CD1-2", "Breathe", "2:50"),
                          tr("CD2-1", "Speak To Me (Live)", "2:46"), tr("CD2-2", "Breathe (Live)", "2:51"),
                          tr("DVD1-1", "5.1 Surround Mix"), tr("DVD1-2", "4.0 Quad Mix"),
                          tr("BR-1", "5.1 Surround Mix (2003)"), tr("BR-2", "Original Stereo Mix"),
                          tr("BR-3", "Live In Brighton 1972")]}


class MED16_BR(unittest.TestCase):
    def test_MED16_br_positions_of_a_listed_blu_ray_are_chapters(self):
        c = ma.discogs_candidate(immersion(), DM)
        self.assertEqual([t["title"] for t in c["tracks"]],
                         ["Speak To Me", "Breathe", "Speak To Me (Live)", "Breathe (Live)"])
        self.assertEqual(c["disc_count"], 2)

    def test_MED16_br_kept_where_the_blu_ray_holds_audio(self):
        c = ma.discogs_candidate(immersion(bd_desc=("Blu-ray Audio", "Stereo")), DM)
        self.assertEqual(len([t for t in c["tracks"] if t["title"] in BR_TITLES]), 3)
        # (audio tracks, as listed: never flagged video - which would leave them out of the layout)
        self.assertFalse([t for t in c["tracks"] if t.get("video")])

    def test_MED16_br_is_no_blu_ray_where_none_is_listed(self):
        # control: "BR-" on a release that lists no Blu-ray is not read as one
        c = ma.discogs_candidate(immersion(bd_name="CD", bd_desc=("Album",)), DM)
        self.assertEqual(len([t for t in c["tracks"] if t["title"] in BR_TITLES]), 3)


def album(**kw):
    a = {"artist": "Pink Floyd", "title": "The Dark Side Of The Moon", "year": 2011, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)],
         "barcodes": {BARCODE}, "catnos": set(),
         # your disc ID: attached in MusicBrainz to the GB entry only; CTDB links both Discogs boxes
         "toc_releases": {"gb", "ca"}, "exact_toc_releases": {"gb"}, "toc_discogs": {"3137117", "3142241"}}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x", "url": "u", "artist": "Pink Floyd", "title": "The Dark Side Of The Moon",
         "year": 2011, "country": "", "label": "EMI", "catnos": set(), "barcodes": {BARCODE},
         "disc_count": 1, "formats": {"cd"},
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)]}
    c.update(kw)
    return c


def run(a, *cands):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    r = mc.classify(scored, CFG)
    r["scored"] = scored
    return r


class CPT1_TwinOfThePick(unittest.TestCase):
    def picks(self, gb_links=("3142241",), ca_links=("3137117",)):
        a = album()
        gb = cand(id="gb", country="GB", discogs_ids=list(gb_links))
        ca = cand(id="ca", country="CA", discogs_ids=list(ca_links))
        mb = run(a, gb, ca)
        dg = run(a, cand(source="discogs", id="3137117", country="USA & Canada"))
        self.assertEqual(dg["status"], "auto", dg["why"])
        return mb, dg

    def test_CPT1_the_twin_linking_the_pick_beats_one_admitted_by_the_shared_disc_id(self):
        mb, dg = self.picks()
        self.assertEqual(sorted(s["cand"]["id"] for s in mc.mb_twins(mb, dg)), ["ca", "gb"])
        out = mc.combine(mb, dg)
        self.assertEqual((out["best"]["cand"]["source"], str(out["best"]["cand"]["id"])), ("discogs", "3137117"))
        self.assertEqual(out.get("mb_twin"), "ca")
        # (the disc ID's entry passed over is said, not hidden)
        self.assertTrue(any("your disc ID is on MusicBrainz gb" in n for n in out["best"]["notes"]), out["best"]["notes"])

    def test_CPT1_a_disc_id_twin_linking_the_pick_too_still_decides(self):
        # MusicBrainz's GB entry links both Discogs boxes: it says nothing against the pick
        mb, dg = self.picks(gb_links=("3142241", "3137117"))
        out = mc.combine(mb, dg)
        self.assertEqual(out.get("mb_twin"), "gb")
        self.assertFalse(any("your disc ID is on" in n for n in out["best"]["notes"]))

    def test_CPT1_a_twin_linking_nothing_beats_one_linking_another_pressing(self):
        mb, dg = self.picks(ca_links=())
        self.assertEqual(mc.combine(mb, dg).get("mb_twin"), "ca")

    def test_CPT1_no_links_either_side_the_disc_id_decides_as_before(self):
        # ambiguous control: MusicBrainz links neither entry - nothing but the disc ID tells them apart
        mb, dg = self.picks(gb_links=(), ca_links=())
        self.assertEqual(mc.combine(mb, dg).get("mb_twin"), "gb")

    def test_CPT1_both_link_other_releases_unchanged(self):
        # conflicting control: neither MusicBrainz entry links the pick - the CA one is no twin at all
        mb, dg = self.picks(gb_links=("3142241",), ca_links=("999",))
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dg)], ["gb"])
        self.assertEqual(mc.combine(mb, dg).get("mb_twin"), "gb")


class MatrixControls(unittest.TestCase):
    US = {"id": 3137117, "source": "discogs", "matrix": [("Matrix / Runout", "DIDX-739080  10  E15")]}
    EU = {"id": 3142241, "source": "discogs", "matrix": [("Matrix / Runout", "www.takt.eu 2100003177544 0289552")]}

    def test_a_matrix_read_tells_the_pressings_apart(self):
        ring = "PINK FLOYD  DIDX-739080 10 E15  IFPI L328"
        self.assertIn("matrix", mc.matrix_clues("", self.US, ring)[0])
        self.assertNotIn("matrix", mc.matrix_clues("", self.EU, ring)[0])

    def test_a_matrix_of_neither_pressing_tells_neither(self):
        ring = "PINK FLOYD  DIDX-123456 1 A01"
        self.assertNotIn("matrix", mc.matrix_clues("", self.US, ring)[0])
        self.assertNotIn("matrix", mc.matrix_clues("", self.EU, ring)[0])


if __name__ == "__main__":
    unittest.main()
