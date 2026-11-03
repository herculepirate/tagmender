# The built-in player (gui/player.py), offscreen: the bar builds and disables its controls when
# there is no audio backend; the album's tracks are gathered on a worker (a plan entry's files in
# order, or a folder's audio files and disc folders in natural order); play/pause, previous/next,
# the end of a track, seeking, speed, volume (kept in gui.json), Space on the album table, and
# letting go of a file before it is written. A stand-in player stands for QMediaPlayer, so nothing
# is heard; one test loads a generated WAV into the real backend, muted, when there is one.
# Patch sites: PlayerBar(backend=...) (a function giving (player, audio, reason)).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import enum
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal
    from PySide6.QtGui import QKeyEvent, QStandardItem, QStandardItemModel
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTableView, QWidget
except ImportError:                                           # GUI is optional
    QApplication = None


def make_wav(path, secs=0.3, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", (i % 40) * 400 - 8000) for i in range(int(secs * rate))))
    return path


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest(), Path(p).stat().st_mtime_ns


class State(enum.Enum):                                       # (the names QMediaPlayer uses)
    StoppedState = 0
    PlayingState = 1
    PausedState = 2


class Status(enum.Enum):
    LoadedMedia = 0
    EndOfMedia = 1


class Err(enum.Enum):
    NoError = 0
    ResourceError = 1


if QApplication is not None:
    class FakePlayer(QObject):
        positionChanged = Signal(int)
        durationChanged = Signal(int)
        playbackStateChanged = Signal(object)
        mediaStatusChanged = Signal(object)
        errorOccurred = Signal(object, str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.src, self.st, self.pos, self.rate = QUrl(), State.StoppedState, 0, 1.0
            self.calls = []

        def source(self):
            return self.src

        def setSource(self, url):
            self.calls.append(("source", url.toLocalFile()))
            self.src, self.pos = url, 0
            if not url.isValid() and self.st != State.StoppedState:
                self._set(State.StoppedState)

        def _set(self, st):
            self.st = st
            self.playbackStateChanged.emit(st)

        def play(self):
            self.calls.append(("play", self.src.toLocalFile()))
            self._set(State.PlayingState)

        def pause(self):
            self.calls.append(("pause",))
            self._set(State.PausedState)

        def stop(self):
            self.calls.append(("stop",))
            self._set(State.StoppedState)

        def playbackState(self):
            return self.st

        def position(self):
            return self.pos

        def setPosition(self, ms):
            self.calls.append(("seek", ms))
            self.pos = ms
            self.positionChanged.emit(ms)

        def setPlaybackRate(self, r):
            self.rate = r

    class Table(QTableView):
        """An album table that notes the Space presses it gets itself (after the bar's filter)."""
        def __init__(self):
            super().__init__()
            self.spaces = 0

        def keyPressEvent(self, e):
            if e.key() == Qt.Key_Space:
                self.spaces += 1
            super().keyPressEvent(e)

    class FakeAudio:
        def __init__(self):
            self.volume, self.muted = None, False

        def setVolume(self, v):
            self.volume = v

        def setMuted(self, m):
            self.muted = m


def fake_backend(parent):
    return FakePlayer(parent), FakeAudio(), None


def no_backend(parent):
    return None, None, "No sound output was found."


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class PlayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from gui import player, style
        style.apply(cls.app)
        cls.player = player

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-player-"))
        self.album = self.tmp / "Band" / "Alpha"
        self.album.mkdir(parents=True)
        self.files = [make_wav(self.album / n) for n in ("1 One.wav", "2 Two.wav", "10 Ten.wav")]
        (self.album / "cover.jpg").write_bytes(b"\xff\xd8")
        self.before = {p: digest(p) for p in self.files}
        self.bars, self.parents = [], []

    def tearDown(self):
        for b in self.bars:
            b.close_player()
            b.deleteLater()
        for w in self.parents:
            w.deleteLater()
        self.app.processEvents()
        for p, d in self.before.items():                      # (playing never touches a file)
            self.assertEqual(digest(p), d)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bar(self, backend=fake_backend, parent=None):
        b = self.player.PlayerBar(parent, backend=backend)
        self.bars.append(b)
        return b

    def shown_parent(self):
        w = QWidget()
        self.parents.append(w)
        return w

    def wait(self, cond, secs=5):
        end = time.monotonic() + secs
        while not cond() and time.monotonic() < end:
            QTest.qWait(10)
        self.assertTrue(cond(), "timed out")

    def entry(self, files=None):
        return {"source_dir": str(self.album), "status": "ready",
                "files": [{"src": str(f), "dst": str(f), "op": "none"} for f in (files or self.files)]}

    # ---- no backend ---------------------------------------------------------
    def test_no_backend_builds_disabled_with_reason(self):
        win = self.shown_parent()
        b = self.bar(no_backend, win)
        win.show()
        self.assertTrue(b.isHidden())                         # (hidden until something plays)
        self.assertFalse(b.isVisible())
        self.assertFalse(b.available)
        for w in b.controls:
            self.assertFalse(w.isEnabled())
            self.assertEqual(w.toolTip(), "No sound output was found.")
        b.show()
        self.assertIsNone(b.play_album(self.entry()))
        self.assertIn("No sound output", b.title.text())
        b.toggle(), b.next(), b.previous(), b.stop()          # (all harmless)
        self.assertFalse(b.release())

    def test_make_backend_reasons(self):
        with mock.patch.dict(sys.modules, {"PySide6.QtMultimedia": None}):
            p, a, why = self.player.make_backend(None)
        self.assertIsNone(p)
        self.assertIn("Qt Multimedia", why)
        try:
            from PySide6 import QtMultimedia
        except ImportError:
            return
        with mock.patch.object(QtMultimedia.QMediaDevices, "audioOutputs", return_value=[]):
            p, a, why = self.player.make_backend(None)
        self.assertEqual((p, why), (None, "No sound output was found."))
        with mock.patch.object(QtMultimedia.QMediaDevices, "audioOutputs", return_value=[object()]), \
                mock.patch.object(QtMultimedia.QMediaPlayer, "isAvailable", return_value=False):
            p, a, why = self.player.make_backend(None)
        self.assertEqual((p, why), (None, "Qt Multimedia has no media backend on this system."))

    # ---- the tracks -----------------------------------------------------------
    def test_album_tracks(self):
        t = self.player.album_tracks
        self.assertEqual(t(str(self.album)), [str(self.files[i]) for i in (0, 1, 2)])
        # a plan entry keeps its own order; a moved file is found at its destination
        moved = self.tmp / "moved.wav"
        shutil.copy(self.files[0], moved)
        e = self.entry([self.files[2], self.files[1]])
        e["files"].append({"src": str(self.tmp / "gone.wav"), "dst": str(moved)})
        e["files"].append({"src": str(self.album / "cover.jpg"), "dst": ""})
        self.assertEqual(t(e), [str(self.files[2]), str(self.files[1]), str(moved)])
        # disc folders when the album folder has no audio of its own
        box = self.tmp / "Box"
        for d in ("CD2", "CD1", "Scans"):
            (box / d).mkdir(parents=True)
        make_wav(box / "CD2" / "01.wav"), make_wav(box / "CD1" / "02.wav"), make_wav(box / "CD1" / "01.wav")
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(box / "CD1" / "01.wav"),
                            str(box / "CD1" / "03.flac")], check=True)
        got = [str(Path(p).relative_to(box)) for p in t(str(box))]
        want = ["CD1/01.wav", "CD1/02.wav"] + (["CD1/03.flac"] if shutil.which("ffmpeg") else []) + ["CD2/01.wav"]
        self.assertEqual(got, want)
        self.assertEqual(t(str(self.tmp / "missing")), [])
        self.assertEqual(self.player.clock(65000), "1:05")
        self.assertEqual(self.player.clock(3725000), "1:02:05")

    # ---- playback -------------------------------------------------------------
    def test_play_album_next_previous_end(self):
        win = self.shown_parent()
        b = self.bar(parent=win)
        win.show()
        self.assertFalse(b.isVisible())
        p = b.player
        seen, states = [], []
        b.track_changed.connect(seen.append)
        b.playing.connect(states.append)
        b.play_album(self.entry())
        self.assertTrue(b.isVisible())
        self.wait(lambda: b.tracks)
        self.assertEqual(p.calls[-1], ("play", str(self.files[0])))
        self.assertTrue(b.is_playing())
        self.assertEqual(b.count.text(), "1 of 3")                    # (its own label: never shortened)
        self.assertEqual(b.btn_play.accessibleName(), "Pause")
        self.assertTrue(b.btn_prev.isEnabled())
        b.next()
        self.assertEqual(b.current(), str(self.files[1]))
        p.pos = 5000
        b.previous()                                          # (past 3 s: the same track again)
        self.assertEqual((b.current(), p.pos), (str(self.files[1]), 0))
        b.previous()
        self.assertEqual(b.current(), str(self.files[0]))
        p.mediaStatusChanged.emit(Status.EndOfMedia)          # (a track ends: the next one plays)
        self.assertEqual(b.current(), str(self.files[1]))
        b.next()
        self.assertFalse(b.btn_next.isEnabled())
        b.next()                                              # (the last track: nothing further)
        self.assertEqual(b.current(), str(self.files[2]))
        b.toggle()                                            # (pause, play: one signal each)
        self.assertEqual(states, [True, False])
        b.toggle()
        self.assertEqual(states, [True, False, True])
        p.mediaStatusChanged.emit(Status.EndOfMedia)          # (the album ends: stopped, at its start)
        self.assertEqual(states, [True, False, True, False])  # (track changes while playing: no repeats)
        self.assertFalse(b.is_playing())
        self.assertEqual(b.index, 0)
        self.assertFalse(p.source().isValid())
        b.toggle()                                            # (Play again starts from the first track)
        self.assertEqual(p.calls[-1], ("play", str(self.files[0])))
        b.toggle()
        self.assertEqual(p.calls[-1], ("pause",))
        self.assertEqual(seen[:3], [str(self.files[0]), str(self.files[1]), str(self.files[0])])
        b.close_player()
        self.assertFalse(b.isVisible())

    def test_newer_album_wins_and_empty_folder(self):
        b = self.bar()
        empty = self.tmp / "Empty"
        empty.mkdir()
        real = self.player.album_tracks
        older_done = []

        def slow_first(album):                                # (the older album finishes last)
            if str(album) == str(self.album):
                time.sleep(0.3)
                older_done.append(True)
            return real(album)
        with mock.patch.object(self.player, "album_tracks", slow_first):
            b.play_album(str(self.album))
            b.play_album(str(empty))
            self.wait(lambda: "No audio files" in b.title.text())
            self.wait(lambda: older_done)
            QTest.qWait(100)                                  # (the older result is delivered, and dropped)
        self.assertEqual((b.tracks, b.current(), b.key), ([], "", self.player.album_key(str(empty))))
        self.assertNotIn(("play", str(self.files[0])), b.player.calls)
        self.assertFalse(b.btn_play.isEnabled())

    def test_seek_rate_volume(self):
        b = self.bar()
        p = b.player
        b.play_files(self.files, 1, "Alpha")
        p.durationChanged.emit(125000)
        p.positionChanged.emit(65000)
        self.assertEqual((b.elapsed.text(), b.remaining.text()), ("1:05", "−1:00"))      # (a minus sign)
        self.assertEqual(b.seek.value(), 65000)
        b.seek.setSliderDown(True)                            # (sliderPressed)
        p.positionChanged.emit(70000)                         # (dragging: the handle stays where it is held)
        self.assertEqual(b.seek.value(), 65000)
        b.seek.setValue(100000)
        self.assertNotEqual(p.calls[-1], ("seek", 100000))
        b.seek.setSliderDown(False)                           # (sliderReleased: seek there)
        self.assertEqual(p.calls[-1], ("seek", 100000))
        b.seek.setFocus()
        b.seek.triggerAction(b.seek.SliderAction.SliderPageStepAdd)        # (keys and clicks beside the handle seek too)
        self.assertEqual(p.calls[-1], ("seek", 110000))
        b.rate.setCurrentIndex(self.player.RATES.index(1.5))
        self.assertEqual(p.rate, 1.5)
        from gui.common import ex
        saved = lambda: json.loads(gj.read_text()).get("player_volume") if gj.exists() else None  # noqa: E731
        gj = ex.CONFIG_DIR / "gui.json"
        b.volume.setSliderDown(True)                          # (a drag: saved when let go)
        b.volume.setValue(30)
        self.assertNotEqual(saved(), 30)
        b.volume.setSliderDown(False)
        self.assertEqual(saved(), 30)
        b.volume.setValue(100)                                # (the wheel or the keys: saved once they stop)
        self.assertEqual(b.audio.volume, 1.0)
        b.volume.setValue(50)
        self.assertTrue(0 < b.audio.volume < 0.5)            # (the slider follows loudness, not amplitude)
        self.assertEqual(saved(), 30)
        self.wait(lambda: saved() == 50)
        b.btn_mute.click()
        self.assertTrue(b.audio.muted)
        self.assertEqual(b.btn_mute.toolTip(), "Unmute")
        self.assertEqual(self.bar().volume.value(), 50)       # (remembered)
        b.volume.setValue(60)                                 # (closing the bar saves a change not saved yet)
        b.close_player()
        self.assertEqual(saved(), 60)

    def test_space_on_album_table(self):
        b = self.bar()
        model = QStandardItemModel(1, 1)
        model.setItem(0, 0, QStandardItem("Alpha"))
        view = Table()
        view.setModel(model)
        chosen = {"album": self.entry()}
        b.attach(view, lambda: chosen["album"])
        view.show()
        view.setFocus()
        QTest.keyClick(view, Qt.Key_Space)
        self.wait(lambda: b.is_playing())
        QTest.keyClick(view, Qt.Key_Space)                    # (the same album: pause)
        self.assertEqual(b.player.calls[-1], ("pause",))
        QTest.keyClick(view, Qt.Key_Space)
        self.assertTrue(b.is_playing())
        other = self.tmp / "Other"
        other.mkdir()
        make_wav(other / "a.wav")
        chosen["album"] = str(other)                          # (another album: from its first track)
        QTest.keyClick(view, Qt.Key_Space)
        self.wait(lambda: b.current() == str(other / "a.wav"))
        self.assertEqual(view.spaces, 0)                      # (an album is selected: the bar's)
        n = len(b.player.calls)
        for _ in range(3):                                    # (holding Space: the repeats do nothing)
            QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True))
        self.assertEqual(len(b.player.calls), n)
        self.assertEqual(view.spaces, 0)
        chosen["album"] = None                                # (nothing selected: Space is the table's)
        QTest.keyClick(view, Qt.Key_Space)
        self.assertEqual(len(b.player.calls), n)
        self.assertEqual(view.spaces, 1)
        view.deleteLater()

    def test_release_before_files_change(self):
        b = self.bar()
        b.play_files(self.files)
        self.assertFalse(b.release([self.tmp / "unrelated.wav"]))
        self.assertTrue(b.is_playing())
        self.assertTrue(b.release([self.files[0]]))
        self.assertFalse(b.is_playing())
        self.assertFalse(b.player.source().isValid())
        self.assertFalse(b.btn_play.isEnabled())
        b.play_files(self.files)
        self.assertTrue(b.release())
        b.play_files(self.files)                              # (a later track of the album: stopped too)
        self.assertEqual(b.current(), str(self.files[0]))
        self.assertTrue(b.release([self.files[2]]))
        self.assertFalse(b.is_playing())
        self.assertFalse(b.player.source().isValid())
        self.assertEqual((b.tracks, b.index, b.key), ([], -1, ""))
        b.play_files(self.files)
        self.assertTrue(b.release([self.files[1]]))
        self.assertFalse(b.player.source().isValid())
        self.assertEqual(b.tracks, [])

    def test_release_during_a_load_stops_what_plays(self):
        b = self.bar()
        other = self.tmp / "Other"
        other.mkdir()
        make_wav(other / "a.wav")
        real, gathered = self.player.album_tracks, []

        def noted(album):
            out = real(album)
            gathered.append(True)
            return out
        b.play_files(self.files)
        self.assertTrue(b.is_playing())
        with mock.patch.object(self.player, "album_tracks", noted):
            b.play_album(str(other))                          # (the worker has not answered yet)
            self.assertTrue(b.release([self.tmp / "unrelated.wav"]))
            self.assertFalse(b.is_playing())
            self.assertFalse(b.player.source().isValid())
            self.assertEqual(b.tracks, [])
            b.player.calls.clear()
            self.wait(lambda: gathered)
            QTest.qWait(100)
        self.assertEqual([c for c in b.player.calls if c[0] == "play"], [])
        self.assertFalse(b.is_playing())

    def test_album_folder_that_cannot_be_read(self):
        b = self.bar()

        def denied(album):
            raise PermissionError("denied")
        with mock.patch.object(self.player, "album_tracks", denied):
            b.play_album(str(self.album))
            self.assertTrue(b._loading)
            self.wait(lambda: b.title.text() == "Could not read the album's folder.")
        self.assertFalse(b._loading)
        b.play_files(self.files)                              # (an unrelated change leaves playback alone)
        self.assertFalse(b.release([self.tmp / "unrelated.wav"]))
        self.assertTrue(b.is_playing())

    def test_quitting_saves_a_pending_volume(self):
        from gui.common import ex
        gj = ex.CONFIG_DIR / "gui.json"
        b = self.bar()
        b.volume.setValue(b.volume.value() // 2 + 7)
        want = b.volume.value()
        self.assertTrue(b._save_timer.isActive())
        self.app.aboutToQuit.emit()                           # (the app quits before the timer fires)
        self.assertFalse(b._save_timer.isActive())
        self.assertEqual(json.loads(gj.read_text()).get("player_volume"), want)

    def test_release_drops_an_album_still_opening(self):
        b = self.bar()
        real, gathered = self.player.album_tracks, []

        def noted(album):
            out = real(album)
            gathered.append(True)
            return out
        for paths in (None, [self.tmp / "unrelated.wav"]):
            b.player.calls.clear()
            gathered.clear()
            with mock.patch.object(self.player, "album_tracks", noted):
                b.play_album(self.entry())                    # (the worker has not answered yet)
                self.assertTrue(b.release(paths))
                self.wait(lambda: gathered)                   # (the worker has finished ...)
                QTest.qWait(100)                              # (... and its result was delivered)
            self.assertEqual([c for c in b.player.calls if c[0] == "play"], [])
            self.assertEqual((b.tracks, b.current()), ([], ""))
            self.assertFalse(b.player.source().isValid())
        b.play_album(self.entry())                            # (a later album still plays)
        self.wait(lambda: b.is_playing())

    def test_error_is_shown(self):
        b = self.bar()
        b.play_files(self.files)
        b.player.errorOccurred.emit(Err.NoError, "")
        self.assertTrue(b.is_playing())
        b.player.errorOccurred.emit(Err.ResourceError, "Not a media file")
        self.assertFalse(b.is_playing())
        self.assertIn("Could not play 1 One.wav: Not a media file", b.title.text())

    def test_retint_and_icons(self):
        b = self.bar()
        b.retint()
        for btn in (b.btn_prev, b.btn_play, b.btn_next, b.btn_mute, b.btn_close):
            self.assertFalse(btn.icon().isNull(), btn.toolTip())

    # ---- the real backend (muted, nothing played) -------------------------------
    def test_real_backend_loads_wav(self):
        player, audio, why = self.player.make_backend(None)
        if player is None:
            self.skipTest(why)
        audio.setMuted(True)
        player.setSource(QUrl.fromLocalFile(str(self.files[0])))
        self.wait(lambda: player.duration() > 0 or player.error().name != "NoError")
        self.assertEqual(player.error().name, "NoError")
        self.assertGreater(player.duration(), 200)
        player.setSource(QUrl())
        player.deleteLater()
        audio.deleteLater()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
