"""Offline tests for the extra clues (M2 group D):

- gnudb.org (freedb's successor): the disc's table of contents asked over CDDB-over-HTTP;
  its artist/title is a clue and a title to search, scored like any other candidate,
  never proof; not set up (no registered e-mail) or offline: skipped, noted, carried on.
- the profile's "acoustid": unsure | always | off.
- the lossy-source (transcode) check of lossless files, a quality note at Audiophile only.

Run:  python3 tests/test_clues.py [plugin-dir]
Needs mutagen and ffmpeg (to build the tiny synthetic albums). No network: every answer is canned.
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.pop("TAGMENDER_GNUDB_USER", None)          # set per test
_os.environ.pop("TAGMENDER_ACOUSTID_KEY", None)

import io
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN = Path(_os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or
              (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else
               "../picard-discogs-enrichment")).expanduser()
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENS = [10000, 20000, 30000]           # the generated tracks last 10, 20 and 30 s (whole CD sectors)
EMAIL = "someone@example.org"
# the files' table of contents: 10, 20 and 30 s tracks from frame 150
TOC = "1 3 4650 150 900 2400"
OTHER_UPC = "075678264320"


def xmcd(dtitle="Artist Alpha / First Light (CD1)", offsets=(150, 900, 2400), seconds=62, titles=None,
         year="2010", genre="Ambient"):
    titles = titles or ["T0", "T1", "T2"]
    lines = ["# xmcd", "#", "# Track frame offsets:"] + [f"#\t{o}" for o in offsets] + \
        ["#", f"# Disc length: {seconds} seconds", "#", "DISCID=0b003e03"]
    # a long title runs over two lines, as freedb stores it
    lines += [f"DTITLE={dtitle[:10]}", f"DTITLE={dtitle[10:]}", f"DYEAR={year}", f"DGENRE={genre}"]
    lines += [f"TTITLE{i}={t}" for i, t in enumerate(titles)] + ["EXTD=", "PLAYORDER="]
    return "\n".join(lines)


def mb_release(rid, title, barcode="", recordings=None):
    recs = recordings or [f"rec-{i}" for i in range(3)]
    return {"id": rid, "title": title, "date": "2010", "country": "CA", "barcode": barcode,
            "artist-credit": [{"name": "Artist Alpha", "joinphrase": ""}], "label-info": [],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms, "recording": {"id": recs[i]}}
                                                  for i, ms in enumerate(LENS)]}]}


class ClueHttp:
    """Canned answers. MusicBrainz knows the album only as "First Light"; gnudb holds it for
    the files' TOC. Requests to anything else are recorded as unexpected."""

    def __init__(self, gnudb_query=None, gnudb_read=None, offline=(), mb_barcode="", acoustid=None):
        self.calls, self.unexpected = [], []
        self.mb = {"mb-first": mb_release("mb-first", "First Light", mb_barcode)}
        self.mb_titles = {"First Light": "mb-first"}
        did = ma.cddb_discid(TOC)
        self.gnudb_query = (f"200 misc {did} Artist Alpha / First Light (CD1)" if gnudb_query is None
                            else gnudb_query)
        self.gnudb_read = (f"210 misc {did} CD database entry follows (until terminating `.')\n" + xmcd() + "\n."
                           if gnudb_read is None else gnudb_read)
        self.offline = set(offline)
        self.acoustid = acoustid or {}

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host in self.offline:
            raise ma.ProviderUnavailable(host, "outage", "URLError")
        if host == "mb":
            if "/ws/2/discid/" in url:
                return {}
            if "/ws/2/release/?" in url:
                query = up.unquote(url.split("query=")[1].split("&")[0])
                return {"releases": [{"id": rid, "track-count": 3} for t, rid in self.mb_titles.items()
                                     if f'release:"{t}"' in query or (self.mb[rid]["barcode"]
                                                                      and f"barcode:{self.mb[rid]['barcode']}" in query)]}
            if "/ws/2/release/" in url:
                return self.mb.get(url.split("/release/")[1].split("?")[0])
            return {}
        if host == "acoustid":
            return self.acoustid
        if host in ("deezer", "deezer-upc", "itunes-upc"):
            return {}
        self.unexpected.append((host, url))
        raise AssertionError(f"unexpected request {host} {url}")

    def get_text(self, url, host, key=None):
        self.calls.append((host, url))
        if host in self.offline:
            raise ma.ProviderUnavailable(host, "outage", "URLError")
        if host == "ctdb":
            return ""
        if host == "gnudb":
            assert key and EMAIL.split("@")[0] not in key, key     # the address never in the cache key
            return self.gnudb_query if "cmd=cddb+query" in url else self.gnudb_read
        self.unexpected.append((host, url))
        raise AssertionError(f"unexpected request {host} {url}")

    def asked(self, host):
        return [u for h, u in self.calls if h == host]


def make_album(root, title, barcode="", rate=44100):
    d = Path(root) / "Artist Alpha" / f"2010 - {title}"
    d.mkdir(parents=True)
    for i in range(3):
        meta = ["-metadata", f"title=T{i}", "-metadata", f"album={title}", "-metadata", "albumartist=Artist Alpha",
                "-metadata", "date=2010", "-metadata", f"track={i + 1}"]
        if barcode:
            meta += ["-metadata", f"barcode={barcode}"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}:sample_rate={rate}", "-ac", "1",
                        "-sample_fmt", "s16", *meta, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
    return {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}


# --------------------------------------------------------------------------
# gnudb
# --------------------------------------------------------------------------
class CddbBasics(unittest.TestCase):
    def test_disc_id(self):
        # start seconds 2, 12, 32 -> digit sums 2 + 3 + 5 = 10; 62 - 2 = 60 s; 3 tracks
        self.assertEqual(ma.cddb_discid(TOC), f"{(10 << 24) | (60 << 8) | 3:08x}")
        # a real disc: the TOC in libdiscid's own test (test_put.c), whose expected freedb ID is
        # 370fce16 and MusicBrainz disc ID xUp1F2NkfP8s8jaeFn_Av3jNEI4- (a check that the TOC is right)
        toc = ("1 22 303602 150 9700 25887 39297 53795 63735 77517 94877 107270 123552 135522 148422 "
               "161197 174790 192022 205545 218010 228700 239590 255470 266932 288750")
        self.assertEqual(ma.mb_discid(toc), "xUp1F2NkfP8s8jaeFn_Av3jNEI4-")
        self.assertEqual(ma.cddb_discid(toc), "370fce16")

    def test_xmcd_read(self):
        e = ma.parse_xmcd(xmcd())
        self.assertEqual((e["artist"], e["title"], e["year"], e["genre"]),
                         ("Artist Alpha", "First Light (CD1)", 2010, "Ambient"))
        self.assertEqual(e["tracks"], ["T0", "T1", "T2"])
        self.assertEqual((e["offsets"], e["seconds"]), ([150, 900, 2400], 62))
        self.assertEqual(ma.gnudb_title(e), "First Light")
        one = ma.parse_xmcd(xmcd(dtitle="Only One Name"))
        self.assertEqual((one["artist"], one["title"]), ("Only One Name", "Only One Name"))

    def test_entry_fits_only_the_same_lengths(self):
        self.assertTrue(ma.gnudb_fits(TOC, ma.parse_xmcd(xmcd()), CFG))
        self.assertFalse(ma.gnudb_fits(TOC, ma.parse_xmcd(xmcd(offsets=(150, 3000, 4000), seconds=90)), CFG))
        self.assertFalse(ma.gnudb_fits(TOC, ma.parse_xmcd(xmcd(titles=["a", "b"])), CFG))
        # a fuzzy entry that differs only in its last track (the disc runs 40 s longer)
        self.assertFalse(ma.gnudb_fits(TOC, ma.parse_xmcd(xmcd(seconds=102)), CFG))
        self.assertTrue(ma.gnudb_fits(TOC, ma.parse_xmcd(xmcd(seconds=63)), CFG))   # a rounding second

    def test_query_answers(self):
        did = ma.cddb_discid(TOC)
        http = ClueHttp(gnudb_query=f"211 Found inexact matches, list follows (until terminating `.')\n"
                                    f"rock {did} A / B\nmisc 0a0b0c0d C / D\n.")
        self.assertEqual(ma.gnudb_matches(http, TOC, EMAIL), [("rock", did), ("misc", "0a0b0c0d")])
        self.assertEqual(ma.gnudb_matches(ClueHttp(gnudb_query="202 No match found"), TOC, EMAIL), [])
        http = ClueHttp()
        ma.gnudb_matches(http, TOC, EMAIL)
        q = http.asked("gnudb")[0]
        self.assertTrue(q.startswith("https://gnudb.gnudb.org/~cddb/cddb.cgi?cmd=cddb+query+"), q)
        self.assertIn(f"+{did}+3+150+900+2400+62&", q)
        self.assertIn("hello=someone+example.org+TagMender+", q)
        self.assertTrue(q.endswith("&proto=6"))

    def test_refusal_is_raised_and_forgotten(self):
        http = ClueHttp(gnudb_query="409 No handshake")
        http.db = sqlite3.connect(":memory:")
        http.db.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        key = f"{ma.GNUDB_URL}?cmd=cddb+query+{ma.cddb_discid(TOC)}+3+150+900+2400+62&proto=6"
        http.db.execute("INSERT INTO http_cache VALUES (?, '409 No handshake', 0)", (key,))
        with self.assertRaises(ma.ProviderUnavailable):
            ma.gnudb_matches(http, TOC, EMAIL)
        self.assertEqual(http.db.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0], 0)

    def test_unknown_application_said_plainly(self):
        """gnudb refusing the application itself is not blamed on the user's address."""
        http = ClueHttp(gnudb_query="500 Unknown application, developer email for TagMender 0.2.0")
        http.host_failed = lambda host: None
        found, notes = ma.gnudb_lookup(http, {"tocs": {1: TOC}}, CFG, EMAIL)
        self.assertEqual((found, len(notes)), ([], 1))
        self.assertIn("registered applications only", notes[0])
        self.assertNotIn("check the e-mail", notes[0])

    def test_refusal_named_and_pauses_gnudb(self):
        """A refused hello is noted as a refusal (not as being offline), and counts as a
        failure of the host: after three, gnudb is not asked for 30 minutes."""
        http = ClueHttp(gnudb_query="431 No such user")
        failed = []
        http.host_failed = failed.append
        found, notes = ma.gnudb_lookup(http, {"tocs": {1: TOC}}, CFG, EMAIL)
        self.assertEqual((found, failed), ([], ["gnudb"]))
        self.assertEqual(len(notes), 1)
        self.assertIn("gnudb refused the lookup (431 No such user)", notes[0])
        self.assertIn("registered", notes[0])
        self.assertNotIn("offline", notes[0])
        # through the real Http: three refusals, then no request at all
        db = sqlite3.connect(":memory:")
        real = ma.Http(db, dict(CFG, mb_delay=0, discogs_delay=0), "")
        sent = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            sent.append(req.full_url)
            return Answer(b"431 No such user\r\n")
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), mock.patch.object(ma.time, "sleep"):
            for _ in range(5):
                ma.gnudb_lookup(real, {"tocs": {1: TOC}}, CFG, EMAIL)
        self.assertEqual(len(sent), 3)
        self.assertTrue(real.host_down("gnudb"))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0], 0)   # never cached
        # a real answer in between forgets the refusals: a busy moment does not add up
        real.down.clear()
        answers = iter([b"431 Busy\r\n", b"202 No match found\r\n"] * 3)
        with mock.patch.object(ma.urllib.request, "urlopen", lambda req, timeout=None: Answer(next(answers))), \
                mock.patch.object(ma.time, "sleep"):
            for i in range(6):
                ma.gnudb_lookup(real, {"tocs": {1: TOC}}, CFG, EMAIL)
                db.execute("DELETE FROM http_cache")
        self.assertFalse(real.host_down("gnudb"))

    def test_titles_compared_with_the_entrys_own_disc(self):
        """A gnudb entry holds one disc, a release every disc: disc 2's entry is compared
        with the release's disc 2."""
        entry = dict(ma.parse_xmcd(xmcd(titles=["Dawn", "Noon", "Dusk"])), disc=2)
        cand = {"tracks": [{"disc": 1, "title": "Intro"}, {"disc": 1, "title": "Outro"}]
                + [{"disc": 2, "title": t} for t in ("Dawn", "Noon", "Night")]}
        self.assertIn(": 2/3 track titles agree with the pick", ma.gnudb_note(entry, cand))
        # disc 1's entry against the same release: its 3 titles fit no 2-track disc
        self.assertNotIn("track titles", ma.gnudb_note(dict(entry, disc=1), cand))
        # one disc of a box, picked as a release of its own (numbered disc 1)
        one = {"tracks": [{"disc": 1, "title": t} for t in ("Dawn", "Noon", "Dusk")]}
        self.assertIn(": 3/3 track titles agree", ma.gnudb_note(entry, one))

    def test_cached_without_the_address(self):
        """Through the real Http: the address is sent, but the cache key leaves it out, and a
        second ask comes from the cache."""
        db = sqlite3.connect(":memory:")
        http = ma.Http(db, dict(CFG, mb_delay=0, discogs_delay=0), "")
        sent = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            sent.append(req.full_url)
            return Answer(b"202 No match found\r\n")
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), mock.patch.object(ma.time, "sleep"):
            self.assertEqual(ma.gnudb_matches(http, TOC, EMAIL), [])
            self.assertEqual(ma.gnudb_matches(http, TOC, EMAIL), [])
        self.assertEqual(len(sent), 1)
        self.assertIn("someone+example.org", sent[0])
        keys = [k for (k,) in db.execute("SELECT url FROM http_cache")]
        self.assertEqual(len(keys), 1)
        self.assertNotIn("someone", keys[0])
        self.assertIn("gnudb", ma.Http.OPTIONAL_HOSTS)          # one try, paused after failures
        self.assertGreaterEqual(ma.Http.HOST_DELAYS["gnudb"], 1.0)

    def test_user_from_a_private_file_only(self):
        import credentials
        with mock.patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("TAGMENDER_GNUDB_USER", None)
            self.assertEqual(ma.gnudb_user(), "")
            p = credentials.store("gnudb", EMAIL)
            try:
                self.assertEqual(ma.gnudb_user(), EMAIL)
                _os.chmod(p, 0o644)
                self.assertFalse(ma._gnudb_ready())               # readable by others: refused
                # ... and the note says to fix the file, not to register an address again
                self.assertIn("chmod 600", ma.gnudb_problem())
                self.assertNotEqual(ma.gnudb_problem(), ma.GNUDB_NOT_SET)
                import engine
                import profiles as pf
                eng = engine.Engine(pf.from_dict({"name": "default"}), http=types.SimpleNamespace(db=None),
                                    ocr="off")
                self.assertIn("chmod 600", eng.notes.get("gnudb", ""))           # the run's note too
                p.unlink()
                self.assertEqual(ma.gnudb_problem(), ma.GNUDB_NOT_SET)   # nothing set up
            finally:
                p.unlink(missing_ok=True)


class GnudbPipeline(unittest.TestCase):
    """ma.process with gnudb: its title is searched, the hits scored against the files."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.dm = ma.load_plugin(str(PLUGIN))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))
        self.https = []
        self.env = mock.patch.dict(_os.environ, {"TAGMENDER_GNUDB_USER": EMAIL})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for http in self.https:
            self.assertEqual(http.unexpected, [])

    def run_album(self, http, title="Erstes Licht", barcode="", dm=False, **args):
        self.https.append(http)
        row = make_album(self.root, title, barcode)
        return ma.process(row, http, CFG, self.dm if dm else None,
                          types.SimpleNamespace(ocr=False, use_shops=False, **args))

    def test_gnudb_title_searched_and_noted(self):
        http = ClueHttp()
        o = self.run_album(http)
        self.assertEqual(len(http.asked("gnudb")), 2)                  # query, then read
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        note = "gnudb: Artist Alpha - First Light (CD1) (2010, Ambient): 3/3 track titles agree with the pick"
        self.assertIn(note, o["gnudb_notes"])
        # a clue only: never the match's own evidence nor a reason
        self.assertNotIn(note, o["best"]["notes"])
        self.assertNotIn(note, o["why"])
        self.assertIn(note, ma.to_row(o)["Notes"])
        import engine
        self.assertIn(note, engine.evidence_of(o, o["best"]))
        self.assertNotEqual(o["status"], "auto", o["why"])              # the files' title still disagrees

    def test_gnudb_hit_scores_as_it_would_alone(self):
        """The release gnudb's title finds is scored against the files exactly as if any
        other search had found it: gnudb lends it nothing."""
        http = ClueHttp()
        o = self.run_album(http, title="First Light!")
        album = ma.read_album(self.root / "Artist Alpha" / "2010 - First Light!", "Artist Alpha", "2010 - First Light!")
        alone = mc.score_candidate(album, ma.mb_candidate(http.mb["mb-first"]), CFG)
        self.assertEqual(o["best"]["confidence"], alone["confidence"])

    def test_gnudb_title_searched_on_discogs_and_scored_alone(self):
        """With Discogs on, gnudb's title is searched there too; the release found is scored
        against the files as it would be alone."""
        det = {"id": 701, "title": "First Light", "year": 2010, "uri": "https://www.discogs.com/release/701",
               "artists": [{"name": "Artist Alpha"}], "labels": [{"name": "Lbl", "catno": "LB-1"}],
               "identifiers": [], "country": "Canada", "formats": [{"name": "CD", "qty": "1"}],
               "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}",
                              "duration": f"0:{10 * (i + 1)}"} for i in range(3)]}

        class DiscogsHttp(ClueHttp):
            def get_json(self, url, host):
                if host != "discogs":
                    return super().get_json(url, host)
                self.calls.append((host, url))
                if "/database/search?" in url:
                    query = up.parse_qs(url.split("?", 1)[1])
                    hit = query.get("release_title") == ["First Light"]
                    return {"results": [{"id": 701, "title": "Artist Alpha - First Light"}] if hit else []}
                if url.endswith("/releases/701"):
                    return det
                return {}
        http = DiscogsHttp()
        http.mb_titles = {}                                  # MusicBrainz finds nothing: Discogs alone
        o = self.run_album(http, dm=True)
        searches = [up.parse_qs(u.split("?", 1)[1]) for u in http.asked("discogs") if "/database/search?" in u]
        self.assertTrue(any(s.get("release_title") == ["First Light"] for s in searches), searches)
        self.assertEqual(str(o["best"]["id"]), "701")
        album = ma.read_album(self.root / "Artist Alpha" / "2010 - Erstes Licht", "Artist Alpha", "2010 - Erstes Licht")
        alone = mc.score_candidate(album, ma.discogs_candidate(det, self.dm), CFG)
        self.assertEqual(o["best"]["confidence"], alone["confidence"])
        self.assertNotEqual(o["status"], "auto", o["why"])

    def test_wrong_pressing_never_auto(self):
        """gnudb's title finds the album, but its barcode is not the files' one. The same
        album with no barcode conflict goes auto, so the barcode is what holds it back."""
        same = self.run_album(ClueHttp(mb_barcode=""), title="First Light!", barcode="724384960650")
        self.assertEqual(same["status"], "auto", same["why"])
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))             # the same folder name again
        o = self.run_album(ClueHttp(mb_barcode=OTHER_UPC), title="First Light!", barcode="724384960650")
        self.assertEqual(o["best"]["id"], "mb-first")
        self.assertNotEqual(o["status"], "auto")
        self.assertLess(o["best"]["confidence"], same["best"]["confidence"])
        self.assertIn("barcode differs from the candidate's", o["why"])

    def test_not_set_up_skipped_silently(self):
        _os.environ.pop("TAGMENDER_GNUDB_USER")
        http = ClueHttp()
        o = self.run_album(http)
        self.assertEqual(http.asked("gnudb"), [])
        self.assertNotIn("gnudb_notes", o)

    def test_switched_off(self):
        http = ClueHttp()
        self.run_album(http, use_gnudb=False)
        self.assertEqual(http.asked("gnudb"), [])

    def test_offline_noted_and_carried_on(self):
        http = ClueHttp(offline=("gnudb",))
        o = self.run_album(http)
        self.assertIn("gnudb lookup failed (offline or unavailable); carried on without it", o["gnudb_notes"])
        self.assertNotEqual(o["status"], "error")                       # not a provider of the match

    def test_entry_that_does_not_fit_is_not_searched(self):
        did = ma.cddb_discid(TOC)
        http = ClueHttp(gnudb_read=f"210 misc {did} entry follows\n"
                                   + xmcd(offsets=(150, 3000, 4000), seconds=90) + "\n.")
        o = self.run_album(http)
        self.assertFalse(any("First Light" in up.unquote(u) for u in http.asked("mb") if "query=" in u))
        self.assertNotIn("gnudb_notes", o)

    def test_no_toc_not_asked(self):
        http = ClueHttp()
        self.https.append(http)
        row = make_album(self.root, "Erstes Licht")
        for f in Path(row["FullPath"]).glob("*.flac"):          # 48 kHz: not a CD rip, no TOC
            subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(f), "-ar", "48000",
                            str(f.with_suffix(".tmp.flac")), "-y"], check=True)
            f.with_suffix(".tmp.flac").replace(f)
        ma.process(row, http, CFG, None, types.SimpleNamespace(ocr=False, use_shops=False))
        self.assertEqual(http.asked("gnudb"), [])

    def test_not_asked_when_already_sure(self):
        http = ClueHttp(mb_barcode="724384960650")
        o = self.run_album(http, title="First Light", barcode="724384960650")
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(http.asked("gnudb"), [])


# --------------------------------------------------------------------------
# AcoustID: unsure | always | off
# --------------------------------------------------------------------------
def acoustid_answer(recording):
    return {"status": "ok", "results": [{"id": "aid", "score": 0.95,
                                         "recordings": [{"id": recording, "releases": [{"id": "mb-first"}]}]}]}


class AcoustidMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))
        self.patches = [mock.patch.object(ma, "fingerprint_ready", return_value=True),
                        mock.patch.object(ma, "acoustid_key", return_value="k"),
                        mock.patch.object(ma, "fingerprint", return_value={"duration": 10, "fingerprint": "AQ"}),
                        mock.patch.object(ma.shutil, "which", side_effect=lambda t, *a, **k: f"/usr/bin/{t}")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_album(self, http, mode, title="First Light", barcode="724384960650"):
        row = make_album(self.root, title, barcode)
        return ma.process(row, http, CFG, None, types.SimpleNamespace(ocr=False, use_shops=False, use_gnudb=False,
                                                                       acoustid=mode))

    def test_mode_values(self):
        self.assertEqual(ma.acoustid_mode(None), "unsure")
        self.assertEqual(ma.acoustid_mode("Always"), "always")
        self.assertEqual(ma.acoustid_mode("nonsense"), "unsure")

    def test_unsure_leaves_a_sure_album_alone(self):
        http = ClueHttp(mb_barcode="724384960650", acoustid=acoustid_answer("rec-other"))
        o = self.run_album(http, "unsure")
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(http.asked("acoustid"), [])
        self.assertNotIn("acoustid_notes", o)

    def test_always_notes_a_disagreement_and_keeps_the_pick(self):
        http = ClueHttp(mb_barcode="724384960650", acoustid=acoustid_answer("rec-other"))
        o = self.run_album(http, "always")
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(o["best"]["id"], "mb-first")
        self.assertEqual(len(http.asked("acoustid")), 3)                 # first, middle, last
        self.assertEqual(len(o["acoustid_notes"]), 1)
        self.assertIn("AcoustID disagrees", o["acoustid_notes"][0])
        self.assertIn("tracks 1, 2, 3", o["acoustid_notes"][0])
        self.assertIn(o["acoustid_notes"][0], ma.to_row(o)["Notes"])
        self.assertNotIn(o["acoustid_notes"][0], o["best"]["notes"])
        import engine
        self.assertIn(o["acoustid_notes"][0], engine.evidence_of(o, o["best"]))   # the reviewer sees it

    def test_always_offline_keeps_the_pick(self):
        http = ClueHttp(mb_barcode="724384960650", offline=("acoustid",))
        o = self.run_album(http, "always")
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(o["best"]["id"], "mb-first")
        self.assertEqual(len(o["acoustid_notes"]), 1, o.get("acoustid_notes"))
        self.assertIn("carried on without it", o["acoustid_notes"][0])

    def test_always_skips_a_pick_without_musicbrainz(self):
        # nothing to compare the fingerprints with: no fpcalc, no AcoustID request
        http = ClueHttp(acoustid=acoustid_answer("rec-other"))
        with mock.patch.object(ma, "acoustid_matches") as matches:
            self.assertEqual(ma.acoustid_disagreement(http, {"tracks": []}, ""), "")
        matches.assert_not_called()
        http = ClueHttp(mb_barcode="724384960650", acoustid=acoustid_answer("rec-other"))
        row = make_album(self.root, "First Light", "724384960650")
        with mock.patch.object(ma, "acoustid_disagreement") as check:
            ma.process(row, http, CFG, None, types.SimpleNamespace(ocr=False, use_shops=False, use_gnudb=False,
                                                                   acoustid="always", use_mb=False))
        check.assert_not_called()

    def test_always_quiet_when_the_fingerprints_agree(self):
        class Agreeing(ClueHttp):
            def get_json(self, url, host):
                if host == "acoustid":
                    self.calls.append((host, url))
                    n = sum(1 for h, _u in self.calls if h == "acoustid") - 1
                    return acoustid_answer(f"rec-{[0, 1, 2][n]}")
                return super().get_json(url, host)
        http = Agreeing(mb_barcode="724384960650")
        o = self.run_album(http, "always")
        self.assertEqual(o["status"], "auto")
        self.assertEqual(len(http.asked("acoustid")), 3)
        self.assertNotIn("acoustid_notes", o)

    def test_always_notes_one_track_of_three(self):
        class OneOff(ClueHttp):
            def get_json(self, url, host):
                if host == "acoustid":
                    self.calls.append((host, url))
                    n = sum(1 for h, _u in self.calls if h == "acoustid") - 1
                    return acoustid_answer(["rec-0", "rec-other", "rec-2"][n])
                return super().get_json(url, host)
        o = self.run_album(OneOff(mb_barcode="724384960650"), "always")
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(len(o["acoustid_notes"]), 1)
        note = o["acoustid_notes"][0]
        self.assertIn("the fingerprint of track 2 points", note)
        self.assertIn("(of 3 fingerprinted)", note)
        self.assertNotIn("track 1", note)
        self.assertTrue(o["acoustid_checked"])

    def test_disagreement_quiet_when_the_track_counts_differ(self):
        """A release with another track count than the files: positions cannot be compared."""
        row = make_album(self.root, "First Light", "724384960650")
        album = ma.read_album(Path(row["FullPath"]), "Artist Alpha", row["AlbumFolder"])
        http = ClueHttp(acoustid=acoustid_answer("rec-other"))
        self.assertIn("AcoustID disagrees", ma.acoustid_disagreement(http, album, "mb-first"))
        http.mb["mb-first"]["media"][0]["tracks"].append({"title": "T3", "length": 1000, "recording": {"id": "rec-3"}})
        self.assertEqual(ma.acoustid_disagreement(http, album, "mb-first"), "")

    def test_always_rechecks_sure_albums_matched_before(self):
        """The CLI keeps stored results; with --acoustid always, a sure match made without the
        check is redone, one checked (or failed: retried) is not."""
        self.assertTrue(ma.acoustid_unchecked({"status": "auto"}, "always"))
        self.assertFalse(ma.acoustid_unchecked({"status": "auto", "acoustid_checked": True}, "always"))
        self.assertFalse(ma.acoustid_unchecked({"status": "review"}, "always"))
        self.assertFalse(ma.acoustid_unchecked({"status": "auto"}, "unsure"))
        with mock.patch.object(ma, "fingerprint_ready", return_value=False):
            self.assertFalse(ma.acoustid_unchecked({"status": "auto"}, "always"))
        o = self.run_album(ClueHttp(mb_barcode="724384960650", acoustid=acoustid_answer("rec-0")), "always")
        self.assertIs(o["acoustid_checked"], True)
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))
        o = self.run_album(ClueHttp(mb_barcode="724384960650", offline=("acoustid",)), "always")
        self.assertIs(o["acoustid_checked"], False)                       # asked again next run
        self.assertTrue(ma.acoustid_unchecked(o, "always"))
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))
        o = self.run_album(ClueHttp(mb_barcode="724384960650"), "unsure")
        self.assertNotIn("acoustid_checked", o)

    def test_off_never_fingerprints(self):
        http = ClueHttp(acoustid=acoustid_answer("rec-0"))
        o = self.run_album(http, "off", title="Erstes Licht", barcode="")
        self.assertNotEqual(o["status"], "auto")
        self.assertEqual(http.asked("acoustid"), [])

    def test_unsure_still_fingerprints_an_unsure_album(self):
        http = ClueHttp(acoustid=acoustid_answer("rec-0"))
        self.run_album(http, "unsure", title="Erstes Licht", barcode="")
        self.assertTrue(http.asked("acoustid"))


class Settings(unittest.TestCase):
    def test_profile_settings(self):
        import profiles as pf
        p = pf.from_dict({"name": "x"})
        self.assertEqual(p.get("acoustid"), "unsure")
        self.assertTrue(p.provider_on("web", "gnudb"))
        self.assertTrue(pf.validate({"name": "x", "acoustid": "sometimes"}))
        self.assertEqual(pf.validate({"name": "x", "acoustid": "always"}), [])
        self.assertFalse(pf.from_dict({"name": "x", "providers": {"web": {"enabled": True, "gnudb": False}}})
                         .provider_on("web", "gnudb"))
        # the Audiophile item (writing each track's AcoustID) is a separate setting
        self.assertTrue(pf.from_dict({"name": "x", "acoustid": "off"}).get("audiophile")["acoustid"])

    def test_command_line(self):
        a = ma.arg_parser().parse_args(["audit.csv"])
        self.assertEqual((a.use_gnudb, a.acoustid), (True, "unsure"))
        a = ma.arg_parser().parse_args(["audit.csv", "--no-gnudb", "--acoustid", "always"])
        self.assertEqual((a.use_gnudb, a.acoustid), (False, "always"))

    def test_engine_note_and_arguments(self):
        import engine
        import profiles as pf

        class NoHttp:
            db = None
        _os.environ.pop("TAGMENDER_GNUDB_USER", None)
        e = engine.Engine(pf.from_dict({"name": "default"}), http=NoHttp(), ocr="off")
        self.assertIn("gnudb", e.notes)                                  # the run's one note
        with mock.patch.dict(_os.environ, {"TAGMENDER_GNUDB_USER": EMAIL}):
            e = engine.Engine(pf.from_dict({"name": "default"}), http=NoHttp(), ocr="off")
            self.assertNotIn("gnudb", e.notes)
        off = pf.from_dict({"name": "default", "providers": {"web": {"enabled": True, "gnudb": False}}})
        self.assertNotIn("gnudb", engine.Engine(off, http=NoHttp(), ocr="off").notes)

    def test_always_without_fingerprints_noted_once(self):
        import engine
        import profiles as pf

        class NoHttp:
            db = None
        with mock.patch.object(ma, "fingerprint_ready", return_value=False):
            e = engine.Engine(pf.from_dict({"name": "x", "acoustid": "always"}), http=NoHttp(), ocr="off")
            self.assertIn("fpcalc", e.notes.get("acoustid", ""))
            self.assertIn("AcoustID application key", e.notes["acoustid"])
            self.assertNotIn("acoustid", engine.Engine(pf.from_dict({"name": "x", "acoustid": "off"}),
                                                       http=NoHttp(), ocr="off").notes)
        with mock.patch.object(ma, "fingerprint_ready", return_value=True):
            e = engine.Engine(pf.from_dict({"name": "x", "acoustid": "always"}), http=NoHttp(), ocr="off")
            self.assertNotIn("acoustid", e.notes)

    def test_default_unsure_without_key_or_fpcalc_is_noted(self):
        # the default profile (acoustid: unsure), no fpcalc and no AcoustID key: the run says so once
        import engine
        import profiles as pf

        class NoHttp:
            db = None
        with mock.patch.object(ma.shutil, "which", return_value=None), \
                mock.patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("TAGMENDER_ACOUSTID_KEY", None)
            e = engine.Engine(pf.default(), http=NoHttp(), ocr="off")
        note = e.notes.get("acoustid", "")
        self.assertIn("not identified for sure are not fingerprinted", note)
        self.assertIn("fpcalc (Chromaprint) is not installed", note)
        self.assertIn("no AcoustID application key", note)
        self.assertIn("acoustid", engine.RUN_NOTES)                       # (never an album's error)

    def test_acoustid_key_file_others_can_read_is_not_used(self):
        import credentials
        import engine
        import profiles as pf
        import requirements as rq

        class NoHttp:
            db = None
        p = credentials.path_of("acoustid")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("made-up-acoustid-key\n")
        p.chmod(0o644)
        try:
            with mock.patch.dict(_os.environ, {}, clear=False), \
                    mock.patch.object(ma.shutil, "which", return_value="/usr/bin/fpcalc"):
                _os.environ.pop("TAGMENDER_ACOUSTID_KEY", None)
                self.assertEqual(ma.acoustid_key(), "")
                self.assertFalse(ma.fingerprint_ready())
                self.assertEqual(rq.status("acoustid"), rq.UNSAFE)
                e = engine.Engine(pf.default(), http=NoHttp(), ocr="off")
            self.assertIn("can be read by other users", e.notes.get("acoustid", ""))
            self.assertNotIn("made-up-acoustid-key", e.notes["acoustid"])
        finally:
            p.unlink()

    def test_always_note_kept_out_of_the_albums_errors(self):
        root = Path(tempfile.mkdtemp())
        row = make_album(root, "First Light")
        with mock.patch.object(ma, "fingerprint_ready", return_value=False):
            import engine
            import profiles as pf
            got = []
            with mock.patch.object(ma, "process", side_effect=lambda row, http, cfg, dm, args: got.append(args)), \
                    mock.patch.object(engine.Engine, "_web", return_value=None):
                e = engine.Engine(pf.from_dict({"name": "x", "acoustid": "always"}),
                                  http=types.SimpleNamespace(db=None), ocr="off")
                e.identify(e.albums_in(Path(row["FullPath"]))[0])
        self.assertIn("acoustid", e.notes)
        self.assertNotIn("acoustid", got[0].provider_errors)
        shutil.rmtree(root, ignore_errors=True)

    def test_validate_uses_the_profile(self):
        import profiles as pf
        import validate
        prof = pf.from_dict({"name": "x", "acoustid": "always",
                             "providers": {"web": {"enabled": True, "gnudb": False}}})
        with mock.patch.object(pf, "default", return_value=prof):
            self.assertEqual(validate.clue_settings(), ("always", False))
        with mock.patch.object(pf, "default", return_value=pf.from_dict({"name": "x"})):
            self.assertEqual(validate.clue_settings(), ("unsure", True))
        with mock.patch.object(pf, "default", side_effect=RuntimeError("broken")):
            self.assertEqual(validate.clue_settings(), ("unsure", True))

    def engine_run(self, profile, folder):
        """Engine.identify on the folder with ma.process stubbed: (the args it got, the
        proposal, the AcoustID requests). The web step is stubbed out too."""
        import engine
        import profiles as pf
        got, asked = [], []

        class FpHttp:
            db = None

            def get_json(self, url, host):
                asked.append(host)
                return {"results": [{"id": "ac-1", "score": 0.97, "recordings": [
                    {"id": "rec-9", "title": "Song", "artists": [{"id": "ar-1", "name": "Singer"}]}]}]}
        with mock.patch.object(ma, "process", side_effect=lambda row, http, cfg, dm, args: got.append(args)), \
                mock.patch.object(ma, "fingerprint_ready", return_value=True), \
                mock.patch.object(ma, "fingerprint", return_value={"duration": 3, "fingerprint": "AQAA"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"), \
                mock.patch.object(engine.Engine, "_web", return_value=None):
            e = engine.Engine(pf.from_dict(dict(profile, name="x")), http=FpHttp(), ocr="off")
            prop = e.identify(e.albums_in(folder)[0])
        return (got[0] if got else None), prop, asked

    def test_engine_passes_the_profile_to_the_matcher(self):
        root = Path(tempfile.mkdtemp())
        row = make_album(root, "First Light")
        args, _prop, _asked = self.engine_run(
            {"acoustid": "always", "providers": {"web": {"enabled": True, "gnudb": False}}}, Path(row["FullPath"]))
        self.assertEqual(args.acoustid, "always")
        self.assertIs(args.use_gnudb, False)
        args, _prop, _asked = self.engine_run({}, Path(row["FullPath"]))
        self.assertEqual((args.acoustid, args.use_gnudb), ("unsure", True))
        shutil.rmtree(root, ignore_errors=True)

    def test_engine_lone_track_fingerprinted_unless_off(self):
        root = Path(tempfile.mkdtemp())
        f = root / "Singles" / "song.flac"
        f.parent.mkdir(parents=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=3", str(f), "-y"],
                       check=True)
        _args, prop, asked = self.engine_run({"acoustid": "off"}, f)
        self.assertNotIn("acoustid", asked)
        self.assertNotEqual(prop.provider, "acoustid")
        _args, prop, asked = self.engine_run({"acoustid": "unsure"}, f)
        self.assertIn("acoustid", asked)
        self.assertEqual((prop.status, prop.provider), ("identified", "acoustid"))
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------
# lossy-source check (Audiophile quality)
# --------------------------------------------------------------------------
class LossySource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        pink = "anoisesrc=color=pink:amplitude=0.3:sample_rate=44100"
        run = lambda *a: subprocess.run(["ffmpeg", "-loglevel", "error", *a, "-y"], check=True)  # noqa: E731
        (cls.tmp / "fake").mkdir()
        (cls.tmp / "real").mkdir()
        run("-f", "lavfi", "-i", pink, "-t", "30", str(cls.tmp / "m.wav"))
        run("-i", str(cls.tmp / "m.wav"), "-b:a", "128k", str(cls.tmp / "m.mp3"))
        run("-i", str(cls.tmp / "m.mp3"), str(cls.tmp / "fake" / "01.flac"))
        run("-i", str(cls.tmp / "m.wav"), str(cls.tmp / "real" / "01.flac"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_found_without_numpy(self):
        import enrich
        with mock.patch.object(enrich, "_power_numpy", side_effect=ImportError):
            self.assertIn("transcoded", enrich.quality([self.tmp / "fake" / "01.flac"]))
            self.assertEqual(enrich.quality([self.tmp / "real" / "01.flac"]), "")

    def test_skipped_without_ffmpeg(self):
        import enrich
        with mock.patch.object(enrich, "spectrum_tools", return_value=False):
            self.assertIsNone(enrich.spectrum_cliff(self.tmp / "fake" / "01.flac"))
            self.assertIsNone(enrich.quality([self.tmp / "fake" / "01.flac"]))   # not measured: not "clean"

    def test_unmeasured_album_is_not_cached_as_clean(self):
        # a run that cannot measure a track must not store "" for good: the next run measures again
        import levels
        cache = levels.Cache(Path(tempfile.mkdtemp()) / "a.sqlite")
        fake = [self.tmp / "fake" / "01.flac"]
        import enrich
        with mock.patch.object(enrich, "spectrum_cliff", return_value=None):
            self.assertIsNone(cache.get("lossy-check", fake[0], lambda: enrich.quality(fake)))
        self.assertIn("transcoded", cache.get("lossy-check", fake[0], lambda: enrich.quality(fake)))
        # a "" left under the old kind by a numpy-less run is not read any more
        cache.get("quality", fake[0], lambda: "")
        prop, notes = self.audiophile("fake", cache=cache)
        self.assertIn("transcoded", prop.targets[0]["tagmender_quality"][0])
        cache.close()

    def test_audiophile_notes_a_track_it_could_not_measure(self):
        import enrich
        with mock.patch.object(enrich, "spectrum_cliff", return_value=None):
            prop, notes = self.audiophile("fake")
        self.assertNotIn("tagmender_quality", prop.targets[0])
        self.assertIn("lossy-source check skipped: a track could not be measured", notes)

    def audiophile(self, folder, level="audiophile", cache=None):
        import levels
        import plan as planmod
        files = sorted(str(f) for f in (self.tmp / folder).glob("*.flac"))
        prop = planmod.Proposal(source_dir=str(self.tmp / folder), files=files, targets=[{} for _ in files])
        prof = {"tag_level": level, "audiophile": {"replaygain": False, "lyrics": False, "acoustid": False,
                                                    "rip_info": False, "quality": True}}
        notes = levels.audiophile(prop, prof, None, cache or levels.Cache(None))
        return prop, notes

    def test_quality_note_at_audiophile(self):
        prop, notes = self.audiophile("fake")
        self.assertIn("transcoded", prop.targets[0]["tagmender_quality"][0])
        self.assertTrue(any(n.startswith("quality: transcoded") for n in notes), notes)
        prop, notes = self.audiophile("real")
        self.assertNotIn("tagmender_quality", prop.targets[0])

    def test_no_quality_below_audiophile(self):
        prop, notes = self.audiophile("fake", level="enthusiast")
        self.assertEqual((prop.targets[0], notes), ({}, []))

    def test_audiophile_without_ffmpeg_notes_the_skip(self):
        import enrich
        import shutil as _shutil
        with mock.patch.object(enrich, "spectrum_tools", return_value=False), \
                mock.patch.object(_shutil, "which", return_value=None):
            prop, notes = self.audiophile("fake")
        self.assertNotIn("tagmender_quality", prop.targets[0])
        said = [n for n in notes if n.startswith("lossy-source check skipped")]
        self.assertEqual(len(said), 1, notes)
        self.assertIn("ffmpeg is not installed. Install it with: sudo apt install ffmpeg", said[0])
        self.assertIn("https://ffmpeg.org", said[0])

    def run_audiophile(self, items, which):
        import levels
        import plan as planmod
        import shutil as _shutil
        files = sorted(str(f) for f in (self.tmp / "fake").glob("*.flac"))
        prop = planmod.Proposal(source_dir=str(self.tmp / "fake"), files=files,
                                targets=[{"musicbrainz_recordingid": ["r"]} for _ in files])
        prof = {"tag_level": "audiophile", "audiophile": dict({"replaygain": False, "lyrics": False,
                                                               "acoustid": False, "rip_info": False,
                                                               "quality": False}, **items)}
        with mock.patch.object(_shutil, "which", side_effect=which), \
                mock.patch.object(levels, "loudness", return_value=None):
            return levels.audiophile(prop, prof, None, levels.Cache(None))

    def test_replaygain_note_names_ffmpeg_when_it_is_missing(self):
        notes = self.run_audiophile({"replaygain": True}, lambda name: None)
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("ReplayGain not written", notes[0])
        self.assertIn("ffmpeg is not installed. Install it with: sudo apt install ffmpeg", notes[0])
        # ffmpeg there but a track it could not measure: the old words
        notes = self.run_audiophile({"replaygain": True}, lambda name: f"/usr/bin/{name}")
        self.assertEqual(notes, ["ReplayGain not written: ffmpeg could not measure every track"])

    def test_acoustid_ids_note_says_which_is_missing(self):
        _os.environ.pop("TAGMENDER_ACOUSTID_KEY", None)
        # fpcalc installed, no key: only the key is named, with where to get it
        notes = self.run_audiophile({"acoustid": True}, lambda name: f"/usr/bin/{name}")
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("AcoustID IDs not added.", notes[0])
        self.assertIn("no AcoustID application key", notes[0])
        self.assertIn("acoustid.org/new-application", notes[0])
        self.assertNotIn("fpcalc", notes[0])
        # neither: both named
        notes = self.run_audiophile({"acoustid": True}, lambda name: None)
        self.assertIn("fpcalc (Chromaprint) is not installed", notes[0])
        self.assertIn("no AcoustID application key", notes[0])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
