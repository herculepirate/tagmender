#!/usr/bin/env python3
"""0.8.21: the "to verify" rules of docs/MATCHING_RULES.md, one TestCase class per rule ID.

Synthetic fixtures only (no network, no music library, no personal config). A test marked
expectedFailure shows a gap between the rule and the code: its comment names the gap."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_match import album, cand, CFG  # noqa: E402  (isolates XDG dirs and Picard.ini first)
import test_engine as te  # noqa: E402
from test_watch_folders import DropBase  # noqa: E402
import artwork  # noqa: E402
import companions  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagger_settings  # noqa: E402
import watcher  # noqa: E402

CODE, OTHER = "707723007025", "0602517880123"


def run(a, cands, ocr=""):
    scored = [mc.score_candidate(a, c, CFG, ocr) for c in cands]
    return dict(mc.classify(scored, CFG), scored=scored)


def tracks(*secs, disc=1, prefix="T"):
    return [{"disc": disc, "num": i + 1, "title": f"{prefix}{i}", "secs": float(s)} for i, s in enumerate(secs)]


def ctracks(*secs, disc=1, prefix="T"):
    return [{"disc": disc, "title": f"{prefix}{i}", "secs": s} for i, s in enumerate(secs)]


def flac(d, name, secs, channels=1, **meta):
    d.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                    "-ar", "8000", "-ac", str(channels), *tags, str(d / name), "-y"], check=True)
    return d / name


def dm():
    try:
        return ma.load_plugin(str(companions.discogs_dir()))
    except SystemExit:
        return None


AC = [{"name": "Band", "joinphrase": "", "artist": {"id": "a1", "name": "Band", "sort-name": "Band"}}]


def mb_rel(date="2015-03-23", first="1987-02-01", status="Official", media=None, **kw):
    r = {"id": "r1", "title": "Album", "artist-credit": AC, "date": date, "status": status,
         "release-group": {"id": "rg1", "primary-type": "Album", "first-release-date": first},
         "media": media or [{"position": 1, "format": "CD", "track-count": 1, "tracks": [
             {"id": "t1", "position": 1, "title": "One", "artist-credit": AC,
              "recording": {"id": "rc1", "title": "One", "artist-credit": AC}}]}]}
    r.update(kw)
    return r


# ---- Barcode -------------------------------------------------------------------------
class BAR5_ScansBarcodeBeatsTags(unittest.TestCase):
    """BAR-5. The tags' barcode is compared first and a different scan barcode cannot undo the
    "barcode differs" conflict (match_core.score_candidate, the a_codes/c_codes block);
    match_albums.process puts decoded scan barcodes into album["barcodes"] only when the tags
    have none."""

    def test_scan_barcode_release_is_taken_over_the_tags_one(self):
        a = album(barcodes={OTHER}, tag_barcodes={OTHER}, ocr_barcodes={CODE})
        r = run(a, [cand(id="tags", barcodes={OTHER}), cand(id="scans", barcodes={CODE})], ocr=f"EAN {CODE}")
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "scans"))


# ---- Evidence from your copy -----------------------------------------------------------
class EVI4_PoorOcrNeverLowersConfidence(unittest.TestCase):
    """EVI-4. score_candidate's "OCR corroboration" adds w_ocr to the points available
    whatever share of artist/title words was read."""

    def test_good_ocr_does_not_lower_it(self):
        base = mc.score_candidate(album(), cand(), CFG)["confidence"]
        self.assertGreaterEqual(mc.score_candidate(album(), cand(), CFG, "Artist Alpha First Light")["confidence"], base)

    def test_poor_ocr_does_not_lower_it(self):
        base = mc.score_candidate(album(), cand(), CFG)["confidence"]
        self.assertGreaterEqual(mc.score_candidate(album(), cand(), CFG, "lorem ipsum dolor")["confidence"], base)


# ---- Album known or full pressing ------------------------------------------------------
class ALB2_ExtraTrackTakesThePressingListingIt(unittest.TestCase):
    """ALB-2. score_candidate tracks_ok / classify "track count differs"."""

    def setUp(self):
        self.a = album(tracks=tracks(200, 305, 410, 99))

    def test_the_pressing_listing_every_file(self):
        r = run(self.a, [cand(id="std"), cand(id="bonus", tracks=ctracks(200, 305, 410, 99))])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "bonus"))

    def test_none_lists_them_all_a_person_decides(self):
        r = run(self.a, [cand(id="std")])
        self.assertNotEqual(r["status"], "auto")
        self.assertIn("track count differs", r["why"])


class ALB3_AlbumKnownWritesOriginalDate(te.Base):
    """ALB-3. match_core.album_only_tags keeps originaldate (not in PRESSING_FIELDS) and puts
    the original date in Date when the pressings' years differ; engine._album_only."""

    def test_album_only_tags(self):
        tied = [{"year": 2015, "formats": ["cd"], "layout": [3]}, {"year": 2018, "formats": ["cd"], "layout": [3]}]
        out = mc.album_only_tags({"album": ["A"], "date": ["2015-05-04"], "originaldate": ["1987-02-01"],
                                  "musicbrainz_albumid": ["x"]}, tied)
        self.assertEqual(out["originaldate"], ["1987-02-01"])
        self.assertEqual(out["date"], ["1987-02-01"])
        self.assertNotIn("musicbrainz_albumid", out)

    def test_through_the_engine(self):
        d = self.album("Ihsahn", "Amr", folder="2018 - Amr")
        eng = self.engine(te.FakeHttp())
        tied = [{"source": "mb", "id": "a", "label": "L", "country": "XE", "formats": ["cd"], "year": 2015},
                {"source": "mb", "id": "b", "label": "L", "country": "US", "formats": ["cd"], "year": 2018}]
        out = {"status": "review", "why": ["runner-up b within 0.0 points (likely another pressing)"],
               "best": {"source": "mb", "id": "a", "mb_id": "a", "url": "u", "confidence": 100.0, "notes": []},
               "album_only": {"tied": tied}}
        full = {"album": ["Amr"], "albumartist": ["Ihsahn"], "musicbrainz_albumid": ["a"],
                "date": ["2015-05-04"], "originaldate": ["1987-02-01"], "originalyear": ["1987"]}
        group = {"ids": {"a", "b"}, "whole": True,
                 "pressings": [{"id": x, "tracks": [{"recording": "rec-1"}]} for x in ("a", "b")]}
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(engine, "_mb_group", return_value=group), \
                mock.patch.object(engine.Engine, "_identified",
                                  lambda self, o, al, files, base, **_kw: pl.Proposal(
                                      **base, targets=[dict(full) for _ in files], release_id="a", url="u",
                                      provenance=dict.fromkeys(full, "musicbrainz"))):
            p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "album_only"))
        self.assertEqual(p.targets[0]["originaldate"], ["1987-02-01"])
        self.assertNotIn("musicbrainz_albumid", p.targets[0])


# ---- Media and audio -------------------------------------------------------------------
class MED2_HiResWithOnlyCdAndVinylIsAlbumKnown(unittest.TestCase):
    """MED-2. classify (no_media_evidence, format_contradictions) and album_only."""

    def test_album_known(self):
        a = album(audio={"hires": True, "sacd": False, "cd_frames": False, "not_cd": True})
        r = run(a, [cand(id="cd", formats={"cd"}), cand(id="lp", formats={"vinyl"}, country="DE")])
        self.assertNotEqual(r["status"], "auto")
        known = mc.album_only(r, CFG)
        self.assertIsNotNone(known)
        self.assertEqual({t["id"] for t in known["tied"]}, {"cd", "lp"})


# ---- Editions, box sets, singles -------------------------------------------------------
class BOX1_DiscsMatchingABoxSetAreTheBoxSet(unittest.TestCase):
    """BOX-1. score_candidate: tracks_ok and the disc layout (a standalone disc's track and
    disc counts differ)."""

    def test_box_not_the_standalone_disc(self):
        files = tracks(200, 230, 260) + tracks(300, 330, 360, disc=2, prefix="U")
        box_tracks = ctracks(200, 230, 260) + ctracks(300, 330, 360, disc=2, prefix="U")
        r = run(album(discs=2, tracks=files),
                [cand(id="solo", tracks=ctracks(200, 230, 260)),
                 cand(id="box", disc_count=2, tracks=box_tracks)])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "box"))


class SNG1_SingleNotTheAlbumHoldingIt(unittest.TestCase):
    """SNG-1. score_candidate tracks_ok; slot_view only with proof (_slots_why_not)."""

    def test_one_track(self):
        a = album(title="Song One", tracks=[{"disc": 1, "num": 1, "title": "Song One", "secs": 200.0}])
        lp = cand(id="album", title="Big Album",
                  tracks=[{"disc": 1, "title": "Song One", "secs": 200}] + ctracks(*range(100, 190, 10), prefix="X"))
        single = cand(id="single", title="Song One", tracks=[{"disc": 1, "title": "Song One", "secs": 200}])
        r = run(a, [lp, single])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "single"))


class BOOT1_BootlegTaggedInFull(unittest.TestCase):
    """BOOT-1. match_core.unofficial_why (taken with your barcode); mb_tags.album_tags
    writes releasestatus from the MusicBrainz status; Discogs: discogs_mapping
    extract_remaining_release_fields keeps Discogs' status as discogs_status only."""

    def test_bootleg_with_your_barcode_is_taken(self):
        r = run(album(barcodes={CODE}), [cand(id="boot", unofficial=True, barcodes={CODE})])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "boot"))

    def test_musicbrainz_bootleg_status_written(self):
        t = mb_tags.release_tags(mb_rel(status="Bootleg"))[0]
        self.assertEqual(t["releasestatus"], ["bootleg"])

    def test_discogs_unofficial_release_marked_bootleg(self):
        m = dm()
        if m is None:
            self.skipTest("Discogs plugin not found")
        det = dict(te.dg_release(901, "Band", "Album"),
                   formats=[{"name": "CD", "descriptions": ["Album", "Unofficial Release"]}])
        t = engine.discogs_tags(det, m, None, dict(tagger_settings.enrichment()))[0]
        self.assertEqual(t.get("releasestatus"), ["bootleg"])


# ---- Names and fields ------------------------------------------------------------------
class _AnyCase(te.FakeHttp):
    """Searches find a release whatever the case of the query (the files spell it lower case)."""

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/release/?" in url:
            q = te.up.unquote(url.split("query=")[1].split("&")[0]).casefold()
            return {"releases": [{"id": r["id"], "track-count": len(r["media"][0]["tracks"])}
                                 for r in self.mb.values() if r["title"].casefold() in q]}
        return super().get_json(url, host)


class NAM3_DatabaseSpelling(te.Base):
    """NAM-3. engine._identified writes mb_tags.release_tags' artist and titles."""

    def test_names_and_titles_from_the_database(self):
        d = self.lib / "band n" / "2005 - album n"
        for i in range(3):
            flac(d, f"{i + 1:02d}.flac", 10 * (i + 1), title=f"t{i}", album="album n", albumartist="band n",
                 artist="band n", track=str(i + 1))
        eng = self.engine(_AnyCase(mb=[te.mb_release("mb-n", "Band N", "Album N")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.tier, "exact", p.why)
        self.assertEqual([t["title"] for t in p.targets], [["T0"], ["T1"], ["T2"]])
        self.assertEqual(p.targets[0]["artist"], ["Band N"])
        self.assertEqual(p.targets[0]["album"], ["Album N"])


# ---- Fields and dates ------------------------------------------------------------------
class SRC3_FieldsFollowSources(te.Base):
    """SRC-3. profiles.SOURCE_MERGE (musicbrainz_preferred -> fill_gaps, discogs_preferred ->
    override) and engine.enrich; the *_only sources ask one database."""

    def merged(self, mode):
        m = dm()
        mb = [{k: v for k, v in t.items() if not k.startswith("~")}
              for t in mb_tags.release_tags(te.mb_release("r1", "Band", "Album"))]
        es = dict(tagger_settings.enrichment(), merge_mode=mode)
        al, trs, n = engine.discogs_parts(te.dg_release(901, "Band", "Album"), m, None, es)
        return engine.enrich(dict(mb[0]), al, trs[0], es, n, m)

    def test_mapping(self):
        self.assertEqual(pf.SOURCE_MERGE["musicbrainz_preferred"], "fill_gaps")
        self.assertEqual(pf.SOURCE_MERGE["discogs_preferred"], "override")

    def test_musicbrainz_preferred_adds_only_what_discogs_alone_has(self):
        t = self.merged("fill_gaps")
        self.assertEqual(t["label"], ["Label MB"])          # MusicBrainz's own
        self.assertEqual(t["genre"], ["Electronic"])        # only Discogs has it
        self.assertEqual(t["musicbrainz_albumid"], ["r1"])

    def test_discogs_preferred_keeps_what_musicbrainz_alone_has(self):
        t = self.merged("override")
        self.assertEqual(t["label"], ["Label DG"])          # Discogs's own
        self.assertEqual(t["isrc"], ["DEXX05000000"])       # only MusicBrainz has it
        self.assertEqual(t["releasetype"], ["album", "live"])

    def test_musicbrainz_only_takes_no_discogs_field(self):
        t = self.merged("mb_only")
        self.assertFalse([k for k in t if k.startswith("discogs_")])
        self.assertNotIn("genre", t)

    def test_discogs_only_profile_writes_no_musicbrainz_field(self):
        d = self.album("Band O", "Album O")
        http = te.FakeHttp(mb=[te.mb_release("mb-o", "Band O", "Album O", discogs=906)],
                           dg=[te.dg_release(906, "Band O", "Album O")])
        eng = self.engine(http, source="discogs_only", version=3)
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", p.why)
        self.assertFalse([k for k in p.targets[0] if k.startswith("musicbrainz_")])


class DAT1_DateAndOriginalDate(unittest.TestCase):
    """DAT-1. mb_tags.album_tags: date from the release, originaldate from the release group."""

    def test_remaster(self):
        t = mb_tags.release_tags(mb_rel(date="2015", first="1987"))[0]
        self.assertEqual((t["date"], t["originaldate"]), (["2015"], ["1987"]))


class DAT2_DatePrecision(unittest.TestCase):
    """DAT-2. mb_tags.album_tags copies the date as given; the Discogs plugin likewise."""

    def test_musicbrainz(self):
        for date in ("2015-03-23", "2015-03", "2015"):
            self.assertEqual(mb_tags.release_tags(mb_rel(date=date))[0]["date"], [date])

    def test_discogs(self):
        m = dm()
        if m is None:
            self.skipTest("Discogs plugin not found")
        for date in ("2015-03-23", "2015-03"):
            det = dict(te.dg_release(901, "Band", "Album"), released=date)
            self.assertEqual(engine.discogs_tags(det, m, None, dict(tagger_settings.enrichment()))[0]["date"], [date])


class CLS1_ClassicalTags(unittest.TestCase):
    """CLS-1. mb_tags._work (work), _rel_artist (composer); no code writes movement or
    movementnumber from MusicBrainz (tagio only maps them)."""

    def rel(self):
        composer = {"target-type": "artist", "type": "composer", "direction": "backward", "attributes": [],
                    "artist": {"id": "c1", "name": "Ludwig van Beethoven", "sort-name": "Beethoven, Ludwig van"}}
        parent = {"target-type": "work", "type": "parts", "direction": "backward", "ordering-key": 1,
                  "work": {"id": "w0", "title": "Symphony No. 5"}}
        work = {"id": "w1", "title": "Symphony No. 5: I. Allegro con brio", "relations": [composer, parent]}
        rec = {"id": "rc1", "title": "Allegro con brio", "artist-credit": AC,
               "relations": [{"target-type": "work", "type": "performance", "attributes": [], "work": work}]}
        return mb_rel(media=[{"position": 1, "format": "CD", "track-count": 1, "tracks": [
            {"id": "t1", "position": 1, "title": "I. Allegro con brio", "artist-credit": AC, "recording": rec}]}])

    def test_composer_work_and_title(self):
        t = mb_tags.release_tags(self.rel())[0]
        self.assertEqual(t["composer"], ["Ludwig van Beethoven"])
        self.assertEqual(t["work"], ["Symphony No. 5: I. Allegro con brio"])
        self.assertEqual(t["title"], ["I. Allegro con brio"])          # as the release has it

    def test_movement(self):
        t = mb_tags.release_tags(self.rel())[0]
        self.assertEqual(t.get("movementnumber"), ["1"])
        self.assertTrue(t.get("movement"))


class ART1_OwnCoverKeptUnlessSmall(unittest.TestCase):
    """ART-1. artwork.needs_art / small_covers; a small cover is replaced only with
    artwork.replace "larger_only", while profiles.DEFAULTS has replace "never"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def need(self, size, **art):
        from PIL import Image
        d = self.tmp / f"a{size}{len(art)}"
        f = flac(d, "01.flac", 1)
        if size:
            Image.new("RGB", (size, size), "red").save(d / "cover.jpg")
        prof = pf.from_dict({"name": "x", "version": 3, "artwork": dict({"embed_front": False}, **art)})
        return artwork.needs_art(d, [f], prof)

    def test_a_large_own_cover_is_kept(self):
        self.assertEqual(self.need(800), "")

    def test_none_is_looked_for(self):
        self.assertTrue(self.need(None))

    def test_small_with_larger_only(self):
        self.assertTrue(self.need(300, replace="larger_only"))

    def test_small_with_the_default_profile(self):
        self.assertTrue(self.need(300))


# ---- Discs in separate folders ---------------------------------------------------------
class DSC1_DiscFoldersSideBySide(te.Base):
    """DSC-1. engine.albums_in joins only disc folders INSIDE an album folder (is_disc_dir:
    names starting with CD/Disc...); siblings "Album CD1", "Album CD2" are separate groups.
    No watcher code waits for a missing disc."""

    def make(self):
        for n in (1, 2):
            d = self.lib / "Band S" / f"Album S CD{n}"
            for i in (1, 2):
                flac(d, f"{i:02d}.flac", 5 * i + n, title=f"D{n}T{i}", album="Album S", albumartist="Band S",
                     track=str(i), disc=str(n))

    def test_disc_folders_inside_the_album_join(self):
        base = self.lib / "Band S" / "Album S"
        for n in (1, 2):
            flac(base / f"CD{n}", "01.flac", 3 + n, album="Album S", disc=str(n))
        groups = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [("Album S", 2)])

    def test_sibling_disc_folders_are_one_album(self):
        self.make()
        groups = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual(len(groups), 1, [(g.folder.name, len(g.files)) for g in groups])


# ---- Your own choices ------------------------------------------------------------------
class CHG1_ChosenReleaseStands(unittest.TestCase):
    """CHG-1. The release your files carry (written by TagMender: tag_release_mb, not
    tags_by_tagger) only breaks ties: match_core.choose_pressing, _tagged_one."""

    def test_kept_among_tied_pressings(self):
        r = run(album(tag_release_mb={"chosen"}), [cand(id="chosen", year=1999), cand(id="other")])
        self.assertEqual(r["best"]["cand"]["id"], "chosen")

    def test_your_catalogue_number_points_to_another(self):
        r = run(album(tag_release_mb={"chosen"}, catnos={"zz123"}),
                [cand(id="chosen", year=1999), cand(id="other", catnos={"zz123"})])
        self.assertEqual(r["best"]["cand"]["id"], "other")

    def test_kept_against_a_better_score(self):
        r = run(album(tag_release_mb={"chosen"}),
                [cand(id="chosen", tracks=ctracks(200, 305, 460)), cand(id="other")])
        self.assertEqual(r["best"]["cand"]["id"], "chosen")


# ---- Working without you ---------------------------------------------------------------
class HF1_WatchedFolderAppliesOnItsOwn(DropBase):
    """HF-1. watcher.Watcher._pass: unattended and every album "ready" -> Executor.run."""

    def test_exact_is_filed(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        self.assertEqual(list(watcher.Watcher([prof], once=True).tick().values()), ["done"])
        self.assertFalse(d.exists())
        self.assertEqual(len(self.audio_in(self.dest)), 3)

    def test_album_known_is_filed(self):
        prof = self.profile()
        d = self.drop("Ihsahn", "Amr")
        tied = [{"source": "mb", "id": "a", "label": "L", "country": "XE", "formats": ["cd"], "year": 2015},
                {"source": "mb", "id": "b", "label": "L", "country": "US", "formats": ["cd"], "year": 2018}]
        out = {"status": "review", "why": ["runner-up b within 0.0 points (likely another pressing)"],
               "best": {"source": "mb", "id": "a", "mb_id": "a", "url": "u", "confidence": 100.0, "notes": []},
               "album_only": {"tied": tied}}
        full = {"album": ["Amr"], "albumartist": ["Ihsahn"], "artist": ["Ihsahn"], "musicbrainz_albumid": ["a"],
                "date": ["2015"], "originaldate": ["1987"]}
        group = {"ids": {"a", "b"}, "whole": True,
                 "pressings": [{"id": x, "tracks": [{"recording": "rec-1"}]} for x in ("a", "b")]}
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(engine, "_mb_group", return_value=group), \
                mock.patch.object(engine.Engine, "_identified",
                                  lambda self, o, al, files, base, **_kw: pl.Proposal(
                                      **base, targets=[dict(full, title=[f"T{i}"], tracknumber=[str(i + 1)])
                                                       for i, _ in enumerate(files)],
                                      release_id="a", url="u", confidence=100.0, margin=0.0,
                                      provenance=dict.fromkeys(full, "musicbrainz"))):
            watcher.Watcher([prof], once=True).tick()
        self.assertFalse(d.exists())
        self.assertEqual(len(self.audio_in(self.dest)), 3)

    def test_unresolved_waits(self):
        prof = self.profile()
        d = self.drop("Nobody", "Nothing")
        self.assertEqual(list(watcher.Watcher([prof], once=True).tick().values()), ["unresolved"])
        self.assertTrue(d.exists())
        self.assertEqual(self.audio_in(self.dest), [])


class _DiscogsDown(te.FakeHttp):
    def get_json(self, url, host):
        if host == "discogs":
            raise ma.ProviderUnavailable("discogs", "outage", "HTTP 503")
        return super().get_json(url, host)


class HF4_PreferredDatabaseDownHolds(te.Base):
    """HF-4. match_albums.process carries on with the other database ("result is
    MusicBrainz-only" / "Discogs-only"); engine._identified tags from Discogs alone when
    MusicBrainz is down (mb_down)."""

    def test_musicbrainz_preferred_and_down(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band H", "Album H")], mb_down=True),
                          source="musicbrainz_preferred")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved", (p.tier, p.provider))

    def test_discogs_preferred_and_down(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(_DiscogsDown(mb=[te.mb_release("mb-h", "Band H", "Album H")]), source="discogs_preferred")
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved", (p.tier, p.provider))


class HF5_PartialAlbumTaggedAsTheRelease(te.Base):
    """HF-5. match_core._with_missing_tracks / slot_view (each file its own track);
    engine._identified writes TAGMENDER_MISSING (merged["tagmender_missing"])."""

    def identify(self):
        d = self.lib / "Band P" / "2005 - Album P"
        for n, s in ((1, 10), (3, 30)):
            flac(d, f"{n:02d}.flac", s, title=f"T{n - 1}", album="Album P", albumartist="Band P", artist="Band P",
                 track=str(n), barcode=CODE)
        rel = dict(te.mb_release("mb-p", "Band P", "Album P"), barcode=CODE)
        eng = self.engine(te.FakeHttp(mb=[rel]))
        return eng.identify(eng.albums_in(d)[0])

    def test_numbered_from_the_release_gaps_kept(self):
        p = self.identify()
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertEqual([t["tracknumber"] for t in p.targets], [["1"], ["3"]])
        self.assertEqual([t["totaltracks"] for t in p.targets], [["3"], ["3"]])
        self.assertIn("incomplete_album", p.reason_codes)

    def test_no_tag_says_incomplete(self):
        p = self.identify()
        self.assertFalse([k for t in p.targets for k in t if "missing" in k or "incomplete" in k])


# ---- Mixed folders ---------------------------------------------------------------------
class MIX1_TwoVersionsInOneFolder(te.Base):
    """MIX-1. engine.albums_in splits a folder by album tags; one release holding both
    versions is matched as one album with disc numbers; mb_tags writes discsubtitle only
    from a medium's own title (release_tags, m["title"])."""

    def versions(self):
        d = self.lib / "Band V" / "2005 - Album V"
        for disc, ch in ((1, 2), (2, 6)):
            for i in range(3):
                flac(d, f"{disc}-{i + 1:02d}.flac", 10 * (i + 1), channels=ch, title=f"T{i}", album="Album V",
                     albumartist="Band V", track=str(i + 1), disc=str(disc))
        rel = te.mb_release("mb-v", "Band V", "Album V")
        rel["media"] = [dict(rel["media"][0], format="SACD"), {"format": "SACD", "position": 2, "tracks": [
            {"id": f"v-t{i}", "position": i + 1, "title": f"T{i}", "length": ms,
             "recording": {"id": f"v-r{i}", "title": f"T{i}"}} for i, ms in enumerate(te.LENS)]}]
        eng = self.engine(te.FakeHttp(mb=[rel]))
        groups = eng.albums_in(d)
        self.assertEqual(len(groups), 1)
        return eng.identify(groups[0])

    def test_different_albums_are_split(self):
        d = self.album("Mixed", "Album M1", folder="downloads")
        for i in range(2):
            flac(d, f"o{i}.flac", 5, album="Other Album", albumartist="Someone", title=f"O{i}")
        groups = engine.Engine.albums_in(self.engine(te.FakeHttp()), d)
        self.assertEqual(sorted(len(g.files) for g in groups), [2, 3])

    def test_one_release_holding_both_is_one_album(self):
        p = self.versions()
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)

    def test_medium_title_is_the_disc_subtitle(self):
        rel = mb_rel()
        rel["media"][0]["title"] = "Stereo Mix"
        self.assertEqual(mb_tags.release_tags(rel)[0]["discsubtitle"], ["Stereo Mix"])

    def test_disc_subtitle_always_tagged(self):
        p = self.versions()
        self.assertTrue(all(t.get("discsubtitle") for t in p.targets))


if __name__ == "__main__":
    unittest.main()
