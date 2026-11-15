"""BAR-12: MusicBrainz's sure pick carries your barcode but not your disc IDs; another
MusicBrainz release lists no barcode, carries every disc ID of your copy (TOC-1), links only
Discogs releases carrying your barcode, and Discogs's own pick (sure or held) is one of them,
not one the barcode entry links: it is an entry of the same product, and it is taken
(Univers Zero - Heatwave, Cuneiform RUNE 9). A disc ID alone never decides it. Synthetic
results only."""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402

UPC = "045775000923"
CFG = dict(mc.DEFAULT_CFG, preferred_countries=["US"])


def _cand(**kw):
    c = {"source": "discogs", "id": "x", "url": "", "artist": "Band", "title": "Heatwave", "year": 1987,
         "country": "US", "label": "Cuneiform", "catnos": {mc.norm_code("RUNE 9 CD")}, "barcodes": set(),
         "disc_count": 1, "formats": {"cd"},
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
    c.update(kw)
    return c


def _album(on=("disc-mb",), **kw):
    """Your copy: the scans' barcode, a rip log's TOC fitting the files, its disc ID attached to
    the releases ON (TOC-1)."""
    a = {"artist": "Band", "title": "Heatwave", "year": 1987, "discs": 1, "folder": "1987 - Heatwave",
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": {UPC}, "ocr_barcodes": [UPC], "catnos": set(),
         "tocs": {1: "toc1"}, "tocs_fit": [1],
         "toc_releases": set(on), "exact_toc_releases": set(on), "exact_toc_on": {r: {"toc1": {1}} for r in on}}
    a.update(kw)
    return a


def res(a, cands):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    return dict(mc.classify(scored, CFG), scored=scored)


def held(r, best_id):
    """Discogs held on a tie, its pick BEST_ID (as Heatwave's: the pressings sharing your barcode)."""
    best = next(s for s in r["scored"] if s["cand"]["id"] == best_id)
    return dict(r, status="review", best=best, why=["runner-up within 0.0 points (likely another pressing)"])


def both(mb, dg, countries=("US",)):
    """combine's pick with BAR-12, and without it (what was planned before)."""
    with_it = mc.combine(mb, dg, list(countries))
    with unittest.mock.patch.object(mc, "_disc_id_twin_of_barcode", lambda m, d: m):
        without = mc.combine(mb, dg, list(countries))
    return with_it, without


def ids(out):
    return (out["status"], out["best"]["cand"]["id"], out.get("mb_twin") or out.get("dg_twin"))


BARCODE_MB = dict(source="mb", id="barcode-mb", country="BE", barcodes={UPC})


class BAR12DiscIdTwin(unittest.TestCase):
    def heatwave(self, album=None, twin=None, dg=None, barcode_mb=None, pick="dg-a"):
        a = album or _album()
        mb = res(a, [_cand(**(barcode_mb or BARCODE_MB)),
                     twin if twin is not None else _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"])])
        dgr = res(a, dg if dg is not None else [_cand(id="dg-a", barcodes={UPC}), _cand(id="dg-b", barcodes={UPC})])
        return mb, held(dgr, pick)

    def test_BAR12_discogs_held_on_its_barcode_tie_picks_the_disc_id_entry(self):
        mb, dg = self.heatwave()
        self.assertEqual((mb["status"], mb["best"]["cand"]["id"]), ("auto", "barcode-mb"))
        out = mc._disc_id_twin_of_barcode(mb, dg)
        self.assertEqual(out["best"]["cand"]["id"], "disc-mb")
        self.assertTrue(any("BAR-12" in n for n in out["best"]["notes"]))
        with_it, without = both(mb, dg)
        self.assertEqual(with_it["best"]["cand"]["id"], "disc-mb")
        self.assertEqual(without["best"]["cand"]["id"], "barcode-mb")

    def test_BAR12_discogs_holding_another_pick_keeps_your_barcode_release(self):
        # (Discogs's held pick is not one the disc-ID entry links: nothing agrees with the disc ID)
        mb, dg = self.heatwave(pick="dg-b")
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_linked_entry_with_another_barcode_keeps_your_barcode_release(self):
        mb, dg = self.heatwave(dg=[_cand(id="dg-a", barcodes={"5012345678900"}), _cand(id="dg-b", barcodes={UPC})])
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_linked_entry_listing_no_barcode_keeps_your_barcode_release(self):
        mb, dg = self.heatwave(dg=[_cand(id="dg-a"), _cand(id="dg-b", barcodes={UPC})])
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_linked_entry_not_scored_keeps_your_barcode_release(self):
        mb, dg = self.heatwave(twin=_cand(source="mb", id="disc-mb", discogs_ids=["dg-a", "unseen"]))
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_no_discogs_link_keeps_your_barcode_release(self):
        # (nothing ties the entry to your barcode: the barcode names the release, BAR-1)
        mb, dg = self.heatwave(twin=_cand(source="mb", id="disc-mb"))
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_an_entry_listing_another_barcode_is_never_taken(self):
        twin = _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"], barcodes={"5012345678900"})
        mb, dg = self.heatwave(twin=twin)
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)
        self.assertEqual(mc.combine(mb, dg, ["US"])["best"]["cand"]["id"], "barcode-mb")

    def test_BAR12_two_such_entries_name_neither(self):
        a = _album(on=("disc-mb", "disc-mb2"))
        mb = res(a, [_cand(**BARCODE_MB), _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"]),
                     _cand(source="mb", id="disc-mb2", discogs_ids=["dg-a"])])
        dg = held(res(a, [_cand(id="dg-a", barcodes={UPC}), _cand(id="dg-b", barcodes={UPC})]), "dg-a")
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_your_barcode_release_with_your_disc_id_stands(self):
        mb, dg = self.heatwave(album=_album(on=("disc-mb", "barcode-mb")))
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    # The independent review's cases (each with and without BAR-12)

    def test_BAR12_discogs_sure_of_a_pick_the_entry_does_not_link_stands(self):
        # (review 1, 2: Discogs sure of dg-b, which the barcode entry links; never a dispute or a
        # lost MusicBrainz pair, whatever the Sources preference)
        a = _album(catnos={mc.norm_code("RUNE 9X")}, catnos_from_folder=[mc.norm_code("RUNE 9X")])
        mb = res(a, [_cand(**BARCODE_MB, discogs_ids=["dg-b"]), _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"])])
        dg = res(a, [_cand(id="dg-a", barcodes={UPC}),
                     _cand(id="dg-b", country="BE", barcodes={UPC}, catnos={mc.norm_code("RUNE 9X")})])
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)
        with_it, without = both(mb, dg)
        self.assertEqual(ids(with_it), ids(without))

    def test_BAR12_discogs_held_on_the_barcode_entrys_pressing_stands(self):
        # (review 3: Discogs's held pick, with a matrix of your scans, is the one the barcode
        # entry links: the disc ID never overrules it)
        a = _album()
        mb = res(a, [_cand(**BARCODE_MB, discogs_ids=["dg-b"]), _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"])])
        dgr = res(a, [_cand(id="dg-b", country="BE", barcodes={UPC}), _cand(id="dg-a", barcodes={UPC})])
        dgr["scored"][0]["clues"] = dict(dgr["scored"][0].get("clues") or {}, physical="matrix BE-1 agrees")
        dg = held(dgr, "dg-b")
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)
        with_it, without = both(mb, dg)
        self.assertEqual(ids(with_it), ids(without))

    def test_BAR12_one_disc_id_of_two_discs_is_no_proof(self):
        # (review 4: TOC-1 needs every disc's ID on that disc of the release)
        tracks = [{"disc": d, "num": i + 1, "title": f"T{d}{i}", "secs": s}
                  for d in (1, 2) for i, s in enumerate((200.0, 305.0, 410.0))]
        ctracks = [{"disc": d, "title": f"T{d}{i}", "secs": s} for d in (1, 2) for i, s in enumerate((200, 305, 410))]
        a = _album(discs=2, tracks=tracks, tocs={1: "t1", 2: "t2"}, tocs_fit=[1, 2], exact_toc_on={"disc-mb": {"t1": {1}}})
        mb = res(a, [_cand(**BARCODE_MB, disc_count=2, tracks=ctracks),
                     _cand(source="mb", id="disc-mb", discogs_ids=["dg-a"], disc_count=2, tracks=ctracks)])
        dg = held(res(a, [_cand(id="dg-a", barcodes={UPC}, disc_count=2, tracks=ctracks),
                          _cand(id="dg-b", barcodes={UPC}, disc_count=2, tracks=ctracks)]), "dg-a")
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)

    def test_BAR12_a_club_entry_discogs_does_not_pick_stays_out(self):
        # (review 5: the club entry carrying the disc ID links a Discogs club entry listing your
        # retail barcode among its own; Discogs holds the retail pressing: nothing agrees)
        a = _album(on=("club-mb",))
        mb = res(a, [_cand(**dict(BARCODE_MB, id="retail-mb", country="US"), discogs_ids=["dg-retail"]),
                     _cand(source="mb", id="club-mb", label="BMG Direct Marketing", discogs_ids=["dg-club"])])
        dg = held(res(a, [_cand(id="dg-retail", barcodes={UPC}),
                          _cand(id="dg-club", label="BMG Direct Marketing", barcodes={UPC, "D131674"})]), "dg-retail")
        self.assertIs(mc._disc_id_twin_of_barcode(mb, dg), mb)
        with_it, without = both(mb, dg)
        self.assertEqual(ids(with_it), ids(without))


if __name__ == "__main__":
    unittest.main()
