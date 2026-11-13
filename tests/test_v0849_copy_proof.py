"""TAG-3 / R9: your copy's own printed evidence trumps the tags on the files (Popol Vuh - In Den
Gärten Pharaos (Pilz) vinyl rip: the label scans read "Bestell-Nr. 20 21276-9" and the matrix
"A-0547 A/C 3A"; only Discogs 525272, Pilz's 1971 first edition, lists that matrix - a release
Discogs's search never returned, linked by the MusicBrainz pick; the files held another tagger's
Discogs 300937, the Italian PDU quad reissue).
- the Discogs releases the best MusicBrainz candidates link are fetched and scored (leads);
- a matrix read off your copy that the pick or its twin lists proves it; a vinyl rip's catalogue
  number printed on your copy, on no other scored pressing, too;
- on an album only, a release ID your copy's printed number contradicts goes with its fields;
- the Discogs fields a TagMender job wrote of the pick's twin are never taken back.
ffmpeg FLACs, mocked providers (test_engine.Base, FakeHttp)."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402
import test_v0847_tag3_one_release as t3  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402

PILZ_MB = "2faf11e7-0257-4dff-a639-b2542ed51070"
CAT = {"state": "proven", "found": ["catalogue_number", "label"], "against": [], "printed": True}


class COPY_Proof(te.Base):
    def eng(self, http, **prof):
        return self.engine(http, thresholds={"organize_min_confidence": 95, "replace_min_confidence": 90}, **prof)

    def prop(self, **kw):
        return pl.Proposal(**dict({"source_dir": "x", "files": []}, **kw))

    # ---- 1. MusicBrainz's links are leads --------------------------------------------------
    def test_COPY_discogs_release_musicbrainz_links_is_fetched_and_scored(self):
        # (your files name another Discogs release: the leads are followed)
        d = self.album("Band P", "Album P", DISCOGS_RELEASE_ID="300937")
        first = te.dg_release(525272, "Band P", "Album P (Gatefold)")      # (Discogs's search misses it)
        http = te.FakeHttp(mb=[te.mb_release("mb-p", "Band P", "Album P")], dg=[first])
        http.mb["mb-p"]["relations"] = [{"type": "discogs", "target-type": "url",
                                         "url": {"resource": "https://www.discogs.com/release/525272"}}]
        e = self.eng(http)
        e.identify(e.albums_in(d)[0])
        self.assertTrue(any(x.startswith("discogs:525272 ") for x in e._last_out.get("pool") or ()),
                        e._last_out.get("pool"))

    def test_COPY_a_failed_lookup_of_a_linked_release_is_no_evidence(self):
        d = self.album("Band P", "Album P", DISCOGS_RELEASE_ID="300937")
        http = te.FakeHttp(mb=[te.mb_release("mb-p", "Band P", "Album P")], dg=[te.dg_release(901, "Band P", "Album P")])
        http.mb["mb-p"]["relations"] = [{"type": "discogs", "target-type": "url",
                                         "url": {"resource": "https://www.discogs.com/release/525272"}}]
        real = http.get_json

        def get_json(url, host):
            if url.endswith("/releases/525272"):
                raise ma.ProviderUnavailable("discogs", "outage", "HTTP 503")
            return real(url, host)
        http.get_json = get_json
        e = self.eng(http)
        p = e.identify(e.albums_in(d)[0])
        self.assertEqual(p.status, "identified", p.why)
        self.assertFalse(any(x.startswith("discogs:525272 ") for x in e._last_out.get("pool") or ()))

    def test_COPY_a_sure_pick_your_files_name_follows_no_leads(self):
        d = self.album("Band P", "Album P", MUSICBRAINZ_ALBUMID="mb-p")
        http = te.FakeHttp(mb=[te.mb_release("mb-p", "Band P", "Album P")], dg=[te.dg_release(525272, "Band P", "X")])
        http.mb["mb-p"]["relations"] = [{"type": "discogs", "target-type": "url",
                                         "url": {"resource": "https://www.discogs.com/release/525272"}}]
        e = self.eng(http)
        e.identify(e.albums_in(d)[0])
        self.assertEqual(e._last_out.get("status"), "auto")
        # (525272 may still be read for the release's tags - its merge - but is never scored as a lead)
        self.assertFalse(any(x.startswith("discogs:525272 ") for x in e._last_out.get("pool") or ()),
                         e._last_out.get("pool"))

    def test_COPY_a_rejected_token_while_following_leads_is_raised(self):
        http = te.FakeHttp()

        def get_json(url, host):
            raise ma.AuthError("401")
        http.get_json = get_json
        mb_res = {"scored": [{"confidence": 90, "cand": {"source": "mb", "id": "m", "discogs_ids": ["525272"]}}]}
        with self.assertRaises(ma.AuthError):
            ma.mb_linked_step(http, object(), {}, {}, mb_res, {"scored": []}, {"status": "review"}, "", [])

    # ---- 2. what of your copy proves the pick -----------------------------------------------
    MATRIX = [("Matrix / Runout", "A-0547 A/C 3 A (Side B label)"), ("Matrix / Runout", "A-0547 B/C 3 A (Side A label)")]

    def view_of(self, printed, others=(), typed=""):
        pick = {"source": "mb", "id": PILZ_MB, "catnos": {"20212769"}, "catno_shown": "20 21276-9", "barcodes": set(),
                "formats": {"vinyl"}, "discogs_ids": ["525272"], "tracks": []}
        twin = {"source": "discogs", "id": "525272", "catnos": {"20212769"}, "matrix": self.MATRIX, "tracks": []}
        scored = [{"cand": pick, "confidence": 71.4}]
        dg = [{"cand": twin, "confidence": 71.4}] + [{"cand": c, "confidence": 71.4} for c in others]
        final = {"best": {"cand": pick}}
        album = {"printed_text": printed, "typed_text": typed, "file_release_ids": {}}
        return ma.release_ids_view(final, ({"scored": scored}, {"scored": dg}), album)

    def test_COPY_a_matrix_read_whole_on_your_copys_print_proves_it(self):
        e = self.eng(te.FakeHttp())
        p = self.prop(all_lengths=False)
        view = self.view_of("Seite 2\nA-0547 A/C 3A\nBestell-Nr. 20 21276-9")
        self.assertEqual(view["matrix"], ["a0547ac3a"])
        self.assertIn("matrix", e._copy_proves(p, {"release_ids": view, "scans": dict(CAT)}, []))
        careful = self.eng(te.FakeHttp(), matching={"extra_careful": True})
        self.assertEqual(careful._copy_proves(p, {"release_ids": view}, []), "")

    def test_COPY_a_matrix_only_typed_in_notes_proves_nothing(self):
        view = self.view_of("Bestell-Nr. 20 21276-9", typed="Matrix / Runout: A-0547 A/C 3 A")
        self.assertEqual(view["matrix"], [])
        self.assertEqual(self.eng(te.FakeHttp())._copy_proves(self.prop(), {"release_ids": view}, []), "")

    def test_COPY_a_stem_of_the_matrix_proves_nothing(self):
        self.assertEqual(self.view_of("A-0547 on the label")["matrix"], [])

    def test_COPY_a_matrix_another_pressing_lists_too_proves_nothing(self):
        repress = {"source": "discogs", "id": "13814072", "catnos": {"x1"}, "matrix": self.MATRIX[:1], "tracks": []}
        view = self.view_of("A-0547 A/C 3A", others=[repress])
        self.assertEqual(view["matrix"], [])
        # (nor one the release your files name lists - looked up, not scored)
        view = self.view_of("A-0547 A/C 3A")
        e = self.eng(te.FakeHttp())
        self.assertEqual(e._copy_proves(self.prop(), {"release_ids": view}, [{"matrices": ["a0547ac3a"]}]), "")

    def test_COPY_a_matrix_with_your_scans_against_the_pick_proves_nothing(self):
        view = self.view_of("A-0547 A/C 3A")
        e = self.eng(te.FakeHttp())
        against = {"state": "differs", "found": [], "against": ["barcode"]}
        self.assertEqual(e._copy_proves(self.prop(), {"release_ids": view, "scans": against}, []), "")

    def test_COPY_a_vinyl_rips_printed_catalogue_number_on_no_other_pressing_proves_it(self):
        e = self.eng(te.FakeHttp())
        p = self.prop(all_lengths=False)
        out = {"best": {"vinyl_rip": True}, "scans": dict(CAT), "release_ids": {"number_rivals": []}}
        self.assertIn("catalogue number", e._copy_proves(p, out, [{"shares_number": False}]))
        # (shared with a tied pressing - the 1971 test pressing, the 1986 reissue: only the album)
        shared = dict(out, release_ids={"number_rivals": ["discogs:37360008"]})
        self.assertEqual(e._copy_proves(p, shared, [{"shares_number": False}]), "")
        self.assertEqual(e._copy_proves(p, dict(out, scans=dict(CAT, printed=False)), []), "", "only typed")
        self.assertEqual(e._copy_proves(p, dict(out, best={}), []), "", "a CD rip: FIL-1's lengths")

    # ---- 3. your copy's printed number contradicts the files' release: it goes -----------------
    def files(self, uri):
        d = self.album("Popol Vuh", "In Den Garten Pharaos", n=2, DISCOGS_RELEASE_ID="300937",
                       DISCOGS_URI=f"https://www.discogs.com/release/{uri}", DISCOGS_COUNTRY="Italy",
                       LABEL="PDU", MUSICBRAINZ_ALBUMID=PILZ_MB)
        return sorted(str(f) for f in d.glob("*.flac"))

    PICK = {"source": "mb", "id": PILZ_MB, "catnos": ["20212769"], "catnos_raw": ["20 21276-9"], "barcodes": [],
            "formats": ["vinyl"], "links": ["525272"], "label": "Pilz", "country": "DE", "year": 1971, "layout": [2]}
    PDU = {"source": "discogs", "id": "300937", "catnos": ["pldsq6009"], "catnos_raw": ["Pld. SQ 6009"],
           "barcodes": [], "formats": ["vinyl"], "links": [], "label": "PDU", "country": "Italy", "year": 1975}

    def test_COPY_the_contradicted_release_goes_with_its_fields(self):
        files = self.files(300937)
        e = self.eng(te.FakeHttp())
        p = self.prop(files=files, targets=[{} for _f in files])
        side = {"musicbrainz_albumid": {PILZ_MB}, "discogs_release_id": {"525272"}}
        gone = e._copy_contradicts(p, {"scans": dict(CAT)}, {"discogs_release_id": {"300937": "x"}},
                                   [dict(self.PDU)], self.PICK, side)
        self.assertIn("discogs_release_id", gone[files[0]])
        self.assertIn("discogs_uri", gone[files[0]])
        self.assertIn("label", gone[files[0]], "PDU is that pressing's label, not the pick's")
        self.assertNotIn("musicbrainz_albumid", gone[files[0]])
        # (a read not printed on your copy - fuzzy, typed: nothing is removed)
        self.assertEqual(e._copy_contradicts(p, {"scans": dict(CAT, printed=False)},
                                             {"discogs_release_id": {"300937": "x"}}, [dict(self.PDU)],
                                             self.PICK, side), {})
        # (your copy prints that release's number too: nothing contradicts it)
        both = {"scans": dict(CAT), "release_ids": {"on_copy": {"catnos": ["pldsq6009"], "barcodes": []}}}
        self.assertEqual(e._copy_contradicts(p, both, {"discogs_release_id": {"300937": "x"}}, [dict(self.PDU)],
                                             self.PICK, side), {})
        # (nothing printed read on the copy: nothing contradicts it)
        self.assertEqual(e._copy_contradicts(p, {"scans": {"state": "agrees", "found": ["label"]}},
                                             {"discogs_release_id": {"300937": "x"}}, [dict(self.PDU)],
                                             self.PICK, side), {})

    def test_COPY_a_twin_sharing_the_barcode_its_number_written_otherwise_is_kept(self):
        files = self.files(300937)
        e = self.eng(te.FakeHttp())
        pick = dict(self.PICK, barcodes=["4006759"+"123456"])
        twin = dict(self.PDU, catnos=["2021276"], catnos_raw=["20 21276"], barcodes=list(pick["barcodes"]))
        view = {"pick": pick, "cands": {"discogs_release_id": {"300937": twin}}, "mb_links": {}}
        p = pl.Proposal(source_dir=str(Path(files[0]).parent), files=files, status="identified", tier="exact",
                        confidence=71.4, release_id=PILZ_MB,
                        targets=[{"album": ["A"], "musicbrainz_albumid": [PILZ_MB], "title": [f"T{i}"]} for i in range(2)])
        p = e._one_release(p, {"release_ids": view, "scans": dict(CAT)},
                           {"file_release_ids": {"discogs_release_id": ["300937"]}})
        ch = pl.build([p], e.profile)["albums"][0]["files"][0]["changes"]
        self.assertNotIn("discogs_release_id", ch, "a release sharing the barcode is no contradiction")

    def test_COPY_discogs_fields_of_the_picks_twin_stay(self):
        # (the file's Discogs fields are 525272's - TagMender's earlier job wrote them beside the
        # other tagger's ID: only the ID goes)
        files = self.files(525272)
        e = self.eng(te.FakeHttp())
        p = self.prop(files=files, targets=[{} for _f in files])
        side = {"musicbrainz_albumid": {PILZ_MB}, "discogs_release_id": {"525272"}}
        gone = e._copy_contradicts(p, {"scans": dict(CAT)}, {"discogs_release_id": {"300937": "x"}},
                                   [dict(self.PDU)], self.PICK, side)
        self.assertIn("discogs_release_id", gone[files[0]])
        self.assertNotIn("discogs_uri", gone[files[0]])

    def test_COPY_album_only_removes_the_contradicted_id_end_to_end(self):
        files = self.files(300937)
        e = self.eng(te.FakeHttp())
        view = {"pick": dict(self.PICK), "cands": {"discogs_release_id": {"300937": dict(self.PDU)}},
                "mb_links": {"525272": [PILZ_MB]}, "number_rivals": ["discogs:37360008"]}
        p = pl.Proposal(source_dir=str(Path(files[0]).parent), files=files, status="identified", tier="exact",
                        confidence=71.4, release_id=PILZ_MB,
                        targets=[{"album": ["In den Gärten Pharaos"], "musicbrainz_albumid": [PILZ_MB],
                                  "discogs_release_id": ["525272"], "title": [f"T{i}"]} for i in range(2)])
        p = e._one_release(p, {"release_ids": view, "scans": dict(CAT), "best": {"vinyl_rip": True}},
                           {"file_release_ids": {"discogs_release_id": ["300937"]}})
        self.assertEqual(p.tier, "album_only", p.notes)
        ch = pl.build([p], e.profile)["albums"][0]["files"][0]["changes"]
        self.assertIsNone(ch.get("discogs_release_id", "kept"), "never kept beside your copy's evidence")
        self.assertNotIn("musicbrainz_albumid", ch, "the pick's MusicBrainz ID TagMender wrote stays")

    # ---- REP-1: TagMender's own writes of the pick's twin are never taken back ---------------
    def test_COPY_a_kept_discogs_id_beside_the_twins_fields_takes_nothing_back(self):
        files = self.files(525272)
        e = self.eng(te.FakeHttp())
        writes = {f: {"discogs_release_id": (("300937",), ("300937",)),
                      "discogs_uri": ((), ("https://www.discogs.com/release/525272",)),
                      "discogs_country": ((), ("Italy",))} for f in files}
        for f in files:
            tagio.write(f, {"discogs_country": ["Italy"], "discogs_uri": ["https://www.discogs.com/release/525272"]})
        side = {"musicbrainz_albumid": {PILZ_MB}, "discogs_release_id": {"525272"}}
        with mock.patch.object(ma, "tagmender_pressing_writes", return_value=writes):
            back = e._restore_own(files, {"discogs_release_id": {"300937": "x"}}, [dict(self.PDU)],
                                  self.PICK, side)
        self.assertEqual(back, {})


if __name__ == "__main__":
    unittest.main()
