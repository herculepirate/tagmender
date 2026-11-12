"""TAG-3: one release per file. Below replace_min_confidence the plan keeps existing values and only
fills empty fields; beside a release ID your files hold for ANOTHER release that left them naming
two pressings (Robin Trower - Back It Up: MusicBrainz's 1983 release kept beside the 1991 US CD
its scans' barcode proves; Tame Impala - Innerspeaker vinyl: another tagger's Australian Discogs
ID beside the US reissue's IDs, a pick the country order broke a tie for). engine._one_release
decides where the proposal is formed: your copy proving the pick - its tags replace the old
release's (the plan writes it at the replace threshold: release_proven_over), the other
database's ID of another pressing removed; nothing proving it, its score below the threshold -
album only (the album's own fields; the files' release tags stay).
Plan level: synthetic FLACs (test_executor.Base). Engine level: ffmpeg FLACs, mocked providers
(test_engine.Base, FakeHttp)."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402

PROVEN = {"state": "proven", "found": ["barcode"], "against": [], "printed": True}
OLD_MB = "adb602df-1111-2222-3333-444455556666"


class TAG3_Plan(Base):
    """What the plan does with a proposal the engine formed (no clash: as ever)."""
    def prof(self, **kw):
        return self.profile(thresholds={"organize_min_confidence": 95, "replace_min_confidence": 90}, **kw)

    def tag_files(self, **tags):
        for f in (self.f1, self.f2):
            tagio.write(f, {k: [v] for k, v in tags.items()})

    def plan_of(self, conf, prof=None, **kw):
        tg = kw.pop("targets", None) or [target(1, "First"), target(2, "Second")]
        return pl.build([self.proposal(conf=conf, targets=tg, **kw)], prof or self.prof())["albums"][0]

    def test_TAG3_same_release_no_clash(self):
        self.tag_files(discogs_release_id="777")
        a = self.plan_of(89.0)
        ch = a["files"][0]["changes"]
        self.assertEqual(ch.get("label"), ["Label"], "empty fields of the same release are filled as before")
        self.assertNotEqual(ch.get("tagmender"), ["Partial"])

    def test_TAG3_no_release_id_in_files_no_clash(self):
        self.tag_files(date="2001")
        a = self.plan_of(89.0)
        self.assertEqual(a["files"][0]["changes"].get("discogs_release_id"), ["777"])
        self.assertNotIn("date", a["files"][0]["changes"], "below the threshold the file's date stays")

    def test_TAG3_at_replace_threshold_unchanged(self):
        self.tag_files(discogs_release_id="555")
        a = self.plan_of(95.0)
        self.assertEqual(a["files"][0]["changes"].get("discogs_release_id"), ["777"])

    def test_TAG3_below_threshold_without_proof_flag_keeps_values(self):
        self.tag_files(date="1983")
        a = self.plan_of(87.6)
        self.assertNotIn("date", a["files"][0]["changes"])

    def test_TAG3_proven_over_replaces_at_the_threshold_from_the_start(self):
        # (the edition, read from the release's date and media, is the new release's too)
        self.tag_files(discogs_release_id="555", date="1983", media="Vinyl")
        tg = [target(i, t, media=["CD, Album, Reissue, Remastered"], originaldate=["1974"]) for i, t in ((1, "First"), (2, "Second"))]
        for t in tg:
            t["~remove"] = ["musicbrainz_albumid"]
        tagio.write(self.f1, {"musicbrainz_albumid": [OLD_MB]})
        a = self.plan_of(87.6, prof=self.prof(naming={"preset": "tagmender", "album_editions": "edition_year"}),
                         targets=tg, release_proven_over=["discogs_release_id", "musicbrainz_albumid"])
        ch = a["files"][0]["changes"]
        self.assertEqual(ch.get("discogs_release_id"), ["777"])
        self.assertEqual(ch.get("date"), ["2001-05-01"], "the old release's date goes with it")
        self.assertIn("musicbrainz_albumid", ch)
        self.assertIsNone(ch["musicbrainz_albumid"], "another pressing's MusicBrainz ID is removed")
        self.assertEqual(ch.get("album"), ["Real Album [2001 Remaster]"], "the edition is the proven release's")

    def test_TAG3_custom_tags_follow_the_raised_confidence(self):
        self.tag_files(discogs_release_id="555")
        tg = [dict(target(i, t), **{"~custom:my_tag": ["x"]}) for i, t in ((1, "First"), (2, "Second"))]
        low = self.plan_of(87.6, targets=[dict(t) for t in tg])
        self.assertNotIn("~custom:my_tag", low["files"][0]["changes"])
        high = self.plan_of(87.6, targets=tg, release_proven_over=["discogs_release_id"])
        self.assertEqual(high["files"][0]["changes"].get("~custom:my_tag"), ["x"])


def _dg(rid, catno="CAT-901", country="US", barcode=None, year=1991, fmt="CD", durs=("0:10", "0:20", "0:30")):
    r = te.dg_release(rid, "Band T", "Album T", durs=durs, catno=catno, country=country)
    r["year"], r["released"] = year, f"{year}-01-01"
    r["formats"] = [{"name": fmt, "descriptions": ["Album"]}]
    if barcode:
        r["identifiers"] = [{"type": "Barcode", "value": barcode}]
    return r


def _mb(rid, catno="OTHER-100", country="GB", discogs=None, barcode="0123456789012", fmt="CD", date="1983-03-01"):
    r = te.mb_release(rid, "Band T", "Album T", catno=catno, country=country)
    r["barcode"], r["date"] = barcode, date
    r["media"][0]["format"] = fmt
    if catno is None:
        r["label-info"] = [{"label": {"name": "Label MB"}, "catalog-number": None}]
    r["relations"] = [{"type": "discogs", "target-type": "url",
                       "url": {"resource": f"https://www.discogs.com/release/{d}"}} for d in discogs or ()]
    return r


PICK_BARCODE = "5099943193424"


class TAG3_Engine(te.Base):
    """engine._one_release on real files and mocked providers, in the shapes of the albums found
    in a real library. Scores held at 88 (below the replace threshold 90) or left as the mocked
    releases score them (near 100, at or above it)."""
    def make(self, folder="1991 - Album T", **tags):
        return self.album("Band T", "Album T", folder=folder, **tags)

    def eng(self, http, **prof):
        return self.engine(http, thresholds={"organize_min_confidence": 95, "replace_min_confidence": 90}, **prof)

    def ident(self, d, http, scans=None, conf=88.0, **prof):
        e = self.eng(http, **prof)
        real = ma.summarize

        def summarize(res):
            b = real(res)
            return dict(b, confidence=conf) if b and conf is not None else b
        with mock.patch.object(ma, "summarize", summarize), \
                mock.patch.object(ma, "scans_report", return_value=dict(scans) if scans else None):
            return e, e.identify(e.albums_in(d)[0])

    def changes(self, e, p, i=0):
        return pl.build([p], e.profile)["albums"][0]["files"][i]["changes"]

    def assert_no_clash(self, p, field):
        self.assertEqual(p.tier, "exact", (p.why, p.notes))
        self.assertNotIn(field, p.targets[0].get("~remove") or [])
        self.assertFalse(any("TAG-3" in n for n in p.notes or []), p.notes)

    def both(self, d, http, **kw):
        """(below the replace threshold, at it)."""
        return [self.ident(d, http(), conf=c, **kw) for c in (88.0, None)]

    # ---- confirmed conflicts: corrected --------------------------------------------------------
    def test_TAG3_state_to_state_barcodes_differ(self):
        # Discogs pick (Europe, its barcode, CDCHR 20135) vs the files' MusicBrainz release (US,
        # another barcode, R2-53704)
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB, MUSICBRAINZ_RELEASETRACKID="old-track")
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno="R2-53704", country="US", barcode="825646406456")],  # noqa
                                   dg=[_dg(904, catno="CDCHR 20135", country="Europe", barcode=PICK_BARCODE)])
        (e1, low), (e2, high) = self.both(d, http)
        self.assertEqual((low.tier, low.release_id), ("album_only", ""), low.why)
        self.assertNotIn("musicbrainz_albumid", self.changes(e1, low), "below: the files' tags stay")
        self.assertEqual(high.tier, "exact")
        ch = self.changes(e2, high)
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"))
        self.assertIsNone(ch.get("musicbrainz_trackid", "kept"), "the losing release's track IDs go too")
        self.assertTrue(any("barcode 825646406456" in n for n in high.notes), high.notes)

    def test_TAG3_rt_ro08_catalogue_numbers_and_links_differ(self):
        # Discogs pick (UK, FREED5011, no barcode) vs MB (US, 501114, no barcode, links Discogs 18844639)
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno="501114", country="US", barcode=None, discogs=[18844639])],  # noqa
                                   dg=[_dg(904, catno="FREED5011", country="UK")])
        (_e1, low), (e2, high) = self.both(d, http)
        self.assertEqual(low.tier, "album_only", low.why)
        self.assertIsNone(self.changes(e2, high).get("musicbrainz_albumid", "kept"))
        note = " ".join(high.notes)
        self.assertIn("catalogue number 501114", note)
        self.assertIn("links it to Discogs 18844639", note)

    def test_TAG3_take_what_you_need_mb_links_the_rejected_discogs(self):
        # the files hold the Discogs pick already, and an MB release linking another Discogs
        # entry (same barcode, another country): two pressings by the link
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB, DISCOGS_RELEASE_ID="904")
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno=None, country="DE", barcode=PICK_BARCODE, discogs=[3784467])],  # noqa
                                   dg=[_dg(904, catno="CDCHR 1", barcode=PICK_BARCODE)])
        (_e1, low), (e2, high) = self.both(d, http)
        self.assertEqual(low.tier, "album_only", low.why)
        ch = self.changes(e2, high)
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"))
        self.assertNotIn("discogs_release_id", ch, "the pick's own ID stays as it is")

    def test_TAG3_afterglow_format_family_differs_corrected_without_contradiction(self):
        # MB pick (2018 digital, XW) vs the files' Discogs release (2007 JP CD, SCH-002): the losing
        # pressing's Discogs fields, media, country and date leave no contradiction behind
        d = self.make(DISCOGS_RELEASE_ID="1585174", DISCOGS_CATALOGNUMBER="SCH-002",
                      DISCOGS_URI="https://www.discogs.com/release/1585174", MEDIA="CD", RELEASECOUNTRY="JP",
                      DATE="2007", CATALOGNUMBER="SCH-002")
        mbid = "d2a5c52b-0000-0000-0000-000000000001"
        http = lambda: te.FakeHttp(mb=[_mb(mbid, catno=None, country="XW", barcode=None, fmt="Digital Media",  # noqa
                                           date="2018-05-01")],
                                   dg=[_dg(1585174, catno="SCH-002", country="Japan", year=2007,
                                           durs=("0:50", "0:50", "0:50"))])
        (_e1, low), (e2, high) = self.both(d, http)
        self.assertEqual(low.tier, "album_only", (low.why, low.notes))
        self.assertEqual((high.tier, high.release_id), ("exact", mbid), high.why)
        ch = self.changes(e2, high)
        for k in ("discogs_release_id", "discogs_catalognumber", "discogs_uri", "catalognumber"):
            self.assertIsNone(ch.get(k, "kept"), k)
        after = {k: ch.get(k) for k in ("media", "releasecountry", "date")}
        self.assertEqual(after["media"], ["Digital Media"], after)
        self.assertEqual(after["releasecountry"], ["XW"], after)
        self.assertTrue(str(after["date"][0]).startswith("2018"), after)

    def test_TAG3_back_it_up_scans_barcode_proves_it_old_date_and_id_replaced(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB, date="1983", media="Vinyl")
        e, p = self.ident(d, te.FakeHttp(mb=[_mb(OLD_MB)], dg=[_dg(904, barcode=PICK_BARCODE)]), scans=PROVEN,
                          naming={"preset": "tagmender", "album_editions": "edition_year"})
        self.assertEqual((p.tier, p.release_id), ("exact", "904"), p.why)
        self.assertEqual(p.release_proven_over, ["musicbrainz_albumid"])
        self.assertTrue(any("TAG-3" in n for n in p.notes), p.notes)
        ch = self.changes(e, p)
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"), "the other pressing's ID is removed")
        self.assertEqual(ch.get("discogs_release_id"), ["904"])
        self.assertTrue(str((ch.get("date") or [""])[0]).startswith("1991"), ch.get("date"))
        album = (ch.get("album") or [""])[0]
        self.assertNotIn("Vinyl", album, "no edition from the old release's media")
        self.assertNotIn("1983", album, "no edition from the old release's date")

    def test_TAG3_unproven_below_threshold_is_album_only_with_reason(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB, date="1983")
        e, p = self.ident(d, te.FakeHttp(mb=[_mb(OLD_MB)], dg=[_dg(904, barcode=PICK_BARCODE)]))
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "album_only", ""), p.why)
        for t in p.targets:
            for k in ("discogs_release_id", "catalognumber", "label", "releasecountry", "barcode",
                      "musicbrainz_albumid", "isrc"):
                self.assertNotIn(k, t, k)
            self.assertNotIn("1991", "".join(t.get("date") or []), "the new pressing's date leaks not")
            self.assertFalse([k for k in t if k.startswith("~") and k not in engine.ALBUM_ONLY_HIDDEN])
        a = pl.build([p], e.profile)["albums"][0]
        self.assertEqual(a["identification"]["tier"], "album_only")
        self.assertTrue(a["identification"]["reason"].startswith("ambiguous_release"), a["identification"])
        self.assertIn("names another pressing", a["identification"]["reason"])
        ch = a["files"][0]["changes"]
        self.assertEqual(ch.get("tagmender"), ["Partial"])
        self.assertNotIn("musicbrainz_albumid", ch, "the release tags your files hold stay")

    def test_TAG3_policy_preserved_field_is_noted_as_kept_not_removed(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        e, p = self.ident(d, te.FakeHttp(mb=[_mb(OLD_MB)], dg=[_dg(904, barcode=PICK_BARCODE)]), conf=None,
                          field_policy={"default": "replace", "fields": {"musicbrainz_albumid": "preserve"}})
        note = next(n for n in p.notes if "TAG-3" in n)
        self.assertIn("kept by your field policy: musicbrainz_albumid", note)
        self.assertNotIn("removed where this release has none: musicbrainz_albumid", note)
        self.assertNotIn("musicbrainz_albumid", self.changes(e, p))

    # ---- compatible IDs: never removed, never demoted ------------------------------------------
    def test_TAG3_bst_mb_release_linking_the_discogs_pick_stays(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno="OTHER-1", country="US", barcode="0123456789012",  # noqa
                                           discogs=[904], fmt="SACD")],
                                   dg=[_dg(904, barcode=PICK_BARCODE, fmt="Vinyl")])
        for e, p in self.both(d, http):
            self.assert_no_clash(p, "musicbrainz_albumid")
            self.assertNotIn("musicbrainz_albumid", {k for k, v in self.changes(e, p).items() if v is None})

    def test_TAG3_compendium_unlinked_same_barcode_and_catalogue_number_stays(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno="REP 5249", country="DE", barcode="4009910524923")],  # noqa
                                   dg=[_dg(904, catno="REP 5249", country="Germany", barcode="4009910524923")])
        for e, p in self.both(d, http):
            self.assert_no_clash(p, "musicbrainz_albumid")
            self.assertNotIn("musicbrainz_albumid", {k for k, v in self.changes(e, p).items() if v is None})

    def test_TAG3_sparse_mb_entry_is_no_clash(self):
        # (the files' MB release lists no barcode or catalogue number, no links: nothing tells
        # it apart - a country alone does not)
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, catno=None, country="GB", barcode=None)],  # noqa
                                   dg=[_dg(904, barcode=PICK_BARCODE, country="US")])
        for e, p in self.both(d, http):
            # (nor does the Sources setting delete it: follow_source asks the same test)
            self.assert_no_clash(p, "musicbrainz_albumid")
            self.assertNotIn("musicbrainz_albumid", self.changes(e, p), "kept end to end")

    def test_TAG3_discogs_off_unlinked_discogs_id_is_no_clash(self):
        _os.environ.pop("DISCOGS_TOKEN", None)
        d = self.make(DISCOGS_RELEASE_ID="2402720")
        http = lambda: te.FakeHttp(mb=[_mb(OLD_MB, discogs=[904])], dg=[_dg(2402720, catno="X-1")])  # noqa
        d = d
        for _e, p in self.both(d, http):
            self.assertEqual(p.release_id, OLD_MB, p.why)
            self.assert_no_clash(p, "discogs_release_id")

    def test_TAG3_mb_id_the_engine_knows_nothing_of_is_no_clash(self):
        d = self.make(MUSICBRAINZ_ALBUMID="99999999-aaaa-bbbb-cccc-dddddddddddd")
        _e, p = self.ident(d, te.FakeHttp(dg=[_dg(904)]))
        self.assert_no_clash(p, "musicbrainz_albumid")

    def test_TAG3_discogs_id_spelt_otherwise_is_the_same_release(self):
        for i, spelt in enumerate(("r904", "[r904]", " 904 ")):
            d = self.make(folder=f"1991 - Album T{' ' * i}", DISCOGS_RELEASE_ID=spelt)
            _e, p = self.ident(d, te.FakeHttp(dg=[_dg(904)]))
            self.assert_no_clash(p, "discogs_release_id")

    # ---- unscored releases: looked up, or nothing known ----------------------------------------
    def view(self, **pick):
        return {"pick": dict({"source": "discogs", "id": "904", "barcodes": [PICK_BARCODE], "catnos": ["cat901"],
                              "formats": ["cd"], "links": []}, **pick), "cands": {}, "mb_links": {}}

    def test_TAG3_unscored_id_is_looked_up_and_compared(self):
        e = self.eng(te.FakeHttp(mb=[_mb(OLD_MB, barcode="825646406456")]))
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["904"]}])
        clash, facts = e._clashing(p, self.view(), {"musicbrainz_albumid": [OLD_MB]})
        self.assertIn(OLD_MB, clash.get("musicbrainz_albumid") or {}, clash)
        self.assertEqual(facts[0]["barcodes"], ["825646406456"])

    def test_TAG3_lookup_outage_is_no_clash(self):
        e = self.eng(te.FakeHttp(mb=[_mb(OLD_MB, barcode="825646406456")], mb_down=True))
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["904"]}])
        self.assertEqual(e._clashing(p, self.view(), {"musicbrainz_albumid": [OLD_MB]}), ({}, []))

    def test_TAG3_old_result_without_the_matchers_view_is_no_clash(self):
        e = self.eng(te.FakeHttp())
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["904"]}])
        self.assertIs(e._one_release(p, {"best": {}}, {"file_release_ids": {"discogs_release_id": ["1"]}}), p)
        self.assertEqual(p.tier, "")

    # ---- REP-1: what TagMender's own earlier job wrote is taken back on album only --------------
    def test_TAG3_album_only_takes_back_what_tagmenders_job_wrote_of_the_losing_pressing(self):
        # (an earlier job wrote MusicBrainz's release Y - its ID, label, catalogue number; Y is now
        # another pressing than the pick: album only takes them back, as they were before that job)
        y = "11111111-2222-3333-4444-555555555555"
        d = self.make(MUSICBRAINZ_ALBUMID=y, LABEL="Label MB", RELEASECOUNTRY="DE")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"musicbrainz_albumid": ((), (y,)), "label": (("PDU",), ("Label MB",)),
                      "releasecountry": ((), ("DE",))} for f in files}
        http = te.FakeHttp(mb=[_mb(y, catno="Y-1", country="DE", barcode="825646406456")],
                           dg=[_dg(904, barcode=PICK_BARCODE)])
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            e, p = self.ident(d, http)
        self.assertEqual(p.tier, "album_only", (p.why, p.notes))
        self.assertEqual(set(p.restore), set(files))
        ch = self.changes(e, p)
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"), "TagMender's own ID of the losing pressing goes")
        self.assertEqual(ch.get("label"), ["PDU"], "the label as it was before TagMender's job")
        self.assertIsNone(ch.get("releasecountry", "kept"))

    def test_TAG3_replan_of_the_release_tagmender_wrote_writes_nothing_to_it(self):
        # (Popol Vuh's shape: an earlier job wrote MusicBrainz's X below the threshold beside another
        # tagger's Discogs D; the plan picks X again, D clashes: album only - and X's own values,
        # which TagMender wrote, stay: no churn, REP-1's second plan writes nothing to them)
        pilz = "11111111-2222-3333-4444-555555555555"
        d = self.make(MUSICBRAINZ_ALBUMID=pilz, DISCOGS_RELEASE_ID="300937", LABEL="Label MB", CATALOGNUMBER="PILZ 1")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"musicbrainz_albumid": ((), (pilz,)), "label": (("PDU",), ("Label MB",)),
                      "catalognumber": ((), ("PILZ 1",))} for f in files}
        http = te.FakeHttp(mb=[_mb(pilz, catno="PILZ 1", country="DE", barcode=None)],
                           dg=[_dg(300937, catno="PDU 1", country="Italy", durs=("0:50", "0:50", "0:50"))])
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            e, p = self.ident(d, http)
        self.assertEqual((p.tier, p.restore), ("album_only", {}), (p.why, p.notes))
        ch = self.changes(e, p)
        for k in ("musicbrainz_albumid", "label", "catalognumber", "discogs_release_id"):
            self.assertNotIn(k, ch, k)

    # ---- Back It Up: the files' MusicBrainz release, its one disc ID not your disc -------------
    BIU_OFFSETS = [150, 17707, 35072, 48345, 88302, 104802, 123117, 134882, 149120]
    BIU_LEADOUT = 172832
    BIU_COPY = [17637, 17318, 13217, 40058, 16607, 18225, 11805, 14208, 23130]

    @staticmethod
    def toc(lengths):
        offs, pos = [], 150
        for n in lengths:
            offs.append(pos)
            pos += n
        return " ".join(str(x) for x in [1, len(lengths), pos] + offs)

    def biu(self):
        adb = "adb602df-0000-4000-8000-000000000001"
        rel = _mb(adb, catno=None, barcode=None, fmt="")
        rel["media"][0]["discs"] = [{"id": "fg9KiD2Ntc7Ne.1PatsLQgKl9wE-", "offset-count": 9,
                                     "offsets": self.BIU_OFFSETS, "sectors": self.BIU_LEADOUT}]
        sparse = {"source": "mb", "id": adb, "barcodes": [], "catnos": [], "formats": [], "links": []}
        view = dict(self.view(id="4238649"), cands={"musicbrainz_albumid": {adb: sparse}})
        return adb, te.FakeHttp(mb=[rel]), view

    def test_TAG3_back_it_up_disc_id_that_fits_no_disc_of_yours_is_another_pressing(self):
        adb, http, view = self.biu()
        e = self.eng(http)
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["4238649"]}])
        why = e._another_pressing(p, dict(view, copy_tocs={"1": self.toc(self.BIU_COPY)}), "musicbrainz_albumid", adb)[0]
        self.assertIn("none of its disc IDs fits your disc's table of contents", why)
        # (your disc's TOC unknown: no evidence; your TOC that fits its disc ID: the same disc)
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs={}), "musicbrainz_albumid", adb)[0], "")
        theirs = [b - a for a, b in zip(self.BIU_OFFSETS, self.BIU_OFFSETS[1:] + [self.BIU_LEADOUT])]
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs={"1": self.toc(theirs)}),
                                             "musicbrainz_albumid", adb)[0], "")
        # (MusicBrainz down: nothing known)
        down = self.eng(te.FakeHttp(mb_down=True))
        self.assertEqual(down._another_pressing(p, dict(view, copy_tocs={"1": self.toc(self.BIU_COPY)}),
                                                "musicbrainz_albumid", adb)[0], "")

    def test_TAG3_back_it_up_end_to_end_proven_and_unproven(self):
        adb, http, view = self.biu()
        album = {"file_release_ids": {"musicbrainz_albumid": [adb]}, "tocs": {1: self.toc(self.BIU_COPY)},
                 "tocs_fit": [1], "tocs_read": {1: self.toc(self.BIU_COPY)}}

        def prop():
            return pl.Proposal(source_dir="x", files=[], status="identified", tier="exact", confidence=87.6,
                               release_id="4238649", targets=[{"album": ["Back It Up"],
                                                               "discogs_release_id": ["4238649"]}])
        e = self.eng(http)
        proven = e._one_release(prop(), {"release_ids": view, "scans": dict(PROVEN)}, album)
        self.assertEqual(proven.release_proven_over, ["musicbrainz_albumid"], proven.notes)
        self.assertTrue(any("disc IDs" in n for n in proven.notes), proven.notes)
        e._accepts = lambda tier: True
        self.assertEqual(e._one_release(prop(), {"release_ids": view}, album).tier, "album_only")
        # (no TOC of your copy read from a log or cue - only the one its files' lengths give:
        # the sparse entry is no other pressing)
        self.assertEqual(e._one_release(prop(), {"release_ids": view}, dict(album, tocs_read={})).tier, "exact")

    def test_TAG3_disc_ids_are_compared_medium_by_medium(self):
        # (your copy is disc 1; MusicBrainz lists a disc ID for medium 2 only: nothing to compare)
        adb, http, view = self.biu()
        rel = http.mb[adb]
        rel["media"][1]["discs"], rel["media"][0]["discs"] = rel["media"][0]["discs"], []
        e = self.eng(http)
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["4238649"]}])
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs={"1": self.toc(self.BIU_COPY)}),
                                             "musicbrainz_albumid", adb)[0], "")
        # (your disc 2's TOC against medium 2's disc ID, a CD: compared)
        rel["media"][1]["format"] = "CD"
        self.assertIn("disc IDs", e._another_pressing(p, dict(view, copy_tocs={"2": self.toc(self.BIU_COPY)}),
                                                      "musicbrainz_albumid", adb)[0])

    def test_TAG3_a_dvd_listed_first_is_no_false_difference(self):
        # (MusicBrainz: 1 DVD, 2 CD1, 3 CD2; your copy numbers its CDs 1 and 2 - your disc 2 is
        # its CD 2, never compared with CD 1's disc ID)
        adb, http, view = self.biu()
        rel = http.mb[adb]
        cd1 = [17000, 17000, 17000, 17000, 17000, 17000, 17000, 17000, 17000]
        disc = lambda ls: {"offsets": [int(x) for x in self.toc(ls).split()[3:]],  # noqa: E731
                           "sectors": int(self.toc(ls).split()[2])}
        rel["media"] = [{"format": "DVD-Video", "position": 1, "tracks": [], "discs": []},
                        {"format": "CD", "position": 2, "tracks": [], "discs": [disc(cd1)]},
                        {"format": "CD", "position": 3, "tracks": [], "discs": [disc(self.BIU_COPY)]}]
        e = self.eng(http)
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["4238649"]}])
        tocs = {"1": self.toc([16000] * 9), "2": self.toc(self.BIU_COPY)}
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs=tocs), "musicbrainz_albumid", adb)[0], "")
        # (only your disc 2, CD 2 listing no disc ID: never compared with CD 1 at position 2 -
        # the DVD first shifts the numbers)
        rel["media"][2]["discs"] = []
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs={"2": self.toc(self.BIU_COPY)}),
                                             "musicbrainz_albumid", adb)[0], "")

    def test_TAG3_one_disc_of_a_set_read_as_disc_one_is_no_false_difference(self):
        # (a folder holding the set's second CD alone: read as disc 1, it fits CD 2's disc ID)
        adb, http, view = self.biu()
        rel = http.mb[adb]
        other = {"offsets": [150, 20150, 40150, 60150, 80150, 100150, 120150, 140150, 160150], "sectors": 180150}
        rel["media"] = [{"format": "CD", "position": 1, "tracks": [], "discs": [other]},
                        {"format": "CD", "position": 2, "tracks": [], "discs": [
                            {"offsets": self.BIU_OFFSETS, "sectors": self.BIU_LEADOUT}]}]
        theirs = [b - a for a, b in zip(self.BIU_OFFSETS, self.BIU_OFFSETS[1:] + [self.BIU_LEADOUT])]
        e = self.eng(http)
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["4238649"]}])
        self.assertEqual(e._another_pressing(p, dict(view, copy_tocs={"1": self.toc(theirs)}),
                                             "musicbrainz_albumid", adb)[0], "")

    def test_TAG3_a_toc_rebuilt_from_the_files_lengths_is_no_evidence(self):
        t = self.toc(self.BIU_COPY)
        other = self.toc([17637 + 150] + self.BIU_COPY[1:])
        self.assertEqual(ma.tocs_read({1: t}, [1], {}, {1: t}, {1: t}), {}, "a cue that only repeats the files")
        self.assertEqual(ma.tocs_read({1: t}, [1], {}, {}, {1: t}), {}, "the files' lengths alone")
        self.assertEqual(ma.tocs_read({1: t}, [1], {1: t}, {}, {1: t}), {1: t}, "a rip log's TOC")
        self.assertEqual(ma.tocs_read({1: other}, [1], {}, {1: other}, {1: t}), {1: other}, "a cue with its pregaps")
        self.assertEqual(ma.tocs_read({1: other}, [], {1: other}, {}, {1: t}), {}, "one that fits no disc of yours")

    def test_TAG3_back_it_up_toc_from_an_image_cue_sheet(self):
        # (the disc's track starts from a cue sheet of one image file: its real TOC)
        import tempfile as _tf
        d = Path(_tf.mkdtemp(dir=str(self.tmp)))
        starts, pos = [], 0
        for n in self.BIU_COPY:
            starts.append(pos)
            pos += n
        lines = ['FILE "Back It Up.flac" WAVE']
        for k, st in enumerate(starts, 1):
            lines += [f"  TRACK {k:02d} AUDIO", f"    INDEX 01 {st // 4500:02d}:{st // 75 % 60:02d}:{st % 75:02d}"]
        (d / "Robin Trower - Back It Up.cue").write_text("\n".join(lines) + "\n")
        infos = [(d / "Back It Up.flac", {"secs": pos / 75})]
        cues = ma.cue_tocs(d, infos)
        self.assertEqual(cues, {1: self.toc(self.BIU_COPY)})
        read = ma.tocs_read(cues, [1], {}, cues, {})
        adb, http, view = self.biu()
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["4238649"]}])
        why = self.eng(http)._another_pressing(p, dict(view, copy_tocs={str(k): v for k, v in read.items()}),
                                               "musicbrainz_albumid", adb)[0]
        self.assertIn("none of its disc IDs fits your disc's table of contents", why)

    # ---- BS&T: the losing pressing's date taken back too -------------------------------------
    def bst(self, date_before):
        x = "5d5bc7a0-6d8a-4cc1-8653-1d2cf03c3df7"            # (the 1980 vinyl job ea73 wrote)
        y = "25e2ef6e-8a11-4562-8b1d-5ab93a2ca570"            # (the 2017 AP SACD, picked now)
        d = self.make(folder="1968 - BST (1968) [SACD] (2017 AP ISO)", MUSICBRAINZ_ALBUMID=x, DATE="1980",
                      MEDIA="Vinyl, LP, Album, Remastered, Reissue")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"musicbrainz_albumid": ((), (x,)), "date": (date_before, ("1980",)),
                      "media": ((), ("Vinyl, LP, Album, Remastered, Reissue",))} for f in files}
        view = {"pick": {"source": "mb", "id": y, "barcodes": ["856276002343"], "catnos": ["capp086sa"],
                         "formats": ["cd"], "links": [], "year": 2017, "label": "Analogue Productions",
                         "country": "US", "layout": [3]},
                "cands": {"musicbrainz_albumid": {x: {"source": "mb", "id": x, "barcodes": [], "catnos": ["sd16605"],
                                                      "formats": ["vinyl"], "links": [], "year": 1980,
                                                      "label": "Direct-Disk Labs", "country": "US", "layout": [3]}}},
                "mb_links": {}}
        album = {"file_release_ids": {"musicbrainz_albumid": [x]}}
        targets = [{"album": ["Blood, Sweat & Tears"], "musicbrainz_albumid": [y], "date": ["2017-03-01"],
                    "originaldate": ["1968"], "media": ["Hybrid SACD"], "title": [f"T{i}"]} for i in range(3)]
        p = pl.Proposal(source_dir=str(d), files=files, status="identified", tier="exact", confidence=88.5,
                        release_id=y, targets=targets)
        e = self.eng(te.FakeHttp())
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            p = e._one_release(p, {"release_ids": view}, album)
        return e, p, files, x

    def test_TAG3_bst_album_only_takes_back_the_vinyls_date(self):
        e, p, files, _x = self.bst(("2016",))
        self.assertEqual(p.tier, "album_only", p.notes)
        self.assertEqual(p.restore[files[0]].get("date"), ["2016"])
        plan = pl.build([p], e.profile)
        ch = plan["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch.get("date"), ["2016"], "the date as it was before TagMender's job")
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"))
        self.assertIsNone(ch.get("media", "kept"))
        # a second plan: the files as the first left them, the history's date now as before
        for f in files:
            tagio.write(f, {"date": ["2016"]})
        e2 = self.eng(te.FakeHttp())
        again = pl.Proposal(source_dir=p.source_dir, files=files, status="identified", tier="exact",
                            confidence=88.5, release_id="25e2ef6e-8a11-4562-8b1d-5ab93a2ca570",
                            targets=[{"album": ["Blood, Sweat & Tears"], "date": ["2017-03-01"],
                                      "originaldate": ["1968"]} for _f in files])
        writes = {f: {"date": (("2016",), ("2016",))} for f in files}
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            again = e2._one_release(again, {"release_ids": {"pick": {"source": "mb", "id": "y", "barcodes": [],
                                                                     "catnos": [], "formats": [], "links": []}}},
                                    {"file_release_ids": {}})
        self.assertNotIn("date", pl.build([again], e2.profile)["albums"][0]["files"][0]["changes"])

    def test_TAG3_bst_date_empty_before_is_the_albums_original_date(self):
        e, p, files, _x = self.bst(())
        self.assertEqual(p.restore[files[0]].get("date"), [])
        ch = pl.build([p], e.profile)["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch.get("date"), ["1968"], "album only's own date rule, not a removal")

    def test_TAG3_a_date_of_the_picks_year_or_the_original_year_is_never_taken_back(self):
        e, p, files, x = self.bst(("2016",))
        side = {"musicbrainz_albumid": {"y"}, "discogs_release_id": set()}
        facts = [{"source": "mb", "id": x, "year": 1980}]
        writes = {f: {"date": (("2016",), ("1980",))} for f in files}
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            self.assertEqual(e._restore_own(files, {"musicbrainz_albumid": {x: "x"}}, facts, {"year": 1980}, side), {})
            self.assertEqual(e._restore_own(files, {"musicbrainz_albumid": {x: "x"}}, facts, {"year": 2017}, side,
                                            {"1980"}), {})

    # ---- review 3: missing numbers, twins, merged IDs, ranges, the original date ---------------
    def test_TAG3_none_catalogue_number_is_no_number(self):
        mbr = ma.release_facts({"source": "mb", "id": "m", "catnos": {"[none]"}, "catno_shown": "[none]"})
        dgr = ma.release_facts({"source": "discogs", "id": "1", "catnos": {"none"}, "catno_shown": "none"})
        self.assertEqual((mbr["catnos"], mbr["catnos_raw"], dgr["catnos"]), ([], [], []))
        pick = ma.release_facts({"source": "discogs", "id": "904", "catnos": {"abc123"}, "catno_shown": "ABC123"})
        side = {"musicbrainz_albumid": set(), "discogs_release_id": {"904"}}
        self.assertEqual(engine._pressings_differ(pick, mbr, side, {}), [])

    def test_TAG3_range_catalogue_numbers_agree(self):
        a = ma.release_facts({"source": "discogs", "id": "1", "catnos": {"sicp101514"}, "catno_shown": "SICP 10151-4"})
        b = ma.release_facts({"source": "mb", "id": "m", "catnos": {"sicp10151~4"}, "catno_shown": "SICP-10151~4"})
        self.assertTrue(engine._catnos_agree(a, b))
        self.assertEqual(engine._pressings_differ(a, b, {"musicbrainz_albumid": set(), "discogs_release_id": {"1"}},
                                                  {}), [])

    def test_TAG3_another_twin_of_the_pick_is_the_same_release(self):
        e = self.eng(te.FakeHttp())
        view = dict(self.view(), twins={"musicbrainz_albumid": ["mb-a", "mb-b"]}, mb_twin="mb-b")
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["904"],
                                                             "musicbrainz_albumid": ["mb-b"]}])
        self.assertEqual(e._another_pressing(p, view, "musicbrainz_albumid", "mb-a")[0], "")
        # (an MB entry not among the twins: only evidence makes it another pressing, never the
        # ID written alone)
        self.assertEqual(e._another_pressing(p, view, "musicbrainz_albumid", "mb-c")[0], "")
        view["cands"] = {"musicbrainz_albumid": {"mb-c": {"source": "mb", "id": "mb-c", "barcodes": ["825646406456"]}}}
        self.assertIn("barcode 825646406456", e._another_pressing(p, view, "musicbrainz_albumid", "mb-c")[0])

    def test_TAG3_merged_old_id_looked_up_as_the_picks_twin_is_the_same_release(self):
        http = te.FakeHttp(mb=[_mb("mb-twin", barcode="825646406456")])
        http.mb["mb-old"] = http.mb["mb-twin"]            # (MusicBrainz answers the merged-into release)
        e = self.eng(http)
        view = dict(self.view(), mb_twin="mb-twin")
        p = pl.Proposal(source_dir="x", files=[], targets=[{"discogs_release_id": ["904"]}])
        self.assertEqual(e._another_pressing(p, view, "musicbrainz_albumid", "mb-old")[0], "")

    def test_TAG3_original_date_of_the_album_is_not_the_losing_pressings(self):
        d = self.make(date="1983")
        f = str(sorted(d.glob("*.flac"))[0])
        e = self.eng(te.FakeHttp())
        old = {"source": "mb", "id": OLD_MB, "year": 1983, "barcodes": [], "catnos": [], "formats": []}
        p = pl.Proposal(source_dir=str(d), files=[f], targets=[{"album": ["Album T"], "originalyear": ["1983"]}])
        e._losing_fields(p, {"musicbrainz_albumid": {OLD_MB: "x"}}, [old])
        self.assertNotIn("date", p.targets[0].get("~remove") or [])
        p = pl.Proposal(source_dir=str(d), files=[f], targets=[{"album": ["Album T"], "originalyear": ["1975"]}])
        e._losing_fields(p, {"musicbrainz_albumid": {OLD_MB: "x"}}, [old])
        self.assertIn("date", p.targets[0].get("~remove") or [])

    # ---- proof ----------------------------------------------------------------------------------
    def test_TAG3_extra_careful_never_proves(self):
        d = self.make(MUSICBRAINZ_ALBUMID=OLD_MB)
        e, p = self.ident(d, te.FakeHttp(mb=[_mb(OLD_MB)], dg=[_dg(904, barcode=PICK_BARCODE)]), scans=PROVEN,
                          matching={"extra_careful": True})
        self.assertFalse(getattr(p, "release_proven_over", None), p.notes)
        self.assertNotEqual(p.tier, "exact", "nothing proves it: album only")
        p = pl.Proposal(source_dir="x", files=[])
        self.assertEqual(e._copy_proves(p, {"scans": dict(PROVEN)}, []), "")
        self.assertTrue(self.eng(te.FakeHttp())._copy_proves(p, {"scans": dict(PROVEN)}, []))

    def test_TAG3_guessed_pick_is_no_proof(self):
        # (Tame Impala - Innerspeaker: a tie the country order broke)
        e = self.eng(te.FakeHttp())
        p = pl.Proposal(source_dir="x", files=[], guessed_pick=True, own_number=True, all_lengths=True)
        self.assertEqual(e._copy_proves(p, {"scans": dict(PROVEN)}, []), "")

    def test_TAG3_own_number_needs_every_length_and_not_on_the_other_release(self):
        e = self.eng(te.FakeHttp())
        p = pl.Proposal(source_dir="x", files=[], own_number=True, all_lengths=False)
        self.assertEqual(e._copy_proves(p, {}, [{"shares_number": False}]), "", "without every length")
        p.all_lengths = True
        self.assertTrue(e._copy_proves(p, {}, [{"shares_number": False}]))
        self.assertEqual(e._copy_proves(p, {}, [{"shares_number": True}]), "", "a number on both")
        self.assertEqual(e._copy_proves(p, {}, [{"unknown": True}]), "", "a release not known")
        cat = {"state": "proven", "found": ["catalogue_number"], "against": [], "printed": True}
        p.own_number = False
        self.assertEqual(e._copy_proves(p, {"scans": cat}, [{"shares_number": True}]), "")
        self.assertTrue(e._copy_proves(p, {"scans": cat}, [{"shares_number": False}]))
        p.all_lengths = False
        self.assertEqual(e._copy_proves(p, {"scans": cat}, [{"shares_number": False}]), "")

    def test_TAG3_barcode_on_scans_proves_with_partial_lengths(self):
        e = self.eng(te.FakeHttp())
        p = pl.Proposal(source_dir="x", files=[], all_lengths=False, most_lengths=True)
        self.assertTrue(e._copy_proves(p, {"scans": dict(PROVEN)}, [{"shares_number": True}]))
        self.assertEqual(e._copy_proves(p, {"scans": dict(PROVEN, against=["catalogue_number"])}, []), "")

    def test_TAG3_norm_release_id(self):
        self.assertEqual(ma.norm_release_id("discogs_release_id", "[r123]"), "123")
        self.assertEqual(ma.norm_release_id("discogs_release_id", " R123 "), "123")
        self.assertEqual(ma.norm_release_id("musicbrainz_albumid", " ADB602DF "), "adb602df")


if __name__ == "__main__":
    unittest.main()
