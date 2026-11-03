"""gui/script_editor.py - The profile's Picard scripts: its file naming script and its tagger
scripts, edited as in Picard's script editor.

  ScriptEditor(parent, profile, album=None)   a window-modal sheet (open with .open() or open_editor)
  open_editor(parent, profile, album=None)    builds, places and opens one; returns it

The editor colours the script language (functions, %variables%, $noop comments, escapes, brackets
and commas, unknown functions in red), completes function and variable names ($ or % then Ctrl+Space,
or as you type), shows the help of the function under the cursor, marks the matching bracket, and
underlines a script error where it is, naming its line and column. The preview runs every script on
the selected album's planned tags (a sample track when none is selected) on a worker and shows the
tags the tagger scripts change and the file name the naming script gives.

Where a profile keeps its scripts: the naming script in "naming.template" (empty: the preset's), the
tagger scripts in "picard.list_of_scripts" ([position, name, enabled, text]) with
"picard.enable_tagger_scripts"; a profile that sets neither follows Picard's own (tagger_settings).
Saving writes the profile file only (profiles.save): never a music file. The scripts reach the music
through the next preview's plan, as every change does."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons, sheets, style
from .common import heading, left_header, load_profile, pf
from .i18n import N_, tr
from .tagnames import NAMES as TAG_NAMES
from .tagnames import display as tag_name
from .widgets import EmptyState
from .work import _alive, start

# the track the preview shows when no album is selected (as Picard's script editor has one)
SAMPLE_PATH = "/music/Sample Artist/Sample Album/01 Sample Title.flac"
SAMPLE = {
    "title": ["Sample Title"], "artist": ["Sample Artist"], "artists": ["Sample Artist"],
    "album": ["Sample Album"], "albumartist": ["Sample Artist"], "albumartists": ["Sample Artist"],
    "albumartistsort": ["Artist, Sample"], "artistsort": ["Artist, Sample"],
    "tracknumber": ["1"], "totaltracks": ["10"], "discnumber": ["1"], "totaldiscs": ["1"],
    "date": ["2021-06-04"], "originaldate": ["1999-03-01"], "originalyear": ["1999"],
    "genre": ["Rock"], "label": ["Sample Records"], "catalognumber": ["SR-001"], "barcode": ["012345678905"],
    "media": ["CD"], "releasetype": ["album"], "releasestatus": ["official"], "releasecountry": ["GB"],
    "script": ["Latn"], "language": ["eng"],
    "musicbrainz_albumid": ["89ad4ac3-39f7-470e-963a-56509c546377"],
    "musicbrainz_artistid": ["f1b07b4c-9e44-4b1a-9a6c-4b7c1d2f3e4a"],
}
# the variables TagMender's naming adds (naming.Ctx) and Picard's file variables (tagio.file_vars)
OWN_VARIABLES = ("_year", "_albumartists_sorted", "_albumartists", "_multiartist", "_edition", "_edition_kind",
                 "_filepath", "_dirname", "_filename", "_extension", "_filesize", "_format", "_bitrate",
                 "_sample_rate", "_channels", "_bits_per_sample", "_releasecomment", "_totalalbumtracks",
                 "_releasegroup_firstreleasedate", "_primaryreleasetype", "_secondaryreleasetype")
NAMING = "naming"
TAGGER = "tagger"
_ERR = re.compile(r"^(\d+):(\d+)(?::\$(\w+))?: (.*)$", re.S)


# --------------------------------------------------------------------------
# the language: functions, variables, errors, brackets (no Qt)
# --------------------------------------------------------------------------
# version notes in the copied function help ("_Since ... 2.3_", "Note that prior to ... 2.3.2 ..."):
# about another program's releases, so left out of the help shown
_VERSION_NOTE = re.compile(r"_[^_\n]*since\s+picard\b[^_\n]*_|^note that prior to picard\b.*$", re.I | re.M)


def public_doc(doc: str) -> str:
    """A function's help text without the version notes of the program it was copied from."""
    return _VERSION_NOTE.sub("", doc or "").strip()


def functions() -> dict:
    """{name: (signature, help text)} for every script function (Picard's, and TagMender's own)."""
    import naming
    naming.functions()                                   # (registers $words)
    from tagscript._shim import script_functions
    out = {}
    for name, item in dict(script_functions.ext_point_script_functions).items():
        doc = public_doc(item.documentation or "")
        sig = item.signature or ""
        if not sig and doc.startswith(f"${name}("):      # ($words: "$words(text,n): what it does")
            sig, _, doc = doc.partition(": ")
        out[name] = (sig or f"${name}(…)", re.sub(r"\n\s+", " ", doc))
    return dict(sorted(out.items()))


def variables(extra=()) -> list:
    """The variable names to complete: Picard's tags, the hidden variables, and those given."""
    names = set(TAG_NAMES) | set(OWN_VARIABLES)
    for k in extra:
        names.add("_" + k[1:] if k.startswith("~") else k)
    return sorted(names, key=str.casefold)


def escaped(text, i) -> bool:
    """True when text[i] follows an odd number of backslashes (\\( is text, not a bracket)."""
    n = 0
    while i - n - 1 >= 0 and text[i - n - 1] == "\\":
        n += 1
    return n % 2 == 1


def match_bracket(text, pos):
    """The index of the bracket matching the one at pos (None when unmatched or not a bracket)."""
    if not (0 <= pos < len(text)) or text[pos] not in "()" or escaped(text, pos):
        return None
    step, depth = (1, 0) if text[pos] == "(" else (-1, 0)
    i = pos
    while 0 <= i < len(text):
        c = text[i]
        if c in "()" and not escaped(text, i):
            depth += 1 if (c == "(") == (step == 1) else -1
            if depth == 0:
                return i
        i += step
    return None


def function_at(text, pos):
    """The name of the function the cursor at pos is on or inside ("" when none): the innermost
    $name( that is open there, or the $name the cursor is in."""
    m = re.search(r"\$(\w*)$", text[:pos])
    if m:
        whole = re.match(r"\w*", text[pos:]).group(0)
        return m.group(1) + whole
    stack = []
    for m in re.finditer(r"\$(\w+)\(|[()]", text[:pos]):
        if escaped(text, m.start()):
            continue
        tok = m.group(0)
        if tok.startswith("$"):
            stack.append(m.group(1))
        elif tok == "(":
            stack.append("")
        elif stack:
            stack.pop()
    for name in reversed(stack):
        if name:
            return name
    return ""


def position(text, line, col) -> int:
    """The index in text of 1-based line, 0-based column (clamped to the text)."""
    lines = text.split("\n")
    line = max(1, min(line, len(lines)))
    return min(len(text), sum(len(x) + 1 for x in lines[:line - 1]) + max(0, min(col, len(lines[line - 1]))))


def problem(exc, text, prepped=False, kind=None):
    """(index, line, column, message) for a script error: where it is in text, in plain words.
    prepped: the error came from the naming script as Picard runs it, tabs and line breaks
    dropped (its positions are in that one line)."""
    msg = str(exc)
    m = _ERR.match(msg)
    if not m:
        return None, 0, 0, msg
    line, col, name, what = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    kind = kind or type(exc).__name__
    if not name:                       # (the reader has gone past the character: back to it)
        col = col - 1 if kind == "ScriptEndOfFile" else col - 2
    if prepped:
        keep = [i for i, c in enumerate(text) if c not in "\t\n"]
        idx = keep[min(max(col, 0), len(keep) - 1)] if keep else 0
        if col >= len(keep):
            idx = len(text)
    else:
        idx = position(text, line, col)
    line = text.count("\n", 0, idx) + 1
    col = idx - (text.rfind("\n", 0, idx) + 1)
    what = what.replace("Unexpected end of script", tr("The script ends before a bracket or a % is closed"))
    return idx, line, col, what


def check(text):
    """None when the script parses, else (index, line, column, message)."""
    import naming
    import tagscript as ps
    naming.functions()
    try:
        ps.ScriptParser().parse(text)
    except ps.ScriptError as exc:
        return problem(exc, text)
    return None


# --------------------------------------------------------------------------
# what the preview runs on, and the run itself (on a worker)
# --------------------------------------------------------------------------
def path_vars(path) -> dict:
    """Picard's file variables a path gives, without opening the file."""
    p = str(path)
    stem, ext = Path(p).stem, Path(p).suffix
    return {"~filepath": [p], "~dirname": [str(Path(p).parent)], "~filename": [stem],
            "~extension": [ext.lower()[1:]]}


def planned_files(album) -> list:
    """[(label, path, tags)]: each file of a plan entry with the tags the plan gives it before any
    tagger script ran ("unscripted", which the plan keeps when the profile runs tagger scripts:
    the plan's "after" already holds what they made, so running them on it would run them twice;
    else each diff row's "after"); one sample track when there is no album or it has no files."""
    out = []
    for fe in (album or {}).get("files") or []:
        src = fe.get("src") or fe.get("dst") or ""
        if isinstance(fe.get("unscripted"), dict):
            tags = {k: values(v) for k, v in fe["unscripted"].items() if v and k != "picture"}
            out.append((Path(src).name or tr("File"), src, tags))
            continue
        tags = {}
        for row in fe.get("diff") or []:
            vals = row.get("after") if row.get("after") is not None else row.get("before")
            if vals and row.get("field") and row.get("field") != "picture":
                tags[row["field"]] = values(vals)
        for k, v in (fe.get("changes") or {}).items():
            if v and k not in tags and not k.startswith("~") and k != "picture":
                tags[k] = values(v)
        if tags or src:
            out.append((Path(src).name or tr("File"), src, tags))
    return out or [(tr("Sample track"), SAMPLE_PATH, {k: list(v) for k, v in SAMPLE.items()})]


def values(v) -> list:
    """A tag's value as a list of strings: a bare string is one value (never its characters)."""
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def run_preview(items, run_taggers, tags, path, settings=None, compat="linux", editions="off", fallback=""):
    """What the scripts make of one file. items: [{"kind", "text", "enabled"}] (the naming script
    and the tagger scripts, in order). Returns {"before", "after", "removed", "name", "errors":
    {item index: (index, line, column, message)}}. fallback: the naming script an empty one stands
    for (the profile's preset; "%title%" when none is given)."""
    import engine
    import naming
    naming.functions()
    tags = {k: values(v) for k, v in (tags or {}).items()}
    out = {"before": tags, "after": dict(tags), "removed": [], "name": "", "errors": {}}
    for i, it in enumerate(items):
        p = check(it["text"]) if it["text"].strip() else None
        if p:
            out["errors"][i] = p
    hidden = path_vars(path)
    now = {k: values(v) for k, v in tags.items()}
    gone = set()
    if run_taggers:
        for i, it in enumerate(items):
            if it["kind"] != TAGGER or not it.get("enabled") or not it["text"].strip() or i in out["errors"]:
                continue
            caught = []

            class _Log:                                  # (run_tagger_scripts logs what it skips)
                def warning(self, fmt, *args, _c=caught):
                    _c.append(next((a for a in args if isinstance(a, Exception)), fmt % args if args else fmt))
            try:
                now, removed = engine.run_tagger_scripts([it["text"]], now, hidden, _Log(), strip=False)
            except engine.TaggerScriptError as exc:
                out["errors"][i] = (None, 0, 0, str(exc))
                continue
            if caught:
                out["errors"][i] = problem(caught[0], it["text"])
            gone = (gone | set(removed)) - set(now)
    out["after"], out["removed"] = now, sorted(gone)
    naming_items = [i for i, it in enumerate(items) if it["kind"] == NAMING]
    if naming_items and naming_items[0] not in out["errors"]:
        i = naming_items[0]
        own = items[i]["text"]
        text = own if own.strip() else (fallback or "%title%")   # (an empty script: the profile's preset)
        ran = naming.with_editions(text, editions)

        def render(t):
            return naming.render(t, {**hidden, **now}, settings or {}, compat, own=Path(str(path)).stem)
        try:
            out["name"] = "/".join(render(ran)) + Path(str(path)).suffix.lower()
        except naming.TemplateError as exc:          # (a function that fails as it runs)
            out["errors"][i] = runtime_problem(exc, own, ran, render) if own.strip() else \
                (None, 0, 0, tr("The profile's preset does not work here: {exc}").format(exc=exc))
    return out


def runtime_problem(exc, text, ran, render):
    """Where in text (the script as written) the naming script failed as it ran. ran: what was
    run (text with the edition piece added or taken out, see naming.with_editions); the error's
    position is in ran, so text itself is run again to find it there."""
    import naming
    if ran != text:
        try:
            render(text)
        except naming.TemplateError as plain:
            return problem(plain, text, prepped=True, kind="ScriptRuntimeError")
        p = problem(exc, ran, prepped=True, kind="ScriptRuntimeError")
        if p[0] is None:
            return p
        idx = min(p[0], len(text))                   # (only the edition piece fails: near it, in text)
        return idx, text.count("\n", 0, idx) + 1, idx - (text.rfind("\n", 0, idx) + 1), p[3]
    return problem(exc, text, prepped=True, kind="ScriptRuntimeError")


def profile_scripts(prof) -> dict:
    """{"naming", "preset", "taggers": [[position, name, enabled, text]], "run", "from_tagger"}:
    the scripts a profile runs, as the editor shows them."""
    import naming
    nm = prof.data.get("naming") or {}
    tpl = nm.get("template") or ""
    preset = ""
    if not tpl.strip():
        try:
            preset = naming.effective_template(nm, prof.data.get("filenames"), editions=False)
        except naming.TemplateError:
            preset = ""
    over = prof.tagger_overrides() if hasattr(prof, "tagger_overrides") else prof.data.get("tagger") or {}
    picard = {}
    if "list_of_scripts" not in over or "enable_tagger_scripts" not in over:
        try:
            picard = prof.picard_tags()
        except Exception:                                # (an unreadable Picard.ini: none)
            picard = {}
    raw = over["list_of_scripts"] if "list_of_scripts" in over else picard.get("list_of_scripts") or []
    taggers = []
    for n, x in enumerate(raw):
        if isinstance(x, str):
            taggers.append([n, f"Script {n + 1}", True, x])
        elif isinstance(x, (list, tuple)) and len(x) == 4:
            taggers.append([n, str(x[1] or f"Script {n + 1}"), bool(x[2]), str(x[3])])
    run = over.get("enable_tagger_scripts", picard.get("enable_tagger_scripts", bool(taggers)))
    return {"naming": tpl or preset, "preset": preset, "taggers": taggers, "run": bool(run),
            "from_tagger": "list_of_scripts" not in over and bool(taggers),
            "editions": nm.get("editions", "off")}


# --------------------------------------------------------------------------
# the editor's parts
# --------------------------------------------------------------------------
# syntax role -> (palette token, bold, italic)
SYNTAX = {"func": ("accent", True, False), "unknown": ("error", False, True), "var": ("identified", False, False),
          "escape": ("attention", False, False), "special": ("muted", True, False),
          "comment": ("muted", False, True)}


class ScriptHighlighter(QSyntaxHighlighter):
    """Picard's TaggerScriptSyntaxHighlighter in the app's colours: known functions, unknown
    ones, %variables%, escapes (\\$, \\u00e9), brackets and commas, and $noop(...) as a comment
    (across lines)."""

    def __init__(self, document, names=()):
        super().__init__(document)
        self.names = set(names)
        self.refresh(rehighlight=False)

    def refresh(self, rehighlight=True):
        self.formats = {}
        for role, (token, bold, italic) in SYNTAX.items():
            f = QTextCharFormat()
            f.setForeground(QColor(style.color(token)))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            self.formats[role] = f
        if rehighlight:
            self.rehighlight()

    def highlightBlock(self, text):
        for m in re.finditer(r"\$(\w*)\(", text):
            if escaped(text, m.start()) or m.group(1) == "noop":
                continue
            self.setFormat(m.start(), m.end() - m.start() - 1,
                           self.formats["func" if m.group(1) in self.names else "unknown"])
        for m in re.finditer(r"%[\w:]*%", text):
            if not escaped(text, m.start()):
                self.setFormat(m.start(), m.end() - m.start(), self.formats["var"])
        for m in re.finditer(r"\\u[0-9a-fA-F]{4}|\\.", text):
            self.setFormat(m.start(), m.end() - m.start(), self.formats["escape"])
        for m in re.finditer(r"[(),]", text):
            if not escaped(text, m.start()):
                self.setFormat(m.start(), 1, self.formats["special"])
        self._comments(text)

    def _comments(self, text):
        """$noop( ... ): everything to its closing bracket, which may be lines further on (the
        block state holds how many brackets are still open)."""
        depth = max(0, self.previousBlockState())
        start = 0 if depth else None
        i = 0
        while i < len(text):
            if depth == 0:
                m = re.compile(r"\$noop\(").search(text, i)
                if not m or escaped(text, m.start()):
                    if not m:
                        break
                    i = m.end()
                    continue
                start, depth, i = m.start(), 1, m.end()
                continue
            c = text[i]
            if c in "()" and not escaped(text, i):
                depth += 1 if c == "(" else -1
                if depth == 0:
                    self.setFormat(start, i + 1 - start, self.formats["comment"])
                    start = None
            i += 1
        if depth and start is not None:
            self.setFormat(start, len(text) - start, self.formats["comment"])
        self.setCurrentBlockState(depth)


class ScriptEdit(QPlainTextEdit):
    """A monospace script field: completion of $functions and %variables%, the matching bracket
    marked, and an error underlined where it is."""

    def __init__(self, names=(), variables_=()):
        super().__init__()
        self.setFont(style.mono())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(True)                    # (Tab moves on, as everywhere; scripts have no tabs)
        self.setAccessibleName(tr("Script"))
        self.function_names = sorted(names)
        self.variable_names = list(variables_)
        self.highlighter = ScriptHighlighter(self.document(), names)
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.popup().setAccessibleName(tr("Suggestions"))
        self.completer.activated.connect(self.insert_completion)
        self.error_at = None                             # (index) of the error shown
        self.cursorPositionChanged.connect(self.mark)

    # ---- completion ----------------------------------------------------
    def word_before(self):
        """("$" or "%", what is typed after it) when the cursor ends a function or variable name
        being typed; None otherwise."""
        c = self.textCursor()
        block = c.block().text()[:c.positionInBlock()]
        m = re.search(r"([$%])([\w:]*)$", block)
        if not m or escaped(block, m.start()):
            return None
        if m.group(1) == "%":
            opens = sum(1 for i, ch in enumerate(block[:m.start() + 1]) if ch == "%" and not escaped(block, i))
            if opens % 2 == 0:                           # (that % closes a variable)
                return None
        elif ":" in m.group(2):
            return None
        return m.group(1), m.group(2)

    def suggest(self, force=False):
        w = self.word_before()
        popup = self.completer.popup()
        if w is None or (not w[1] and not force):
            popup.hide()
            return False
        sigil, typed = w
        words = [f"${n}(" for n in self.function_names] if sigil == "$" else [f"%{n}%" for n in self.variable_names]
        if self.model.stringList() != words:
            self.model.setStringList(words)
        self.completer.setCompletionPrefix(sigil + typed)
        if self.completer.completionCount() == 0 or \
                (self.completer.completionCount() == 1 and self.completer.currentCompletion() == sigil + typed):
            popup.hide()
            return False
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        r = self.cursorRect()
        r.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 16)
        self.completer.complete(r)
        return True

    def insert_completion(self, word):
        w = self.word_before()
        typed = (w[0] + w[1]) if w else ""
        c = self.textCursor()
        c.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(typed))
        rest = self.toPlainText()[c.selectionEnd():]
        if word.startswith("%") and rest.startswith("%"):
            word = word[:-1]                             # (the closing % is there already)
        c.insertText(word)
        self.setTextCursor(c)

    def keyPressEvent(self, e):
        popup = self.completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            idx = popup.currentIndex()
            word = idx.data() if idx.isValid() else self.completer.currentCompletion()
            popup.hide()
            if word:
                self.insert_completion(word)
            return
        if popup.isVisible() and e.key() == Qt.Key_Escape:
            popup.hide()
            return
        if e.key() == Qt.Key_Space and e.modifiers() & Qt.ControlModifier:
            self.suggest(force=True)
            return
        super().keyPressEvent(e)
        if (e.text() and e.text().isprintable()) or e.key() == Qt.Key_Backspace:
            self.suggest()

    # ---- brackets and errors -------------------------------------------
    def show_error(self, index):
        self.error_at = index
        self.mark()

    def mark(self):
        text = self.toPlainText()
        pos = self.textCursor().position()
        sels = []
        for at in (pos, pos - 1):
            if 0 <= at < len(text) and text[at] in "()" and not escaped(text, at):
                other = match_bracket(text, at)
                for i in ((at, other) if other is not None else (at,)):
                    s = QTextEdit.ExtraSelection()
                    s.cursor = QTextCursor(self.document())
                    s.cursor.setPosition(i)
                    s.cursor.setPosition(i + 1, QTextCursor.KeepAnchor)
                    colour = QColor(style.color("accent" if other is not None else "error"))
                    colour.setAlpha(60)
                    s.format.setBackground(colour)
                    sels.append(s)
                break
        if self.error_at is not None:
            s = QTextEdit.ExtraSelection()
            s.cursor = QTextCursor(self.document())
            i = min(self.error_at, len(text))
            s.cursor.setPosition(max(0, i - 1) if i >= len(text) and text else i)
            s.cursor.setPosition(min(len(text), s.cursor.position() + 1), QTextCursor.KeepAnchor)
            s.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            s.format.setUnderlineColor(QColor(style.color("error")))
            s.format.setProperty(QTextFormat.FullWidthSelection, False)
            sels.append(s)
        self.setExtraSelections(sels)

    def refresh_colours(self):
        self.highlighter.refresh()
        self.mark()


class ScriptEditor(QDialog):
    """The profile's naming script and tagger scripts, with help, completion and a live preview.
    A window-modal sheet: open() it (or use open_editor). album: a plan entry whose planned tags
    the preview runs on (the sample track when None)."""

    IDLE_CHECK = 50                                      # (ms between looks at a closed editor's preview)
    DEBOUNCE = 300                                       # (ms after the last key before the preview runs)
    TITLE = N_("Script Editor")                           # (the menu command's name: Tools > Script Editor…)
    NARROW = 780                                         # a sheet narrower: the preview under the script
    LIST_W, SCRIPT_MIN, SIDE_W = 160, 320, (260, 360)    # the scripts list; the script's narrowest; the side's

    def __init__(self, parent, profile, album=None):
        super().__init__(parent.window() if isinstance(parent, QWidget) else None)
        sheets.prepare(self, tr(self.TITLE))
        self.profile_name = profile if isinstance(profile, str) else getattr(profile, "name", "default")
        self.album = album
        self.docs = functions()
        self.files = planned_files(album)
        self.items = []
        self.current = -1
        self.wrap = {NAMING: True, TAGGER: False}          # (Wrap Lines, by kind of script)
        self.loaded = None
        self.dirty = False
        self.generation = 0
        self.result = None
        self.setAccessibleName(tr(self.TITLE))
        self.resize(1040, 640)
        self.setMinimumSize(720, 480)
        self._build()
        self.load()

    # ---- layout ----------------------------------------------------------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["m"])
        self.head = heading(f"{tr(self.TITLE)}: “{self.profile_name}”",
                            tr("The file naming script names and places each file; tagger scripts change the "
                               "tags first. Changes reach your music only through a new preview you apply."))
        lay.addLayout(self.head)
        # the scripts list | the script and the preview (side by side; under it when narrow)
        split = self.split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        self.work = QSplitter(Qt.Horizontal)
        self.work.setChildrenCollapsible(False)
        for sp in (split, self.work):                      # (a 1 px hairline, as the window's: style.py)
            sp.setObjectName("scriptSplit")
            sp.setHandleWidth(1)
        self._fitted = None                               # (the orientation the panes were last fitted for)

        # scripts list
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(style.SPACE["s"])
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Scripts"))
        self.list.setMinimumWidth(140)
        self.list.currentRowChanged.connect(self.choose)
        lv.addWidget(self.list)                           # (as tall as its scripts: fit_list)
        row = QHBoxLayout()
        row.setSpacing(style.SPACE["xs"])
        self.add_btn = self._tool("plus", tr("Add tagger script (Ctrl+N)"), self.add_script)
        self.remove_btn = self._tool("minus", tr("Remove tagger script (Delete)"), self.remove_script)
        row.addWidget(self.add_btn)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        lv.addLayout(row)
        self.run_box = QCheckBox(tr("Run tagger scripts"))
        self.run_box.setAccessibleName(tr("Run tagger scripts"))
        self.run_box.toggled.connect(self.edited)
        lv.addWidget(self.run_box)
        self.source_note = QLabel("")
        self.source_note.setProperty("role", "caption")
        self.source_note.setWordWrap(True)
        lv.addWidget(self.source_note)
        lv.addStretch(1)
        left.setMinimumWidth(140)
        split.addWidget(left)

        # the script
        mid = QWidget()
        mid.setMinimumWidth(self.SCRIPT_MIN)                  # (the script is what this sheet is for)
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(style.SPACE["s"])
        nrow = QHBoxLayout()
        self.name_head = QLabel("")                         # (the naming script: its fixed name, a headline)
        self.name_head.setProperty("role", "headline")
        nrow.addWidget(self.name_head, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setAccessibleName(tr("Script name"))
        self.name_edit.setPlaceholderText(tr("Script name"))
        self.name_edit.textEdited.connect(self.renamed)
        self.enabled_box = QCheckBox(tr("On"))
        self.enabled_box.setAccessibleName(tr("Script on"))
        self.enabled_box.setToolTip(tr("Run this tagger script"))
        self.enabled_box.toggled.connect(self.toggled)
        nrow.addWidget(self.name_edit, 1)
        nrow.addWidget(self.enabled_box)
        mv.addLayout(nrow)
        self.edit = ScriptEdit(self.docs, variables(list(self.files[0][2]) if self.files else ()))
        self.edit.textChanged.connect(self.text_changed)
        self.edit.cursorPositionChanged.connect(self.show_help)
        mv.addWidget(self.edit, 1)
        erow = QHBoxLayout()
        erow.setSpacing(style.SPACE["s"])
        self.error_icon = QLabel()
        self.error_icon.setFixedSize(16, 16)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName(tr("Script error"))
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        erow.addWidget(self.error_icon, 0, Qt.AlignTop)
        erow.addWidget(self.error_label, 1)
        mv.addLayout(erow)
        crow = QHBoxLayout()
        self.cursor_label = QLabel("")
        self.cursor_label.setProperty("role", "caption")
        crow.addWidget(self.cursor_label, 1)
        self.wrap_box = QCheckBox(tr("Wrap Lines"))             # (on for the naming script, one long line)
        self.wrap_box.setAccessibleName(tr("Wrap lines"))
        self.wrap_box.setToolTip(tr("Wrap long lines to the editor's width"))
        self.wrap_box.toggled.connect(self.wrap_toggled)
        crow.addWidget(self.wrap_box)
        mv.addLayout(crow)
        self.work.addWidget(mid)

        # help and preview
        self.side = QTabWidget()
        self.side.setDocumentMode(True)
        self.side.setAccessibleName(tr("Help and preview"))
        prev = QWidget()
        pv = QVBoxLayout(prev)
        pv.setContentsMargins(0, style.SPACE["s"], 0, 0)
        pv.setSpacing(style.SPACE["s"])
        self.file_box = QComboBox()
        self.file_box.setAccessibleName(tr("Preview file"))
        for label, path, _t in self.files:
            self.file_box.addItem(label, path)
        self.file_box.currentIndexChanged.connect(lambda _i: self.schedule(0))
        pv.addWidget(self.file_box)
        cap = QLabel(tr("File name"))
        cap.setProperty("role", "headline")
        pv.addWidget(cap)
        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        self.name_label.setFont(style.mono())
        self.name_label.setAccessibleName(tr("New file name"))
        self.name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pv.addWidget(self.name_label)
        cap2 = QLabel(tr("Tags the tagger scripts change"))
        cap2.setProperty("role", "headline")
        pv.addWidget(cap2)
        panel = QFrame()                                  # (framed alike in light and dark, rows or none)
        panel.setProperty("role", "panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(1, 1, 1, 1)
        self.tag_stack = QStackedWidget()
        pl.addWidget(self.tag_stack)
        self.tags = QTableWidget(0, 3)
        self.tags.setHorizontalHeaderLabels([tr("Tag"), tr("Planned"), tr("After scripts")])
        self.tags.setAccessibleName(tr("Tags the scripts change"))
        self.tags.setShowGrid(False)
        self.tags.setAlternatingRowColors(True)
        self.tags.verticalHeader().hide()
        self.tags.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tags.setSelectionBehavior(QAbstractItemView.SelectRows)
        left_header(self.tags)
        for c in (1, 2):
            self.tags.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch)
        self.tag_stack.addWidget(self.tags)
        self.no_tags = EmptyState("tag", tr("Tagger scripts are off"), "")
        self.tag_stack.addWidget(self.no_tags)
        pv.addWidget(panel, 1)
        self.preview_note = QLabel("")
        self.preview_note.setProperty("role", "caption")
        self.preview_note.setWordWrap(True)
        pv.addWidget(self.preview_note)
        self.help = QTextBrowser()
        self.help.setAccessibleName(tr("Function help"))
        self.help.setOpenExternalLinks(False)
        self.side.addTab(prev, icons.icon("eye"), tr("Preview"))
        self.side.addTab(self.help, icons.icon("info"), tr("Function Help"))
        self.side.setTabToolTip(0, tr("Preview (Ctrl+1)"))
        self.side.setTabToolTip(1, tr("Function Help (Ctrl+2)"))
        self.side.setMinimumWidth(self.SIDE_W[0])
        self.work.addWidget(self.side)
        self.work.setStretchFactor(0, 1)
        split.addWidget(self.work)
        split.setStretchFactor(1, 1)
        # keys: Ctrl+N adds a tagger script, Delete (in the list) removes one, Ctrl+1 / Ctrl+2 the side tabs
        QShortcut(QKeySequence(QKeySequence.New), self, activated=self.add_script)
        rm = QShortcut(QKeySequence(QKeySequence.Delete), self.list, activated=self.remove_script)
        rm.setContext(Qt.WidgetShortcut)
        for i in range(2):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.side.setCurrentIndex(i))

        brow, self.save_btn = sheets.button_row(self, tr("Save Scripts"), self.save)
        lay.addLayout(brow)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.run)

    def fit_panes(self, force=False):
        """The panes for the sheet's real width: the list LIST_W, the side pane at most SIDE_W
        and the rest the script's; under NARROW px the preview goes under the script. Redone
        when shown and when the layout flips (a width you dragged to is kept otherwise)."""
        narrow = self.width() < self.NARROW
        if not force and self._fitted == narrow:
            return
        self._fitted = narrow
        self.work.setOrientation(Qt.Vertical if narrow else Qt.Horizontal)
        self.side.setMinimumWidth(0 if narrow else self.SIDE_W[0])
        total = max(self.split.width(), self.width() - 2 * style.MARGIN)
        left = self.LIST_W
        self.split.setSizes([left, max(self.SCRIPT_MIN, total - left)])
        rest = max(self.SCRIPT_MIN, total - left - self.split.handleWidth())
        if narrow:
            h = max(2, self.work.height())
            self.work.setSizes([h // 2, h - h // 2])
        else:
            side = max(self.SIDE_W[0], min(self.SIDE_W[1], rest * 2 // 5))
            self.work.setSizes([rest - side, side])

    def sizeHint(self):
        """1040 x 640, or the window's room less a margin (sheets.place sizes a sheet to its hint:
        the content's own hint would squeeze the script)."""
        w, h = 1040, 640
        win = self.parentWidget()
        if win is not None:
            w, h = min(w, win.width() - 48), min(h, win.height() - 96)
        return QSize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))

    def adjustSize(self):
        """(sheets.place calls it) the hint as it is: Qt would cap a window at 2/3 of the screen."""
        self.resize(self.sizeHint())

    def showEvent(self, e):
        super().showEvent(e)
        self.fit_panes(force=True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible():
            self.fit_panes()

    def _tool(self, icon, name, fn):
        b = QToolButton()
        b.setIcon(icons.icon(icon))
        b.setProperty("role", "tool")
        b.setAccessibleName(name.split(" (")[0])
        b.setToolTip(name)
        b.clicked.connect(fn)
        return b

    # ---- the profile -----------------------------------------------------
    def load(self):
        """The profile's scripts into the editor (no file is written)."""
        self.can_save = self.profile_name != "default"
        try:
            prof = load_profile(self.profile_name)
            got = profile_scripts(prof)
        except Exception as exc:                          # (a profile that does not load: said, nothing to save)
            got = {"naming": "", "preset": "", "taggers": [], "run": False, "from_tagger": False, "editions": "off"}
            self.can_save = False
            self.source_note.setText(tr("This profile could not be read: {exc}").format(exc=exc))
        self.loaded = got
        self.items = [{"kind": NAMING, "name": tr("File naming script"), "enabled": True, "text": got["naming"]}]
        self.items += [{"kind": TAGGER, "name": n, "enabled": e, "text": t} for _p, n, e, t in got["taggers"]]
        self.run_box.blockSignals(True)
        self.run_box.setChecked(got["run"])
        self.run_box.blockSignals(False)
        if got["from_tagger"]:
            self.source_note.setText(tr("The tagger scripts come from your tagger settings. Saving keeps them in "
                                        "this profile from then on."))
        elif got["preset"] and self.can_save:
            self.source_note.setText(tr("The naming script is the profile's preset. Change it to give the "
                                        "profile its own."))
        if self.profile_name == "default":
            self.source_note.setText(tr("The default profile cannot be changed. Make a profile in Profiles "
                                        "(Ctrl+N) to keep scripts; this one is for trying them out."))
        self.fill_list(0)
        self.dirty = False
        self.save_btn.setEnabled(False)

    def fill_list(self, row):
        self.list.blockSignals(True)
        self.list.clear()
        for it in self.items:
            li = QListWidgetItem(icons.icon("folder-open" if it["kind"] == NAMING else "tag"), "")
            self.list.addItem(li)
            self.show_item(li, it)
        self.list.blockSignals(False)
        self.list.setCurrentRow(max(0, min(row, len(self.items) - 1)))
        self.fit_list()
        self.choose(self.list.currentRow())

    def fit_list(self):
        """The list as tall as its scripts (at least three rows): no tall empty box."""
        n = max(3, self.list.count())
        one = self.list.sizeHintForRow(0) if self.list.count() else self.list.fontMetrics().height() + 8
        self.list.setMaximumHeight(n * max(one, 1) + 2 * self.list.frameWidth() + 8)

    def wrap_toggled(self, on):
        if 0 <= self.current < len(self.items):
            self.wrap[self.items[self.current]["kind"]] = bool(on)
        self.edit.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    @staticmethod
    def show_item(li, it):
        """A script in the list: its name, and "(off)" in words for a tagger script turned off
        (muted too, and read out as "Name, off")."""
        off = it["kind"] == TAGGER and not it["enabled"]
        name = it["name"] or tr("Untitled script")
        li.setText(tr("{name} (off)").format(name=name) if off else name)
        li.setData(Qt.AccessibleTextRole, f"{name}, off" if off else name)
        li.setForeground(QColor(style.color("muted" if off else "ink")))
        li.setToolTip(tr("Off: this script does not run") if off else "")

    def choose(self, row):
        if not (0 <= row < len(self.items)):
            return
        self.current = row
        it = self.items[row]
        tagger = it["kind"] == TAGGER
        self.name_edit.blockSignals(True)
        self.name_edit.setText(it["name"])
        self.name_edit.setCursorPosition(0)              # (its start in view, never scrolled away)
        self.name_edit.setMinimumWidth(min(260, self.name_edit.fontMetrics().horizontalAdvance(it["name"]) + 24))
        self.name_edit.blockSignals(False)
        self.name_edit.setVisible(tagger)                 # (the naming script has its fixed name: a headline)
        self.name_head.setText(it["name"])
        self.name_head.setVisible(not tagger)
        self.enabled_box.blockSignals(True)
        self.enabled_box.setChecked(it["enabled"])
        self.enabled_box.blockSignals(False)
        self.enabled_box.setVisible(tagger)
        self.remove_btn.setEnabled(tagger)
        self.edit.blockSignals(True)
        self.edit.setPlainText(it["text"])
        self.edit.blockSignals(False)
        self.edit.setAccessibleName(it["name"])
        self.wrap_box.blockSignals(True)
        self.wrap_box.setChecked(self.wrap[it["kind"]])
        self.wrap_box.blockSignals(False)
        self.wrap_toggled(self.wrap[it["kind"]])
        self.show_problem()
        self.show_help()
        self.schedule(0)

    def add_script(self):
        n = sum(1 for it in self.items if it["kind"] == TAGGER) + 1
        self.items.append({"kind": TAGGER, "name": f"Script {n}", "enabled": True, "text": ""})
        self.edited()
        self.fill_list(len(self.items) - 1)
        self.edit.setFocus()

    def remove_script(self):
        """Remove the tagger script shown; one with text asks first (Cancel on the editor puts
        it back too: nothing is saved until Save Scripts)."""
        if not (0 <= self.current < len(self.items)) or self.items[self.current]["kind"] != TAGGER:
            return
        it = self.items[self.current]
        if it["text"].strip() and not sheets.confirm(
                self, tr("Remove “{0}”?").format(it['name']), tr("Its script text is removed from the list."),
                tr("Cancel on the editor keeps the profile's scripts as they were; nothing is saved until "
                   "Save Scripts."), tr("Remove Script"), destructive=True):
            return
        del self.items[self.current]
        self.edited()
        self.fill_list(self.current)

    def renamed(self, text):
        if 0 <= self.current < len(self.items):
            self.items[self.current]["name"] = text
            self.show_item(self.list.item(self.current), self.items[self.current])
            self.edited()

    def toggled(self, on):
        if 0 <= self.current < len(self.items):
            self.items[self.current]["enabled"] = on
            self.show_item(self.list.item(self.current), self.items[self.current])
            self.edited()

    def text_changed(self):
        if 0 <= self.current < len(self.items):
            text = self.edit.toPlainText()
            if text == self.items[self.current]["text"]:   # (colours redone: no edit)
                return
            self.items[self.current]["text"] = text
            self.edit.error_at = None
            self.edited()

    def edited(self, *_):
        self.dirty = True
        self.save_btn.setEnabled(self.can_save)
        self.schedule()

    def changes(self) -> dict:
        """What saving writes into the profile: {"naming": {...}, "tagger": {...}} (only what the
        editor changed, so a profile that follows Picard's tagger scripts keeps following them)."""
        got = self.loaded or {}
        out = {}
        naming_text = self.items[0]["text"]
        if naming_text != got.get("naming", ""):
            out["template"] = "" if naming_text.strip() == (got.get("preset") or "").strip() else naming_text
        taggers = [[i, it["name"], bool(it["enabled"]), it["text"]]
                   for i, it in enumerate(x for x in self.items if x["kind"] == TAGGER)]
        if taggers != [list(t) for t in got.get("taggers") or []]:
            out["list_of_scripts"] = taggers
        if self.run_box.isChecked() != got.get("run"):
            out["enable_tagger_scripts"] = self.run_box.isChecked()
        return out

    def save(self) -> bool:
        """Write the scripts into the profile file (True when saved). A script that does not parse
        is named and nothing is saved."""
        if not self.can_save:
            return False
        for i, it in enumerate(self.items):
            p = check(it["text"]) if it["text"].strip() else None
            if p:
                self.fill_list(i)
                self.show_problem(p)
                sheets.warning(self, tr("“{0}” has an error, so nothing was saved.").format(it['name']),
                               tr("Line {0}, column {1}: {2}").format(p[1], p[2] + 1, p[3]))
                return False
        ch = self.changes()
        if not ch:
            self.dirty = False
            self.accept()
            return True
        try:
            data = pf.rename_legacy(pf.raw(self.profile_name))     # (older setting names in today's form)
            if "template" in ch:
                data.setdefault("naming", {})["template"] = ch["template"]
            pic = dict(data.get("tagger") or {})
            for k in ("list_of_scripts", "enable_tagger_scripts"):
                if k in ch:
                    pic[k] = ch[k]
            data["tagger"] = pic
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The scripts were not saved to “{profile_name}”.")
                           .format(profile_name=self.profile_name), str(exc))
            return False
        except OSError as exc:
            sheets.warning(self, tr("The scripts were not saved to “{profile_name}”.")
                           .format(profile_name=self.profile_name), str(exc))
            return False
        self.dirty = False
        win = self.parentWidget()
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(tr("Saved the scripts of {profile_name}")
                                        .format(profile_name=self.profile_name), 4000)
        self.accept()
        return True

    def reject(self):
        if self.dirty and not sheets.question(self, tr("Discard your script changes?"),
                                              tr("The profile keeps the scripts it had."),
                                              action=tr("Discard Changes")):
            return
        self.dirty = False
        super().reject()

    # ---- help ----------------------------------------------------------------
    def show_help(self):
        c = self.edit.textCursor()
        self.cursor_label.setText(tr("Line {0}, column {1}").format(c.blockNumber() + 1, c.positionInBlock() + 1))
        name = function_at(self.edit.toPlainText(), c.position())
        self.help_for(name)

    def help_for(self, name):
        if name in self.docs:
            sig, doc = self.docs[name]
            self.help.setMarkdown(f"**`{sig}`**\n\n{doc}")
        elif name:
            self.help.setMarkdown(tr("There is no function named `${name}`.").format(name=name))
        else:
            self.help.setMarkdown(tr("Put the cursor on a function to read what it does. Type `$` for functions "
                                     "or `%` for variables, or press Ctrl+Space, to see the names."))
        self.help_name = name

    # ---- preview and errors -----------------------------------------------------
    def schedule(self, delay=None):
        self.timer.start(self.DEBOUNCE if delay is None else delay)

    def run(self):
        """The preview, on a worker; only the newest run's answer is shown."""
        self.generation += 1
        gen = self.generation
        idx = max(0, self.file_box.currentIndex())
        _label, path, tags = self.files[idx]
        items = [dict(it) for it in self.items]
        run_taggers = self.run_box.isChecked()
        name = self.profile_name
        editions = (self.loaded or {}).get("editions", "off")

        def work(_w):                                    # (the profile and Picard's settings read here too)
            import naming
            try:
                prof = load_profile(name)
                settings, compat = prof.picard_tags(), prof["filenames"].get("compat", "windows")
            except Exception:
                prof, settings, compat = None, {}, "linux"
            try:                                         # (saving an empty script keeps the preset)
                fallback = naming.effective_template({**((prof.data.get("naming") if prof else None) or {}),
                                                      "template": ""},
                                                     prof.data.get("filenames") if prof else None, editions=False)
            except naming.TemplateError:
                fallback = ""
            return run_preview(items, run_taggers, tags, path, settings, compat, editions, fallback)
        self.preview_note.setText(tr("Working…"))
        start(self, work,
              lambda out, g=gen: self.shown(g, out),
              failed=lambda msg, g=gen: self.shown(g, {"failed": msg}))

    def shown(self, gen, out):
        if gen != self.generation:
            return
        self.result = out
        if out.get("failed"):
            self.preview_note.setText(tr("The preview did not work: ") + out["failed"].split("\n")[0])
            return
        self.name_label.setText(out["name"] or "—")
        rows = []
        before, after = out["before"], out["after"]
        for k in sorted(set(before) | set(after), key=str.casefold):
            b, a = before.get(k) or [], after.get(k) or []
            if b != a:
                rows.append((k, "; ".join(b), "; ".join(a) if a else "(removed)"))
        self.tags.setRowCount(len(rows))
        for r, (k, b, a) in enumerate(rows):
            it = QTableWidgetItem(tag_name(k))
            it.setToolTip(k)
            self.tags.setItem(r, 0, it)
            self.tags.setItem(r, 1, QTableWidgetItem(b))
            self.tags.setItem(r, 2, QTableWidgetItem(a))
        on = tr("on the album's planned tags") if self.album and (self.album.get("files") or []) else tr("on a sample "
                                                                                                         "track")
        n = len(out["errors"])
        self.preview_note.setText(f"Run {on}." + (tr(" {n} script{0} with an error.").format('s' if n != 1 else '',
                                                                                             n=n) if n else ""))
        self.show_no_tags(rows)
        self.show_problem()

    def show_no_tags(self, rows):
        """The table, or in its place why it has no rows (the scripts off, none, or no change)."""
        taggers = [it for it in self.items if it["kind"] == TAGGER]
        if rows:
            self.tag_stack.setCurrentWidget(self.tags)
            return
        if not taggers:
            self.no_tags.set_text(tr("No tagger scripts"), tr("Add one with + (Ctrl+N) to change tags before the "
                                                       "files are named."))
        elif not self.run_box.isChecked() or not any(it["enabled"] and it["text"].strip() for it in taggers):
            self.no_tags.set_text(tr("Tagger scripts are off"), tr("Tick Run tagger scripts, and turn a script on, to "
                                                            "see what it changes."))
        else:
            self.no_tags.set_text(tr("These scripts change no tag"),
                                  tr("Every planned tag of this file stays as it is."))
        self.tag_stack.setCurrentWidget(self.no_tags)

    def show_problem(self, p=None):
        """The error of the script shown, under it and underlined in it (none: cleared)."""
        if p is None and self.result:
            p = self.result.get("errors", {}).get(self.current)
        if p is None:
            self.error_icon.clear()
            self.error_label.setText("")
            self.edit.show_error(None)
            return
        idx, line, col, msg = p
        self.error_icon.setPixmap(icons.icon("circle-x", "error", 16).pixmap(16, 16))
        self.error_label.setText(tr("Line {line}, column {0}: {msg}").format(col + 1, line=line,
                                                                             msg=msg) if idx is not None else msg)
        self.edit.show_error(idx)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.PaletteChange, e.Type.StyleChange) and hasattr(self, "edit"):
            self.edit.refresh_colours()                  # (light and dark: the colours follow)

    def done(self, r):
        """Closed (saved, cancelled or Esc), never waiting for a preview still running (it runs
        the user's script: the window would hang). Its answer is dropped (the generation moves
        on); a dialog deleted on close is deleted once its last preview has ended, since Qt stops
        the app when a running thread is deleted."""
        self.timer.stop()
        self.generation += 1                             # (an answer still to come: not shown)
        if self.testAttribute(Qt.WA_DeleteOnClose) and self._previewing():
            self.setAttribute(Qt.WA_DeleteOnClose, False)
            QTimer.singleShot(self.IDLE_CHECK, self._delete_when_idle)
        super().done(r)

    def _previewing(self) -> bool:
        return any(_alive(th) for th, _w in getattr(self, "_threads", []))

    def _delete_when_idle(self):
        """(a closed editor) deleted once no preview of it is running any more"""
        if self._previewing():
            QTimer.singleShot(self.IDLE_CHECK, self._delete_when_idle)
        else:
            self.deleteLater()

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Save):
            if not self.can_save:                          # (Ctrl+S answers, never silence)
                sheets.information(self, tr("These scripts cannot be saved."), self.source_note.text() or
                                   tr("The default profile cannot be changed. Make a profile in Profiles (Ctrl+N) "
                                      "to keep scripts."))
                return
            self.save()
            return
        super().keyPressEvent(e)


def open_editor(parent, profile, album=None) -> ScriptEditor:
    """The script editor for a profile (a name), as a sheet of parent's window, opened without
    blocking. album: the selected plan entry, for the preview."""
    dlg = ScriptEditor(parent, profile, album)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    sheets.place(dlg)
    sheets.fade(dlg)
    dlg.open()
    return dlg
