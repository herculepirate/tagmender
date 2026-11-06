# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Log file setup with fixed/variable retention, redaction and correlation IDs (design section 6.6).

Pure Python (standard library only). Picard integration (forwarding warnings to Picard's own log and
choosing the folder under Picard's cache directory) is done by the caller.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import logging.handlers
import queue
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

LOGGER_NAME = "coverup"
LOG_NAME = "coverup.log"

MODE_FIXED = "fixed"  # rotating files: max_bytes each, `backups` kept (default 2 MB x 3 backups)
MODE_TOTAL = "total"  # variable size, capped by a total size across files
MODE_AGE = "age"  # variable size, files kept for `retain_days`, with a hard ceiling
MODE_SESSION = "session"  # one timestamped file per session, keep the last `sessions_keep`
MODE_OFF = "off"
MODES = (MODE_FIXED, MODE_TOTAL, MODE_AGE, MODE_SESSION, MODE_OFF)

MB = 1024 * 1024

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("coverup_run", default="-")
_album_id: contextvars.ContextVar[str] = contextvars.ContextVar("coverup_album", default="-")


@contextlib.contextmanager
def log_context(run: str | None = None, album: str | None = None) -> Iterator[None]:
    """Attach a run ID and/or album ID to every record logged inside the block (and its threads/tasks)."""
    tokens = []
    if run is not None:
        tokens.append((_run_id, _run_id.set(run)))
    if album is not None:
        tokens.append((_album_id, _album_id.set(album)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


@dataclass
class LogSettings:
    folder: Path
    mode: str = MODE_FIXED
    max_bytes: int = 2 * MB
    backups: int = 3
    total_cap_bytes: int = 25 * MB
    retain_days: int = 14
    safety_ceiling_bytes: int = 100 * MB
    sessions_keep: int = 5
    level: str = "INFO"
    verbose_until_restart: bool = False
    anonymise: bool = False
    sensitive_strings: tuple[str, ...] = ()  # e.g. artist/album text to hash when anonymising

    def effective_level(self) -> int:
        if self.verbose_until_restart:
            return logging.DEBUG
        return level_from_name(self.level)


def level_from_name(name: str) -> int:
    name = (name or "INFO").upper()
    if name == "TRACE":
        return TRACE
    return getattr(logging, name, logging.INFO) if isinstance(getattr(logging, name, None), int) else logging.INFO


# ---- redaction ---------------------------------------------------------------------------------------

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|token|basic)?\s*[^\s,;'\"]+"),
    re.compile(r"(?i)((?:api[_-]?key|apikey|client[_-]?secret|access[_-]?token|token|secret|password)\s*[=:]\s*)[^\s&,;'\"]+"),
    re.compile(r"(?i)([?&](?:api[_-]?key|apikey|key|token|access_token|client_secret)=)[^&\s]+"),
]
REDACTED = "***"


class RedactingFilter(logging.Filter):
    """Masks configured secrets exactly and common key/token patterns in every record."""

    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets: list[str] = []
        self.set_secrets(secrets)

    def set_secrets(self, secrets: Iterable[str]) -> None:
        # longest first so a secret that contains another is masked fully
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        for pat in _PATTERNS:
            text = pat.sub(lambda m: m.group(1) + (REDACTED if m.lastindex == 1 else REDACTED), text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # malformed format args must never break logging
            msg = str(record.msg)
        record.msg = self.redact(msg)
        record.args = ()
        _freeze_exception_text(record)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


def _freeze_exception_text(record: logging.LogRecord) -> None:
    """Render exc_info into exc_text now, so filters can clean it before the queue handler formats it."""
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)


class AnonymiseFilter(logging.Filter):
    """Replaces the home directory and listed sensitive strings with stable short hashes."""

    def __init__(self, sensitive: Iterable[str] = ()):
        super().__init__()
        self._home = str(Path.home())
        self._sensitive = sorted({s for s in sensitive if s and len(s) >= 2}, key=len, reverse=True)

    @staticmethod
    def _h(s: str) -> str:
        return "anon-" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]

    def anonymise(self, text: str) -> str:
        if self._home and self._home != "/":
            text = text.replace(self._home, "<HOME>")
        for s in self._sensitive:
            text = text.replace(s, self._h(s))
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.anonymise(record.getMessage())
        record.args = ()
        _freeze_exception_text(record)
        if record.exc_text:
            record.exc_text = self.anonymise(record.exc_text)
        return True


class CollapseRepeatsFilter(logging.Filter):
    """Collapses runs of identical messages: later duplicates are dropped and counted."""

    def __init__(self) -> None:
        super().__init__()
        self._last: tuple[int, str] | None = None
        self._count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.levelno, record.getMessage())
        if self._last == key:
            self._count += 1
            return False
        if self._count:
            record.msg = f"(previous message repeated {self._count} more times) {record.getMessage()}"
            record.args = ()
        self._last = key
        self._count = 0
        return True


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run = _run_id.get()
        record.album = _album_id.get()
        return True


class _CallbackHandler(logging.Handler):
    """Forwards records at or above its level to a callable (used for Picard's own log)."""

    def __init__(self, callback: Callable[[int, str], None], level: int):
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(record.levelno, self.format(record))
        except Exception:
            pass  # never let the sink break the plugin


# ---- retention helpers -------------------------------------------------------------------------------

def _log_files(folder: Path, pattern: str = "coverup*.log*") -> list[Path]:
    try:
        files = [p for p in folder.glob(pattern) if p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime)


def prune_by_age(folder: Path, retain_days: int, ceiling_bytes: int, keep: Path | None = None) -> list[Path]:
    """Delete logs older than ``retain_days`` and then oldest-first until under ``ceiling_bytes``."""
    removed: list[Path] = []
    cutoff = time.time() - retain_days * 86400
    files = _log_files(folder)
    for p in list(files):
        if p != keep and p.stat().st_mtime < cutoff:
            _unlink(p, removed)
            files.remove(p)
    total = sum(p.stat().st_size for p in files)
    for p in list(files):
        if total <= ceiling_bytes:
            break
        if p == keep:
            continue
        total -= p.stat().st_size
        _unlink(p, removed)
    return removed


def prune_sessions(folder: Path, keep_n: int, current: Path | None = None) -> list[Path]:
    removed: list[Path] = []
    # ``current`` is the session being started: it counts toward ``keep_n`` even if it does not exist yet
    files = [p for p in _log_files(folder, "coverup-*.log") if p != current]
    allowed_old = max(0, max(1, keep_n) - 1)
    excess = len(files) - allowed_old
    for p in files:
        if excess <= 0:
            break
        _unlink(p, removed)
        excess -= 1
    return removed


def _unlink(p: Path, removed: list[Path]) -> None:
    try:
        p.unlink()
        removed.append(p)
    except OSError:
        pass


# ---- setup -------------------------------------------------------------------------------------------

@dataclass
class LogHandle:
    logger: logging.Logger
    settings: LogSettings
    path: Path | None = None
    enabled: bool = True
    warnings: list[str] = field(default_factory=list)
    _listener: logging.handlers.QueueListener | None = None
    _redactor: RedactingFilter | None = None
    _file_handler: logging.Handler | None = None

    def set_level(self, level: str | int) -> None:
        lvl = level if isinstance(level, int) else level_from_name(level)
        self.logger.setLevel(lvl)

    def set_secrets(self, secrets: Iterable[str]) -> None:
        if self._redactor:
            self._redactor.set_secrets(secrets)

    def close(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        for h in list(self.logger.handlers):
            self.logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        if self._file_handler:
            try:
                self._file_handler.close()
            except Exception:
                pass


_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [run=%(run)s album=%(album)s] %(message)s"


def _formatter() -> logging.Formatter:
    fmt = logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S%z")
    fmt.converter = time.localtime
    return fmt


def setup_logging(
    settings: LogSettings,
    secrets: Iterable[str] = (),
    picard_sink: Callable[[int, str], None] | None = None,
    name: str = LOGGER_NAME,
) -> LogHandle:
    """Configure the ``coverup`` logger according to ``settings`` and return a handle.

    File problems (unwritable folder, full disk) disable file logging and record one warning in
    ``handle.warnings``; they never raise. WARNING and above are forwarded to ``picard_sink``.
    """
    logger = logging.getLogger(name)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = False
    logger.setLevel(settings.effective_level())
    handle = LogHandle(logger=logger, settings=settings)

    redactor = RedactingFilter(secrets)
    handle._redactor = redactor
    fmt = _formatter()
    handlers: list[logging.Handler] = []

    if picard_sink is not None:
        cb = _CallbackHandler(picard_sink, logging.WARNING)
        cb.setFormatter(logging.Formatter("CoverUp: %(message)s"))
        handlers.append(cb)

    if settings.mode != MODE_OFF:
        try:
            settings.folder.mkdir(parents=True, exist_ok=True)
            fh = _make_file_handler(settings)
            fh.setFormatter(fmt)
            handle._file_handler = fh
            handle.path = Path(fh.baseFilename)  # type: ignore[attr-defined]
            handlers.append(fh)
        except OSError as exc:
            handle.enabled = False
            handle.warnings.append(f"File logging disabled: cannot use {settings.folder} ({exc})")

    q: queue.Queue = queue.Queue(-1)
    qh = logging.handlers.QueueHandler(q)
    # filters run on the producer side so the context vars of the calling thread are captured
    qh.addFilter(ContextFilter())
    if settings.anonymise:
        qh.addFilter(AnonymiseFilter(settings.sensitive_strings))
    qh.addFilter(redactor)
    qh.addFilter(CollapseRepeatsFilter())
    logger.addHandler(qh)
    listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=True)
    listener.start()
    handle._listener = listener

    for w in handle.warnings:
        logger.warning(w)
    return handle


def _make_file_handler(settings: LogSettings) -> logging.Handler:
    folder = settings.folder
    mode = settings.mode
    if mode == MODE_SESSION:
        path = folder / f"coverup-{time.strftime('%Y%m%d-%H%M%S')}.log"
        prune_sessions(folder, settings.sessions_keep, current=path)
        return _SafeRotatingHandler(str(path), maxBytes=0, backupCount=0)
    path = folder / LOG_NAME
    if mode == MODE_FIXED:
        return _SafeRotatingHandler(str(path), maxBytes=settings.max_bytes, backupCount=settings.backups)
    if mode == MODE_TOTAL:
        per_file = max(64 * 1024, settings.max_bytes)
        backups = max(1, settings.total_cap_bytes // per_file - 1)
        return _SafeRotatingHandler(str(path), maxBytes=per_file, backupCount=backups)
    if mode == MODE_AGE:
        prune_by_age(folder, settings.retain_days, settings.safety_ceiling_bytes, keep=path)
        per_file = max(64 * 1024, settings.max_bytes)
        backups = max(1, settings.safety_ceiling_bytes // per_file - 1)
        return _SafeRotatingHandler(str(path), maxBytes=per_file, backupCount=backups)
    raise ValueError(f"Unknown log mode {mode!r}")


class _SafeRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that survives rotation failures (e.g. Windows file locks) and full disks."""

    def __init__(self, filename: str, maxBytes: int, backupCount: int):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, encoding="utf-8", delay=False)
        self._disabled = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._disabled:
            return
        try:
            super().emit(record)
            if record.levelno >= logging.ERROR:
                self.flush()
        except OSError:
            self._disabled = True  # one failure disables the file handler quietly

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            # could not rotate: truncate in place so the size limit still holds
            try:
                if self.stream:
                    self.stream.close()
                    self.stream = None  # type: ignore[assignment]
                with open(self.baseFilename, "w", encoding="utf-8"):
                    pass
                self.stream = self._open()
            except OSError:
                self._disabled = True
