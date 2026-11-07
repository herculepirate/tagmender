# Find Covers…: the album's own pictures (its folder, disc folders and picture folders, and the
# front in its tracks), the identifiers a plan's album gives the search, and AI covers from the
# presets - each request faked, the made cover marked generated, its lettering checked.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
for _k in ("TAVILY_API_KEY", "TAGMENDER_IMAGE_API_KEY", "TAGMENDER_IMAGE_API_URL", "TAGMENDER_IMAGE_MODEL"):
    _os.environ.pop(_k, None)

import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import covers_ai  # noqa: E402
import covers_find  # noqa: E402


def img(path: Path, side: int, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (side, side), color).save(path)


def png(side=1024, color="blue") -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (side, side), color).save(b, "PNG")
    return b.getvalue()


class Own(unittest.TestCase):
    def test_every_picture_of_the_album_fronts_first_each_once(self):
        a = Path(tempfile.mkdtemp()) / "Album"
        img(a / "folder.jpg", 300)
        img(a / "Artwork" / "Scans" / "Front Cover.jpg", 1400, "green")
        img(a / "Artwork" / "Scans" / "Back.jpg", 2000, "blue")
        img(a / "Artwork" / "Scans" / "copy of back.jpg", 2000, "blue")      # (the same picture twice)
        got = covers_find.own(a)
        self.assertEqual([c.source for c in got], ["Artwork/Scans/Front Cover.jpg", "folder.jpg",
                                                   "Artwork/Scans/Back.jpg"])
        self.assertEqual((got[0].width, got[0].height), (1400, 1400))
        self.assertTrue(got[0].thumb and got[0].full())


class Ids(unittest.TestCase):
    def test_the_release_comes_from_the_plan_over_the_files_own_tags(self):
        album = {"source_dir": "/x", "files": [{"src": "/nonexistent/01.flac",
                                                "changes": {"musicbrainz_albumid": ["mb-1"], "album": ["Calm"],
                                                            "albumartist": ["Band"]}}],
                 "identification": {"provider": "discogs", "release_id": "77"}}
        ids, artist, title = covers_find.ids_of(album)
        self.assertEqual((ids["release_mbid"], ids["discogs_release_id"], artist, title), ("mb-1", "77", "Band", "Calm"))


class AiCovers(unittest.TestCase):
    def setUp(self):
        _os.environ["TAGMENDER_IMAGE_API_KEY"] = "sk-test"
        self.addCleanup(_os.environ.pop, "TAGMENDER_IMAGE_API_KEY", None)
        self.album = {"source_dir": "/x", "files": [{"src": "/nonexistent/01.flac",
                      "changes": {"albumartist": ["Rapture"], "album": ["Paroxysm of Hatred"],
                                  "date": ["2018-05-01"], "genre": ["Death Metal"]}}]}

    def test_the_presets_that_can_be_made(self):
        front = covers_find.Choice("own", "folder.jpg", 300, 300, data=png(300), extra={"front": True})
        rows = covers_ai.plan(self.album, "Rapture", "Paroxysm of Hatred", [front])
        self.assertEqual([r["preset"] for r in rows], ["faithful", "era"])      # (no Tavily key: no references)
        self.assertEqual(rows[0]["facts"]["year"], "2018")
        self.assertEqual([r["preset"] for r in covers_ai.plan(self.album, "Rapture", "X", [])], ["era"])
        _os.environ["TAVILY_API_KEY"] = "tvly-x"
        try:
            self.assertEqual([r["preset"] for r in covers_ai.plan(self.album, "Rapture", "X", [front])],
                             ["faithful", "creative", "art", "era"])
        finally:
            del _os.environ["TAVILY_API_KEY"]

    def _answer(self, sent):
        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            sent.append((req.full_url, req.headers, req.data))
            return R(json.dumps({"data": [{"b64_json": base64.b64encode(png()).decode()}]}).encode())
        return urlopen

    def test_era_is_made_without_references_marked_generated_and_checked(self):
        sent = []
        rows = covers_ai.plan(self.album, "Rapture", "Paroxysm of Hatred", [])
        with mock.patch.object(covers_ai.urllib.request, "urlopen", self._answer(sent)), \
                mock.patch.object(covers_ai, "lettering_ok", return_value=(True, "RAPTURE PAROXYSM OF HATRED")):
            got = covers_ai.make(rows)
        self.assertEqual(len(got), 1)
        self.assertTrue(sent[0][0].endswith("/images/generations"))
        body = json.loads(sent[0][2])
        self.assertEqual(body["model"], "gpt-image-1")
        self.assertIn('"Paroxysm of Hatred" by Rapture', body["prompt"])
        self.assertIn("Death Metal release from 2018", body["prompt"])
        self.assertEqual(sent[0][1]["Authorization"], "Bearer sk-test")
        import coverup_pil_render as R
        self.assertTrue(R.is_generated(got[0].data))                    # (a real cover replaces it later)
        self.assertEqual(got[0].extra["preset"], "era")

    def test_faithful_sends_the_own_cover_as_an_image_and_a_misspelled_one_is_made_again(self):
        sent = []
        front = covers_find.Choice("own", "folder.jpg", 300, 300, data=png(300), extra={"front": True})
        rows = covers_ai.plan(self.album, "Rapture", "Paroxysm of Hatred", [front])[:1]
        with mock.patch.object(covers_ai.urllib.request, "urlopen", self._answer(sent)), \
                mock.patch.object(covers_ai, "lettering_ok", return_value=(False, "RAPTUER")):
            got = covers_ai.make(rows)
        self.assertEqual(len(sent), 2)                                   # (once more: its lettering was wrong)
        self.assertTrue(sent[0][0].endswith("/images/edits"))
        self.assertIn(b'name="image[]"', sent[0][2])
        self.assertIn("lettering may be wrong", got[0].note)

    def test_nothing_is_sent_without_a_key(self):
        del _os.environ["TAGMENDER_IMAGE_API_KEY"]
        self.assertTrue(covers_ai.not_ready())
        with self.assertRaises(covers_ai.CoverAIError):
            covers_ai.generate("x", [])



class ChooserSheet(unittest.TestCase):
    def test_own_pictures_shown_and_the_pick_goes_to_the_inspector(self):
        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import time
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        from gui.cover_chooser import CoverChooser
        a = Path(tempfile.mkdtemp()) / "Album"
        img(a / "folder.jpg", 300)
        img(a / "Scans" / "Front.jpg", 1400, "green")
        win = QWidget()

        class Insp:
            album = {"source_dir": str(a), "files": [], "status": "ready"}
            fetched = None

            def window(self):
                return win

            def _fetch(self, what, get, source):
                self.fetched = (what, get(), source)
        insp = Insp()
        dlg = CoverChooser(insp, insp.album)
        end = time.time() + 10
        while time.time() < end and dlg.grid.count() < 2:
            app.processEvents()
            time.sleep(0.02)
        self.assertEqual(dlg.grid.count(), 2)
        self.assertEqual(dlg.chosen().source, "Scans/Front.jpg")           # (the big front first)
        self.assertTrue(dlg.ai_missing)                                   # (no image service key: it says so)
        dlg.use()
        what, data, source = insp.fetched
        self.assertEqual((what, source), ("Scans/Front.jpg", "chosen:own:Scans/Front.jpg"))
        self.assertEqual(data, (a / "Scans" / "Front.jpg").read_bytes())
        # (the reading thread's quit is queued after its result: let it end before the window
        # goes, as the app's own window waits for its threads on closing)
        from gui.work import _alive
        for th, _w in getattr(dlg, "_threads", []):
            end = time.time() + 10
            while _alive(th) and time.time() < end:
                app.processEvents()
                th.wait(20)
        dlg.close()
        dlg.deleteLater()
        win.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
