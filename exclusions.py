"""exclusions.py - Ignore and Skip rules, shared by every TagMender tool.

A rule says which files or albums TagMender leaves alone:

  Ignore  excluded persistently, for as long as the rule still applies (the item
          is remembered, so watched folders do not queue it again until its files
          or the rules change);
  Skip    left out of the current job only (looked at again next time).

  target  "file"  - only the matching files;
          "album" - every file of the matching album. An album is the files that
          share an album (and album artist) tag, or, once identified, the matched
          release; never simply every file in the same folder.

Conditions test fields of the files or of the identified release:
  album, title (track title), artist, albumartist, genre, filename, folder,
  path, format, and tag:<NAME> for any other tag.
Operators: contains, equals, starts_with, ends_with (literal text, case-insensitive
by default: "[no vox]" means those exact characters), and glob or regex only when
chosen. Conditions combine with {"all": [...]} (AND) and {"any": [...]} (OR),
nested as deep as needed; "not": true inverts one condition.

Rules live in ~/.config/tagmender/exclusions.json (global) and in profiles
(~/.config/tagmender/profiles/<name>.json, key "exclusions"). Remembered ignores
and "process anyway" overrides live in ~/.local/state/tagmender/state.db.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import mutagen

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "tagmender"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "tagmender"
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".oga", ".wv", ".ape", ".wav", ".aiff", ".aif",
             ".dsf", ".dff", ".wma", ".mpc", ".aac", ".alac"}
FIELDS = ("album", "title", "artist", "albumartist", "genre", "filename", "folder", "path", "format")
OPS = ("contains", "equals", "starts_with", "ends_with", "glob", "regex")
ACTIONS = ("ignore", "skip")
TARGETS = ("file", "album")

# Shipped once, the first time rules are read (it replaces TagMender's earlier
# built-in "[no vox]" handling, so it can be switched off or changed).
DEFAULT_RULES = [{
    "id": "no-vox", "name": "[no vox] instrumental editions", "enabled": True,
    "action": "ignore", "target": "album",
    "when": {"any": [{"field": "album", "op": "contains", "value": "[no vox]"},
                     {"field": "folder", "op": "contains", "value": "[no vox]"},
                     {"field": "path", "op": "regex", "value": r"(?<![a-z])no[ ._-]?vox(?![a-z])"}]},
}]


class RuleError(ValueError):
    """A rule that cannot work (unknown field or operator, broken pattern)."""


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
@dataclass
class Rule:
    id: str
    name: str
    action: str
    target: str
    when: dict
    enabled: bool = True
    scope: str = "global"                       # "global" or the profile's name

    @staticmethod
    def from_dict(d, scope="global"):
        when = d.get("when")
        if when is None:                                   # flat form: conditions + match all/any
            when = {"any" if d.get("match") == "any" else "all": d.get("conditions") or []}
        r = Rule(id=str(d.get("id") or _slug(d.get("name") or "rule")), name=d.get("name") or d.get("id") or "rule",
                 action=d.get("action", "skip"), target=d.get("target", "file"), when=when,
                 enabled=bool(d.get("enabled", True)), scope=scope)
        r.validate()
        return r

    def to_dict(self):
        return {"id": self.id, "name": self.name, "enabled": self.enabled, "action": self.action,
                "target": self.target, "when": self.when}

    def validate(self):
        if self.action not in ACTIONS:
            raise RuleError(f"rule {self.id}: action must be one of {', '.join(ACTIONS)}")
        if self.target not in TARGETS:
            raise RuleError(f"rule {self.id}: target must be one of {', '.join(TARGETS)}")
        _validate(self.when, self.id)

    def describe(self):
        return f"{self.name} ({self.id})"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-") or "rule"


def _validate(node, rid):
    if not isinstance(node, dict):
        raise RuleError(f"rule {rid}: a condition must be an object")
    for key in ("all", "any"):
        if key in node:
            if not isinstance(node[key], list) or not node[key]:
                raise RuleError(f"rule {rid}: '{key}' needs a list of conditions")
            for child in node[key]:
                _validate(child, rid)
            return
    f, op = node.get("field", ""), node.get("op", "contains")
    if f not in FIELDS and not f.startswith("tag:"):
        raise RuleError(f"rule {rid}: unknown field '{f}' (use {', '.join(FIELDS)} or tag:NAME)")
    if op not in OPS:
        raise RuleError(f"rule {rid}: unknown operator '{op}' (use {', '.join(OPS)})")
    if "value" not in node:
        raise RuleError(f"rule {rid}: condition on '{f}' has no value")
    if op == "regex":
        try:
            re.compile(str(node["value"]))
        except re.error as exc:
            raise RuleError(f"rule {rid}: bad regular expression {node['value']!r}: {exc}") from exc


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rules_file():
    return CONFIG_DIR / "exclusions.json"


def profile_file(profile):
    return CONFIG_DIR / "profiles" / f"{profile}.json"


def _raw_rules(scope="global", create_defaults=True):
    if scope == "global":
        data = _read_json(rules_file())
        if data is None:
            data = {"rules": DEFAULT_RULES if create_defaults else []}
            if create_defaults:
                _write(rules_file(), data)
        return data
    data = _read_json(profile_file(scope)) or {"name": scope}
    data.setdefault("exclusions", [])
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_rules(profile=None, include_disabled=False):
    """Global rules, then the profile's (profile: argument, else $TAGMENDER_PROFILE)."""
    profile = profile if profile is not None else os.environ.get("TAGMENDER_PROFILE") or None
    out = [Rule.from_dict(d, "global") for d in _raw_rules("global").get("rules", [])]
    if profile:
        out += [Rule.from_dict(d, profile) for d in _raw_rules(profile).get("exclusions", [])]
    return out if include_disabled else [r for r in out if r.enabled]


def save_rule(rule, scope="global"):
    data = _raw_rules(scope)
    key = "rules" if scope == "global" else "exclusions"
    lst = [d for d in data.get(key, []) if d.get("id") != rule.id]
    lst.append(rule.to_dict())
    data[key] = lst
    _write(rules_file() if scope == "global" else profile_file(scope), data)


def remove_rule(rule_id, scope="global"):
    data = _raw_rules(scope)
    key = "rules" if scope == "global" else "exclusions"
    before = len(data.get(key, []))
    data[key] = [d for d in data.get(key, []) if d.get("id") != rule_id]
    _write(rules_file() if scope == "global" else profile_file(scope), data)
    return before != len(data[key])


def set_enabled(rule_id, enabled, scope="global"):
    data = _raw_rules(scope)
    key = "rules" if scope == "global" else "exclusions"
    hit = False
    for d in data.get(key, []):
        if d.get("id") == rule_id:
            d["enabled"] = bool(enabled)
            hit = True
    if hit:
        _write(rules_file() if scope == "global" else profile_file(scope), data)
    return hit


def rules_hash(rules):
    """Changes whenever an enabled rule changes: remembered ignores are then re-checked."""
    blob = json.dumps(sorted((r.scope, r.to_dict()) for r in rules if r.enabled), sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Metadata of a file, for the conditions
# --------------------------------------------------------------------------
_ID3 = {"TALB": "album", "TIT2": "title", "TPE1": "artist", "TPE2": "albumartist", "TCON": "genre"}
_MP4 = {"\xa9alb": "album", "\xa9nam": "title", "\xa9ART": "artist", "aART": "albumartist", "\xa9gen": "genre"}


def read_meta(path):
    """{field: [values]} for one file: its tags (all of them, lower-case names)
    plus filename, folder, path and format. Never raises."""
    p = Path(path)
    meta = {"filename": [p.name], "folder": [p.parent.name], "path": [str(p)],
            "format": [p.suffix.lower().lstrip(".")]}
    try:
        f = mutagen.File(p)
    except Exception:
        f = None
    if f is None:
        return meta
    meta["format"].append(type(f).__name__.lower())
    tags = getattr(f, "tags", None) or {}
    try:
        items = list(tags.items())
    except Exception:
        items = []
    for k, v in items:
        key = str(k)
        vals = v if isinstance(v, list) else [v]
        text = []
        for x in vals:
            x = getattr(x, "text", x)
            for y in (x if isinstance(x, list) else [x]):
                if isinstance(y, (bytes, bytearray)):
                    y = y.decode("utf-8", "ignore")
                if isinstance(y, tuple):
                    y = "/".join(str(z) for z in y)
                text.append(str(y))
        name = _ID3.get(key[:4]) if key[:4] in _ID3 else _MP4.get(key)
        if key.startswith("TXXX:"):
            name = key[5:].casefold()
        elif key.startswith("----:com.apple.iTunes:"):
            name = key.split(":", 2)[2].casefold()
        name = name or key.casefold()
        meta.setdefault(name, []).extend(t for t in text if t)
    return meta


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------
def _test(op, value, wanted, case_sensitive):
    v, w = (value, wanted) if case_sensitive else (value.casefold(), wanted.casefold())
    if op == "contains":
        return w in v
    if op == "equals":
        return v == w
    if op == "starts_with":
        return v.startswith(w)
    if op == "ends_with":
        return v.endswith(w)
    if op == "glob":
        return fnmatch.fnmatchcase(v, w)
    if op == "regex":
        return re.search(wanted, value, 0 if case_sensitive else re.IGNORECASE) is not None
    return False


def matches(node, meta):
    """Does a condition tree hold for this metadata? Multi-valued fields match if any value does."""
    if "all" in node:
        return all(matches(c, meta) for c in node["all"])
    if "any" in node:
        return any(matches(c, meta) for c in node["any"])
    f = node["field"]
    key = f[4:].casefold() if f.startswith("tag:") else f
    values = meta.get(key) or []
    hit = any(_test(node.get("op", "contains"), str(v), str(node["value"]), bool(node.get("case_sensitive")))
              for v in values)
    return (not hit) if node.get("not") else hit


@dataclass
class Exclusion:
    action: str                       # "ignore" or "skip"
    rule: Rule
    where: str = ""                   # what matched: a file name, or "identified release"

    @property
    def label(self):
        return "Ignored" if self.action == "ignore" else "Skipped"

    def reason(self):
        return f"{self.label} by rule '{self.rule.name}'" + (f" ({self.where})" if self.where else "")


@dataclass
class Decision:
    excluded: dict = field(default_factory=dict)        # path -> Exclusion
    kept: list = field(default_factory=list)

    @property
    def all_excluded(self):
        return bool(self.excluded) and not self.kept

    def summary(self):
        """The exclusion that applies to the whole album (all files), or None."""
        if not self.all_excluded:
            return None
        ex = list(self.excluded.values())
        return next((e for e in ex if e.action == "ignore"), ex[0])


def album_key(meta):
    """Files of one album share album + album artist tags; untagged files are
    grouped by folder until identified."""
    album = (meta.get("album") or [""])[0].strip().casefold()
    artist = ((meta.get("albumartist") or meta.get("artist") or [""])[0]).strip().casefold()
    if album:
        return ("tags", artist, album)
    return ("folder", (meta.get("path") or [""])[0].rsplit("/", 1)[0])


def _overridden(path, overrides):
    """PATH is an overridden folder or file, or lies inside one (not a mere name prefix)."""
    p = str(Path(path).expanduser().resolve())
    return any(p == o or p.startswith(o.rstrip(os.sep) + os.sep) for o in overrides)


def evaluate(paths, rules=None, release=None, overrides=None, metas=None):
    """Which of these files the rules exclude.

    File rules test each file. Album rules test each album (its files grouped by
    album_key); when `release` (identified metadata, e.g. {"album": [...],
    "albumartist": [...], "genre": [...]}) is given, the files are one album and
    its metadata is tested too. Files under a "process anyway" override are kept."""
    rules = load_rules() if rules is None else rules
    overrides = overrides or set()
    metas = metas or {}
    paths = [Path(p) for p in paths]
    meta = {p: metas.get(p) or read_meta(p) for p in paths}
    dec = Decision()
    groups = {}
    for p in paths:
        groups.setdefault(("release",) if release is not None else album_key(meta[p]), []).append(p)
    for p in paths:
        if _overridden(p, overrides):
            continue
        for r in rules:
            if r.enabled and r.target == "file" and matches(r.when, meta[p]):
                dec.excluded[p] = Exclusion(r.action, r, p.name)
                break
    for key, members in groups.items():
        live = [p for p in members if not _overridden(p, overrides)]
        if not live:
            continue
        for r in rules:
            if not (r.enabled and r.target == "album"):
                continue
            hit = None
            if release is not None and matches(r.when, dict(release, path=[str(live[0].parent)],
                                                            folder=[live[0].parent.name])):
                hit = "identified release"
            else:
                hit = next((p.name for p in live if matches(r.when, meta[p])), None)
            if hit:
                for p in live:
                    if p not in dec.excluded or (dec.excluded[p].action == "skip" and r.action == "ignore"):
                        dec.excluded[p] = Exclusion(r.action, r, hit)
                break
    dec.kept = [p for p in paths if p not in dec.excluded]
    return dec


def audio_files(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT
                  and not p.name.startswith("."))


def check_folder(folder, rules=None, release=None, overrides=None):
    return evaluate(audio_files(folder), rules, release, overrides if overrides is not None else State().overrides())


# --------------------------------------------------------------------------
# Remembered ignores and "process anyway"
# --------------------------------------------------------------------------
def signature(paths):
    """Changes when any file is added, removed, rewritten or retagged."""
    h = hashlib.sha1()
    for p in sorted(str(x) for x in paths):
        try:
            st = os.stat(p)
            h.update(f"{p}|{st.st_size}|{int(st.st_mtime)}\n".encode())
        except OSError:
            h.update(f"{p}|gone\n".encode())
    return h.hexdigest()[:16]


class State:
    def __init__(self, path=None):
        self.path = Path(path) if path else STATE_DIR / "state.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=60)
        self.db.execute("CREATE TABLE IF NOT EXISTS ignored (item TEXT PRIMARY KEY, rule_id TEXT, rule_name TEXT,"
                        " rules_hash TEXT, signature TEXT, ts REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS overrides (path TEXT PRIMARY KEY, ts REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS history (ts REAL, job TEXT, item TEXT, label TEXT, rule_id TEXT,"
                        " rule_name TEXT, detail TEXT)")
        self.db.commit()

    # ignores ----------------------------------------------------------------
    def remember(self, item, exclusion, sig, rhash):
        self.db.execute("INSERT OR REPLACE INTO ignored VALUES (?,?,?,?,?,?)",
                        (str(item), exclusion.rule.id, exclusion.rule.name, rhash, sig, time.time()))
        self.db.commit()

    def still_ignored(self, item, sig, rhash):
        """The item was ignored, and neither its files nor the rules changed since:
        no need to look at it again."""
        row = self.db.execute("SELECT signature, rules_hash, rule_name FROM ignored WHERE item=?",
                              (str(item),)).fetchone()
        if row and row[0] == sig and row[1] == rhash and str(item) not in self.overrides():
            return row[2]
        return None

    def forget(self, item):
        self.db.execute("DELETE FROM ignored WHERE item=?", (str(item),))
        self.db.commit()

    def ignored(self):
        return self.db.execute("SELECT item, rule_id, rule_name, ts FROM ignored ORDER BY item").fetchall()

    # process anyway ---------------------------------------------------------
    def allow(self, path):
        path = str(Path(path).expanduser().resolve())
        self.db.execute("INSERT OR REPLACE INTO overrides VALUES (?,?)", (path, time.time()))
        self.db.execute("DELETE FROM ignored WHERE item = ? OR substr(item, 1, ?) = ?",
                        (path, len(path) + 1, path + os.sep))
        self.db.commit()

    def disallow(self, path):
        self.db.execute("DELETE FROM overrides WHERE path=?", (str(Path(path).expanduser().resolve()),))
        self.db.commit()

    def overrides(self):
        return {r[0] for r in self.db.execute("SELECT path FROM overrides")}

    # history ----------------------------------------------------------------
    def log(self, job, item, exclusion, detail=""):
        self.db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?)",
                        (time.time(), job, str(item), exclusion.label, exclusion.rule.id, exclusion.rule.name,
                         detail or exclusion.where))
        self.db.commit()

    def log_raw(self, job, item, label, rule_id, rule_name, detail=""):
        self.db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?)",
                        (time.time(), job, str(item), label, rule_id, rule_name, detail))
        self.db.commit()

    def history(self, limit=200):
        return self.db.execute("SELECT ts, job, item, label, rule_name, detail FROM history ORDER BY ts DESC LIMIT ?",
                               (limit,)).fetchall()


def gate(folder_or_files, job, rules=None, release=None, process_anyway=False, state=None):
    """The one check every tool runs before touching an album (and again with
    the identified release before writing). Returns the Decision; remembers
    ignores and logs the exclusion in the history. process_anyway=True bypasses
    the rules for this job (it is logged too)."""
    state = state or State()
    files = audio_files(folder_or_files) if not isinstance(folder_or_files, (list, tuple)) else list(folder_or_files)
    rules = load_rules() if rules is None else rules
    if process_anyway:
        return Decision(kept=[Path(p) for p in files])
    dec = evaluate(files, rules, release, state.overrides())
    if dec.excluded:
        item = Path(folder_or_files) if not isinstance(folder_or_files, (list, tuple)) else Path(files[0]).parent
        rh, sig = rules_hash(rules), signature(files)
        whole = dec.summary()
        if whole is not None:
            if whole.action == "ignore":
                state.remember(item, whole, sig, rh)
            state.log(job, item, whole)
        else:
            for p, ex in dec.excluded.items():
                if ex.action == "ignore":
                    state.remember(p, ex, signature([p]), rh)
                state.log(job, p, ex)
    return dec
