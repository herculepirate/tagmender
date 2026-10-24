# Tests for engine.py and mb_tags.py with recorded-style API responses (no network):
# MusicBrainz-only, Discogs-only, both on one pressing, providers disagreeing, missing
# credentials, outages, mixed folders, single tracks, rules on identified metadata,
# and the whole path through the planner and executor.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN = Path(_os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or
              (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else
               "~/Documents/Github/picard-discogs-enrichment")).expanduser()
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)
_os.environ["TAGMENDER_DISCOGS_PLUGIN"] = str(PLUGIN)

import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import match_albums as ma  # noqa: E402
import mb_tags  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

LENS = [10000, 20000, 30000]


def mb_release(rid, artist, title, lens=LENS, country="DE", discogs=None, catno="CAT-1", aid="a-1"):
    rels = []
    if discogs:
        rels.append({"type": "discogs", "url": {"resource": f"https://www.discogs.com/release/{discogs}"}})
    return {"id": rid, "title": title, "date": "2005-03-01", "country": country, "status": "Official",
            "barcode": "0123456789012",
            "release-group": {"id": f"rg-{rid}", "primary-type": "Album", "secondary-types": ["Live"],
                              "first-release-date": "1999-01-01"},
            "text-representation": {"script": "Latn", "language": "eng"},
            "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": aid, "name": artist,
                                                                            "sort-name": f"{artist}, The"}}],
            "label-info": [{"label": {"name": "Label MB"}, "catalog-number": catno}],
            "relations": rels,
            "media": [{"format": "CD", "position": 1, "tracks": [
                {"id": f"{rid}-t{i}", "position": i + 1, "title": f"T{i}", "length": ms,
                 "recording": {"id": f"{rid}-r{i}", "title": f"T{i}", "isrcs": [f"DEXX0500000{i}"]}}
                for i, ms in enumerate(lens)]},
                {"format": "DVD-Video", "position": 2, "tracks": [{"id": "v", "title": "Video", "length": 1,
                                                                  "recording": {"id": "vr", "video": True}}]}]}


def dg_release(rid, artist, title, durs=("0:10", "0:20", "0:30"), catno="CAT-1", country="Germany"):
    return {"id": rid, "title": title, "year": 2005, "released": "2005-03-01",
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": artist, "id": 77}],
            "labels": [{"name": "Label DG", "catno": catno}], "identifiers": [], "country": country,
            "genres": ["Electronic"], "styles": ["Ambient"],
            "formats": [{"name": "CD", "descriptions": ["Album"]}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": d}
                          for i, d in enumerate(durs)]}


class FakeHttp:
    def __init__(self, mb=(), dg=(), mb_down=False):
        self.db = None
        self.mb = {r["id"]: r for r in mb}
        self.dg = {r["id"]: r for r in dg}
        self.mb_down = mb_down
        self.calls = []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host == "mb":
            if self.mb_down:
                raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
            if "/ws/2/release/?" in url:
                q = up.unquote(url.split("query=")[1].split("&")[0])
                return {"releases": [{"id": r["id"], "track-count": len(r["media"][0]["tracks"])}
                                     for r in self.mb.values() if r["title"] in q]}
            if "/ws/2/" in url and "/release/" in url:
                return self.mb.get(url.split("/release/")[1].split("?")[0])
            return {"releases": [], "recordings": [], "artists": []}
        if host == "discogs":
            if "/database/search" in url:
                q = up.unquote(url)
                return {"results": [{"id": r["id"], "title": f"{r['artists'][0]['name']} - {r['title']}",
                                     "year": "2005"} for r in self.dg.values() if r["title"] in q]}
            tail = url.rsplit("/", 1)[1]
            return self.dg.get(int(tail)) if tail.isdigit() else None
        return None


class Base(unittest.TestCase):
    def setUp(self):
        if not (PLUGIN / "discogs_mapping.py").exists():
            self.skipTest(f"Discogs Enrichment not found at {PLUGIN}")
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})
        _os.environ["DISCOGS_TOKEN"] = "test-token"
        _os.environ.pop("TAGMENDER_PROFILE", None)
        logsetup.reset()
        self.lib = self.tmp / "lib"

    def tearDown(self):
        logsetup.reset()
        _os.environ.pop("DISCOGS_TOKEN", None)
        _os.environ.pop("TAGMENDER_PROFILE", None)
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def album(self, artist, title, n=3, folder=None, **extra):
        d = self.lib / artist / (folder or f"2005 - {title}")
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            meta = sum((["-metadata", f"{k}={v}"] for k, v in
                        dict({"title": f"T{i}", "album": title, "albumartist": artist, "track": str(i + 1)},
                             **extra).items()), [])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            *meta, str(d / f"{i + 1:02d}{extra.get('_ext', '.flac')}"), "-y"], check=True)
        return d

    def engine(self, http, **prof):
        d = {"name": "default", "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}
        d.update(prof)
        return engine.Engine(pf.from_dict(d), http=http)


class MbTags(unittest.TestCase):
    def test_picard_mapping_and_video_tracks_left_out(self):
        tags = mb_tags.release_tags(mb_release("r1", "Band", "Album"))
        self.assertEqual(len(tags), 3)
        t = tags[1]
        self.assertEqual(t["title"], ["T1"])
        self.assertEqual(t["musicbrainz_recordingid"], ["r1-r1"])
        self.assertEqual(t["musicbrainz_trackid"], ["r1-t1"])
        self.assertEqual(t["musicbrainz_albumartistid"], ["a-1"])
        self.assertEqual(t["albumartistsort"], ["Band, The"])
        self.assertEqual((t["tracknumber"], t["totaltracks"], t["discnumber"], t["totaldiscs"]),
                         (["2"], ["3"], ["1"], ["1"]))
        self.assertEqual(t["releasetype"], ["album", "live"])
        self.assertEqual(t["releasestatus"], ["official"])
        self.assertEqual((t["originaldate"], t["date"]), (["1999-01-01"], ["2005-03-01"]))
        self.assertEqual((t["label"], t["catalognumber"]), (["Label MB"], ["CAT-1"]))
        self.assertEqual(t["isrc"], ["DEXX05000001"])


class Identify(Base):
    def test_musicbrainz_only(self):
        d = self.album("Band A", "Album A")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "musicbrainz"))
        self.assertEqual(p.targets[0]["musicbrainz_albumid"], ["mb-a"])
        self.assertGreaterEqual(p.confidence, 90)

    def test_discogs_only_without_any_musicbrainz_id(self):
        d = self.album("Band B", "Album B")
        eng = self.engine(FakeHttp(dg=[dg_release(901, "Band B", "Album B")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider, p.release_id), ("identified", "discogs", "901"))
        t = p.targets[0]
        self.assertEqual(t["discogs_release_id"], ["901"])
        self.assertEqual(t["catalognumber"], ["CAT-1"])
        self.assertEqual(t["releasecountry"], ["DE"])                 # the plugin's country -> code
        self.assertEqual(t["genre"], ["Electronic"])
        self.assertFalse([k for k in t if k.startswith("musicbrainz_")])

    def test_same_pressing_on_both_mb_base_and_discogs_fills(self):
        d = self.album("Band C", "Album C")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-c", "Band C", "Album C", discogs=902)],
                                   dg=[dg_release(902, "Band C", "Album C")]))
        p = eng.identify(eng.albums_in(d)[0])
        t = p.targets[0]
        self.assertEqual(p.provider, "musicbrainz+discogs")
        self.assertEqual(t["musicbrainz_albumid"], ["mb-c"])
        self.assertEqual(t["discogs_release_id"], ["902"])
        self.assertEqual(t["genre"], ["Electronic"])                  # field_sources: genre from Discogs
        self.assertEqual(t["label"], ["Label DG"])                    # label: Discogs first
        self.assertEqual(p.provenance["genre"], "discogs")
        self.assertEqual(p.provenance["musicbrainz_recordingid"], "musicbrainz")

    def test_providers_disagree_on_the_pressing_nothing_is_mixed(self):
        d = self.album("Band D", "Album D", folder="2005 - Album D [CAT-9]")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-d", "Band D", "Album D", catno="OTHER-1", country="US")],
                                   dg=[dg_release(903, "Band D", "Album D", catno="CAT-9")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))
        self.assertFalse([k for k in p.targets[0] if k.startswith("musicbrainz_")])

    def test_missing_discogs_token_still_identifies_from_musicbrainz(self):
        _os.environ.pop("DISCOGS_TOKEN")
        d = self.album("Band E", "Album E")
        eng = self.engine(FakeHttp(mb=[mb_release("mb-e", "Band E", "Album E")]))
        self.assertIn("missing_credentials", eng.notes["discogs"])
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified")
        self.assertEqual(p.provider_errors, {"discogs": "missing_credentials"})

    def test_musicbrainz_outage_discogs_identifies(self):
        d = self.album("Band F", "Album F")
        eng = self.engine(FakeHttp(dg=[dg_release(904, "Band F", "Album F")], mb_down=True))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.provider), ("identified", "discogs"))
        self.assertEqual(p.provider_errors.get("musicbrainz"), "outage")

    def test_nothing_found_is_unresolved_with_codes(self):
        d = self.album("Band G", "Album G")
        p = self.engine(FakeHttp()).identify(engine.Engine.albums_in(self.engine(FakeHttp()), d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertIn("no_candidate", p.reason_codes)

    def test_outage_without_another_source_is_retryable_not_no_candidate(self):
        d = self.album("Band H", "Album H")
        eng = self.engine(FakeHttp(mb_down=True), providers={"discogs": {"enabled": False},
                                                             "web": {"enabled": False}})
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertIn("musicbrainz_unavailable", p.reason_codes)

    def test_mixed_folder_is_split_by_album(self):
        d = self.album("Mixed", "Album M1", folder="downloads")
        for i in range(2):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={700 + i}:duration=5",
                            "-ar", "8000", "-ac", "1", "-metadata", "album=Other Album", "-metadata",
                            "albumartist=Someone", "-metadata", f"title=O{i}", str(d / f"o{i}.flac"), "-y"], check=True)
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), d)
        self.assertEqual(sorted(len(g.files) for g in groups), [2, 3])
        self.assertTrue(all(len(g.others) == 5 - len(g.files) for g in groups))

    def test_disc_folders_belong_to_their_album(self):
        d = self.lib / "X" / "2005 - Box"
        for disc in ("CD1", "CD2"):
            (d / disc).mkdir(parents=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000",
                            "-metadata", "album=Box", str(d / disc / "01.flac"), "-y"], check=True)
        groups = engine.Engine.albums_in(self.engine(FakeHttp()), self.lib)
        self.assertEqual([(g.folder.name, len(g.files)) for g in groups], [("2005 - Box", 2)])

    def test_single_track_by_fingerprint_is_a_recording_not_an_album(self):
        d = self.lib / "Singles"
        d.mkdir(parents=True)
        f = d / "song.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=3", str(f), "-y"],
                       check=True)
        http = FakeHttp()
        real = http.get_json

        def acoustid(url, host):
            if host == "acoustid":
                return {"results": [{"id": "ac-1", "score": 0.97, "recordings": [
                    {"id": "rec-9", "title": "Song", "artists": [{"id": "ar-1", "name": "Singer"}]}]}]}
            return real(url, host)
        http.get_json = acoustid
        with mock.patch.object(ma, "fingerprint_ready", return_value=True), \
                mock.patch.object(ma, "fingerprint", return_value={"duration": 3, "fingerprint": "AQAA"}), \
                mock.patch.object(ma, "acoustid_key", return_value="k"):
            eng = self.engine(http)
            p = eng.identify(eng.albums_in(f)[0])
        self.assertEqual((p.status, p.provider), ("identified", "acoustid"))
        self.assertEqual(p.targets[0]["musicbrainz_recordingid"], ["rec-9"])
        self.assertNotIn("album", p.targets[0])
        self.assertIn("album edition is not established", p.why[0])


class EndToEnd(Base):
    def test_identify_plan_apply_and_repeat(self):
        d = self.album("Band Z", "Album Z")
        eng = self.engine(FakeHttp(dg=[dg_release(905, "Band Z", "Album Z")]))
        p = eng.plan(d)
        self.assertEqual(p["albums"][0]["status"], "ready", pl.preview(p))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        t = tagio.read(d / "01.flac")
        self.assertEqual((t["tagmender"], t["discogs_release_id"]), (["Resolved"], ["905"]))
        again = self.engine(FakeHttp(dg=[dg_release(905, "Band Z", "Album Z")])).plan(d)
        self.assertEqual(again["albums"][0]["status"], "unchanged")

    def test_rule_matching_only_the_identified_title(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "nv", "name": "no vox", "action": "ignore", "target": "album",
                                               "when": {"all": [{"field": "album", "op": "contains",
                                                                 "value": "[no vox]"}]}}]})
        d = self.album("Band V", "Album V")                               # files do not say [no vox]
        eng = self.engine(FakeHttp(dg=[dg_release(906, "Band V", "Album V [No Vox]")]))
        with mock.patch.object(ma, "discogs_search",
                               lambda http, album, cfg, dm: [ma.discogs_candidate(http.dg[906], dm)]):
            p = eng.plan(d)
        a = p["albums"][0]
        self.assertEqual((a["status"], a["label"]), ("excluded", "Ignored"))
        before = (d / "01.flac").read_bytes()
        executor.Executor(p).run()
        self.assertEqual((d / "01.flac").read_bytes(), before)


class Artwork(Base):
    """CoverUp's real pipeline, offline: only its local-files source is switched on."""
    def setUp(self):
        super().setUp()
        coverup = Path(_os.environ.get("TAGMENDER_COVERUP_DIR", "~/Documents/Github/picard-coverup")).expanduser()
        if not (coverup / "pipeline.py").exists():
            self.skipTest("CoverUp not found (set TAGMENDER_COVERUP_DIR)")

    def _engine(self, http, d):
        import artwork
        eng = self.engine(http, artwork={"enabled": True, "provider": "coverup"})
        cu = artwork.CoverUp(self.tmp / "staging")
        cu.s = cu.s.with_(providers_enabled=["local"])
        eng._art = [cu]
        return eng

    def test_local_scan_is_embedded_into_files_without_art_and_no_file_is_added(self):
        from PIL import Image
        d = self.album("Band K", "Album K")
        Image.new("RGB", (1200, 1200), (200, 30, 30)).save(d / "folder.jpg", quality=90)
        eng = self._engine(te_http := FakeHttp(dg=[dg_release(910, "Band K", "Album K")]), d)
        p = eng.plan(d)
        art = p["albums"][0]["artwork"]
        self.assertIsNotNone(art, pl.preview(p))
        self.assertEqual(art["source"], "coverup:local")
        self.assertEqual(len(art["embed_into"]), 3)
        self.assertIsNone(art["file"])                       # the folder already has its image
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(tagio.read(d / "02.flac")["~pictures"], ["1"])
        del te_http

    def test_no_cover_anywhere_does_not_block_tagging(self):
        d = self.album("Band L", "Album L")
        eng = self._engine(FakeHttp(dg=[dg_release(911, "Band L", "Album L")]), d)
        p = eng.plan(d)
        a = p["albums"][0]
        self.assertIsNone(a["artwork"])
        self.assertTrue(any(w.startswith("no cover") for w in a["identification"]["why"]))
        executor.Executor(p).run()
        self.assertEqual(tagio.read(d / "01.flac")["discogs_release_id"], ["911"])


if __name__ == "__main__":
    unittest.main()
