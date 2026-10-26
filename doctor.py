"""doctor.py - what TagMender needs, whether it is there, and a diagnostics bundle.

  checks() -> [Check]       each with status ok / warn / fail and a plain explanation
  export(path, anonymize)   a .zip to attach to a bug report: the checks, your profiles and
                            rules, and the end of the log; never a token or key; with
                            anonymize=True, folder and file names are replaced by short
                            hashes (the same name always gets the same hash)
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import companions
import credentials
import exclusions as ex
import logsetup
import profiles as pf

PICARD_INI = "~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini"


@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail
    detail: str
    fix: str = ""


def _module(name):
    try:
        m = importlib.import_module(name)
        return getattr(m, "__version__", None) or getattr(m, "version", None) or "installed"
    except Exception:
        return None


def checks() -> list[Check]:
    out = []
    v = sys.version_info
    out.append(Check("python", "ok" if v >= (3, 10) else "fail", platform.python_version(),
                     "" if v >= (3, 10) else "TagMender needs Python 3.10 or newer"))
    for mod, need, fix in (("mutagen", True, "sudo apt install python3-mutagen  (or: pip install mutagen)"),
                           ("PIL", True, "sudo apt install python3-pil  (or: pip install Pillow)"),
                           ("fontTools", False, "sudo apt install python3-fonttools  (or: pip install fonttools)  "
                                                "- generated covers check their fonts have every letter of a title"),
                           ("PySide6", False, "pip install PySide6  (only for `tagmender gui`)"),
                           ("numpy", False, "sudo apt install python3-numpy  (transcode detection)"),
                           ("pyzbar", False, "pip install pyzbar + libzbar0  (barcodes on scans)"),
                           ("pykakasi", False, "pip install pykakasi  (Japanese title matching)")):
        ver = _module(mod)
        out.append(Check(mod, "ok" if ver else ("fail" if need else "warn"),
                         str(ver) if ver else "not installed", "" if ver else fix))
    for tool, why, fix in (("ffmpeg", "audio checks, ReplayGain, payload verification", "sudo apt install ffmpeg"),
                           ("fpcalc", "audio fingerprints (AcoustID)", "sudo apt install libchromaprint-tools"),
                           ("tesseract", "reading catalogue numbers from scans", "sudo apt install tesseract-ocr")):
        path = shutil.which(tool)
        out.append(Check(tool, "ok" if path else "warn", path or f"not found ({why})", "" if path else fix))
    for name in ("discogs", "acoustid", "fanart"):
        try:
            src = credentials.source(name)
            credentials.get(name)
            st = "ok" if src else ("fail" if name == "discogs" else "warn")
            out.append(Check(f"{name} key", st, f"set ({src})" if src else "not set",
                             "" if src else "tagmender setup"))
        except credentials.CredentialError as exc:
            out.append(Check(f"{name} key", "fail", "file readable by others", str(exc)))
    for label, repo, d, marker in (
            ("discogs-enrichment", "picard-discogs-enrichment", companions.discogs_dir(), "discogs_mapping.py"),
            ("coverup", "picard-coverup", companions.coverup_dir(), "pipeline.py")):
        found = (d / marker).exists()
        out.append(Check(label, "ok" if found else "fail",
                         f"{d} ({companions.version_of(d)})" if found else f"not found at {d}",
                         "" if found else f"git clone https://github.com/herculepirate/{repo} {d}"))
    try:
        ex.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        probe = ex.CONFIG_DIR / f".probe-{os.getpid()}"
        probe.write_text("x")
        probe.unlink()
        out.append(Check("config folder", "ok", str(ex.CONFIG_DIR)))
    except OSError as exc:
        out.append(Check("config folder", "fail", f"{ex.CONFIG_DIR}: {exc}"))
    try:
        ex.State()
        out.append(Check("state database", "ok", str(ex.STATE_DIR / "state.db")))
    except sqlite3.Error as exc:
        out.append(Check("state database", "fail", str(exc)))
    try:
        rules = ex.load_rules(include_disabled=True)
        out.append(Check("rules", "ok", f"{len(rules)} global rule(s)"))
    except ex.RuleError as exc:
        out.append(Check("rules", "fail", str(exc), "tagmender rules list"))
    for n in pf.names():
        try:
            up = pf.upgrade(pf.raw(n))
            problems = pf.validate(up) + pf.check_template(up)
        except pf.ProfileError as exc:
            problems = [str(exc)]
        out.append(Check(f"profile {n}", "fail" if problems else "ok", "; ".join(problems) or "valid",
                         f"tagmender profiles check {n}" if problems else ""))
        if not problems:
            p = pf.load(n)
            if p["operation"] in ("copy", "move"):
                dest = Path(p["destination"]).expanduser()
                mounted = dest.exists()
                out.append(Check(f"profile {n} destination", "ok" if mounted else "warn",
                                 str(dest) if mounted else f"{dest} is not there (unmounted?)",
                                 "" if mounted else "mount it; TagMender never creates a missing destination"))
    try:
        import executor
        pending = executor.pending_jobs()
        out.append(Check("interrupted jobs", "warn" if pending else "ok",
                         f"{len(pending)} job(s) need recovery: {', '.join(pending[:5])}" if pending else "none",
                         "tagmender recover" if pending else ""))
    except Exception as exc:                                   # executor missing or broken: say so
        out.append(Check("interrupted jobs", "warn", f"could not check: {exc}"))
    try:
        import picard_config
        s = picard_config.enrichment()
        src = picard_config.source()
        out.append(Check("Discogs Enrichment settings", "ok" if picard_config.from_ini() else "warn",
                         f"merge {s['merge_mode']}, styles {s['style_mode']}, marketplace "
                         f"{'on' if s['marketplace'] else 'off'} (from {src})",
                         "" if picard_config.from_ini() else
                         "open Picard > Options > Plugins > Discogs Enrichment once and press OK, "
                         "or set TAGMENDER_PICARD_INI"))
    except Exception as exc:
        out.append(Check("Discogs Enrichment settings", "warn", f"could not read: {exc}"))
    try:
        import picard_config
        t = picard_config.tag_settings()
        where = picard_config.ini_path()
        ver = ".".join(map(str, picard_config.picard_version())) if where else ""
        out.append(Check("Picard tagging settings", "ok",
                         f"artist names {t['standardize_artist_names']}"
                         f"{', translated (' + ', '.join(t['translation_locales']) + ')' if t['translate_artist_names'] else ''}"
                         f", release relationships {'on' if t['release_ars'] else 'off'}"
                         f", track relationships {'on' if t['track_ars'] else 'off'}"
                         f", ID3v2 {'as each file has it' if t['write_id3v23'] is None else ('v2.3' if t['write_id3v23'] else 'v2.4')}"
                         f", ID3v1 {'updated if there' if t['write_id3v1'] is None else ('written' if t['write_id3v1'] else 'removed')}"
                         + (f" (from {where}, Picard {ver or '?'}, with its enabled option profiles)" if where
                            else " (Picard 3's defaults: no Picard.ini)")))
    except Exception as exc:
        out.append(Check("Picard tagging settings", "warn", f"could not read: {exc}"))
    ini = Path(PICARD_INI).expanduser()
    if ini.exists():
        import tag_albums
        keep = tag_albums.picard_preserves_tagmender(ini)
        out.append(Check("Picard preserves TAGMENDER tags", "ok" if keep else "warn",
                         "yes" if keep else "no: Picard would remove TAGMENDER and TAGMENDER_REASON",
                         "" if keep else "Picard: Options > Tags > Preserve these tags: tagmender, tagmender_reason"))
    logf = logsetup.log_dir() / "tagmender.log"
    out.append(Check("log", "ok", f"{logf} ({logf.stat().st_size // 1024} KiB)" if logf.exists()
                     else f"{logf} (not written yet)"))
    return out


# --------------------------------------------------------------------------
# diagnostics export
# --------------------------------------------------------------------------
_PATH = re.compile(r"(?<![\w.])(/(?:[^\s'\"/:,;()\[\]]+/)*[^\s'\"/:,;()\[\]]+)")
_KEEP = {"usr", "bin", "lib", "etc", "home", "media", "mnt", "tmp", "var", "opt", "run", "proc", "dev"}


def anonymize(text: str) -> str:
    """Folder and file names -> short hashes; the home folder -> ~. File extensions and
    the top-level system folders stay readable."""
    home = str(Path.home())
    text = text.replace(home, "~")

    def one(m):
        parts = m.group(1).split("/")
        out = []
        for i, part in enumerate(parts):
            if not part or (i == 1 and part in _KEEP):
                out.append(part)
                continue
            stem, dot, ext = part.rpartition(".")
            if dot and 1 <= len(ext) <= 5 and ext.isalnum():
                out.append("h" + hashlib.sha1(stem.encode()).hexdigest()[:6] + "." + ext)
            else:
                out.append("h" + hashlib.sha1(part.encode()).hexdigest()[:6])
        return "/".join(out)
    return _PATH.sub(one, text)


def _tail(path: Path, max_bytes=2_000_000) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, path.stat().st_size - max_bytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def export(dest, anonymize_paths=False) -> Path:
    dest = Path(dest).expanduser()
    if dest.is_dir():
        dest = dest / f"tagmender-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    clean = (lambda t: anonymize(logsetup.redact(t))) if anonymize_paths else logsetup.redact
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("checks.json", clean(json.dumps([asdict(c) for c in checks()], indent=2, ensure_ascii=False)))
        z.writestr("system.txt", clean(f"{platform.platform()}\nPython {platform.python_version()}\n"
                                       f"TagMender at {Path(__file__).resolve().parent}\n"))
        rules = ex.rules_file()
        if rules.exists():
            z.writestr("config/exclusions.json", clean(rules.read_text(encoding="utf-8")))
        for n in pf.names():
            z.writestr(f"config/profiles/{n}.json", clean(pf.path_of(n).read_text(encoding="utf-8")))
        for f in sorted(logsetup.log_dir().glob("tagmender.log*")):
            if f.suffix != ".lock":
                z.writestr(f"logs/{f.name}", clean(_tail(f)))
        # credential files are never read here
    return dest
