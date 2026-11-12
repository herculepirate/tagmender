# Regression tests for the review of the Sources setting, missing tracks and the web fallback:
# an EP holding a track and its remix (no "mixed versions"), a release held back by its own
# deluxe edition, the Sources preference against evidence on both sides, which IDs of the
# other database a preferred mode removes, a gap list removed once the folder is complete,
# copies of a track in another format, a folder-name catalogue number and a shop sticker,
# two web versions fitting alike, older merge modes, Sources against the providers, a disc
# of a set kept in place, and the plan header's merge mode. Generated audio, mocked answers.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release, dg_release  # noqa: E402  (the repo's own helpers)
from test_web_partial import WebHttp, deezer_album, tone  # noqa: E402

import engine  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def titled_release(rid, artist, title, titles, secs):
    """mb_release with these track titles and lengths (seconds)."""
    r = mb_release(rid, artist, title, lens=[int(s * 1000) for s in secs])
    for t, name in zip(r["media"][0]["tracks"], titles):
        t["title"] = t["recording"]["title"] = name
    return r


class VersionsInTrackTitles(Base):
    """engine.mixed_versions: a version's mark inside the track titles is no second album version
    while a release fits the folder."""

    def folder(self, titles, secs=(10, 20, 30, 40)):
        d = self.lib / "Band E" / "2001 - Shine EP"
        for i, (t, s) in enumerate(zip(titles, secs)):
            tone(d / f"{i + 1:02d} {t}.flac", s, freq=300 + 40 * i, title=t, album="Shine EP",
                 albumartist="Band E", artist="Band E", track=str(i + 1), disc="1")
        return d

    def identify(self, d, titles, secs=(10, 20, 30, 40)):
        eng = self.engine(FakeHttp(mb=[titled_release("mb-e", "Band E", "Shine EP", titles, secs)]))
        return eng.identify(eng.albums_in(d)[0])

    def test_a_remix_ep_is_matched_as_its_release(self):
        titles = ("Shine", "Fall", "Shine (Remix)", "Fall (Remix)")
        p = self.identify(self.folder(titles), titles)
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-e"), p.reason)

    def test_a_mono_and_stereo_disc_is_matched_as_its_release(self):
        titles = ("Shine (Mono)", "Fall (Mono)", "Shine (Stereo)", "Fall (Stereo)")
        p = self.identify(self.folder(titles), titles)
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-e"), p.reason)

    def test_without_any_release_it_is_still_two_versions(self):
        titles = ("Shine", "Fall", "Shine (Remix 2024)", "Fall (Remix 2024)")
        d = self.folder(titles)
        eng = self.engine(FakeHttp())
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertIn("two versions of the album are mixed in this folder", p.reason)


class DeluxeEdition(unittest.TestCase):
    """match_core.classify: the release holding just the files' tracks is never held back by a
    longer edition scored against the files' own slots of it."""

    def test_the_full_release_wins_over_its_deluxe_edition(self):
        lens = (200.0, 305.0, 410.0, 150.0, 260.0)
        album = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
                 "tracks": [{"disc": 1, "num": n, "title": f"T{n}", "secs": lens[n - 1]} for n in (1, 2, 3)],
                 "barcodes": set(), "catnos": set(), "toc_releases": {"orig", "deluxe"},
                 "exact_toc_releases": {"orig", "deluxe"}}

        def cand(rid, n):
            return {"source": "mb", "id": rid, "url": "u", "artist": "Artist Alpha", "title": "First Light",
                    "year": 2010, "country": "CA", "label": "L", "catnos": {"cat12"}, "barcodes": {"0801837013429"},
                    "disc_count": 1, "tracks": [{"disc": 1, "title": f"T{i + 1}", "secs": s}
                                                for i, s in enumerate(lens[:n])]}
        scored = [mc.score_candidate(album, c, CFG) for c in (cand("orig", 3), cand("deluxe", 5))]
        self.assertTrue(scored[1].get("missing"))           # (alone, the deluxe fits by its slots)
        r = mc.classify(scored, CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "orig"), r["why"])
        self.assertNotIn("missing", r["best"])
        # alone, the deluxe edition with tracks 4 and 5 missing is still the match
        r = mc.classify([mc.score_candidate(album, cand("deluxe", 5), CFG)], CFG)
        self.assertEqual((r["status"], r["best"].get("missing")), ("auto", {1: [4, 5]}), r["why"])


def _result(cand, prefer, **best):
    b = {"cand": cand, "confidence": 95.0, "notes": [], "clues": {}, "code": "", **best}
    return {"status": "auto", "best": b, "runner_up": None, "margin": 20.0, "why": [], "scored": [b],
            "cfg": {"prefer_source": prefer}}


def _cand(source, rid, catno, country, **extra):
    return {"source": source, "id": rid, "title": "Album", "artist": "Band", "year": 2005, "country": country,
            "label": "L", "catnos": [catno], "barcodes": [], "formats": ["CD"], "tracks": [], **extra}


class PreferenceOnlyBreaksTies(unittest.TestCase):
    """match_core.combine with a Sources preference: evidence on both sides is still a review,
    and the release your files are tagged with is kept."""

    def test_your_disc_id_on_musicbrainz_against_discogs_evidence_is_a_review(self):
        mb = _result(_cand("mb", "mb-a", "A-1", "US"), "discogs", clues={"disc TOC": mc.EXACT_TOC_CLUE})
        dg = _result(_cand("discogs", 7, "B-2", "DE"), "discogs", code="catalogue number")
        with mock.patch.object(mc, "has_evidence", side_effect=lambda a, b: a is dg["best"] or a is mb["best"]):
            out = mc.combine(mb, dg)
        self.assertEqual(out["status"], "review")
        self.assertIn("disc ID is attached to MusicBrainz", out["why"][0])

    def test_discogs_preferred_keeps_the_release_your_files_are_tagged_with(self):
        mb = _result(_cand("mb", "mb-a", "A-1", "US"), "discogs", tagged=True)
        dg = _result(_cand("discogs", 7, "B-2", "DE"), "discogs")
        out = mc.combine(mb, dg, countries=["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "mb-a"))
        self.assertTrue(any("kept the release your files are tagged with" in n for n in out["best"]["notes"]))

    def test_musicbrainz_preferred_keeps_the_release_your_files_are_tagged_with(self):
        mb = _result(_cand("mb", "mb-a", "A-1", "US"), "musicbrainz")
        dg = _result(_cand("discogs", 7, "B-2", "DE"), "musicbrainz", tagged=True)
        out = mc.combine(mb, dg)
        self.assertEqual(out["best"]["cand"]["id"], 7)

    def test_musicbrainz_preferred_with_evidence_on_both_sides_is_a_review(self):
        mb = _result(_cand("mb", "mb-a", "A-1", "US"), "musicbrainz")
        dg = _result(_cand("discogs", 7, "B-2", "DE"), "musicbrainz")
        with mock.patch.object(mc, "has_evidence", return_value=True):
            out = mc.combine(mb, dg)
        self.assertEqual(out["status"], "review")
        self.assertIn("each with evidence about your copy", out["why"][0])


class OtherDatabaseIds(Base):
    """engine.follow_source in a preferred mode: what of the other database's tags goes."""

    def prop(self, d, written=None, sure=None, differs=lambda f, v: True):
        # (differs: whether the release the files' ID names is another pressing by evidence -
        # Engine._another_pressing in a run; here, by default, it is)
        f = d / "01.flac"
        t = {"album": ["A"], "discogs_release_id": ["901"], **(written or {})}
        p = pl.Proposal(source_dir=str(d), files=[str(f)], status="identified", targets=[t], tier="exact")
        engine.follow_source(p, "discogs_preferred", sure=sure, differs=differs)
        return p.targets[0].get("~remove") or []

    def tagged(self):
        return self.album("Band F", "Album F", n=1, MUSICBRAINZ_ALBUMID="mb-x", MUSICBRAINZ_DISCID="disc-d",
                          MUSICBRAINZ_RELEASEGROUPID="rg-1", MUSICBRAINZ_TRACKID="rec-1",
                          MUSICBRAINZ_RELEASETRACKID="trk-1")

    def test_a_sure_musicbrainz_pick_of_another_release_removes_its_release_and_track_ids(self):
        gone = self.prop(self.tagged(), sure={"mb": "mb-y", "discogs": "901"})
        self.assertEqual(sorted(gone), ["musicbrainz_albumid", "musicbrainz_trackid"])

    def test_no_evidence_of_two_pressings_removes_nothing(self):
        # (the Sources setting chooses whose release is written: an ID of the same pressing, or one
        # nothing is known of, stays)
        self.assertEqual(self.prop(self.tagged(), sure={"mb": "mb-y"}, differs=lambda f, v: False), [])
        self.assertEqual(self.prop(self.tagged(), sure={"mb": "mb-y"}, differs=None), [])

    def test_the_disc_id_release_group_and_recordings_stay(self):
        gone = self.prop(self.tagged(), sure={"mb": "mb-y"})
        for k in ("musicbrainz_discid", "musicbrainz_releasegroupid", "musicbrainz_recordingid"):
            self.assertNotIn(k, gone)

    def test_musicbrainz_not_sure_removes_nothing(self):
        self.assertEqual(self.prop(self.tagged(), sure={"discogs": "901"}), [])
        self.assertEqual(self.prop(self.tagged(), sure=None), [])

    def test_a_twin_written_keeps_the_disc_id(self):
        gone = self.prop(self.tagged(), written={"musicbrainz_albumid": ["mb-y"]}, sure={"mb": "mb-y"})
        self.assertNotIn("musicbrainz_discid", gone)

    def test_discogs_only_still_keeps_the_disc_id(self):
        d = self.tagged()
        p = pl.Proposal(source_dir=str(d), files=[str(d / "01.flac")], status="identified",
                        targets=[{"album": ["A"]}], tier="exact")
        engine.follow_source(p, "discogs_only")
        gone = p.targets[0]["~remove"]
        self.assertIn("musicbrainz_albumid", gone)
        self.assertNotIn("musicbrainz_discid", gone)

    def test_the_matcher_reports_each_database_sure_pick(self):
        d = self.album("Band G", "Album G")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-g", "Band G", "Album G", discogs=921)],
                                   dg=[dg_release(921, "Band G", "Album G")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.sure_picks, {"mb": "mb-g", "discogs": "921"})


class GapListOnceComplete(Base):
    """plan: TAGMENDER_MISSING (and an earlier web page) removed once the album no longer has them."""

    def test_markers_go_when_the_album_is_identified_without_them(self):
        d = self.album("Band H", "Album H", TAGMENDER_MISSING="2", TAGMENDER_SOURCE="https://www.deezer.com/album/1",
                       TAGMENDER="Partial")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-h", "Band H", "Album H")]))
        p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["tier"], "exact")
        ch = a["files"][0]["changes"]
        self.assertIsNone(ch.get("tagmender_missing", "kept"))
        self.assertIsNone(ch.get("tagmender_source", "kept"))
        executor.Executor(p).run()
        t = tagio.read(d / "03.flac")
        self.assertNotIn("tagmender_missing", t)
        self.assertNotIn("tagmender_source", t)

    def test_a_database_page_stays(self):
        # Theory In Practice - Evolving Transhumanism: TAGMENDER_SOURCE names its Discogs page;
        # an exact match now leaves it (only a shop's page goes)
        url = "https://www.discogs.com/release/10033680-Band-K-Album-K"
        d = self.album("Band K", "Album K", TAGMENDER_SOURCE=url)
        eng = self.engine(FakeHttp(mb=[mb_release("mb-k", "Band K", "Album K")]))
        p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual(a["identification"]["tier"], "exact")
        self.assertNotIn("tagmender_source", a["files"][0]["changes"])
        executor.Executor(p).run()
        self.assertEqual(tagio.read(d / "03.flac")["tagmender_source"], [url])

    def test_shop_page_test(self):
        self.assertTrue(pl._shop_page(["https://www.deezer.com/album/1"]))
        self.assertTrue(pl._shop_page(["https://en.wikipedia.org/wiki/X"]))
        self.assertFalse(pl._shop_page(["https://www.discogs.com/release/1"]))
        self.assertFalse(pl._shop_page(["https://musicbrainz.org/release/x"]))
        self.assertFalse(pl._shop_page(["notes"]))

    def test_a_missing_file_restored_drops_the_gap_list(self):
        d = self.album("Band M", "Album M")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-m", "Band M", "Album M")]))
        for f in sorted(d.glob("*.flac")):
            m = __import__("mutagen").File(f)
            m["tagmender_missing"] = "2"
            m.save()
        a = eng.plan(d)["albums"][0]
        for fe in a["files"]:
            self.assertIsNone(fe["changes"].get("tagmender_missing", "kept"), fe["src"])


class CopiesInAnotherFormat(unittest.TestCase):
    """match_albums._drop_duplicate_tracks: a copy without a disc tag, in another format, is a copy."""

    def info(self, title, secs, disc=0):
        return {"title": title, "secs": secs, "disc": disc, "has_ids": False}

    def test_a_flac_and_an_mp3_without_a_disc_tag_are_one_track(self):
        tmp = Path(_tempfile.mkdtemp())
        names = {"01 Song.flac": self.info("Song", 10.0, 1), "01 - Song.mp3": self.info("Song", 10.1),
                 "02 Other.flac": self.info("Other", 20.0, 1), "02 - Other.mp3": self.info("Other", 20.1)}
        info = {}
        for n, i in names.items():
            (tmp / n).write_bytes(b"x")
            info[tmp / n] = i
        kept, gone = ma._drop_duplicate_tracks(sorted(info), info)
        self.assertEqual(sorted(gone), ["01 - Song.mp3", "02 - Other.mp3"])

    def test_the_same_title_on_two_disc_tags_or_disc_folders_stays(self):
        tmp = Path(_tempfile.mkdtemp())
        info = {}
        for rel, i in (("01.flac", self.info("Same", 10.0, 1)), ("02.flac", self.info("Same", 10.1, 2))):
            (tmp / rel).write_bytes(b"x")
            info[tmp / rel] = i
        self.assertEqual(ma._drop_duplicate_tracks(sorted(info), info)[1], [])
        info = {}
        for rel in ("Disc 1/01.flac", "Disc 2/01.flac"):
            (tmp / rel).parent.mkdir(exist_ok=True)
            (tmp / rel).write_bytes(b"x")
            info[tmp / rel] = self.info("Same", 10.0)
        self.assertEqual(ma._drop_duplicate_tracks(sorted(info), info)[1], [])


class ShopSticker(unittest.TestCase):
    """match_core._shop_sticker: a catalogue number only the folder name holds proves nothing."""

    def album(self, **kw):
        lens = (200.0, 305.0, 410.0)
        a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
             "tracks": [{"disc": 1, "num": n, "title": f"T{n}", "secs": lens[n - 1]} for n in (1, 2, 3)],
             "barcodes": {"5020083807827"}, "catnos": {"cat1"}, "unlisted_barcodes": {"5020083807827"}}
        a.update(kw)
        return a

    def cand(self):
        return {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
                "year": 2010, "country": "CA", "label": "L", "catnos": {"cat1"}, "barcodes": {"0801837013429"},
                "disc_count": 1, "tracks": [{"disc": 1, "title": f"T{i + 1}", "secs": s}
                                            for i, s in enumerate((200.0, 305.0, 410.0))]}

    def test_a_folder_name_catalogue_number_keeps_the_barcode_conflict(self):
        s = mc.score_candidate(self.album(catnos_from_folder={"cat1"}), self.cand(), CFG)
        self.assertTrue(s["conflict"])

    def test_the_rip_notes_catalogue_number_still_counts(self):
        s = mc.score_candidate(self.album(catnos_from_folder={"cat1"}, catnos_typed={"cat1"}), self.cand(), CFG)
        self.assertFalse(s["conflict"])


class WebVersionsAlike(Base):
    """engine._web: two web versions fitting alike name no page or cover of either."""

    def test_no_source_page_or_cover_of_one_version(self):
        lens = (10, 20, 30)
        d = self.lib / "Band W" / "2005 - Album W"
        for i, s in enumerate(lens):
            tone(d / f"0{i + 1}.flac", s, title=f"T{i}", album="Album W", albumartist="Band W", artist="Band W",
                 track=str(i + 1))
        http = WebHttp([deezer_album(41, "Band W", "Album W (Remaster 2024)", lens),
                        deezer_album(42, "Band W", "Album W (Remix 2024)", lens)])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "partial"))
        self.assertEqual(p.targets[0]["album"], ["Album W"])
        self.assertFalse([t for t in p.targets if t.get("tagmender_source")])
        self.assertEqual((p.url, p.web_cover), ("", ""))
        notes = " ".join(p.why)
        self.assertIn("https://www.deezer.com/album/41", notes)
        self.assertIn("https://www.deezer.com/album/42", notes)


class SourcesAndProviders(unittest.TestCase):
    """profiles: an "only" Sources mode with that database turned off identifies nothing."""

    def test_validate_names_sources(self):
        d = {"name": "x", "source": "discogs_only", "providers": {"discogs": {"enabled": False},
                                                                  "musicbrainz": {"enabled": True},
                                                                  "web": {"enabled": False}}}
        problems = pf.validate(pf.rename_legacy(d))
        self.assertTrue(any("source is Discogs only but Discogs is not enabled" in x for x in problems), problems)
        d["source"] = "discogs_preferred"
        self.assertEqual(pf.validate(pf.rename_legacy(d)), [])

    def test_merge_mode_named_where_it_comes_from(self):
        p = pf.from_dict({"name": "x", "discogs_enrichment": {"merge_mode": "combine"}, "follow_tagger_settings": False})
        self.assertIn("merge combine (this profile's discogs_enrichment)", pl._enrichment_summary(p))
        p = pf.from_dict({"name": "x", "follow_tagger_settings": False})
        self.assertIn("merge override (from Sources)", pl._enrichment_summary(p))


class OneDiscOfASet(Base):
    """plan: a disc of a set whose discs are different releases is never moved or copied alone."""

    def test_a_move_profile_tags_the_disc_in_place(self):
        top = self.lib / "Band P" / "1991 - Album P [2CD]"
        disc = top / "Disc 01"
        tone(disc / "01.flac", 10, title="T0", album="Album P", albumartist="Band P", track="1")
        (top / "rip.log").write_text("log")
        dest = self.tmp / "dest"
        dest.mkdir()
        prof = pf.from_dict({"name": "m", "operation": "move", "destination": str(dest),
                             "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}})
        prop = pl.Proposal(source_dir=str(disc), files=[str(disc / "01.flac")], status="identified", tier="exact",
                           provider="musicbrainz", release_id="rel-1", confidence=100.0,
                           targets=[{"album": ["Album P"], "albumartist": ["Band P"], "title": ["T0"],
                                     "tracknumber": ["1"], "musicbrainz_albumid": ["rel-1"]}])
        prop.disc_folder = disc
        with mock.patch.object(pl, "mounted", return_value=True):
            a = pl.build([prop], prof)["albums"][0]
        self.assertTrue(all(Path(fe["dst"]).parent == disc for fe in a["files"]), a["files"])
        self.assertTrue(any("one disc of a set whose discs are different releases" in n for n in a["notes"]))


if __name__ == "__main__":
    unittest.main()
