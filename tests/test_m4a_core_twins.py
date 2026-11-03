"""Offline tests for the M4a core-twin rules of the other-pressing audit:

- R6: a disc ID MusicBrainz has attached to a release decides; MusicBrainz's pick with it
  overrules a Discogs pick whose twin lacks it; a one- or two-track disc counts when its
  disc ID is exact and the title matches; the twin is the release carrying the disc ID.
- R7: same_pressing(): formats that do not overlap are two releases; "[none]" is no
  catalogue number; the only download on each side may be dated differently.
- R4 (second half): combine() lets MusicBrainz's format evidence beat a Discogs pick of
  another format that has none, and sends the two to review when both have evidence.

The albums are synthetic, modelled on the audit's (no file of the library is read).
Run:  python3 tests/test_m4a_core_twins.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)          # the plugin folder: not needed here

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
CFG_US = dict(mc.DEFAULT_CFG, preferred_countries=["CA", "US"])
LENS = (1210.0, 905.0, 1090.0, 1585.0)          # The Incubus Of Karma, roughly
CD_AUDIO = {"hires": False, "cd_frames": True, "not_cd": False}


def album(lens=LENS, **kw):
    a = {"artist": "Mournful Congregation", "title": "The Incubus of Karma", "year": 2018, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(lens=LENS, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Mournful Congregation",
         "title": "The Incubus of Karma", "year": 2018, "country": "US", "label": "L", "catnos": set(),
         "barcodes": set(), "disc_count": 1, "formats": {"cd"},
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}
    c.update(kw)
    return c


def run(a, *cands, cfg=CFG):
    scored = [mc.score_candidate(a, c, cfg) for c in cands]
    r = mc.classify(scored, cfg)
    r["scored"] = scored
    return r


class FakeHttp:
    """The MusicBrainz disc ID lookup, answered from a dict {disc ID: [release ids]}."""

    def __init__(self, attached):
        self.attached = attached

    def get_json(self, url, _src):
        did = url.split("/discid/")[1].split("?")[0]
        if did in self.attached:
            return {"id": did, "releases": [{"id": r} for r in self.attached[did]]}
        return {"releases": [{"id": "fuzzy-us"}]}          # fuzzy: "a CD of about this length"


class ExactDiscId(unittest.TestCase):
    """R6"""

    def test_lookup_records_the_releases_a_disc_id_is_attached_to(self):
        toc_jp = "1 4 359364 150 90900 158775 240525"
        toc_other = "1 4 359357 150 90900 158775 240525"
        a = {"tocs": {1: toc_jp}}
        found = ma.toc_releases(FakeHttp({ma.mb_discid(toc_jp): ["jp"]}), a)
        self.assertEqual(found, {"jp"})
        self.assertEqual(a["exact_toc_releases"], {"jp"})
        a = {"tocs": {1: toc_other}}
        found = ma.toc_releases(FakeHttp({}), a)
        self.assertEqual(found, {"fuzzy-us"})
        self.assertEqual(a["exact_toc_releases"], set())

    def incubus(self):
        # the rip's disc ID (lead-out 359364) is attached to the Japanese Weird Truth CD
        # only; CTDB also links the US 20 Buck Spin CD, whose own disc ID ends at 359357
        a = album(toc_releases={"jp", "us"}, exact_toc_releases={"jp"}, rip_log_toc=True, audio=CD_AUDIO)
        jp = cand(id="jp", country="JP", label="Weird Truth Productions", catnos={"wt055"},
                  barcodes={"4562209680347"}, discogs_id="12194731")
        us = cand(id="us", country="US", label="20 Buck Spin", catnos={"spin100"}, barcodes={"793888000001"})
        return a, jp, us

    def test_exact_disc_id_tells_musicbrainz_pressings_apart(self):
        a, jp, us = self.incubus()
        r = run(a, us, jp, cfg=CFG_US)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertEqual(r["best"]["cand"]["id"], "jp")
        self.assertEqual(r["best"]["clues"]["disc TOC"], mc.EXACT_TOC_CLUE)
        us_s = next(s for s in r["scored"] if s["cand"]["id"] == "us")
        self.assertNotIn("disc TOC", us_s["clues"])          # found by CTDB only: another disc

    def test_without_an_exact_hit_fuzzy_disc_ids_still_count(self):
        a = album(toc_releases={"us"}, rip_log_toc=True, audio=CD_AUDIO)
        s = mc.score_candidate(a, cand(id="us"), CFG)
        self.assertEqual(s["clues"]["disc TOC"], mc.TOC_CLUE)

    def test_musicbrainz_exact_disc_id_overrules_a_discogs_country_pick(self):
        a, jp, us = self.incubus()
        mb = run(a, us, jp, cfg=CFG_US)
        dg_us = cand(source="discogs", id="11000001", country="US", label="20 Buck Spin", catnos={"spin100"},
                     barcodes={"793888000001"})
        dg_jp = cand(source="discogs", id="12194731", country="JP", label="Weird Truth Productions",
                     catnos={"wt055"}, barcodes={"4562209680347"})
        # Discogs: nothing tells the two apart but the country order
        dg = run(a, dg_jp, dg_us, cfg=CFG_US)
        dg = dict(dg, status="auto", why=[])              # as before R2 made a country pick review
        dg["best"] = next(s for s in dg["scored"] if s["cand"]["id"] == "11000001")
        out = mc.combine(mb, dg, ["CA", "US"])
        self.assertEqual(out["status"], "auto")
        self.assertEqual((out["best"]["cand"]["source"], out["best"]["cand"]["id"]), ("mb", "jp"))
        self.assertTrue(any("disc ID is attached" in n for n in out["best"]["notes"]))

    def test_discogs_evidence_of_its_own_against_the_disc_id_goes_to_review(self):
        a, jp, us = self.incubus()
        a = dict(a, tag_label="20 Buck Spin")          # the label in the tags: the US CD's
        mb = run(a, us, jp, cfg=CFG_US)
        self.assertEqual(mb["best"]["cand"]["id"], "jp")   # the disc ID outweighs a label
        dg_us = cand(source="discogs", id="11000001", country="US", label="20 Buck Spin", catnos={"spin100"})
        dg = run(a, dg_us)
        self.assertEqual(dg["status"], "auto", dg["why"])
        out = mc.combine(mb, dg, ["CA", "US"])
        self.assertEqual(out["status"], "review")
        self.assertIn("disc ID is attached", out["why"][0])
        self.assertIsNone(mc.album_only(out, CFG))        # a contradiction, not a tie

    def test_twin_with_the_disc_id_keeps_discogs(self):
        a, jp, us = self.incubus()
        mb = run(a, us, jp, cfg=CFG_US)
        dg = run(a, cand(source="discogs", id="12194731", country="JP", label="Weird Truth Productions",
                         catnos={"wt055"}, barcodes={"4562209680347"}))
        out = mc.combine(mb, dg)
        self.assertEqual(out["best"]["cand"]["source"], "discogs")
        self.assertEqual(out["mb_twin"], "jp")

    def test_twin_prefers_the_release_carrying_the_disc_id(self):
        # Stygian Bough: the CD and the download share a barcode; R7 drops the download, and
        # among two CDs that are both the pick's pressing the one with the disc ID is the twin
        lens = (600.0, 700.0, 800.0)
        a = album(lens, title="Stygian Bough", toc_releases={"cd-a", "cd-b"}, exact_toc_releases={"cd-a"},
                  audio=CD_AUDIO)
        common = dict(title="Stygian Bough", barcodes={"843563127414"}, catnos={"pfl244"}, country="CA")
        web = cand(lens, id="web", formats={"digital"}, **dict(common, country="XW"))
        cd_a = cand(lens, id="cd-a", **common)
        cd_b = cand(lens, id="cd-b", **common)
        mb = run(a, web, cd_b, cd_a)
        mb["scored"] = sorted(mb["scored"], key=lambda s: s["cand"]["id"] != "cd-b")
        for s in mb["scored"]:
            s["confidence"] = 100.0 if s["cand"]["id"] != "cd-a" else 99.0
        dg = run(a, cand(lens, source="discogs", id="15470132", **common))
        out = mc.combine(mb, dg)
        self.assertEqual(out["mb_twin"], "cd-a")

    def mirror_reaper(self, **kw):
        # two discs of one track each; disc 2's disc ID is attached to the PFL191 2CD
        lens = (2892.80, 2129.88)
        tracks = [{"disc": d + 1, "num": 1, "title": f"T{d}", "secs": s} for d, s in enumerate(lens)]
        a = album(lens, artist="Bell Witch", title="Mirror Reaper", discs=2, tracks=tracks,
                  toc_releases={"pfl191"}, exact_toc_releases={"pfl191"}, rip_log_toc=True, audio=CD_AUDIO)
        a.update(kw)
        c = cand(lens, id="pfl191", artist="Bell Witch", title="Mirror Reaper", disc_count=2,
                 tracks=[{"disc": d + 1, "title": f"T{d}", "secs": s} for d, s in enumerate(lens)])
        return a, c

    def test_two_track_disc_with_an_exact_disc_id(self):
        a, c = self.mirror_reaper()
        s = mc.score_candidate(a, c, CFG)
        self.assertEqual(s["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE)

    def test_short_disc_needs_the_exact_disc_id_and_the_title(self):
        a, c = self.mirror_reaper(exact_toc_releases=set())            # fuzzy only
        self.assertNotIn("disc TOC", mc.score_candidate(a, c, CFG)["clues"])
        a, c = self.mirror_reaper()
        other = dict(c, title="The Last Musician of Ur", artist="Michael Mauldin")
        self.assertNotIn("disc TOC", mc.score_candidate(a, other, CFG)["clues"])
        # a search probe that dropped the disc lookups gives nothing either
        a, c = self.mirror_reaper(toc_releases=set())
        self.assertNotIn("disc TOC", mc.score_candidate(a, c, CFG)["clues"])

    def test_short_disc_reads_the_runs_title_setting(self):
        a, c = self.mirror_reaper()
        near = dict(c, title="Mirror Reaper (Live)")
        sim = mc.title_sim("Mirror Reaper", near["title"])
        loose, strict = dict(CFG, exact_title_sim=sim - 0.01), dict(CFG, exact_title_sim=sim + 0.01)
        self.assertIn("disc TOC", mc.score_candidate(a, near, loose)["clues"])
        self.assertNotIn("disc TOC", mc.score_candidate(a, near, strict)["clues"])

    def test_note_is_added_once_when_combine_runs_again(self):
        a, jp, us = self.incubus()
        mb = run(a, us, jp, cfg=CFG_US)
        dg = run(a, cand(source="discogs", id="11000001", country="US", label="20 Buck Spin",
                         catnos={"spin100"}, barcodes={"793888000001"}), cfg=CFG_US)
        dg = dict(dg, status="auto", why=[])
        mc.combine(mb, dg, ["CA", "US"])
        out = mc.combine(mb, dg)
        self.assertEqual(sum("disc ID is attached" in n for n in out["best"]["notes"]), 1)

    def lost_machine(self):
        # Voivod - Lost Machine - Live: the files are whole sectors and so is the Century
        # Media download; no rip log, so the disc ID rebuilt from the samples may be the
        # download's. MusicBrainz's CD stub carries that disc ID.
        lens = (470.6, 300.2, 201.0)
        a = album(lens, artist="Voivod", title="Lost Machine - Live", toc_releases={"stub"},
                  exact_toc_releases={"stub"}, audio=CD_AUDIO)
        common = dict(artist="Voivod", title="Lost Machine - Live", label="Century Media", year=2020)
        stub = cand(lens, id="stub", country="XE", catnos={"19439750832"}, **common)
        mb_dl = cand(lens, id="mb-dl", country="XW", formats={"digital"}, discogs_id="16000002", **common)
        dg_dl = cand(lens, source="discogs", id="16000002", country="XW", formats={"digital"}, **common)
        return a, stub, mb_dl, dg_dl

    def test_disc_id_a_download_could_give_does_not_overrule(self):
        a, stub, mb_dl, dg_dl = self.lost_machine()
        mb = run(a, stub, mb_dl)
        mb = dict(mb, status="auto", why=[], best=next(s for s in mb["scored"] if s["cand"]["id"] == "stub"))
        # (M4b) the disc ID rebuilt from whole sectors is the download's too: not counted
        self.assertNotIn("disc TOC", mb["best"]["clues"])
        self.assertIn("disc ID they give not counted", " ".join(mb["best"]["notes"]))
        dg = dict(run(a, dg_dl), status="auto", why=[])
        out = mc.combine(mb, dg)
        self.assertNotEqual((out["best"]["cand"]["source"], out["status"]), ("mb", "auto"))
        self.assertEqual(out["best"]["cand"]["id"], "16000002")
        self.assertEqual(out.get("mb_twin"), "mb-dl")
        # a rip log backs the CD: the disc ID is the disc's, and it decides
        a = dict(a, rip_log_toc=True)
        mb = run(a, stub, mb_dl)
        mb = dict(mb, status="auto", why=[], best=next(s for s in mb["scored"] if s["cand"]["id"] == "stub"))
        self.assertEqual(mb["best"]["clues"].get("format"), mc.SECTOR_LOG_CLUE)
        out = mc.combine(mb, dict(run(a, dg_dl), status="auto", why=[]))
        self.assertEqual(out["best"]["cand"]["id"], "stub")

    ALIGNED = (1210.6, 905.2, 1090.0, 1585.4)      # whole sectors of 1/75 s, to the ms

    def incubus_aligned(self, **kw):
        # The Incubus Of Karma with a sector-aligned MusicBrainz download among the scored
        # candidates; the rip log backs the CD, whatever set the format clue first
        a = album(self.ALIGNED, toc_releases={"jp", "us"}, exact_toc_releases={"jp"}, rip_log_toc=True,
                  audio=CD_AUDIO, **kw)
        jp = cand(self.ALIGNED, id="jp", country="JP", label="Weird Truth Productions", catnos={"wt055"},
                  barcodes={"4562209680347"}, discogs_id="12194731")
        us = cand(self.ALIGNED, id="us", country="US", label="20 Buck Spin", catnos={"spin100"},
                  barcodes={"793888000001"})
        web = cand(self.ALIGNED, id="mb-dl", country="XW", label="20 Buck Spin", formats={"digital"})
        mb = run(a, us, jp, web, cfg=CFG_US)
        mb = dict(mb, status="auto", why=[], best=next(s for s in mb["scored"] if s["cand"]["id"] == "jp"))
        dg = run(a, cand(self.ALIGNED, source="discogs", id="11000001", country="US", label="20 Buck Spin",
                         catnos={"spin100"}, barcodes={"793888000001"}), cfg=CFG_US)
        return mb, dict(dg, status="auto", why=[])

    def test_rip_log_backs_the_disc_id_whatever_set_the_format_clue(self):
        for kw in ({}, {"media_hint": "cd"}, {"tag_media": "cd"}):
            mb, dg = self.incubus_aligned(**kw)
            self.assertTrue(any(mc._sector_aligned_download(s) for s in mb["scored"]))
            self.assertEqual(mb["best"]["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE, kw)
            out = mc.combine(mb, dg, ["CA", "US"])
            self.assertEqual((out["status"], out["best"]["cand"]["source"], out["best"]["cand"]["id"]),
                             ("auto", "mb", "jp"), kw)
            self.assertTrue(any("disc ID is attached" in n for n in out["best"]["notes"]), kw)

    def test_disc_id_from_the_samples_does_not_beat_a_discogs_download(self):
        # King Goat-like: whole-sector files with no rip log or scans, the CD carries the
        # disc ID they rebuild to, Discogs picks the download (lengths to the second) and
        # no MusicBrainz download was scored: the disc ID may be the download's
        lens = (400.0, 520.0, 610.0)
        a = album(lens, artist="King Goat", title="Debt of Aeons", toc_releases={"cd"},
                  exact_toc_releases={"cd"}, audio=CD_AUDIO)
        common = dict(artist="King Goat", title="Debt of Aeons", label="Aural Music", year=2016)
        mb = run(a, cand(lens, id="cd", country="IT", catnos={"aural100"}, **common))
        mb = dict(mb, status="auto", why=[])
        self.assertEqual(mb["best"]["clues"].get("disc TOC"), mc.EXACT_TOC_CLUE)
        dg = dict(run(a, cand(lens, source="discogs", id="9000001", country="UK", formats={"digital"},
                              **common)), status="auto", why=[])
        out = mc.combine(mb, dg)
        self.assertNotEqual((out["best"]["cand"]["source"], out["status"]), ("mb", "auto"))
        self.assertFalse(any("disc ID is attached" in n for n in out["best"]["notes"]))
        # a rip log backs the CD: then the disc ID decides
        a = dict(a, rip_log_toc=True)
        mb = dict(run(a, cand(lens, id="cd", country="IT", catnos={"aural100"}, **common)), status="auto", why=[])
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "cd"))

    def test_twin_of_the_pick_with_the_same_disc_id_keeps_discogs(self):
        # one disc ID attached to two MusicBrainz releases (JP and US share the TOC):
        # MusicBrainz picks JP, Discogs the US CD whose twin carries the disc ID too
        a, jp, us = self.incubus()
        a = dict(a, exact_toc_releases={"jp", "us"})
        mb = run(a, us, jp, cfg=CFG_US)
        mb = dict(mb, status="auto", why=[], best=next(s for s in mb["scored"] if s["cand"]["id"] == "jp"))
        self.assertTrue(all(mc._exact_toc(s) for s in mb["scored"]))
        dg = run(a, cand(source="discogs", id="11000001", country="US", label="20 Buck Spin",
                         catnos={"spin100"}, barcodes={"793888000001"}), cfg=CFG_US)
        dg = dict(dg, status="auto", why=[])
        out = mc.combine(mb, dg, ["CA", "US"])
        self.assertEqual(out["best"]["cand"]["source"], "discogs")
        self.assertEqual(out["mb_twin"], "us")
        self.assertFalse(any("disc ID is attached" in n for n in out["best"]["notes"]))


class SamePressing(unittest.TestCase):
    """R7"""

    def test_cd_and_download_with_one_barcode_are_two_releases(self):
        # Stygian Bough: Discogs CD 15470132, MusicBrainz digital b21b39f3 and CD 69d4ab4e
        dg = cand(source="discogs", id="15470132", country="US, Canada & Europe", barcodes={"843563127414"},
                  catnos={"pfl244"}, formats={"cd"})
        web = cand(id="b21b39f3", country="XW", barcodes={"843563127414"}, catnos={"pfl244"}, formats={"digital"})
        cd = cand(id="69d4ab4e", country="CA", barcodes={"843563127414"}, catnos={"pfl244"}, formats={"cd"})
        self.assertFalse(mc.same_pressing(web, dg))
        self.assertTrue(mc.same_pressing(cd, dg))

    def test_file_and_vinyl_sharing_label_catno_country_year(self):
        # Sunn O))) - Rehearsal Demo: Discogs File 11703497 against the MusicBrainz 12"
        common = dict(label="Ideologic Organ; Southern Lord", catnos={"soma13", "lord177"}, country="US", year=2012)
        self.assertFalse(mc.same_pressing(cand(id="2d37a31f", formats={"vinyl"}, **common),
                                          cand(source="discogs", id="11703497", formats={"digital"}, **common)))
        self.assertTrue(mc.same_pressing(cand(id="mb-file", formats={"digital"}, **common),
                                         cand(source="discogs", id="11703497", formats={"digital"}, **common)))

    def test_none_catalogue_number_is_no_number(self):
        # Sunn O))) - Candlewolff: MusicBrainz "[none]", [no label]; Discogs Not On Label
        one = ((1166.21,),)
        mb = cand(*one, id="fc5d9d91", label="[no label]", catnos={mc.norm_code("[none]")}, country="XW",
                  year=2013, formats={"digital"})
        dg = cand(*one, source="discogs", id="11703436", label="Not On Label (Sunn O))) Self-released)",
                  catnos={"none"}, country="Worldwide", year=2013, formats={"digital"})
        self.assertTrue(mc.same_pressing(mb, dg))
        self.assertFalse(mc.same_pressing(mb, dict(dg, year=2005)))          # another year: another release

    def test_sole_downloads_may_be_dated_differently(self):
        # Sunn O))) - Veils It White: MusicBrainz 2013 XW, Discogs 2003 US, each the only download
        one = ((1274.88,),)
        mb = cand(*one, id="9f4e7468", label="[no label]", catnos=set(), country="XW", year=2013,
                  formats={"digital"})
        dg = cand(*one, source="discogs", id="11703407", label="Not On Label (Sunn O))) Self-released)",
                  catnos=set(), country="US", year=2003, formats={"digital"})
        self.assertFalse(mc.same_pressing(mb, dg))
        self.assertTrue(mc.same_pressing(mb, dg, sole_digital=True))
        # the lengths must still agree, and a vinyl is never let through by it
        self.assertFalse(mc.same_pressing(mb, cand((1290.0,), **dict(dg, tracks=[{"disc": 1, "secs": 1290.0}])),
                                          sole_digital=True))
        self.assertFalse(mc.same_pressing(dict(mb, formats={"vinyl"}), dict(dg, formats={"vinyl"}),
                                          sole_digital=True))
        # a Discogs File entry with no lengths listed proves nothing
        bare = dict(dg, tracks=[{"disc": 1, "title": "T0", "secs": 0}])
        self.assertFalse(mc.same_pressing(mb, bare, sole_digital=True))
        # nor one that names no country
        self.assertFalse(mc.same_pressing(mb, dict(dg, country=""), sole_digital=True))

    def test_only_download_is_counted_from_the_full_listing(self):
        data = {"release-count": 3, "releases": [
            {"id": "a", "media": [{"format": "Digital Media"}]},
            {"id": "b", "media": [{"format": "CD"}]}, {"id": "c", "media": [{"format": '12" Vinyl'}]}]}
        fmts = lambda v: [m.get("format") for m in v.get("media") or []]
        self.assertEqual(ma._digital_count(data, "releases", "release-count", fmts)["a"], {"group_digital": 1})
        # a group of more than one page: unknown
        self.assertEqual(ma._digital_count(dict(data, **{"release-count": 140}), "releases", "release-count",
                                           fmts), {})
        versions = {"pagination": {"pages": 1, "items": 3}, "versions": [
            {"id": 11703407, "major_formats": ["File"]}, {"id": 2, "major_formats": ["File"]},
            {"id": 3, "format": "CD, Album"}]}
        dfmts = lambda v: v.get("major_formats") or [v.get("format") or ""]
        self.assertEqual(ma._digital_count(versions, "versions", None, dfmts)["11703407"], {"master_digital": 2})
        paged = dict(versions, pagination={"pages": 2, "items": 140})
        self.assertEqual(ma._digital_count(paged, "versions", None, dfmts), {})
        self.assertEqual(ma._digital_count(None, "versions", None, dfmts), {})

    def test_combine_maps_the_only_download_as_the_twin(self):
        one = (1274.88,)
        a = album(one, artist="Sunn O)))", title="Veils It White", tracks=[{"disc": 1, "num": 1, "title": "T0",
                                                                          "secs": 1274.88}],
                  audio={"hires": False, "cd_frames": False, "not_cd": True})
        common = dict(artist="Sunn O)))", title="Veils It White", catnos=set())
        mb_web = cand(one, id="9f4e7468", label="[no label]", country="XW", year=2013, formats={"digital"},
                      group_digital=1, **common)
        mb_lp = cand(one, id="lp", label="Southern Lord", country="US", year=2003, formats={"vinyl"}, **common)
        dg_web = cand(one, source="discogs", id="11703407", label="Not On Label (Sunn O))) Self-released)",
                      country="US", year=2003, formats={"digital"}, master_digital=1, **common)
        mb = run(a, mb_web, mb_lp)
        dg = run(a, dg_web)
        self.assertEqual(dg["status"], "auto", dg["why"])
        out = mc.combine(mb, dg)
        self.assertEqual(out.get("mb_twin"), "9f4e7468")
        # a second download on the MusicBrainz side: no longer sure which is the twin
        mb2 = run(a, mb_web, cand(one, id="web2", label="[no label]", country="XW", year=2015,
                                  formats={"digital"}, **common))
        self.assertNotIn("mb_twin", mc.combine(mb2, dg))
        # a second download on the Discogs side
        dg_web2 = dict(dg_web, id="11703999", year=2008)
        dg2 = run(a, dg_web, dg_web2)
        dg2 = dict(dg2, status="auto", why=[], best=next(s for s in dg2["scored"] if s["cand"]["id"] == "11703407"))
        self.assertNotIn("mb_twin", mc.combine(run(a, mb_web, mb_lp), dg2))
        # the only one among the versions fetched, but not known to be the only one of its
        # release group / master (another page, or a listing not read): no twin
        for mb_c, dg_c in ((dict(mb_web, group_digital=None), dg_web), (mb_web, dict(dg_web, master_digital=2)),
                           (dict(mb_web, group_digital=None), dict(dg_web, master_digital=None))):
            self.assertNotIn("mb_twin", mc.combine(run(a, mb_c, mb_lp), run(a, dg_c)))

    def test_only_downloads_listed_differently_are_twins(self):
        # Sunn O))) - Rehearsal Demo: MusicBrainz's Bandcamp entry ([no label], [none], XW,
        # 2013) and Discogs's File (Ideologic Organ / Southern Lord, SOMA 013 / LORD 177,
        # US, 2012), each the only download of its release group / master; the 12" shares
        # the Discogs File's label, numbers, country and year
        lens = (1500.12, 1320.5)
        a = album(lens, artist="Sunn O)))", title="Rehearsal Demo Nov 11 2011",
                  audio={"hires": False, "cd_frames": False, "not_cd": True})
        common = dict(artist="Sunn O)))", title="Rehearsal Demo Nov 11 2011")
        labelled = dict(label="Ideologic Organ; Southern Lord", catnos={"soma013", "lord177"}, country="US",
                        year=2012)
        mb_web = cand(lens, id="041598e9", label="[no label]", catnos={mc.norm_code("[none]")}, country="XW",
                      year=2013, formats={"digital"}, group_digital=1, **common)
        mb_lp = cand(lens, id="2d37a31f", formats={"vinyl"}, discogs_id="3663845", **dict(common, **labelled))
        dg_web = cand(lens, source="discogs", id="11703497", formats={"digital"}, master_digital=1,
                      **dict(common, **labelled))
        self.assertFalse(mc.same_pressing(mb_web, dg_web, sole_digital=True))
        dg = run(a, dg_web)
        self.assertEqual(dg["status"], "auto", dg["why"])
        for countries in (None, ["US"]):
            out = mc.combine(run(a, mb_web, mb_lp), dg, countries)
            self.assertEqual((out["status"], out["best"]["cand"]["source"], out["best"]["cand"]["id"]),
                             ("auto", "discogs", "11703497"), countries)
            self.assertEqual(out.get("mb_twin"), "041598e9", countries)
        # not known to be the only download, or other lengths: no twin
        for mb_c in (dict(mb_web, group_digital=None),
                     dict(mb_web, tracks=[dict(t, secs=t["secs"] + 30) for t in mb_web["tracks"]])):
            self.assertNotIn("mb_twin", mc.combine(run(a, mb_c, mb_lp), dg))


class FormatEvidence(unittest.TestCase):
    """R4, second half"""

    LENS = (300.0, 420.0, 510.0, 600.0, 380.0)

    def loum(self, **kw):
        # Dave Edwardson & Krallice - Loum: lossless tracks that are not whole CD sectors
        a = album(self.LENS, artist="Dave Edwardson & Krallice", title="Loum",
                  audio={"hires": False, "cd_frames": False, "not_cd": True}, **kw)
        common = dict(artist="Dave Edwardson & Krallice", title="Loum")
        mb_web = cand(self.LENS, id="c8e55dad", country="XW", label="[no label]", formats={"digital"}, **common)
        dg_cd = cand(self.LENS, source="discogs", id="11251884", country="US", label="Hathenter",
                     formats={"cd"}, **common)
        return a, mb_web, dg_cd

    def test_musicbrainz_download_beats_a_discogs_cd_with_nothing_behind_it(self):
        a, mb_web, dg_cd = self.loum()
        mb, dg = run(a, mb_web), run(a, dg_cd)
        dg = dict(dg, status="auto", why=[])         # what R1 now demotes, in case it is not
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "c8e55dad"))
        self.assertTrue(any("your copy shows a digital release" in n for n in out["best"]["notes"]))

    def test_twin_of_the_cd_does_not_hide_the_download(self):
        # before: a MusicBrainz twin of the Discogs CD meant "Discogs decides"
        a, mb_web, dg_cd = self.loum()
        mb_cd = dict(dg_cd, source="mb", id="mb-cd")
        mb = run(a, mb_web, mb_cd)
        mb = dict(mb, status="auto", why=[], best=next(s for s in mb["scored"] if s["cand"]["id"] == "c8e55dad"))
        dg = dict(run(a, dg_cd), status="auto", why=[])
        out = mc.combine(mb, dg)
        self.assertEqual(out["best"]["cand"]["id"], "c8e55dad")

    def test_discogs_label_evidence_against_musicbrainz_format_goes_to_review(self):
        a, mb_web, dg_cd = self.loum(tag_label="Hathenter")           # the CD's label in the tags
        mb, dg = run(a, mb_web), dict(run(a, dg_cd), status="auto", why=[])
        self.assertIn("label", dg["best"]["clues"])
        out = mc.combine(mb, dg)
        self.assertEqual(out["status"], "review")
        self.assertIn("disagree on the format", out["why"][0])

    def test_discogs_format_evidence_against_musicbrainz_goes_to_review(self):
        # MEDIA=CD in the tags: the Discogs CD has a format clue of its own, and nothing else
        a, mb_web, dg_cd = self.loum(tag_media="cd")
        mb = run(a, mb_web)
        mb = dict(mb, status="auto", why=[])
        dg = dict(run(a, dg_cd), status="auto", why=[])
        self.assertEqual(set(mc._evidence(dg["best"])) - set(mc._evidence(mb["best"])), set())
        self.assertIn("format", dg["best"]["clues"])
        out = mc.combine(mb, dg)
        self.assertEqual(out["status"], "review")
        self.assertIn("disagree on the format", out["why"][0])

    def test_no_format_evidence_on_musicbrainz_leaves_discogs_deciding(self):
        a, mb_web, dg_cd = self.loum()
        a["audio"] = {}
        mb = run(a, mb_web)
        self.assertNotIn("format", mb["best"]["clues"])
        mb = dict(mb, status="auto", why=[])
        dg = dict(run(a, dg_cd), status="auto", why=[])
        out = mc.combine(mb, dg)
        self.assertEqual(out["best"]["cand"]["source"], "discogs")
        self.assertFalse(any("your copy shows" in n for r in (mb, dg) for n in r["best"]["notes"]))

    def test_same_format_leaves_discogs_deciding(self):
        a, mb_web, dg_cd = self.loum()
        dg_web = dict(dg_cd, formats={"digital"}, label="Hathenter", country="US", catnos={"hath07"})
        mb, dg = run(a, mb_web), run(a, dg_web)
        self.assertEqual(dg["status"], "auto", dg["why"])
        self.assertEqual(mc.combine(mb, dg)["best"]["cand"]["source"], "discogs")


if __name__ == "__main__":
    unittest.main()
