# MED-22: the layers of one hybrid SACD are one disc. MusicBrainz lists a hybrid SACD's CD layer
# and its SACD layer (stereo, multichannel) as media of their own (Billy Joel - Greatest Hits
# Vol. I & II, MFSL 2017: 2 hybrid SACDs listed as 4 media, CD layer / SACD layer of each). A rip
# of the CD layers is discs 1 and 2 of 2, every track present; a single hybrid SACD's CD-layer
# rip is 1 of 1. Version folders of the SACD layers ("2.0/", "5.1/") still find their own layer
# (match_core.version_view keeps every medium). Synthetic data only.
import subprocess
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


def hires_flac(d, name, secs, freq=440, **meta):
    """A 24-bit/88.2 kHz stereo FLAC (an SACD layer's audio, converted)."""
    d.mkdir(parents=True, exist_ok=True)
    tags = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    "-ar", "88200", "-ac", "2", "-sample_fmt", "s32", *tags, str(d / name), "-y"], check=True)
    return d / name

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


class MED22_Families(unittest.TestCase):
    def test_MED22_sacd_layer_naming_no_channels_joins(self):
        rel = rel_of([medium(1, CDL, DISC1), medium(2, "Hybrid SACD (SACD layer)", DISC1)])
        self.assertEqual([m["format"] for m in mb_tags.one_layer(rel)["media"]], [CDL])
        self.assertEqual([m["format"] for m in mb_tags.one_layer(rel, "stereo")["media"]], ["Hybrid SACD (SACD layer)"])
        self.assertEqual([m["format"] for m in mb_tags.one_layer(rel, "multichannel")["media"]],
                         ["Hybrid SACD (SACD layer)"])

    def test_MED22_a_plain_sacd_never_joins_a_hybrid(self):
        rel = rel_of([medium(1, "SACD (2 channels)", DISC1), medium(2, CDL, DISC1)])
        self.assertIs(mb_tags.one_layer(rel), rel)
        rel = rel_of([medium(1, CDL, DISC1), medium(2, SAM, DISC1), medium(3, "SACD (2 channels)", DISC1)])
        self.assertEqual([[m["position"] for m in g] for g in mb_tags.layer_groups(rel)], [[1, 2], [3]])
        # (a plain SACD's two areas are one disc; an SHM-SACD's with its own)
        rel = rel_of([medium(1, "SACD (2 channels)", DISC1), medium(2, "SACD (multichannel)", DISC1),
                      medium(3, "SHM-SACD (2 channels)", DISC2), medium(4, "SHM-SACD (multichannel)", DISC2)])
        self.assertEqual([[m["position"] for m in g] for g in mb_tags.layer_groups(rel)], [[1, 2], [3, 4]])
        rel = rel_of([medium(1, "SACD (2 channels)", DISC1), medium(2, "SHM-SACD (multichannel)", DISC1)])
        self.assertIs(mb_tags.one_layer(rel), rel)

    def test_MED22_counts_alone_join_only_cd_layer_then_its_sacd_layer(self):
        def counted(formats, n=11):
            return rel_of([{"position": p, "format": f, "track-count": n} for p, f in enumerate(formats, 1)])
        # (CD, CD, SACD, SACD of one count: the order is not the discs' - nothing joined)
        rel = counted([CDL, CDL, SA2, SA2])
        self.assertIs(mb_tags.one_layer(rel), rel)
        rel = counted([SA2, CDL, SA2, CDL])
        self.assertIs(mb_tags.one_layer(rel), rel)
        rel = counted([CDL, SA2, CDL, SA2])
        self.assertEqual([[m["position"] for m in g] for g in mb_tags.layer_groups(rel)], [[1, 2], [3, 4]])
        rel = counted([CDL, SA2, SAM, "CD"])
        self.assertEqual([[m["position"] for m in g] for g in mb_tags.layer_groups(rel)], [[1, 2, 3], [4]])

    def test_MED22_disc_numbers_skip_a_video_disc(self):
        rel = rel_of([medium(1, "DVD-Video", ["Clip"]), medium(2, CDL, DISC1), medium(3, SA2, DISC1),
                      medium(4, CDL, DISC2), medium(5, SA2, DISC2)])
        rel["media"][0]["tracks"][0]["recording"]["video"] = True
        cand = ma.mb_candidate(rel)
        self.assertEqual([t["disc"] for t in cand["tracks"]], [1, 1, 2, 2, 2])
        self.assertEqual(cand["layer_positions"], {2: 1, 3: 1, 4: 2, 5: 2})
        self.assertEqual(mc.layer_discs(cand, {4}), {2})
        self.assertEqual(engine._layer_prefer({"tracks": [{"disc": 1, "audio": {"rate": 44100, "bits": 16}},
                                                          {"disc": 2, "audio": {"rate": 88200, "bits": 24}}]}, rel),
                         {2: "cd", 3: "stereo"})
        self.assertEqual(engine._audio_position(mb_tags.one_layer(rel), 2), 3)
        self.assertEqual(engine._audio_position(rel_of([medium(1, "CD", DISC1)]), 1), 1)


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
        cd = {"rate": 44100, "bits": 16, "channels": 2}
        hi = {"rate": 88200, "bits": 24, "channels": 2}
        mch = {"rate": 88200, "bits": 24, "channels": 6}
        tr = lambda d, a: {"disc": d, "audio": a}   # noqa: E731
        rel = greatest_hits()
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, cd), tr(2, cd)]}, rel), {1: "cd", 2: "cd"})
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, hi), tr(2, hi)]}, rel), {1: "stereo", 2: "stereo"})
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, cd), tr(2, mch)]}, rel), {1: "cd", 2: "multichannel"})
        # (a disc whose files mix the two: its CD layer)
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, cd), tr(1, hi), tr(2, hi)]}, rel), {1: "cd", 2: "stereo"})
        # (an SACD area named in the tags)
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, dict(hi, sacd_area="Multichannel")),
                                                          tr(2, dict(hi, sacd_area="Multichannel"))]}, rel),
                         {1: "multichannel", 2: "multichannel"})
        # (one disc of the set alone: the copy's layer)
        self.assertEqual(engine._layer_prefer({"tracks": [tr(1, hi)]}, rel, box=2), {1: "stereo", 2: "stereo"})
        # (no audio facts on the files: the copy's)
        self.assertEqual(engine._layer_prefer({"audio": {"hires": True}, "tracks": [{"disc": 1, "channels": 2}]}, rel),
                         {1: "stereo", 2: "stereo"})
        self.assertEqual(engine._layer_prefer({"audio": {"hires": False}, "tracks": [{"disc": 1}]}, rel), {1: "cd", 2: "cd"})

    def test_MED22_one_folder_of_every_disc_takes_the_copys_layer(self):
        # (26 files of the 2-disc set, no disc tags: all on "disc 1" - every disc the copy's layer,
        # never disc 2 left to its CD layer)
        hi = {"rate": 88200, "bits": 24, "channels": 2}
        album = {"tracks": [{"disc": 1, "audio": hi}] * 5}
        prefer = engine._layer_prefer(album, greatest_hits())
        self.assertEqual(prefer, {1: "stereo", 2: "stereo"})
        self.assertEqual([m["format"] for m in mb_tags.one_layer(greatest_hits(), prefer)["media"]], [SA2, SA2])
        cd = {"rate": 44100, "bits": 16, "channels": 2}
        self.assertEqual(engine._layer_prefer({"tracks": [{"disc": 1, "audio": cd}] * 5}, greatest_hits()),
                         {1: "cd", 2: "cd"})

    def test_MED22_group_pressings_take_the_picks_layer(self):
        class Http:
            def get_json(self, url, host):
                rel = greatest_hits()
                for m in rel["media"]:
                    if m["format"] == SA2:
                        for t in m["tracks"]:
                            t["recording"] = dict(t["recording"], id="sa-" + t["recording"]["id"])
                return {"releases": [rel], "release-count": 1}
        self.assertEqual(engine._uniform_layer({1: "stereo", 2: "stereo"}), "stereo")
        self.assertEqual(engine._uniform_layer({1: "cd", 2: "stereo"}), "cd")
        rec = [x["recording"] for x in engine._mb_group(Http(), "rg-gh", "stereo")["pressings"][0]["tracks"]]
        self.assertEqual(rec, ["sa-r-" + t for t in DISC1 + DISC2])
        rec = [x["recording"] for x in engine._mb_group(Http(), "rg-gh")["pressings"][0]["tracks"]]
        self.assertEqual(rec, ["r-" + t for t in DISC1 + DISC2])

    def test_MED22_one_layer_disc_by_disc(self):
        one = mb_tags.one_layer(greatest_hits(), {1: "cd", 2: "stereo"})
        self.assertEqual([(m["position"], m["format"], m["~layer_position"]) for m in one["media"]],
                         [(1, CDL, 1), (2, SA2, 4)])
        tags = mb_tags.release_tags(one, {})
        self.assertEqual(numbers(tags), [("1", "2")] * 2 + [("2", "2")] * 3)
        self.assertEqual([t["musicbrainz_trackid"][0] for t in tags], ["t-1-0", "t-1-1", "t-4-0", "t-4-1", "t-4-2"])
        # (a disc the map does not name: its CD layer)
        self.assertEqual([m["format"] for m in mb_tags.one_layer(greatest_hits(), {2: "stereo"})["media"]], [CDL, SA2])

    def test_MED22_group_pressings_count_the_layers_once(self):
        # (_partial_mb: the release group's pressings compared with the one-layer pick)
        class Http:
            def get_json(self, url, host):
                return {"releases": [greatest_hits()], "release-count": 1}
        group = engine._mb_group(Http(), "rg-gh")
        p = group["pressings"][0]
        self.assertEqual(len(p["tracks"]), 5)
        self.assertEqual(p["layout"], ((2, 3), 2))
        self.assertEqual([x["recording"] for x in p["tracks"]], ["r-" + t for t in DISC1 + DISC2])

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

    def make(self, discs=(1, 2), hires=()):
        top = self.lib / "Piano Player" / "2017 - Greatest Hits"
        for n in discs:
            for i, t in enumerate(DISC1 if n == 1 else DISC2):
                d = top / f"CD{n}" if len(discs) > 1 else top
                meta = dict(title=t, album="Greatest Hits", albumartist="Piano Player",
                            artist="Piano Player", track=str(i + 1), disc=str(n))
                if n in hires:
                    hires_flac(d, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
                else:
                    flac(d, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
        return top

    def lone_disc_two(self, rel):
        top = self.make(discs=(2,))
        eng = self.engine(te.FakeHttp(mb=[rel]))
        p = eng.identify(eng.albums_in(top)[0])
        return p, [(t["title"][0], t["discnumber"][0], t["totaldiscs"][0], t["musicbrainz_trackid"][0])
                   for t in p.targets]

    def dvd_first(self, media):
        dvd = {"position": 1, "format": "DVD-Video", "track-count": 1, "tracks": [
            {"id": "t-v", "position": 1, "number": "1", "title": "Clip", "length": 5000,
             "recording": {"id": "r-v", "title": "Clip", "video": True}}]}
        rel = greatest_hits(names=("Volume One", "Greatest Hits"))
        rel["media"] = [dvd] + [m for m in rel["media"] if m["format"] in media]
        for i, m in enumerate(rel["media"], 1):
            for t in m["tracks"]:
                t["id"] = t["id"].replace(f"t-{m['position']}-", f"t-{i}-")
            m["position"] = i
        return rel

    def test_MED22_box2_lone_disc_two_after_a_dvd(self):
        # (BOX-2: the candidate's disc 2 is the release's second audio disc, medium 3 after a
        # DVD-Video - never medium 2, disc 1's tracks)
        rel = self.dvd_first({"CD"} | {CDL})
        for m in rel["media"][1:]:
            m["format"] = "CD"
        p, got = self.lone_disc_two(rel)
        self.assertEqual(got, [("Song C", "3", "3", "t-3-0"), ("Song D", "3", "3", "t-3-1"),
                               ("Song E", "3", "3", "t-3-2")], (p.status, p.reason, p.why))

    def test_MED22_box2_lone_disc_two_after_a_dvd_layered(self):
        rel = self.dvd_first({CDL, SA2})
        p, got = self.lone_disc_two(rel)
        # (media: DVD 1, disc 1's layers 2-3, disc 2's 4-5: disc 2 is the CD layer at 4, numbered 3 of 3)
        self.assertEqual(got, [("Song C", "3", "3", "t-4-0"), ("Song D", "3", "3", "t-4-1"),
                               ("Song E", "3", "3", "t-4-2")], (p.status, p.reason, p.why))

    def test_MED22_one_folder_hires_copy_of_both_discs(self):
        # (no disc subfolders, no disc tags: every file the SACD layer's)
        top = self.lib / "Piano Player" / "2017 - Greatest Hits"
        for k, t in enumerate(DISC1 + DISC2):
            hires_flac(top, f"{k + 1:02d}.flac", (10, 20, 10, 20, 30)[k],
                       freq=300 + 10 * k, title=t, album="Greatest Hits", albumartist="Piano Player",
                       artist="Piano Player", track=str(k + 1))
        eng = self.engine(te.FakeHttp(mb=[greatest_hits()]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertTrue(p.targets, (p.status, p.reason, p.why))
        self.assertEqual({t.get("media", [""])[0] for t in p.targets}, {SA2}, (p.status, p.reason, p.why, p.targets[0]))
        self.assertEqual(sorted(t["musicbrainz_trackid"][0] for t in p.targets),
                         ["t-2-0", "t-2-1", "t-4-0", "t-4-1", "t-4-2"])

    def test_MED22_cd_layer_disc_one_sacd_layer_disc_two(self):
        top = self.make(hires=(2,))
        eng = self.engine(te.FakeHttp(mb=[greatest_hits()]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertTrue(p.targets, (p.status, p.reason, p.why))
        got = sorted((t["discnumber"][0], t["totaldiscs"][0], t["media"][0], t["musicbrainz_trackid"][0])
                     for t in p.targets)
        self.assertEqual(got, [("1", "2", CDL, "t-1-0"), ("1", "2", CDL, "t-1-1"),
                               ("2", "2", SA2, "t-4-0"), ("2", "2", SA2, "t-4-1"), ("2", "2", SA2, "t-4-2")])

    def test_MED22_hires_stereo_copy_is_the_sacd_layers(self):
        top = self.make(hires=(1, 2))
        eng = self.engine(te.FakeHttp(mb=[greatest_hits()]))
        p = eng.identify(eng.albums_in(top)[0])
        self.assertTrue(p.targets, (p.status, p.reason, p.why))
        got = sorted((t["discnumber"][0], t["totaldiscs"][0], t["media"][0], t["musicbrainz_trackid"][0])
                     for t in p.targets)
        self.assertEqual(got, [("1", "2", SA2, "t-2-0"), ("1", "2", SA2, "t-2-1"),
                               ("2", "2", SA2, "t-4-0"), ("2", "2", SA2, "t-4-1"), ("2", "2", SA2, "t-4-2")])

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


class MED22BoxMedium(unittest.TestCase):
    def test_MED22_a_lone_disc_medium_by_its_database_numbering(self):
        import engine
        rel = {"media": [{"position": 1, "format": "DVD-Video"}, {"position": 2, "format": "CD"},
                         {"position": 3, "format": "CD"}]}
        # (MusicBrainz's disc 1 is its first audio disc: medium 2; Discogs's disc 2 counts the DVD: medium 2)
        self.assertEqual(engine._box_medium(rel, 1, "mb"), 2)
        self.assertEqual(engine._box_medium(rel, 2, "discogs"), 2)
        self.assertEqual(engine._box_medium({"media": [{"position": 1, "format": "CD"}, {"position": 2, "format": "CD"}]},
                                            2, "mb"), 2)


if __name__ == "__main__":
    unittest.main()
