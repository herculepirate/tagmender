# MED-22: the layers of one hybrid SACD are one disc. MusicBrainz lists a hybrid SACD's CD layer
# and its SACD layer (stereo, multichannel) as media of their own (Billy Joel - Greatest Hits
# Vol. I & II, MFSL 2017: 2 hybrid SACDs listed as 4 media, CD layer / SACD layer of each). A rip
# of the CD layers is discs 1 and 2 of 2, every track present; a single hybrid SACD's CD-layer
# rip is 1 of 1. Version folders of the SACD layers ("2.0/", "5.1/") still find their own layer
# (match_core.version_view keeps every medium). Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
from test_v0821_engine_rules import flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import mb_tags  # noqa: E402

AC = [{"name": "Piano Player", "joinphrase": "",
       "artist": {"id": "a-pp", "name": "Piano Player", "sort-name": "Player, Piano"}}]
CDL, SA2, SAM = "Hybrid SACD (CD layer)", "Hybrid SACD (SACD layer, 2 channels)", "Hybrid SACD (SACD layer, multichannel)"


def medium(pos, fmt, titles, title="", discid=False, lens=None):
    tracks = [{"id": f"t-{pos}-{i}", "position": i + 1, "number": str(i + 1), "title": t,
               "length": (lens or [10000, 20000, 30000])[i % 3],
               "recording": {"id": f"r-{t}", "title": t}} for i, t in enumerate(titles)]
    m = {"position": pos, "format": fmt, "track-count": len(titles), "tracks": tracks}
    if title:
        m["title"] = title
    if discid:
        m["discs"] = [{"id": f"disc-{pos}", "offset-count": len(titles)}]
    return m


DISC1 = ["Song A", "Song B"]
DISC2 = ["Song C", "Song D", "Song E"]


def greatest_hits(rid="mb-gh", names=("1973-1977", "1978-1985")):
    """Modelled on MusicBrainz 6b57a84d (Greatest Hits Vol. I & II, MFSL): disc 1 CD layer and
    stereo SACD layer, disc 2 likewise; the CD layers carry the disc IDs."""
    n1, n2 = names
    media = [medium(1, CDL, DISC1, n1, discid=True), medium(2, SA2, DISC1, n1),
             medium(3, CDL, DISC2, n2, discid=True), medium(4, SA2, DISC2, n2)]
    return {"id": rid, "title": "Greatest Hits", "artist-credit": AC, "status": "Official",
            "date": "2017", "country": "US",
            "release-group": {"id": "rg-gh", "primary-type": "Album", "first-release-date": "1985"},
            "media": media}


def rel_of(media, rid="mb-x"):
    return {"id": rid, "title": "X", "artist-credit": AC, "release-group": {"id": "rg-x"}, "media": media}


def numbers(tags):
    return [(t["discnumber"][0], t["totaldiscs"][0]) for t in tags]


class MED22_OneLayer(unittest.TestCase):
    def test_MED22_two_discs_of_two_layers_are_two_discs(self):
        one = mb_tags.one_layer(greatest_hits())
        self.assertEqual([(m["position"], m["format"], m["~layer_position"]) for m in one["media"]],
                         [(1, CDL, 1), (2, CDL, 3)])
        self.assertEqual([m["title"] for m in one["media"]], ["1973-1977", "1978-1985"])
        self.assertEqual([len(g) for g in mb_tags.layer_groups(greatest_hits())], [2, 2])

    def test_MED22_the_release_itself_is_left_alone(self):
        rel = greatest_hits()
        mb_tags.one_layer(rel)
        self.assertEqual([m["position"] for m in rel["media"]], [1, 2, 3, 4])

    def test_MED22_cd_or_stereo_sacd_layer_preferred(self):
        stereo = mb_tags.one_layer(greatest_hits(), "stereo")
        self.assertEqual([(m["position"], m["format"], m["~layer_position"]) for m in stereo["media"]],
                         [(1, SA2, 2), (2, SA2, 4)])
        # (no multichannel layer listed: the first SACD layer)
        self.assertEqual([m["format"] for m in mb_tags.one_layer(greatest_hits(), "multichannel")["media"]], [SA2, SA2])

    def test_MED22_multichannel_layer(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1), medium(3, SAM, DISC1)])
        self.assertEqual([(m["format"], m["position"], m["~layer_position"])
                          for m in mb_tags.one_layer(rel, "multichannel")["media"]], [(SAM, 1, 3)])
        self.assertEqual([m["format"] for m in mb_tags.one_layer(rel, "cd")["media"]], [CDL])
        self.assertEqual([m["format"] for m in mb_tags.one_layer(rel, "stereo")["media"]], [SA2])

    def test_MED22_no_layers_unchanged(self):
        for formats in (["CD", "CD"], ["CD", "DVD-Video"], ["Hybrid SACD", "Hybrid SACD"], ["SACD"]):
            rel = rel_of([medium(i, f, DISC1) for i, f in enumerate(formats, 1)])
            self.assertIs(mb_tags.one_layer(rel), rel, formats)

    def test_MED22_a_twin_of_another_track_count_stays_apart(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1 + ["Bonus"])])
        self.assertIs(mb_tags.one_layer(rel), rel)
        # (other titles likewise: another disc)
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, ["Other 1", "Other 2"])])
        self.assertIs(mb_tags.one_layer(rel), rel)

    def test_MED22_two_cd_layers_in_a_row_are_two_discs(self):
        # (the second CD layer, the same titles, is the next disc: a disc holds one CD layer)
        rel = rel_of([medium(1, CDL, DISC1), medium(2, CDL, DISC1), medium(3, SA2, DISC1)])
        one = mb_tags.one_layer(rel)
        self.assertEqual([(m["position"], m["~layer_position"]) for m in one["media"]], [(1, 1), (2, 2)])

    def test_MED22_a_plain_cd_beside_a_hybrid_keeps_its_disc(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1), medium(3, "CD", DISC2), medium(4, "DVD-Video", ["V"])])
        one = mb_tags.one_layer(rel)
        self.assertEqual([(m["position"], m["format"], m["~layer_position"]) for m in one["media"]],
                         [(1, CDL, 1), (2, "CD", 3), (3, "DVD-Video", 4)])

    def test_MED22_track_counts_alone_without_tracks(self):
        # (a disc ID lookup, inc=discids+media, carries no tracks: the counts are compared)
        rel = rel_of([{"position": p, "format": f, "track-count": n} for p, f, n in
                      ((1, CDL, 11), (2, SA2, 11), (3, CDL, 15), (4, SA2, 15))])
        self.assertEqual([m["~layer_position"] for m in mb_tags.one_layer(rel)["media"]], [1, 3])


class MED22_Candidate(unittest.TestCase):
    def test_MED22_candidate_counts_the_physical_discs(self):
        cand = ma.mb_candidate(greatest_hits())
        self.assertEqual(len(cand["tracks"]), 5)
        self.assertEqual(cand["disc_count"], 2)
        self.assertEqual([t["disc"] for t in cand["tracks"]], [1, 1, 2, 2, 2])
        self.assertEqual(cand["media_formats"], [CDL, CDL])
        self.assertEqual(cand["formats"], {"cd"})
        self.assertEqual(cand["disc_titles"], {1: "1973-1977", 2: "1978-1985"})
        self.assertTrue(cand["layers"])
        self.assertEqual(cand["layer_positions"], {1: 1, 2: 1, 3: 2, 4: 2})
        self.assertTrue(cand["sacd_media"] and cand["hires_media"])
        # (every medium kept for version folders)
        self.assertEqual([m["format"] for m in cand["media_info"]], [CDL, SA2, CDL, SA2])

    def test_MED22_no_layers_no_change(self):
        cand = ma.mb_candidate(rel_of([medium(1, "CD", DISC1), medium(2, "CD", DISC2)]))
        self.assertFalse(cand["layers"])
        self.assertEqual((len(cand["tracks"]), cand["disc_count"]), (5, 2))

    def test_MED22_disc_id_positions_read_as_discs(self):
        cand = ma.mb_candidate(greatest_hits())
        self.assertEqual(mc.layer_discs(cand, {1, 3}), {1, 2})
        self.assertEqual(mc.layer_discs({"layer_positions": {}}, {1, 3}), {1, 3})
        # (a disc ID on the CD layer at position 3 is disc 2's: one disc of the set alone)
        files = [{"title": t, "secs": s} for t, s in zip(DISC2, (10, 20, 30))]
        album = {"tracks": files, "title": "Greatest Hits", "exact_toc_media": {"mb-gh": {3}}}
        box = mc._box_disc(album, cand)
        self.assertEqual((box["box_disc"], box.get("box_proof")), (2, "disc ID"))

    def test_MED22_search_hit_counts_the_layers_once(self):
        hit = {"id": "h", "track-count": 52, "media": [{"format": f, "track-count": n} for f, n in
                                                       ((CDL, 11), (SA2, 11), (CDL, 15), (SA2, 15))]}
        self.assertEqual(ma._mb_hit_tracks(hit), 26)
        self.assertEqual(ma._mb_track_total(hit), 26)
        plain = {"id": "p", "track-count": 13, "media": [{"format": "CD", "track-count": 10},
                                                         {"format": "DVD-Video", "track-count": 3}]}
        self.assertEqual(ma._mb_hit_tracks(plain), 13)


class MED22_Tags(unittest.TestCase):
    def test_MED22_cd_layer_rip_of_two_discs_is_one_and_two_of_two(self):
        tags = mb_tags.release_tags(mb_tags.one_layer(greatest_hits()), {})
        self.assertEqual(numbers(tags), [("1", "2")] * 2 + [("2", "2")] * 3)
        self.assertEqual({t["media"][0] for t in tags}, {CDL})
        self.assertEqual([t["discsubtitle"][0] for t in tags], ["1973-1977"] * 2 + ["1978-1985"] * 3)
        self.assertEqual([t["musicbrainz_trackid"][0] for t in tags],
                         ["t-1-0", "t-1-1", "t-3-0", "t-3-1", "t-3-2"])
        self.assertEqual([t["totaltracks"][0] for t in tags], ["2", "2", "3", "3", "3"])

    def test_MED22_stereo_sacd_rip_takes_the_sacd_layers_tracks(self):
        tags = mb_tags.release_tags(mb_tags.one_layer(greatest_hits(), "stereo"), {})
        self.assertEqual(numbers(tags), [("1", "2")] * 2 + [("2", "2")] * 3)
        self.assertEqual({t["media"][0] for t in tags}, {SA2})
        self.assertEqual(tags[2]["musicbrainz_trackid"], ["t-4-0"])

    def test_MED22_single_hybrid_is_one_of_one_all_tracks(self):
        rel = rel_of([medium(1, CDL, DISC2), medium(2, SA2, DISC2)])
        tags = mb_tags.release_tags(mb_tags.one_layer(rel), {})
        self.assertEqual(numbers(tags), [("1", "1")] * 3)
        cand = ma.mb_candidate(rel)
        self.assertEqual((len(cand["tracks"]), cand["disc_count"]), (3, 1))
        # (before: 6 tracks of 2 discs, half of them "missing")
        self.assertEqual(len(mb_tags.release_tags(rel, {})), 6)

    def test_MED22_med17_video_after_the_hybrid_still_counted_out(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1), medium(3, CDL, DISC2), medium(4, SA2, DISC2),
                      medium(5, "DVD-Video", ["Clip"])])
        rel["media"][4]["tracks"][0]["recording"]["video"] = True
        tags = mb_tags.release_tags(mb_tags.one_layer(rel), {})
        self.assertEqual(numbers(tags), [("1", "2")] * 2 + [("2", "2")] * 3)

    def test_MED22_med20_download_after_the_hybrid_counted_out(self):
        rel = mb_tags.one_layer(rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1), medium(3, "Digital Media", ["Bonus"])]))
        self.assertEqual(engine._unheld_digital_media(rel, [0, 1]), {2})
        self.assertEqual(numbers(mb_tags.release_tags(rel, {}, uncounted={2})[:2]), [("1", "1")] * 2)

    def test_MED22_layer_prefer_from_the_audio(self):
        self.assertEqual(engine._layer_prefer({"audio": {"hires": False}}), "cd")
        self.assertEqual(engine._layer_prefer({"audio": {"hires": True}, "tracks": [{"channels": 2}]}), "stereo")
        self.assertEqual(engine._layer_prefer({"audio": {"sacd": True, "hires": True},
                                               "tracks": [{"channels": 6}]}), "multichannel")
        self.assertEqual(engine._layer_prefer({"audio": {"hires": True},
                                               "tracks": [{"channels": 2, "sacd_area": "Multichannel"}]}), "multichannel")


class MED22_DiscIds(unittest.TestCase):
    """TAG-3's disc ID check: your disc 2's TOC is compared with the second hybrid's CD layer
    (position 3), never read as "numbered otherwise"."""

    class Stub:
        use_mb = True

        def __init__(self, rel):
            self.rel = rel
            self.http = self
            self.log = __import__("logging").getLogger("med22")

        def get_json(self, url, host):
            return self.rel

    def toc(self, lengths):
        offs, pos = [], 150
        for n in lengths:
            offs.append(pos)
            pos += n
        return " ".join(str(x) for x in [1, len(lengths), pos] + offs)

    def rel(self):
        rel = {"id": "mb-gh", "media": [{"position": p, "format": f, "track-count": n} for p, f, n in
                                        ((1, CDL, 2), (2, SA2, 2), (3, CDL, 3), (4, SA2, 3))]}
        rel["media"][2]["discs"] = [{"id": "d2", "offsets": [150, 1150, 2150], "sectors": 3150}]
        return rel

    def test_MED22_your_disc_two_compared_with_the_second_cd_layer(self):
        stub = self.Stub(self.rel())
        other = engine.Engine._disc_ids_differ(stub, "mb-gh", {"2": self.toc([900, 1000, 1300])})
        self.assertIn("none of its disc IDs fits", other)
        self.assertEqual(engine.Engine._disc_ids_differ(stub, "mb-gh", {"2": self.toc([1000, 1000, 1000])}), "")


class MED22_VersionFolders(unittest.TestCase):
    def test_MED22_version_folders_still_find_their_sacd_layers(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SA2, DISC1), medium(3, SAM, DISC1)], rid="mb-v")
        cand = ma.mb_candidate(rel)
        self.assertEqual(len(cand["tracks"]), 2)
        tracks = [{"title": t, "secs": s, "disc": d} for d in (1, 2) for t, s in zip(DISC1, (10, 20))]
        album = {"tracks": tracks, "version_discs": {1: "stereo", 2: "multichannel"},
                 "version_kinds": {1: "sacd", 2: "sacd"}, "version_labels": {1: "2.0", 2: "5.1"}}
        view = mc.version_view(album, cand)
        self.assertIsNotNone(view)
        self.assertEqual(view["version_media"], {1: 2, 2: 3})
        self.assertEqual(view["version_tracks"], [[2, 0], [2, 1], [3, 0], [3, 1]])


class MED22_Engine(te.Base):
    """A CD-quality rip of the two hybrid SACDs (EAC: disc folders CD1, CD2) identified and tagged:
    discs 1 and 2 of 2, the CD layers' tracks."""

    def make(self, discs=(1, 2)):
        top = self.lib / "Piano Player" / "2017 - Greatest Hits"
        for n in discs:
            for i, t in enumerate(DISC1 if n == 1 else DISC2):
                flac(top / f"CD{n}" if len(discs) > 1 else top, f"{i + 1:02d}.flac", 10 * (i + 1),
                     freq=300 + 100 * n + 10 * i, title=t, album="Greatest Hits", albumartist="Piano Player",
                     artist="Piano Player", track=str(i + 1), disc=str(n))
        return top

    def test_MED22_two_disc_rip_tagged_one_and_two_of_two(self):
        top = self.make()
        eng = self.engine(te.FakeHttp(mb=[greatest_hits()]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertTrue(p.targets, (p.status, p.reason, p.why))
        self.assertEqual(sorted({(t["discnumber"][0], t["totaldiscs"][0]) for t in p.targets}),
                         [("1", "2"), ("2", "2")], (p.reason, p.why))
        self.assertEqual({t["media"][0] for t in p.targets}, {CDL})
        self.assertEqual(sorted(t["musicbrainz_trackid"][0] for t in p.targets),
                         ["t-1-0", "t-1-1", "t-3-0", "t-3-1", "t-3-2"])

    def test_MED22_disc_two_alone_is_two_of_two(self):
        # (one disc of the set in a folder of its own, BOX-2: the second hybrid's CD layer,
        # numbered as the set's discs are - never "3 of 4"; found by its disc title here, by
        # its disc ID in MED22_Candidate)
        top = self.make(discs=(2,))
        eng = self.engine(te.FakeHttp(mb=[greatest_hits(names=("Volume One", "Greatest Hits"))]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertEqual({(t["discnumber"][0], t["totaldiscs"][0]) for t in p.targets}, {("2", "2")},
                         (p.status, p.reason, p.why))
        self.assertEqual({t["media"][0] for t in p.targets}, {CDL})
        self.assertEqual([t["musicbrainz_trackid"][0] for t in p.targets], ["t-3-0", "t-3-1", "t-3-2"])

if __name__ == "__main__":
    unittest.main()
