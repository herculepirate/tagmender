"""doctor.py - what TagMender needs, whether it is there, and a diagnostics bundle.

  checks() -> [Check]       each with status ok / warn / fail and a plain explanation
  connection_checks()       one live request to MusicBrainz, Discogs, AcoustID and the Cover
                            Art Archive each (`doctor --connection`; connection.py)
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

@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail
    detail: str
    fix: str = ""
    group: str = ""        # for what a user sets up (requirements.py): recommended | optional


def registry_checks() -> list[Check]:
    """One check per key, login, program and module of requirements.py, with its group; the
    fix is its notice (what is not used, and where to set it). Never a value."""
    import requirements as rq
    out = []
    for row in rq.report():
        e, state = rq.entry(row["id"]), row["status"]
        if e.web_search:
            continue                          # (web_search_checks says those, with their profiles)
        if state == rq.UNSAFE:
            status, detail = "fail", "file readable by others"
        elif row["ready"]:
            status = "ok"
            if state == rq.SET:
                detail = f"set ({row['source']})"
            elif e.kind == "program":
                detail = shutil.which(e.id) or "installed"
            else:
                detail = "installed" if state == rq.INSTALLED else "shared public key"
        else:
            status = "fail" if e.id == "discogs" else "warn"
            detail = ("not set" if e.kind == "key" else "not logged in" if e.kind == "login"
                      else "not installed") + ("" if e.group == rq.RECOMMENDED else " (optional)")
        out.append(Check(e.check or e.id, status, detail, row["notice"], group=e.group))
    return out


def _module(name):
    try:
        m = importlib.import_module(name)
        return getattr(m, "__version__", None) or getattr(m, "version", None) or "installed"
    except Exception:
        return None


def checks() -> list[Check]:
    """Everything doctor reports."""
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
                           ("pykakasi", False, "pip install pykakasi  (Japanese title matching)")):
        ver = _module(mod)
        out.append(Check(mod, "ok" if ver else ("fail" if need else "warn"),
                         str(ver) if ver else "not installed", "" if ver else fix))
    out += registry_checks()
    for label, repo, d, marker in (
            ("Discogs Enrichment", "picard-discogs-enrichment", companions.discogs_dir(), "discogs_mapping.py"),
            ("CoverUp", "picard-coverup", companions.coverup_dir(), "pipeline.py")):
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
        import tagger_settings
        s = tagger_settings.enrichment()
        src = tagger_settings.source_label()
        out.append(Check("Discogs Enrichment settings", "ok" if tagger_settings.from_ini() else "warn",
                         f"styles {s['style_mode']}, marketplace "
                         f"{'on' if s['marketplace'] else 'off'} (from {src}); the merge mode follows "
                         "each profile's Sources setting",
                         "" if tagger_settings.from_ini() else
                         "no tagger settings were found, so the defaults are used; to use your own, "
                         "set TAGMENDER_TAGGER_INI to your tagger's settings file"))
    except Exception as exc:
        out.append(Check("Discogs Enrichment settings", "warn", f"could not read: {exc}"))
    try:
        import tagger_settings
        t = tagger_settings.tag_settings()
        where = tagger_settings.ini_path()
        ver = ".".join(map(str, tagger_settings.picard_version())) if where else ""
        out.append(Check("tagging settings", "ok",
                         f"artist names {t['standardize_artist_names']}"
                         f"{', translated (' + ', '.join(t['translation_locales']) + ')' if t['translate_artist_names'] else ''}"
                         f", release relationships {'on' if t['release_ars'] else 'off'}"
                         f", track relationships {'on' if t['track_ars'] else 'off'}"
                         f", ID3v2 {'as each file has it' if t['write_id3v23'] is None else ('v2.3' if t['write_id3v23'] else 'v2.4')}"
                         f", ID3v1 {'updated if there' if t['write_id3v1'] is None else ('written' if t['write_id3v1'] else 'removed')}"
                         + (f" (settings imported from an existing tagger installation, version "
                            f"{ver or '?'}, with its enabled option profiles)" if where else " (the defaults: no tagger settings found)")))
    except Exception as exc:
        out.append(Check("tagging settings", "warn", f"could not read: {exc}"))
    import tagger_settings
    ini = tagger_settings.ini_path()          # (the same settings file "tagging settings" read)
    if ini is not None:
        import tag_albums
        keep = tag_albums.picard_preserves_tagmender(ini)
        out.append(Check("other tagger keeps TAGMENDER tags", "ok" if keep else "warn",
                         "yes" if keep else "no: the tagger installed here would remove TAGMENDER and "
                                            "TAGMENDER_REASON when it saves",
                         "" if keep else "in that tagger's tag options, add tagmender, tagmender_reason to "
                                         "the tags it preserves"))
    out += web_search_checks()
    out += provider_health(ex.STATE_DIR / "cache.sqlite")
    logf = logsetup.log_dir() / "tagmender.log"
    out.append(Check("log", "ok", f"{logf} ({logf.stat().st_size // 1024} KiB)" if logf.exists()
                     else f"{logf} (not written yet)"))
    return out


# what to do when a service does not answer as it should
_CONNECTION_FIX = {
    "busy": "it is overloaded or limits requests; TagMender waits and tries again, so a run is slower meanwhile",
    "unreachable": "check the network connection (and a proxy or firewall); a run meanwhile leaves the albums "
                   "that need it to try again later",
    "refused": "set the key again in Setup (tagmender setup NAME); it may have been revoked",
}


def connection_checks(opener=None) -> list[Check]:
    """A live Test Connection of each service (connection.test): ok when it answers (or
    answers without a key set up: nothing to fix), warn when busy, unreachable or a key is
    refused. Keys are sent only to their own service and never shown."""
    import connection
    out = []
    for r in connection.test_all(opener=opener):
        state = "ok" if r.state in (connection.OK, connection.NO_KEY) else "warn"
        out.append(Check(f"{r.title} connection", state, r.detail, _CONNECTION_FIX.get(r.state, "")))
    return out


def _ago(secs):
    secs = max(0, int(secs))
    return f"{secs // 86400} days" if secs >= 86400 else f"{secs // 3600} h" if secs >= 3600 else f"{secs // 60} min"


def web_search_checks() -> list[Check]:
    """The optional web search engines: each profile's chain (tried in order), which engines
    are set up, this month's searches against each one's cap and which engine answered last."""
    import match_albums as ma
    using, chains = {}, []
    for n in pf.names():
        try:
            ws = pf.load(n).web_search()
        except Exception:
            continue
        chain = ma.web_engines({"web_search": ws})
        if not chain:
            continue
        for engine, cap in chain:
            using.setdefault(engine, []).append((n, cap))
        added = " (marginalia added: Tavily is never the only engine)" \
            if "marginalia" not in ws["engines"] and "marginalia" in dict(chain) else ""
        chains.append(f"{n}: " + ", then ".join(f"{e} {ma.web_search_count(e)}/{c}" for e, c in chain) + added)
    out, answers = [], ma.web_search_answers()
    if not using:
        out.append(Check("web search", "ok", "off in every profile (the default: MusicBrainz, Discogs and the "
                                             "shops' free APIs only)"))
    else:
        last = max(answers.items(), key=lambda kv: kv[1][0], default=None)
        said = (f"last answer: {last[0]} at {time.strftime('%Y-%m-%d %H:%M', time.localtime(last[1][0]))}"
                f"{' (a release link)' if last[1][1] else ' (no release link)'}" if last else "no answer yet")
        out.append(Check("web search chain", "ok", "tried in order, searches this month/cap: "
                                                   + "; ".join(chains) + f"; {said}"))
    for engine in ma.WEB_ENGINES:
        src = credentials.source(engine)
        users = using.get(engine, [])
        _value, problem = ma.web_engine_setting(engine)
        if not src and not users and engine != "marginalia":
            continue
        if engine == "marginalia" and not src and not users:
            continue                                   # the shared key needs nothing set up: said when used
        count = ma.web_search_count(engine)
        cap = min((c for _n, c in users), default=ma.WEB_CAPS[engine])
        detail = (f"{'set (' + src + ')' if src else 'shared public key'}; "
                  f"{'used by ' + ', '.join(n for n, _c in users) if users else 'no profile uses it'}; "
                  f"{count} of {cap} searches this month")
        status, fix = ("warn", problem) if problem else ("ok", "")
        if engine in answers:
            detail += "; last answered " + time.strftime("%Y-%m-%d %H:%M", time.localtime(answers[engine][0]))
        if users and count >= cap:
            status = "warn"
            fix = fix or ("the cap is reached: this engine hands over to the next in the chain until next "
                          "month (providers.web.search.monthly_cap)")
        if engine == "tavily":
            detail += " (a paid AI search)"
        out.append(Check(f"web search {engine}", status, detail, fix))
    return out


def provider_health(cache_path, now=None) -> list[Check]:
    """One check per provider the matcher has called (match_albums.Http's provider_health
    table in the cache): warn when its last 3 calls or more failed and none succeeded
    since, with the last error (a host down, a bad hostname, an SSL mismatch)."""
    path = Path(cache_path)
    if not path.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = db.execute("SELECT host, fails, last_error, last_fail, last_ok FROM provider_health "
                              "ORDER BY host").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []                          # an older cache without the table: nothing recorded yet
    now = time.time() if now is None else now
    out = []
    for host, fails, err, last_fail, last_ok in rows:
        if (fails or 0) >= 3 and (last_fail or 0) > (last_ok or 0):
            out.append(Check(f"{host} reachability", "warn",
                             f"failed {fails} times in a row, last {_ago(now - last_fail)} ago: {err}",
                             f"check the network/hostname; results meanwhile lack {host}'s evidence"))
        else:
            out.append(Check(f"{host} reachability", "ok",
                             f"answered {_ago(now - last_ok)} ago" if last_ok else "no failures in a row"))
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
        z.writestr("checks.json", clean(json.dumps([asdict(c) for c in checks()], indent=2,
                                                    ensure_ascii=False)))
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
