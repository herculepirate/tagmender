# 0.8.80: your copy's own proof (rip logs' disc IDs, a barcode, the release ID in the tags) over
# listing quirks, and lengths never taken as proof (docs/MATCHING_RULES.md):
# - EVI-10: a database's cut-off answer is asked again (Queen - The Crown Jewels, its release ID in
#   the tags: one broken read of a large box left it "unresolved");
# - CAT-8: a disc marker in brackets ("(CD10)" in a rip log's name) is no catalogue number (Philip
#   Glass - Symphonies Nos.1-10, held by "folder catalogue number is not on it");
# - MED-17b: discs numbered as the release's media, a video disc's audio among them (King Crimson -
#   The Complete 1969 Recordings: CDs 1-18, 25, 26 and the DVD's audio as disc 20);
# - DSC-2b: disc folders of one album title are one set whatever album artist each disc credits
#   (Bach - 6 Suites, W. & P. Kuijken; Arvo Part - The Collection);
# - DSC-14b: the box a folder's name names, proven by the disc IDs it has (Madlib - Medicine Show:
#   The Brick), and TOC-1 against a version word in the release's own title;
# - DSC-18: albums ripped from fewer CDs (Buddy Rich - Seven Classic Albums [4CD]);
# - DSC-16: a disc fitted by lengths only is not proven (Joy Division - 1977-1980);
# - DSC-12a: a lone disc matched to a box's disc by lengths alone is never auto;
# - CONF-1: a fingerprint excuses a low confidence only on an album of several tracks;
# - TIE-1 / TOC-3: an exact disc ID several pressings share decides no dispute (Keith Jarrett - Testament);
# - DSC-19: a file the release puts on another disc moves into that disc's folder.
# Stubbed HTTP, no network.
import contextlib
import io
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
from test_v0875_dsc14_disc_id_sets import DiscIdHttp, log_text, mb_toc, sessions, the_set  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as planmod  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])


# ---- EVI-10 --------------------------------------------------------------------------------
class EVI10_BrokenAnswerAskedAgain(unittest.TestCase):
    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def http(self):
        db = sqlite3.connect(":memory:")
        return ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")

    def test_EVI10_a_cut_off_release_page_is_read_again(self):
        http = self.http()
        answers = [ma._HTTPException("IncompleteRead(147032 bytes read, 131975 more expected)"),
                   self.Resp(b'{"id": "bdfde30f"}')]
        with mock.patch("urllib.request.urlopen", side_effect=answers), mock.patch("time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = http.get_text("https://musicbrainz.org/ws/2/release/bdfde30f?fmt=json", "mb")
        self.assertEqual(got, '{"id": "bdfde30f"}')

    def test_EVI10_an_answer_broken_every_time_still_gives_up(self):
        http = self.http()
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionResetError("reset by peer")), \
                mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ma.ProviderUnavailable):
                http.get_text("https://musicbrainz.org/ws/2/release/x?fmt=json", "mb")

    def test_EVI10_text_that_is_not_utf8_is_not_asked_again(self):
        http = self.http()
        calls = []

        def answer(*a, **k):
            calls.append(1)
            return self.Resp(b"\xff\xfe")
        with mock.patch("urllib.request.urlopen", side_effect=answer), mock.patch("time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ma.ProviderUnavailable):
                http.get_text("https://musicbrainz.org/ws/2/release/y?fmt=json", "mb")
        self.assertEqual(len(calls), 1)


class EVI10_TheTaggedReleaseIsACandidate(te.Base):
    def test_EVI10_a_release_id_in_the_tags_is_looked_up_when_no_search_finds_it(self):
        from test_v0864_box_subfolders import box_release
        rel = box_release("bdfde30f-4598-42fb-8cd1-eaf0a9de5374", "Band Q", "The Crown Jewels: A 25th Anniversary Celebration", 2, video=False)
        top = self.lib / "Band Q" / "1998 - The Crown Jewels- A 25th Anniversary Celebration"
        for n in (1, 2):
            for i, s in enumerate(te.LENS):
                flac(top / f"Disc {n}", f"{n}{i + 1:02d}.flac", s / 1000, freq=300 + 50 * n + 10 * i, title=f"D{n}-{i}",
                     album="The Crown Jewels - A 25th Anniversary Celebration", albumartist="Band Q", artist="Band Q", track=str(i + 1), disc=f"{n}/2",
                     musicbrainz_albumid="bdfde30f-4598-42fb-8cd1-eaf0a9de5374")
        plan = self.engine(te.FakeHttp(mb=[rel])).plan(top)        # (no search finds it)
        self.assertEqual([(a["status"], a["identification"]["release_id"]) for a in plan["albums"]],
                         [("ready", "bdfde30f-4598-42fb-8cd1-eaf0a9de5374")], [a["identification"].get("why") for a in plan["albums"]])


# ---- CAT-8 ---------------------------------------------------------------------------------
class CAT8_DiscMarkerIsNoNumber(unittest.TestCase):
    def test_CAT8_disc_markers_in_brackets(self):
        for t in ("Philip Glass - Symphonies Nos.1-10 (CD10)", "Album (CD11)", "Album [Disc11]", "Album (CD-2)",
                  "Album [Disk 3]"):
            self.assertEqual(mc.catno_hints(t), [], t)

    def test_CAT8_a_rip_logs_name_gives_none(self):
        self.assertEqual(ma._path_codes("", "Philip Glass - Symphonies Nos.1-10 (CD10)"), [])

    def test_CAT8_real_numbers_stay(self):
        self.assertEqual(mc.catno_hints("Album (CD123)"), ["CD123"])
        self.assertEqual(mc.catno_hints("Album [Hubro CD2544]"), ["CD2544"])
        self.assertEqual(mc.catno_hints("Album [XLCD 1]"), ["XLCD 1"])


# ---- MED-17b -------------------------------------------------------------------------------
def video_box(rid="mb-box"):
    """Two CDs (3 tracks each), a DVD whose two tracks are audio, a Blu-ray of four, a CD after them."""
    rel = te.mb_release(rid, "Band V", "The Complete Sessions")
    lens = {1: [10, 20, 30], 2: [11, 21, 31], 3: [40, 41], 4: [5, 6, 7, 8], 5: [12, 22]}
    fmts = {1: "CD", 2: "CD", 3: "DVD", 4: "Blu-ray", 5: "CD"}
    rel["media"] = [{"format": fmts[n], "position": n, "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"M{n}-{i}", "length": s * 1000,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"M{n}-{i}"}}
        for i, s in enumerate(lens[n])]} for n in (1, 2, 3, 4, 5)]
    return rel, lens


class MED17b_VideoDiscAudioByNumber(te.Base):
    def test_MED17b_the_view_takes_the_dvd_by_its_number(self):
        rel, lens = video_box()
        cand = ma.mb_candidate(rel)
        self.assertEqual(len(cand["tracks"]), 8)            # (the CDs only: 3 + 3 + 2)
        album = {"tracks": [{"disc": d, "num": i + 1, "title": f"M{d}-{i}", "secs": float(s)}
                            for d in (1, 2, 3, 5) for i, s in enumerate(lens[d])]}
        view = mc.medium_view(album, cand)
        self.assertEqual(view["version_media"], {1: 1, 2: 2, 3: 3, 5: 5})
        self.assertEqual(len(view["tracks"]), 10)
        self.assertEqual(view["version_tracks"][6:8], [[3, 0], [3, 1]])
        # (no video medium among your discs: the audio media as usual)
        plain = {"tracks": [t for t in album["tracks"] if t["disc"] != 3]}
        self.assertIsNone(mc.medium_view(plain, cand))
        # (a disc of yours holding another count than its medium: no view)
        odd = {"tracks": album["tracks"][:-1]}
        self.assertIsNone(mc.medium_view(odd, cand))

    def test_MED17b_scored_whole_and_tagged_by_medium(self):
        rel, lens = video_box()
        top = self.lib / "Band V" / "2020 - The Complete Sessions"
        for d in (1, 2, 3, 5):
            for i, s in enumerate(lens[d]):
                flac(top / f"Disc {d:02d}", f"{d}{i + 1:02d}.flac", s, freq=300 + 40 * d + 10 * i, title=f"M{d}-{i}",
                     album="The Complete Sessions", albumartist="Band V", artist="Band V", track=str(i + 1),
                     disc=f"{d}/5", musicbrainz_albumid="mb-box")
        plan = self.engine(SetHttp(mb=[rel])).plan(top)
        [a] = plan["albums"]
        self.assertEqual((a["status"], a["identification"]["release_id"]), ("ready", "mb-box"),
                         (a["identification"].get("why"), a.get("notes")))
        dvd = [f for f in a["files"] if Path(f["src"]).parent.name == "Disc 03"]
        got = {((f.get("changes") or {}).get("musicbrainz_trackid") or ["kept"])[0] for f in dvd}
        self.assertEqual(got, {"mb-box-3-t0", "mb-box-3-t1"})


# ---- DSC-2b --------------------------------------------------------------------------------
def disc(d, n, album, albumartist, k=3, disc_tag=None):
    for i in range(k):
        flac(d, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 50 * n + 10 * i, title=f"D{n}-{i}", album=album,
             albumartist=albumartist, artist=albumartist, track=str(i + 1),
             **({"disc": disc_tag} if disc_tag else {}))


class DSC2b_PerDiscAlbumArtists(te.Base):
    def groups(self, top):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), top)

    def test_DSC2b_a_disc_crediting_one_more_performer_stays_in_the_set(self):
        top = self.lib / "Bach - 6 Suites (W. Kuijken, P. Kuijken) (2015)"
        disc(top / "CD1 - Suites 1,2,3", 1, "Bach - 6 Suites", "W. Kuijken (cello)", disc_tag="01/03")
        disc(top / "CD2 - Suites 4,6", 2, "Bach - 6 Suites", "W. Kuijken (cello)", disc_tag="02/03")
        disc(top / "CD3 - Suite 5 & Gamba", 3, "Bach - 6 Suites", "W. Kuijken (cello), P. Kuijken (harpsichord)",
             disc_tag="03/03")
        gs = self.groups(top)
        self.assertEqual([len(g.files) for g in gs], [9])

    def test_DSC2b_the_folder_named_as_the_title_joins_discs_tagged_by_their_performers(self):
        top = self.lib / "Arvo Part - The Collection - 2021"
        disc(top / "CD1", 1, "The Collection", "Performer A")
        disc(top / "CD2", 2, "Arvo Part - The Collection - CD2", "Performer B")
        disc(top / "CD3", 3, "Arvo Part - The Collection - CD3", "Performer C")
        self.assertEqual([len(g.files) for g in self.groups(top)], [9])

    def test_DSC2b_different_titles_stay_apart(self):
        top = self.lib / "Band - Two Albums"
        disc(top / "CD1", 1, "Alpha", "Band")
        disc(top / "CD2", 2, "Beta", "Other")
        self.assertEqual(len(self.groups(top)), 2)

    def test_DSC2b_a_disc_tag_against_its_folder_stays_apart(self):
        top = self.lib / "Bach - 6 Suites (2015)"
        disc(top / "CD1", 1, "Bach - 6 Suites", "W. Kuijken (cello)", disc_tag="1/2")
        disc(top / "CD2", 2, "Bach - 6 Suites", "P. Kuijken", disc_tag="1/2")
        self.assertEqual(len(self.groups(top)), 2)

    def test_DSC2b_artists_sharing_no_name_under_another_title_stay_apart(self):
        top = self.lib / "Various - Sampler 2021"
        disc(top / "CD1", 1, "Hits", "Band A")
        disc(top / "CD2", 2, "Hits", "Band B")
        self.assertEqual(len(self.groups(top)), 2)

    def test_DSC2b_the_whole_set_meets_its_barcode(self):
        top = self.lib / "Bach - 6 Suites (W. Kuijken, P. Kuijken) (2015)"
        for n, aa in ((1, "W. Kuijken (cello)"), (2, "W. Kuijken (cello)"), (3, "W. Kuijken (cello), P. Kuijken")):
            for i, s in enumerate(te.LENS):
                flac(top / f"CD{n}", f"{i + 1:02d}.flac", s / 1000, freq=300 + 50 * n + 10 * i, title=f"D{n}-{i}",
                     album="Bach - 6 Suites", albumartist=aa, artist=aa, track=str(i + 1), disc=f"{n}/3",
                     barcode="3760195733837")
        from test_v0864_box_subfolders import box_release
        rel = box_release("mb-bach", "W. Kuijken", "Bach - 6 Suites", 3, video=False)
        rel["barcode"] = "3760195733837"
        plan = self.engine(SetHttp(mb=[rel])).plan(top)
        self.assertEqual([(a["status"], a["identification"]["release_id"], len(a["files"])) for a in plan["albums"]],
                         [("ready", "mb-bach", 9)], [a["identification"].get("why") for a in plan["albums"]])


class NAM12_RolesInBrackets(unittest.TestCase):
    def test_NAM12_a_performers_role_is_no_part_of_the_name(self):
        a = "Wieland Kuijken (violoncello, violoncello piccolo, basse de viole)"
        self.assertGreaterEqual(mc.artist_sim(a, "Wieland Kuijken"), 0.95)
        self.assertGreaterEqual(mc.artist_sim(a + ", Piet Kuijken (clavecin)", "Wieland Kuijken, Piet Kuijken"), 0.95)
        self.assertLess(mc.artist_sim("Anna Other (violin)", "Wieland Kuijken"), 0.5)

    def test_NAM12_a_credit_with_brackets_of_its_own_is_compared_whole(self):
        self.assertEqual(mc.artist_sim("Band (UK)", "Band (UK)"), 1.0)
        self.assertLess(mc.artist_sim("Prince (2)", "Prince"), 0.9)      # (a Discogs number: not a role)


# ---- DSC-14b -------------------------------------------------------------------------------
class DSC14b_TheBoxTheNameNames(te.Base):
    BOX = "Band Q Medicine Show: The Brick"

    def plan(self, name, on, n=4):
        top = sessions(self.lib, n=n, name=name)
        box = self.BOX

        class Http(DiscIdHttp):
            def get_json(self, url, host):
                out = super().get_json(url, host)
                if host == "mb" and "/ws/2/discid/" in url and out:
                    for r in out.get("releases") or ():
                        r["title"] = box if r["id"] == "mb-set" else r["id"]
                return out
        return self.engine(Http(on, mb=[the_set("mb-set", n=n, title=box)])).plan(top)

    def on(self, box_at=None):
        # (discs 1 and 2: on their own volumes only; 3 and 4 on the box too)
        box_at = box_at or {3: 3, 4: 4}
        out = {k: [(f"Medicine Show {k}", 1, 1)] for k in (1, 2, 3, 4)}
        for k, pos in box_at.items():
            out[k] = [("mb-set", pos, 4)] + out[k]
        return out

    def test_DSC14b_named_box_proven_by_some_disc_ids_joins(self):
        plan = self.plan("Band Q - Band Q Medicine Show-The Brick [2012] [FLAC]", self.on())
        self.assertEqual([(a["identification"]["release_id"], len(a["files"])) for a in plan["albums"]],
                         [("mb-set", 12)], [a.get("notes") for a in plan["albums"]])
        self.assertTrue(any("the folder's name names it" in x for x in plan["albums"][0].get("notes") or []))

    def test_DSC14b_a_name_naming_no_release_stays_apart(self):
        plan = self.plan("Band Q - Medicine Sessions [2012]", self.on())
        self.assertGreater(len(plan["albums"]), 1)

    def test_DSC14b_a_disc_id_at_another_place_stays_apart(self):
        plan = self.plan("Band Q - Band Q Medicine Show-The Brick [2012] [FLAC]", self.on({3: 4}))
        self.assertGreater(len(plan["albums"]), 1)

    def test_DSC14b_no_disc_id_on_the_box_stays_apart(self):
        plan = self.plan("Band Q - Band Q Medicine Show-The Brick [2012] [FLAC]",
                         {k: [(f"Medicine Show {k}", 1, 1)] for k in (1, 2, 3, 4)})
        self.assertGreater(len(plan["albums"]), 1)


class TOC1_VersionWordInTheReleasesOwnTitle(unittest.TestCase):
    def test_TOC1_an_exact_disc_id_answers_a_version_word(self):
        a = {"artist": "Band Q", "title": "Medicine Show 12: Raw Medicine", "year": 2011, "discs": 1,
             "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 305.0, 410.0))],
             "barcodes": set(), "catnos": set(), "toc_releases": {"x1"}, "exact_toc_releases": {"x1"},
             "tocs": {1: "1 3 69525 150 15150 38025"}}
        c = {"source": "mb", "id": "x1", "url": "u", "artist": "Band Q", "title": "Medicine Show 12: Raw Medicine: Remixes",
             "year": 2011, "country": "US", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
             "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
        s = mc.score_candidate(a, c, CFG)
        self.assertEqual(s["other_version"], "remixes")
        out = mc.classify([s], CFG)
        self.assertFalse(any("remixes version" in w for w in out["why"]), out["why"])
        a2 = dict(a, toc_releases=set(), exact_toc_releases=set())
        out = mc.classify([mc.score_candidate(a2, c, CFG)], CFG)
        self.assertTrue(any("remixes version" in w for w in out["why"]), out["why"])


# ---- DSC-18 --------------------------------------------------------------------------------
def lp_toc(secs):
    starts, at = [], 0
    for s in secs:
        starts.append(at)
        at += s * 75
    return " ".join(str(x) for x in [1, len(secs), at + 150] + [x + 150 for x in starts])


def lp_log(secs, title):
    starts, at, rows = [], 0, []
    for i, s in enumerate(secs):
        rows.append(f"        {i + 1}  | 00:00:00 | 00:10:00 |     {at}    |    {at + s * 75 - 1}   ")
        at += s * 75
    return ("X Lossless Decoder version 20120407 (140.0)\n\nXLD extraction logfile from 2019-05-16 21:39:17\n\n"
            f"Band Z / {title}\n\nTOC of the extracted CD\n     Track |   Start  |  Length  | Start sector | "
            "End sector \n    ---------------------------------------------------------\n" + "\n".join(rows) + "\n\n")


class DSC18_AlbumsOnFewerCDs(te.Base):
    LPS = {1: [10, 12, 14], 2: [11, 13], 3: [15, 16, 17]}      # (three LPs: 3 + 2 + 3 tracks)
    CDS = [[10, 12, 14, 11], [13, 15, 16, 17]]                  # (two CDs: 4 + 4)

    def make(self, name="2019 - Three Classic Albums [2CD]", logs=True, bad=False):
        top = self.lib / "Band Z" / name
        for n, secs in self.LPS.items():
            for i, s in enumerate(secs):
                flac(top / f"Disc {n}", f"{i + 1:02d}.flac", s, freq=300 + 40 * n + 10 * i, title=f"L{n}-{i}",
                     album=f"{('Alpha', 'Beta', 'Gamma')[n - 1]} [2019 2CD Classic Albums]", albumartist="Band Z", artist="Band Z",
                     track=str(i + 1), disc=str({1: 1955, 2: 1956, 3: 1956}[n]))   # (years: one twice)
        if logs:
            (top / "Disc 2" / "Band Z - Three Classic Albums CD1.log").write_text(
                lp_log([s + (1 if bad else 0) for s in self.CDS[0]], "CD1"))
            (top / "Disc 3" / "Band Z - Three Classic Albums CD2.log").write_text(lp_log(self.CDS[1], "CD2"))
        return top

    def release(self):
        rel = te.mb_release("mb-3", "Band Z", "Three Classic Albums")
        titles = [f"L{n}-{i}" for n, secs in self.LPS.items() for i in range(len(secs))]
        k = 0
        rel["media"] = []
        for p, secs in enumerate(self.CDS, 1):
            tracks = []
            for i, s in enumerate(secs):
                tracks.append({"id": f"mb-3-{p}-t{i}", "position": i + 1, "title": titles[k], "length": s * 1000,
                               "recording": {"id": f"mb-3-{p}-r{i}", "title": titles[k]}})
                k += 1
            rel["media"].append({"format": "CD", "position": p, "tracks": tracks})
        return rel

    def test_DSC18_the_split_reads_each_file_as_its_cds_track(self):
        top = self.make()
        a = ma.read_album(top, "Band Z", top.name)
        self.assertEqual([(t["disc"], t["num"]) for t in a["tracks"]],
                         [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2), (2, 3), (2, 4)])
        self.assertEqual(sorted(a["tocs"]), [1, 2])

    def test_DSC18_one_album_of_the_cd_release(self):
        top = self.make()
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), top)
        self.assertEqual([len(g.files) for g in gs], [8])
        plan = self.engine(SetHttp(mb=[self.release()])).plan(top)
        [a] = plan["albums"]
        self.assertEqual((a["status"], a["identification"]["release_id"]), ("ready", "mb-3"),
                         a["identification"].get("why"))
        got = sorted((Path(f["src"]).parent.name, (f["changes"].get("discnumber") or ["kept"])[0],
                      (f["changes"].get("tracknumber") or ["kept"])[0]) for f in a["files"])
        self.assertIn(("Disc 2", "1", "4"), got)
        self.assertIn(("Disc 2", "2", "1"), got)

    def test_DSC18_logs_that_do_not_fit_keep_the_albums(self):
        top = self.make(bad=True)
        self.assertEqual(len(engine.Engine.albums_in(self.engine(te.FakeHttp()), top)), 3)

    def test_DSC18_no_cd_count_in_the_name_keeps_the_albums(self):
        top = self.make(name="2019 - Three Classic Albums")
        self.assertEqual(len(engine.Engine.albums_in(self.engine(te.FakeHttp()), top)), 3)


# ---- DSC-16 --------------------------------------------------------------------------------
class DSC16_LengthsProveNoDisc(te.Base):
    def setUp(self):
        super().setUp()
        self._was = engine.Engine._disc_id_set
        engine.Engine._disc_id_set = lambda self, folder, parts: None

    def tearDown(self):
        engine.Engine._disc_id_set = self._was
        super().tearDown()

    def test_DSC16_a_disc_only_its_lengths_fit_holds_the_named_set(self):
        import test_v0877_dsc16_disc_id_claim as d16

        class Http(d16.DiscIdHttp):
            # (disc 2's disc ID: on another pressing of its own album only - "Unknown Pleasures"
            # ripped from POCD-1879 -, its lengths the box's disc 2)
            def get_json(self, url, host):
                did = ma.mb_discid(d16.mb_toc(2))
                if host == "mb" and "/ws/2/discid/" in url and did in url:
                    return {"id": did, "releases": [{"id": "mb-other", "title": "Beta", "media": [
                        {"position": 1, "format": "CD", "discs": [{"id": did}]}]}]}
                return super().get_json(url, host)
        top = d16.DSC16_DiscIdClaim.make(self)
        box = d16.release("mb-box", "1977-1980", [1, 2, 3])
        box["media"][1]["discs"] = []
        http = Http(mb=[box, d16.release("mb-single", "Single", [3])])
        plan = self.engine(http).plan(top)
        joined = [a for a in plan["albums"] if len(a["files"]) == 8]
        self.assertEqual(len(joined), 1, [(a["status"], len(a["files"])) for a in plan["albums"]])
        a = joined[0]
        self.assertEqual(a["identification"]["release_id"], "mb-box")
        self.assertTrue(planmod.held(a), a.get("notes"))
        self.assertIn("disc_not_proven", a["identification"]["reason_codes"])
        self.assertIn("'2 - Beta' (disc 2)", a["held"])


# ---- DSC-12a -------------------------------------------------------------------------------
class DSC12a_LengthsOnlyLoneDisc(te.Base):
    """The engine's DSC-12a step on a lone disc matched exactly to disc 1 of a 3-disc box (the
    matcher's match given): no rip log, no code of the box in the files."""

    def prop(self, folder, title="1977-1980"):
        return planmod.Proposal(source_dir=str(folder), files=[str(f) for f in sorted(folder.glob("*.flac"))],
                                status="identified", tier="exact", provider="musicbrainz", release_id="mb-box",
                                confidence=95.0,
                                targets=[{"album": [title], "albumartist": ["Band Y"], "discnumber": ["1"],
                                          "totaldiscs": ["3"], "musicbrainz_albumid": ["mb-box"]} for _ in range(3)])

    def folder(self, parent, log=False):
        top = self.lib / parent / "07 - Alpha sessions"
        for i, s in enumerate(te.LENS):
            flac(top, f"{i + 1:02d}.flac", s / 1000, freq=300 + 10 * i, title=f"D1-{i}", album="Alpha sessions",
                 albumartist="Band Y", artist="Band Y", track=str(i + 1))
        return top

    def test_DSC12a_lengths_only_under_box_words_is_held(self):
        top = self.folder("Band Y - Complete Studio Recordings (fan)")
        eng = self.engine(te.FakeHttp())
        out = eng._disc_not_box(engine.Group(top, sorted(top.glob("*.flac"))), self.prop(top))
        self.assertTrue(getattr(out, "held", ""), out)
        self.assertEqual(out.reason_codes, ["lengths_box_disc"])
        self.assertIn("nothing of your copy", out.held)
        self.assertLess(out.confidence, 90)

    def test_DSC12a_an_own_titled_release_the_matcher_scored_low_is_never_forced(self):
        # (a 5-track release titled as the folder's album - a bootleg of more tracks, scored 43 -
        # is no "own album's release" to tag it as: the box disc stays proposed, held)
        top = self.folder("Band Y - Complete Studio Recordings (fan)")
        own = te.mb_release("mb-own", "Band Y", "Alpha sessions", lens=[10000, 20000, 30000, 40000, 50000])
        eng = self.engine(te.FakeHttp(mb=[own]))
        prop = self.prop(top)
        prop.candidates = [{"source": "musicbrainz", "id": "mb-own", "url": "https://musicbrainz.org/release/mb-own",
                            "title": "Band Y - Alpha sessions (1979)", "score": 43.0}]
        out = eng._disc_not_box(engine.Group(top, sorted(top.glob("*.flac"))), prop)
        self.assertEqual(out.release_id, "mb-box")
        self.assertTrue(getattr(out, "held", ""), out)
        prop = self.prop(top)
        prop.candidates = [dict(c, score=100.0) for c in [{"source": "musicbrainz", "id": "mb-own",
                                                           "url": "https://musicbrainz.org/release/mb-own",
                                                           "title": "Band Y - Alpha sessions (1979)"}]]
        out = eng._disc_not_box(engine.Group(top, sorted(top.glob("*.flac"))), prop)
        self.assertEqual(out.release_id, "mb-own")        # (scored at the threshold: the forced match, as before)

    def test_DSC12a_a_folder_naming_the_release_stands(self):
        top = self.folder("Band Y - Heart Of The Box")
        eng = self.engine(te.FakeHttp())
        out = eng._disc_not_box(engine.Group(top, sorted(top.glob("*.flac"))), self.prop(top, "Heart Of The Box"))
        self.assertFalse(getattr(out, "held", ""))


# ---- CONF-1 --------------------------------------------------------------------------------
class CONF1_FingerprintOnFewTracks(unittest.TestCase):
    def run_on(self, n):
        secs = [100.0 + 37 * i for i in range(n)]
        a = {"artist": "Band T", "title": "Transmission session II (1979-07-28 to 1979-08-04)", "year": 1979,
             "discs": 1, "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)],
             "barcodes": set(), "catnos": set(), "fp_releases": {"x1"}}
        c = {"source": "mb", "id": "x1", "url": "u", "artist": "Band T", "title": "Transmission", "year": 2020,
             "country": "XW", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
             "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)]}
        s = mc.score_candidate(a, c, CFG)
        return s, mc.classify([s], CFG)

    def test_CONF1_a_two_track_single_under_the_threshold_is_held(self):
        s, out = self.run_on(2)
        self.assertLess(s["confidence"], CFG["auto_min_confidence"])
        self.assertTrue(any(w.startswith("confidence") for w in out["why"]), out["why"])
        self.assertNotEqual(out["status"], "auto")

    def test_CONF1_an_album_of_many_tracks_is_excused(self):
        s, out = self.run_on(mc.RECORDINGS_EXCUSE_MIN_TRACKS + 2)
        self.assertFalse(any(w.startswith("confidence") for w in out["why"]), (s["confidence"], out["why"]))


# ---- TIE-1 / TOC-3 -------------------------------------------------------------------------
class TIE1_SharedDiscIdDisputesNothing(unittest.TestCase):
    def test_TIE1_two_pressings_with_your_disc_id_are_suggested_not_written(self):
        secs = (200.0, 305.0, 410.0)
        a = {"artist": "Keith J", "title": "Testament", "year": 2009, "discs": 1,
             "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)],
             "barcodes": set(), "catnos": set(), "toc_releases": {"mb-us", "mb-de"},
             "exact_toc_releases": {"mb-us", "mb-de"}, "tocs": {1: "1 3 69525 150 15150 38025"}}

        def c(src, rid, country):
            return {"source": src, "id": rid, "url": "u", "artist": "Keith J", "title": "Testament", "year": 2009,
                    "country": country, "label": "ECM", "catnos": {"ecm213032"}, "barcodes": set(), "disc_count": 1,
                    "formats": {"cd"}, "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)]}
        cfg = dict(CFG, prefer_source="musicbrainz")
        mb = mc.classify([mc.score_candidate(a, c("mb", "mb-us", "US"), cfg),
                          mc.score_candidate(a, c("mb", "mb-de", "DE"), cfg)], cfg)
        mb["cfg"] = cfg
        self.assertNotEqual(mb["status"], "auto")
        dg = mc.classify([mc.score_candidate(a, c("discogs", "dg-jp", "Japan"), cfg)], cfg)
        dg["cfg"] = cfg
        self.assertEqual(dg["status"], "auto")
        out = mc._proof_left_unsure(mb, dg, "musicbrainz")
        self.assertIsNotNone(out)
        self.assertTrue(out["best"].get("tie_guess"), out)
        others = {x["id"] for x in out["best"]["tie_guess"]["others"]}
        self.assertTrue({"mb-de", "dg-jp"} <= others | {out["best"]["cand"]["id"]}, others)


# ---- DSC-19 --------------------------------------------------------------------------------
class DSC19_DiscFolderAgreesWithTags(unittest.TestCase):
    def test_DSC19_a_file_of_another_disc_moves_into_its_folder(self):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="tm-dsc19-"))
        for d in ("Disc 1", "Disc 2", "Disc 3"):
            (root / d).mkdir()
        f = root / "Disc 3" / "301.A.flac"
        f.write_bytes(b"x")
        g = root / "Disc 3" / "303.MO.flac"
        g.write_bytes(b"x")
        entry = {"files": [{"src": str(f), "dst": str(f), "op": "tag", "changes": {"discnumber": ["2"]}},
                           {"src": str(g), "dst": str(g), "op": "tag", "changes": {"tracknumber": ["1"]}}],
                 "notes": []}
        planmod._disc_folder_moves(entry)
        self.assertEqual((entry["files"][0]["op"], entry["files"][0]["dst"]), ("move", str(root / "Disc 2" / f.name)))
        self.assertEqual(entry["files"][1]["dst"], str(g))
        self.assertTrue(any("moved into Disc 2" in n for n in entry["notes"]))

    def test_DSC19_no_folder_of_that_disc_keeps_it_and_says_so(self):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="tm-dsc19-"))
        (root / "Disc 3").mkdir()
        f = root / "Disc 3" / "301.A.flac"
        f.write_bytes(b"x")
        entry = {"files": [{"src": str(f), "dst": str(f), "op": "tag", "changes": {"discnumber": ["2"]}}], "notes": []}
        planmod._disc_folder_moves(entry)
        self.assertEqual(entry["files"][0]["dst"], str(f))
        self.assertTrue(any("no folder of that disc" in n for n in entry["notes"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
