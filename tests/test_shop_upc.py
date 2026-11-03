"""Offline tests for the shops' barcode lookups (Deezer /album/upc:, iTunes lookup?upc=):
what a shop sells under the album's barcode is a clue and new titles to search on
MusicBrainz and Discogs, scored like any other candidate, never barcode proof.

Run:  python3 tests/test_shop_upc.py [plugin-dir]
Needs mutagen and ffmpeg (ffmpeg only to build the tiny synthetic albums).
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
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


def _upc(body):
    """`body` (11 digits) with the check digit that makes it a valid UPC-A."""
    return next(body + str(d) for d in range(10) if mc.valid_barcode(body + str(d)))


UPC = _upc("07567826432")              # the files' own barcode (12 digits)
OTHER_UPC = _upc("08811234567")        # another pressing's barcode
LENS = [10000, 20000, 30000]           # the generated tracks last 10, 20 and 30 s


def mb_release(rid, title, barcode=""):
    return {"id": rid, "title": title, "date": "2010", "country": "CA", "barcode": barcode,
            "artist-credit": [{"name": "Artist Alpha", "joinphrase": ""}], "label-info": [],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms} for i, ms in enumerate(LENS)]}]}


def dg_release(rid, title):
    return {"id": rid, "title": title, "year": 2010, "uri": f"https://www.discogs.com/release/{rid}",
            "artists": [{"name": "Artist Alpha"}], "labels": [{"name": "Lbl", "catno": "LB-1"}],
            "identifiers": [], "country": "Canada",
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": d}
                          for i, d in enumerate(["0:10", "0:20", "0:30"])]}


def deezer_album(title="First Light", n=3):
    return {"id": 99, "title": title, "upc": UPC, "artist": {"name": "Artist Alpha"},
            "tracks": {"data": [{"title": f"T{i}", "duration": 10 * (i + 1)} for i in range(n)]}}


def itunes_album(title="First Light"):
    songs = [{"wrapperType": "track", "kind": "song", "trackName": f"T{i}", "trackNumber": i + 1,
              "discNumber": 1, "trackTimeMillis": 10000 * (i + 1)} for i in (2, 0, 1)]   # any order
    return {"resultCount": 4, "results": [{"wrapperType": "collection", "collectionName": title,
                                           "artistName": "Artist Alpha", "trackCount": 3}] + songs}


class ShopHttp:
    """Canned answers. MusicBrainz and Discogs know the album only as "First Light" and
    find nothing by its barcode; the shops know the barcode (Deezer as the 12-digit UPC)."""

    def __init__(self, mb_barcode="", deezer=None, itunes=None, offline=(), with_discogs=True):
        self.calls, self.unexpected = [], []
        self.mb = {"mb-first": mb_release("mb-first", "First Light", mb_barcode),
                   "mb-dawn": mb_release("mb-dawn", "Dawn Light")}
        self.mb_titles = {"First Light": "mb-first"}          # what a title search finds
        self.dg = {701: dg_release(701, "First Light")} if with_discogs else {}
        self.deezer = deezer_album() if deezer is None else deezer
        self.itunes = itunes_album() if itunes is None else itunes
        self.offline = set(offline)

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host in self.offline:
            raise ma.ProviderUnavailable(host, "outage", "URLError")
        if host == "deezer-upc":
            if getattr(self, "deezer_always", False):
                return self.deezer
            return self.deezer if url.endswith(f"upc:{UPC}") and self.deezer else \
                {"error": {"type": "DataException", "message": "no data", "code": 800}}
        if host == "itunes-upc":
            return self.itunes if f"upc={UPC}" in url and self.itunes else {"resultCount": 0, "results": []}
        if host == "mb":
            if "/ws/2/release/?" in url:
                query = up.unquote(url.split("query=")[1].split("&")[0])
                return {"releases": [{"id": rid, "track-count": 3} for t, rid in self.mb_titles.items()
                                     if f'release:"{t}"' in query]}
            if "/ws/2/release/" in url:
                return self.mb.get(url.split("/release/")[1].split("?")[0])
            return {}
        if host == "discogs":
            if "/database/search" in url:
                if "release_title=First%20Light" in url and self.dg:
                    return {"results": [{"id": 701, "title": "Artist Alpha - First Light", "year": "2010"}]}
                return {"results": []}
            if "/releases/" in url:
                return self.dg.get(int(url.rsplit("/", 1)[1]))
            return {}
        if host == "deezer":                         # the ISRC route later on: nothing there
            return {}
        self.unexpected.append((host, url))       # kept: the shop path turns errors into notes
        raise AssertionError(f"unexpected request {host} {url}")

    def asked(self, host):
        return [u for h, u in self.calls if h == host]


def make_album(root, title, barcode=UPC, folder=None):
    d = Path(root) / "Artist Alpha" / (folder or f"2010 - {title}")
    d.mkdir(parents=True)
    for i in range(3):
        meta = ["-metadata", f"title=T{i}", "-metadata", f"album={title}", "-metadata", "albumartist=Artist Alpha",
                "-metadata", "date=2010", "-metadata", f"track={i + 1}"]
        if barcode:
            meta += ["-metadata", f"barcode={barcode}"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                        *meta, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
    return {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}


class ShopAnswers(unittest.TestCase):
    """What the shops' answers are read as."""

    def test_deezer_album_read(self):
        shop = ma.deezer_upc_album(ShopHttp(), UPC)
        self.assertEqual((shop["shop"], shop["artist"], shop["title"]), ("Deezer", "Artist Alpha", "First Light"))
        self.assertEqual([t["secs"] for t in shop["tracks"]], [10.0, 20.0, 30.0])

    def test_unknown_code_is_none_not_an_album(self):
        self.assertIsNone(ma.deezer_upc_album(ShopHttp(), OTHER_UPC))     # Deezer's {"error": ...}
        self.assertIsNone(ma.itunes_upc_album(ShopHttp(), OTHER_UPC))     # resultCount 0

    def test_itunes_songs_in_disc_and_track_order(self):
        shop = ma.itunes_upc_album(ShopHttp(), UPC)
        self.assertEqual([t["title"] for t in shop["tracks"]], ["T0", "T1", "T2"])
        self.assertEqual([t["secs"] for t in shop["tracks"]], [10.0, 20.0, 30.0])

    def test_itunes_kind_dropped_from_the_title(self):
        """iTunes writes "First Light - EP" or "Dawn - Single"; the databases do not."""
        for name, want in [("First Light - EP", "First Light"), ("Dawn - Single", "Dawn"),
                           ("Re-Up - Remixes", "Re-Up - Remixes"), ("EP", "EP")]:
            shop = ma.itunes_upc_album(ShopHttp(itunes=itunes_album(name)), UPC)
            self.assertEqual(shop["title"], want, name)

    def test_both_barcode_forms_asked(self):
        """Deezer keeps this code as its 12-digit UPC: the EAN-13 form is asked first, then it."""
        http = ShopHttp()
        found, failed = ma.shop_upc_albums(http, {UPC})
        self.assertEqual(failed, [])
        self.assertEqual([s["shop"] for s in found], ["Deezer", "iTunes"])
        self.assertEqual([u.rsplit(":", 1)[1] for u in http.asked("deezer-upc")], ["0" + UPC, UPC])

    def test_invalid_barcode_not_asked(self):
        http = ShopHttp()
        self.assertEqual(ma.shop_upc_albums(http, {"123456789013"}), ([], []))
        self.assertEqual(http.calls, [])

    def test_one_shop_offline_the_other_still_asked(self):
        http = ShopHttp(offline={"deezer-upc"})
        found, failed = ma.shop_upc_albums(http, {UPC})
        self.assertEqual([s["shop"] for s in found], ["iTunes"])
        self.assertEqual(len(http.asked("deezer-upc")), 1)                 # not asked again for the other form
        self.assertTrue(failed and "Deezer barcode lookup failed" in failed[0], failed)

    def test_at_most_two_barcodes_asked(self):
        """An album with three valid barcodes: only the first two are asked of the shops."""
        codes = {UPC, OTHER_UPC, _upc("06025123456")}
        http = ShopHttp()
        ma.shop_upc_albums(http, codes)
        asked = {u.rsplit(":", 1)[1].lstrip("0") for u in http.asked("deezer-upc")}
        asked |= {u.split("upc=")[1].split("&")[0].lstrip("0") for u in http.asked("itunes-upc")}
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked, {c.lstrip("0") for c in sorted(codes)[:2]})

    def test_deezer_error_other_than_no_data_is_a_failure(self):
        """Deezer's "Quota limit exceeded" (code 4) comes back as HTTP 200: it is not "no album"."""
        http = ShopHttp(deezer={"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}})
        http.deezer_always = True
        with self.assertRaises(ma.ProviderUnavailable) as cm:
            ma.deezer_upc_album(http, UPC)
        self.assertEqual((cm.exception.host, cm.exception.kind), ("deezer-upc", "rate_limited"))
        found, failed = ma.shop_upc_albums(http, {UPC})
        self.assertEqual([s["shop"] for s in found], ["iTunes"])
        self.assertTrue(failed and "Deezer barcode lookup failed" in failed[0], failed)

    def test_note_says_whether_the_shop_fits(self):
        album = {"tracks": [{"secs": 10.0}, {"secs": 20.0}, {"secs": 30.0}]}
        fits = ma.shop_upc_albums(ShopHttp(), {UPC})[0][0]
        self.assertEqual(ma.shop_note(album, fits, CFG),
                         f"Deezer: UPC {UPC} = Artist Alpha - First Light (lengths agree)")
        short = ma.deezer_upc_album(ShopHttp(deezer=deezer_album(n=2)), UPC)
        self.assertIn("2 tracks, does not fit your files", ma.shop_note(album, short, CFG))

    def test_same_count_other_lengths_does_not_fit(self):
        """Another edition: the files' track count, but its lengths disagree."""
        album = {"tracks": [{"secs": 10.0}, {"secs": 20.0}, {"secs": 30.0}]}
        other = ma.deezer_upc_album(ShopHttp(deezer=dict(deezer_album(), tracks={"data": [
            {"title": "T0", "duration": 200}, {"title": "T1", "duration": 300}, {"title": "T2", "duration": 400}]})), UPC)
        self.assertFalse(ma.shop_fits(album, other, CFG))
        self.assertTrue(ma.shop_note(album, other, CFG).endswith("(3 tracks, does not fit your files)"))

    def test_no_lengths_known_does_not_fit(self):
        """A shop giving no lengths at all: the track count alone is not enough."""
        album = {"tracks": [{"secs": 10.0}, {"secs": 20.0}, {"secs": 30.0}]}
        blank = ma.deezer_upc_album(ShopHttp(deezer=dict(deezer_album(), tracks={"data": [
            {"title": f"T{i}", "duration": 0} for i in range(3)]})), UPC)
        self.assertFalse(ma.shop_fits(album, blank, CFG))


class CachedAndOffline(unittest.TestCase):
    """The real Http: answers are cached, and an unreachable shop is given up on at once."""

    def http(self):
        return ma.Http(sqlite3.connect(":memory:"), dict(CFG, mb_delay=0, discogs_delay=0), "")

    def test_answer_cached(self):
        import json
        http = self.http()
        body = json.dumps(deezer_album()).encode()
        with mock.patch.object(ma.urllib.request, "urlopen",
                               side_effect=lambda *a, **k: mock.MagicMock(__enter__=lambda s: io.BytesIO(body),
                                                                          __exit__=lambda *x: False)) as op:
            self.assertEqual(ma.deezer_upc_album(http, UPC)["title"], "First Light")
            self.assertEqual(ma.deezer_upc_album(http, UPC)["title"], "First Light")
        self.assertEqual(op.call_count, 1)

    @staticmethod
    def answers(body):
        """urlopen giving `body` (JSON) to every request."""
        import json
        data = json.dumps(body).encode()
        return mock.patch.object(ma.urllib.request, "urlopen",
                                 side_effect=lambda *a, **k: mock.MagicMock(__enter__=lambda s: io.BytesIO(data),
                                                                            __exit__=lambda *x: False))

    def test_deezer_quota_answer_not_cached(self):
        """Over Deezer's limit: noted as failed, and not kept as "no album" for that barcode."""
        http = self.http()
        quota = {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}
        with self.answers(quota) as op, mock.patch.object(ma.time, "sleep"):
            found, failed = ma.shop_upc_albums(http, {UPC})
            self.assertTrue(any("Deezer barcode lookup failed" in f for f in failed), failed)
            asked = op.call_count
            with self.assertRaises(ma.ProviderUnavailable):
                ma.deezer_upc_album(http, "0" + UPC)
        self.assertEqual(op.call_count, asked + 1)                          # asked again, not read back
        self.assertIsNone(http.db.execute("SELECT 1 FROM http_cache WHERE url LIKE '%deezer.com/album/upc:%'")
                          .fetchone())

    def test_deezer_no_data_cached(self):
        """Deezer's "no data" (800) is a real answer: kept, not asked again."""
        http = self.http()
        with self.answers({"error": {"type": "DataException", "message": "no data", "code": 800}}) as op:
            self.assertIsNone(ma.deezer_upc_album(http, UPC))
            self.assertIsNone(ma.deezer_upc_album(http, UPC))
        self.assertEqual(op.call_count, 1)

    def test_itunes_requests_spaced(self):
        """iTunes allows about 20 requests a minute: its lookups are 3 s apart."""
        http = self.http()
        with self.answers({"resultCount": 0, "results": []}), mock.patch.object(ma.time, "sleep") as slept:
            ma.itunes_upc_album(http, UPC)
            ma.itunes_upc_album(http, "0" + UPC)
        waits = [c.args[0] for c in slept.call_args_list if c.args]
        self.assertTrue(waits and 2.5 < max(waits) <= 3.0, waits)

    def test_itunes_search_keeps_its_retries_and_shares_the_limit(self):
        """The barcode lookup has a host name of its own: web_tag's iTunes searches keep their
        retries and are not paused by a barcode outage, but both are spaced as one service."""
        self.assertNotIn("itunes", ma.Http.OPTIONAL_HOSTS)
        self.assertIn("itunes-upc", ma.Http.OPTIONAL_HOSTS)
        http = self.http()
        with self.answers({"resultCount": 0, "results": []}), mock.patch.object(ma.time, "sleep") as slept:
            http.get_json("https://itunes.apple.com/search?term=x&entity=album", "itunes")
            ma.itunes_upc_album(http, UPC)
        waits = [c.args[0] for c in slept.call_args_list if c.args]
        self.assertTrue(waits and 2.5 < max(waits) <= 3.0, waits)

    def test_offline_noted_without_retries(self):
        http = self.http()
        with mock.patch.object(ma.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")) as op, \
                mock.patch.object(ma.time, "sleep") as slept:
            found, failed = ma.shop_upc_albums(http, {UPC})
        self.assertEqual(found, [])
        self.assertEqual(op.call_count, 2)                                 # once per shop, no retries
        self.assertFalse([c for c in slept.call_args_list if c.args and c.args[0] >= 1])
        self.assertEqual(len(failed), 2)


class Pipeline(unittest.TestCase):
    """ma.process with the shops: the shop's title is searched, scored like any candidate."""

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

    def tearDown(self):
        for http in self.https:                 # a stray request must not hide in a "failed" note
            self.assertEqual(http.unexpected, [])

    def run_album(self, http, title="Erstes Licht", barcode=UPC, dm=True, notes_text="", **args):
        self.https.append(http)
        row = make_album(self.root, title, barcode)
        if notes_text:
            (Path(row["FullPath"]) / "notes.txt").write_text(notes_text)
        return ma.process(row, http, CFG, self.dm if dm else None, types.SimpleNamespace(ocr=False, **args))

    def no_failure_notes(self, o):
        self.assertFalse([n for n in o.get("shop_notes") or [] if "failed" in n], o.get("shop_notes"))

    def test_shop_title_searched_and_noted(self):
        """The files carry another title (a translation): only the shop's title finds the album."""
        http = ShopHttp()
        o = self.run_album(http)
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        self.assertTrue(any(a.startswith("discogs:701 ") for a in o["alts"]), o["alts"])
        self.assertIn(f"Deezer: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])
        self.assertIn(f"iTunes: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])
        self.no_failure_notes(o)
        # a clue, not a reason and not the match's own evidence: validate reads best["notes"]
        # for proof about your copy, and a shop title ("Made in Japan") must not count as that
        self.assertNotIn(o["shop_notes"][0], o["best"]["notes"])
        self.assertIn(o["shop_notes"][0], ma.to_row(o)["Notes"])
        self.assertNotIn(o["shop_notes"][0], ma.to_row(o)["Evidence"])
        self.assertNotIn(o["shop_notes"][0], o["why"])
        import engine
        self.assertEqual(engine.evidence_of(o, o["best"]), o["best"]["notes"] + o["shop_notes"])
        # the same title from both shops is searched once
        self.assertEqual(sum('release:"First Light" AND artist:"Artist Alpha"' in up.unquote(u)
                             for u in http.asked("mb")), 1)
        self.assertNotEqual(o["status"], "auto", o["why"])                # the title still disagrees

    def test_shop_agreeing_is_not_barcode_proof(self):
        """The release found lacks the barcode: the shop's UPC does not stand in for it, so it
        scores exactly as it would have if the title search had found it."""
        http = ShopHttp()
        o = self.run_album(http, title="First Light!", dm=False)
        rel = ma.mb_candidate(http.mb["mb-first"])
        album = ma.read_album(Path(self.root) / "Artist Alpha" / "2010 - First Light!", "Artist Alpha",
                              "2010 - First Light!")
        alone = mc.score_candidate(album, rel, CFG)
        self.assertFalse(alone["code"].startswith("barcode"))
        self.assertEqual(o["best"]["confidence"], alone["confidence"])
        self.assertEqual(o["status"], mc.classify([alone], CFG)["status"])

    def test_wrong_pressing_never_auto(self):
        """The shop's title finds the album, but MusicBrainz lists another barcode for it."""
        o = self.run_album(ShopHttp(mb_barcode=OTHER_UPC), title="First Light!", dm=False)
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        self.assertNotEqual(o["status"], "auto")

    def test_shop_album_that_does_not_fit_is_not_searched(self):
        http = ShopHttp(deezer=deezer_album(title="Something Else", n=5), itunes={})
        o = self.run_album(http)
        self.assertIn("5 tracks, does not fit your files", o["shop_notes"][0])
        self.assertFalse(any("Something Else" in up.unquote(u) for u in http.asked("mb") + http.asked("discogs")))

    def test_shop_album_with_other_lengths_is_not_searched(self):
        """The files' track count but other lengths (another edition): noted, not searched."""
        http = ShopHttp(deezer=dict(deezer_album(title="Something Else"), tracks={"data": [
            {"title": "T0", "duration": 200}, {"title": "T1", "duration": 300}, {"title": "T2", "duration": 400}]}),
            itunes={})
        o = self.run_album(http)
        self.assertTrue(o["shop_notes"][0].endswith("(3 tracks, does not fit your files)"), o["shop_notes"])
        self.assertFalse(any("Something Else" in up.unquote(u) for u in http.asked("mb") + http.asked("discogs")))

    def test_shops_offline_noted_and_carried_on(self):
        o = self.run_album(ShopHttp(offline={"deezer-upc", "itunes-upc"}))
        self.assertNotEqual(o["status"], "error")                         # not retried over a shop
        self.assertTrue(any("Deezer barcode lookup failed" in n for n in o["shop_notes"]), o)
        self.assertTrue(any("iTunes barcode lookup failed" in n for n in o["shop_notes"]), o)

    def test_shop_artist_searched(self):
        """The shop credits another artist: the title search uses the shop's artist."""
        shop = dict(deezer_album(), artist={"name": "Artist Beta"})
        http = ShopHttp(deezer=shop, itunes={})
        o = self.run_album(http)
        self.assertIn(f"Deezer: UPC {UPC} = Artist Beta - First Light (lengths agree)", o["shop_notes"])
        self.assertTrue(any('release:"First Light" AND artist:"Artist Beta"' in up.unquote(u)
                            for u in http.asked("mb")), http.asked("mb"))
        self.no_failure_notes(o)

    def test_each_shop_title_searched(self):
        """Deezer and iTunes sell the barcode under two titles: both are searched, once each,
        and what each finds is kept."""
        http = ShopHttp(itunes=itunes_album("Dawn Light"))
        http.mb_titles["Dawn Light"] = "mb-dawn"
        o = self.run_album(http, dm=False)
        for title in ("First Light", "Dawn Light"):
            self.assertEqual(sum(f'release:"{title}" AND artist:"Artist Alpha"' in up.unquote(u)
                                 for u in http.asked("mb")), 1, (title, http.asked("mb")))
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        self.assertTrue(any(a.startswith("mb:mb-dawn ") for a in o["alts"]), o["alts"])
        self.no_failure_notes(o)

    def test_barcode_from_a_document_asked(self):
        """No barcode tag: the one a notes file in the folder gives is asked of the shops."""
        http = ShopHttp()
        o = self.run_album(http, barcode="", notes_text=f"Artist Alpha - Erstes Licht\nBarcode: {UPC}\n")
        self.assertTrue(http.asked("deezer-upc"), http.calls)
        self.assertIn(f"Deezer: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])

    def test_search_of_the_shop_title_failing_is_noted(self):
        """MusicBrainz failing on the shop's title: noted, and the album carries on."""
        class Failing(ShopHttp):
            def get_json(self, url, host):
                if host == "mb" and 'release:"First Light"' in up.unquote(url):
                    self.calls.append((host, url))
                    raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
                return super().get_json(url, host)
        o = self.run_album(Failing(itunes={}))
        self.assertNotEqual(o["status"], "error")
        # a provider failure, as for the other searches: in the reasons, not among the clues
        self.assertTrue(any(n.startswith("MusicBrainz search of the Deezer title failed") for n in o["why"]),
                        o["why"])
        self.no_failure_notes(o)
        self.assertTrue(any(a.startswith("discogs:701 ") for a in o["alts"]), o["alts"])   # Discogs still asked

    def test_musicbrainz_down_on_the_shop_title_without_a_match_is_retried(self):
        """The command line passes no provider_errors: a "no match" that the failed search
        of the shop's title may hide is an error (retried), not a final no-match."""
        o = self.run_album(self.MbDownOnShopTitle(itunes={}, with_discogs=False))
        self.assertEqual(o["status"], "error", o["why"])
        self.assertTrue(any(n.startswith("MusicBrainz search of the Deezer title failed") for n in o["why"]),
                        o["why"])

    def test_discogs_down_on_the_shop_title_is_retried(self):
        class DgDown(ShopHttp):
            def get_json(self, url, host):
                if host == "discogs" and "/database/search" in url and "release_title=First%20Light" in url:
                    self.calls.append((host, url))
                    raise ma.ProviderUnavailable("discogs", "outage", "HTTP 502")
                return super().get_json(url, host)
        errors = {}
        o = self.run_album(DgDown(itunes={}), provider_errors=errors)
        self.assertEqual(errors.get("discogs"), "outage")
        self.assertTrue(any(n.startswith("Discogs search of the Deezer title failed") for n in o["why"]), o["why"])
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])   # MusicBrainz kept

    def test_unexpected_shop_error_noted_and_result_kept(self):
        """A bug in reading a shop's answer stops the shop step only, not the album."""
        http = ShopHttp(mb_barcode=OTHER_UPC)
        with mock.patch.object(ma, "shop_upc_search", side_effect=RuntimeError("bad shop answer")):
            o = self.run_album(http, title="First Light", dm=False)
        self.assertNotEqual(o["status"], "error", o["why"])
        self.assertEqual(o["shop_notes"], ["shop barcode lookup failed (RuntimeError: bad shop answer); "
                                           "carried on without it"])
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])

    def test_shop_title_equal_to_the_album_title_not_searched_again(self):
        http = ShopHttp(mb_barcode=OTHER_UPC)                            # another pressing: not sure
        o = self.run_album(http, title="First Light", dm=False)
        self.assertNotEqual(o["status"], "auto", o["why"])
        self.assertEqual(sum('release:"First Light" AND artist:"Artist Alpha"' in up.unquote(u)
                             for u in http.asked("mb")), 1, http.asked("mb"))
        self.assertIn(f"Deezer: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])
        self.assertIn(f"iTunes: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])

    def test_itunes_ep_title_searched_without_its_kind(self):
        http = ShopHttp(deezer={}, itunes=itunes_album("First Light - EP"))
        o = self.run_album(http, dm=False)
        self.assertIn(f"iTunes: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        self.assertFalse(any("- EP" in up.unquote(u) for u in http.asked("mb")), http.asked("mb"))

    def test_barcode_from_the_investigation_asked(self):
        """A barcode only the deep reading of the scans found is asked of the shops too,
        and each barcode once only."""
        http = ShopHttp()

        def investigate(http_, base, album, cfg, dm, mb_res, dg_res, final, ocr_txt, out, notes, *rest):
            album["barcodes"] = {UPC}
            return mb_res, dg_res, final, ocr_txt
        with mock.patch.object(ma, "_needs_investigation", return_value=True), \
                mock.patch.object(ma, "investigate", side_effect=investigate):
            self.https.append(http)
            row = make_album(self.root, "Erstes Licht", barcode="")
            o = ma.process(row, http, CFG, self.dm, types.SimpleNamespace(ocr=True, max_images=8))
        self.assertIn(f"Deezer: UPC {UPC} = Artist Alpha - First Light (lengths agree)", o["shop_notes"])
        self.assertTrue(any(a.startswith("mb:mb-first ") for a in o["alts"]), o["alts"])
        asked = http.asked("itunes-upc")
        self.assertTrue(asked)
        self.assertEqual(len(asked), len(set(asked)), asked)

    def test_shops_asked_once_when_the_investigation_finds_no_new_barcode(self):
        http = ShopHttp()
        with mock.patch.object(ma, "_needs_investigation", return_value=True), \
                mock.patch.object(ma, "investigate", side_effect=lambda *a: (a[5], a[6], a[7], a[8])):
            self.https.append(http)
            row = make_album(self.root, "Erstes Licht")
            o = ma.process(row, http, CFG, self.dm, types.SimpleNamespace(ocr=True, max_images=8))
        asked = http.asked("itunes-upc") + http.asked("deezer-upc")
        self.assertEqual(len(asked), len(set(asked)), asked)
        self.assertEqual(len([n for n in o["shop_notes"] if n.startswith("Deezer:")]), 1, o["shop_notes"])

    class MbDownOnShopTitle(ShopHttp):
        def get_json(self, url, host):
            if host == "mb" and 'release:"First Light"' in up.unquote(url):
                self.calls.append((host, url))
                raise ma.ProviderUnavailable("mb", "rate_limited", "HTTP 503")
            return super().get_json(url, host)

    def test_musicbrainz_down_on_the_shop_title_is_retried(self):
        """MusicBrainz unavailable for the shop's title is a provider error, as for the other
        searches: the album is retried on the next run, not saved as a final no-match."""
        errors = {}
        o = self.run_album(self.MbDownOnShopTitle(itunes={}), provider_errors=errors)
        self.assertNotEqual(o["status"], "error")
        self.assertEqual(errors.get("musicbrainz"), "rate_limited")

    def test_musicbrainz_alone_down_on_the_shop_title_raises(self):
        """With Discogs off, nothing else can answer: raised, as the first search does."""
        with self.assertRaises(ma.ProviderUnavailable):
            self.run_album(self.MbDownOnShopTitle(itunes={}), dm=False, provider_errors={})

    def test_switched_off(self):
        http = ShopHttp()
        self.run_album(http, use_shops=False)
        self.assertEqual(http.asked("deezer-upc") + http.asked("itunes-upc"), [])

    def test_not_asked_without_a_barcode(self):
        http = ShopHttp()
        self.run_album(http, barcode="")
        self.assertEqual(http.asked("deezer-upc") + http.asked("itunes-upc"), [])

    def test_not_asked_when_already_sure(self):
        http = ShopHttp(mb_barcode=UPC)
        o = self.run_album(http, title="First Light", dm=False)
        self.assertEqual(o["status"], "auto", o["why"])
        self.assertEqual(http.asked("deezer-upc") + http.asked("itunes-upc"), [])


class CommandLine(unittest.TestCase):
    def test_no_shops_flag(self):
        self.assertTrue(ma.arg_parser().parse_args(["audit.csv"]).use_shops)
        self.assertFalse(ma.arg_parser().parse_args(["audit.csv", "--no-shops"]).use_shops)


class EngineFlag(unittest.TestCase):
    def test_profile_flag(self):
        import profiles as pf
        self.assertTrue(pf.from_dict({"name": "x"}).provider_on("web", "shops"))
        self.assertFalse(pf.from_dict({"name": "x", "providers": {"web": {"enabled": True, "shops": False}}})
                         .provider_on("web", "shops"))
        self.assertFalse(pf.from_dict({"name": "x", "providers": {"web": {"enabled": False}}})
                         .provider_on("web", "shops"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
