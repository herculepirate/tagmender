# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Where each chosen image goes on disk, and writing it safely. Pure Python (image preparation is
injected), so the routing rules are unit-tested without Qt or Picard."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..imageinfo import probe
from ..model import ArtType, Candidate
from ..settings import ARTIST_TYPES, Settings
from . import files as F

log = logging.getLogger("coverup.apply")

_DISC_DIR = re.compile(r"^(cd|disc|disk|dvd|sacd|vinyl|side)[\s_-]*\d+$", re.IGNORECASE)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff")
SIDECAR = "coverup.json"

# prepare(data, max_side, fmt, quality, min_side, upscale) -> (bytes, w, h, fmt)
PrepareFn = Callable[..., "tuple[bytes, int, int, str]"]


@dataclass
class FileItem:
    """One image to write, already chosen. ``data`` is the full-size original."""

    art_type: ArtType
    data: bytes
    candidate: Candidate | None = None
    disc_number: int | None = None
    page: int | None = None
    provenance: dict = field(default_factory=dict)


@dataclass
class Target:
    folder: Path
    stem: str


def dims_of(path: Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as fh:
            info = probe(fh.read(65536))
    except OSError:
        return None
    return (info[0], info[1]) if info else None


def targets_for(item: FileItem, album_dir: str | Path, s: Settings) -> list[Target]:
    """Folder(s) and file stem(s) for one item, following the output settings."""
    album_dir = Path(album_dir)
    values = {"discnumber": item.disc_number or 1, "n": f"{item.page or 1:02d}"}
    stem = F.render_name(s.name_template(item.art_type), values)
    if item.art_type in ARTIST_TYPES:
        if not s["save_artist_art"]:
            return []
        base = album_dir.parent if _DISC_DIR.match(album_dir.name) else album_dir  # Album/CD1 -> Album
        parent = base.parent
        if parent == album_dir:  # filesystem root: no artist folder
            return []
        return [Target(parent, stem)]
    sub = s["folder_name"] if s["folder_mode"] == "subfolder" else None
    art_dir = F.resolve_target_dir(album_dir, sub)
    out = [Target(art_dir, stem)]
    if item.art_type == ArtType.FRONT and sub and s["front_in_album_folder"]:
        out.insert(0, Target(album_dir, stem))
    return out


def _sibling_images(folder: Path, stem: str) -> list[Path]:
    try:
        return [p for p in folder.iterdir() if p.is_file() and p.stem.lower() == stem.lower() and p.suffix.lower() in IMAGE_EXTS]
    except OSError:
        return []


def write_item(
    item: FileItem, target: Target, s: Settings, prepare: PrepareFn, batch: str
) -> F.WriteResult:
    """Prepare (resize/convert) and safely write one image to one target."""
    data, w, h, fmt = prepare(
        item.data,
        max_side=int(s["file_max_side"] or 0),
        fmt="jpeg",  # always JPEG, whatever the source (PNG, TIFF, WebP...)
        quality=int(s["jpeg_quality"]),
        min_side=s.min_side_for(item.art_type),
        upscale=bool(s["upscale_to_minimum"]),
    )
    ext = ".jpg" if fmt == "jpeg" else f".{fmt}"
    path = target.folder / f"{target.stem}{ext}"
    policy = s["collision_policy"]
    # an existing cover.png must be compared with (and replaced by) a new cover.jpg, not kept beside it
    siblings = [p for p in _sibling_images(target.folder, target.stem) if p.name != path.name]
    if siblings and policy in (F.IF_BETTER, F.OVERWRITE, F.SKIP) and not path.exists():
        old = siblings[0]
        if policy == F.SKIP:
            return F.WriteResult("skipped", old, reason=f"{old.name} exists (policy: skip)")
        if policy == F.IF_BETTER:
            od = dims_of(old)
            if not (od and w * h > od[0] * od[1]):
                return F.WriteResult("skipped", old, reason=f"Existing {old.name} is as good or better")
        res = F.safe_write(path, data, (w, h), F.OVERWRITE, dims_of, backup=bool(s["backups"]), batch=batch)
        F.retire(old, backup=bool(s["backups"]), batch=batch)
        return res
    return F.safe_write(path, data, (w, h), policy, dims_of, backup=bool(s["backups"]), batch=batch)


def update_sidecar(folder: Path, entries: list[dict]) -> None:
    """Merge provenance entries into ``coverup.json`` (one record per written file name)."""
    if not entries:
        return
    path = folder / SIDECAR
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        existing = {}
    files = existing.get("files", {})
    for e in entries:
        files[e["file"]] = e
    existing.update({"generator": "CoverUp (TagMender)", "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": files})
    try:
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write %s: %s", path, exc)


def provenance_for(item: FileItem) -> dict:
    c = item.candidate
    out = {"type": item.art_type.value}
    if item.disc_number:
        out["disc"] = item.disc_number
    if c is not None:
        out.update({"source": c.provider, "url": c.url, "tier": c.tier.value, "score": round(c.score, 1), "size": f"{c.width}x{c.height}"})
        if c.attribution:
            out["attribution"] = c.attribution
        if c.generated:
            out["generated"] = True
            out.update({k: v for k, v in c.extra.items() if k in ("method", "template", "seed", "model", "prompt", "backend")})
    out.update(item.provenance)
    return out


@dataclass
class FolderPlan:
    """Album-level bookkeeping so each folder gets each image once, whatever order files are saved in."""

    items: list[FileItem]
    settings: Settings
    batch: str
    done: set[tuple[str, str]] = field(default_factory=set)  # (folder, item key)
    album_dirs_done: list[str] = field(default_factory=list)
    results: list[F.WriteResult] = field(default_factory=list)

    @staticmethod
    def key(item: FileItem) -> str:
        return f"{item.art_type.value}:{item.disc_number or ''}:{item.page or ''}"

    def write_for_folder(self, album_dir: str | Path, disc: int, prepare: PrepareFn) -> list[F.WriteResult]:
        """Write everything that belongs in ``album_dir`` for a track on ``disc``. Idempotent."""
        album_dir = str(Path(album_dir))
        first_dir = not self.album_dirs_done
        if album_dir not in self.album_dirs_done:
            if self.settings["multi_folder"] == "first" and not first_dir:
                album_wide_allowed = False
            else:
                album_wide_allowed = True
                self.album_dirs_done.append(album_dir)
        else:
            album_wide_allowed = True
        out: list[F.WriteResult] = []
        sidecar: dict[Path, list[dict]] = {}
        for item in self.items:
            if item.art_type == ArtType.DISC:
                if (item.disc_number or 1) != disc:
                    continue
            elif not album_wide_allowed:
                continue
            k = (album_dir, self.key(item))
            if k in self.done:
                continue
            self.done.add(k)
            for t in targets_for(item, album_dir, self.settings):
                if item.art_type in ARTIST_TYPES and (str(t.folder), self.key(item)) in self.done:
                    continue  # one artist folder shared by several album folders
                self.done.add((str(t.folder), self.key(item)))
                try:
                    r = write_item(item, t, self.settings, prepare, self.batch)
                except (OSError, ValueError) as exc:
                    log.error("Writing %s into %s failed: %s", item.art_type.value, t.folder, exc)
                    r = F.WriteResult("failed", None, reason=str(exc))
                log.info("File %s: %s %s %s", item.art_type.value, r.action, r.path or t.folder, r.reason)
                out.append(r)
                if r.action in ("written", "replaced", "kept_both") and r.path is not None and self.settings["write_sidecar"]:
                    sidecar.setdefault(r.path.parent, []).append({"file": r.path.name, **provenance_for(item)})
        for folder, entries in sidecar.items():
            update_sidecar(folder, entries)
        self.results.extend(out)
        return out
