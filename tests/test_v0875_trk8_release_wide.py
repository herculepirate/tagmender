# TRK-8 (docs/MATCHING_RULES.md): once a set's release is chosen, each file is placed on its own
# track anywhere on the release - by its recording or track ID (its length agreeing), else by the
# weighted likeness of title, length, track and disc number and artist, its title agreeing and no
# other track as close - when the files' own order does not fit (discs ripped into each other's
# folders, a disc labelled as another). Nothing is moved on doubt: one file unsure, or two on one
# track, and the files keep their order (TRK-2 and the other rules decide as before).
# DSC-15: a disc marker followed by the disc's own years ("My Lives (Disc 3) 1989-1999") is no
# other album than "My Lives [Disc 1]".
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac, two_cd_release  # noqa: E402
import engine  # noqa: E402
import match_core as mc  # noqa: E402


def track(disc, title, secs, **kw):
    return dict(disc=disc, title=title, secs=secs, **kw)


def cand(*discs):
    return {"id": "r", "tracks": [dict(t, rec_id=f"rec-{t['title']}", track_id=f"trk-{t['title']}")
                                  for d in discs for t in d]}


D1 = [track(1, "Alpha", 200), track(1, "Beta", 180), track(1, "Gamma", 240)]
D2 = [track(2, "Delta", 210), track(2, "Epsilon", 190), track(2, "Zeta", 250)]


def files_of(*specs):
    return {"tracks": [dict(disc=d, num=n, title=t, secs=s, ids=list(ids)) for d, n, t, s, ids in specs]}


class TRK8_ReleaseView(unittest.TestCase):
    def test_TRK8_discs_ripped_into_each_others_folders(self):
        album = files_of((1, 1, "Delta", 210, ()), (1, 2, "Epsilon", 190, ()), (1, 3, "Zeta", 250, ()),
                         (2, 1, "Alpha", 200, ()), (2, 2, "Beta", 180, ()), (2, 3, "Gamma", 240, ()))
        v = mc.release_view(album, cand(D1, D2))
        self.assertEqual(v["slots"], [3, 4, 5, 0, 1, 2])
        self.assertTrue(v["reordered"])
        self.assertEqual([t["title"] for t in v["tracks"]], ["Delta", "Epsilon", "Zeta", "Alpha", "Beta", "Gamma"])

    def test_TRK8_a_recording_id_places_a_file_whatever_its_title(self):
        album = files_of((1, 1, "Alpha", 200, ()), (1, 2, "Beta", 180, ()), (1, 3, "Untitled", 250, ["rec-Zeta"]),
                         (2, 1, "Delta", 210, ()), (2, 2, "Epsilon", 190, ()), (2, 3, "Gamma", 240, ()))
        v = mc.release_view(album, cand(D1, D2))
        self.assertEqual(v["slots"], [0, 1, 5, 3, 4, 2])

    def test_TRK8_an_id_whose_length_disagrees_places_nothing_by_it(self):
        album = files_of((1, 1, "Alpha", 200, ()), (1, 2, "Beta", 180, ()), (1, 3, "Untitled", 30, ["rec-Zeta"]),
                         (2, 1, "Delta", 210, ()), (2, 2, "Epsilon", 190, ()), (2, 3, "Gamma", 240, ()))
        self.assertIsNone(mc.release_view(album, cand(D1, D2)))

    def test_TRK8_files_in_their_own_order_are_left_to_the_other_rules(self):
        album = files_of((1, 1, "Alpha", 200, ()), (1, 2, "Beta", 180, ()), (1, 3, "Gamma", 240, ()),
                         (2, 1, "Delta", 210, ()), (2, 2, "Epsilon", 190, ()), (2, 3, "Zeta", 250, ()))
        self.assertIsNone(mc.release_view(album, cand(D1, D2)))

    def test_TRK8_a_title_two_tracks_share_places_nothing(self):
        d2 = [track(2, "Alpha", 200), track(2, "Epsilon", 190), track(2, "Zeta", 250)]
        # (a file "Alpha" with no disc or track number: the two "Alpha" tracks are as close)
        album = files_of((0, 0, "Alpha", 200, ()), (1, 2, "Epsilon", 190, ()), (1, 3, "Zeta", 250, ()),
                         (2, 1, "Alpha", 200, ()), (2, 2, "Beta", 180, ()), (2, 3, "Gamma", 240, ()))
        self.assertIsNone(mc.release_view(album, cand(D1, d2)))

    def test_TRK8_an_untitled_file_places_nothing(self):
        album = files_of((1, 1, "Delta", 210, ()), (1, 2, "", 190, ()), (1, 3, "Zeta", 250, ()),
                         (2, 1, "Alpha", 200, ()), (2, 2, "Beta", 180, ()), (2, 3, "Gamma", 240, ()))
        self.assertIsNone(mc.release_view(album, cand(D1, D2)))

    def test_TRK8_more_files_than_tracks_places_nothing(self):
        album = files_of((1, 1, "Delta", 210, ()), (1, 2, "Epsilon", 190, ()), (1, 3, "Zeta", 250, ()),
                         (2, 1, "Alpha", 200, ()), (2, 2, "Beta", 180, ()), (2, 3, "Gamma", 240, ()),
                         (2, 4, "Bonus", 100, ()))
        self.assertIsNone(mc.release_view(album, cand(D1, D2)))

    def test_TRK8_a_title_on_two_discs_is_never_placed_by_likeness(self):
        # (a studio disc and a live disc with the same songs, the folders labelled the wrong way
        # round: "Song A" fits both discs' "Song A" - their lengths 2 s apart tell nothing)
        rel = [track(1, "Song A", 240), track(1, "Song B", 200), track(1, "Studio Only", 300),
               track(2, "Song A", 242), track(2, "Song B", 203), track(2, "Live Only", 310)]
        album = files_of((1, 1, "Song A", 242, ()), (1, 2, "Song B", 203, ()), (1, 3, "Live Only", 310, ()),
                         (2, 1, "Song A", 240, ()), (2, 2, "Song B", 200, ()), (2, 3, "Studio Only", 300, ()))
        self.assertIsNone(mc.release_view(album, cand(rel[:3], rel[3:])))

    def test_TRK8_a_title_on_two_discs_is_placed_by_its_id_or_lengths_far_apart(self):
        d1 = [track(1, "Song A", 240), track(1, "Song B", 200), track(1, "Studio Only", 300)]
        d2 = [track(2, "Song A", 420), track(2, "Song B", 380), track(2, "Live Only", 310)]
        album = files_of((1, 1, "Song A", 420, ()), (1, 2, "Song B", 380, ()), (1, 3, "Live Only", 310, ()),
                         (2, 1, "Song A", 240, ()), (2, 2, "Song B", 200, ()), (2, 3, "Studio Only", 300, ()))
        self.assertEqual(mc.release_view(album, cand(d1, d2))["slots"], [3, 4, 5, 0, 1, 2])
        near = [track(2, "Song A", 242), track(2, "Song B", 203), track(2, "Live Only", 310)]
        album = files_of((1, 1, "Song A", 242, ["rec-Song A-live"]), (1, 2, "Song B", 203, ["trk-live-b"]),
                         (1, 3, "Live Only", 310, ()), (2, 1, "Song A", 240, ()), (2, 2, "Song B", 200, ()),
                         (2, 3, "Studio Only", 300, ()))
        c = cand(d1, near)
        c["tracks"][3]["rec_id"], c["tracks"][4]["track_id"] = "rec-Song A-live", "trk-live-b"
        self.assertIsNone(mc.release_view(album, c))      # (two files by ID; "Song A" 240 s still ambiguous)

    def test_TRK8_a_26_disc_box_is_looked_at_in_well_under_a_second(self):
        import time
        tracks = [track(d, f"Track {d}-{i} title words", 180 + (d * 7 + i * 13) % 120)
                  for d in range(1, 27) for i in range(1, 16)]
        files = [dict(t, num=(k % 15) + 1, ids=[]) for k, t in enumerate(tracks)]
        files[0], files[1] = files[1], files[0]
        t0 = time.monotonic()
        v = mc.release_view({"tracks": files}, {"tracks": tracks})
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(v["slots"][:2], [1, 0])

    def test_DSC15_disc_marker_with_the_discs_years_is_the_same_album(self):
        t = engine._album_title
        titles = {t({"album": [a]}) for a in ("My Lives [Disc 1]", "My Lives (Disc 2)", "My Lives (Disc 3) 1989-1999",
                                              "My Lives [Disk 4]")}
        self.assertEqual(titles, {"my lives"})
        self.assertEqual(t({"album": ["Drops 1"]}), "drops 1")
        self.assertNotEqual(t({"album": ["Hits 1999"]}), t({"album": ["Hits"]}))


class DSC15_Years(unittest.TestCase):
    def test_DSC15_years_join_only_discs_of_different_numbers(self):
        m = lambda a: {"album": [a], "albumartist": ["X"]}  # noqa: E731
        metas = {"a": m("Hits (CD 2) 1999"), "b": m("Hits (CD 2) 2001")}
        self.assertEqual(len(set(engine.album_keys(list(metas), "/x", metas).values())), 2)
        metas = {"a": m("My Lives [Disc 1]"), "b": m("My Lives (Disc 3) 1989-1999")}
        self.assertEqual(len(set(engine.album_keys(list(metas), "/x", metas).values())), 1)


class TRK8_DSC15_Engine(te.Base):
    def test_TRK8_mislabelled_disc_folders_are_tagged_as_their_own_tracks(self):
        # (the second disc's tracks ripped into "CD1", the first's into "CD2")
        top = self.lib / "Band" / "2005 - Double"
        for folder, prefix, n in (("CD1", "U", 2), ("CD2", "T", 1)):
            for i in range(3):
                flac(top / folder, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i,
                     title=f"{prefix}{i}", album="Double", albumartist="Band", artist="Band", track=str(i + 1),
                     barcode="0123456789012")             # (the release proven by more than its name)
        eng = self.engine(SetHttp(mb=[two_cd_release("mb-2", "Band", "Double")]))
        [g] = eng.albums_in(self.lib)
        p = eng.identify(g)
        self.assertEqual((p.status, p.release_id), ("identified", "mb-2"), p.why)
        got = {(Path(f).parent.name, t["title"][0], t["discnumber"][0], t["tracknumber"][0])
               for f, t in zip(p.files, p.targets)}
        self.assertEqual(got, {("CD1", f"U{i}", "2", str(i + 1)) for i in range(3)} |
                         {("CD2", f"T{i}", "1", str(i + 1)) for i in range(3)})

    def test_DSC15_my_lives_disc_folders_are_one_album(self):
        top = self.lib / "Band. My Lives. Part 1"
        for n, album in enumerate(("My Lives [Disc 1]", "My Lives (Disc 2)", "My Lives (Disc 3) 1989-1999",
                                   "My Lives [Disk 4]"), 1):
            flac(top / f"Disk {n}", "01.flac", 10, freq=300 + 50 * n, title=f"D{n}", album=album,
                 albumartist="Band", artist="Band", track="1")
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual([(g.folder, len(g.files)) for g in gs], [(top, 4)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
