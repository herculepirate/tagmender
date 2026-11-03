# Tests for the profile's Sources setting (musicbrainz_only, musicbrainz_preferred,
# discogs_preferred, discogs_only): which database identifies, the merge mode, the older
# "primary" read into it, and the release IDs already in the files - replaced or removed as a
# visible, undoable change, never a reason for review. Synthetic audio, recorded-style API
# answers (no network).
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_engine import Base, FakeHttp, dg_release, mb_release  # noqa: E402

import argparse  # noqa: E402
import unittest  # noqa: E402

import engine  # noqa: E402
import executor  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


class Setting(unittest.TestCase):
    """The setting itself: default, older profiles, validation, the merge mode it sets."""

    def setUp(self):
        self.ini = os.environ.get("TAGMENDER_TAGGER_INI")
        os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/tagger.ini"

    def tearDown(self):
        if self.ini is None:
            os.environ.pop("TAGMENDER_TAGGER_INI", None)
        else:
            os.environ["TAGMENDER_TAGGER_INI"] = self.ini

    def test_default_is_discogs_preferred(self):
        p = pf.from_dict({"name": "x"})
        self.assertEqual(p.source(), "discogs_preferred")
        self.assertEqual(p.enrichment()["merge_mode"], "override")
        self.assertEqual(p.matcher_cfg({})["prefer_source"], "discogs")

    def test_each_mode_sets_identification_and_merge(self):
        want = {"musicbrainz_only": ("mb_only", True, False, "musicbrainz"),
                "musicbrainz_preferred": ("fill_gaps", True, True, "musicbrainz"),
                "discogs_preferred": ("override", True, True, "discogs"),
                "discogs_only": ("discogs_only", False, True, "discogs")}
        for src, (merge, mb, dg, prefer) in want.items():
            p = pf.from_dict({"name": "x", "source": src})
            self.assertEqual((p.enrichment()["merge_mode"], p.identifies_with("musicbrainz"),
                              p.identifies_with("discogs"), p.prefers()), (merge, mb, dg, prefer), src)

    def test_merge_follows_source_not_the_tagger_settings(self):
        tmp = Path(__import__("tempfile").mkdtemp())
        ini = tmp / "tagger.ini"
        ini.write_text("[plugin.dd789f69-332f-498a-af94-d8e3273cc435]\nmerge_mode=combine\n")
        os.environ["TAGMENDER_TAGGER_INI"] = str(ini)
        self.assertEqual(pf.from_dict({"name": "x"}).enrichment()["merge_mode"], "override")
        self.assertEqual(pf.from_dict({"name": "x", "source": "musicbrainz_preferred"}).enrichment()["merge_mode"],
                         "fill_gaps")

    def test_profile_merge_mode_fine_tunes_a_preferred_mode_only(self):
        p = pf.from_dict({"name": "x", "discogs_enrichment": {"merge_mode": "combine"}})
        self.assertEqual((p.source(), p.enrichment()["merge_mode"]), ("discogs_preferred", "combine"))
        p = pf.from_dict({"name": "x", "source": "discogs_only", "discogs_enrichment": {"merge_mode": "combine"}})
        self.assertEqual(p.enrichment()["merge_mode"], "discogs_only")

    def test_older_primary_is_read_as_source(self):
        for primary, src in (("discogs", "discogs_preferred"), ("musicbrainz", "musicbrainz_preferred"),
                             ("both", "discogs_preferred")):
            p = pf.from_dict({"name": "x", "primary": primary})
            self.assertEqual(p.source(), src, primary)
            self.assertNotIn("primary", p.data)
        # an older merge mode that wrote one database's tags only: that database preferred,
        # both still identifying, and its merge mode kept
        p = pf.from_dict({"name": "x", "primary": "discogs", "discogs_enrichment": {"merge_mode": "mb_only"}})
        self.assertEqual((p.source(), p.enrichment()["merge_mode"]), ("musicbrainz_preferred", "mb_only"))
        self.assertTrue(p.identifies_with("discogs") and p.identifies_with("musicbrainz"))
        p = pf.from_dict({"name": "x", "discogs_enrichment": {"merge_mode": "discogs_only"}})
        self.assertEqual((p.source(), p.enrichment()["merge_mode"]), ("discogs_preferred", "discogs_only"))
        self.assertTrue(p.identifies_with("discogs") and p.identifies_with("musicbrainz"))
        # a source set wins over the older words
        p = pf.from_dict({"name": "x", "primary": "musicbrainz", "source": "discogs_only"})
        self.assertEqual(p.source(), "discogs_only")
        self.assertTrue(pf.has_legacy({"name": "x", "primary": "both"}))

    def test_minimal_saves_source_not_primary(self):
        out = pf.minimal(pf.from_dict({"name": "x", "primary": "discogs"}))
        self.assertEqual(out["source"], "discogs_preferred")
        self.assertNotIn("primary", out)
        self.assertNotIn("source", pf.minimal(pf.from_dict({"name": "y", "version": pf.SCHEMA_VERSION})))

    def test_bad_values_are_named(self):
        self.assertIn("source must be one of musicbrainz_only, musicbrainz_preferred, discogs_preferred, discogs_only",
                      pf.validate({"name": "x", "source": "spotify"}))
        self.assertTrue(any(x.startswith("primary must be one of")
                            for x in pf.validate({"name": "x", "primary": "spotify"})))
        with self.assertRaises(pf.ProfileError):
            pf.from_dict({"name": "x", "source": "both"})

    def test_cli_source_overrides_the_profile_for_one_run(self):
        import tagmender
        prof = tagmender._profile(argparse.Namespace(profile=None, profile_opt=None, level=None,
                                                     source="musicbrainz_only"))
        self.assertEqual(prof.source(), "musicbrainz_only")
        parser = argparse.ArgumentParser()
        tagmender.work_parsers(parser.add_subparsers(dest="cmd"))
        self.assertEqual(parser.parse_args(["plan", "/x", "--source", "discogs_only"]).source, "discogs_only")
        import contextlib
        import io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["run", "/x", "--source", "both"])


def _result(cand, status="auto", confidence=95.0, prefer="", **best):
    b = {"cand": cand, "confidence": confidence, "notes": [], "clues": {}, "code": "", **best}
    return {"status": status, "best": b, "runner_up": None, "margin": 20.0, "why": [], "scored": [b],
            "cfg": {"prefer_source": prefer}}


def _cand(source, rid, catno, country, **extra):
    return {"source": source, "id": rid, "title": "Album", "artist": "Band", "year": 2005, "country": country,
            "label": "L", "catnos": [catno], "barcodes": [], "formats": ["CD"], "tracks": [], **extra}


class Combine(unittest.TestCase):
    """match_core.combine: both databases sure of different pressings."""

    def picks(self, prefer):
        mb = _result(_cand("mb", "mb-1", "A-1", "US"), prefer=prefer)
        dg = _result(_cand("discogs", 9, "B-2", "DE"), prefer=prefer)
        return mb, dg

    def test_discogs_preferred_never_guesses(self):
        mb, dg = self.picks("discogs")
        out = mc.combine(mb, dg, countries=["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", 9))
        self.assertTrue(any("Discogs decides" in n for n in out["best"]["notes"]))

    def test_musicbrainz_preferred_decides_first(self):
        mb, dg = self.picks("musicbrainz")
        out = mc.combine(mb, dg, countries=["DE"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "mb-1"))
        self.assertTrue(any("MusicBrainz decides" in n for n in out["best"]["notes"]))

    def test_musicbrainz_preferred_brings_discogs_twin(self):
        mb = _result(_cand("mb", "mb-1", "A-1", "DE", barcodes=["0123456789012"]), prefer="musicbrainz")
        dg = _result(_cand("discogs", 9, "A-1", "DE", barcodes=["0123456789012"]), prefer="musicbrainz")
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"], out.get("dg_twin")), ("auto", "mb-1", "9"))

    def test_without_a_preference_as_before(self):
        mb, dg = self.picks("")
        out = mc.combine(mb, dg, countries=["US"])
        self.assertEqual(out["status"], "review")


class Modes(Base):
    """The engine and planner under each mode, with release IDs already in the files."""

    def plan(self, d, http, **prof):
        eng = self.engine(http, **prof)
        return eng, eng.plan(d)

    def rows(self, p, field):
        return [r for fe in p["albums"][0]["files"] for r in fe["diff"] if r["field"] == field]

    def test_musicbrainz_only_never_asks_discogs_and_removes_its_tags(self):
        d = self.album("Band S", "Album S", DISCOGS_RELEASE_ID="555", DISCOGS_STYLE="Old")
        http = FakeHttp(mb=[mb_release("mb-s", "Band S", "Album S", discogs=905)],
                        dg=[dg_release(905, "Band S", "Album S")])
        eng, p = self.plan(d, http, source="musicbrainz_only")
        self.assertFalse([u for h, u in http.calls if h == "discogs"])
        a = p["albums"][0]
        self.assertEqual((a["identification"]["status"], a["identification"]["provider"]),
                         ("identified", "musicbrainz"))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["musicbrainz_albumid"], ["mb-s"])
        self.assertIsNone(ch["discogs_release_id"])
        self.assertIsNone(ch["discogs_style"])
        self.assertFalse([k for k, v in ch.items() if k.startswith("discogs_") and v])
        self.assertEqual(self.rows(p, "discogs_release_id")[0]["note"], "removes your Discogs release ID 555")
        self.assertIn("removes your Discogs release ID 555", a["notes"])

    def test_discogs_only_never_asks_musicbrainz_and_removes_its_ids(self):
        d = self.album("Band T", "Album T", MUSICBRAINZ_ALBUMID="mb-old", MUSICBRAINZ_ARTISTID="art-old")
        http = FakeHttp(mb=[mb_release("mb-t", "Band T", "Album T", discogs=906)],
                        dg=[dg_release(906, "Band T", "Album T")])
        eng, p = self.plan(d, http, source="discogs_only")
        self.assertFalse([u for h, u in http.calls if h == "mb"])
        a = p["albums"][0]
        self.assertEqual((a["identification"]["status"], a["identification"]["provider"]),
                         ("identified", "discogs"))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["discogs_release_id"], ["906"])
        self.assertIsNone(ch["musicbrainz_albumid"])
        self.assertIsNone(ch["musicbrainz_artistid"])
        self.assertIn("removed   musicbrainz_albumid: mb-old => —  (removes your MusicBrainz release ID mb-old)",
                      pl.preview(p))
        # undo puts the IDs back
        executor.Executor(p).run()
        t = tagio.read(d / "01.flac")
        self.assertNotIn("musicbrainz_albumid", t)
        self.assertEqual(t["discogs_release_id"], ["906"])
        executor.undo(p["job"])
        t = tagio.read(d / "01.flac")
        self.assertEqual((t["musicbrainz_albumid"], t["musicbrainz_artistid"]), (["mb-old"], ["art-old"]))

    def test_discogs_preferred_replaces_a_differing_musicbrainz_id_with_the_twin(self):
        d = self.album("Band U", "Album U", MUSICBRAINZ_ALBUMID="mb-old")
        http = FakeHttp(mb=[mb_release("mb-u", "Band U", "Album U", discogs=907)],
                        dg=[dg_release(907, "Band U", "Album U")])
        eng, p = self.plan(d, http)
        a = p["albums"][0]
        self.assertEqual((a["identification"]["status"], a["identification"]["tier"]), ("identified", "exact"))
        self.assertEqual(a["files"][0]["changes"]["musicbrainz_albumid"], ["mb-u"])
        self.assertEqual(self.rows(p, "musicbrainz_albumid")[0]["note"], "replaces your MusicBrainz release ID mb-old")
        self.assertIn("replaces your MusicBrainz release ID mb-old with mb-u", a["notes"])
        self.assertFalse(a["conflicts"])

    def test_discogs_preferred_keeps_the_pressing_your_files_are_tagged_with(self):
        # MusicBrainz is sure of another pressing, and the files are tagged with it: the
        # preference only breaks a tie, and the files' own release is kept
        MBV = "0b5e0c1e-1111-4222-8333-444455556666"               # (only a real MBID names a release)
        d = self.album("Band V", "Album V", MUSICBRAINZ_ALBUMID=MBV)
        http = FakeHttp(mb=[mb_release(MBV, "Band V", "Album V", catno="OTHER-1", country="US")],
                        dg=[dg_release(908, "Band V", "Album V", catno="CAT-908")])
        eng = self.engine(http)
        eng.cfg["preferred_countries"] = ["US"]
        a = eng.plan(d)["albums"][0]
        self.assertEqual((a["identification"]["provider"], a["identification"]["tier"]), ("musicbrainz", "exact"))
        self.assertTrue(any("kept the release your files are tagged with" in n
                            for n in a["identification"].get("evidence") or []), a["identification"])
        self.assertNotIn("musicbrainz_albumid", a["files"][0]["changes"])

    def test_discogs_preferred_removes_a_musicbrainz_id_of_another_pressing_without_review(self):
        # MusicBrainz is sure of another pressing than the one your files name: Discogs decides,
        # and your MusicBrainz release and track IDs go (its recordings and disc ID stay)
        d = self.album("Band V", "Album V", MUSICBRAINZ_ALBUMID="mb-old", MUSICBRAINZ_RELEASETRACKID="old-track",
                       MUSICBRAINZ_TRACKID="old-rec", MUSICBRAINZ_DISCID="disc-own")
        http = FakeHttp(mb=[mb_release("mb-v", "Band V", "Album V", catno="OTHER-1", country="US")],
                        dg=[dg_release(908, "Band V", "Album V", catno="CAT-908")])
        eng = self.engine(http)
        eng.cfg["preferred_countries"] = ["US"]
        p = eng.plan(d)
        a = p["albums"][0]
        ident = a["identification"]
        self.assertEqual((ident["status"], ident["provider"], ident["tier"]), ("identified", "discogs", "exact"))
        self.assertFalse(ident.get("why"), ident.get("why"))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["discogs_release_id"], ["908"])
        self.assertIsNone(ch["musicbrainz_albumid"])
        self.assertIsNone(ch["musicbrainz_trackid"])          # (the release track's ID, as stored)
        self.assertNotIn("musicbrainz_recordingid", ch)
        self.assertNotIn("musicbrainz_discid", ch)
        self.assertIn("removes your MusicBrainz release ID mb-old", a["notes"])

    def test_discogs_preferred_keeps_musicbrainz_ids_when_musicbrainz_was_not_asked(self):
        d = self.album("Band W", "Album W", MUSICBRAINZ_ALBUMID="mb-w")
        http = FakeHttp(dg=[dg_release(909, "Band W", "Album W")], mb_down=True)
        eng, p = self.plan(d, http)
        ch = p["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch["discogs_release_id"], ["909"])
        self.assertNotIn("musicbrainz_albumid", ch)              # (an outage says nothing about the pressing)

    def test_musicbrainz_preferred_decides_and_removes_a_discogs_id_of_another_pressing(self):
        # (your files name a Discogs release neither database offers; Discogs is sure of 910)
        d = self.album("Band X", "Album X", DISCOGS_RELEASE_ID="900", DISCOGS_CATALOGNUMBER="CAT-900")
        http = FakeHttp(mb=[mb_release("mb-x", "Band X", "Album X", catno="OTHER-1", country="US")],
                        dg=[dg_release(910, "Band X", "Album X", catno="CAT-910")])
        eng, p = self.plan(d, http, source="musicbrainz_preferred")
        a = p["albums"][0]
        ident = a["identification"]
        self.assertEqual((ident["status"], ident["provider"], ident["tier"]), ("identified", "musicbrainz", "exact"))
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["musicbrainz_albumid"], ["mb-x"])
        self.assertIsNone(ch["discogs_release_id"])
        self.assertIsNone(ch["discogs_catalognumber"])
        self.assertIn("removes your Discogs release ID 900", a["notes"])

    def test_musicbrainz_preferred_fills_gaps_from_the_same_pressing(self):
        d = self.album("Band Y", "Album Y", DISCOGS_RELEASE_ID="999")
        http = FakeHttp(mb=[mb_release("mb-y", "Band Y", "Album Y", discogs=911)],
                        dg=[dg_release(911, "Band Y", "Album Y")])
        eng, p = self.plan(d, http, source="musicbrainz_preferred")
        a = p["albums"][0]
        self.assertEqual(a["identification"]["provider"], "musicbrainz+discogs")
        ch = a["files"][0]["changes"]
        self.assertEqual(ch["label"], ["Label MB"])                 # fill_gaps: MusicBrainz's value stays
        self.assertEqual(ch["genre"], ["Electronic"])                # ... and Discogs fills the gap
        self.assertEqual(ch["discogs_release_id"], ["911"])
        self.assertEqual(self.rows(p, "discogs_release_id")[0]["note"], "replaces your Discogs release ID 999")

    def test_follow_source_leaves_an_album_only_album_alone(self):
        prop = pl.Proposal(source_dir="/x", files=["/nonexistent/01.flac"], status="identified",
                           targets=[{"album": ["A"], "musicbrainz_albumid": ["m"]}], tier="album_only")
        engine.follow_source(prop, "discogs_preferred")
        self.assertEqual(prop.targets[0], {"album": ["A"], "musicbrainz_albumid": ["m"]})
        engine.follow_source(prop, "discogs_only")
        self.assertEqual(prop.targets[0], {"album": ["A"]})


class ProfilesPage(Base):
    """The app's Profiles page: "Sources" as one plain choice, read and saved."""

    def test_sources_choice_is_shown_and_saved(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 not installed")
        app = QApplication.instance() or QApplication([])
        from gui import profiles as gp
        pf.save(pf.from_dict({"name": "Old", "primary": "musicbrainz"}))
        from unittest import mock
        page = gp.Profiles(mock.MagicMock())
        page.reload()
        app.processEvents()
        names = [page.list.item(i).text() for i in range(page.list.count())]
        page.list.setCurrentRow(names.index("Old"))
        app.processEvents()
        self.assertEqual([page.source.itemText(i) for i in range(page.source.count())],
                         ["MusicBrainz only", "MusicBrainz preferred", "Discogs preferred", "Discogs only"])
        self.assertEqual(page.source.currentData(), "musicbrainz_preferred")    # (the older "primary")
        page.source.setCurrentIndex(page.source.findData("discogs_only"))
        self.assertTrue(page.save_btn.isEnabled())
        page.save()
        raw = pf.raw("Old")
        self.assertEqual((raw.get("source"), "primary" in raw), ("discogs_only", False))
        self.assertEqual(pf.load("Old").source(), "discogs_only")
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
