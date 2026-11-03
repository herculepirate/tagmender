"""gui/player.py - The built-in player (as Picard 3's player toolbar): a compact bar at the bottom of
the window that plays the selected album's tracks from its folder. It only reads the files.

PlayerBar(parent) builds the bar, hidden until something plays. play_album(album) plays a plan entry
(its files, in the plan's order) or an album folder (its audio files, then those of its disc
folders, in natural order); the list is gathered on a worker, so the UI never waits on the disk.
attach(view, album_for) makes Space on that table play or pause the selected album. release(paths)
stops playback before Apply or Undo touches the files. A volume change not saved yet is saved by
close_player() and when the app quits; the main window's closeEvent should call close_player().

Qt Multimedia may be missing, or find no sound output (a server, offscreen tests): the bar still
builds, and every control is disabled with a tooltip that says why."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from PySide6.QtCore import (QByteArray, QCoreApplication, QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton

from . import icons, style
from .common import _gui_prefs, _set_gui_pref, album_words, ex
from .i18n import tr
from .work import start

log = logging.getLogger("tagmender.gui")

RATES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
SKIP = 5000                     # ms: Skip Forward / Back when the setting cannot be read


def skip_ms() -> int:
    """Skip Forward / Back: Preferences › General › Skip by."""
    try:
        import app_settings
        return int(app_settings.get("player.skip_seconds", SKIP // 1000)) * 1000
    except (OSError, ValueError, TypeError):
        return SKIP
VOLUME_STEP = 10                # Volume Up / Down, of 100
MINUS = "−"                     # (the remaining time's sign: a minus, not a hyphen)
RESTART_AFTER = 3000            # ms: Previous past this point starts the track again (as every player does)
VOLUME_PREF = "player_volume"   # gui.json: the volume slider, 0-100
SAVE_VOLUME_AFTER = 500         # ms: the volume is saved once the wheel or the keys stop
TITLE_CHARS = 12                # the title is never shortened below about this many characters
SEEK_MAX = 320                  # px: the seek slider's widest

# Lucide line icons (ISC licence) the app's icon folder does not ship yet; a file of the same name in
# assets/icons/lucide/ is used instead once it is there
_SVG = {
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "skip-back": '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/>',
    "skip-forward": '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/>',
    "volume-2": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 '
                '0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/>'
                '<path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
    "volume-x": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 '
                '0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/>'
                '<line x1="22" x2="16" y1="9" y2="15"/><line x1="16" x2="22" y1="9" y2="15"/>',
}


def _icon(name, role="ink", size=16) -> QIcon:
    if name not in _SVG or (icons.DIR / f"{name}.svg").exists():
        return icons.icon(name, role, size)
    from PySide6.QtSvg import QSvgRenderer
    ic = QIcon()
    for state, colour in ((QIcon.Normal, style.color(role)), (QIcon.Disabled, style.color("faint"))):
        text = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
                f'stroke="{colour}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                f'{_SVG[name]}</svg>')
        for ratio in (1.0, 2.0):
            px = QPixmap(int(size * ratio), int(size * ratio))
            px.fill(Qt.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.Antialiasing)
            QSvgRenderer(QByteArray(text.encode())).render(p, QRectF(0, 0, size * ratio, size * ratio))
            p.end()
            px.setDevicePixelRatio(ratio)
            ic.addPixmap(px, state)
    return ic


# --------------------------------------------------------------------------
# the tracks (on a worker: the folder may be on a slow disk)
# --------------------------------------------------------------------------
def _natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def _audio_in(folder: Path) -> list:
    try:
        return sorted((str(x) for x in folder.iterdir() if x.is_file() and x.suffix.lower() in ex.AUDIO_EXT),
                      key=_natural)
    except OSError:
        return []


def album_tracks(album) -> list:
    """The files to play, in order. A plan entry gives its own files (where they are now: the source,
    or after an Apply the destination); a folder gives its audio files, or else those of its disc
    folders (CD1, CD2 ...). Nothing is opened for writing."""
    if isinstance(album, dict) and album.get("files"):
        out = []
        for fe in album["files"]:
            for p in (fe.get("src"), fe.get("dst")):
                if p and os.path.isfile(p) and Path(p).suffix.lower() in ex.AUDIO_EXT:
                    out.append(str(p))
                    break
        if out:
            return out
    folder = Path(str(album["source_dir"] if isinstance(album, dict) else album))
    out = _audio_in(folder)
    if not out:
        try:
            subs = sorted((x for x in folder.iterdir() if x.is_dir()), key=_natural)
        except OSError:
            subs = []
        for d in subs:
            out += _audio_in(d)
    return out


def album_key(album) -> str:
    folder = album.get("source_dir", "") if isinstance(album, dict) else album
    return os.path.realpath(str(folder)) if folder else ""


def clock(ms) -> str:
    """1:05, or 1:02:05 past an hour."""
    s = max(0, int(ms or 0)) // 1000
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def _state(x) -> str:
    """A Qt Multimedia enum by name ("PlayingState", "EndOfMedia"): a stand-in player in tests can
    use any enum with the same names."""
    return getattr(x, "name", str(x)).split(".")[-1]


# --------------------------------------------------------------------------
# the media backend
# --------------------------------------------------------------------------
def make_backend(parent):
    """(player, audio output, None), or (None, None, why playback is not possible)."""
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    except ImportError as exc:
        log.info("player: Qt Multimedia is not available (%s)", exc)
        return None, None, tr("Playback needs Qt Multimedia, which is not installed.")
    if not QMediaDevices.audioOutputs():
        return None, None, tr("No sound output was found.")
    player = QMediaPlayer(parent)
    if not player.isAvailable():
        player.deleteLater()
        return None, None, tr("Qt Multimedia has no media backend on this system.")
    audio = QAudioOutput(parent)
    player.setAudioOutput(audio)
    devices = QMediaDevices(parent)                      # (follow the system's default output)
    devices.audioOutputsChanged.connect(lambda: audio.setDevice(QMediaDevices.defaultAudioOutput()))
    player._devices = devices
    return player, audio, None


def _linear(v):
    """The slider's 0-100 (as loudness is heard) as the output's linear 0.0-1.0."""
    if v >= 100:
        return 1.0
    try:
        from PySide6.QtMultimedia import QtAudio
        return QtAudio.convertVolume(v / 100, QtAudio.VolumeScale.LogarithmicVolumeScale,
                                     QtAudio.VolumeScale.LinearVolumeScale)
    except Exception:
        return (v / 100) ** 2


# --------------------------------------------------------------------------
# the bar
# --------------------------------------------------------------------------
class _Space(QObject):
    """Space on an album table: play or pause the selected album."""
    def __init__(self, bar, view, album_for):
        super().__init__(view)
        self.bar, self.album_for = bar, album_for

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and e.key() == Qt.Key_Space \
                and not (e.modifiers() & ~Qt.KeypadModifier):
            album = self.album_for()
            if album is None:
                return False
            e.accept()
            if e.type() == QEvent.KeyPress and not e.isAutoRepeat():   # (holding Space: once)
                self.bar.play_pause_album(album)
            return True
        return False


class ElidedLabel(QLabel):
    """A one-line label that shortens its text at the end to the room it has (text() is all of it)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setTextFormat(Qt.PlainText)

    def setText(self, text):
        self._full = text or ""
        self.updateGeometry()
        self._elide()

    def text(self):
        return self._full

    def sizeHint(self):                                  # (all of the text: no wider than its words)
        h = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, h.height())

    def minimumSizeHint(self):
        return QSize(self.minimumWidth(), super().minimumSizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width())))


class PlayerBar(QFrame):
    """The player. Signals: playing(bool) as playback starts and stops, track_changed(path or "")."""
    playing = Signal(bool)
    track_changed = Signal(str)

    def __init__(self, parent=None, backend=None):
        super().__init__(parent)
        self.setObjectName("playerBar")
        self.tracks, self.index, self.key, self.label = [], -1, "", ""
        self._gen = 0                                    # (a newer play_album wins over an older one)
        self._loading = False                            # (a play_album is gathering its tracks)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_VOLUME_AFTER)
        self._save_timer.timeout.connect(self._save_volume)
        app = QCoreApplication.instance()
        if app is not None:                              # (quitting within SAVE_VOLUME_AFTER: still saved)
            app.aboutToQuit.connect(self._save_pending_volume)
        self._seeking = False
        self._was_playing = False
        self.player, self.audio, self.reason = (backend or make_backend)(self)
        self._build()
        if self.player is not None:
            self.player.positionChanged.connect(self._position)
            self.player.durationChanged.connect(self._duration)
            self.player.playbackStateChanged.connect(self._state_changed)
            self.player.mediaStatusChanged.connect(self._status)
            self.player.errorOccurred.connect(self._error)
            if hasattr(self.player, "metaDataChanged"):
                self.player.metaDataChanged.connect(self._show_title)
            if self.audio is not None:                   # (the saved volume: nothing to save again)
                self.audio.setVolume(_linear(self.volume.value()))
        self._enable()
        self.retint()
        self.setVisible(False)

    # ---- building ---------------------------------------------------------
    def _tool(self, name, tip, slot):
        b = QToolButton()
        b.setProperty("role", "tool")
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(tip.split(" (")[0])
        b.setProperty("icon_name", name)
        b.clicked.connect(slot)
        return b

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(style.SPACE["m"], style.SPACE["xs"], style.SPACE["s"], style.SPACE["xs"])
        lay.setSpacing(style.SPACE["xs"])
        self.btn_prev = self._tool("skip-back", tr("Previous track"), self.previous)
        self.btn_play = self._tool("play", tr("Play (Space)"), self.toggle)
        self.btn_next = self._tool("skip-forward", tr("Next track"), self.next)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            lay.addWidget(b)
        lay.addSpacing(style.SPACE["s"])
        self.title = ElidedLabel()                       # (as wide as its words; shortened to ~12 characters last)
        self.title.setMinimumWidth(self.title.fontMetrics().averageCharWidth() * TITLE_CHARS)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        lay.addWidget(self.title)
        self.count = QLabel("")                          # ("3 of 11": right after the title, never shortened)
        self.count.setFont(style.font("caption"))
        self.count.setAccessibleName(tr("Track number"))
        lay.addWidget(self.count)
        lay.addStretch(1)                                # (spare room goes here, not between title and count)
        lay.addSpacing(style.SPACE["s"])
        self.elapsed = QLabel("0:00")
        self.remaining = QLabel(MINUS + "0:00")
        for w in (self.elapsed, self.remaining):
            w.setFont(style.font("caption"))
            w.setMinimumWidth(w.fontMetrics().horizontalAdvance(MINUS + "0:00:00"))
        self.elapsed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)       # (both toward the slider)
        self.remaining.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.elapsed.setAccessibleName(tr("Time played"))
        self.remaining.setAccessibleName(tr("Time left"))
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setAccessibleName(tr("Position in the track"))
        self.seek.setToolTip(tr("Position in the track"))
        self.seek.setMinimumWidth(96)
        self.seek.setMaximumWidth(SEEK_MAX)
        self.seek.setRange(0, 0)
        self.seek.setPageStep(10000)
        self.seek.setSingleStep(5000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderMoved.connect(lambda v: self._times(v, self.seek.maximum()))
        self.seek.sliderReleased.connect(self._seek_done)
        self.seek.actionTriggered.connect(self._seek_action)
        lay.addWidget(self.elapsed)
        lay.addSpacing(style.SPACE["s"])
        lay.addWidget(self.seek, 2)                      # (room up to SEEK_MAX; the title comes first)
        lay.addSpacing(style.SPACE["s"])
        lay.addWidget(self.remaining)
        lay.addSpacing(style.SPACE["s"])
        self.rate = QComboBox()                          # (compact: borderless, as wide as "0.75×")
        self.rate.setProperty("role", "tool")
        self.rate.setAccessibleName(tr("Playback speed"))
        self.rate.setToolTip(tr("Playback speed"))
        for r in RATES:
            self.rate.addItem(f"{r:g}×", r)
        self.rate.setCurrentIndex(RATES.index(1.0))
        widest = max(self.rate.fontMetrics().horizontalAdvance(self.rate.itemText(i)) for i in range(len(RATES)))
        self.rate.setFixedWidth(widest + 6 + 16 + 4)     # (text, padding, the arrow, the border)
        self.rate.currentIndexChanged.connect(self._rate)
        lay.addWidget(self.rate)
        self.btn_mute = self._tool("volume-2", tr("Mute"), self.toggle_mute)
        self.btn_mute.setCheckable(True)
        lay.addWidget(self.btn_mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setAccessibleName(tr("Volume"))
        self.volume.setToolTip(tr("Volume"))
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(70)
        try:
            v = int(_gui_prefs().get(VOLUME_PREF, 80))
        except (TypeError, ValueError):
            v = 80
        self.volume.setValue(min(100, max(0, v)))
        self.volume.valueChanged.connect(self._set_volume)
        self.volume.sliderReleased.connect(self._save_volume)
        lay.addWidget(self.volume)
        self.btn_close = self._tool("x", tr("Stop and close the player"), self.close_player)
        lay.addWidget(self.btn_close)
        self.controls = (self.btn_prev, self.btn_play, self.btn_next, self.seek, self.rate, self.btn_mute,
                         self.volume)

    def retint(self):
        """Redraw the icons and the bar in the current palette (call when the appearance changes)."""
        for b in (self.btn_prev, self.btn_play, self.btn_next, self.btn_mute, self.btn_close):
            b.setIcon(_icon(b.property("icon_name"), "muted" if b is self.btn_close else "ink"))
        self.elapsed.setStyleSheet(f"color: {style.color('muted')};")
        self.remaining.setStyleSheet(f"color: {style.color('muted')};")
        self.setStyleSheet(f"QFrame#playerBar {{ background: {style.color('raised')}; "
                           f"border-top: 1px solid {style.color('line')}; }}")

    def _enable(self):
        ok = self.player is not None
        has = ok and bool(self.tracks)
        for w in self.controls:
            w.setEnabled(has if w in (self.btn_prev, self.btn_play, self.btn_next, self.seek) else ok)
            if not ok:
                w.setToolTip(self.reason)
        if has:
            self.btn_prev.setEnabled(True)
            self.btn_next.setEnabled(self.index < len(self.tracks) - 1)

    # ---- what the window calls ---------------------------------------------
    @property
    def available(self) -> bool:
        return self.player is not None

    def is_playing(self) -> bool:
        return self.player is not None and _state(self.player.playbackState()) == "PlayingState"

    def current(self) -> str:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else ""

    def play_album(self, album, index=0):
        """Play an album (a plan entry or a folder) from its track `index`. The bar shows itself; the
        tracks are gathered on a worker."""
        self._gen += 1
        gen, key = self._gen, album_key(album)
        folder = album.get("source_dir", "") if isinstance(album, dict) else album
        label = album_words(folder) if folder else ""
        self.setVisible(True)
        if self.player is None:
            self._say(self.reason)
            return None
        self._say(tr("Opening {label}…").format(label=label) if label else tr("Opening…"))
        self._loading = True

        def done(tracks):
            if gen == self._gen:
                self._loading = False
                self._loaded(tracks, index, key, label)

        def failed(msg):
            if gen == self._gen:
                self._loading = False
                self._say(tr("Could not read the album's folder."))
        return start(self, lambda w: album_tracks(album), done, failed=failed)

    def play_files(self, paths, index=0, label=""):
        """Play these files in this order (already gathered: nothing is read here)."""
        self._gen += 1
        self._loading = False
        self.setVisible(True)
        if self.player is None:
            self._say(self.reason)
            return
        self._loaded([str(p) for p in paths], index, "", label)

    def play_pause_album(self, album):
        """Space: the album already loaded plays or pauses; another album starts from its first track."""
        if self.tracks and album_key(album) and album_key(album) == self.key:
            self.setVisible(True)
            self.toggle()
        else:
            self.play_album(album)

    def attach(self, view, album_for):
        """Space on `view` (the album table) plays or pauses album_for() - the selected album (a plan
        entry or a folder), or None to leave Space to the table."""
        f = _Space(self, view, album_for)
        view.installEventFilter(f)
        return f

    def release(self, paths=None) -> bool:
        """Stop and let go of the files before they are written, moved or restored: always when `paths`
        is None, else when any track of the loaded album is one of these (a later track would open
        at its old path once the one playing ends). True when playback was stopped. An album still
        being opened is dropped either way (its files cannot be checked yet), so it never starts once
        they change."""
        loading = self._loading
        if paths is None or loading:
            self._gen += 1                               # (a pending play_album result is ignored)
            self._loading = False
        if self.player is None or not (self.tracks or loading):
            return False
        if not loading and paths is not None:
            wanted = {os.path.realpath(str(p)) for p in paths}
            if not any(os.path.realpath(t) in wanted for t in self.tracks):
                return False
        self.stop()
        self.tracks, self.index, self.key = [], -1, ""
        self._say(tr("Stopped while the files change."))
        self._enable()
        return True

    # ---- controls ----------------------------------------------------------
    def toggle(self):
        if self.player is None or not self.tracks:
            return
        if self.is_playing():
            self.player.pause()
        else:
            if _state(self.player.playbackState()) == "StoppedState" and not self.player.source().isValid():
                self._open(max(0, self.index))
            self.player.play()

    def next(self):
        if self.player is not None and self.index < len(self.tracks) - 1:
            self._go(self.index + 1)

    def previous(self):
        if self.player is None or not self.tracks:
            return
        if self.player.position() > RESTART_AFTER or self.index <= 0:
            self.player.setPosition(0)
        else:
            self._go(self.index - 1)

    def stop(self):
        if self.player is None:
            return
        self.player.stop()
        self.player.setSource(QUrl())                    # (let go of the file at once)

    def close_player(self):
        self._gen += 1
        self._loading = False
        if self._save_timer.isActive():                  # (a volume change not saved yet)
            self._save_timer.stop()
            self._save_volume()
        self.stop()
        self.setVisible(False)

    def _say(self, text):
        """A word in place of the title (opening, an error): no track count beside it."""
        self.title.setText(text)
        self.count.setText("")

    def skip(self, ms):
        """Controls > Skip Forward / Back: ms further into the track (back when negative)."""
        if self.player is None or not self.tracks or not self.player.source().isValid():
            return
        pos = max(0, self.player.position() + ms)
        top = self.seek.maximum()
        self.player.setPosition(min(pos, top) if top else pos)

    def step_volume(self, step):
        """Controls > Volume Up / Down (saved as the slider is)."""
        self.volume.setValue(min(100, max(0, self.volume.value() + step)))

    def mute(self):
        """Controls > Mute: as the bar's own button."""
        if self.player is not None:
            self.btn_mute.setChecked(not self.btn_mute.isChecked())
            self.toggle_mute()

    def toggle_mute(self):
        if self.audio is not None:
            self.audio.setMuted(self.btn_mute.isChecked())
        self.btn_mute.setProperty("icon_name", "volume-x" if self.btn_mute.isChecked() else "volume-2")
        self.btn_mute.setToolTip(tr("Unmute") if self.btn_mute.isChecked() else tr("Mute"))
        self.btn_mute.setIcon(_icon(self.btn_mute.property("icon_name")))

    # ---- inside --------------------------------------------------------------
    def _loaded(self, tracks, index, key, label):
        self.tracks, self.key, self.label = list(tracks), key, label
        if not self.tracks:
            self.index = -1
            self.stop()
            self._say(tr("No audio files in {label}.")
                               .format(label=label) if label else tr("No audio files to play."))
            self._enable()
            return
        self._go(min(max(0, index), len(self.tracks) - 1))

    def _go(self, i):
        self._open(i)
        self.player.play()

    def _open(self, i):
        self.index = i
        self.player.setSource(QUrl.fromLocalFile(self.tracks[i]))
        self._times(0, 0)
        self.seek.setRange(0, 0)
        self._show_title()
        self._enable()
        self.track_changed.emit(self.tracks[i])

    def _show_title(self):
        cur = self.current()
        if not cur:
            return
        name, artist = Path(cur).stem, ""
        try:
            from PySide6.QtMultimedia import QMediaMetaData
            md = self.player.metaData()
            name = md.stringValue(QMediaMetaData.Title) or name
            artist = md.stringValue(QMediaMetaData.ContributingArtist) or md.stringValue(QMediaMetaData.AlbumArtist)
        except Exception:
            pass
        n = tr("{0} of {1}").format(self.index + 1, len(self.tracks))
        words = " — ".join(x for x in (name, artist) if x)
        self.title.setText(words)
        self.count.setText(n)
        self.title.setToolTip(f"{cur}\n{self.label}" if self.label else cur)
        self.title.setAccessibleName(tr("Now playing: {words}, track {n}").format(words=words, n=n))

    def _times(self, pos, dur):
        self.elapsed.setText(clock(pos))
        self.remaining.setText(MINUS + clock(max(0, dur - pos)))

    def _position(self, pos):
        if not self._seeking:
            self.seek.blockSignals(True)
            self.seek.setValue(pos)
            self.seek.blockSignals(False)
            self._times(pos, self.seek.maximum())

    def _duration(self, dur):
        self.seek.setRange(0, max(0, dur))
        self._times(self.player.position(), dur)

    def _seek_done(self):
        self._seeking = False
        self.player.setPosition(self.seek.value())

    def _seek_action(self, action):
        quiet = {int(QSlider.SliderAction.SliderMove.value), int(QSlider.SliderAction.SliderNoAction.value)}
        if int(getattr(action, "value", action)) not in quiet and not self.seek.isSliderDown():
            self.player.setPosition(self.seek.sliderPosition())  # (a click beside the handle, the arrow keys)

    def _rate(self, i):
        if self.player is not None:
            self.player.setPlaybackRate(self.rate.itemData(i) or 1.0)

    def _set_volume(self, v):
        if self.audio is not None:
            self.audio.setVolume(_linear(v))
        if not self.volume.isSliderDown():
            self._save_timer.start()                     # (sliderReleased saves a drag at once)

    @Slot()
    def _save_pending_volume(self):
        if self._save_timer.isActive():
            self._save_volume()

    def _save_volume(self):
        self._save_timer.stop()
        if _gui_prefs().get(VOLUME_PREF) != self.volume.value():
            _set_gui_pref(VOLUME_PREF, self.volume.value())

    def _state_changed(self, st):
        on = _state(st) == "PlayingState"
        self.btn_play.setProperty("icon_name", "pause" if on else "play")
        self.btn_play.setIcon(_icon("pause" if on else "play"))
        self.btn_play.setToolTip(tr("Pause (Space)") if on else tr("Play (Space)"))
        self.btn_play.setAccessibleName(tr("Pause") if on else tr("Play"))
        if on != self._was_playing:
            self._was_playing = on
            self.playing.emit(on)

    def _status(self, st):
        if _state(st) == "EndOfMedia":
            if self.index < len(self.tracks) - 1:
                self._go(self.index + 1)
            else:                                        # (the album ended: back to its first track, stopped)
                self.stop()
                self.index = 0
                self._show_title()
                self._times(0, 0)
                self._enable()

    def _error(self, err, msg=""):
        if _state(err) == "NoError":
            return
        cur = self.current()
        log.info("player: could not play %s: %s", cur, msg)
        self.stop()
        self._say(tr("Could not play {name}: {0}").format(msg or tr('unknown error'),
                                                                   name=Path(cur).name) if cur
                           else tr("Could not play: {0}").format(msg or tr('unknown error')))
        self._enable()
