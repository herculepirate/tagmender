"""BAR-12: MusicBrainz's sure pick carries your barcode but not your exact disc ID; another
MusicBrainz release lists no barcode, carries your exact disc ID and links only Discogs
releases carrying your barcode: it is an entry of the same product, and the disc ID picks it
(Univers Zero - Heatwave, Cuneiform RUNE 9). Synthetic results only."""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402

UPC = "045775000923"


def _cand(**kw):
    c = {"source": "discogs", "id": "x", "url": "", "artist": "Band", "title": "Heatwave", "year": 1987,
         "country": "US", "label": "Cuneiform", "catnos": {mc.norm_code("RUNE 9 CD")}, "barcodes": set(),
         "disc_count": 1, "formats": {"cd"},
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
    c.update(kw)
    return c


def _album(**kw):
    a = {"artist": "Band", "title": "Heatwave", "year": 1987, "discs": 1, "folder": "1987 - Heatwave",
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": {UPC}, "ocr_barcodes": [UPC], "catnos": set(),
         "toc_releases": {"disc-mb"}, "exact_toc_releases": {"disc-mb"}}
    a.update(kw)
    return a


class BAR12DiscIdTwin(unittest.TestCase):
    def results(self, album=None, mb_extra=(), twin=None, dg=None):
        cfg = dict(mc.DEFAULT_CFG, preferred_countries=["US"])
        a = album or _album()

        def res(cands):
            scored = [mc.score_candidate(a, c, cfg) for c in cands]
            return dict(mc.classify(scored, cfg), scored=scored)
        twin = twin if twin is not None else _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"])
        mb = res([_cand(source="mb", id="barcode-mb", country="BE", barcodes={UPC}), twin, *mb_extra])
        dg = res(dg if dg is not None else [_cand(id="dg-a", barcodes={UPC}), _cand(id="dg-b", barcodes={UPC})])
        return mb, dg

    def test_BAR12_the_disc_id_picks_the_entry_listing_no_barcode(self):
        mb, dg = self.results()
        self.assertEqual((mb["status"], mb["best"]["cand"]["id"]), ("auto", "barcode-mb"))
        out = mc._disc_id_twin_of_barcode(mb, dg)
        self.assertEqual(out["best"]["cand"]["id"], "disc-mb")
        self.assertTrue(any("BAR-12" in n for n in out["best"]["notes"]))
        # (the Discogs entry it links to comes along: the pair your disc ID and barcode name)
        out = mc.combine(mb, dg, ["US"])
        self.assertEqual((out["best"]["cand"]["id"], out.get("mb_twin")), ("dg-a", "disc-mb"))

    def test_BAR12_discogs_held_on_its_barcode_tie(self):
        # (Heatwave: Discogs ties the pressings sharing your barcode; before BAR-12 MusicBrainz's
        # barcode entry was taken, now the entry carrying your disc ID)
        mb, dg = self.results()
        dg = dict(dg, status="review", why=["runner-up dg-b within 0.0 points (likely another pressing)"])
        self.assertEqual(mc.combine(mb, dg, ["US"])["best"]["cand"]["id"], "disc-mb")
        with unittest.mock.patch.object(mc, "_disc_id_twin_of_barcode", lambda m, d: m):
            self.assertEqual(mc.combine(mb, dg, ["US"])["best"]["cand"]["id"], "barcode-mb")

    def test_BAR12_a_linked_entry_with_another_barcode_keeps_your_barcode_release(self):
        mb, dg = self.results(dg=[_cand(id="dg-a", barcodes={"5012345678900"}), _cand(id="dg-b", barcodes={UPC})])
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_linked_entry_listing_no_barcode_keeps_your_barcode_release(self):
        mb, dg = self.results(dg=[_cand(id="dg-a"), _cand(id="dg-b", barcodes={UPC})])
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_linked_entry_not_scored_keeps_your_barcode_release(self):
        twin = _cand(source="mb", id="disc-mb", discogs_ids=["dg-a", "unseen"])
        mb, dg = self.results(twin=twin)
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_no_discogs_link_keeps_your_barcode_release(self):
        # (nothing ties the entry to your barcode: the barcode names the release, BAR-1)
        mb, dg = self.results(twin=_cand(source="mb", id="disc-mb"))
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_an_entry_listing_another_barcode_is_another_product(self):
        twin = _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"], barcodes={"5012345678900"})
        mb, dg = self.results(twin=twin)
        self.assertNotEqual(mc.combine(mb, dg, ["US"])["best"]["cand"]["id"], "disc-mb")

    def test_BAR12_a_disc_id_only_near_or_not_attached_proves_nothing(self):
        mb, dg = self.results(album=_album(exact_toc_releases=set()))
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_two_such_entries_name_neither(self):
        a = _album(toc_releases={"disc-mb", "disc-mb2"}, exact_toc_releases={"disc-mb", "disc-mb2"})
        extra = [_cand(source="mb", id="disc-mb2", discogs_ids=["dg-b"])]
        mb, dg = self.results(album=a, mb_extra=extra)
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_your_barcode_release_with_your_disc_id_stands(self):
        a = _album(toc_releases={"disc-mb", "barcode-mb"}, exact_toc_releases={"disc-mb", "barcode-mb"})
        mb, dg = self.results(album=a)
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)


if __name__ == "__main__":
    unittest.main()
