"""plan.py - what TagMender is about to do, decided before anything is touched.

build(proposals, profile) turns identified (or unresolved) albums into a Plan: for every
file the tags before and after (added / changed / kept / removed / locked), the source
and destination path, the companion files that go along, artwork, the evidence and
confidence behind it, conflicts, and the albums the rules exclude (labelled Ignored or
Skipped, never Unresolved). The same Plan is shown by `tagmender plan`, the GUI, and
written as JSON with --output; `tagmender apply PLAN` executes exactly that plan.

Planning reads files and providers but never changes the music.

What decides what:
  - the profile's rules (exclusions) first, and again with the identified release;
  - field policy per field: preserve | fill_missing | replace | remove | lock;
    an existing value is only replaced at >= thresholds.replace_min_confidence;
  - files are renamed, copied or moved only at >= thresholds.organize_min_confidence;
    below it a move/copy profile tags in place (move) or does nothing (copy), and says so;
  - unresolved albums: unresolved.action = leave | mark (TAGMENDER=Unresolved and the
    reason codes) | move (to unresolved.move_to);
  - a value equal to what the file has is not written again (repeat runs rewrite nothing).
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import exclusions as ex
import filenames as fnm
import naming
import reasons
import tagio

PLAN_VERSION = 1
AUDIO_EXT = ex.AUDIO_EXT
CUE_LIKE = {".cue", ".m3u", ".m3u8"}
EMBEDDABLE = {".flac", ".mp3", ".m4a", ".mp4", ".aiff", ".aif", ".wav", ".ogg", ".oga", ".opus"}


@dataclass
class Proposal:
    """One album (or single file) as identification left it."""
    source_dir: str
    files: list                         # paths, in track order
    targets: list = field(default_factory=list)   # per file: {field: [values]} (empty when unresolved)
    status: str = "identified"          # identified | unresolved
    provider: str = ""                  # musicbrainz | discogs | web | ...
    release_id: str = ""
    url: str = ""
    confidence: float = 0.0             # the matcher's score (0-100); a heuristic, not a probability
    margin: float | None = None
    why: list = field(default_factory=list)          # reasons it is not more certain
    evidence: list = field(default_factory=list)     # what agreed
    reason: str = ""                    # unresolved: in words
    reason_codes: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)   # field -> provider
    provider_errors: dict = field(default_factory=dict)   # provider -> error (recorded, not fatal)
    release: dict = field(default_factory=dict)      # identified album/albumartist/genre, for rules
    artwork: dict | None = None         # {"front": staged file, "source": ..., "width": .., "height": ..}
    sidecars: dict = field(default_factory=dict)     # audio file -> text of a new .lrc next to it


def _fp(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino}


def _vals(v):
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if str(x) != ""]


def diff_tags(before: dict, target: dict, profile, confidence: float, extra_removals=()):
    """(changes {field: [..] | None}, rows [{field, before, after, action}])."""
    changes, rows = {}, []
    replace_ok = confidence >= float(profile["thresholds"]["replace_min_confidence"])
    fields = list(dict.fromkeys(list(target) + list(extra_removals)))
    for f in fields:
        if f.startswith("~"):
            continue
        b, a = _vals(before.get(f)), _vals(target.get(f))
        pol = profile.policy(f)
        if f in extra_removals and f not in target:
            pol = "remove"
        if pol == "lock":
            if a != b:
                rows.append({"field": f, "before": b, "after": b, "action": "locked"})
            continue
        if pol == "remove":
            if b:
                changes[f] = None
                rows.append({"field": f, "before": b, "after": [], "action": "removed"})
            continue
        if pol == "preserve":
            if a != b and b:
                rows.append({"field": f, "before": b, "after": b, "action": "preserved"})
            continue
        if not a or a == b:
            continue
        if not b:
            changes[f] = a
            rows.append({"field": f, "before": b, "after": a, "action": "added"})
        elif pol == "fill_missing":
            rows.append({"field": f, "before": b, "after": b, "action": "preserved"})
        elif replace_ok or f.startswith("tagmender"):
            changes[f] = a
            rows.append({"field": f, "before": b, "after": a, "action": "changed"})
        else:
            rows.append({"field": f, "before": b, "after": b, "action": "kept (confidence below replace threshold)"})
    # policy "remove" for fields the target does not mention
    for f, pol in (profile["field_policy"].get("fields") or {}).items():
        if pol == "remove" and f not in fields and _vals(before.get(f)):
            changes[f] = None
            rows.append({"field": f, "before": _vals(before.get(f)), "after": [], "action": "removed"})
    return changes, rows


MOUNT_PARENTS = ("/media", "/mnt", "/run/media")


def mounted(folder) -> bool:
    """False for a folder under /media, /mnt or /run/media when nothing is mounted between
    it and that parent: the share it belongs to is not mounted, and anything written there
    would land on the system disk."""
    real = os.path.realpath(os.path.expanduser(str(folder)))
    parent = next((m for m in MOUNT_PARENTS if real.startswith(m + os.sep)), None)
    if parent is None:
        return True
    p = Path(real)
    while str(p) != parent and p != p.parent:
        if os.path.ismount(p):
            return True
        p = p.parent
    return False


MAX_TREE = 5000                           # entries looked at below one album folder


def _companions(src_dir: Path, moved: dict, profile, shallow=False):
    """([(companion, destination)], why not): the non-audio files that go with an album
    that moves or is copied, as Picard's "move additional files" and foobar2000's "move
    entire source folder content" do:
      - in each folder holding this album's music (the album folder, its disc folders):
        the files whose extension is in the profile's "companions";
      - in folders with no music anywhere inside (Scans, covers, artwork ...): everything;
      - nothing from a folder holding music that is not moving with this album (a mixed
        folder, a bonus album in a subfolder), nor from below it.
    Each goes to the same place relative to where the music lands. Hidden files and
    symbolic links stay. moved: {source file: destination file}."""
    exts = {e.lower() for e in profile["companions"]}
    moved = {Path(k): Path(v) for k, v in moved.items()}
    dmap = {}
    for s_, d_ in moved.items():
        dmap.setdefault(s_.parent, set()).add(d_.parent)
    if any(len(v) > 1 for v in dmap.values()):
        return [], "files of one folder go to different folders; companion files stay where they are"
    dmap = {k: next(iter(v)) for k, v in dmap.items()}
    if src_dir not in dmap:
        top = Path(os.path.commonpath([str(d) for d in dmap.values()]))
        if any(d != top and d.parent != top for d in dmap.values()):
            return [], "the album's folders go to different places; companion files stay where they are"
        dmap[src_dir] = top

    def audio_in(d):
        try:
            return [p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT and p.is_file()
                    and not p.name.startswith(".")]
        except OSError:
            return []
    if any(p not in moved for p in audio_in(src_dir)):
        return [], "folder holds other music too; companion files stay where they are"
    tree, seen, foreign = [], 0, []
    targets = {os.path.realpath(str(x)) for x in dmap.values()}

    def overlaps(d):                       # a folder the album is moving into (or out of it)
        r = os.path.realpath(str(d))
        return any(t == r or t.startswith(r + os.sep) or r.startswith(t + os.sep) for t in targets)
    for dirpath, dirs, names in os.walk(src_dir):
        d = Path(dirpath)
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not (d / x).is_symlink()
                         and not overlaps(d / x))
        if shallow:
            dirs[:] = []
        here = [d / n for n in names if not n.startswith(".")]
        if d != src_dir and any(p.suffix.lower() in AUDIO_EXT and p not in moved for p in here):
            dirs[:] = []                                   # another album's folder: left alone
            foreign.append(d)
            continue
        seen += len(here) + len(dirs)
        if seen > MAX_TREE:
            return _companions(src_dir, moved, profile, shallow=True)[0], \
                "album folder holds too many files to look through; only its own companion files go"
        tree.append((d, here))
    music_below = set()
    for d, here in [(f_, [f_ / "x.flac"]) for f_ in foreign] + tree:
        if any(p.suffix.lower() in AUDIO_EXT for p in here):
            q = d
            while True:
                music_below.add(q)
                if q == src_dir or q == q.parent:
                    break
                q = q.parent
    by_stem = {(s_.parent, s_.stem): d_ for s_, d_ in moved.items()}
    out = []
    for d, here in tree:
        anchor = d
        while anchor not in dmap:
            anchor = anchor.parent
        dest_dir = dmap[anchor] / d.relative_to(anchor)
        for p in sorted(here):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() in AUDIO_EXT:
                continue
            if d in music_below and p.suffix.lower() not in exts:
                continue
            track = by_stem.get((d, p.stem))                # "01 a.lrc" goes with "01 a.flac"
            out.append((p, track.with_name(track.stem + p.suffix) if track else dest_dir / p.name))
    return out, ""


def _cue_rewrites(path: Path, renames: dict):
    """New text of a cue sheet or playlist whose entries name files being renamed or moved,
    or None. renames: {old path relative to the sheet's folder: new path relative to where
    the sheet goes}. Every entry is replaced in one pass (so 01->02 and 02->03 never chain),
    as a quoted FILE name or as a whole playlist line; "\\" separators count as "/"."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    table = {}
    for old, new in renames.items():
        old_n, new_n = old.replace("\\", "/"), new.replace("\\", "/")
        if old_n != new_n:
            table[old_n.casefold()] = new_n
    if not table:
        return None

    def swap(name):
        return table.get(name.replace("\\", "/").casefold())

    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]

    def line(ln):
        m = re.match(r'^(\s*FILE\s+")([^"]+)(".*)$', ln, re.I)
        if m and swap(m.group(2)):
            return m.group(1) + swap(m.group(2)) + m.group(3)
        body = ln.rstrip("\r")
        if body.strip() and not body.lstrip().startswith("#") and swap(body.strip()):
            return ln[:len(ln) - len(ln.lstrip())] + swap(body.strip()) + ln[len(body):]
        return ln
    new = "\n".join(line(ln) for ln in text.split("\n"))
    if new == text:
        return None
    if bom:
        new = "\ufeff" + new
    try:
        new.encode(enc)
    except UnicodeEncodeError:
        return None, enc                                # the new names cannot be written in its encoding
    return new, enc


class Planner:
    def __init__(self, profile, job_id=None, rules=None, overrides=None, extra_roots=()):
        self.profile = profile
        self.extra_roots = [os.path.realpath(os.path.expanduser(str(r))) for r in extra_roots]
        self.job = job_id or time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.rules = ex.load_rules(profile.name if profile.name != "default" else None) if rules is None else rules
        self.overrides = ex.State().overrides() if overrides is None else overrides
        self.claimed = {}          # folded destination path -> source (collisions inside the plan)
        self.created_dirs = set()
        self._dirs = {}

    # ---- the plan for one album -------------------------------------------
    def album(self, prop: Proposal) -> dict:
        p = self.profile
        files = [Path(f) for f in prop.files]
        entry = {"source_dir": str(prop.source_dir), "status": "ready", "label": "", "rule": None,
                 "identification": {k: getattr(prop, k) for k in
                                    ("status", "provider", "release_id", "url", "confidence", "margin", "why",
                                     "evidence", "reason_codes", "provenance", "provider_errors")},
                 "release": prop.release, "files": [], "companions": [], "artwork": None,
                 "conflicts": [], "skipped": [], "notes": []}
        if prop.status == "unresolved":
            entry["identification"]["reason"] = reasons.encode(prop.reason, prop.reason_codes or None)
        # 1. rules, with what the files say and what identification found
        dec = ex.evaluate(files, self.rules, release=prop.release or None, overrides=self.overrides)
        if dec.excluded:
            whole = dec.summary()
            if whole is not None:
                entry.update(status="excluded", label=whole.label,
                             rule={"id": whole.rule.id, "name": whole.rule.name, "action": whole.action,
                                   "matched": whole.where})
                return entry
            for f, e in dec.excluded.items():
                entry["skipped"].append({"file": str(f), "label": e.label, "rule": e.rule.id, "matched": e.where})
        kept = [f for f in files if f not in dec.excluded]
        # 2. profile filters
        for f in list(kept):
            if not p.wants_format(f):
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "profile formats",
                                         "matched": f.suffix.lower()})
                kept.remove(f)
            elif p.excluded_path(f):
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "profile exclude_paths",
                                         "matched": str(f)})
                kept.remove(f)
        if not kept:
            entry["status"] = "skipped"
            entry["label"] = "Skipped"
            return entry
        targets = {Path(f): t for f, t in zip(files, prop.targets or [{}] * len(files))}
        identified = prop.status == "identified"
        conf = float(prop.confidence or 0)
        organize_ok = identified and conf >= float(p["thresholds"]["organize_min_confidence"])
        op = p["operation"]
        if not identified:
            ua = p["unresolved"]["action"]
            entry["status"] = "unresolved"
            entry["label"] = "Unresolved"
            if ua == "leave" or op == "copy":
                for f in kept:
                    entry["files"].append({"src": str(f), "dst": str(f), "op": "none", "changes": {}, "diff": [],
                                           "fingerprint": _fp(f)})
                return entry
            op = "move" if ua == "move" else "in_place"
        elif op in ("copy", "move") and not organize_ok:
            entry["notes"].append(f"confidence {conf:.1f} is below organize_min_confidence "
                                  f"{p['thresholds']['organize_min_confidence']}: "
                                  + ("not copied" if op == "copy" else "tagged in place, not moved"))
            if op == "copy":
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
            op = "in_place"
        # 3. destination root (never created: an unmounted share must not fill the local disk)
        dest_root = None
        if op in ("copy", "move"):
            dest_root = Path(p["unresolved"]["move_to"] if not identified else p["destination"]).expanduser()
            if not dest_root.is_dir():
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is not available (unmounted?); "
                                          "TagMender never creates it")
                return entry
            if not mounted(dest_root):
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is on the system disk, not on the share "
                                          "mounted there (unmounted?)")
                return entry
            entry["dest_root"] = {"path": str(dest_root), "dev": os.stat(dest_root).st_dev}
        template = naming.template_for(p["naming"])
        for f in kept:
            try:
                before = tagio.read(f)
            except tagio.Unsupported as exc:
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "unsupported_format",
                                         "matched": str(exc)})
                continue
            target = dict(targets.get(f) or {})
            if identified and p["mark_resolved"]:
                target.setdefault("tagmender", ["Resolved"])
                if not before.get("tagmender_reason"):
                    target.setdefault("tagmender_reason", [])
            if not identified and p["unresolved"]["action"] in ("mark", "move"):
                target = {"tagmender": ["Unresolved"], "tagmender_reason": [entry["identification"]["reason"]]}
            removals = ["tagmender_reason"] if identified and before.get("tagmender_reason") and \
                str((before.get("tagmender") or [""])[0]).lower() == "unresolved" else []
            changes, rows = diff_tags(before, target, p, conf if identified else 100.0, removals)
            fe = {"src": str(f), "dst": str(f), "op": "tag" if changes else "none", "changes": changes,
                  "diff": rows, "fingerprint": _fp(f)}
            if op in ("copy", "move"):
                merged = {**{k: v for k, v in before.items()}, **{k: v for k, v in changes.items() if v is not None}}
                for k, v in changes.items():
                    if v is None:
                        merged.pop(k, None)
                if not identified:      # as it is, with its folders below the profile's source
                    src_root = Path(prop.source_dir)
                    try:
                        rel = f.relative_to(src_root)
                    except ValueError:
                        rel = Path(f.name)
                    real_hold = os.path.realpath(dest_root)
                    if os.path.realpath(str(src_root)).startswith(real_hold.rstrip(os.sep) + os.sep):
                        dst = f                         # already in the holding folder: stays
                    else:
                        dst = dest_root / self._below_source(src_root) / rel
                    notes = []
                else:
                    comps = naming.render(template, merged)
                    dst, notes = fnm.make_path(dest_root, comps, f.suffix.lower(), p["filenames"])
                    if dst is not None:
                        dst = self._spelled(dst, dest_root)
                entry["notes"] += notes
                if dst is None:
                    entry["conflicts"].append(f"{f.name}: " + (notes[-1] if notes else "no destination path"))
                    continue
                dst = self._unique(dst, f, entry)
                if dst is None:
                    continue
                fe["dst"] = str(dst)
                if Path(dst) == f:                       # already where the template puts it
                    fe["op"] = "tag" if changes else "none"
                else:
                    fe["op"] = op
                    if Path(dst).exists() and os.path.samefile(dst, f) and fnm.fold(dst) != fnm.fold(f):
                        fe["linked"] = True              # the destination is a hard link to this file
                        if op == "copy" and not changes:
                            fe["op"] = "none"            # already there
            entry["files"].append(fe)
        if entry["conflicts"]:
            entry["status"] = "blocked"
            return entry
        # 4. companion files go with an album that moves or is copied
        moved = [fe for fe in entry["files"] if fe["op"] in ("copy", "move")]
        if moved:
            # every file of the album, those already in place too: their folder is the album's
            pairs = {fe["src"]: fe["dst"] for fe in entry["files"]}
            src_root = Path(prop.source_dir)
            comps, why_not = _companions(src_root, pairs, p,
                                         shallow=os.path.realpath(src_root) in self.roots())
            if why_not:
                entry["notes"].append(why_not)
            for c, want in comps:
                dst = c if want == c else self._unique(want, c, entry)
                if dst is None:
                    continue
                ce = {"src": str(c), "dst": str(dst), "op": moved[0]["op"] if dst != c else "rewrite",
                      "fingerprint": _fp(c)}
                if c.suffix.lower() in CUE_LIKE:
                    rel = {os.path.relpath(s_, c.parent): os.path.relpath(d_, dst.parent)
                           for s_, d_ in pairs.items()}
                    rw = _cue_rewrites(c, rel)
                    if rw and rw[0] is None:
                        entry["notes"].append(f"{c.name}: the new file names cannot be written in its "
                                              f"{rw[1]} encoding; it keeps the old names")
                    elif rw:
                        ce["rewrite"] = {"text": rw[0], "encoding": rw[1]}
                if ce["op"] == "rewrite" and not ce.get("rewrite"):
                    continue                                  # stays where it is, unchanged
                entry["companions"].append(ce)
        # 5. artwork (staged by the artwork provider; never replaces a picture already embedded)
        art = prop.artwork if p["artwork"]["enabled"] else None
        if art and art.get("front") and Path(art["front"]).exists():
            embed = [fe["dst"] for fe in entry["files"]
                     if p["artwork"]["embed_front"] and Path(fe["src"]).suffix.lower() in EMBEDDABLE
                     and not tagio.read(fe["src"]).get("~pictures")]
            # the album's own folder: for a disc set in "Disc 01", "Disc 02" ... that is the folder
            # holding the disc folders, not the first disc's
            folder = (Path(os.path.commonpath([str(Path(fe["dst"]).parent) for fe in entry["files"]]))
                      if entry["files"] else Path(prop.source_dir))
            has_image = folder.exists() and any(x.suffix.lower() in (".jpg", ".jpeg", ".png") for x in folder.iterdir())
            has_image = has_image or any(Path(c["dst"]).parent == folder and Path(c["dst"]).suffix.lower() in
                                         (".jpg", ".jpeg", ".png") for c in entry["companions"])
            for fe in entry["files"]:
                if fe["dst"] in embed and fe["op"] == "none":
                    fe["op"] = "tag"                          # only the cover is new
            entry["artwork"] = {"front": art["front"], "source": art.get("source", ""),
                                "embed_into": embed, "file": None if (has_image or not p["artwork"]["files"])
                                else str(folder / "cover.jpg"),
                                "width": art.get("width"), "height": art.get("height")}
        # 6. lyrics files (.lrc) next to the tracks; never over a file that is already there
        entry["sidecars"] = []
        if identified:
            for fe in entry["files"]:
                text = (prop.sidecars or {}).get(fe["src"])
                dst = Path(fe["dst"]).with_suffix(".lrc")
                if not text or dst.exists() or any(c["dst"] == str(dst) for c in entry["companions"]):
                    continue
                entry["sidecars"].append({"src": fe["src"], "dst": str(dst), "text": text})
        if all(fe["op"] == "none" for fe in entry["files"]) and not entry["companions"] and \
                not entry["sidecars"] and \
                not (entry["artwork"] and (entry["artwork"]["embed_into"] or entry["artwork"]["file"])):
            entry["status"] = "unchanged" if entry["status"] == "ready" else entry["status"]
        return entry

    def _below_source(self, folder: Path) -> Path:
        """The album folder's path below the deepest profile source holding it
        ("Artist/Album"), else its last two folders."""
        real = os.path.realpath(str(folder))
        best = None
        for s_ in self.profile["sources"]:
            r = os.path.realpath(os.path.expanduser(s_["path"]))
            if real.startswith(r.rstrip(os.sep) + os.sep) and (best is None or len(r) > len(best)):
                best = r
        if best:
            return Path(os.path.relpath(real, best))
        return Path(folder.parent.name) / folder.name

    def _spelled(self, path: Path, root: Path) -> Path:
        """path, with each folder below root spelled as it already is on disk when the only
        difference is the Unicode form (and, with Windows-compatible names, letter case):
        never a second, look-alike "Beyoncé" folder."""
        windows = self.profile["filenames"].get("compat") == "windows"
        cur = Path(root)
        rel = Path(path).relative_to(root)
        parts = list(rel.parts)
        for k, comp in enumerate(parts[:-1]):
            if (cur / comp).is_dir():
                cur = cur / comp
                continue
            listing = self._listing(cur)
            key = unicodedata.normalize("NFC", comp)
            key = key.casefold() if windows else key
            if key not in listing:
                listing[key] = comp                      # later albums of this plan use this spelling
                cur = cur / comp
                continue
            cur = cur / listing[key]
        return cur / parts[-1]

    def _listing(self, d: Path) -> dict:
        if d not in self._dirs:
            windows = self.profile["filenames"].get("compat") == "windows"
            out = {}
            try:
                for name in os.listdir(d):
                    k = unicodedata.normalize("NFC", name)
                    out.setdefault(k.casefold() if windows else k, name)
            except OSError:
                pass
            self._dirs[d] = out
        return self._dirs[d]

    def _taken_alike(self, cand: Path, src: Path) -> bool:
        """With Windows-compatible names, a file whose name differs only in case or Unicode
        form is taken too (Windows and SMB see one name)."""
        if self.profile["filenames"].get("compat") != "windows" or not cand.parent.is_dir():
            return False
        k = unicodedata.normalize("NFC", cand.name).casefold()
        for name in os.listdir(cand.parent):
            if unicodedata.normalize("NFC", name).casefold() == k:
                other = cand.parent / name
                return not (src.exists() and os.path.samefile(other, src) and fnm.fold(other) == fnm.fold(src))
        return False

    def _unique(self, dst: Path, src: Path, entry):
        """dst, or dst with a stable suffix when another file has (or will have) that name;
        None (with a conflict noted) when even that is taken."""
        for attempt in range(2):
            cand = dst if attempt == 0 else dst.with_name(
                dst.stem + fnm.collision_suffix(str(src)) + dst.suffix)
            key = fnm.fold(cand)
            other = self.claimed.get(key)
            same_file = src.exists() and cand.exists() and os.path.samefile(cand, src)
            # this file, spelled differently, or a hard link to it already there (an *arr import)
            taken_on_disk = (cand.exists() and not same_file) or \
                (not cand.exists() and self._taken_alike(cand, src))
            if other in (None, str(src)) and not taken_on_disk:
                self.claimed[key] = str(src)
                if attempt:
                    entry["notes"].append(f"{dst.name} is taken; using {cand.name}")
                return cand
        entry["conflicts"].append(f"{dst} already exists (a different file) and so does the suffixed name")
        return None

    def build(self, proposals) -> dict:
        albums = [self.album(pr) for pr in proposals]
        counts = {}
        for a in albums:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return {"version": PLAN_VERSION, "job": self.job, "created": time.time(), "profile": self.profile.name,
                "profile_data": self.profile.to_dict(), "operation": self.profile["operation"],
                "roots": self.roots(), "albums": albums, "summary": counts,
                "discogs_settings": _enrichment_summary(self.profile),
                "preserve_timestamps": self.profile.preserve_timestamps()}

    def roots(self):
        p = self.profile
        r = [os.path.realpath(os.path.expanduser(s["path"])) for s in p["sources"]]
        for d in (p["destination"], p["unresolved"].get("move_to")):
            if d:
                r.append(os.path.realpath(os.path.expanduser(d)))
        return list(dict.fromkeys(r + self.extra_roots))


def build(proposals, profile, job_id=None, **kw) -> dict:
    return Planner(profile, job_id, **kw).build(proposals)


def save(plan: dict, path) -> Path:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(plan, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("version") != PLAN_VERSION:
        raise ValueError(f"plan version {plan.get('version')} is not {PLAN_VERSION}")
    return plan


def _enrichment_summary(profile) -> str:
    """One line saying how Discogs data is applied, and where that came from."""
    try:
        import picard_config
        s = profile.enrichment()
        src = (picard_config.source() if profile.get("follow_picard", True)
               else "the plugin's defaults (follow_picard is off)")
        return (f"Discogs Enrichment: merge {s['merge_mode']}, styles {s['style_mode']}, "
                f"marketplace {'on' if s['marketplace'] else 'off'}, "
                f"original date in Date {'on' if s['prefer_original_date'] else 'off'}  (from {src})")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# preview text (the CLI and GUI show the same thing)
# --------------------------------------------------------------------------
def preview(plan: dict, verbose=False) -> str:
    out = [f"Plan {plan['job']}  profile {plan['profile']}  ({plan['operation']})"]
    if plan.get("discogs_settings"):
        out.append(plan["discogs_settings"])
    for a in plan["albums"]:
        ident = a["identification"]
        head = f"\n[{a['status'].upper()}] {a['source_dir']}"
        if a["status"] == "excluded":
            head += f"\n    {a['label']} by rule '{a['rule']['name']}' (matched {a['rule']['matched']})"
        elif ident.get("status") == "identified":
            head += (f"\n    {ident['provider']} {ident['release_id']}  score {ident['confidence']:.1f}"
                     + (f"  margin {ident['margin']:.1f}" if ident.get("margin") is not None else ""))
            if ident.get("evidence"):
                head += "\n    agreed: " + "; ".join(ident["evidence"][:6])
            if ident.get("why"):
                head += "\n    doubts: " + "; ".join(ident["why"][:4])
        else:
            codes, words = reasons.parse(ident.get("reason", ""))
            head += f"\n    Unresolved: {', '.join(codes)} — {words}"
        if ident.get("provider_errors"):
            head += "\n    provider problems (recorded): " + "; ".join(f"{k}: {v}" for k, v in
                                                                      ident["provider_errors"].items())
        out.append(head)
        for c in a["conflicts"]:
            out.append(f"    CONFLICT: {c}")
        for n in a["notes"]:
            out.append(f"    note: {n}")
        for s in a["skipped"]:
            out.append(f"    {s['label']}: {Path(s['file']).name} ({s['rule']}: {s['matched']})")
        for fe in a["files"]:
            if fe["op"] == "none" and not verbose:
                continue
            move = f"  ->  {fe['dst']}" if fe["dst"] != fe["src"] else ""
            out.append(f"    {fe['op']:<5} {Path(fe['src']).name}{move}")
            for r in fe["diff"]:
                if r["action"] in ("added", "changed", "removed") or verbose:
                    b = "; ".join(r["before"]) or "—"
                    af = "; ".join(r["after"]) or "—"
                    out.append(f"           {r['action']:<9} {r['field']}: {b[:50]} => {af[:50]}")
        for c in a["companions"]:
            out.append(f"    {c['op']:<5} {Path(c['src']).name}  ->  {c['dst']}"
                       + ("  (file references updated)" if c.get("rewrite") else ""))
        if a.get("artwork"):
            art = a["artwork"]
            if art["embed_into"]:
                out.append(f"    art   front cover embedded into {len(art['embed_into'])} file(s) from {art['source']}")
            if art["file"]:
                out.append(f"    art   {art['file']}")
        for sc in a.get("sidecars") or []:
            out.append(f"    lyric {Path(sc['dst']).name}  (time-synced lyrics, new file)")
    s = plan["summary"]
    out.append("\n" + ", ".join(f"{v} {k}" for k, v in sorted(s.items())))
    return "\n".join(out)


def as_dict(p: Proposal) -> dict:
    return asdict(p)
