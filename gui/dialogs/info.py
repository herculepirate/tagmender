"""gui/dialogs/info.py - File Info (Picard's Info dialog): what a music file is and what it holds.

Format, codec, bitrate, sample rate, bit depth, channels, length and file size; every tag as the file
stores it (the raw keys: TIT2, ©nam, TITLE ...); each embedded picture's type, size and dimensions.
The file is read on a worker with mutagen and never written to.

The window calls show_file_info(parent, path)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import sheets, style
from ..common import heading, left_header, plural
from ..i18n import N_, tr
from ..work import start

# ID3's (and FLAC's) picture types, as Picard names them
PICTURE_TYPES = (N_("Other"), N_("File icon"), N_("Other file icon"), N_("Front cover"), N_("Back cover"),
                 N_("Booklet page"), N_("Medium"),
                 N_("Lead artist"), N_("Artist"), N_("Conductor"), N_("Band"), N_("Composer"), N_("Lyricist"),
                 N_("Recording location"),
                 N_("During recording"), N_("During performance"), N_("Screen capture"), N_("Bright coloured fish"),
                 N_("Illustration"), N_("Band logo"), N_("Publisher logo"))
FORMATS = {"FLAC": "FLAC", "MP3": "MP3", "EasyMP3": "MP3", "MP4": "MPEG-4 audio (M4A)", "EasyMP4": "MPEG-4 audio (M4A)",
           "OggVorbis": "Ogg Vorbis", "OggOpus": "Opus", "OggFLAC": "Ogg FLAC", "OggSpeex": "Speex",
           "OggTheora": "Ogg Theora", "WAVE": "WAV", "AIFF": "AIFF", "ASF": "Windows Media Audio",
           "WavPack": "WavPack", "MonkeysAudio": "Monkey's Audio", "Musepack": "Musepack", "TrueAudio": "TrueAudio",
           "OptimFROG": "OptimFROG", "TAK": "TAK", "DSF": "DSF (DSD)", "DSDIFF": "DSDIFF (DSD)", "AAC": "AAC (ADTS)",
           "AC3": "AC-3", "ID3FileType": "MP3", "APEv2File": "APEv2"}
LONG = 400                                               # (a longer value - lyrics - is cut in the table)


def human_size(n) -> str:
    """4213000 -> "4.2 MB (4,213,000 bytes)"."""
    if n is None:
        return ""
    for unit, k in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= k:
            return f"{n / k:.1f} {unit} ({n:,} bytes)"
    return plural(n, tr("byte"), tr("bytes"))


def length_text(seconds) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    h, m = divmod(s // 60, 60)
    return (f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}")


def _value(v) -> str:
    """A stored value as text: bytes as their size, a pair as "1/10", lists joined."""
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return tr("{0:,} bytes of data").format(len(v))
    if isinstance(v, tuple):
        return "/".join(str(x) for x in v)
    if isinstance(v, list):
        return "; ".join(_value(x) for x in v)
    val = getattr(v, "value", None)                       # (APEv2 and ASF values)
    if val is not None and not isinstance(v, str):
        return _value(val)
    return str(v)


def _id3_value(fr) -> str:
    if hasattr(fr, "text"):
        return "; ".join(str(x) for x in fr.text)
    if hasattr(fr, "url"):
        return str(fr.url)
    if hasattr(fr, "people"):
        return "; ".join(f"{a}: {b}" for a, b in fr.people)
    if hasattr(fr, "data"):
        return tr("{0:,} bytes of data").format(len(fr.data))
    try:
        return fr.pprint()
    except Exception:
        return ""


def raw_tags(f) -> list:
    """[(key, value)] as stored: every ID3 frame, MP4 atom, Vorbis comment, APEv2 or ASF item
    (pictures are listed apart; their key names them as "(picture)")."""
    from mutagen.id3 import ID3
    tags = getattr(f, "tags", None)
    if tags is None:
        return []
    out = []
    if isinstance(tags, ID3):
        for fr in tags.values():
            key = fr.HashKey
            out.append((key, "(picture)" if fr.FrameID == "APIC" else _id3_value(fr)))
        return out
    items = tags.items() if hasattr(tags, "items") else list(tags)
    for k, v in items:
        if k.lower() in ("covr", "metadata_block_picture", "wm/picture", "cover art (front)", "cover art (back)"):
            out.append((k, "(picture)"))
        else:
            out.append((k, _value(v)))
    return out


def _picture(ptype, mime, desc, data, thumb=48) -> dict:
    from PIL import Image
    dims, kind = None, ""
    try:
        with Image.open(io.BytesIO(data)) as im:
            dims, kind = im.size, im.format or ""
    except Exception:
        pass
    img = QImage.fromData(bytes(data))                  # (QImage, not QPixmap: safe off the UI thread)
    if not img.isNull():
        img = img.scaled(thumb, thumb, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    name = tr(PICTURE_TYPES[ptype]) if isinstance(ptype, int) and 0 <= ptype < len(PICTURE_TYPES) else str(ptype)
    return {"type": name, "mime": mime or (f"image/{kind.lower()}" if kind else ""), "description": desc or "",
            "bytes": len(data), "dimensions": dims, "image": None if img.isNull() else img}


def pictures(f) -> list:
    """Each embedded picture: type, MIME type, description, size in bytes, (width, height)."""
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4, MP4Cover
    out = []
    tags = getattr(f, "tags", None)
    if isinstance(f, FLAC):
        out += [_picture(p.type, p.mime, p.desc, p.data) for p in f.pictures]
    if isinstance(tags, ID3):
        out += [_picture(fr.type, fr.mime, fr.desc, fr.data) for fr in tags.getall("APIC")]
    elif isinstance(f, MP4):
        for c in (tags or {}).get("covr", []):
            mime = "image/png" if getattr(c, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
            out.append(_picture(3, mime, "", bytes(c)))
    elif tags is not None and hasattr(tags, "get"):
        for v in tags.get("metadata_block_picture", []) or []:
            try:
                p = Picture(base64.b64decode(v))
            except Exception:
                continue
            out.append(_picture(p.type, p.mime, p.desc, p.data))
    return out


def read_info(path) -> dict:
    """Everything the dialog shows, read with mutagen (never written). error: why it could not be read."""
    import tagio
    p = Path(path)
    out = {"path": str(p), "name": p.name, "size": None, "format": "", "codec": "", "bitrate": None,
           "sample_rate": None, "bits": None, "channels": None, "length": None, "tags": [], "pictures": [],
           "error": ""}
    try:
        out["size"] = p.stat().st_size
        f = tagio.mutagen_file(p)
    except Exception as exc:
        out["error"] = tr("It cannot be read: {exc}").format(exc=exc)
        return out
    if f is None:
        out["error"] = tr("It is not an audio file TagMender can read.")
        return out
    info = getattr(f, "info", None)
    out["format"] = FORMATS.get(type(f).__name__, type(f).__name__)
    codec = getattr(info, "codec_description", None) or getattr(info, "codec", None)
    if not codec and type(f).__name__ in ("MP3", "EasyMP3"):
        codec = f"MPEG-{info.version:g} Layer {info.layer}" if getattr(info, "layer", None) else ""
    out["codec"] = str(codec or out["format"])
    out["bitrate"] = getattr(info, "bitrate", None) or None
    out["sample_rate"] = getattr(info, "sample_rate", None) or None
    out["bits"] = getattr(info, "bits_per_sample", None) or None
    out["channels"] = getattr(info, "channels", None) or None
    out["length"] = getattr(info, "length", None) or None
    try:
        out["tags"] = raw_tags(f)
    except Exception as exc:
        out["error"] = tr("Its tags cannot be read: {exc}").format(exc=exc)
    if p.suffix.lower() == ".wav":                        # (RIFF INFO, beside an ID3 chunk)
        try:
            out["tags"] += [(f"INFO {k}", _value(v)) for k, v in (tagio.riff_info(p) or {}).items()]
        except Exception:
            pass
    try:
        out["pictures"] = pictures(f)
    except Exception:
        out["pictures"] = []
    return out


def info_rows(d) -> list:
    """(label, text) for the Info tab; a value the file does not say is left out."""
    ch = d.get("channels")
    rows = [(tr("File"), d["name"]), (tr("Folder"), str(Path(d["path"]).parent)), (tr("Format"), d["format"]),
            (tr("Codec"), d["codec"]),
            (tr("Bitrate"), f"{round(d['bitrate'] / 1000)} kbps" if d.get("bitrate") else ""),
            (tr("Sample rate"), f"{d['sample_rate']:,} Hz" if d.get("sample_rate") else ""),
            (tr("Bit depth"), f"{d['bits']} bits" if d.get("bits") else ""),
            (tr("Channels"), {1: tr("1 (mono)"), 2: tr("2 (stereo)")}.get(ch, str(ch)) if ch else ""),
            (tr("Length"), length_text(d.get("length"))), (tr("File size"), human_size(d.get("size")))]
    return [(k, v) for k, v in rows if v]


MAX_HEIGHT = 560                                     # File Info at its tallest (its tables scroll)

def summary(d) -> str:
    """The sheet's subtitle: the format at a glance ("WAV · 44.1 kHz · 16-bit · stereo · 0:01");
    the folder is in the Info tab."""
    ch = d.get("channels")
    rate = d.get("sample_rate")
    parts = [d.get("format") or "",
             (f"{rate / 1000:g} kHz" if rate else ""),
             (f"{d['bits']}-bit" if d.get("bits") else ""),
             ({1: "mono", 2: "stereo"}.get(ch, f"{ch} channels") if ch else ""),
             length_text(d.get("length")) if d.get("length") else ""]
    return " · ".join(p for p in parts if p)


def _table(cols, name):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels([tr(c) for c in cols])
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().hide()
    t.setWordWrap(False)
    t.setAccessibleName(name)
    left_header(t)
    for c in range(len(cols) - 1):
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class FileInfo(QDialog):
    """Tabs Info, Tags and Pictures, filled once the worker has read the file."""

    def __init__(self, parent, path):
        super().__init__(sheets._window(parent))
        sheets.prepare(self, tr("Info: {name}").format(name=Path(path).name))
        self.path = str(path)
        self.data = None
        self.setMinimumSize(560, 320)
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        box = heading(Path(path).name, tr("Reading the file…"))   # (the file's name, then its format: show_info)
        for i in range(box.count()):
            box.itemAt(i).widget().setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.head, self.hint = box.itemAt(0).widget(), box.itemAt(1).widget()
        lay.addLayout(box)
        self.stack = QStackedWidget()
        self.status = QLabel(tr("Reading the file…"))
        self.status.setProperty("role", "hint")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.stack.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName(tr("File info"))
        page = QWidget()
        self.form = QFormLayout(page)
        self.form.setLabelAlignment(Qt.AlignRight)
        self.form.setHorizontalSpacing(style.SPACE["m"])
        self.form.setVerticalSpacing(style.SPACE["s"])
        self.tabs.addTab(page, tr("Info"))
        self.tags = _table([N_("Key"), N_("Value")], tr("Tags as stored"))
        self.tabs.addTab(self.tags, tr("Tags"))
        self.pics = _table([N_("Picture"), N_("Type"), N_("Size"), N_("Dimensions"), N_("Format")],
                           tr("Embedded pictures"))
        self.pics.setIconSize(QSize(48, 48))
        self.tabs.addTab(self.pics, tr("Pictures"))
        self.stack.addWidget(self.tabs)
        lay.addWidget(self.stack, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.setAccessibleName(tr("Close"))
        close.setDefault(True)
        close.setProperty("role", "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.worker = start(self, lambda _w: read_info(self.path), self.show_info, failed=self.show_error)

    def show_error(self, msg):
        self.hint.setText(str(Path(self.path).parent))
        self.status.setText(tr("This file cannot be read.\n") + (msg.splitlines()[0] if msg else ""))
        self.stack.setCurrentWidget(self.status)

    def show_info(self, d):
        self.data = d
        if d.get("error") and not d.get("format"):
            self.show_error(d["error"])
            return
        for k, v in info_rows(d):
            val = QLabel(v)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setWordWrap(True)
            val.setAccessibleName(k)
            self.form.addRow(k + ":", val)
        if d.get("error"):
            note = QLabel(d["error"])
            note.setProperty("role", "hint")
            self.form.addRow("", note)
        self.tags.setRowCount(len(d["tags"]))
        for i, (k, v) in enumerate(d["tags"]):
            key = QTableWidgetItem(k)
            key.setFont(style.mono())
            text = v if len(v) <= LONG else v[:LONG] + "…"
            val = QTableWidgetItem(text.replace("\n", " ⏎ "))
            val.setToolTip(v[:4000])
            self.tags.setItem(i, 0, key)
            self.tags.setItem(i, 1, val)
        self.tabs.setTabText(1, tr("Tags ({0})").format(len(d['tags'])))
        self.pics.setRowCount(len(d["pictures"]))
        for i, p in enumerate(d["pictures"]):
            thumb = QTableWidgetItem()
            if p.get("image") is not None:
                thumb.setIcon(QPixmap.fromImage(p["image"]))
            thumb.setData(Qt.AccessibleTextRole, p["type"])
            self.pics.setRowHeight(i, 56)
            dims = (f"{p['dimensions'][0]} × {p['dimensions'][1]}" if p.get("dimensions")
                    else tr("Not an image it can read"))
            typ = p["type"] + (f" ({p['description']})" if p.get("description") else "")
            for j, it in enumerate((thumb, QTableWidgetItem(typ), QTableWidgetItem(human_size(p["bytes"])),
                                    QTableWidgetItem(dims), QTableWidgetItem(p.get("mime") or ""))):
                self.pics.setItem(i, j, it)
        self.tabs.setTabText(2, tr("Pictures ({0})").format(len(d['pictures'])))
        self.hint.setText(summary(d) or str(Path(self.path).parent))
        self.stack.setCurrentWidget(self.tabs)
        self.fit_height()

    def fit_height(self):
        """As tall as the Info tab needs (the Tags and Pictures tables scroll), at most MAX_HEIGHT:
        a short file's facts are not set in a tall empty sheet."""
        lay = self.layout()
        lay.activate()
        page = self.tabs.widget(0)
        extra = self.tabs.tabBar().sizeHint().height() + style.SPACE["s"]
        rest = lay.sizeHint().height() - self.stack.sizeHint().height()
        want = rest + page.sizeHint().height() + extra
        self.resize(self.width(), max(self.minimumHeight(), min(want, MAX_HEIGHT)))


def show_file_info(parent, path):
    """Open File Info for one file as a sheet of parent's window (offscreen: recorded, not shown)."""
    dlg = FileInfo(parent, path)
    sheets.run(dlg, "file_info")
    return dlg
