# Tests for the web step's track-count prefilter (web_tag.album_candidates n_tracks, engine._web)
# and the shared Deezer search, with a fake web (no network).
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

# TagMender settings and state of the tests never touch the real ones
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/Picard.ini"
HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import web_tag as wt  # noqa: E402
import engine  # noqa: E402


class FakeHttp:
    def __init__(self, nb, with_count=True):
        self.urls, self.nb, self.with_count = [], nb, with_count

    def get_json(self, url, host):
        self.urls.append(url)
        if "/search/album" in url:
            return {"data": [dict({"id": i}, **({"nb_tracks": self.nb} if self.with_count else {}))
                             for i in range(1, 4)]}
        if url.endswith("/tracks?limit=300"):
            return {"data": [{"id": i, "title": f"t{i}", "duration": 100, "track_position": i + 1} for i in range(self.nb)]}
        if "/album/" in url:
            aid = url.rsplit("/", 1)[1]
            return {"id": int(aid), "title": "A", "artist": {"name": "B"}, "release_date": "2020-01-01"}
        if "itunes.apple.com/search" in url:
            return {"results": [dict({"collectionId": i, "collectionName": "A", "artistName": "B"},
                                     **({"trackCount": self.nb} if self.with_count else {})) for i in range(1, 4)]}
        if "itunes.apple.com/lookup" in url:
            return {"results": []}
        return None

    def details(self):
        return [u for u in self.urls if "/search" not in u]


ALBUM = {"artist": "B", "title": "A", "tracks": [{"secs": 100.0}] * 3, "tag_urls": []}
SOURCES = ["Deezer", "iTunes"]


class CountPrefilter(unittest.TestCase):
    def test_web_step_makes_no_detail_requests_for_hits_the_files_cannot_be(self):
        http = FakeHttp(12)
        found = wt.album_candidates(http, ALBUM, None, SOURCES, n_tracks=3)
        self.assertEqual(found, [])
        self.assertEqual(http.details(), [])

    def test_engine_web_passes_the_files_track_count_and_nothing_else_does(self):
        eng = engine.Engine.__new__(engine.Engine)
        eng.http, eng.log, eng.profile = object(), mock.Mock(), {}
        with mock.patch.object(wt, "album_candidates", return_value=[]) as ac, \
                mock.patch.object(engine.Engine, "_web_album", return_value=(None, "")):
            eng._web({}, ALBUM, [], {"source_dir": "x"})
        self.assertEqual(ac.call_args.kwargs.get("n_tracks"), 3)

    def test_no_web_tags_when_a_chosen_source_did_not_answer(self):
        # (the silent source might have held a rival that makes the others refuse)
        eng = engine.Engine.__new__(engine.Engine)
        eng.http, eng.log, eng.profile = object(), mock.Mock(), {}

        def candidates(*a, failed=None, **k):
            failed.append("Deezer")
            return [("iTunes", {"title": "A"})]
        with mock.patch.object(wt, "album_candidates", candidates), \
                mock.patch.object(engine.Engine, "_web_album") as judged:
            self.assertIsNone(eng._web({}, ALBUM, [], {"source_dir": "x"}))
        judged.assert_not_called()

    def test_a_failing_source_is_reported_to_the_caller(self):
        class Down(FakeHttp):
            def get_json(self, url, host):
                if "deezer" in url:
                    raise ma.ProviderUnavailable("deezer", "outage", "down")
                return super().get_json(url, host)
        failed = []
        found = wt.album_candidates(Down(3), ALBUM, None, SOURCES, n_tracks=3, failed=failed)
        self.assertEqual(failed, ["Deezer"])
        self.assertTrue(found)                                     # (the others still listed)

    def test_mixed_folder_keeps_its_12_track_candidates(self):
        album = dict(ALBUM, tracks=[{"secs": 100.0}] * 24)          # two versions of a 12-track album
        http = FakeHttp(12)
        found = wt.album_candidates(http, album, None, SOURCES)     # (_mixed passes no count)
        self.assertEqual(len(found), 3 + 3)
        self.assertTrue(http.details())

    def test_a_hit_without_a_count_is_still_fetched(self):
        http = FakeHttp(12, with_count=False)
        found = wt.album_candidates(http, ALBUM, None, SOURCES, n_tracks=3)
        self.assertEqual(len(found), 3 + 3)

    def test_a_hit_within_three_tracks_is_fetched(self):
        http = FakeHttp(5)
        self.assertEqual(len(wt.album_candidates(http, ALBUM, None, ["Deezer"], n_tracks=3)), 3)

    def test_find_passes_no_count(self):
        with mock.patch.object(wt, "album_candidates") as ac:
            ac.return_value = []
            try:
                wt.find(FakeHttp(12), dict(ALBUM, tracks=ALBUM["tracks"]), {}, "", SOURCES)
            except Exception:
                pass
        for call in ac.call_args_list:
            self.assertNotIn("n_tracks", call.kwargs)


class SharedDeezerSearch(unittest.TestCase):
    def test_the_isrc_step_and_the_web_step_ask_the_same_search_url(self):
        http = FakeHttp(12)
        ma.deezer_isrc_releases(http, dict(ALBUM, tracks=[{"secs": 100.0}] * 12))
        wt.deezer(http, "B", "A")
        searches = [u for u in http.urls if "/search/album" in u]
        self.assertGreaterEqual(len(searches), 2)
        self.assertEqual(len(set(searches)), 1)
        self.assertIn("limit=10", searches[0])

    def test_the_isrc_step_still_reads_only_the_first_three_hits(self):
        http = FakeHttp(12)
        data = {"data": [{"id": i, "nb_tracks": 12 if i >= 3 else 99} for i in range(10)]}
        seen = []

        def get_json(url, host):
            seen.append(url)
            return data if "/search/album" in url else {}
        http.get_json = get_json
        self.assertEqual(ma.deezer_isrc_releases(http, dict(ALBUM, tracks=[{"secs": 1.0}] * 12)), set())
        self.assertEqual(len(seen), 1)                           # hits 4..10 (right count) never fetched


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
