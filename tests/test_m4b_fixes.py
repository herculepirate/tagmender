"""Offline tests for the M4b fixes of the other-pressing audit: each is modelled on an audited album, synthetic (generated
audio in temporary folders, made-up candidates); no file of the library is read.

- Track totals (R11): one album-wide total over discs numbered from 1 (Voivod - The Wake);
  a total written from the very release picked, which lists exactly the tracks here
  (Dark Tranquillity, Intronaut, Tourniquet).
- Twins: a download listed on both sides with the same lengths (Soused, ReFocus); an
  unlabelled CD whose format MusicBrainz leaves blank (Confessor - Blueprint Soul).
- Pressings: a release fitting every track to the ms keeps a pick off auto (Bell Witch -
  Demo 2011); an undated download in the folder's edition (Slayer [RM 2015]); a foreign rip
  log withholds the "not whole sectors" digital clue (Cynic - Re-Traced); whole sectors and
  their disc ID are the download's too (Voivod - Lost Machine - Live); an untitled box disc
  carrying the exact disc ID (Jesu); "Videoclip" positions and a TOC-less log proven by an
  .accurip (Pestilence); a postcode after a slash, packaging in notes/text, inlay scans and
  the review order (Sadist); exact lengths weigh 2 (BTBAM - Alaska); another version never
  blocks the tie-break (Ihsahn).
- Reporting: failed CTDB and MusicBrainz disc ID lookups (any network, HTTP or decoding
  error) are notes, never a silent "no release" nor the album's error; a CTDB note never
  turns a no match into an error; provider health for doctor; outages in Engine.plan and on
  the CLI plan path; validate.py skips gone folders, says "no audio" (not counted as
  tagged) and matches the untagged ones.
Run:  python3 tests/test_m4b_fixes.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import csv
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)          # the plugin folder: not needed here

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import tagger_settings as pc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
CD_AUDIO = {"hires": False, "cd_frames": True, "not_cd": False}
NOT_CD = {"hires": False, "cd_frames": False, "not_cd": True}
HIRES = {"hires": True, "cd_frames": False, "not_cd": False}


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


def sure(res, cid):
    """res as a sure pick of cid (combine is tested, not the tie inside one source)."""
    best = next(s for s in res["scored"] if str(s["cand"]["id"]) == cid)
    return dict(res, status="auto", why=[], best=best)


def flac(path, secs=2.0, freq=440, **meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", "44100", "-ac", "2", "-sample_fmt", "s16", *tags, str(path), "-y"], check=True)


class TrackTotals(unittest.TestCase):
    """R11: the tags' track total against the files here"""

    @staticmethod
    def infos(nums_by_disc, total):
        return [(None, {"disc_eff": d, "num": n, "tracktotal": total, "write_failed_total": 0})
                for d, nums in nums_by_disc.items() for n in nums]

    def test_one_album_wide_total_over_discs_numbered_from_one(self):
        # Voivod - The Wake: disc 1 is 1-8, disc 2 is 1-11, every file says 19 (not 38)
        self.assertEqual(ma._track_total(self.infos({1: range(1, 9), 2: range(1, 12)}, 19)), 19)
        # disc 2 missing its last track: 38 (more than the 18 here), so R11 still fires
        self.assertEqual(ma._track_total(self.infos({1: range(1, 9), 2: range(1, 11)}, 19)), 38)
        # per-disc totals as before
        self.assertEqual(ma._track_total(self.infos({1: range(1, 11), 2: range(1, 11)}, 10)), 20)
        # per-disc totals with half of each disc gone (1-5 and 1-5, each saying 10): 20, not
        # 10, so R11 still says files are missing
        self.assertEqual(ma._track_total(self.infos({1: range(1, 6), 2: range(1, 6)}, 10)), 20)
        # a gap inside a disc (1-8 and 1-12 without 6, saying 19): not read as album-wide
        self.assertEqual(ma._track_total(self.infos({1: range(1, 9), 2: [n for n in range(1, 13) if n != 6]}, 19)),
                         38)

    def intronaut(self, **kw):
        # Intronaut - Prehistoricisms: TOTALTRACKS=12 written from Discogs's index sub-tracks;
        # 8 files, tagged with MusicBrainz 2f4c11df, which lists 8 tracks
        lens = [200.0 + 10 * i for i in range(8)]
        a = album(lens, tracktotal=12, tag_release_mb={"2f4c11df"}, tag_release_discogs={"1467713"})
        a.update(kw)
        pick = mc.score_candidate(a, cand(lens, id="2f4c11df"), CFG)
        return a, pick, lens

    def test_a_total_written_from_the_pick_is_no_missing_file(self):
        a, pick, lens = self.intronaut()
        self.assertEqual(pick["dur_frac"], 1.0)
        self.assertEqual(ma._copy_disagrees({"best": pick}, a, "", ()), [])

    def test_the_total_still_counts_otherwise(self):
        a, pick, lens = self.intronaut()
        why = ["the files' tags say the album has 12 tracks; this folder holds 8"]
        # the files name another release
        b = dict(a, tag_release_mb={"other"}, tag_release_discogs=set())
        self.assertEqual(ma._copy_disagrees({"best": pick}, b, "", ()), why)
        # the release named lists 12 tracks: four are elsewhere
        twelve = mc.score_candidate(a, cand(lens + [100.0] * 4, id="2f4c11df"), CFG)
        self.assertEqual(ma._copy_disagrees({"best": twelve}, a, "", ()), why)
        # even when every file's length agrees: the track count alone decides
        self.assertEqual(ma._copy_disagrees({"best": dict(twelve, dur_frac=1.0)}, a, "", ()), why)
        # a length that does not agree
        off = mc.score_candidate(a, cand(lens[:-1] + [400.0], id="2f4c11df"), CFG)
        self.assertEqual(ma._copy_disagrees({"best": off}, a, "", ()), why)
        # no IDs at all (Rotting Christ's split deluxe album)
        self.assertEqual(ma._copy_disagrees({"best": pick}, dict(a, tag_release_mb=set(), tag_release_discogs=set()),
                                            "", ()), why)

    def test_the_discogs_pick_and_its_mb_twin(self):
        # Dark Tranquillity: 71 = DVD chapters + CDs; the tags name the pick's MusicBrainz twin
        a, _pick, lens = self.intronaut(tracktotal=71, tag_release_mb={"526d2d9f"}, tag_release_discogs=set())
        dg = mc.score_candidate(a, cand(lens, source="discogs", id="1998130"), CFG)
        self.assertEqual(ma._copy_disagrees({"best": dg, "mb_twin": "526d2d9f"}, a, "", ()), [])
        self.assertTrue(ma._copy_disagrees({"best": dg, "mb_twin": "else"}, a, "", ()))

    def test_a_blind_read_never_keeps_the_ids_for_the_total(self):
        d = Path(_tempfile.mkdtemp(prefix="tm-total-")) / "Intronaut" / "2010 - Prehistoricisms"
        try:
            for n in (1, 2):
                flac(d / f"{n:02d}.flac", secs=1 + n * 0.4, freq=300 + 100 * n, title=f"T{n}",
                     tracknumber=str(n), TOTALTRACKS="5", MUSICBRAINZ_ALBUMID=MBID, album="Prehistoricisms")
            a = ma.read_album(d, "Intronaut", d.name, blind=True)
            self.assertEqual(a["tag_release_mb"], set())                 # hidden from the matcher
            # and from the total's waiver: they are the validation's truth
            self.assertEqual(a["total_releases"], (set(), set()))
            self.assertEqual(a["tracktotal"], 5)
            a = ma.read_album(d, "Intronaut", d.name, blind=False)
            self.assertEqual(a["total_releases"][0], {MBID})
        finally:
            shutil.rmtree(d.parent.parent, ignore_errors=True)


MBID = "2f4c11df-0000-4000-8000-000000000001"
SOUSED_24 = (522.4, 719.3, 561.2, 548.1, 562.6)
SOUSED_16 = (525.4, 722.3, 563.9, 549.9, 564.8)


class LengthTwins(unittest.TestCase):
    """The pick's own twin is no rival"""

    def soused(self, mb_extra=(), dg_extra=()):
        common = dict(artist="Scott Walker & Sunn O)))", title="Soused", year=2014, label="4AD",
                      formats={"digital"})
        a = album(SOUSED_24, artist="Scott Walker & Sunn O)))", title="Soused", year=2014, audio=HIRES)
        mb = run(a, cand(SOUSED_24, id="059a2bb2", barcodes={"652637342840"}, **common),
                 cand(SOUSED_16, id="0306931c", barcodes={"652637342833"}, **common), *mb_extra)
        dg = run(a, cand(SOUSED_24, source="discogs", id="17682040", **common),
                 cand(SOUSED_16, source="discogs", id="7339070", **common), *dg_extra)
        return a, sure(mb, "059a2bb2"), sure(dg, "17682040")

    def test_one_download_listed_twice(self):
        # Soused: no link, no shared barcode, no country: the same 24/96 lengths on both sides
        _a, mb, dg = self.soused()
        self.assertEqual([s["cand"]["id"] for s in mc.mb_twins(mb, dg)], ["059a2bb2"])
        for countries in (None, ["US"]):
            out = mc.combine(mb, dg, countries)
            self.assertEqual((out["status"], out["best"]["cand"]["id"], out.get("mb_twin")),
                             ("auto", "17682040", "059a2bb2"), out["why"])

    def test_a_second_entry_with_those_lengths_cancels_it(self):
        common = dict(artist="Scott Walker & Sunn O)))", title="Soused", year=2014, label="4AD",
                      formats={"digital"})
        _a, mb, dg = self.soused(mb_extra=(cand(SOUSED_24, id="again", **common),))
        self.assertEqual(mc.mb_twins(mb, dg), [])
        # or a MusicBrainz download of that year that lists no lengths
        _a, mb, dg = self.soused(mb_extra=(cand((0, 0, 0, 0, 0), id="mbnolen", **common),))
        self.assertEqual(mc.mb_twins(mb, dg), [])
        # or a Discogs one
        _a, mb, dg = self.soused(dg_extra=(cand((0, 0, 0, 0, 0), source="discogs", id="nolen", **common),))
        self.assertEqual(mc.mb_twins(mb, dg), [])
        out = mc.combine(mb, dg, ["US"])
        self.assertEqual(out["status"], "review")

    def test_cds_and_stated_differences_are_never_length_twins(self):
        # two CDs of one year with one disc ID's lengths: a label apart is another product
        lens = (300.0, 400.0, 500.0)
        a = album(lens, audio=CD_AUDIO)
        mb = sure(run(a, cand(lens, id="osmose", country="FR", label="Osmose")), "osmose")
        dg = sure(run(a, cand(lens, source="discogs", id="2952", country="US", label="20 Buck Spin")), "2952")
        self.assertEqual(mc.mb_twins(mb, dg), [])
        # two CDs of one year and the same lengths, stating no country, number or barcode:
        # only the labels differ, and that is enough for CDs (the downloads-only guard)
        mb = sure(run(a, cand(lens, id="cd1", label="Osmose")), "cd1")
        dg = sure(run(a, cand(lens, source="discogs", id="cd2", label="20 Buck Spin")), "cd2")
        self.assertEqual(mc.mb_twins(mb, dg), [])
        # a download stating another country or barcode
        mb = sure(run(a, cand(lens, id="m", country="JP", formats={"digital"})), "m")
        dg = sure(run(a, cand(lens, source="discogs", id="d", country="US", formats={"digital"})), "d")
        self.assertEqual(mc.mb_twins(mb, dg), [])

    def test_a_download_of_other_lengths_does_not_block_it(self):
        # Cynic - ReFocus: Season of Mist (XW, barcode) vs Masvidalien Records (Worldwide);
        # MusicBrainz's 2024 download is another cut (scores far off); Discogs's LP/cassette
        lens = (318.0, 212.0, 205.0, 276.0, 334.0, 204.0, 278.0, 322.0)
        common = dict(artist="Cynic", title="ReFocus", year=2023)
        a = album(lens, artist="Cynic", title="ReFocus", year=2023, audio=NOT_CD)
        mb = run(a, cand(lens, id="53179f61", country="XW", label="Season of Mist", barcodes={"197187720309"},
                         formats={"digital"}, group_digital=2, **common),
                 cand([65.0, 134.0, 187.0, 90.0, 80.0, 70.0, 60.0, 50.0], id="bb0fb605", label="showalter records",
                      formats={"digital"}, **dict(common, year=2024)),
                 cand(lens, id="7b99479f", formats={"cd"}, **common))
        dg = run(a, cand(lens, source="discogs", id="30658795", country="Worldwide", label="Masvidalien Records",
                         formats={"digital"}, master_digital=1, **common),
                 cand([x + 3 for x in lens], source="discogs", id="lp", formats={"vinyl"}, **common),
                 cand([x + 3 for x in lens], source="discogs", id="tape", formats={"cassette"}, **common))
        out = mc.combine(sure(mb, "53179f61"), sure(dg, "30658795"), ["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"], out.get("mb_twin")),
                         ("auto", "30658795", "53179f61"), out["why"])


class UnlabelledTwin(unittest.TestCase):
    """Confessor - Blueprint Soul (EP): MusicBrainz's self-released CD without a format"""

    LENS = (355.5, 338.1, 329.5)

    def pair(self, mb_lens=LENS, mb_formats=frozenset()):
        common = dict(artist="Confessor", title="Blueprint Soul", year=2004, country="US")
        mb = cand(mb_lens, id="74b53a22", label="", formats=set(mb_formats), **common)
        dg = cand((355.0, 338.0, 330.0), source="discogs", id="1381372",
                  label="Not On Label (Confessor Self-released)", formats={"cd"}, **common)
        return mb, dg

    def test_a_blank_format_with_every_length_agreeing(self):
        self.assertTrue(mc.same_pressing(*self.pair()))
        self.assertFalse(mc.same_pressing(*self.pair(mb_lens=(355.5, 338.1, 335.0))))   # 5 s off
        self.assertFalse(mc.same_pressing(*self.pair(mb_formats={"digital"})))           # stated, differs

    def test_the_disc_id_lands_on_the_twin(self):
        mb_c, dg_c = self.pair()
        a = album(self.LENS, artist="Confessor", title="Blueprint Soul", year=2004, audio=CD_AUDIO,
                  toc_releases={"74b53a22"}, exact_toc_releases={"74b53a22"}, rip_log_toc=True,
                  scans={"physical": True, "why": "scans of the back"})
        mb = sure(run(a, mb_c), "74b53a22")
        dg = sure(run(a, dg_c), "1381372")
        self.assertEqual(mb["best"]["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE)
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"], out.get("mb_twin")),
                         ("auto", "1381372", "74b53a22"), out["why"])


class Pressings(unittest.TestCase):
    def test_a_release_fitting_to_the_millisecond_keeps_the_pick_off_auto(self):
        # Bell Witch - Demo 2011: the 2015 reissue "Demo 2011" is 24-35 ms short on every
        # track and scores 100; the 2011 entry titled "Bell Witch" fits to the ms and scores 74
        lens = (641.507, 683.240, 717.893, 509.147)
        a = album(lens, artist="Bell Witch", title="Demo 2011", year=2015)
        common = dict(artist="Bell Witch", formats={"digital"})
        reissue = cand([x - 0.03 for x in lens], id="d6f7a466", title="Demo 2011", year=2015, **common)
        # 4 ms off on every track: close to the ms, not exact (M4c picks only an exact fit)
        first = cand([x + 0.004 for x in lens], id="3dc36a34", title="Bell Witch", year=2011, **common)
        r = run(a, reissue, first)
        self.assertEqual((r["status"], r["best"]["cand"]["id"], r["runner_up"]["cand"]["id"]),
                         ("review", "d6f7a466", "3dc36a34"))
        self.assertIn("3dc36a34 fits every track to the millisecond", " ".join(r["why"]))
        # a pick that fits to the ms itself is never demoted
        r = run(a, cand(lens, id="d6f7a466", title="Demo 2011", year=2015, **common),
                cand([x - 0.03 for x in lens], id="other", title="Bell Witch", year=2011, **common))
        self.assertEqual(r["status"], "auto", r["why"])

    def test_a_rival_within_a_second_only_never_demotes(self):
        # the pick is 2.5 s off on two tracks; another entry fits within 0.5 s ("close", not
        # to the millisecond): only the millisecond rule keeps a pick off auto
        lens = (641.507, 683.240, 717.893, 509.147)
        a = album(lens, artist="Bell Witch", title="Demo 2011", year=2015)
        common = dict(artist="Bell Witch", formats={"digital"})
        pick = cand([lens[0] + 2.5, lens[1] + 2.5, lens[2] + 0.2, lens[3] + 0.2], id="d6f7a466",
                    title="Demo 2011", year=2015, **common)
        rival = cand([x + 0.5 for x in lens], id="3dc36a34", title="Bell Witch", year=2011, **common)
        r = run(a, pick, rival)
        hit = mc.exact_lengths(r["scored"])
        self.assertEqual((hit[0]["cand"]["id"], hit[2]), ("3dc36a34", "close"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "d6f7a466"), r["why"])
        self.assertNotIn("to the millisecond", " ".join(r["why"]))

    def slayer(self):
        a = album((300.0, 400.0, 500.0), artist="Slayer", title="Seasons In The Abyss", year=1990, pressing_year=2015,
                  folder="1990 - Seasons In The Abyss [RM 2015]", audio=dict(HIRES, cd_frames=False))
        common = dict(artist="Slayer", title="Seasons In The Abyss")
        lp = cand((300.0, 400.0, 500.0), id="91cba3f2", year=2013, country="US", formats={"vinyl"}, **common)
        web = cand((300.0, 400.0, 500.0), id="5d04b666", year=None, country="XW", formats={"digital"}, **common)
        return a, lp, web, common

    def test_an_undated_download_in_the_folders_edition(self):
        # Slayer - Seasons In The Abyss [RM 2015], 24/192: the undated download over a 2013 LP
        a, lp, web, _common = self.slayer()
        pick = mc.choose_pressing([mc.score_candidate(a, c, CFG) for c in (lp, web)])
        self.assertEqual(pick[0]["cand"]["id"], "5d04b666")
        self.assertIn("format", pick[1])
        self.assertIn("undated", pick[1]["date"])
        # your copy's catalogue number on none of them, and only a format clue (weight 1, under
        # a disc TOC's): the undated pressing does not win the edition
        tied = [dict(mc.score_candidate(a, c, CFG), own_catno_absent=True) for c in (lp, web)]
        self.assertIsNone(mc._undated_edition([tied[0]], [tied[1]]))
        pick = mc.choose_pressing(tied)
        self.assertFalse(pick and pick[0]["cand"]["id"] == "5d04b666", pick)

    def test_an_undated_pressing_without_more_evidence_does_not_win(self):
        a, lp, web, common = self.slayer()
        a = dict(a, audio={"hires": False, "cd_frames": False, "not_cd": False})   # nothing tells them apart
        cd = cand((300.0, 400.0, 500.0), id="cd13", year=2013, country="US", formats={"cd"}, **common)
        undated_cd = dict(web, formats={"cd"})
        pick = mc.choose_pressing([mc.score_candidate(a, c, CFG) for c in (cd, undated_cd)])
        self.assertEqual(pick[0]["cand"]["id"], "cd13")
        self.assertEqual(set(pick[1]), {"date"})

    def test_south_of_heaven_exact_lengths_and_hi_res(self):
        # Slayer - South Of Heaven [RM 2015]: the undated download fits every track within 1 s,
        # the 2013 download and LP are 2 s off on two tracks (the 1988 master)
        files = (298.0, 290.5, 255.0, 241.0, 336.0)
        a = album(files, artist="Slayer", title="South Of Heaven", year=1988, pressing_year=2015,
                  folder="1988 - South Of Heaven [RM 2015]", audio=dict(HIRES, cd_frames=False))
        common = dict(artist="Slayer", title="South Of Heaven")
        old = (298.0, 292.55, 255.0, 241.0, 338.02)
        dl13 = cand(old, id="0e7434c0", year=2013, formats={"digital"}, **common)
        lp13 = cand(old, id="662e5853", year=2013, formats={"vinyl"}, **common)
        web = cand((298.4, 290.9, 255.3, 241.2, 336.5), id="bc746447", year=None, formats={"digital"}, **common)
        r = run(a, dl13, lp13, web)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "bc746447"), r["why"])

    def test_a_foreign_rip_log_withholds_the_digital_clue(self):
        # Cynic - Re-Traced: files not whole sectors, beside an EAC log that fits none of them
        lens = (300.0, 400.0, 500.0)
        dl, cd = cand(lens, id="29809369", formats={"digital"}), cand(lens, id="cd", formats={"cd"})
        a = album(lens, audio=NOT_CD)
        self.assertIn("format", mc.score_candidate(a, dl, CFG)["clues"])
        a = album(lens, audio=NOT_CD, foreign_logs=["Cynic - Re-Traced.log"])
        s = mc.score_candidate(a, dl, CFG)
        self.assertNotIn("format", s["clues"])
        # the CD is still not these files' source
        self.assertIn("lossless tracks are not whole CD sectors, so not ripped from this CD",
                      mc.score_candidate(a, cd, CFG)["clues_against"])
        self.assertNotEqual(run(a, dl, cd)["status"], "auto")

    def test_whole_sectors_to_the_millisecond(self):
        # Voivod - Lost Machine - Live: MusicBrainz truncates, so whole sectors read .xx6/.xx3
        for x in (470.600, 271.693, 287.853, 204.506, 204.507):
            self.assertTrue(mc._whole_sectors_ms(x), x)
        for x in (204.500, 271.700, 287.860):
            self.assertFalse(mc._whole_sectors_ms(x), x)
        # never rounded up: 15337 sectors are 204493.33 ms, so 204.493 is, 204.494 is not
        self.assertTrue(mc._whole_sectors_ms(204.493))
        self.assertFalse(mc._whole_sectors_ms(204.494))

    def test_the_disc_id_a_sector_aligned_download_gives_is_not_counted(self):
        lens = (470.600, 271.693, 287.853, 204.506)
        a = album(lens, artist="Voivod", title="Lost Machine - Live", year=2020, audio=CD_AUDIO,
                  toc_releases={"0de2374a"}, exact_toc_releases={"0de2374a"})
        common = dict(artist="Voivod", title="Lost Machine - Live", year=2020)
        stub = cand(lens, id="0de2374a", country="XE", **common)
        dl = cand(lens, id="72982c7a", country="XW", formats={"digital"}, **common)
        r = run(a, stub, dl)
        stub_s = next(s for s in r["scored"] if s["cand"]["id"] == "0de2374a")
        self.assertNotIn("disc TOC", stub_s["clues"])
        self.assertNotEqual(r["status"], "auto")
        # a rip log backs the CD: its disc ID decides again
        r = run(dict(a, rip_log_toc=True), stub, dl)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "0de2374a"), r["why"])

    def test_an_untitled_box_disc_with_the_exact_disc_id(self):
        # Jesu - Sun Down / Sun Rise: the disc ID is on medium 2 of the Japanese Conqueror,
        # which has no disc title
        conq = [{"disc": 1, "title": f"C{i}", "secs": 300.0 + i} for i in range(8)]
        bonus = [{"disc": 2, "title": "Sun Down", "secs": 662.21}, {"disc": 2, "title": "Sun Rise", "secs": 718.47}]
        lens = (662.2, 718.5)
        c = cand([], id="d2876a54", artist="Jesu", title="Conqueror", year=2007, country="JP", label="Daymare",
                 disc_count=2, tracks=conq + bonus)
        a = album(lens, artist="Jesu", title="Sun Down / Sun Rise", year=2007, audio=CD_AUDIO,
                  toc_releases={"d2876a54"}, exact_toc_releases={"d2876a54"})
        self.assertNotIn("box_disc", mc.score_candidate(a, c, CFG)["cand"])         # no disc said it before
        a["exact_toc_media"] = {"d2876a54": {2}}
        s = mc.score_candidate(a, c, CFG)
        self.assertEqual((s["cand"].get("box_disc"), s["cand"].get("box_proof")), (2, "disc ID"))
        self.assertEqual(s["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE)
        # the disc ID on another medium: no
        a["exact_toc_media"] = {"d2876a54": {1}}
        self.assertNotIn("box_disc", mc.score_candidate(a, c, CFG)["cand"])
        # the right medium, but one length 0.2 s off the file: no
        a["exact_toc_media"] = {"d2876a54": {2}}
        off = dict(c, tracks=conq + [dict(bonus[0], secs=662.4), bonus[1]])
        s = mc.score_candidate(a, off, CFG)
        self.assertNotIn("box_disc", s["cand"])
        self.assertNotEqual(s["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE)

    def test_the_lookup_records_the_medium(self):
        did_toc = "1 2 103550 150 49816"
        did = ma.mb_discid(did_toc)

        class Http:
            def get_json(self, url, host):
                return {"id": did, "releases": [{"id": "d2876a54", "media": [
                    {"position": 1, "discs": [{"id": "other"}]}, {"position": 2, "discs": [{"id": did}]}]}]}
        a = {"tocs": {1: did_toc}}
        ma.toc_releases(Http(), a)
        self.assertEqual(a["exact_toc_media"], {"d2876a54": {2}})

    def test_exact_lengths_outweigh_one_clue(self):
        # BTBAM - Alaska: the 2020 remix fits every track; the 2005 CDs each have a whole-sector clue
        files = (234.81, 440.61, 503.67, 300.0)
        a = album(files, artist="BTBAM", title="Alaska", year=2005, audio=CD_AUDIO)
        common = dict(artist="BTBAM", title="Alaska", year=2005)
        remix = cand((235.0, 441.0, 504.0, 300.0), id="d55a50d1", formats={"digital"}, **common)
        # (2.2 s off on two tracks: not "closest on average", the per-track test decides)
        cd05 = cand((237.0, 442.8, 503.67, 300.0), id="470507f1", country="US", **common)
        r = run(a, cd05, remix)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "d55a50d1"), r["why"])
        self.assertEqual(mc.CLUE_WEIGHTS["exact lengths"], 2)

    def test_another_version_never_blocks_the_tie_break(self):
        # Ihsahn - Ihsahn, 24/48: the orchestral download ties the album's download on its format
        lens = (300.0, 400.0, 500.0)
        a = album(lens, artist="Ihsahn", title="Ihsahn", year=2024, audio=HIRES)
        common = dict(artist="Ihsahn", year=2024)
        dl = cand(lens, id="11e4d059", title="Ihsahn", formats={"digital"}, **common)
        orch = cand(lens, id="ce97ea79", title="Ihsahn (Orchestral Version)", formats={"digital"}, **common)
        cd = cand(lens, id="5026529e", title="Ihsahn", **common)
        r = run(a, dl, orch, cd)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "11e4d059"), r["why"])

    def test_one_real_pressing_beside_another_version_still_wins_on_its_clues(self):
        # the tie is not narrowed to one: the real pressing wins on its own clues (a country
        # and a label the scans name), as before the other-version filter (it once returned
        # None, sending the album to review on "runner-up within N points")
        lens = (300.0, 400.0, 500.0)
        a = album(lens, artist="Ihsahn", title="Ihsahn", year=2024, audio=HIRES)
        common = dict(artist="Ihsahn", year=2024, formats={"digital"})
        dl = mc.score_candidate(a, cand(lens, id="11e4d059", title="Ihsahn", **common), CFG)
        dl = dict(dl, clues={"country": "your scans name Norway", "label": "your scans name Candlelight"})
        orch = mc.score_candidate(a, cand(lens, id="ce97ea79", title="Ihsahn (Orchestral Version)", **common), CFG)
        orch = dict(orch, clues={})
        self.assertTrue(orch.get("other_version"))
        self.assertFalse(dl.get("other_version"))
        pick = mc.choose_pressing([dl, orch])
        self.assertIsNotNone(pick)
        self.assertEqual(pick[0]["cand"]["id"], "11e4d059")


class PestilenceAndSadist(unittest.TestCase):
    def setUp(self):
        self.d = Path(_tempfile.mkdtemp(prefix="tm-m4b-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_videoclip_positions_are_dropped(self):
        rel = {"formats": [{"name": "CD", "descriptions": ["Album", "Enhanced"]}]}
        tracks = [{"position": str(i), "title": f"T{i}"} for i in range(1, 4)] + [
            {"position": "Videoclip", "title": "Dehydrated"}]
        self.assertEqual(len(ma.drop_video_tracks(tracks, rel)), 3)
        self.assertEqual(len(ma.drop_video_tracks(tracks, {"formats": [{"name": "CD"}]})), 4)  # not Enhanced

    ACCURIP = ("[CUETools log; Date: 01.11.2014 16:02:42; Version: 2.1.5]\n"
               "Track Peak [ CRC32  ] [W/O NULL] [  LOG   ]\n"
               " --   98,9 [C199DCC1] [90E183CD]  W/O NULL \n"
               " 01   98,9 [0115E4C5] [A9406118]           \n")
    LOG = ("Exact Audio Copy V0.99 prebeta 4 from 23. January 2008\n\nPestilence / Mind Reflections (Best Of)\n\n"
           "Range status and errors\n\nSelected range\n\n"
           "     Filename C:\\Lossrip\\Pestilence [2006 - reissue] [CD] Mind Reflections\\Mind Reflections.wav\n\n"
           "     Peak level 98.9 %\n     Copy CRC {crc}\n     CRC {crc}\n")

    def test_a_log_without_a_toc_proven_by_the_accurip(self):
        (self.d / "x.log").write_text(self.LOG.format(crc="90E183CD"))
        (self.d / "x.accurip").write_text(self.ACCURIP)
        logs = ma.rip_logs(self.d)
        self.assertEqual(logs[0]["toc"], "")
        self.assertTrue(ma.accurip_confirms(self.d, logs[0]))
        self.assertEqual(ma._log_year(self.d, logs), 2006)
        # a log with a TOC is read for codes, never for a year
        self.assertIsNone(ma._log_year(self.d, [dict(logs[0], toc="1 1 30000 150")]))
        # the matching row kept, but another row naming a log CRC that differs: rejected
        (self.d / "x.accurip").write_text(self.ACCURIP.replace("[A9406118]           ",
                                                               "[A9406118] [11111111]"))
        self.assertFalse(ma.accurip_confirms(self.d, logs[0]))
        self.assertIsNone(ma._log_year(self.d, logs))
        (self.d / "x.accurip").write_text(self.ACCURIP)
        # another CRC, or a report naming a log CRC that differs: not this log's files
        (self.d / "x.log").write_text(self.LOG.format(crc="12345678"))
        self.assertIsNone(ma._log_year(self.d, ma.rip_logs(self.d)))
        (self.d / "x.log").write_text(self.LOG.format(crc="90E183CD"))
        (self.d / "x.accurip").write_text(self.ACCURIP.replace("  W/O NULL ", " [11111111]"))
        self.assertIsNone(ma._log_year(self.d, ma.rip_logs(self.d)))

    def test_the_log_year_never_overrides_the_folder(self):
        d = self.d / "Pestilence" / "1994 - Mind Reflections"
        flac(d / "01.flac", secs=1.2, title="T1", tracknumber="1")
        (d / "x.log").write_text(self.LOG.format(crc="90E183CD"))
        (d / "x.accurip").write_text(self.ACCURIP)
        self.assertEqual(ma.read_album(d, "Pestilence", d.name)["pressing_year"], 2006)
        self.assertEqual(ma.read_album(d, "Pestilence", "1994 - Mind Reflections [2010 RM]")["pressing_year"], 2010)

    def test_a_postcode_after_a_slash_is_no_country(self):
        # Sadist - Spellbound: "ICR/N - 16153 GENOA, ITALY" is no Norwegian address
        self.assertEqual(mc.countries_from_addresses("Nosferatu ICR/N - 16153 GENOA, ITALY"), set())
        # with no country named after it, the slash alone keeps "N - 16153" from Norway
        self.assertEqual(mc.countries_from_addresses("Nosferatu ICR/N - 16153 Genoa"), set())
        self.assertEqual(mc.countries_from_addresses("N - 16153 Genoa, Italy"), set())   # another country named
        self.assertEqual(mc.countries_from_addresses("Prophecy Productions, D-54550 Daun"), {"DE"})
        self.assertEqual(mc.countries_from_addresses("D-54550 Daun, write to us"), {"DE"})
        self.assertEqual(mc.countries_from_addresses("D-54550 Daun, Germany"), {"DE"})

    def test_packaging_from_the_entry_and_the_scans(self):
        jewel = {"formats": [{"name": "CD", "descriptions": ["Album"]}], "notes": "Packaged in jewel case."}
        digi = {"formats": [{"name": "CD", "descriptions": ["Album", "Limited Edition"], "text": "Digipak"}]}
        self.assertEqual((ma.discogs_packaging(jewel), ma.discogs_packaging(digi)), ("jewel", "digipak"))
        self.assertEqual(ma.discogs_packaging({"formats": [{"name": "CD"}]}), "")
        for n in ("Inlay.jpg", "Front.jpg"):
            (self.d / n).write_bytes(b"x")
        self.assertEqual(ma.packaging_evidence(self.d), {"jewel"})
        (self.d / "Digipak inside.jpg").write_bytes(b"x")
        self.assertEqual(ma.packaging_evidence(self.d), {"digipak"})       # another name: it decides

    def test_a_contradicted_review_loses_even_with_evidence_and_more_points(self):
        # both in review, both with evidence of their own: the one your files contradict
        # (a CD rip log against a download) is not shown, though it scored higher
        mb = {"status": "review", "why": [], "best": {
            "cand": {"source": "mb", "id": "m1"}, "confidence": 95.0, "code": "",
            "clues": {"label": "your scans name the label"},
            "clues_against": ["your folder has a CD rip log, so not this download"]}}
        dg = {"status": "review", "why": [], "best": {
            "cand": {"source": "discogs", "id": "d1"}, "confidence": 70.0, "code": "",
            "clues": {"country": "your scans name the country"}, "clues_against": []}}
        self.assertTrue(mc.format_contradictions(mb["best"]))
        self.assertTrue(mc.has_evidence(mb["best"], dg["best"]) and mc.has_evidence(dg["best"], mb["best"]))
        self.assertEqual(mc.combine(mb, dg)["best"]["cand"]["id"], "d1")

    def test_the_review_shown_is_never_the_contradicted_one(self):
        # both in review: MusicBrainz's download (your CD rip log against it) and Discogs's CD
        lens = (300.0, 400.0, 500.0)
        a = album(lens, artist="Sadist", title="Spellbound", rip_log_toc=True, audio=CD_AUDIO,
                  barcodes={"4600317008823"})
        common = dict(artist="Sadist", title="Spellbound")
        mb = run(a, cand(lens, id="a6712418", formats={"digital"}, **common))
        dg = run(a, cand(lens, source="discogs", id="12806711", barcodes={"4600317008823"}, packaging="jewel",
                         **common), cand(lens, source="discogs", id="12880462", barcodes={"4600317008823"},
                                         packaging="digipak", **common))
        mb, dg = dict(mb, status="review"), dict(dg, status="review")
        self.assertTrue(mc.format_contradictions(mb["best"]))
        self.assertEqual(mc.combine(mb, dg)["best"]["cand"]["source"], "discogs")
        self.assertEqual(mc.combine(mb, dg, ["US"])["best"]["cand"]["source"], "discogs")


class LookupFailures(unittest.TestCase):
    TOC = "1 3 69000 150 20000 40000"

    class Http:
        def __init__(self, mb=None, ctdb=None):
            self.mb, self.ctdb = mb, ctdb

        def get_json(self, url, host):
            if self.mb:
                raise self.mb
            return {"releases": []}

        def get_text(self, url, host):
            if self.ctdb:
                raise self.ctdb
            return ""

    def test_a_failed_ctdb_lookup_is_a_note_not_no_release(self):
        a = {"tocs": {1: self.TOC}}
        ma.toc_releases(self.Http(ctdb=ma.ProviderUnavailable("ctdb", "outage", "URLError: SSL mismatch")), a)
        errors = {}
        notes = ma.clue_failure_notes(a, errors)
        self.assertEqual(len(notes), 1)
        self.assertIn("CUETools DB lookup of the disc failed", notes[0])
        self.assertEqual(errors, {"ctdb": "outage"})
        # a bug is no outage: it is raised
        with self.assertRaises(KeyError):
            ma.toc_releases(self.Http(ctdb=KeyError("x")), {"tocs": {1: self.TOC}})
        self.assertIn("ctdb", ma.Http.OPTIONAL_HOSTS)

    def test_a_failed_disc_id_lookup_keeps_the_rest(self):
        a = {"tocs": {1: self.TOC}}
        ma.toc_releases(self.Http(mb=ma.ProviderUnavailable("mb", "rate_limited", "503")), a)
        self.assertEqual(len(a["discids"]), 1)                       # the disc ID is still known
        self.assertIsNone(a["discids"][0][2])                        # attached or not: unknown
        errors = {}
        self.assertIn("MusicBrainz disc ID lookup failed", ma.clue_failure_notes(a, errors)[0])
        self.assertEqual(errors, {"musicbrainz": "rate_limited"})

    def test_a_failed_lookup_offers_no_attach_link(self):
        """A disc ID whose lookup failed is no "MusicBrainz has no such disc": only a
        lookup that answered "not attached" goes to discid_submissions.csv."""
        tmp = Path(_tempfile.mkdtemp(prefix="tm-subs-"))
        try:
            db = sqlite3.connect(":memory:")
            db.execute("CREATE TABLE results (path TEXT, result TEXT)")
            for name, exact in (("failed", None), ("missing", False), ("attached", True)):
                db.execute("INSERT INTO results VALUES (?, ?)", (name, json.dumps(
                    {"path": name, "status": "pending", "discids": [(name, self.TOC, exact)]})))
            ma.export(db, tmp)
            with open(tmp / "discid_submissions.csv", encoding="utf-8") as fh:
                self.assertEqual([r["DiscID"] for r in csv.DictReader(fh)], ["missing"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refusals_and_broken_answers_are_recorded(self):
        """HTTP 403 (re-raised) and a reset connection (a ProviderUnavailable) both reach
        provider health, so outage_notes and doctor see a CTDB failing this way, and CTDB
        backs off like any optional host."""
        refusal = urllib.error.HTTPError("https://db.cue.tools/x", 403, "Forbidden", {}, None)
        for exc, raised in ((refusal, urllib.error.HTTPError),
                            (ConnectionResetError("reset by peer"), ma.ProviderUnavailable),
                            (ma._HTTPException("IncompleteRead"), ma.ProviderUnavailable)):
            db = sqlite3.connect(":memory:")
            http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
            with mock.patch("urllib.request.urlopen", side_effect=exc), mock.patch("time.sleep"), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                for i in range(3):
                    with self.assertRaises(raised):
                        http.get_text(f"https://db.cue.tools/lookup2.php?toc={i}", "ctdb")
                with self.assertRaises(ma.ProviderUnavailable):     # backed off: not asked again
                    http.get_text("https://db.cue.tools/lookup2.php?toc=9", "ctdb")
            self.assertEqual(http.run_failures["ctdb"], 3, exc)
            self.assertIn("every ctdb call failed this run (3x", ma.outage_notes(http)["ctdb"])
            row = db.execute("SELECT fails FROM provider_health WHERE host='ctdb'").fetchone()
            self.assertEqual(row[0], 3)
            self.assertTrue(http.host_down("ctdb"))
        # the toc lookup still turns each into a note
        for exc in (refusal, ConnectionResetError("reset by peer")):
            db = sqlite3.connect(":memory:")
            http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
            a = {"tocs": {1: self.TOC}}
            with mock.patch("urllib.request.urlopen", side_effect=exc), mock.patch("time.sleep"), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                ma.toc_releases(http, a)
            self.assertEqual(a["clue_failures"]["ctdb"][1], "outage")

    def test_ctdb_is_no_reason_code(self):
        import engine
        eng = engine.Engine.__new__(engine.Engine)
        out = {"status": "nomatch", "why": ["no candidates found"], "best": None}
        prop = eng._unresolved(out, [], dict(source_dir="x", files=[]), {"ctdb": "outage"})
        self.assertNotIn("ctdb_unavailable", prop.reason_codes)
        prop = eng._unresolved(out, [], dict(source_dir="x", files=[]), {"discogs": "outage"})
        self.assertIn("discogs_unavailable", prop.reason_codes)

    def test_provider_health_is_recorded_and_reported(self):
        import doctor
        path = Path(_tempfile.mkdtemp(prefix="tm-health-")) / "cache.sqlite"
        db = sqlite3.connect(path)
        http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
        err = urllib.error.URLError("hostname 'db.cue.tools' doesn't match")
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch("time.sleep"), \
                contextlib.redirect_stderr(io.StringIO()):
            for i in range(3):
                with self.assertRaises(ma.ProviderUnavailable):
                    http.get_text(f"https://db.cue.tools/lookup2.php?toc={i}", "ctdb")
        self.assertEqual(http.run_failures["ctdb"], 3)
        note = ma.outage_notes(http)["ctdb"]
        self.assertIn("every ctdb call failed this run (3x", note)
        self.assertIn("doesn't match", note)
        self.assertNotIn("toc=", note)
        row = db.execute("SELECT fails, last_error FROM provider_health WHERE host='ctdb'").fetchone()
        self.assertEqual(row[0], 3)
        checks = doctor.provider_health(path)
        self.assertEqual([(c.name, c.status) for c in checks], [("ctdb reachability", "warn")])
        self.assertIn("failed 3 times in a row", checks[0].detail)
        http._health("ctdb")
        db.close()
        self.assertEqual(ma.outage_notes(http), {})
        self.assertEqual(doctor.provider_health(path)[0].status, "ok")
        self.assertEqual(doctor.provider_health(path.parent / "none.sqlite"), [])
        shutil.rmtree(path.parent, ignore_errors=True)


class ClueFailuresNeverCostTheMatch(unittest.TestCase):
    """A disc lookup is a clue: an HTTP 403, a reset connection or an answer that is not
    UTF-8 is a note, never the album's error; a CTDB note never makes a no match an error"""
    TOC = LookupFailures.TOC

    def test_errors_http_re_raises_are_notes(self):
        resp = urllib.error.HTTPError("https://db.cue.tools/x", 403, "Forbidden", {}, None)
        for exc in (resp, ConnectionResetError("reset by peer"), ma._HTTPException("IncompleteRead"),
                    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")):
            a = {"tocs": {1: self.TOC}}
            ma.toc_releases(LookupFailures.Http(ctdb=exc), a)
            self.assertEqual(a["clue_failures"]["ctdb"][1], "outage", exc)
            self.assertIn(type(exc).__name__, a["clue_failures"]["ctdb"][0])
            a = {"tocs": {1: self.TOC}}
            ma.toc_releases(LookupFailures.Http(mb=exc), a)
            self.assertEqual(a["clue_failures"]["musicbrainz disc ID"][1], "outage", exc)
            self.assertEqual(len(a["discids"]), 1)

    class Empty:
        """Every search answers nothing; a disc lookup fails as given."""
        def __init__(self, ctdb=None, discid=None):
            self.ctdb, self.discid = ctdb, discid

        def get_json(self, url, host):
            if self.discid and "/discid/" in url:
                raise self.discid
            return {"releases": []} if host == "mb" else {"results": []}

        def get_text(self, url, host):
            if self.ctdb and host == "ctdb":
                raise self.ctdb
            return None

    def process(self, http, toc_releases=None):
        d = Path(_tempfile.mkdtemp(prefix="tm-clue-")) / "Artist Alpha" / "2010 - First Light"
        try:
            flac(d / "01.flac", secs=1.0, title="T1", tracknumber="1")
            real = ma.read_album

            def read_album(*a, **k):
                out = real(*a, **k)
                out["tocs"] = {1: self.TOC}
                return out
            row = {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            errors = {}
            args = types.SimpleNamespace(ocr=False, use_discogs=False, use_shops=False, use_gnudb=False,
                                         acoustid="never", provider_errors=errors)
            patches = [mock.patch.object(ma, "read_album", read_album)]
            if toc_releases:
                patches.append(mock.patch.object(ma, "toc_releases", toc_releases))
            with contextlib.ExitStack() as stack, contextlib.redirect_stderr(io.StringIO()):
                for pt in patches:
                    stack.enter_context(pt)
                return ma.process(row, http, CFG, None, args), errors
        finally:
            shutil.rmtree(d.parent.parent, ignore_errors=True)

    def test_a_ctdb_note_never_turns_a_no_match_into_an_error(self):
        out, errors = self.process(self.Empty(ctdb=ConnectionResetError("reset by peer")))
        self.assertEqual(out["status"], "nomatch", out["why"])
        self.assertTrue(any("CUETools DB lookup of the disc failed" in w for w in out["why"]), out["why"])
        self.assertEqual(errors, {"ctdb": "outage"})
        # a MusicBrainz disc ID failure is MusicBrainz's: the no match is retried
        out, errors = self.process(self.Empty(discid=ma.ProviderUnavailable("mb", "outage", "503")))
        self.assertEqual(out["status"], "error", out["why"])
        self.assertEqual(errors.get("musicbrainz"), "outage")

    def test_a_bug_in_the_disc_lookup_never_costs_the_album(self):
        def broken(http, album):
            raise KeyError("releases")
        out, errors = self.process(self.Empty(), toc_releases=broken)
        self.assertEqual(out["status"], "nomatch", out["why"])
        self.assertTrue(any("disc lookup failed (KeyError" in w for w in out["why"]), out["why"])
        self.assertEqual(errors, {})


class EngineAndDoctorNotes(unittest.TestCase):
    def test_engine_plan_tells_an_outage(self):
        import engine
        import plan as planmod
        eng = engine.Engine.__new__(engine.Engine)
        eng.notes, eng.profile, eng.job = {}, {}, "job"
        eng.http = types.SimpleNamespace(run_failures={"ctdb": 3}, run_ok=set(), run_errors={"ctdb": "URLError: x"})
        eng.proposals = lambda path, progress=None, cancel=None: []
        with mock.patch.object(planmod, "build", lambda props, profile, job, extra_roots=(), **kw: {"albums": []}):
            p = eng.plan(_tempfile.gettempdir())
        self.assertIn("every ctdb call failed this run (3x", p["provider_notes"]["ctdb"])
        eng.http.run_ok = {"ctdb"}
        with mock.patch.object(planmod, "build", lambda props, profile, job, extra_roots=(), **kw: {"albums": []}):
            self.assertNotIn("ctdb", eng.plan(_tempfile.gettempdir())["provider_notes"])

    def test_the_cli_plan_tells_an_outage_found_during_the_run(self):
        import engine
        import tagmender

        class FakeEngine:
            def __init__(self, prof, ocr=None):
                self.notes, self.force = {"discogs": "no token: Discogs is off"}, {}

            def plan(self, path, progress=None):
                return {"albums": [], "provider_notes": {
                    "discogs": "no token: Discogs is off",
                    "ctdb": "outage: every ctdb call failed this run (3x, last: URLError: x)"}}
        args = types.SimpleNamespace(path=_tempfile.gettempdir(), profile=None, release=None, ocr=False)
        with mock.patch.object(engine, "Engine", FakeEngine), mock.patch.object(tagmender, "_profile", lambda a: {}), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            tagmender._make_plan(args)
        text = err.getvalue()
        self.assertIn("note: ctdb: outage: every ctdb call failed", text)
        self.assertEqual(text.count("no token: Discogs is off"), 1)          # said once

    def test_doctor_checks_read_the_provider_health(self):
        import credentials
        import doctor
        state = Path(_tempfile.mkdtemp(prefix="tm-doctor-"))
        try:
            db = sqlite3.connect(state / "cache.sqlite")
            http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
            for _ in range(3):
                http._health("ctdb", "URLError: hostname mismatch")
            db.close()
            # nothing of this machine is read: no Picard.ini, no token in the environment
            home = state / "home"
            env = {k: v for k, v in _os.environ.items() if k not in credentials.ENV.values()}
            env.update(HOME=str(home), TAGMENDER_TAGGER_INI=str(state / "none.ini"))
            with mock.patch.object(doctor.ex, "STATE_DIR", state), \
                    mock.patch.dict(_os.environ, env, clear=True):
                checks = doctor.checks()
            ctdb = [c for c in checks if c.name == "ctdb reachability"]
            self.assertEqual([c.status for c in ctdb], ["warn"])
        finally:
            shutil.rmtree(state, ignore_errors=True)

    def test_a_404_is_an_answer(self):
        db = sqlite3.connect(":memory:")
        http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
        http._health("ctdb", "URLError: x")
        self.assertEqual(http.run_failures["ctdb"], 1)
        err = urllib.error.HTTPError("https://db.cue.tools/x", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch("time.sleep"):
            self.assertIsNone(http.get_text("https://db.cue.tools/lookup2.php?toc=1", "ctdb"))
        self.assertIn("ctdb", http.run_ok)
        self.assertEqual(ma.outage_notes(http), {})
        row = db.execute("SELECT fails FROM provider_health WHERE host='ctdb'").fetchone()
        self.assertEqual(row[0], 0)


class ValidateRun(unittest.TestCase):
    """validate.main(): gone folders are skipped, untagged albums are matched (not blind)"""

    def test_gone_and_untagged_folders(self):
        import validate as va
        tmp = Path(_tempfile.mkdtemp(prefix="tm-va-"))
        try:
            lib, out_dir = tmp / "lib", tmp / "out"
            tagged = lib / "Meshuggah" / "2008 - obZen [2023 CD]"
            untagged = lib / "Nile" / "2005 - Annihilation Of The Wicked"
            gone = lib / "Meshuggah" / "2008 - obZen [2023 WEB]"
            noaudio = lib / "Nile" / "2009 - Ithyphallic"
            noaudio.mkdir(parents=True)
            (noaudio / "notes.txt").write_text("no audio here")
            flac(tagged / "01.flac", secs=1.0, MUSICBRAINZ_ALBUMID="cd", title="T0")
            flac(untagged / "01.flac", secs=1.0, freq=550, title="T0")
            seen = []

            def process(row, http, cfg, dm, margs):
                seen.append((row["FullPath"], margs.blind))
                return {"status": "auto", "why": [], "best": {"source": "mb", "id": "cd", "title": "T",
                                                               "notes": ["durations 1/1 agree"]}}
            with mock.patch.multiple(ma, load_plugin=lambda d: None, plugin_files=lambda dm: [],
                                     _script_version=lambda files=(): "abcdef12", process=process,
                                     picard_preferred_countries=lambda *a, **k: [], gnudb_problem=lambda: "",
                                     picard_release_preferences=lambda *a, **k: dict(pc.MATCH_DEFAULTS),
                                     _PLUGIN_STAMP=ma._PLUGIN_STAMP), \
                    mock.patch.object(va, "album_folders", lambda lib, artist=None: iter([tagged, untagged, noaudio, gone])), \
                    mock.patch.object(va, "verdict", lambda *a, **k: ("agree", "")), \
                    mock.patch.object(va, "clue_settings", lambda: ("never", False)), \
                    mock.patch.dict(_os.environ, {"DISCOGS_TOKEN": "x", "HOME": str(tmp / "home")}), \
                    mock.patch.object(sys, "argv", ["validate.py", str(lib), "--out-dir", str(out_dir), "--cache",
                                                    "--journals", "--no-ocr"]), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                va.main()
            st = sqlite3.connect(out_dir / "state.db")
            rows = {p: json.loads(r) for p, r in st.execute("SELECT path, row FROM validation")}
            self.assertNotIn(str(gone), rows)                          # nothing recorded for it
            self.assertEqual(sorted(seen), sorted([(str(tagged), True), (str(untagged), False)]))
            r = rows[str(untagged)]
            self.assertEqual((r["verdict"], r["status"], r["picked"]), ("not tagged", "auto", "mb:cd"))
            self.assertTrue(r["files_stamp"])
            self.assertIn("gone " + str(gone), printed.getvalue())
            self.assertIn("not tagged (this run): 1 auto", printed.getvalue())
            # a folder with no audio is neither matched nor counted among the tagged albums
            self.assertEqual(rows[str(noaudio)]["verdict"], "no audio")
            self.assertNotIn(str(noaudio), [p for p, _b in seen])
            self.assertIn("1 of 1 tagged albums", printed.getvalue())
            self.assertRegex(printed.getvalue(), r"\] - no audio\s+Nile \| 2009 - Ithyphallic")
            # an empty folder read no audio: "no audio", not "not tagged"
            (tmp / "empty").mkdir()
            self.assertEqual(va.album_identity(tmp / "empty")["files"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
