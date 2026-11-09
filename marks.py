"""What the copy is, written where a player can filter on it: GROUPING = "Vinyl Rip" for a vinyl
rip and GROUPING = "HD Audio" for anything finer than a CD (more than 16 bits or more than
44.1 kHz, DSD included). Only lossless audio counts: a lossy file's rate says nothing. Only
where the file has no Grouping: it never replaces yours. marks.grouping."""
from __future__ import annotations

from pathlib import Path

import match_core as mc
import tagio

LOSSLESS = {".flac", ".wav", ".aif", ".aiff", ".ape", ".wv", ".dsf", ".dff", ".m4a", ".alac", ".tak"}
HD, VINYL = "HD Audio", "Vinyl Rip"


def is_hd(path) -> bool:
    """True when this file's audio is lossless and finer than a CD's."""
    import mutagen
    p = Path(str(path))
    if p.suffix.lower() not in LOSSLESS:
        return False
    try:
        info = mutagen.File(str(p)).info
    except Exception:                       # (no mark on a file that cannot be read: it is only a mark)
        return False
    if p.suffix.lower() == ".m4a" and "alac" not in str(getattr(info, "codec", "")).lower():
        return False
    return (getattr(info, "bits_per_sample", 0) or 0) > 16 or (getattr(info, "sample_rate", 0) or 0) > 44100


def of_album(targets, files, folder="") -> str:
    """The album's mark: "Vinyl Rip" when its release is on vinyl or its folder says so, "HD
    Audio" when any of its files is finer than a CD, else ""."""
    kinds = {mc.media_kind(m) for t in targets or () for m in t.get("media") or ()}
    if "vinyl" in kinds or mc.media_hint(Path(str(folder)).name) == "vinyl":
        return VINYL
    return HD if any(is_hd(f) for f in files or ()) else ""


def apply(targets, files, folder, profile, full_targets=None) -> list:
    """The tracks' tags with the album's mark where the file and the tags have no Grouping
    (marks.grouping on, and Grouping not among the profile's level_fields.drop). full_targets:
    the tags before the tagging level cut them, for the release's media."""
    if not (profile.get("marks") or {}).get("grouping"):
        return targets
    if "grouping" in {str(x).lower() for x in (profile.get("level_fields") or {}).get("drop") or ()}:
        return targets
    mark = of_album(full_targets or targets, files, folder)
    if not mark:
        return targets
    files = list(files or ())
    return [dict(t, grouping=[mark]) if not t.get("grouping") and not _has_grouping(files[i] if len(files) == len(targets) else None)
            else t for i, t in enumerate(targets)]


def _has_grouping(path) -> bool:
    if path is None:
        return True                              # (which file it is, unknown: nothing is risked)
    try:
        return bool(tagio.read(path).get("grouping"))
    except Exception:
        return True
