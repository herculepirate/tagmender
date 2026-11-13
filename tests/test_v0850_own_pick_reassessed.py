"""TAG-1 / REP-1: TagMender's previous choice is history, not independent evidence. Its own earlier
pick keeps its standing while what decided it is still found on your copy; a deciding clue no
longer found (an OCR invention the reading no longer makes, lengths that no longer tell it from
the tied pressings) is disproved, a clue not looked for now is only missing.
- Apocalyptica - Reflections: Discogs 26572991 (Egypt cassette, 063 683-4) "pressing chosen by
  catalogue number 636834 on the scans" - read inside OCR noise "06400636834", no number at all;
- Megadeth - Rust In Peace (Instrumental): an instrumental album no release found is: held."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import test_engine as te  # noqa: E402
from test_v0845_replan_stable import _History  # noqa: E402

CFG = {"review_min_confidence": 70, "auto_min_margin": 5}
OCR_REASON = "pressing chosen by catalogue number 636834 on the scans (not on 13771912, 8003949)"
LEN_REASON = "pressing chosen by track lengths closest (off by 0.8s on average, others 2.5s+) (not on x, y)"


def cand(cid, conf=93.1, **kw):
    s = {"cand": {"id": cid, "source": "discogs"}, "confidence": conf, "tracks_ok": True, "notes": [],
         "clues": {}, "code": "", "tagged": False, "tagger_tagged": False}
    s.update(kw)
    return s


class OWN_DigitsPrinted(unittest.TestCase):
    def test_OWN_a_number_inside_ocr_noise_is_not_printed(self):
        self.assertFalse(mc.digits_printed("re=ety >> 06400636834 ngsougso654", "636834"))

    def test_OWN_a_number_printed_in_groups_is(self):
        self.assertTrue(mc.digits_printed("Cat. No. 063 683-4", "636834"))
        self.assertTrue(mc.digits_printed("LC 0309 063 683-4", "636834"))
        self.assertTrue(mc.digits_printed("CDP 7 91935 2", "7919352"))
        self.assertFalse(mc.digits_printed("0636834567", "636834"))

    def test_OWN_pressing_clues_no_longer_read_the_noise(self):
        c = {"source": "discogs", "id": "26572991", "catnos": {"636834"}}
        agree, _ = mc.pressing_clues("re=ety >> 06400636834 ngsougso654", c)
        self.assertNotIn("catalogue number", agree)
        agree, _ = mc.pressing_clues("MIRAGE RECORDS 063 683-4", c)
        self.assertIn("catalogue number", agree)


class OWN_Standing(unittest.TestCase):
    def tied(self, **own):
        mine = cand("26572991", **dict({"tagged": True, "own_decided": OCR_REASON, "scans_read": True}, **own))
        return mine, [cand("13771912"), cand("6499288"), mine, cand("8003949")]

    def test_OWN_its_deciding_code_no_longer_read_is_disproved(self):
        mine, scored = self.tied()
        self.assertIsNone(mc._kept_choice(scored[0], scored, CFG))
        self.assertFalse(mine["tagged"], "no standing left, never kept in a tie either")
        self.assertTrue(any("weighed afresh" in n for n in mine["notes"]))

    def test_OWN_its_deciding_code_still_read_keeps_it(self):
        mine, scored = self.tied(clues={"catalogue number": "catalogue number 636834 on the scans"})
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)
        self.assertTrue(mine["tagged"])

    def test_OWN_scans_not_read_now_is_missing_not_disproved(self):
        mine, scored = self.tied(scans_read=False)
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)

    def test_OWN_a_reason_that_is_no_clue_of_your_copy_keeps_it(self):
        for why in ("pressing chosen by your preferred country order (CA, US) puts Canada first", ""):
            mine, scored = self.tied(own_decided=why)
            self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine, why)

    def test_OWN_lengths_that_no_longer_tell_it_apart_are_disproved(self):
        mine = cand("7426872d", conf=100.0, tagged=True, own_decided=LEN_REASON, len_err=1.4, len_diffs=[1.4])
        scored = [cand("200d226d", conf=100.0, len_err=1.3, len_diffs=[1.3]), mine,
                  cand("2b2021d2", conf=100.0, len_err=1.5, len_diffs=[1.5])]
        self.assertIsNone(mc._kept_choice(scored[0], scored, CFG))
        # (still clearly closest: kept)
        mine = cand("7426872d", conf=100.0, tagged=True, own_decided=LEN_REASON, len_err=0.8, len_diffs=[0.8])
        scored = [cand("200d226d", conf=100.0, len_err=2.5, len_diffs=[2.5]), mine,
                  cand("2b2021d2", conf=100.0, len_err=2.9, len_diffs=[2.9])]
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)


class OWN_History(_History):
    """The job's deciding evidence line is kept with the files TagMender's job tagged."""

    def ident(self, rid, evidence):
        return {"tier": "exact", "provider": "discogs", "release_id": rid,
                "url": f"https://www.discogs.com/release/{rid}", "evidence": evidence}

    def test_OWN_the_deciding_evidence_is_recorded_and_a_later_keeping_job_carries_it(self):
        f = Path("/music/Apocalyptica/2002 - Reflections/01.opus")
        rows = [{"field": "discogs_release_id", "before": ["2044995"], "after": ["26572991"], "action": "changed"}]
        self.job("j1", f.parent, [(f, f, "tag", rows)], self.ident("26572991", ["artist 1.00", OCR_REASON]), ts=1.0)
        self.assertEqual(ma.tagmender_decided([f]), OCR_REASON)
        kept = [{"field": "discogs_release_id", "before": ["26572991"], "after": ["26572991"], "action": "kept"}]
        self.job("j2", f.parent, [(f, f, "none", kept)],
                 self.ident("26572991", ["kept the release your files are tagged with (TAG-1)"]), ts=2.0)
        ma._OWN_INDEX[:] = [None, {}]
        self.assertEqual(ma.tagmender_decided([f]), OCR_REASON, "a job that only kept it keeps its reason")

    def test_OWN_a_release_chosen_anew_without_a_recorded_reason_has_none(self):
        f = Path("/music/Apocalyptica/2002 - Reflections/01.opus")
        rows = [{"field": "discogs_release_id", "before": [], "after": ["26572991"], "action": "added"}]
        self.job("j1", f.parent, [(f, f, "tag", rows)], self.ident("26572991", [OCR_REASON]), ts=1.0)
        rows2 = [{"field": "discogs_release_id", "before": ["26572991"], "after": ["8003949"], "action": "changed"}]
        self.job("j2", f.parent, [(f, f, "tag", rows2)], self.ident("8003949", ["artist 1.00"]), ts=2.0)
        self.assertEqual(ma.tagmender_decided([f]), "")


class OWN_CopyVersion(te.Base):
    def test_OWN_copy_version_words(self):
        album = {"title": "Rust In Peace (Instrumental)", "folder": "1990 - Rust In Peace (Instrumental)"}
        self.assertEqual(mc.copy_version(album, {"title": "Rust in Peace", "tracks": [{"title": "Hangar 18"}]}),
                         "instrumental")
        self.assertEqual(mc.copy_version(album, {"title": "Rust in Peace (Instrumentals)", "tracks": []}), "")
        self.assertEqual(mc.copy_version({"title": "Rust in Peace", "folder": "1990 - Rust in Peace"},
                                         {"title": "Rust in Peace", "tracks": []}), "")

    def test_OWN_an_instrumental_album_no_release_is_is_held(self):
        d = self.album("Band I", "Album I", folder="1990 - Album I (Instrumental)")
        e = self.engine(te.FakeHttp(mb=[te.mb_release("mb-i", "Band I", "Album I")]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual((p.status, p.reason_codes), ("unresolved", ["conflicting_sources"]), (p.reason, p.why))
        self.assertIn("instrumental version", p.reason)

    def test_OWN_an_instrumental_release_matches_an_instrumental_copy(self):
        # (MusicBrainz's disambiguation names the version)
        d = self.album("Band I", "Album I", folder="1990 - Album I (Instrumental)")
        rel = te.mb_release("mb-i", "Band I", "Album I")
        rel["disambiguation"] = "instrumental version"
        e = self.engine(te.FakeHttp(mb=[rel]))
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.status, "identified", (p.reason, p.why))



from unittest import mock  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402


class OWN_Withdrawn(te.Base):
    """The level the evidence supports: TagMender's own pick withdrawn and a guess in its place is
    album only, an album held for it unresolved - and what TagMender's job wrote of the withdrawn
    pressing is taken back (as before that job; removed when it was empty)."""
    CASSETTE = {"source": "discogs", "id": "26572991", "catnos": ["636834"], "catnos_raw": ["063 683-4"],
                "barcodes": ["44006368344"], "formats": ["cassette"], "links": [], "label": "Mirage Records; Universal",
                "country": "Egypt", "year": 2003, "layout": [13]}
    PICK = {"source": "discogs", "id": "470143", "catnos": ["770152"], "barcodes": [], "formats": ["cd"], "links": [],
            "label": "Island Records; Mercury; Universal", "country": "Europe", "year": 2003, "layout": [13]}

    def files(self):
        d = self.album("Apocalyptica", "Reflections", n=2, DISCOGS_RELEASE_ID="26572991", CATALOGNUMBER="063 683-4",
                       RELEASECOUNTRY="EG", MEDIA="Cassette, Album", DATE="2003", LABEL="Mirage Records")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"discogs_release_id": (("2044995",), ("26572991",)), "catalognumber": ((), ("063 683-4",)),
                      "releasecountry": ((), ("EG",)), "media": ((), ("Cassette, Album",)),
                      "date": (("2002",), ("2003",)), "label": ((), ("Mirage Records",))} for f in files}
        return d, files, writes

    def out(self, guessed=True, disproved=OCR_REASON):
        return {"best": {"guessed_pick": guessed, "id": "470143"}, "own_disproved": disproved,
                "release_ids": {"pick": dict(self.PICK), "held": {"discogs_release_id": ["26572991"]},
                                "cands": {"discogs_release_id": {"26572991": dict(self.CASSETTE)}}, "mb_links": {}}}

    def prop(self, d, files):
        return pl.Proposal(source_dir=str(d), files=files, status="identified", tier="exact", confidence=93.1,
                           release_id="470143", guessed_pick=True,
                           targets=[{"album": ["Reflections"], "discogs_release_id": ["470143"], "date": ["2003"],
                                     "originaldate": ["2002"], "catalognumber": ["077 015-2"], "title": [f"T{i}"]}
                                    for i in range(2)])

    def test_OWN_a_guess_in_place_of_a_disproved_own_pick_is_album_only_its_writes_taken_back(self):
        d, files, writes = self.files()
        e = self.engine(te.FakeHttp())
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            p = e._own_guess_withdrawn(self.prop(d, files), self.out())
        self.assertEqual((p.tier, p.release_id), ("album_only", ""))
        self.assertIn("catalogue number 636834", " ".join(p.why))
        ch = pl.build([p], e.profile)["albums"][0]["files"][0]["changes"]
        self.assertEqual(ch.get("discogs_release_id"), ["2044995"], "as before TagMender's job (another tagger's)")
        for k in ("catalognumber", "releasecountry", "media", "label"):
            self.assertIsNone(ch.get(k, "kept"), k)
        self.assertEqual(ch.get("date"), ["2002"])

    def test_OWN_a_disproved_own_pick_your_copy_now_proves_otherwise_stays_exact(self):
        d, files, _w = self.files()
        e = self.engine(te.FakeHttp())
        p = self.prop(d, files)
        p.evidence = ["pressing chosen by catalogue number 770152 on the scans"]
        self.assertIsNone(e._own_guess_withdrawn(p, self.out(guessed=False)))

    def test_OWN_a_fresh_tie_without_an_own_pick_keeps_tie1(self):
        d, files, _w = self.files()
        e = self.engine(te.FakeHttp())
        self.assertIsNone(e._own_guess_withdrawn(self.prop(d, files), self.out(disproved="")))

    def test_OWN_an_album_held_for_its_instrumental_copy_takes_back_tagmenders_writes(self):
        d = self.album("Band I", "Album I", folder="1990 - Album I (Instrumental)", MUSICBRAINZ_ALBUMID="mb-i",
                       CATALOGNUMBER="CAT-1", MEDIA="Vinyl, LP")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"musicbrainz_albumid": ((), ("mb-i",)), "catalognumber": ((), ("CAT-1",)),
                      "media": ((), ("Vinyl, LP",)), "album": (("Album I (Instrumental)",), ("Album I",))}
                  for f in files}
        rel = te.mb_release("mb-i", "Band I", "Album I")
        rel["media"][0]["format"] = '12" Vinyl'
        e = self.engine(te.FakeHttp(mb=[rel]), unresolved={"action": "leave"})
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved", p.why)
        a = pl.build([p], e.profile)["albums"][0]
        ch = a["files"][0]["changes"]
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"))
        self.assertIsNone(ch.get("catalognumber", "kept"))
        self.assertIsNone(ch.get("media", "kept"))
        self.assertNotIn("tagmender", ch, "Unresolved \"leave\": nothing new is written")
        self.assertEqual(ch.get("album"), ["Album I (Instrumental)"], "the title TagMender's job wrote goes back")
        self.assertEqual(a["files"][0]["dst"], a["files"][0]["src"], "a held album is never renamed or moved")

    def test_OWN_a_title_you_changed_since_stays(self):
        d = self.album("Band I", "My Title", folder="1990 - Album I (Instrumental)", MUSICBRAINZ_ALBUMID="mb-i")
        files = sorted(str(f) for f in d.glob("*.flac"))
        writes = {f: {"musicbrainz_albumid": ((), ("mb-i",)), "album": (("Album I (Instrumental)",), ("Album I",))}
                  for f in files}
        e = self.engine(te.FakeHttp(mb=[te.mb_release("mb-i", "Band I", "Album I")]), unresolved={"action": "leave"})
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            p = e.identify(e.albums_in(d)[0])
        if p.status == "unresolved":
            self.assertNotIn("album", (p.restore or {}).get(files[0], {}))


import executor  # noqa: E402

WITHDRAWN = ("ambiguous_release: album known, pressing not established: pressing unknown (TagMender chose this "
             "album's pressing earlier by catalogue number 636834 on the scans, which your copy no longer shows, "
             "and nothing of your copy decides between the pressings")


class OWN_Review(te.Base):
    """The independent review's cases: what is missing is never disproved; a withdrawn pick never
    stays by another path; a second plan confirms the first."""

    def test_OWN_spaced_split_and_confusable_numbers_are_read(self):
        for text in ("063 - 683 - 4", "063\n683-4", "063 683\u20134", "O63 6B3-4", "063 683 \u2014 4"):
            self.assertTrue(mc.digits_printed(text, "636834"), repr(text))
        self.assertFalse(mc.digits_printed("06400636834", "636834"))
        self.assertTrue(mc.digits_in_noise("06400636834", "636834"))
        self.assertFalse(mc.digits_printed("SOLO 683", "50683"), "letters of a word are no digits")

    def test_OWN_a_clue_only_the_earlier_reading_found_is_missing(self):
        mine = cand("26572991", tagged=True, own_decided=OCR_REASON, scans_read=True, catno_unsure=True)
        scored = [cand("13771912"), mine]
        self.assertIs(mc._kept_choice(scored[0], scored, CFG), mine)

    def test_OWN_scans_not_all_read_as_images_is_missing(self):
        album = {"tracks": [], "title": "A", "artist": "B", "scans_complete": False}
        s = {"scans_read": bool(album.get("scans_complete")) and True}
        self.assertFalse(s["scans_read"])
        mine = cand("26572991", tagged=True, own_decided=OCR_REASON, scans_read=False)
        self.assertIs(mc._kept_choice(cand("1"), [cand("1"), mine], CFG), mine)

    def test_OWN_a_matrix_reason_is_not_judged(self):
        mine = cand("26572991", tagged=True, scans_read=True,
                    own_decided="pressing chosen by matrix A-0547 A/C 3 A on the scans")
        self.assertIs(mc._kept_choice(cand("1"), [cand("1"), mine], CFG), mine)

    def test_OWN_version_marks_not_genre_words(self):
        album = {"title": "Beats (Instrumental Hip-Hop)", "folder": "2010 - Beats (Instrumental Hip-Hop)"}
        self.assertEqual(mc.copy_version(album, {"title": "Beats", "tracks": []}), "")
        album = {"title": "Rust In Peace - Instrumentals", "folder": ""}
        self.assertEqual(mc.copy_version(album, {"title": "Rust In Peace", "tracks": []}), "instrumental")
        album = {"title": "Album [Instrumental Version]", "folder": ""}
        self.assertEqual(mc.copy_version(album, {"title": "Album", "tracks": []}), "instrumental")

    def test_OWN_no_restore_while_a_database_is_out(self):
        e = self.engine(te.FakeHttp())
        out = {"release_ids": {"held": {"discogs_release_id": ["1"]}, "cands": {}, "pick": {}}}
        self.assertEqual(e._withdraw_own(["/x/01.flac"], out, errors={"musicbrainz": "outage"}), {})

    def test_OWN_a_withdrawn_guess_stays_album_only_plan_after_plan(self):
        # (after the first plan made it album only: the files are marked Partial with its reason,
        # TagMender's IDs gone - a later plan's guess is album only again, and writes nothing)
        d = self.album("Band D2", "Album D2", TAGMENDER="Partial", TAGMENDER_REASON=WITHDRAWN)
        def http():
            return te.FakeHttp(mb=[te.mb_release("mb-d2", "Band D2", "Album D2", catno="OTHER-100", country="US")],
                               dg=[te.dg_release(904, "Band D2", "Album D2", catno="CAT-901")])

        def identify():
            eng = self.engine(http())
            eng.cfg["preferred_countries"] = ["US"]
            eng.cfg["prefer_source"] = ""
            return eng, eng.identify(eng.albums_in(d)[0])
        eng, p = identify()
        self.assertEqual(p.tier, "album_only", (p.why, p.evidence))
        plan = pl.build([p], eng.profile)
        executor.Executor(plan).run()
        eng, p2 = identify()
        self.assertEqual(p2.tier, "album_only")
        again = pl.build([p2], eng.profile)["albums"][0]
        self.assertEqual([fe["changes"] for fe in again["files"]], [{}] * len(again["files"]))

    def test_OWN_a_fresh_album_tie_keeps_tie1_in_full(self):
        d = self.album("Band D2", "Album D2")
        eng = self.engine(te.FakeHttp(mb=[te.mb_release("mb-d2", "Band D2", "Album D2", catno="OTHER-100", country="US")],
                                      dg=[te.dg_release(904, "Band D2", "Album D2", catno="CAT-901")]))
        eng.cfg["preferred_countries"] = ["US"]
        eng.cfg["prefer_source"] = ""
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.tier, p.release_id), ("exact", "mb-d2"))


if __name__ == "__main__":
    unittest.main()
