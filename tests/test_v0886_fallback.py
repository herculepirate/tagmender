# Fallback tiers never end with nothing for an album the databases know (decided 2026-11-19, after
# The Minimalists: album artist "Various", every length agreeing, nothing written).
#   FBK-1: artist OR title short of an automatic pick's, the tracks proving the album: Partial.
#   FBK-2: a rival that does not fit your files does not refuse the album's Partial tags.
#   FBK-3: the release's tracks not lining up with the files: Partial, not nothing.
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_partial import (Base, DG_BEST, FakeHttp, MB_BEST, PRESSING, dg_master, dg_release, ma,  # noqa: E402
                          mb_release, pool_of, review)


class FBK1_TracksProveTheAlbum(Base):
    def test_FBK1_album_artist_various_every_track_agreeing_is_partial(self):
        d = self.album(artist="Various", n=3)
        http = FakeHttp(mb=[mb_release("mb-a", artist="Composer A, Composer B; Ensemble")])
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, artist="Composer A, Composer B; Ensemble")), d=d)
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "partial", ""), self.logged(eng))
        t = p.targets[1]
        self.assertEqual((t["title"], t["musicbrainz_recordingid"]), (["T1"], ["rec-1"]))
        for k in PRESSING:
            self.assertNotIn(k, t)

    def test_FBK1_artist_and_title_both_differing_is_another_album(self):
        d = self.album(artist="Various", title="Something Else", n=3)
        http = FakeHttp(mb=[mb_release("mb-a", artist="Composer A")])
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, artist="Composer A")), d=d)
        self.rejected(eng, p, "artist or title agree only partly")

    def test_FBK1_a_track_title_that_differs_is_no_proof(self):
        d = self.album(artist="Various", n=3)
        http = FakeHttp(mb=[mb_release("mb-a", artist="Composer A", titles=["T0", "Other", "T2"])])
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, artist="Composer A")), d=d)
        self.rejected(eng, p, "artist or title agree only partly")


    def test_FBK1_a_sound_alike_artist_is_another_album(self):
        # (The Hit Crew's "Thriller": the original's titles and lengths, nobody of its credit)
        d = self.album(artist="The Hit Crew", n=5)
        http = FakeHttp(mb=[mb_release("mb-a", artist="Michael Jackson", lens=[10000, 20000, 30000, 40000, 50000])])
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, artist="Michael Jackson")), d=d)
        self.rejected(eng, p, "artist or title agree only partly")

    def test_FBK1_a_title_differing_needs_five_tracks(self):
        d = self.album(title="Another Title", n=3)
        http = FakeHttp(mb=[mb_release("mb-a")])
        eng, p, _d = self.run_partial(http, review(MB_BEST), d=d)
        self.rejected(eng, p, "artist or title agree only partly")

    def test_FBK1_web_entries_need_artist_and_title(self):
        eng = self.engine(FakeHttp())
        album = {"artist": "Various", "title": "Album P", "year": 2005, "discs": 1, "barcodes": set(),
                 "catnos": set(), "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 10.0 * (i + 1)}
                                             for i in range(3)]}
        cand = {"source": "web", "id": "w", "artist": "Composer A", "title": "Album P", "year": 2005,
                "catnos": set(), "barcodes": set(), "disc_count": 1,
                "tracks": [{"disc": 1, "title": f"T{i}", "secs": 10.0 * (i + 1)} for i in range(3)]}
        self.assertEqual(eng._established(album, cand, "web", content=True)[1], "")
        self.assertIn("agree only partly", eng._established(album, cand, "web", content=False)[1])


class FBK6_NoLengthsListed(Base):
    def test_FBK6_a_release_listing_no_lengths_by_its_titles(self):
        http = FakeHttp(dg=[dg_release(900, durs=("", "", ""))], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)))
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))
        self.assertEqual(p.targets[1]["title"], ["T1"])
        self.assertNotIn("musicbrainz_recordingid", p.targets[1])

    def test_FBK6_a_title_that_differs_is_not_enough(self):
        rel = dg_release(900, durs=("", "", ""))
        rel["tracklist"][1]["title"] = "Something Else"
        http = FakeHttp(dg=[rel], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)))
        self.assertEqual(p.status, "unresolved")


class FBK6_SparseLengths(Base):
    def test_FBK6_a_release_listing_too_few_lengths_by_its_titles(self):
        # (The Parallax I & II box: lengths for 3 of 15 tracks; here 1 of 5, agreeing)
        d = self.album(n=5)
        http = FakeHttp(dg=[dg_release(900, durs=("0:10", "", "", "", ""))], masters=[dg_master(500, n=5)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)), d=d)
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))

    def test_FBK6_two_listed_lengths_one_off_and_one_on_need_more(self):
        d = self.album(n=8)                                          # (3 of 8 listed: too few to compare)
        http = FakeHttp(dg=[dg_release(900, durs=("0:10", "0:20", "9:30") + ("",) * 5)], masters=[dg_master(500, n=8)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)), d=d)
        self.assertEqual(p.status, "identified", self.logged(eng))  # (2 of 3 agree: as the Parallax box)
        http = FakeHttp(dg=[dg_release(900, durs=("0:10", "8:20", "9:30") + ("",) * 5)], masters=[dg_master(500, n=8)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)), d=d)
        self.assertEqual(p.status, "unresolved")                    # (2 of 3 off)

    def test_FBK6_the_few_lengths_listed_disagreeing_is_no_proof(self):
        d = self.album(n=5)
        http = FakeHttp(dg=[dg_release(900, durs=("9:10", "", "", "", ""))], masters=[dg_master(500, n=5)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)), d=d)
        self.assertEqual(p.status, "unresolved")


class FBK2_PartOfYourFiles(Base):
    def test_FBK2_a_higher_rival_holding_fewer_tracks_than_your_files_is_set_aside(self):
        # (The Parallax II, 12 tracks, scored above the 15-track box your files are)
        d = self.album(n=5)
        http = FakeHttp(mb=[mb_release("mb-a", lens=[10000, 20000, 30000, 40000, 50000]),
                            mb_release("mb-z", title="Album P II", rg="rg-ii", lens=[10000, 20000, 30000])])
        out = dict(review(dict(MB_BEST, confidence=70.0)), pool=pool_of(("mb", "mb-z", 79.0), ("mb", "mb-a", 70.0)))
        eng, p, _d = self.run_partial(http, out, d=d)
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))


class FBK6_Guards(Base):
    def test_FBK6_generic_titles_are_no_proof(self):
        rel = dg_release(900, durs=("", "", ""))
        for i, t in enumerate(rel["tracklist"]):
            t["title"] = f"Untitled {i + 1}"
        d = self.album(n=3)
        for f in sorted(d.glob("*.flac")):
            self.retag(f, title=f"Untitled {int(f.stem)}")
        http = FakeHttp(dg=[rel], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, confidence=85.0)), d=d)
        self.assertEqual(p.status, "unresolved")

    def test_FBK6_another_volume_is_no_proof(self):
        http = FakeHttp(dg=[dg_release(900, title="Album P Vol. 2", durs=("", "", ""))], masters=[dg_master(500)])
        eng, p, _d = self.run_partial(http, review(dict(DG_BEST, title="Album P Vol. 2", confidence=85.0)))
        self.assertEqual(p.status, "unresolved")


class FBK7_NoMaster(Base):
    def test_FBK7_a_reissues_year_is_never_the_albums(self):
        best = {"source": "discogs", "id": 901, "url": "u", "confidence": 75.0, "notes": [], "mb_id": "",
                "discogs_id": "", "artist": "Band P", "title": "Album P"}
        http = FakeHttp(dg=[dict(dg_release(901), master_id=None, year=2015)])
        eng, p, _d = self.run_partial(http, review(best))
        self.assertEqual(p.tier, "partial", self.logged(eng))
        for k in ("originalyear", "originaldate", "date"):
            self.assertNotIn(k, p.targets[0])

    def test_FBK7_the_master_musicbrainz_links_wins(self):
        from test_partial import links
        http = FakeHttp(mb=[mb_release("mb-a", discogs=901)], dg=[dict(dg_release(901), master_id=None)],
                        masters=[dg_master(500)], groups={"rg-p": links(500)})
        eng, p, _d = self.run_partial(http, review(dict(MB_BEST, discogs_id=901)))
        self.assertEqual(p.tier, "partial", self.logged(eng))
        self.assertEqual(p.targets[0].get("discogs_master_id"), ["500"])


class FBK8_OtherDatabaseSameAlbum(Base):
    def test_FBK8_the_other_databases_entry_written_its_own_way_is_the_album(self):
        # MusicBrainz "Album P, Pt. 1", Discogs "Album P, Part 1" of another master, unlinked
        d = self.album(title="Album P, Part 1", n=3)
        http = FakeHttp(mb=[mb_release("mb-a", title="Album P, Part 1")],
                        dg=[dg_release(999, title="Album P, Pt. 1", master=600)], masters=[dg_master(600)])
        out = dict(review(dict(MB_BEST, title="Album P, Part 1", confidence=85.0)),
                   pool=pool_of(("mb", "mb-a", 85.0), ("discogs", "999", 84.0)))
        eng, p, _d = self.run_partial(http, out, d=d)
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))

    def test_FBK8_an_instrumental_edition_with_the_same_running_order_is_another_album(self):
        # (MusicBrainz's studio album; Discogs's "(Instrumental)", no lengths listed, same titles)
        d = self.album(n=5)
        lens = [10000, 20000, 30000, 40000, 50000]
        http = FakeHttp(mb=[mb_release("mb-a", lens=lens)],
                        dg=[dg_release(999, title="Album P (Instrumental)", master=600, durs=("",) * 5)],
                        masters=[dg_master(600, n=5)])
        out = dict(review(dict(MB_BEST, confidence=85.0)), pool=pool_of(("mb", "mb-a", 85.0), ("discogs", "999", 86.0)))
        eng, p, _d = self.run_partial(http, out, d=d)
        # (never the instrumental's: the studio album, every length agreeing, is the files' - FBK-2)
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))
        self.assertEqual(p.targets[0]["album"], ["Album P"])
        self.assertNotIn("listed twice", " ".join(p.why))

    def test_FBK8_another_part_is_still_another_album(self):
        d = self.album(title="Album P, Part 1", n=3)
        http = FakeHttp(mb=[mb_release("mb-a", title="Album P, Part 1")],
                        dg=[dg_release(999, title="Album P, Part 2", master=600)], masters=[dg_master(600)])
        out = dict(review(dict(MB_BEST, title="Album P, Part 1", confidence=85.0)),
                   pool=pool_of(("mb", "mb-a", 85.0), ("discogs", "999", 86.0)))
        eng, p, _d = self.run_partial(http, out, d=d)
        self.assertEqual(p.status, "unresolved")


class FBK2_RivalsMustFit(Base):
    def test_FBK2_another_album_whose_tracks_do_not_fit_does_not_refuse(self):
        http = FakeHttp(mb=[mb_release("mb-a"),
                            mb_release("mb-x", title="Album P", rg="rg-other", lens=[90000, 95000, 99000])])
        out = dict(review(dict(MB_BEST, confidence=85.0)),
                   pool=pool_of(("mb", "mb-a", 85.0), ("mb", "mb-x", 84.0)))
        eng, p, _d = self.run_partial(http, out)
        self.assertEqual((p.status, p.tier), ("identified", "partial"), self.logged(eng))

    def test_FBK2_another_album_that_fits_still_refuses(self):
        http = FakeHttp(mb=[mb_release("mb-a"), mb_release("mb-x", title="Album P Live", rg="rg-other")])
        out = dict(review(dict(MB_BEST, confidence=85.0)),
                   pool=pool_of(("mb", "mb-a", 85.0), ("mb", "mb-x", 84.0)))
        eng, p, _d = self.run_partial(http, out)
        self.rejected(eng, p, "fits about as well")


class FBK3_ExactPathFails(Base):
    def test_FBK3_tracks_not_lining_up_falls_back_to_partial(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        out = {"status": "auto", "why": [], "best": dict(MB_BEST, confidence=95.0), "alts": [], "margin": 50.0}
        eng = self.engine(http)
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(type(eng), "_identified", side_effect=RuntimeError("tracks do not line up")):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual((p.status, p.tier), ("identified", "partial"))
        self.assertIn("FBK-3", " ".join(p.notes))

    def test_FBK3_a_provider_outage_while_falling_back_is_retried_later(self):
        http = FakeHttp(mb=[mb_release("mb-a")])
        out = {"status": "auto", "why": [], "best": dict(MB_BEST, confidence=95.0), "alts": [], "margin": 50.0}
        eng = self.engine(http)
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(type(eng), "_identified", side_effect=RuntimeError("tracks do not line up")), \
                mock.patch.object(type(eng), "_partial", side_effect=ma.ProviderUnavailable("mb", "outage")):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual((p.status, p.reason_codes), ("unresolved", ["musicbrainz_unavailable"]))


class LVE1_LeaveHolds(Base):
    def test_LVE1_recordings_are_held_under_leave(self):
        out = {"status": "nomatch", "why": ["no candidates"], "best": None, "alts": [], "margin": None}
        eng = self.engine(FakeHttp(), unresolved={"action": "leave", "move_to": ""})

        def rec_of(_self, path):
            i = int(Path(path).stem) - 1
            return ({"title": [f"Rec {i}"], "musicbrainz_recordingid": [f"r{i}"]}, {"score": 0.97, "id": "a"}, {"id": "r"})
        with mock.patch.object(ma, "process", return_value=out), mock.patch.object(ma, "fingerprint_ready",
                                                                                 return_value=True), \
                mock.patch.object(type(eng), "_recording_of", rec_of):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual(p.tier, "recording")
        self.assertIn("leave", p.held)


class FBK9_DiscsEachTheirAlbum(Base):
    def run_discs(self, albums):
        import engine
        from engine import Group
        top = self.lib / "Band P" / "2005 - Set"
        for n in (1, 2):
            (top / f"Disc {n}").mkdir(parents=True, exist_ok=True)
            (top / f"Disc {n}" / "01.flac").write_bytes(b"")
        g = Group(top, [top / "Disc 1" / "01.flac", top / "Disc 2" / "01.flac"])
        eng = self.engine(FakeHttp())
        props = iter(engine.planmod.Proposal(source_dir=str(top / f"Disc {n}"), files=[], status="identified",
                                             tier=t, provider="musicbrainz", targets=[{"album": [a]}])
                     for n, (t, a) in enumerate(albums, 1))
        with mock.patch.object(type(eng), "_identify", lambda _s, sub, names=None: next(props)), \
                mock.patch.object(engine, "set_dir", lambda d, skips=None: True):
            return eng._per_disc(g, {})

    def test_FBK9_each_disc_its_own_album_by_partial(self):
        p = self.run_discs([("partial", "Album P"), ("album_only", "Album Q")])
        self.assertIsNotNone(p)
        self.assertEqual(len(p.parts), 1)

    def test_FBK9_two_discs_of_one_album_are_not_two_albums(self):
        self.assertIsNone(self.run_discs([("partial", "Album P"), ("partial", "Album P")]))
        self.assertIsNone(self.run_discs([("partial", "Album P"), ("partial", "ALBUM P")]))

    def test_FBK9_one_disc_sure_and_one_only_as_its_album_is_no_split(self):
        self.assertIsNone(self.run_discs([("exact", "Album P"), ("partial", "Album Q")]))


class FBK4_Recordings(Base):
    def run_rec(self, known):
        """An album nothing fits (no candidate), its files' recordings KNOWN by index."""
        out = {"status": "nomatch", "why": ["no candidates"], "best": None, "alts": [], "margin": None}
        eng = self.engine(FakeHttp())

        def rec_of(_self, path):
            i = int(Path(path).stem) - 1
            if i not in known:
                return None
            return ({"title": [f"Rec {i}"], "artist": ["Real Artist"], "musicbrainz_recordingid": [f"r{i}"]},
                    {"score": 0.97, "id": f"a{i}"}, {"id": f"r{i}"})
        with mock.patch.object(ma, "process", return_value=out), mock.patch.object(ma, "fingerprint_ready",
                                                                                 return_value=True), \
                mock.patch.object(type(eng), "_recording_of", rec_of):
            return eng.identify(eng.albums_in(self.album())[0])

    def test_FBK4_most_files_identified_are_tagged_as_their_recordings(self):
        p = self.run_rec({0, 1})
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "recording", ""))
        self.assertEqual([t.get("musicbrainz_recordingid") for t in p.targets], [["r0"], ["r1"], None])
        for t in p.targets:
            for k in PRESSING + ("album", "albumartist", "tracknumber", "musicbrainz_releasegroupid"):
                self.assertNotIn(k, t)
        self.assertIn("FBK-4", " ".join(p.why))

    def test_FBK4_marked_partial_in_the_plan_its_other_files_untouched(self):
        out = {"status": "nomatch", "why": ["no candidates"], "best": None, "alts": [], "margin": None}
        d = self.album()
        eng = self.engine(FakeHttp())

        def rec_of(_self, path):
            i = int(Path(path).stem) - 1
            return None if i == 2 else ({"title": [f"Rec {i}"], "musicbrainz_recordingid": [f"r{i}"]},
                                        {"score": 0.97, "id": "a"}, {"id": "r"})
        with mock.patch.object(ma, "process", return_value=out), mock.patch.object(ma, "fingerprint_ready",
                                                                                 return_value=True), \
                mock.patch.object(type(eng), "_recording_of", rec_of):
            plan = eng.plan(d)
        ch = [f["changes"] for f in plan["albums"][0]["files"]]
        self.assertEqual([c.get("tagmender") for c in ch], [["Partial"], ["Partial"], None])
        self.assertEqual(ch[2], {})                                   # (not identified: left as it is)
        self.assertEqual(ch[0]["musicbrainz_recordingid"], ["r0"])

    def test_FBK4_acoustid_not_answering_stops_and_is_retried(self):
        out = {"status": "nomatch", "why": ["no candidates"], "best": None, "alts": [], "margin": None}
        eng = self.engine(FakeHttp())
        calls = []

        def rec_of(_self, path):
            calls.append(path)
            raise ma.ProviderUnavailable("acoustid", "rate_limited")
        with mock.patch.object(ma, "process", return_value=out), mock.patch.object(ma, "fingerprint_ready",
                                                                                 return_value=True), \
                mock.patch.object(type(eng), "_recording_of", rec_of):
            p = eng.identify(eng.albums_in(self.album())[0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(p.status, "unresolved")
        self.assertEqual(p.provider_errors.get("acoustid"), "rate_limited")

    def test_FBK4_too_few_identified_is_unresolved(self):
        p = self.run_rec({0})
        self.assertEqual((p.status, p.tier), ("unresolved", "unresolved"))


if __name__ == "__main__":
    unittest.main()
