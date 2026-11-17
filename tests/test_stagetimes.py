# PRF-1: where an album's look-up spends its time - stagetimes' accounting, the hooks in the
# provider requests (and their back-off), the tag reads, and the engine's one log line per album.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import sqlite3
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stagetimes as st  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


class Clock:
    """time.monotonic, moved on by hand."""
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Accounting(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        p = mock.patch.object(st.time, "monotonic", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def test_a_stage_inside_another_is_charged_to_itself_only(self):
        with st.album() as secs:
            self.clock.now += 1                    # other
            with st.stage("musicbrainz"):
                self.clock.now += 2
                with st.stage("musicbrainz back-off"):
                    self.clock.now += 5
                self.clock.now += 1
            with st.stage("tags"):
                self.clock.now += 0.5
                with st.stage("tags"):             # (the same stage nested: one)
                    self.clock.now += 0.5
        self.assertEqual(secs, {"musicbrainz": 3.0, "musicbrainz back-off": 5.0, "tags": 1.0})

    def test_outside_an_album_nothing_is_kept(self):
        with st.stage("discogs"):
            self.clock.now += 3
        with st.album() as secs:
            pass
        self.assertEqual(secs, {})

    def test_the_log_line_reads_back(self):
        line = st.line("/m/Band: Live/2005 - A, B", 12.34, {"musicbrainz": 5.1, "discogs back-off": 3.0,
                                                             "tags": 0.001})
        self.assertEqual(line, "timing /m/Band: Live/2005 - A, B: 12.34 s: musicbrainz 5.10, "
                               "discogs back-off 3.00, other 4.24")
        folder, total, secs = st.parse("2026-11-16 INFO  job=x engine provider=-  " + line)
        self.assertEqual(folder, "/m/Band: Live/2005 - A, B")
        self.assertEqual(total, 12.34)
        self.assertEqual(secs, {"musicbrainz": 5.1, "discogs back-off": 3.0, "other": 4.24})
        self.assertIsNone(st.parse("an ordinary line"))


class Hooks(unittest.TestCase):
    def test_a_503_retry_wait_is_charged_to_the_providers_back_off(self):
        http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
        tries = []

        def urlopen(req, timeout=None):
            tries.append(req.full_url)
            if len(tries) == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "2"}, None)
            return io.BytesIO(b'{"ok": 1}')
        waited = []
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), \
                mock.patch.object(ma.Http, "_pause", lambda self, s: waited.append(s)), \
                mock.patch.object(ma, "_tell", lambda text: None), st.album() as secs:
            self.assertEqual(http.get_json("https://musicbrainz.org/ws/2/release/x?fmt=json", "mb"), {"ok": 1})
        self.assertEqual(len(tries), 2)
        self.assertEqual(len(waited), 1)
        self.assertIn("musicbrainz", secs)
        self.assertIn("musicbrainz back-off", secs)
        # a cached answer is no request: nothing charged
        with st.album() as again:
            http.get_json("https://musicbrainz.org/ws/2/release/x?fmt=json", "mb")
        self.assertEqual(again, {})

    def test_a_files_tag_read_is_charged_to_tags(self):
        f = Path(_tempfile.mkdtemp()) / "01.flac"
        f.write_bytes(b"not really audio")
        with st.album() as secs:
            meta = ex.read_meta(f)
        self.assertEqual(meta["filename"], ["01.flac"])
        self.assertIn("tags", secs)
        self.assertEqual(st.provider_of("deezer"), "web")


class EngineLine(unittest.TestCase):
    def test_each_album_looked_up_writes_its_timing_line(self):
        import test_engine as te
        t = te.Base("setUp")
        t.setUp()
        self.addCleanup(t.tearDown)
        d = t.album("Band T", "Album T")
        import time

        class Timed(te.FakeHttp):                  # (as Http._fetch charges a live request)
            def get_json(self, url, host):
                with st.stage(st.provider_of(host)):
                    time.sleep(0.06)
                    return super().get_json(url, host)
        e = t.engine(Timed(dg=[te.dg_release(931, "Band T", "Album T")]))
        lines = []
        e.log = mock.Mock(info=lambda fmt, *a: lines.append(fmt % a), warning=mock.Mock(),
                          error=mock.Mock(), exception=mock.Mock())
        props = e.proposals(d)
        self.assertEqual(len(props), 1)
        timed = [st.parse(x) for x in lines if x.startswith("timing ") and not x.startswith("timing scan ")]
        self.assertEqual(len(timed), 1, lines)
        folder, total, secs = timed[0]
        self.assertEqual(folder, str(d))
        self.assertIn("discogs", secs, lines)
        self.assertGreaterEqual(total, 0.0)


if __name__ == "__main__":
    unittest.main()
