# CRD-1 (docs/MATCHING_RULES.md): a Discogs credit listing the tracks it applies to (its
# `tracks` field: "CD1-5, CD2-5, CD3-4", "A1 to A3") is written only on those tracks; one
# listing none stays album-wide; one whose list cannot be matched is left off. Pink Floyd - The
# Dark Side Of The Moon Immersion (Discogs 3137117): Clare Torry, composer of "The Great Gig In
# The Sky" only, was written as the composer of all 36 files. Synthetic releases: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
from test_engine import Base, FakeHttp, dg_release, mb_release  # noqa: E402

DM = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
TORRY = {"name": "Clare Torry", "role": "Composed By [Vocals]", "tracks": "CD1-2, CD2-2"}


def tr(pos, title, dur="4:00"):
    return {"position": pos, "title": title, "duration": dur, "type_": "track"}


def work(title, subs, dur=""):
    return {"position": "", "title": title, "duration": dur, "type_": "index", "sub_tracks": subs}


def box(credits, bluray=True, works=False):
    """2 CDs of 3 tracks (and a Blu-ray of TV footage); WORKS: CD 2's track 3 a work in lettered
    parts listed without lengths."""
    cd2_3 = work("Suite", [tr("CD2-3a", "Pig", ""), tr("CD2-3b", "Door", "")]) if works else tr("CD2-3", "Six")
    rel = {"id": 7, "title": "Recordings", "artists": [{"name": "The Band"}], "year": 2026, "country": "Europe",
           "formats": [{"name": "CD", "qty": "2", "descriptions": ["Album"]}, {"name": "Box Set", "qty": "1"}],
           "labels": [{"name": "Label", "catno": "123"}], "identifiers": [],
           "extraartists": list(credits),
           "tracklist": [tr("CD1-1", "One"), tr("CD1-2", "Two"), tr("CD1-3", "Three"),
                         tr("CD2-1", "Four"), tr("CD2-2", "Five"), cd2_3]}
    if bluray:
        rel["formats"].insert(1, {"name": "Blu-ray", "qty": "1", "descriptions": ["Compilation", "Stereo"]})
        rel["tracklist"] += [{"position": "", "title": "The Video Vaults", "type_": "heading"},
                             tr("BD1-1", "Facelift (TV)", ""), tr("BD1-2", "Teeth (TV)", "")]
    return rel


def tags(rel, whole_works=False):
    """Each track's tags as the engine writes a release from Discogs alone."""
    album, tracks, _n = engine.discogs_parts(copy.deepcopy(rel), DM, None, None, whole_works=whole_works)
    return engine.discogs_only_tags(album, tracks)


def field(rel, name, **kw):
    return [t.get(name) for t in tags(rel, **kw)]


class Positions(unittest.TestCase):
    POS = ["a1", "a2", "a3", "b1", "b2", "1", "2", "3", "5"]

    def test_CRD1_a_list_of_positions(self):
        self.assertEqual(DM.credit_track_positions("A1, B2", self.POS), ["a1", "b2"])

    def test_CRD1_ranges(self):
        for spec in ("A1 to A3", "A1-A3", "A1 – A3", "A1 to 3"):
            self.assertEqual(DM.credit_track_positions(spec, self.POS), ["a1", "a2", "a3"], spec)
        self.assertEqual(DM.credit_track_positions("1-3, 5", self.POS), ["1", "2", "3", "5"])
        # (across sides, in the release's order)
        self.assertEqual(DM.credit_track_positions("A3 to B1", self.POS), ["a3", "b1"])

    def test_CRD1_a_hyphen_naming_a_position_is_that_position(self):
        pos = ["cd1-1", "cd1-2", "cd1-3", "cd1-5"]
        self.assertEqual(DM.credit_track_positions("CD1-5", pos), ["cd1-5"])
        self.assertEqual(DM.credit_track_positions("CD1-1 to CD1-3", pos), ["cd1-1", "cd1-2", "cd1-3"])

    def test_CRD1_a_work_listed_by_its_movements_is_named_by_its_own_position(self):
        # (Soft Machine - Slightly All The Time: "BD2-18, BD2-19" over "BD2-19a", "BD2-19b")
        pos = ["bd2-17", "bd2-18", "bd2-19a", "bd2-19b", "cd3-1"]
        self.assertEqual(DM.credit_track_positions("BD2-18, BD2-19", pos), ["bd2-18", "bd2-19a", "bd2-19b"])
        self.assertEqual(DM.credit_track_positions("BD2-17 to BD2-19", pos), pos[:4])

    def test_CRD1_unmatched_lists_name_nothing(self):
        for spec in ("Side C", "A1 to A9", "A3 to A1", "C1, A1", "", "  "):
            self.assertIsNone(DM.credit_track_positions(spec, self.POS), spec)


class Scoped(unittest.TestCase):
    def test_CRD1_a_scoped_composer_is_written_on_its_tracks_only(self):
        self.assertEqual(field(box([TORRY]), "composer"),
                         [None, ["Clare Torry"], None, None, ["Clare Torry"], None])

    def test_CRD1_a_range_names_each_track_between(self):
        rel = box([{"name": "Alan Parsons", "role": "Engineer", "tracks": "CD1-1 to CD1-3"},
                   {"name": "Dick Parry", "role": "Saxophone", "tracks": "CD1-3 to CD2-1"}])
        self.assertEqual(field(rel, "engineer"), [["Alan Parsons"]] * 3 + [None] * 3)
        self.assertEqual(field(rel, "performer:saxophone"), [None, None, ["Dick Parry"], ["Dick Parry"], None, None])

    def test_CRD1_a_credit_of_video_chapters_only_is_on_no_file(self):
        rel = box([{"name": "Jacques Bourton", "role": "Film Director [Directed By]", "tracks": "BD1-1 to BD1-2"},
                   {"name": "Hugh Hopper", "role": "Bass", "tracks": "CD2-1 to CD2-3, BD1-1 to BD1-2"}])
        out = tags(rel)
        self.assertEqual(len(out), 6)                      # (the chapters are no files: MED-16)
        self.assertFalse([t for t in out if any(k.startswith("discogs_credit_film") for k in t)], out)
        self.assertEqual([t.get("performer:bass") for t in out], [None] * 3 + [["Hugh Hopper"]] * 3)

    def test_CRD1_an_unmatched_track_list_is_left_off(self):
        for spec in ("Bonus disc", "CD4-1", "CD1-1, CD9-9"):
            rel = box([{"name": "Clare Torry", "role": "Composed By", "tracks": spec}])
            self.assertEqual(field(rel, "composer"), [None] * 6, spec)

    def test_CRD1_a_credit_without_tracks_is_album_wide(self):
        rel = box([{"name": "Roger Waters", "role": "Composed By", "tracks": ""}, TORRY])
        self.assertEqual(field(rel, "composer")[:2], [["Roger Waters"], ["Roger Waters", "Clare Torry"]])
        self.assertEqual(field(rel, "composer")[2], ["Roger Waters"])
        self.assertEqual(DM.extract_discogs_fields(box([{"name": "Chris Thomas", "role": "Mixed By"}]))["mixer"],
                         ["Chris Thomas"])

    def test_CRD1_the_album_fields_hold_no_scoped_credit(self):
        album, _t, _n = engine.discogs_parts(box([TORRY]), DM, None, None)
        self.assertNotIn("composer", album)

    def test_CRD1_a_work_read_as_one_track_takes_its_movements_credits(self):
        # TRK-5: the work (its parts lettered, no lengths) is one file; a credit naming its parts
        # names that file, one naming the disc's other tracks does not
        rel = box([{"name": "Mark Charig", "role": "Cornet", "tracks": "CD2-3a to CD2-3b"},
                   {"name": "Clare Torry", "role": "Composed By", "tracks": "CD2-2"}], works=True)
        whole = tags(rel, whole_works=True)
        self.assertEqual([t["title"] for t in whole], [["One"], ["Two"], ["Three"], ["Four"], ["Five"], ["Suite"]])
        self.assertEqual([t.get("performer:cornet") for t in whole], [None] * 5 + [["Mark Charig"]])
        self.assertEqual([t.get("composer") for t in whole], [None] * 4 + [["Clare Torry"], None])
        # (as listed, the parts are tracks of their own: each takes it)
        listed = tags(rel)
        self.assertEqual([t.get("performer:cornet") for t in listed], [None] * 5 + [["Mark Charig"]] * 2)

    def test_CRD1_one_disc_of_the_set_keeps_its_own_credits(self):
        # BOX-2: one disc's tracks taken out of the set keep the credits of their own positions
        album, tracks, _n = engine.discogs_parts(box([TORRY]), DM, None, None)
        disc2 = [t for t in tracks if t["discnumber"] == "2"]
        self.assertEqual([t.get("composer") for t in engine.discogs_only_tags(album, disc2)],
                         [None, ["Clare Torry"], None])


class Merged(Base):
    """The whole engine: MusicBrainz's release with its Discogs twin, a file missing (slots)."""

    def rel_pair(self):
        dg = dg_release(4261, "Band C", "Album C")
        dg["identifiers"] = [{"type": "Barcode", "value": "0123456789012"}]
        dg["extraartists"] = [{"name": "Clare Torry", "role": "Composed By", "tracks": "3"},
                              {"name": "Alan Parsons", "role": "Engineer", "tracks": ""}]
        mb = mb_release("mb-c", "Band C", "Album C", discogs=4261)
        return mb, dg

    def test_CRD1_a_scoped_credit_follows_its_track_when_files_are_missing(self):
        d = self.album("Band C", "Album C", barcode="0123456789012")
        os.remove(d / "02.flac")
        mb, dg = self.rel_pair()
        eng = self.engine(FakeHttp(mb=[mb], dg=[dg]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier), ("identified", "exact"), p.why)
        self.assertEqual([t["tracknumber"] for t in p.targets], [["1"], ["3"]])
        self.assertEqual([t.get("composer") for t in p.targets], [None, ["Clare Torry"]], p.targets)
        self.assertEqual([t.get("engineer") for t in p.targets], [["Alan Parsons"]] * 2)
        self.assertEqual(p.provenance.get("composer"), "discogs", p.provenance)

    def composers(self, n, tracks, merge_mode):
        d = self.album(f"Band {n}", f"Album {n}", barcode="0123456789012")
        mb, dg = self.rel_pair()
        mb["id"], mb["title"], dg["id"], dg["title"] = f"mb-{n}", f"Album {n}", 4270 + n, f"Album {n}"
        mb["relations"][0]["url"]["resource"] = f"https://www.discogs.com/release/{4270 + n}"
        dg["extraartists"][0]["tracks"] = tracks
        for i, t in enumerate(mb["media"][0]["tracks"]):
            t["recording"]["relations"] = [{
                "target-type": "work", "type": "performance", "direction": "forward",
                "work": {"id": f"w{i}", "title": f"T{i}", "relations": [
                    {"target-type": "artist", "type": "composer", "direction": "backward",
                     "artist": {"id": "c1", "name": "Rick Wright", "sort-name": "Wright, Rick"}}]}}]
        eng = self.engine(FakeHttp(mb=[mb], dg=[dg]), discogs_enrichment={"merge_mode": merge_mode})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "musicbrainz+discogs"), p.why)
        return [t.get("composer") for t in p.targets], p.provenance.get("composer")

    def test_CRD1_musicbrainz_and_discogs_settle_a_scoped_field_as_an_album_wide_one(self):
        # the merge mode decides on the credit's own track exactly as it decided for an album-wide
        # credit before; the other tracks keep MusicBrainz's composer alone
        for k, mode in enumerate(("combine", "fill_gaps", "override")):
            scoped, _w = self.composers(2 * k + 1, "3", mode)
            wide, _w2 = self.composers(2 * k + 2, "", mode)
            self.assertEqual(scoped[:2], [["Rick Wright"]] * 2, mode)
            self.assertEqual(scoped[2], wide[2], mode)
            expect = {"fill_gaps": ["Rick Wright"], "override": ["Clare Torry"]}.get(mode, ["Clare Torry", "Rick Wright"])
            self.assertEqual(sorted(scoped[2]), expect, (mode, scoped))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
