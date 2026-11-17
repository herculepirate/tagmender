"""TIE-1 (docs/MATCHING_RULES.md, decided 2026-11-17): a pressing chosen among the album's tied
pressings by a tie-break alone - your country order, the original release date, your preferred
release types or formats - is no proof: the album is tagged as album known (album_only), and the
tie-break's pick is proposed (the plan's "suggested" release, with the other tied pressings) for you
to confirm by choosing it (Album > Use Suggested Pressing, Change Release, `plan --release`). A
pick your copy's evidence made - your barcode or catalogue number on it alone, your scans, an exact
disc ID that only it carries - stays exact; a disc ID several pressings share proves the disc, not
the pressing (TOC-3). Synthetic albums and candidates, mocked providers: no library, no network."""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import test_engine as te  # noqa: E402
from test_v0868_dsc10_set_trial import DSC10Base, DiscHttp, release  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
SECS = [219.0, 450.0, 539.0, 186.0, 330.0, 470.0, 288.0, 245.0, 238.0, 115.0]
TOC = "1 10 231407 150 16575 50325 90747 104642 129450 164850 186450 204825 222900"
# what only one pressing has: never written for an album known
PRESSING_FIELDS = ("musicbrainz_albumid", "discogs_release_id", "catalognumber", "barcode", "label",
                   "releasecountry", "media")


def album(**kw):
    a = {"artist": "Band", "title": "Album", "year": 1999, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(SECS)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(cid, country="GB", **kw):
    c = {"source": "mb", "id": cid, "url": f"https://musicbrainz.org/release/{cid}", "artist": "Band",
         "title": "Album", "year": 1999, "date": "1999-01-01", "country": country, "label": f"Label {cid}",
         "catnos": set(), "barcodes": set(), "disc_count": 1, "formats": {"cd"}, "release_group": "rg-1",
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(SECS)]}
    c.update(kw)
    return c


def pick(a, cands, cfg=CFG, ocr=""):
    return mc.classify([mc.score_candidate(a, c, cfg, ocr) for c in cands], cfg)


class TIE1_Matcher(unittest.TestCase):
    """match_core: which picks are a tie-break's guess (tie_guess), which your copy's evidence."""

    def test_TIE1_country_order_only_is_a_guess(self):
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(album(), [cand("gb"), cand("ca", country="CA"), cand("us", country="US")], cfg)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "ca"), r["why"])
        g = r["best"]["tie_guess"]
        self.assertIn("country order", g["by"])
        self.assertEqual(g["pick"]["ref"], "ca")
        self.assertEqual(sorted(x["id"] for x in g["others"]), ["gb", "us"])

    def test_TIE1_original_date_only_is_a_guess(self):
        r = pick(album(year=None), [cand("re", date="2009-05-01", year=2009), cand("first", date="1999-01-01")])
        self.assertEqual(r["best"]["cand"]["id"], "first", r["why"])
        g = r["best"].get("tie_guess")
        self.assertTrue(g, r["best"]["notes"])
        self.assertIn("original pressing", g["by"])
        self.assertEqual([x["id"] for x in g["others"]], ["re"])

    def test_TIE1_your_barcode_on_one_is_evidence(self):
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(album(barcodes={"0123456789012"}),
                 [cand("gb", barcodes={"0123456789012"}), cand("ca", country="CA")], cfg)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "gb"), r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_TIE1_your_own_catalogue_number_on_one_is_evidence(self):
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(album(catnos={"ABC-123"}), [cand("gb", catnos={"ABC-123"}), cand("ca", country="CA")], cfg)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "gb"), r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_TIE1_a_number_printed_on_your_scans_is_evidence(self):
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(album(), [cand("gb", catnos={"XYZ-7781"}), cand("ca", country="CA", catnos={"QQ-1"})], cfg,
                 ocr="Band Album\nCat. No. XYZ-7781\nMade in England")
        self.assertEqual(r["best"]["cand"]["id"], "gb", r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def toc_album(self, on):
        return album(tocs={1: TOC}, tocs_fit=[1], toc_releases=set(on), exact_toc_releases=set(on),
                     exact_toc_media={x: {1} for x in on}, exact_toc_on={x: {TOC: {1}} for x in on})

    def test_TIE1_an_exact_disc_id_only_one_carries_is_evidence(self):
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(self.toc_album({"gb"}), [cand("gb"), cand("ca", country="CA")], cfg)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "gb"), r["why"])
        self.assertNotIn("tie_guess", r["best"])

    def test_TIE1_a_disc_id_several_pressings_share_is_no_evidence(self):
        """TOC-3: it proves the disc, not which of them; the country order only suggests one."""
        cfg = dict(CFG, preferred_countries=["CA"])
        r = pick(self.toc_album({"gb", "ca"}), [cand("gb"), cand("ca", country="CA")], cfg)
        self.assertEqual(r["best"]["cand"]["id"], "ca", r["why"])
        self.assertEqual([x["id"] for x in r["best"]["tie_guess"]["others"]], ["gb"])

    def test_TIE1_one_pressing_listed_twice_is_no_guess(self):
        self.assertIsNone(mc.tie_guess({"cand": {}}, [], {"duplicate": "the same pressing listed twice"}))

    def test_TIE1_a_pressing_your_copy_rules_out_is_no_alternative(self):
        best = {"cand": cand("ca", country="CA"), "clues_against": []}
        vinyl = {"cand": cand("lp", formats={"vinyl"}), "clues_against": ["your folder has a CD rip log, so not this "
                                                                         "vinyl release"]}
        self.assertIsNone(mc.tie_guess(best, [best, vinyl], {"country preference": "CA first"}))

    def test_TIE1_between_the_two_databases_sure_picks(self):
        """combine: MusicBrainz's and Discogs's sure picks of two pressings, the country order
        choosing: the pick is a guess, the other database's pressing its alternative."""
        cfg = dict(CFG, preferred_countries=["CA"])
        mb = mc.classify([mc.score_candidate(album(), cand("mb-ca", country="CA"), cfg)], cfg)
        dg = mc.classify([mc.score_candidate(album(), cand("77", country="Germany", source="discogs",
                                                           url="https://www.discogs.com/release/77",
                                                           release_group=""), cfg)], cfg)
        mb["scored"], dg["scored"] = [mb["best"]], [dg["best"]]
        out = mc.combine(mb, dg, ["CA"])
        self.assertEqual(out["best"]["cand"]["id"], "mb-ca")
        g = out["best"]["tie_guess"]
        self.assertEqual([x["ref"] for x in g["others"]], ["discogs:77"])
        again = mc.combine(mb, dg, ["CA"])                          # (combine runs more than once)
        self.assertEqual(len(again["best"]["tie_guess"]["others"]), 1)


CA, US = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1", "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"
VOY_DE, VOY_US = "4e2b3cd1-8d9e-4f1b-a27f-0b7e4f6e16d5", "5f3c4de2-9e0f-4a2c-b380-1c8f5a7f27e6"
FIRST, LATER = "2c0f19bf-6b7c-4df9-a05d-8f5c2d4cf4b3", "3d1a2bc0-7c8d-4e0a-b16e-9a6d3e5d05c4"


class TIE1_Engine(te.Base):
    """engine + planner: album known, the pick suggested; your choice tags it in full."""

    def rel(self, rid, country, catno, label, barcode):
        r = te.mb_release(rid, "Band T", "Album T", country=country, catno=catno)
        r["label-info"] = [{"label": {"name": label}, "catalog-number": catno}]
        r["barcode"] = barcode
        r["release-group"]["id"] = "rg-tie"
        r["media"] = r["media"][:1]                               # (the CD only)
        return r

    def http(self):
        return te.FakeHttp(mb=[self.rel(CA, "CA", "CA-100", "Maple", "0000000000017"),
                               self.rel(US, "US", "US-200", "Eagle", "0000000000024")])

    def eng(self, http, **prof):
        e = self.engine(http, **prof)
        e.cfg["preferred_countries"] = ["CA"]
        e.cfg["prefer_source"] = ""
        e.use_discogs = False
        return e

    def test_TIE1_country_order_tie_is_album_known_with_a_suggestion(self):
        d = self.album("Band T", "Album T")
        e = self.eng(self.http())
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "album_only", ""), (p.why, p.evidence))
        self.assertEqual(p.suggested["ref"], CA)
        self.assertEqual(p.suggested["url"], f"https://musicbrainz.org/release/{CA}")
        self.assertEqual([x["ref"] for x in p.suggested["others"]], [US])
        self.assertIn("country order", p.suggested["why"])
        self.assertIn(f"--release {CA}", p.suggested["why"])
        for t in p.targets:
            self.assertFalse([k for k in PRESSING_FIELDS if k in t], t)
            self.assertEqual(t["album"], ["Album T"])
        # the plan: what is written, its reason, and the suggestion (CLI --json carries it)
        built = pl.build([p], e.profile, "job", extra_roots=[self.lib])
        a = built["albums"][0]
        ident = a["identification"]
        self.assertEqual((ident["tier"], ident["suggested"]["ref"]), ("album_only", CA))
        self.assertTrue(ident["reason"].startswith("ambiguous_release"), ident["reason"])
        written = {k for fe in a["files"] for k in (fe.get("changes") or {})}
        self.assertFalse(written & set(PRESSING_FIELDS), written)
        self.assertIn("tagmender", written)                         # (marked Partial, its reason)
        text = pl.preview(built)
        self.assertIn(f"suggested pressing: musicbrainz {CA}", text)
        self.assertIn(f"--release {CA}", text)

    def test_TIE1_original_date_tie_is_album_known_too(self):
        d = self.album("Band T", "Album T")
        first = self.rel(FIRST, "US", "US-1", "Eagle", "0000000000031")
        first["date"] = "1999-01-01"
        later = self.rel(LATER, "US", "US-2", "Hawk", "0000000000048")
        later["date"] = "2009-01-01"
        e = self.eng(te.FakeHttp(mb=[first, later]))
        e.cfg["preferred_countries"] = []
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.tier, "album_only", (p.why, p.evidence))
        self.assertEqual(p.suggested.get("ref"), FIRST)
        self.assertIn("original pressing", p.suggested["by"])
        self.assertFalse([k for k in PRESSING_FIELDS if k in p.targets[0]], p.targets[0])

    def test_TIE1_your_barcode_on_one_stays_exact(self):
        d = self.album("Band T", "Album T", barcode="0000000000024")
        e = self.eng(self.http())
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.tier, p.release_id), ("exact", US), (p.why, p.evidence))
        self.assertFalse(p.suggested)
        self.assertEqual(p.targets[0]["catalognumber"], ["US-200"])

    def test_TIE1_your_own_catalogue_number_stays_exact(self):
        d = self.album("Band T", "Album T", catalognumber="US-200")
        e = self.eng(self.http())
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.tier, p.release_id), ("exact", US), (p.why, p.evidence))

    def test_TIE1_forcing_the_suggested_release_tags_it_in_full(self):
        d = self.album("Band T", "Album T")
        e = self.eng(self.http())
        p = e.identify(e.albums_in(d)[0])
        ref = p.suggested["ref"]
        e.force = {os.path.realpath(str(d)): ref}                   # (Use Suggested Pressing, --release)
        q = e.identify(e.albums_in(d)[0])
        self.assertEqual((q.status, q.tier, q.release_id), ("identified", "exact", CA), q.why)
        self.assertFalse(q.suggested)
        t = q.targets[0]
        self.assertEqual((t["catalognumber"], t["barcode"], t["label"], t["musicbrainz_albumid"]),
                         (["CA-100"], ["0000000000017"], ["Maple"], [CA]))

    def test_TIE1_unattended_plan_writes_no_pressing_fields(self):
        """What a watched folder and `tagmender run` do: Engine.plan, nobody choosing."""
        d = self.album("Band T", "Album T")
        e = self.eng(self.http())
        plan = e.plan(str(d))
        a = plan["albums"][0]
        self.assertEqual(a["identification"]["tier"], "album_only")
        self.assertEqual(a["identification"]["suggested"]["ref"], CA)
        written = {k for fe in a["files"] for k in (fe.get("changes") or {})}
        self.assertTrue(written)
        self.assertFalse(written & set(PRESSING_FIELDS), written)

    def test_TIE1_exact_only_profile_holds_it_with_the_suggestion(self):
        d = self.album("Band T", "Album T")
        e = self.eng(self.http(), identification={"accept": "exact"})
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertIn("ambiguous_release", p.reason_codes)
        self.assertEqual(p.suggested["ref"], CA)
        self.assertIn("pressing suggested", p.reason)

    def test_TIE1_the_reason_names_the_suggestion(self):
        d = self.album("Band T", "Album T")
        e = self.eng(self.http())
        a = pl.build([e.identify(e.albums_in(d)[0])], e.profile, "job", extra_roots=[self.lib])["albums"][0]
        import reasons
        why = a["identification"]["reason"]
        self.assertEqual(why, f"ambiguous_release: album known, pressing not established: "
                              f"pressing suggested, not confirmed: {CA}")
        self.assertLessEqual(len(why), reasons.MAX_LEN)
        self.assertEqual(reasons.suggested_ref(why), CA)
        self.assertEqual(reasons.suggested_ref("ambiguous_release: album known, pressing not established"), "")
        # (written to the files as TAGMENDER_REASON)
        self.assertEqual({str((fe["changes"].get("tagmender_reason") or [""])[0]) for fe in a["files"]}, {why})

    def test_TIE1_needs_attention_report_lists_it_to_confirm(self):
        import report
        rows = [{"folder": "/m/A", "codes": ["ambiguous_release"], "mark": "Partial",
                 "reason": f"album known, pressing not established: pressing suggested, not confirmed: {CA}"},
                {"folder": "/m/B", "codes": ["ambiguous_release"], "mark": "Partial",
                 "reason": "album known, pressing not established: pressing unknown (2 pressings fit equally)"}]
        rep = report.for_attention(rows, look_up=False)
        self.assertEqual([(r["folder"], r["section"]) for r in rep["rows"]], [("/m/A", "decide")])
        self.assertIn(f"--release {CA}", rep["rows"][0]["issue"])

    def sources(self, prefer, linked=False):
        d = self.album("Band T", "Album T")
        mb = self.rel(CA, "CA", "CA-100", "Maple", "0000000000017")
        if linked:                                       # (MusicBrainz's entry links Discogs's: one pressing)
            mb["relations"] = [{"type": "discogs", "url": {"resource": "https://www.discogs.com/release/904"}}]
        dg = te.dg_release(904, "Band T", "Album T", catno="DG-904", country="Germany")
        if linked:                                       # (and Discogs's entry is that same pressing)
            dg = te.dg_release(904, "Band T", "Album T", catno="CA-100", country="Canada")
            dg["labels"] = [{"name": "Maple", "catno": "CA-100"}]
            dg["identifiers"] = [{"type": "Barcode", "value": "0000000000017"}]
        e = self.eng(te.FakeHttp(mb=[mb], dg=[dg]))
        e.use_discogs = True
        e.cfg["preferred_countries"] = []
        e.cfg["prefer_source"] = prefer
        return e.identify(e.albums_in(d)[0])

    def test_TIE1_the_sources_preference_alone_only_suggests(self):
        """HF-2 / TIE-1: two different pressings, nothing of your copy behind either."""
        p = self.sources("musicbrainz")
        self.assertEqual(p.tier, "album_only", (p.why, p.evidence))
        self.assertEqual((p.suggested["ref"], [x["ref"] for x in p.suggested["others"]]), (CA, ["discogs:904"]))
        self.assertIn("Sources prefers MusicBrainz", p.suggested["by"])
        self.assertFalse([k for k in PRESSING_FIELDS if k in p.targets[0]], p.targets[0])
        p = self.sources("discogs")
        self.assertEqual(p.tier, "album_only", (p.why, p.evidence))
        self.assertEqual((p.suggested["ref"], [x["ref"] for x in p.suggested["others"]]), ("discogs:904", [CA]))

    def test_TIE1_the_two_databases_one_pressing_stays_exact(self):
        p = self.sources("musicbrainz", linked=True)
        self.assertEqual((p.tier, p.release_id), ("exact", CA), (p.why, p.evidence))
        self.assertFalse(p.suggested)

    def test_TIE1_your_barcode_on_one_database_pick_stays_exact(self):
        d = self.album("Band T", "Album T", barcode="0000000000017")
        mb = self.rel(CA, "CA", "CA-100", "Maple", "0000000000017")
        e = self.eng(te.FakeHttp(mb=[mb], dg=[te.dg_release(904, "Band T", "Album T", catno="DG-904")]))
        e.use_discogs = True
        e.cfg["preferred_countries"], e.cfg["prefer_source"] = [], "discogs"
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.tier, p.release_id), ("exact", CA), (p.why, p.evidence))


class TIE1_Set(DSC10Base):
    """DSC-10: a set searched for as a whole whose pressings tie is album known as one set."""

    def test_TIE1_a_set_whose_pressings_tie_is_one_album_known(self):
        top = self.voyage()
        rels = []
        for rid, country, code in ((VOY_DE, "DE", "0123456789012"), (VOY_US, "US", "0123456789029")):
            r = release(rid, "Voyage", ("Dreams", "Live", "Questions"), barcode=code)
            r["country"], r["release-group"]["id"] = country, "rg-voyage"
            r["label-info"] = [{"label": {"name": f"Label {country}"}, "catalog-number": f"V-{country}"}]
            rels.append(r)
        eng = self.engine(DiscHttp(mb=rels))
        eng.cfg["preferred_countries"] = ["US"]
        props = eng.proposals(self.lib)
        self.assertEqual(len(props), 1, self.summary(props))           # (never each disc on its own)
        p = props[0]
        self.assertEqual((p.status, p.tier, p.release_id, p.source_dir), ("identified", "album_only", "", str(top)),
                         (p.why, p.notes))
        self.assertEqual((p.suggested["ref"], [x["ref"] for x in p.suggested["others"]]), (VOY_US, [VOY_DE]))
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3 + ["3"] * 3)
        self.assertFalse([k for t in p.targets for k in PRESSING_FIELDS if k in t])
        self.assertTrue(any("found searching for the set" in n for n in p.notes), p.notes)


try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow
except ImportError:                                                   # GUI is optional
    QApplication = None


class _Page(QMainWindow if QApplication else object):
    """The Overview's side of force_release: the selected album, the forced releases, preview()."""

    def __init__(self, a):
        super().__init__()
        self.a, self.forced, self.previews, self.running, self.plan = a, {}, 0, False, None
        self.profile = QComboBox()
        self.profile.addItem("default")
        self.win = self

    def selected(self):
        return self.a

    def preview(self):
        self.previews += 1


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TIE1_Gui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp(prefix="tm-tie1-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def album(self, tier="album_only", suggested=True):
        sg = {"source": "musicbrainz", "id": CA, "ref": CA, "url": f"https://musicbrainz.org/release/{CA}",
              "by": "your preferred country order (CA) puts Canada first", "others": [{"ref": US}]}
        return {"source_dir": str(self.tmp / "Band" / "Album"), "status": "ready", "files": [],
                "album_files": [str(self.tmp / "Band" / "Album" / "01.flac")],
                "identification": dict({"tier": tier, "status": "identified"},
                                       **({"suggested": sg} if suggested else {}))}

    def test_TIE1_action_enabled_only_for_a_suggested_pressing(self):
        from gui import actions, search
        self.assertIn("use_suggested_pressing", dict(actions.MENUS)["&Album"])
        win = QMainWindow()
        a = actions.make(win, "use_suggested_pressing", *actions.COMMANDS["use_suggested_pressing"])
        page = _Page(self.album())
        for album, on in ((self.album(), True), (self.album(suggested=False), False),
                          (self.album(tier="exact"), False), (None, False)):
            self.assertEqual(search.can_use_suggested(album), on, album)
        a.setEnabled(search.can_use_suggested(page.selected()))
        self.assertTrue(a.isEnabled())
        a.triggered.connect(lambda: search.use_suggested(page))
        a.trigger()
        key = os.path.realpath(page.a["source_dir"])
        self.assertEqual([c["ref"] for c in page.forced[key]], [CA])     # (the suggested release forced)
        self.assertEqual(page.previews, 1)                                      # (and planned again)
        page.a = self.album(suggested=False)
        self.assertFalse(search.use_suggested(page))
        self.assertEqual(page.previews, 1)
        win.deleteLater()
        page.deleteLater()

    def test_TIE1_the_status_says_album_known_pressing_suggested(self):
        from gui.common import label_for
        self.assertEqual(label_for(self.album())[1], "Ready · Album known · pressing suggested")
        self.assertEqual(label_for(self.album(suggested=False))[1], "Ready · Album only")

    def test_TIE1_the_evidence_tab_offers_the_button(self):
        from gui.inspector import EvidenceView
        v = EvidenceView()
        v.show_album(self.album(), None)
        self.assertFalse(v.suggest.isHidden())
        self.assertIn(CA, v.suggest.toolTip())
        v.show_album(self.album(suggested=False), None)
        self.assertTrue(v.suggest.isHidden())
        v.deleteLater()


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TIE1_NeedsAttention(unittest.TestCase):
    """The command and the Evidence button on an album Needs attention lists (its TAGMENDER_REASON)."""

    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import test_gui_shell
        self.shell = test_gui_shell
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.win = self.gui.Main()
        self.win.show()
        test_gui_shell.settle(self.app)

    def tearDown(self):
        self.shell.Shell.dispose(self, self.win)

    def test_TIE1_use_suggested_pressing_from_needs_attention(self):
        from unittest import mock
        att = self.win.pages["Needs attention"]
        self.win.go("Needs attention")
        rows = [{"folder": "/m/Band/Suggested", "codes": ["ambiguous_release"], "mark": "Partial", "files": 3,
                 "reason": f"album known, pressing not established: pressing suggested, not confirmed: {CA}"},
                {"folder": "/m/Band/Tied", "codes": ["ambiguous_release"], "mark": "Partial", "files": 3,
                 "reason": "album known, pressing not established: pressing unknown (2 pressings fit equally)"}]
        att.show_rows(rows)
        texts = {att.table.item(i, 1).data(Qt.UserRole): att.table.item(i, 0).text() for i in range(2)}
        self.assertEqual(texts["/m/Band/Suggested"], "Pressing suggested — confirm")
        cmd = self.win.commands["use_suggested_pressing"]
        for folder, on in (("/m/Band/Tied", False), ("/m/Band/Suggested", True)):
            i = next(i for i in range(2) if att.table.item(i, 1).data(Qt.UserRole) == folder)
            att.table.selectRow(i)
            self.shell.settle(self.app)
            self.win.update_album_actions()
            self.assertEqual(cmd.isEnabled(), on, folder)
            self.assertEqual(not self.win.inspector.evidence.suggest.isHidden(), on, folder)
        ov = self.win.pages["Overview"]
        with mock.patch.object(type(ov), "preview_release", return_value=True) as pr:
            self.win.inspector.evidence.suggest.click()               # (the Evidence tab's button)
            self.shell.settle(self.app)
        pr.assert_called_once_with("/m/Band/Suggested", CA)
        self.assertIs(self.win.stack.currentWidget(), ov)


if __name__ == "__main__":
    unittest.main()
