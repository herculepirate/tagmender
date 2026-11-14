# CRD-1a/b/c (docs/MATCHING_RULES.md), decided 2026-11-14:
# a) a credit's track list naming some positions the release does not have keeps the ones it
#    does (Soft Machine - Slightly All The Time, Discogs 38260923: Phil Howard's drums list his CD
#    tracks and "BD2-5 to BD2-24" on a Blu-ray of 19 tracks - the whole credit was left off);
# b) a credit whose role names a video disc ("Authoring [Blu Ray Authoring]", "Film Director")
#    is written on that disc's tracks only, so a rip holding none of them has it on no file;
# c) a field Discogs credits on some tracks only never replaces MusicBrainz's value of that track
#    (Pink Floyd - The Dark Side Of The Moon Immersion, Discogs 3137117: "Composed By [Vocals]"
#    Clare Torry took Richard Wright off "The Great Gig In The Sky").
# Synthetic releases and files: no network, no library files.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import copy  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_engine import Base as EngBase, FakeHttp, dg_release, mb_release  # noqa: E402
from test_executor import target  # noqa: E402
from test_v0860_track_credits import DM, box, tags  # noqa: E402
from test_v0861_stale_credits import READ_DG, _Stale  # noqa: E402

AUTHORING = [{"name": "Richard Osborn", "role": "Authoring [Blu Ray Authoring]", "tracks": ""},
             {"name": "Type40 Creative", "role": "Authoring [Blu Ray Authoring]", "tracks": ""}]
PRODUCER = {"name": "Mike Producer", "role": "Producer", "tracks": ""}


def field(rel, name):
    return [t.get(name) for t in tags(rel)]


def bluray_audio(credits):
    """box() with its Blu-ray an audio disc: its tracks are files of the rip (MED-16)."""
    rel = box(credits)
    rel["formats"][1]["descriptions"] = ["Blu-ray Audio", "Stereo"]
    return rel


class PartialLists(unittest.TestCase):
    """CRD-1a"""
    POS = ["cd1-1", "cd1-2", "cd1-3", "cd2-1", "cd2-2",
           "bd2-4", "bd2-5", "bd2-18", "bd2-19a", "bd2-19b", "cd3-1"]

    def test_CRD1a_a_range_past_its_medium_runs_to_the_medium_end(self):
        # (Phil Howard: "BD2-5 to BD2-24" on a Blu-ray of 19 tracks)
        self.assertEqual(DM.credit_track_positions("BD2-5 to BD2-24", self.POS),
                         ["bd2-5", "bd2-18", "bd2-19a", "bd2-19b"])
        self.assertEqual(DM.credit_track_positions("CD1-2 to CD1-9", self.POS), ["cd1-2", "cd1-3"])
        self.assertEqual(DM.credit_track_positions("CD1-2 to 9", self.POS), ["cd1-2", "cd1-3"])
        self.assertEqual(DM.credit_track_positions("A2 to A9", ["a1", "a2", "a3", "b1"]), ["a2", "a3"])
        self.assertEqual(DM.credit_track_positions("2-9", ["1", "2", "3", "a1"]), ["2", "3"])

    def test_CRD1a_an_open_range_from_a_movement_ends_with_its_work(self):
        pos = ["cd6-1", "cd6-2", "cd6-3a", "cd6-3b", "cd6-3c", "cd6-4", "cd6-5"]
        self.assertEqual(DM.credit_track_positions("CD6-3a to CD6-3k", pos), ["cd6-3a", "cd6-3b", "cd6-3c"])
        self.assertEqual(DM.credit_track_positions("CD6-3b to CD6-3k", pos), ["cd6-3b", "cd6-3c"])
        self.assertEqual(DM.credit_track_positions("A1a to A1f", ["a1a", "a1b", "a2", "a3"]), ["a1a", "a1b"])
        self.assertEqual(DM.credit_track_positions("3a to 3k", ["1", "3a", "3b", "4", "5"]), ["3a", "3b"])
        self.assertEqual(DM.credit_track_positions("1.i to 1.ix", ["1.i", "1.ii", "2", "3"]), ["1.i", "1.ii"])
        # (a range of whole tracks still runs to its medium's end)
        self.assertEqual(DM.credit_track_positions("CD6-2 to CD6-9", pos), pos[1:])

    def test_CRD1a_a_list_keeps_the_positions_it_can_match(self):
        self.assertEqual(DM.credit_track_positions("CD1-1, CD2-2, CD9-4, BD2-5 to BD2-24", self.POS),
                         ["cd1-1", "cd2-2", "bd2-5", "bd2-18", "bd2-19a", "bd2-19b"])
        self.assertEqual(DM.credit_track_positions("CD9-1 to CD9-3, CD3-1", self.POS), ["cd3-1"])

    def test_CRD1a_a_list_matching_nothing_is_still_left_off(self):
        for spec in ("CD9-1, CD9-2", "CD9-1 to CD9-9", "Bonus disc", "CD1-3 to CD1-1"):
            self.assertIsNone(DM.credit_track_positions(spec, self.POS), spec)
            rel = box([{"name": "Phil Howard", "role": "Drums", "tracks": spec}])
            self.assertEqual(field(rel, "performer:drums"), [None] * 6, spec)

    def test_CRD1a_the_credit_is_written_on_the_tracks_it_matches(self):
        # his CD tracks and a Blu-ray range running past its disc: on his CD files
        rel = box([{"name": "Phil Howard", "role": "Drums, Percussion",
                    "tracks": "CD1-2 to CD1-3, CD2-1, BD1-2 to BD1-24"}])
        self.assertEqual(field(rel, "performer:drums"), [None, ["Phil Howard"], ["Phil Howard"],
                                                         ["Phil Howard"], None, None])
        self.assertEqual(field(rel, "performer:percussion"), field(rel, "performer:drums"))


class VideoRoles(unittest.TestCase):
    """CRD-1b"""

    def test_CRD1b_the_role_names_its_video_disc(self):
        for role, kind in (("Authoring [Blu Ray Authoring]", "blu-ray"), ("Authoring [BD]", "blu-ray"),
                           ("Mastered By [Blu-ray]", "blu-ray"), ("Blu-ray Authoring", "blu-ray"),
                           ("DVD Authoring", "dvd"), ("Authoring [DVD]", "dvd"), ("Mastered By [DVD-Video]", "dvd"),
                           ("Film Director", "video"), ("Film Editor", "video"), ("Film Producer", "video"),
                           ("Video Editor", "video"), ("Authoring", "video"), ("Cinematographer", "video"),
                           ("Menu Design", "video"), ("Subtitles", "video"),
                           ("Directed By [Video]", "video"), ("Edited By [Video]", "video"),
                           ("Editor [Video]", "video"), ("Producer [Video]", "video"), ("Producer [Film]", "video"),
                           ("Camera [Video]", "video"), ("Video By", "video"), ("Filmed By", "video"),
                           ("Videography By", "video")):
            self.assertEqual(DM.video_disc_of_role(role), kind, role)
        for role in ("Producer", "Mixed By [Audio]", "Mastered By [Audio & Video]",
                     "Music By [Film Score]", "Engineer [Brooklyn, NY]", "Liner Notes"):
            self.assertEqual(DM.video_disc_of_role(role), "", role)

    def test_CRD1b_a_role_naming_an_audio_carrier_too_stays_album_wide(self):
        for role in ("Mastered By [CD & DVD]", "Mastered By [CD, Blu-ray]", "Mastered By [CD/DVD]",
                     "Mixed By [Stereo & 5.1 DVD]", "Mastered By [Vinyl & DVD]", "Authoring [Blu-ray Audio]",
                     "Authoring [SACD]", "Mastered By [LP, DVD]"):
            self.assertEqual(DM.video_disc_of_role(role), "", role)
        rel = box([{"name": "Mike Mastering", "role": "Mastered By [CD & DVD]", "tracks": ""}])
        self.assertEqual(field(rel, "engineer"), [["Mike Mastering"]] * 6)

    def test_CRD1b_film_video_and_bd_words_alone_stay_album_wide(self):
        for role in ("Music By [Film]", "Score [Film]", "Photography By [Film Stills]", "Mixed By [Video Mix]",
                     "Music By [Video]", "Composed By [Film]", "Vocals [BD]", "Mixed By [BD Mix]", "Edited By [Film & Video]"):
            self.assertEqual(DM.video_disc_of_role(role), "", role)
        rel = box([{"name": "Ann Composer", "role": "Music By [Film]", "tracks": ""}])
        self.assertEqual(field(rel, "composer"), [["Ann Composer"]] * 6)

    def test_CRD1b_a_video_role_is_on_no_file_of_a_cd_rip(self):
        rel = box(AUTHORING + [{"name": "Jacques Bourton", "role": "Film Director", "tracks": ""}])
        out = tags(rel)
        self.assertEqual(len(out), 6)                          # (the Blu-ray's chapters: no files)
        self.assertFalse([t for t in out if "performer:authoring" in t or "performer:film director" in t], out)
        album, _t, _n = __import__("engine").discogs_parts(copy.deepcopy(rel), DM, None, None)
        self.assertNotIn("performer:authoring", album)
        # (a release of CDs only: no disc of the role's at all)
        cds = box(AUTHORING, bluray=False)
        self.assertEqual(field(cds, "performer:authoring"), [None] * 6)

    def test_CRD1b_kept_on_the_blu_ray_audio_of_the_rip(self):
        rel = bluray_audio(AUTHORING + [{"name": "Dee Vee", "role": "Authoring [DVD]", "tracks": ""}])
        out = tags(rel)
        self.assertEqual(len(out), 8)                          # (6 CD tracks + the Blu-ray's 2)
        self.assertEqual([t.get("performer:authoring") for t in out],
                         [None] * 6 + [["Richard Osborn", "Type40 Creative"]] * 2)
        # (a DVD's role is not the Blu-ray's)
        self.assertFalse([t for t in out if "Dee Vee" in str(t)], out)
        # a Blu-ray Audio release on its own: every track
        one = box(AUTHORING, bluray=False)
        one["formats"] = [{"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio", "Stereo"]}]
        self.assertEqual(field(one, "performer:authoring"), [["Richard Osborn", "Type40 Creative"]] * 6)

    def test_CRD1b_a_plain_album_wide_credit_stays_on_every_track(self):
        self.assertEqual(field(box([PRODUCER] + AUTHORING), "producer"), [["Mike Producer"]] * 6)
        # (a credit naming a video role beside another: only the video role is the disc's)
        rel = box([{"name": "Mike Producer", "role": "Producer, Film Director", "tracks": ""}])
        self.assertEqual(field(rel, "producer"), [["Mike Producer"]] * 6)
        self.assertEqual(field(rel, "performer:film director"), [None] * 6)

    def test_CRD1b_a_video_role_listing_its_tracks_follows_the_list(self):
        rel = box([{"name": "Hugh Hopper", "role": "Bass [Film]", "tracks": "CD2-1"}])
        self.assertEqual(field(rel, "performer:bass"), [None] * 3 + [["Hugh Hopper"]] + [None] * 2)


class REP2Interplay(_Stale):
    """CRD-1b with REP-2: the album-wide authoring credit an earlier TagMender job wrote on a CD
    rip is taken back on the next plan."""

    def test_CRD1b_REP2_the_earlier_authoring_credit_is_taken_back(self):
        now = tags(box([PRODUCER] + AUTHORING))[:2]
        credits = [{k: v for k, v in t.items() if ma.credit_field(k)} for t in now]
        self.assertEqual(credits, [{"producer": ["Mike Producer"]}] * 2)
        before = {"producer": ["Mike Producer"], "performer:authoring": ["Richard Osborn", "Type40 Creative"]}
        prof = self.profile()
        first = pl.build([self.proposal(targets=[target(1, "First", **before), target(2, "Second", **before)])], prof)
        self.assertEqual(executor.Executor(first).run()["counts"], {"done": 1})
        self._fresh()
        p = pl.build([self.proposal(targets=[target(1, "First", **credits[0]), target(2, "Second", **credits[1])],
                                    credits_read=dict(READ_DG))], prof)
        for f in (self.f1, self.f2):
            rows, _fe = self.rows(p, f)
            self.assertEqual(rows["performer:authoring"]["action"], "removed", rows)
            self.assertNotIn("producer", rows)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertNotIn("performer:authoring", tagio.read(self.f1))
        self.assertEqual(tagio.read(self.f2).get("producer"), ["Mike Producer"])


class REP2BluRayAudio(_Stale):
    """CRD-1b with REP-2 on a Blu-ray Audio rip: the video disc's credit, kept for its audio
    files, is not taken back from them."""

    def test_CRD1b_REP2_a_kept_bluray_credit_stays_on_the_bluray_audio_files(self):
        one = box([PRODUCER] + AUTHORING, bluray=False)
        one["formats"] = [{"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio", "Stereo"]}]
        now = tags(one)[:2]
        credits = [{k: v for k, v in t.items() if ma.credit_field(k)} for t in now]
        self.assertEqual([c.get("performer:authoring") for c in credits],
                         [["Richard Osborn", "Type40 Creative"]] * 2)
        prof = self.profile()
        first = pl.build([self.proposal(targets=[target(1, "First", **credits[0]),
                                                 target(2, "Second", **credits[1])])], prof)
        self.assertEqual(executor.Executor(first).run()["counts"], {"done": 1})
        self._fresh()
        p = pl.build([self.proposal(targets=[target(1, "First", **credits[0]), target(2, "Second", **credits[1])],
                                    credits_read=dict(READ_DG))], prof)
        for f in (self.f1, self.f2):
            rows, _fe = self.rows(p, f)
            self.assertNotIn("performer:authoring", rows)
        self.assertEqual(tagio.read(self.f1).get("performer:authoring"), ["Richard Osborn", "Type40 Creative"])


class ComposerPrecedence(EngBase):
    """CRD-1c: MusicBrainz's composer of a track stands against a Discogs composer credited on
    that track only; where MusicBrainz gives none, the Discogs credit fills it."""

    def identify(self, n, mode, mb_composers):
        d = self.album(f"Band {n}", f"Album {n}", barcode="0123456789012")
        dg = dg_release(4880 + n, f"Band {n}", f"Album {n}")
        dg["identifiers"] = [{"type": "Barcode", "value": "0123456789012"}]
        dg["extraartists"] = [{"name": "Clare Torry", "role": "Composed By [Vocals]", "tracks": "2"},
                              {"name": "Alan Parsons", "role": "Engineer", "tracks": "2"},
                              {"name": "Roger Waters", "role": "Lyrics By", "tracks": ""}]
        mb = mb_release(f"mb-{n}", f"Band {n}", f"Album {n}", discogs=4880 + n)
        for i, t in enumerate(mb["media"][0]["tracks"]):
            t["recording"]["relations"] = [{
                "target-type": "work", "type": "performance", "direction": "forward",
                "work": {"id": f"w{n}-{i}", "title": f"T{i}", "relations": [
                    {"target-type": "artist", "type": "composer", "direction": "backward",
                     "artist": {"id": f"c{k}", "name": who, "sort-name": who}} for k, who in enumerate(mb_composers)]}}]
        eng = self.engine(FakeHttp(mb=[mb], dg=[dg]), discogs_enrichment={"merge_mode": mode})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "musicbrainz+discogs"), p.why)
        return p.targets

    def test_CRD1c_musicbrainz_composer_stands_on_the_scoped_track(self):
        targets = self.identify(1, "override", ["Richard Wright", "Clare Torry"])
        self.assertEqual([t.get("composer") for t in targets], [["Richard Wright", "Clare Torry"]] * 3)
        # (a field MusicBrainz gives no track: the scoped credit is on its own track)
        self.assertEqual([t.get("engineer") for t in targets], [None, ["Alan Parsons"], None])
        # (an album-wide Discogs credit still follows Override)
        self.assertEqual({str(t.get("lyricist")) for t in targets}, {str(["Roger Waters"])})

    def test_CRD1c_only_work_credits_keep_musicbrainz(self):
        # (a recording credit - engineer - follows Override on its track as before)
        import engine
        mb = {"composer": ["Richard Wright", "Clare Torry"], "engineer": ["Peter James"]}
        track = {"~credits": {"composer": ["Clare Torry"], "engineer": ["Joel Plante"]}}
        es = dict(merge_mode="override", split_collaborations=False, track_tags=True, protected_tags="")
        out = engine.enrich(dict(mb), {"album": "X"}, track, es, 1, DM)
        self.assertEqual(out["composer"], ["Richard Wright", "Clare Torry"])
        self.assertEqual(out["engineer"], ["Joel Plante"])
        # (with an album-wide Discogs composer, Override replaces on every track as before)
        out = engine.enrich(dict(mb), {"album": "X", "composer": ["Roger Waters"]},
                            {"~credits": {"composer": ["Roger Waters", "Clare Torry"]}}, es, 1, DM)
        self.assertEqual(out["composer"], ["Roger Waters", "Clare Torry"])

    def test_CRD1c_combine_still_adds_the_credit_beside_musicbrainz(self):
        targets = self.identify(2, "combine", ["Richard Wright"])
        self.assertEqual(sorted(targets[1]["composer"]), ["Clare Torry", "Richard Wright"])
        self.assertEqual(targets[0]["composer"], ["Richard Wright"])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
