# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Folder resolution, file naming, safe writes, backups and an undo journal (sections 3.4, 6.3).

Pure Python. Image dimensions are injected through a callable so there is no Qt dependency here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FOLDER_PRESETS = ("covers", "art", "artwork", "booklet")
DEFAULT_FOLDER = "covers"
BACKUP_DIR_NAME = ".coverup-backup"
JOURNAL_NAME = "journal.jsonl"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_NAME = 120

DEFAULT_TEMPLATES = {
    "front": "cover",
    "back": "back",
    "disc": "disc{discnumber}",
    "booklet": "booklet-{n}",
    "artist-thumb": "artist",
}


def sanitize_filename(name: str) -> str:
    """Make a single path component safe on Windows, macOS and Linux."""
    cleaned = _ILLEGAL.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:MAX_NAME]


def validate_folder_name(name: str) -> str:
    """Validate a user-chosen sub-folder name. Returns it unchanged or raises ValueError."""
    if not name or not name.strip():
        raise ValueError("Folder name is empty")
    if name != name.strip():
        raise ValueError("Folder name must not start or end with spaces")
    if name in (".", "..") or "/" in name or "\\" in name or _ILLEGAL.search(name):
        raise ValueError(f"Invalid folder name: {name!r}")
    if name.endswith(".") or name.split(".")[0].lower() in _RESERVED:
        raise ValueError(f"Invalid folder name: {name!r}")
    if len(name) > MAX_NAME:
        raise ValueError("Folder name too long")
    return name


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def render_name(template: str, values: dict[str, object]) -> str:
    """Fill ``{placeholders}`` in a file-name template and sanitise the result (no extension)."""

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise ValueError(f"Unknown placeholder {{{key}}} in template {template!r}")
        return str(values[key])

    return sanitize_filename(_PLACEHOLDER.sub(sub, template))


def resolve_target_dir(album_dir: str | os.PathLike, folder: str | None) -> Path:
    """Album directory, or a validated sub-folder of it. ``folder`` of None/'' means same folder."""
    base = Path(album_dir)
    if not folder:
        return base
    return base / validate_folder_name(folder)


# ---- collision policy ------------------------------------------------------------------------------

SKIP = "skip"
OVERWRITE = "overwrite"
IF_BETTER = "if_better"
KEEP_BOTH = "keep_both"
POLICIES = (SKIP, OVERWRITE, IF_BETTER, KEEP_BOTH)

DimsOf = Callable[[Path], "tuple[int, int] | None"]


@dataclass
class WriteResult:
    action: str  # written | replaced | skipped | kept_both
    path: Path | None
    backup: Path | None = None
    reason: str = ""


def _new_file_mode() -> int:
    """Permissions an ordinary new file gets (0o666 minus the process umask).

    Read from /proc instead of os.umask(), which can only be read by setting
    it and is process-wide (CoverUp writes from Picard's worker threads)."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Umask:"):
                    return 0o666 & ~int(line.split()[1], 8)
    except (OSError, ValueError):
        pass
    return 0o644


def _unique_path(path: Path) -> Path:
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise OSError(f"Could not find a free name next to {path}")


def _journal(backup_root: Path, entry: dict) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    with open(backup_root / JOURNAL_NAME, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def safe_write(
    path: str | os.PathLike,
    data: bytes,
    new_dims: tuple[int, int] | None = None,
    policy: str = IF_BETTER,
    dims_of: DimsOf | None = None,
    validate: Callable[[bytes], bool] | None = None,
    backup: bool = True,
    batch: str | None = None,
) -> WriteResult:
    """Write ``data`` to ``path`` atomically, honouring the collision policy.

    - ``if_better`` replaces an existing file only when the new image has more pixels than the old one
      (unknown dimensions on either side count as "not better").
    - Any replaced file is copied to ``.coverup-backup/`` next to it and journaled so it can be undone.
    """
    if policy not in POLICIES:
        raise ValueError(f"Unknown collision policy {policy!r}")
    if validate is not None and not validate(data):
        return WriteResult("skipped", None, reason="Downloaded data failed validation")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    exists = target.exists()
    if exists:
        if policy == SKIP:
            return WriteResult("skipped", target, reason="File exists (policy: skip)")
        if policy == KEEP_BOTH:
            target = _unique_path(target)
            exists = False
        elif policy == IF_BETTER:
            old = dims_of(target) if dims_of else None
            if not (old and new_dims and new_dims[0] * new_dims[1] > old[0] * old[1]):
                return WriteResult("skipped", target, reason="Existing file is as good or better (or sizes unknown)")

    backup_path: Path | None = None
    if exists and backup:
        root = target.parent / BACKUP_DIR_NAME
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = root / f"{stamp}-{target.name}"
        n = 1
        while backup_path.exists():
            n += 1
            backup_path = root / f"{stamp}-{n}-{target.name}"
        shutil.copy2(target, backup_path)

    fd, tmp = tempfile.mkstemp(prefix=".coverup-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp() creates the file 0600 (owner only). Without this, every
        # image CoverUp saves is unreadable to media servers running as
        # another user (Plex, Jellyfin, Navidrome, Lyrion on a NAS). A
        # replaced file keeps the permissions it had.
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode) if target.exists() else _new_file_mode()
            os.chmod(tmp, mode)
        except OSError:
            pass  # e.g. a share that does not support chmod; keep going
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    action = "replaced" if exists else ("kept_both" if policy == KEEP_BOTH and target.name != Path(path).name else "written")
    _journal(
        target.parent / BACKUP_DIR_NAME,
        {
            "ts": time.time(),
            "action": action,
            "path": str(target),
            "backup": str(backup_path) if backup_path else None,
            "batch": batch,
        },
    )
    return WriteResult(action, target, backup_path)


def _backup_copy(path: Path) -> Path:
    root = path.parent / BACKUP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / f"{stamp}-{path.name}"
    n = 1
    while dest.exists():
        n += 1
        dest = root / f"{stamp}-{n}-{path.name}"
    shutil.copy2(path, dest)
    return dest


def retire(path: str | os.PathLike, backup: bool = True, batch: str | None = None) -> Path | None:
    """Remove a file being superseded (e.g. old cover.png replaced by cover.jpg), backing it up first."""
    p = Path(path)
    if not p.exists():
        return None
    dest = _backup_copy(p) if backup else None
    p.unlink()
    _journal(p.parent / BACKUP_DIR_NAME, {"ts": time.time(), "action": "removed", "path": str(p), "backup": str(dest) if dest else None, "batch": batch})
    return dest


def _read_journal(folder: str | os.PathLike) -> tuple[Path, list[dict]]:
    journal = Path(folder) / BACKUP_DIR_NAME / JOURNAL_NAME
    if not journal.exists():
        return journal, []
    entries = []
    for ln in journal.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                entries.append(json.loads(ln))
            except ValueError:
                continue
    return journal, entries


def _undo_entry(entry: dict) -> str | None:
    p = Path(entry["path"])
    if entry.get("backup") and Path(entry["backup"]).exists():
        shutil.copy2(entry["backup"], p)
        return f"Restored {p.name} from backup"
    if entry["action"] in ("written", "kept_both") and p.exists():
        p.unlink()
        return f"Removed {p.name}"
    return None


def last_batch(folder: str | os.PathLike) -> str | None:
    _j, entries = _read_journal(folder)
    for e in reversed(entries):
        if e.get("batch"):
            return e["batch"]
    return None


def undo_batch(folder: str | os.PathLike, batch: str) -> list[str]:
    """Undo every journaled change of one apply run in ``folder`` (newest first)."""
    journal, entries = _read_journal(folder)
    keep, undo = [], []
    for e in entries:
        (undo if e.get("batch") == batch else keep).append(e)
    done = [msg for e in reversed(undo) if (msg := _undo_entry(e))]
    if undo:
        journal.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep), encoding="utf-8")
    return done


def undo_last(folder: str | os.PathLike, count: int = 1) -> list[str]:
    """Undo the last ``count`` journaled writes in ``folder``: restore backups or remove new files.

    Returns human-readable lines describing what was undone.
    """
    root = Path(folder) / BACKUP_DIR_NAME
    journal = root / JOURNAL_NAME
    if not journal.exists():
        return []
    lines = [ln for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    done: list[str] = []
    remaining = list(lines)
    for _ in range(min(count, len(lines))):
        entry = json.loads(remaining.pop())
        p = Path(entry["path"])
        if entry["backup"] and Path(entry["backup"]).exists():
            shutil.copy2(entry["backup"], p)
            done.append(f"Restored {p.name} from backup")
        elif entry["action"] in ("written", "kept_both") and p.exists():
            p.unlink()
            done.append(f"Removed {p.name}")
    journal.write_text("".join(ln + "\n" for ln in remaining), encoding="utf-8")
    return done
