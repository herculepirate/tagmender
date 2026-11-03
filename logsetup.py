"""logsetup.py - TagMender's log file: rotated, shared safely between processes, never
holding a secret.

  ~/.local/state/tagmender/logs/tagmender.log      the active file
  tagmender.log.1 .. tagmender.log.N                older files (N = 3 by default)

A file is rotated when the next line would take it past MAX_BYTES = 104,857,600 bytes
(100 MiB). Several TagMender processes (the watcher, a run from the terminal, the GUI)
may write at once: each line is written under an exclusive lock on tagmender.log.lock,
and a process notices when another one has rotated the file and reopens it, so no line
is lost or written into a rotated file.

Every line has a time, level, job ID, component and provider, e.g.
  2026-10-24 09:12:03,114 INFO  job=20261024-091200-ab12 match provider=discogs  ...

Tokens, keys, passwords and Authorization headers are replaced by *** before a line is
written (also inside URLs: ?token=..., &client=...).

Recovery journals and operation history are NOT kept here; they live in the state
database and job folders, which rotation never touches.
"""
from __future__ import annotations

import fcntl
import logging
import logging.handlers
import os
import re
import secrets
import time
from pathlib import Path

import exclusions as ex

MAX_BYTES = 104_857_600          # 100 MiB
BACKUPS = 3
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
FORMAT = "%(asctime)s %(levelname)-5s job=%(job)s %(component)s provider=%(provider)s  %(message)s"

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+(?:\s+\S+)?)"), r"\1***"),
    (re.compile(r"(?i)(discogs\s+token=)[^\s&\"']+"), r"\1***"),
    (re.compile(r"(?i)([?&](?:token|key|client|api_key|apikey|secret|password|passwd|auth|access_token)=)"
                r"[^&\s\"'#]+"), r"\1***"),
    (re.compile(r"(?i)\b((?:discogs_token|acoustid_key|token|api[_-]?key|key|client|secret|password|passwd)"
                r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"), r"\1***"),
]


def redact(text: str) -> str:
    s = str(text)
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def log_dir() -> Path:
    return ex.STATE_DIR / "logs"


class _Context(logging.Filter):
    """Fills in job, component and provider, and redacts secrets from the message and its
    arguments (the record is formatted here, so nothing unredacted reaches a handler)."""

    def __init__(self, job="-", component="tagmender"):
        super().__init__()
        self.job, self.component = job, component

    def filter(self, record):
        record.job = getattr(record, "job", None) or self.job
        record.component = getattr(record, "component", None) or self.component
        record.provider = getattr(record, "provider", None) or "-"
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg, record.args = redact(msg), None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


class SharedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that several processes can share: emit() and the rollover run
    under an flock, the size is taken from the file on disk (not this process's count),
    and the stream is reopened when another process has rotated it."""

    def __init__(self, filename, max_bytes=MAX_BYTES, backups=BACKUPS):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(filename, maxBytes=max_bytes, backupCount=max(3, backups), encoding="utf-8", delay=True)
        self._lock_path = str(filename) + ".lock"

    def _reopen_if_rotated(self):
        if self.stream is None:
            return
        try:
            same = os.fstat(self.stream.fileno()).st_ino == os.stat(self.baseFilename).st_ino
        except FileNotFoundError:
            same = False
        if not same:
            self.stream.close()
            self.stream = None

    def shouldRollover(self, record):
        msg = (self.format(record) + "\n").encode("utf-8")
        try:
            size = os.stat(self.baseFilename).st_size
        except FileNotFoundError:
            return False
        return size > 0 and size + len(msg) > self.maxBytes

    def emit(self, record):
        try:
            with open(self._lock_path, "a") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                try:
                    self._reopen_if_rotated()
                    if self.shouldRollover(record):
                        if self.stream:
                            self.stream.close()
                            self.stream = None
                        self.doRollover()
                    logging.FileHandler.emit(self, record)
                    if self.stream:
                        self.stream.flush()
                finally:
                    fcntl.flock(lk, fcntl.LOCK_UN)
        except Exception:
            self.handleError(record)


_configured = {}


def setup(component="tagmender", job=None, level=None, console=False, max_bytes=None, backups=None,
          path=None) -> logging.Logger:
    """The 'tagmender' logger, writing to the shared rotating file. Level: argument, else
    $TAGMENDER_LOG_LEVEL, else INFO. Calling it again changes job/component/level only."""
    level = (level or os.environ.get("TAGMENDER_LOG_LEVEL") or "INFO").upper()
    if level not in LEVELS:
        level = "INFO"
    log = logging.getLogger("tagmender")
    log.setLevel(level)
    log.propagate = False
    path = Path(path) if path else log_dir() / "tagmender.log"
    key = str(path)
    if key not in _configured:
        h = SharedRotatingFileHandler(path, max_bytes or MAX_BYTES, backups or BACKUPS)
        h.setFormatter(logging.Formatter(FORMAT))
        ctx = _Context()
        h.addFilter(ctx)
        log.addHandler(h)
        _configured[key] = (h, ctx)
    h, ctx = _configured[key]
    ctx.job, ctx.component = job or ctx.job, component
    if console and not any(getattr(x, "_tm_console", False) for x in log.handlers):
        c = logging.StreamHandler()
        c._tm_console = True
        c.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        c.addFilter(_Context(job or "-", component))
        log.addHandler(c)
    return log


def reset():
    """Close the handlers (tests; and before a diagnostics export reads the file)."""
    log = logging.getLogger("tagmender")
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    _configured.clear()
