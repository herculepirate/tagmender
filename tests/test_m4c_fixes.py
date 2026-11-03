"""Offline tests for the M4c fixes: each is modelled on an audited
album, synthetic (made-up candidates); no file of the library is read.

- Twins: another album's download a search brought in never makes the album's only
  download "one of several" (Sunn O))) - Veils It White: MusicBrainz 9f4e7468 is the twin of
  Discogs 11703407, not "another pressing").
- Exact lengths: the one release fitting every track exactly, where the pick misses every
  one and nothing else decides, is picked (Bell Witch - Demo 2011: 3dc36a34 over d6f7a466);
  two such releases, a near fit, or evidence for the pick keep the pick and its review.
- Track totals in a blind run: a total that is the other database's count of the pick's
  own pressing (DVD chapters, index sub-tracks) is no missing file (Dark Tranquillity,
  Intronaut, Tourniquet), with the files' release IDs hidden.
- Barcode on the scans over the folder's edition year (BTBAM - The Great Misdirect).
Run:  python3 tests/test_m4c_fixes.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN_DIR = _os.environ.get("TAGMENDER_DISCOGS_PLUGIN", "../picard-discogs-enrichment")
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    PLUGIN_DIR = sys.argv.pop(1)      # the plugin folder (discogs_candidate reads a release with it)

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import reasons  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])


def album(lens, **kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(lens, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "formats": {"cd"}, "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}
    c.update(kw)
    return c


def run(a, *cands, cfg=CFG):
    scored = [mc.score_candidate(a, c, cfg) for c in cands]
    r = mc.classify(scored, cfg)
    r["scored"] = scored
    return r


class OtherAlbumsDownloads(unittest.TestCase):
    """Sunn O))) - Veils It White: one download on each side, plus the artist's live sets"""

    def results(self, live_title="Pigs On Ice"):
        a = album((1268.0,), artist="Sunn O)))", title="Veils It White", year=2003)
        common = dict(artist="Sunn O)))", title="Veils It White", formats={"digital"})
        mb = run(a, cand((1268.0,), id="9f4e7468", year=2013, country="XW", label="[no label]", group_digital=1,
                         **common),
                 cand((1268.12,), id="144a3636", year=None, country="XW", label="[no label]",
                      **dict(common, title=live_title)),
                 cand((1500.0,), id="vinyl", year=2003, country="US", label="Thin the Herd", catnos={"tth12"},
                      **dict(common, formats={"vinyl"})))
        dg = run(a, cand((1268.88,), source="discogs", id="11703407", year=2003, country="US",
                         label="Not On Label (Sunn O))) Self-released)", master_digital=1, **common))
        return mb, dg

    def test_another_albums_download_is_not_a_second_download(self):
        mb, dg = self.results()
        self.assertEqual(mc._only_digital(mb["scored"])["cand"]["id"], "9f4e7468")
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dg)], ["9f4e7468"])
        for countries in (None, ["CA", "US", "XW"]):
            out = mc.combine(dict(mb, status="auto", why=[]), dict(dg, status="auto", why=[]), countries)
            self.assertEqual((out["status"], out["best"]["cand"]["id"], out.get("mb_twin")),
                             ("auto", "11703407", "9f4e7468"), out["why"])
            self.assertNotIn("is another pressing", " ".join(out["best"]["notes"]))

    def test_a_second_download_of_this_album_still_counts(self):
        mb, dg = self.results(live_title="Veils It White")
        self.assertIsNone(mc._only_digital(mb["scored"]))

    def test_another_artists_download_of_the_title_is_not_this_albums(self):
        a = album((1268.0,), artist="Sunn O)))", title="Veils It White", year=2003)
        common = dict(title="Veils It White", formats={"digital"}, country="XW", label="[no label]")
        mb = run(a, cand((1268.0,), id="9f4e7468", artist="Sunn O)))", **common),
                 cand((1268.5,), id="cover", artist="Zzyzx Quartet", **common))
        self.assertEqual(mc._only_digital(mb["scored"], mb["best"])["cand"]["id"], "9f4e7468")

    def test_the_thresholds_classify_ran_with_are_used(self):
        mb, dg = self.results()
        self.assertEqual(mb["cfg"]["auto_min_title"], CFG["auto_min_title"])
        # thresholds a profile lowered: the live set "Pigs On Ice" then passes for this album's
        self.assertIsNone(mc._only_digital(mb["scored"], mb["best"], dict(mb["cfg"], auto_min_title=0.0)))
        self.assertEqual(mc.mb_twins(dict(mb, cfg=dict(mb["cfg"], auto_min_title=0.0)), dg), [])
        # likewise on the Discogs side: its own live set download then counts as a second one
        a = album((1268.0,), artist="Sunn O)))", title="Veils It White", year=2003)
        live = mc.score_candidate(a, cand((1300.0,), source="discogs", id="live", title="Pigs On Ice",
                                          artist="Sunn O)))", year=2010, formats={"digital"}), CFG)
        dg2 = dict(dg, scored=dg["scored"] + [live])
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dg2)], ["9f4e7468"])
        self.assertEqual(mc.mb_twins(mb, dict(dg2, cfg=dict(dg["cfg"], auto_min_title=0.0))), [])

    def test_a_download_named_like_the_pick_counts(self):
        # the folder's title is unlike the pick's: a second download under the pick's own
        # title, fitting worse, is still this album's
        a = album((1268.0,), artist="Sunn O)))", title="Zyx Quarrel Bounce", year=2003)
        common = dict(artist="Sunn O)))", title="Veils It White", formats={"digital"}, country="XW")
        scored = [mc.score_candidate(a, c, CFG) for c in (cand((1268.0,), id="pick", **common),
                                                          cand((1290.0,), id="again", **common))]
        self.assertLess(scored[1]["title_sim"], CFG["auto_min_title"])
        self.assertIsNone(mc._only_digital(scored, scored[0]))
        self.assertIs(mc._only_digital(scored[:1], scored[0]), scored[0])

    def test_a_download_carrying_your_fingerprints_counts(self):
        # another title, fitting worse, but your files' fingerprints are on it (Bell Witch)
        a = album((1268.0,), artist="Sunn O)))", title="Veils It White", year=2003)
        common = dict(artist="Sunn O)))", formats={"digital"}, country="XW")
        scored = [mc.score_candidate(a, c, CFG) for c in (cand((1268.0,), id="pick", title="Veils It White", **common),
                                                          cand((1290.0,), id="fp", title="Pigs On Ice", **common))]
        self.assertIs(mc._only_digital(scored, scored[0]), scored[0])
        scored[1]["clues"] = dict(scored[1].get("clues") or {}, fingerprint="audio fingerprints are on it")
        self.assertIsNone(mc._only_digital(scored, scored[0]))

    def test_the_pick_counts_whatever_its_title(self):
        # a download accepted on its barcode under another title is still this album's
        mb, dg = self.results()
        pick = dict(dg["best"], title_sim=0.2)
        self.assertIs(mc._only_digital([pick], pick), pick)
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dict(dg, best=pick, scored=[pick]))],
                         ["9f4e7468"])

    def test_a_download_under_another_title_fitting_as_well_counts(self):
        # Bell Witch: MusicBrainz's 2015 "Demo 2011" and 2011 "Bell Witch" downloads; Discogs's
        # 2011 download "Demo 2011" (dated 2012): the 2015 one is no sole download, so no twin
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2011)
        common = dict(artist="Bell Witch", formats={"digital"}, country="XW", label="[no label]")
        mb = run(a, cand([x - 0.03 for x in BW], id="d6f7a466", title="Demo 2011", year=2015, group_digital=1,
                         **common),
                 cand(list(BW), id="3dc36a34", title="Bell Witch", year=2011, group_digital=1, **common))
        dg = run(a, cand(list(BW), source="discogs", id="dg2011", title="Demo 2011", year=2012, master_digital=1,
                         **dict(common, country="US", label="Not On Label")))
        mb_pick = dict(mb, best=next(s for s in mb["scored"] if s["cand"]["id"] == "d6f7a466"))
        self.assertIsNone(mc._only_digital(mb_pick["scored"], mb_pick["best"]))
        self.assertNotIn("d6f7a466", [s["cand"]["id"] for s in mc.mb_twins(mb_pick, dg)])
        out = mc.combine(dict(mb_pick, status="review"), dict(dg, status="auto", why=[]))
        self.assertNotEqual(out.get("mb_twin"), "d6f7a466")


BW = (641.507, 683.240, 717.893, 509.147)


class ExactLengths(unittest.TestCase):
    """Bell Witch - Demo 2011: the 2015 reissue (100 points, ~30 ms short on every track)
    against the 2011 download "Bell Witch" (74 points, exact on every track)"""

    common = dict(artist="Bell Witch", formats={"digital"})

    def releases(self, first_lens=BW, **first_kw):
        reissue = cand([x - 0.03 for x in BW], id="d6f7a466", title="Demo 2011", year=2015, **self.common)
        first = cand(list(first_lens), id="3dc36a34", title="Bell Witch", year=2011, **dict(self.common, **first_kw))
        return reissue, first

    @staticmethod
    def with_clues(a, cands, clues):
        """classify with clues (as the scans would give them) added to some candidates"""
        scored = [mc.score_candidate(a, c, CFG) for c in cands]
        for s in scored:
            s["clues"] = dict(s.get("clues") or {}, **clues.get(s["cand"]["id"], {}))
        return mc.classify(scored, CFG)

    def test_the_exact_fit_is_picked(self):
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        r = run(a, *self.releases())
        self.assertEqual((r["best"]["cand"]["id"], r["runner_up"]["cand"]["id"]), ("3dc36a34", "d6f7a466"))
        self.assertIn("pressing chosen by track lengths match to the millisecond", " ".join(r["best"]["notes"]))
        # its own gates still hold: nothing proves the album under its other title, so review
        self.assertEqual(r["status"], "review")
        self.assertIn("title similarity", " ".join(r["why"]))
        self.assertIn("pressing chosen by track lengths alone; runner-up d6f7a466 scored", " ".join(r["why"]))
        self.assertGreaterEqual(r["margin"], 0)

    def test_a_fingerprint_both_carry_proves_no_pressing(self):
        # the real album: AcoustID's fingerprints are on both, so they prove the recordings
        # and decide nothing between the two: the exact fit is the pick, a person confirms it
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015, fp_releases={"d6f7a466", "3dc36a34"})
        r = run(a, *self.releases())
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "3dc36a34"), r["why"])
        self.assertEqual(r["why"], ["pressing chosen by track lengths alone; runner-up d6f7a466 scored 100.0 "
                                    "(likely another pressing)"])
        self.assertGreaterEqual(r["margin"], 0)
        # filed as a doubt about the pressing, never tagged album-only (no tie within the margin)
        self.assertEqual(reasons.classify(r["why"][0]), ["ambiguous_release"])
        self.assertFalse(mc._TIE_WHY.match(r["why"][0]))

    def test_proof_of_the_pressing_on_the_exact_fit_needs_no_person(self):
        # your barcode on the exact fit: the pressing is proven, not chosen by lengths alone
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015, barcodes={"889326491477"})
        reissue, first = self.releases(barcodes={"889326491477"})
        r = run(a, reissue, first)
        self.assertEqual(r["best"]["cand"]["id"], "3dc36a34")
        self.assertNotIn("pressing chosen by track lengths alone", " ".join(r["why"]))
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        # (a disc ID is not counted here: these lengths are whole CD sectors, as a download's may be)
        for clue in ({"isrc": "ISRC of your files"}, {"source link": "your files' link"}):
            r = self.with_clues(a, self.releases(), {"3dc36a34": clue})
            self.assertEqual(r["best"]["cand"]["id"], "3dc36a34", clue)
            self.assertNotIn("pressing chosen by track lengths alone", " ".join(r["why"]), clue)

    def test_the_edition_your_folder_names_keeps_the_pick(self):
        # the folder names the 2015 edition; the exact fit is the 2011 one and carries your
        # tags' catalogue number: the folder decides, the pick stays (and so does the review)
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2011, pressing_year=2015, catnos={"abc1"})
        reissue, first = self.releases(catnos={"abc1"})
        r = run(a, reissue, dict(first, title="Demo 2011"))
        self.assertTrue(r["best"]["year_ok"] and r["best"]["pressing_year"] == 2015)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"), r["why"])
        self.assertNotIn("pressing chosen by track lengths", " ".join(r["best"]["notes"]))

    def test_a_contradiction_on_the_exact_fit_keeps_the_pick(self):
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        for key, value in (("conflict", True), ("clues_against", ["folder name says vinyl, not this"]),
                           ("media_why", "not this format"), ("other_version", "instrumental")):
            scored = [mc.score_candidate(a, c, CFG) for c in self.releases()]
            best, first = scored
            self.assertTrue(mc._exact_rival(best, scored, CFG))
            first[key] = value
            self.assertIsNone(mc._exact_rival(best, scored, CFG), key)
        # a code on each, of another kind (your catalogue number on the pick, a barcode on the other)
        scored = [mc.score_candidate(a, c, CFG) for c in self.releases()]
        scored[0]["code"], scored[1]["code"] = "catalogue number", "barcode"
        self.assertIsNone(mc._exact_rival(scored[0], scored, CFG))

    def test_your_copys_own_clue_on_the_pick_keeps_it(self):
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015, fp_releases={"d6f7a466"})
        r = run(a, *self.releases())
        self.assertIn("fingerprint", r["best"].get("clues") or {})
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"))
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        for clue in ({"source link": "your files' link"}, {"isrc": "ISRC of your files"},
                     {"label": "label Profound Lore Records on the scans"}, {"matrix": "matrix on the disc scans"},
                     {"country": "Made in USA on the scans"}, {"packaging": "digipak on the scans"}):
            r = self.with_clues(a, self.releases(), {"d6f7a466": clue})
            self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"), clue)
        # a clue both carry still decides nothing
        r = self.with_clues(a, self.releases(), {x: {"label": "label on the scans"} for x in ("d6f7a466", "3dc36a34")})
        self.assertEqual(r["best"]["cand"]["id"], "3dc36a34")

    def test_a_scan_pick_on_evidence_keeps_it(self):
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        scored = [mc.score_candidate(a, c, CFG) for c in self.releases()]
        best = next(s for s in scored if s["cand"]["id"] == "d6f7a466")
        self.assertTrue(mc._exact_rival(best, scored, CFG))
        for key in ("label", "date", "tagged release"):
            self.assertIsNone(mc._exact_rival(best, scored, CFG, (best, {key: "..."})), key)
        self.assertTrue(mc._exact_rival(best, scored, CFG, (best, {"country preference": "..."})))

    def test_ambiguous_or_decided_otherwise_keeps_the_pick(self):
        a = album(BW, artist="Bell Witch", title="Demo 2011", year=2015)
        reissue, first = self.releases()
        # two releases fit exactly: the lengths do not say which
        twin = cand(list(BW), id="again", title="Bell Witch", year=2011, **self.common)
        r = run(a, reissue, first, twin)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"))
        self.assertIn("fits every track to the millisecond", " ".join(r["why"]))
        # a third release a hundredth of a second off on every track: no proof either
        near = cand([x + 0.01 for x in BW], id="near", title="Bell Witch", year=2012, **self.common)
        r = run(a, reissue, first, near)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"))
        # close to the ms but not exact (4 ms): only kept off auto
        r = run(a, *self.releases(first_lens=[x + 0.004 for x in BW]))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("review", "d6f7a466"))
        # your barcode is on the pick: it decides, not the lengths
        a2 = album(BW, artist="Bell Witch", title="Demo 2011", year=2015, barcodes={"889326491477"})
        reissue2 = dict(reissue, barcodes={"889326491477"})
        r = run(a2, reissue2, first)
        self.assertEqual(r["best"]["cand"]["id"], "d6f7a466")
        # the exact one is a CD, for files that are not whole sectors: the audio backs the
        # download picked and not the CD (an _evidence clue the other lacks), so the pick stays
        a3 = album(BW, artist="Bell Witch", title="Demo 2011", year=2015,
                   audio={"hires": False, "cd_frames": False, "not_cd": True})
        r = run(a3, reissue, dict(first, formats={"cd"}))
        self.assertEqual(r["best"]["cand"]["id"], "d6f7a466")


class BlindTrackTotals(unittest.TestCase):
    """R11 in a blind run: the files' release IDs are hidden, the databases are not"""

    def results(self, total_tracks, dg_barcode="727701851327", sub_lens=None, **mb_kw):
        lens = [200.0 + 10 * i for i in range(8)]
        a = album(lens, artist="Intronaut", title="Prehistoricisms", year=2008, tracktotal=12,
                  tag_release_mb=set(), tag_release_discogs=set(), total_releases=(set(), set()))
        common = dict(artist="Intronaut", title="Prehistoricisms", year=2008, country="US", catnos={"cm7713"})
        mb = run(a, cand(lens, id="2f4c11df", barcodes={"727701851327"}, **dict(common, **mb_kw)))
        # Discogs counts the index track's sub-tracks: 12 rows, the last ones without lengths
        dg_lens = lens[:7] + [0.0] * (total_tracks - 7)
        sub_lens = sub_lens or {}
        dg_c = cand([sub_lens.get(i, x) for i, x in enumerate(dg_lens)], source="discogs", id="1467713",
                    barcodes={dg_barcode}, **common)
        for t in dg_c["tracks"][7:]:
            t["index"] = 7
        dg = run(a, dg_c)
        final = {"best": mb["scored"][0], "status": "auto", "why": []}
        return a, final, (mb, dg)

    def test_the_other_databases_count_of_the_pressing(self):
        a, final, results = self.results(12)
        self.assertEqual(ma._copy_disagrees(final, a, "", results), [])
        # a box's DVD chapters (Dark Tranquillity: 71 rows for 2 CDs of 21), lengths and all
        a, final, (mb, dg) = self.results(12)
        c = dg["scored"][0]["cand"]
        c["tracks"] = [dict(t, secs=t["secs"] or 200.0) for t in c["tracks"][:8]] + [
            {"disc": 3, "title": f"V{i}", "secs": 240.0, "video": True} for i in range(4)]
        self.assertEqual(ma._copy_disagrees(final, a, "", (mb, dg)), [])

    def test_discogs_rows_are_marked(self):
        class DM:
            _parse_duration = staticmethod(lambda d: 60.0 if d else 0.0)
        tl = [{"position": "1", "title": "a", "duration": "1:00"},
              {"type_": "index", "title": "work", "duration": "9:00",
               "sub_tracks": [{"position": "2-I", "title": "i"}, {"position": "2-II", "title": "ii"}]},
              {"position": "DVD1-1", "title": "v", "duration": "4:00"}]
        flat = ma.flatten_tracklist(tl, DM)
        self.assertEqual([t.get("index") for t in flat], [None, 1, 1, None])
        self.assertTrue(ma._VIDEO_MEDIUM.match(flat[3]["position"]))
        self.assertEqual(ma._audio_rows({"tracks": [{"secs": 60.0}, {"secs": 0, "index": 1}, {"secs": 0, "index": 1},
                                                    {"secs": 240.0, "video": True}]}), 2)

    def test_the_marks_discogs_candidate_sets_clear_the_total(self):
        dm = ma.load_plugin(PLUGIN_DIR)
        lens = [200.0 + 10 * i for i in range(8)]
        dur = lambda x: f"{int(x) // 60}:{int(x) % 60:02d}"    # noqa: E731
        songs = [{"position": str(i + 1), "title": f"T{i}", "duration": dur(x)} for i, x in enumerate(lens[:7])]
        base = {"id": 1467713, "title": "Prehistoricisms", "year": 2008, "country": "US",
                "artists": [{"name": "Intronaut"}], "labels": [{"name": "Century Media", "catno": "CM 7713"}],
                "identifiers": [{"type": "Barcode", "value": "727701851327"}], "formats": [{"name": "CD", "qty": "1"}]}
        # the index track's five movements, without lengths: 12 rows for 8 tracks
        index = dict(base, tracklist=songs + [{"type_": "index", "title": "The Reptilian Brain", "position": "8",
                                               "sub_tracks": [{"position": f"8-{n}", "title": n}
                                                              for n in ("I", "II", "III", "IV", "V")]}])
        # a box's DVD chapters: 12 rows for 8 tracks
        video = dict(base, formats=[{"name": "CD", "qty": "1"}, {"name": "DVD", "qty": "1"}],
                     tracklist=songs + [{"position": "8", "title": "T7", "duration": dur(lens[7])}]
                     + [{"position": f"DVD1-{i}", "title": f"V{i}", "duration": "4:00"} for i in range(1, 5)])
        for rel in (index, video):
            c = ma.discogs_candidate(rel, dm)
            self.assertEqual((len(c["tracks"]), ma._audio_rows(c)), (12, 8))
            a, final, (mb, _) = self.results(12)
            dg = run(a, c)
            self.assertEqual(ma._copy_disagrees(final, a, "", (mb, dg)), [], rel["formats"])
            # without the marks the rows count as tracks, and the total stands
            bare = dict(c, tracks=[{k: v for k, v in t.items() if k not in ("index", "video")} for t in c["tracks"]])
            self.assertEqual(len(ma._copy_disagrees(final, a, "", (mb, run(a, bare)))), 1)

    def test_the_total_still_counts_otherwise(self):
        why = ["the files' tags say the album has 12 tracks; this folder holds 8"]
        # another pressing nothing states apart: no barcode, catalogue number or country,
        # but another year and label
        a, final, (mb, dg) = self.results(12)
        dg["scored"][0]["cand"].update(barcodes=set(), catnos=set(), country="", year=2016, label="Other Label")
        self.assertFalse(mc._stated_apart(dg["scored"][0]["cand"], final["best"]["cand"]))
        self.assertEqual(ma._copy_disagrees(final, a, "", (mb, dg)), why)
        # another pressing's count (barcode and catalogue number differ)
        a, final, results = self.results(12, dg_barcode="111111111116", catnos={"other"})
        self.assertEqual(ma._copy_disagrees(final, a, "", results), why)
        # the pressing's entry lists another count
        a, final, results = self.results(10)
        self.assertEqual(ma._copy_disagrees(final, a, "", results), why)
        # no other entry at all
        a, final, results = self.results(12)
        self.assertEqual(ma._copy_disagrees(final, a, "", results[:1]), why)
        # only the barcode differs
        a, final, results = self.results(12, dg_barcode="111111111116")
        self.assertEqual(ma._copy_disagrees(final, a, "", results), why)
        # the other entry's extra rows have lengths: songs the pick's entry may lack
        a, final, results = self.results(12, sub_lens={i: 300.0 + 10 * i for i in range(7, 12)})
        self.assertEqual(ma._copy_disagrees(final, a, "", results), why)
        a, final, (mb, dg) = self.results(12)
        for t in dg["scored"][0]["cand"]["tracks"]:
            t.pop("index", None)                # plain rows without lengths, not sub-tracks
        self.assertEqual(ma._copy_disagrees(final, a, "", (mb, dg)), why)
        # a second entry of the same database lists 12: not the other database's count
        a, final, (mb, dg) = self.results(12)
        dup = dict(dg["scored"][0], cand=dict(dg["scored"][0]["cand"], source="mb", id="dup"))
        self.assertEqual(ma._copy_disagrees(final, a, "", (dict(mb, scored=mb["scored"] + [dup]),)), why)
        # the pick itself does not list exactly the tracks here
        a, final, results = self.results(12)
        final["best"] = dict(final["best"], dur_frac=0.9)
        self.assertEqual(ma._copy_disagrees(final, a, "", results), why)


NOT_CD = {"hires": False, "cd_frames": False, "not_cd": True}
MISDIRECT = (102.0, 357.0, 425.0, 342.0, 480.0, 2140.0)


class BarcodeOverEdition(unittest.TestCase):
    """BTBAM - The Great Misdirect: the folder Picard filed it from was "2009 - The Great
    Misdirect [2019 RMX RM]"; the scans show the Russian CD's barcode 4601250367880, the
    tracks are not whole CD sectors"""

    def results(self, barcodes=frozenset({"4601250367880"}), audio=NOT_CD):
        a = album(MISDIRECT, artist="Between The Buried And Me", title="The Great Misdirect", year=2009,
                  pressing_year=2019, folder="2009 - The Great Misdirect [2019 RMX RM]", audio=audio,
                  barcodes=set(barcodes))
        common = dict(artist="Between The Buried And Me", title="The Great Misdirect")
        mb = run(a, cand(MISDIRECT, id="9f398ec8", year=2019, country="XW", label="Craft Recordings",
                         barcodes={"888072136991"}, formats={"digital"}, **common),
                 cand(MISDIRECT, id="3ca5fab5", year=2017, country="XW", label="Craft Recordings",
                      barcodes={"886443184077"}, formats={"digital"}, **common),
                 cand(MISDIRECT, id="c175834c", year=2009, country="US", label="Victory Records",
                      barcodes={"746105053328"}, catnos={"vr533"}, **common))
        dg = run(a, cand(MISDIRECT, source="discogs", id="3190836", year=2010, country="Russia",
                         label="Soyuz Music; Victory Records", barcodes={"4601250367880"}, catnos={"vr533"},
                         **common),
                 cand(MISDIRECT, source="discogs", id="17048655", year=2013, country="US", label="Victory Records",
                      catnos={"vr533"}, formats={"vinyl"}, **common))
        return mb, dg

    def test_the_barcode_on_the_scans_keeps_its_release(self):
        mb, dg = self.results()
        self.assertEqual(dg["best"]["cand"]["id"], "3190836")
        self.assertTrue(dg["best"]["code"].startswith("barcode"))
        self.assertTrue(mc.format_contradictions(dg["best"]))         # not whole sectors: review
        self.assertTrue(mb["best"]["conflict"])                       # MusicBrainz: another barcode
        for countries in (None, ["CA", "US", "XW"]):
            out = mc.combine(mb, dg, countries)
            self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("review", "3190836"), out["why"])
            self.assertIn("lossless tracks are not whole CD sectors", " ".join(out["why"]))

    def test_a_barcode_both_picks_carry_waives_nothing(self):
        # Stygian Bough: the CD and the download share one barcode; the contradicted pick is
        # shown second however its barcode agrees
        mb, dg = self.results()
        mb2 = dict(mb, status="review", best=dict(mb["best"], code="barcode", conflict=False, clues_against=[]))
        self.assertFalse(mc.format_contradictions(mb2["best"]))
        # the contradicted CD has more of your copy behind it (its label on the scans) and more points
        dg = dict(dg, best=dict(dg["best"], clues=dict(dg["best"].get("clues") or {}, label="label on the scans")))
        self.assertTrue(mc.has_evidence(dg["best"], mb2["best"]))
        for pair in ((mb2, dg), (dg, mb2)):
            self.assertEqual(mc.combine(*pair)["best"]["cand"]["id"], "9f398ec8")

    def test_a_barcode_with_a_conflict_waives_nothing(self):
        mb, dg = self.results()
        dg2 = dict(dg, best=dict(dg["best"], conflict=True))
        self.assertNotEqual(mc.combine(mb, dg2)["best"]["cand"]["id"], "3190836")

    def test_the_barcode_waives_nothing_against_a_pick_your_copy_does_not_contradict(self):
        # the edition the folder names ("[2019 RMX RM]") carries no barcode against yours:
        # the CD your files contradict is not shown over it
        mb, dg = self.results()
        mb2 = dict(mb, best=dict(mb["best"], conflict=False))
        self.assertFalse(mc.format_contradictions(mb2["best"]))
        for pair in ((mb2, dg), (dg, mb2)):
            self.assertEqual(mc.combine(*pair)["best"]["cand"]["id"], "9f398ec8")

    def test_without_the_barcode_the_contradicted_cd_is_not_shown(self):
        # Sadist's rule stands: the CD your files contradict, with nothing of yours on it
        mb, dg = self.results(barcodes=())
        self.assertNotEqual(mc.combine(mb, dg)["best"]["cand"]["id"], "3190836")


if __name__ == "__main__":
    unittest.main(verbosity=1)
