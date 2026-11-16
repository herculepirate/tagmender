"""BAR-13: the entry carrying your copy's own code (the folder's catalogue number) whose track list
is too sparse to compare - no lengths, fewer tracks than your files - is the pressing, when a full
entry of the same album (same master, same discs, its titles) fits every track but lacks your code:
the album is proven by the full entry, the pressing by the code (Neil Young - Archives Vol. II,
folder "(Reprise 093624899051)": Discogs 16262491, the 2020 box, 131 tracks without lengths, over
17690677 and MusicBrainz 3796a182, the 2021 retail box, 139 agreeing). Synthetic data only."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
import match_core as mc  # noqa: E402

CODE, OTHER = "093624899051", "093624926214"
TITLES = ["Letter From Nam", "Come Along And Say", "Last Dance", "Walk On", "Homegrown"]
SECS = (180.0, 240.0, 300.0, 200.0, 260.0)
CFG = dict(mc.DEFAULT_CFG)


def _album(**kw):
    a = {"artist": "Neil Band", "title": "Archives Vol. II", "year": 2020, "discs": 1,
         "folder": f"2020 - Archives Vol. II (Reprise {CODE})",
         "tracks": [{"disc": 1, "num": i + 1, "title": t, "secs": s} for i, (t, s) in enumerate(zip(TITLES, SECS))],
         "barcodes": set(), "ocr_barcodes": [], "catnos": {mc.norm_code(CODE)},
         "catnos_from_folder": {mc.norm_code(CODE)}, "rip_log_year": 2020}
    a.update(kw)
    return a


def _full(**kw):
    """The full entry: every track, every length; the 2021 retail box's number."""
    c = {"source": "discogs", "id": "full", "url": "", "artist": "Neil Band", "title": "Archives Vol. II",
         "year": 2021, "date": "2021-03-05", "country": "Europe", "label": "Reprise",
         "catnos": {mc.norm_code(OTHER)}, "catno_shown": OTHER, "barcodes": {mc.norm_code(OTHER)},
         "disc_count": 1, "formats": {"cd"}, "master_id": "900",
         "tracks": [{"disc": 1, "title": t, "secs": s} for t, s in zip(TITLES, SECS)]}
    c.update(kw)
    return c


def _sparse(**kw):
    """The pressing your folder names: four of the five tracks, no lengths."""
    c = _full(id="sparse", year=2020, date="2020-11-20", country="USA, Canada & Europe",
              catnos={mc.norm_code(CODE)}, catno_shown=CODE, barcodes={mc.norm_code(CODE)},
              tracks=[{"disc": 1, "title": t, "secs": 0} for t in TITLES[:4]])
    c.update(kw)
    return c


def _scored(album, *cands, cfg=CFG):
    return mc.code_twins(album, [mc.score_candidate(album, c, cfg) for c in cands], cfg)


def _res(album, *cands, cfg=CFG):
    scored = _scored(album, *cands, cfg=cfg)
    return dict(mc.classify(scored, cfg), scored=scored, cfg=cfg, source=cands[0]["source"])


def _pick(res):
    return str(((res or {}).get("best") or {}).get("cand", {}).get("id"))


class BAR13CodeTwin(unittest.TestCase):
    def test_BAR13_the_entry_carrying_your_code_is_the_pressing(self):
        res = _res(_album(), _full(), _sparse())
        self.assertEqual((res["status"], _pick(res)), ("auto", "sparse"), res.get("why"))
        best = res["best"]
        self.assertEqual(best["cand"]["tracks_of"], "full")
        self.assertEqual(len(best["cand"]["tracks"]), 5)
        self.assertEqual(best["code"], "catalogue number (folder name)")
        self.assertTrue(any("BAR-13" in n for n in best["notes"]), best["notes"])

    def test_BAR13_without_lengths_and_every_track_listed_too(self):
        sparse = _sparse(tracks=[{"disc": 1, "title": t, "secs": 0} for t in TITLES])
        res = _res(_album(), _full(), sparse)
        self.assertEqual((res["status"], _pick(res)), ("auto", "sparse"), res.get("why"))

    def test_BAR13_scored_again_from_its_own_entry(self):
        # (a result scored again - match_albums._whole - starts from the sparse entry itself)
        sc = _scored(_album(), _full(), _sparse())
        view = next(s for s in sc if s["cand"]["id"] == "sparse")
        self.assertEqual(len(view["cand"]["sparse_cand"]["tracks"]), 4)

    def test_BAR13_another_album_carrying_your_code_is_not_picked(self):
        other = _sparse(master_id="901", title="Homegrown")
        self.assertNotEqual(_pick(_res(_album(), _full(), other)), "sparse")
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), _full(), other)))

    def test_BAR13_another_disc_count_is_not_picked(self):
        other = _sparse(disc_count=2)
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), _full(), other)))
        self.assertNotEqual(_pick(_res(_album(), _full(), other)), "sparse")

    def test_BAR13_a_pressing_later_than_your_rip_log_is_not_picked(self):
        later = _sparse(year=2021, date="2021-03-05")
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), _full(), later)))
        self.assertNotEqual(_pick(_res(_album(), _full(), later)), "sparse")

    def test_BAR13_a_barcode_in_your_tags_that_differs_keeps_it_out(self):
        album = _album(barcodes={"0724352081127"})
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(album, _full(), _sparse())))
        self.assertNotEqual(_pick(_res(album, _full(), _sparse())), "sparse")

    def test_BAR13_a_full_entry_carrying_your_code_too_needs_no_twin(self):
        full = _full(catnos={mc.norm_code(CODE)}, barcodes={mc.norm_code(CODE)})
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), full, _sparse())))

    def test_BAR13_titles_of_another_album_under_one_master_are_not_its_twin(self):
        other = _sparse(tracks=[{"disc": 1, "title": t, "secs": 0} for t in ("A", "B", "C", "D")])
        self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), _full(), other)))

    def test_BAR13_an_entry_of_your_number_listing_its_own_lengths_is_never_outranked(self):
        # (one number on the original and a reissue: the original lists its own lengths, sleeve-
        # typed, one or two seconds off; a remaster of the master without your number fits to the
        # second - its tracks never make the sparse reissue a view that beats the original)
        for off in ((4,), (3, 4)):
            with self.subTest(off=off):
                orig = _full(id="orig", catnos={mc.norm_code(CODE)}, catno_shown=CODE, barcodes=set(),
                             year=2020, date="2020-11-20",
                             tracks=[{"disc": 1, "title": t, "secs": s + (9 if i in off else 0)}
                                     for i, (t, s) in enumerate(zip(TITLES, SECS))])
                sparse = _sparse(barcodes=set())
                sc = _scored(_album(), sparse, _full(id="remaster"), orig)
                self.assertFalse(any(s["cand"].get("tracks_of") for s in sc))
                self.assertEqual(_pick(_res(_album(), sparse, _full(id="remaster"), orig)), "orig")

    def test_BAR13_a_list_of_one_or_two_tracks_names_no_album(self):
        for titles in (TITLES[3:4], TITLES[3:5]):
            with self.subTest(n=len(titles)):
                few = _sparse(tracks=[{"disc": 1, "title": t, "secs": 0} for t in titles])
                self.assertFalse(any(s["cand"].get("tracks_of") for s in _scored(_album(), _full(), few)))

    def test_BAR13_a_musicbrainz_entry_is_not_tagged_from_another(self):
        mb = dict(source="mb", master_id="", release_group="rg-1")
        sc = _scored(_album(), _full(id="mb-full", **mb), _sparse(**mb))
        self.assertFalse(any(s["cand"].get("tracks_of") for s in sc))

    def test_BAR13_without_a_code_of_yours_nothing_changes(self):
        album = _album(catnos=set(), catnos_from_folder=set(), folder="2020 - Archives Vol. II")
        plain = [mc.score_candidate(album, c, CFG) for c in (_full(), _sparse())]
        twins = _scored(album, _full(), _sparse())
        self.assertEqual([s["confidence"] for s in twins], [s["confidence"] for s in plain])
        self.assertEqual(_pick(_res(album, _full(), _sparse())), "full")


class BAR13AcrossTheDatabases(unittest.TestCase):
    def _mb(self, prefer=""):
        cfg = dict(CFG, prefer_source=prefer)
        # MusicBrainz's entry of the 2021 box, your exact disc ID attached (as disc IDs of one master are)
        mb = _full(source="mb", id="mb-2021", year=None, date="", country="XE", master_id="",
                   release_group="rg-1")
        res = _res(_album(), mb, cfg=cfg)
        res["best"]["clues"] = dict(res["best"].get("clues") or {}, **{"disc TOC": mc.EXACT_TOC_CLUE})
        return res, cfg

    def test_BAR13_musicbrainz_entry_of_another_pressing_yields(self):
        for prefer in ("", "musicbrainz", "discogs"):
            with self.subTest(prefer=prefer):
                mb, cfg = self._mb(prefer)
                self.assertEqual(mb["status"], "auto")
                dg = _res(_album(), _full(), _sparse(), cfg=cfg)
                final = mc.combine(mb, dg)
                self.assertEqual((final["status"], _pick(final)), ("auto", "sparse"), final.get("why"))
                self.assertFalse(final.get("mb_twin"))

    def test_BAR13_musicbrainz_entry_carrying_your_code_stands(self):
        mb, cfg = self._mb("musicbrainz")
        mb["best"]["code"] = "catalogue number (folder name)"
        dg = _res(_album(), _full(), _sparse(), cfg=cfg)
        self.assertIs(mc._yields_to_code_twin(mb, dg), mb)

    def test_BAR13_musicbrainz_entry_of_another_album_stands(self):
        mb, cfg = self._mb("musicbrainz")
        mb["best"]["cand"] = dict(mb["best"]["cand"], title="Harvest Moon")
        dg = _res(_album(), _full(), _sparse(), cfg=cfg)
        self.assertIs(mc._yields_to_code_twin(mb, dg), mb)


class VersionsHttp(te.FakeHttp):
    """FakeHttp that also lists a Discogs master's versions (one page)."""

    def get_json(self, url, host):
        if host == "discogs" and "/masters/" in url and "/versions" in url:
            mid = int(url.split("/masters/")[1].split("/")[0])
            vs = [{"id": r["id"]} for r in self.dg.values() if r.get("master_id") == mid]
            return {"versions": vs, "pagination": {"pages": 1}}
        return super().get_json(url, host)


def _releases():
    full = te.dg_release(501, "Neil Band", "Archives", durs=("0:10", "0:20", "0:30", "0:40"), catno=OTHER)
    full.update(year=2021, released="2021-03-05", master_id=900, identifiers=[{"type": "Barcode", "value": OTHER}])
    sparse = te.dg_release(502, "Neil Band", "Archives", durs=("", "", ""), catno=CODE, country="US")
    sparse.update(year=2020, released="2020-11-20", master_id=900, identifiers=[{"type": "Barcode", "value": CODE}])
    mb = te.mb_release("mb-2021", "Neil Band", "Archives", lens=[10000, 20000, 30000, 40000], catno=OTHER)
    mb["barcode"] = OTHER
    mb["media"] = mb["media"][:1]
    return full, sparse, mb


class BAR13Tagged(te.Base):
    """Through the engine: the pressing's own fields, the full entry's tracks, no MusicBrainz ID."""

    def folder(self):
        return self.album("Neil Band", "Archives", n=4, folder=f"2020 - Archives (Reprise {CODE})")

    def http(self):
        full, sparse, mb = _releases()
        return VersionsHttp(mb=[mb], dg=[full, sparse])

    def assert_bar13(self, p):
        self.assertEqual((p.status, p.release_id), ("identified", "502"), p.why)
        self.assertEqual([t["title"][0] for t in p.targets], ["T0", "T1", "T2", "T3"])
        self.assertEqual({t["discogs_release_id"][0] for t in p.targets}, {"502"})
        self.assertEqual({t["catalognumber"][0] for t in p.targets}, {CODE})
        self.assertFalse(any(t.get("musicbrainz_albumid") for t in p.targets))

    def test_BAR13_tagged_from_the_full_entrys_tracks(self):
        self.folder()
        eng = self.engine(self.http())
        self.assert_bar13(eng.identify(eng.albums_in(self.lib)[0]))

    def test_BAR13_the_pressing_chosen_by_hand_is_tagged_as_the_matcher_tags_it(self):
        d = self.folder()
        eng = self.engine(self.http())
        eng.force = {os.path.realpath(d): "https://www.discogs.com/release/502"}
        self.assert_bar13(eng.identify(eng.albums_in(self.lib)[0]))

    def test_BAR13_planned_again_after_its_job_nothing_changes(self):
        d = self.folder()
        p = self.engine(self.http()).plan(d)
        self.assertEqual(p["albums"][0]["status"], "ready", te.pl.preview(p))
        self.assertEqual(te.executor.Executor(p).run()["counts"], {"done": 1})
        t = te.tagio.read(next(iter(sorted(self.lib.rglob("*.flac")))))
        self.assertEqual((t["discogs_release_id"], t["catalognumber"]), (["502"], [CODE]))
        again = self.engine(self.http()).plan(self.lib / "Neil Band")
        self.assertEqual([a["status"] for a in again["albums"]], ["unchanged"], te.pl.preview(again))

    def test_BAR13_a_full_entry_carrying_your_code_is_tagged_as_before(self):
        folder = f"2020 - Archives (Reprise {CODE})"
        self.album("Neil Band", "Archives", n=4, folder=folder)
        full = te.dg_release(501, "Neil Band", "Archives", durs=("0:10", "0:20", "0:30", "0:40"), catno=CODE)
        full.update(year=2020, master_id=900)
        eng = self.engine(te.FakeHttp(dg=[full]))
        p = eng.identify(eng.albums_in(self.lib)[0])
        self.assertEqual((p.status, p.release_id), ("identified", "501"), p.why)


if __name__ == "__main__":
    unittest.main()
