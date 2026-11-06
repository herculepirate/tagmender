"""plugins.py - plugins: code other people write that adds to TagMender (docs/PLUGINS.md).

A plugin is a folder (or a .zip holding one) with two files:

  plugin.toml     id, name, version, api (= API_VERSION), author, description, license,
                  homepage, requires (Python modules it needs), and [[settings]] it asks for
  __init__.py     def register(api): ...  - says what the plugin adds, through `api`

Installed plugins live in ~/.local/share/tagmender/plugins/<id>/ and start switched off; which
are on is this computer's setting (app_settings "plugins.enabled"), never a profile's. What a
plugin can add (API 1):

  api.naming_function(name, fn)   a $name(...) for naming and tagger scripts
  api.tag_processor(fn)           fn(tags, info) -> tags: changes an album's tags; runs after the
                                  profile's tagger scripts, so its changes are in the plan (shown,
                                  backed up, undoable) - a plugin never writes a file itself
  api.setting(key)                the value of one of its [[settings]] (secrets: a 0600 file)
  api.log                         a logger of its own

A plugin that fails to load, or raises, is switched off and the error kept (status()); set
TAGMENDER_NO_PLUGINS=1 (or `tagmender --no-plugins`) to start without any.

Two plugins come with TagMender (BUILTIN, in plugins_builtin/): Discogs (identifying and tagging
from Discogs) and CoverUp (finding and choosing cover art). They are part of TagMender's own
code rather than API plugins: they are listed with the others, start switched on, can be switched
off (app_settings "plugins.builtin_off") but not removed, and TAGMENDER_NO_PLUGINS leaves them on.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import sys
import stat
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib                      # (Python 3.11+)
except ImportError:                     # pragma: no cover
    try:
        import tomli as tomllib         # (Python 3.10: pip install tomli / apt install python3-tomli)
    except ImportError:
        tomllib = None

API_VERSION = 1
MANIFEST = "plugin.toml"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
SETTING_KINDS = ("text", "secret", "switch")
log = logging.getLogger("tagmender.plugins")
log.addHandler(logging.NullHandler())      # (said once by the command line, not again by Python's own last resort)


def N_(text):                           # (a text the app translates when it shows it: gui/i18n)
    return text


BUILTIN_DIR = Path(__file__).resolve().parent / "plugins_builtin"
BUILTIN = {
    "discogs": {"id": "discogs", "name": "Discogs", "version": "0.3.8", "author": "TagMender",
                "license": "GPL-2.0-or-later", "builtin": True,
                "description": N_("Identifies albums on Discogs and fills in their tags (needs a Discogs token, "
                                  "set in Setup). Off: albums are identified with MusicBrainz only.")},
    "coverup": {"id": "coverup", "name": "CoverUp", "version": "0.6.1", "author": "TagMender",
                "license": "GPL-2.0-or-later", "builtin": True,
                "description": N_("Finds, checks and chooses cover art from shops, the web and the album's own "
                                  "scans. Off: covers come from the Cover Art Archive and Discogs only, and "
                                  "those already there are kept.")},
}


def builtin_on(pid: str) -> bool:
    """Whether the built-in plugin PID is switched on (they are unless switched off)."""
    try:
        import app_settings
        off = app_settings.get("plugins.builtin_off") or []
    except Exception:                  # (unreadable settings: as they come)
        off = []
    return pid not in off


def plugins_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "tagmender" / "plugins"


class PluginError(Exception):
    pass


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    folder: Path
    manifest: dict
    enabled: bool = False
    loaded: bool = False
    builtin: bool = False
    error: str = ""
    adds: list = field(default_factory=list)       # what register() added, in words


# ---- reading and checking -------------------------------------------------------------------
def read_manifest(folder: Path) -> dict:
    """The plugin.toml in FOLDER, checked; PluginError says what is wrong."""
    f = Path(folder) / MANIFEST
    if tomllib is None:
        raise PluginError("plugins need Python 3.11 or later, or the tomli module on Python 3.10 "
                          "(sudo apt install python3-tomli, or pip install tomli)")
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PluginError(f"no {MANIFEST} in {folder}") from None
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"{MANIFEST} cannot be read: {exc}") from None
    problems = check_manifest(data)
    if not (Path(folder) / "__init__.py").is_file():
        problems.append("__init__.py is missing (it holds register(api))")
    if problems:
        raise PluginError("; ".join(problems))
    return data


def check_manifest(data: dict) -> list[str]:
    p = []
    pid = data.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        p.append("id must be 2-40 lower-case letters, digits, - or _, starting with a letter")
    for key in ("name", "version", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            p.append(f"{key} is missing")
    if data.get("api") != API_VERSION:
        p.append(f"api must be {API_VERSION} (this TagMender's plugin API); it says {data.get('api')!r}")
    req = data.get("requires", [])
    if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
        p.append("requires must be a list of Python module names")
    keys = set()
    for i, s in enumerate(data.get("settings", []) if isinstance(data.get("settings", []), list) else [None]):
        if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not re.match(r"^[a-z][a-z0-9_]*$", s["key"]):
            p.append(f"settings[{i}] needs a key of lower-case letters, digits and _")
            continue
        if s["key"] in keys:
            p.append(f"settings[{i}]: the key {s['key']!r} is there twice")
        keys.add(s["key"])
        if s.get("kind", "text") == "switch" and "default" in s and not isinstance(s["default"], bool):
            p.append(f"settings[{i}]: a switch's default must be true or false")
        elif s.get("kind", "text") in ("text", "secret") and "default" in s and not isinstance(s["default"], str):
            p.append(f"settings[{i}]: a text setting's default must be text")
        if s.get("kind", "text") not in SETTING_KINDS:
            p.append(f"settings[{i}].kind must be one of {', '.join(SETTING_KINDS)}")
        if not isinstance(s.get("label"), str) or not s["label"].strip():
            p.append(f"settings[{i}] needs a label")
    return p


def missing_modules(data: dict) -> list[str]:
    def there(m):
        try:
            return importlib.util.find_spec(m) is not None
        except (ImportError, ValueError):       # ("pkg.sub" with pkg missing, or a bad name)
            return False
    return [m for m in data.get("requires", []) if not there(m)]


def enabled_ids() -> list[str]:
    import app_settings
    v = app_settings.get("plugins.enabled") or []
    return [x for x in v if isinstance(x, str)]


def installed() -> list[Plugin]:
    """The built-in plugins, then every plugin in plugins_dir(), with whether it is on and why it
    cannot be used."""
    on = set(enabled_ids())
    out = []
    import companions
    for pid, m in BUILTIN.items():
        e = builtin_on(pid)
        d, marker = {"discogs": (companions.discogs_dir(), "discogs_mapping.py"),
                     "coverup": (companions.coverup_dir(), "pipeline.py")}[pid]
        gone = "" if (d / marker).exists() else f"its code is missing from {d}: reinstall TagMender"
        out.append(Plugin(pid, m["name"], m["version"], d, dict(m), enabled=e, loaded=e and not gone,
                          error=gone, builtin=True))
    root = plugins_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            m = read_manifest(d)
            p = Plugin(m["id"], m["name"], m["version"], d, m, enabled=m["id"] in on)
            if m["id"] != d.name:
                p.error = f"its folder is named {d.name!r}, not its id {m['id']!r}"
            elif m["id"] in BUILTIN:
                p.error = f"{m['id']!r} is the id of a plugin that comes with TagMender"
        except PluginError as exc:                # (switched on but unreadable: still on, its error said)
            p = Plugin(d.name, d.name, "?", d, {}, enabled=d.name in on, error=str(exc))
        p.error = p.error or _LOADED.get(p.id, Plugin(p.id, "", "", d, {})).error
        p.loaded = p.id in _LOADED and not _LOADED[p.id].error
        out.append(p)
    return out


# ---- installing -----------------------------------------------------------------------------
def install(source) -> Plugin:
    """Install a plugin from a folder or a .zip (switched off). One already there with the same
    id is replaced, the old one kept in plugins/.removed/."""
    src = Path(source).expanduser()
    tmp = None
    try:
        import zlib  # noqa: F401  (its error, for a damaged zip)
        if src.is_file() and zipfile.is_zipfile(src):
            tmp = plugins_dir() / f".unpack-{os.getpid()}"
            shutil.rmtree(tmp, ignore_errors=True)
            with zipfile.ZipFile(src) as z:
                for n in z.namelist():                 # (nothing outside the folder it unpacks to)
                    if n.startswith("/") or ".." in Path(n).parts:
                        raise PluginError(f"the zip holds an unsafe path: {n}")
                try:
                    z.extractall(tmp)
                except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
                    raise PluginError(f"the zip cannot be read: {exc}") from None
            found = [m.parent for m in tmp.rglob(MANIFEST) if len(m.relative_to(tmp).parts) <= 2]
            if len(found) != 1:
                raise PluginError(f"the zip must hold one plugin ({MANIFEST} at its top or in one folder)")
            src_dir = found[0]
        elif src.is_dir():
            src_dir = src
        else:
            raise PluginError(f"{src} is not a plugin folder or a .zip")
        m = read_manifest(src_dir)
        if m["id"] in BUILTIN:
            raise PluginError(f"{m['id']!r} is the id of a plugin that comes with TagMender; choose another")
        dst = plugins_dir() / m["id"]
        if dst.exists() and src_dir.resolve() == dst.resolve():
            raise PluginError("that is the installed plugin's own folder: install it from your own copy")
        was_on = dst.exists() and m["id"] in enabled_ids()     # (an update of one switched on: stays on)
        if dst.exists():
            _set_aside(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        set_enabled(m["id"], was_on)
        return Plugin(m["id"], m["name"], m["version"], dst, m, enabled=was_on)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def remove(pid: str) -> Path:
    """Switch the plugin off and move its folder to plugins/.removed/ (put it back by moving it)."""
    d = plugins_dir() / pid
    if pid in BUILTIN and not d.is_dir():                # (an installed one using its id can still go)
        raise PluginError(f"{BUILTIN[pid]['name']} comes with TagMender: switch it off instead")
    if not d.is_dir():
        raise PluginError(f"no plugin {pid!r} is installed")
    if pid not in BUILTIN:                               # (never the built-in one's switch)
        set_enabled(pid, False)
    return _set_aside(d)


def _set_aside(d: Path) -> Path:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    keep, n = d.parent / ".removed" / f"{d.name}-{stamp}", 1
    while keep.exists():                      # (twice in one second: a folder of its own each time)
        n += 1
        keep = d.parent / ".removed" / f"{d.name}-{stamp}-{n}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(keep))
    return keep


def set_enabled(pid: str, on: bool):
    import app_settings
    if pid in BUILTIN:
        off = [x for x in (app_settings.get("plugins.builtin_off") or []) if x != pid] + ([] if on else [pid])
        app_settings.set_value("plugins.builtin_off", off)
        return
    now = [x for x in enabled_ids() if x != pid] + ([pid] if on else [])
    app_settings.set_value("plugins.enabled", now)


# ---- loading and the API --------------------------------------------------------------------
_LOADED: dict[str, Plugin] = {}
_PROCESSORS: list = []                  # (plugin id, fn)
_FUNCTIONS: dict = {}                   # script function name -> the plugin id that added it


class Api:
    """What a plugin's register(api) is given."""

    def __init__(self, plugin: Plugin):
        self._p = plugin
        self.log = logging.getLogger(f"tagmender.plugin.{plugin.id}")
        self.api_version = API_VERSION

    def naming_function(self, name: str, fn, eval_args=True, documentation=""):
        """A $name(...) script function. A name TagMender already has is refused."""
        import naming
        if not re.match(r"^[a-z_][a-z0-9_]*$", name or ""):
            raise PluginError(f"a script function's name must be lower-case letters, digits and _: {name!r}")
        if name in naming.functions() and _FUNCTIONS.get(name) != self._p.id:   # (its own, loaded again: fine)
            owner = _FUNCTIONS.get(name)
            raise PluginError(f"${name} is a script function the plugin {owner!r} already has" if owner else
                              f"${name} is a script function TagMender already has")
        _FUNCTIONS[name] = self._p.id
        from tagscript._shim import register_script_function
        register_script_function(_guarded(self._p, fn), name=name, eval_args=eval_args,
                                 documentation=documentation or f"From the plugin {self._p.name}.")
        self._p.adds.append(f"${name}()")

    def tag_processor(self, fn):
        """fn(tags, info) -> tags. tags: {name: [values]} as the plan will write them; info:
        {"file", "album_folder", "profile"}. Return the tags to write (changed or not)."""
        _PROCESSORS.append((self._p.id, fn))
        self._p.adds.append("tag changes")

    def setting(self, key: str):
        spec = next((s for s in self._p.manifest.get("settings", []) if s.get("key") == key), None)
        if spec is None:
            raise PluginError(f"{key!r} is not one of the plugin's settings")
        return setting_value(self._p.id, spec)


def setting_value(pid: str, spec: dict):
    """A plugin setting's value: secrets from a 0600 file, the rest from this computer's settings."""
    if spec.get("kind") == "secret":
        return get_secret(pid, spec["key"])
    import app_settings
    values = (app_settings.get("plugins.settings") or {}).get(pid) or {}
    return values.get(spec["key"], spec.get("default", False if spec.get("kind") == "switch" else ""))


def _secret_file(pid: str, key: str) -> Path:
    import exclusions as ex
    return ex.CONFIG_DIR / "plugins" / pid / key


def get_secret(pid: str, key: str) -> str:
    """A plugin's secret setting, from a file only you can read ("" when none; a file others
    can read is refused, as the keys are)."""
    f = _secret_file(pid, key)
    if not f.exists():
        return ""
    if f.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PluginError(f"{f} can be read by other users; run: chmod 600 '{f}'")
    return f.read_text(encoding="utf-8").strip()


def store_secret(pid: str, key: str, value: str) -> Path:
    f = _secret_file(pid, key)
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(f.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(value.strip() + "\n")
    os.replace(f.with_suffix(".tmp"), f)
    return f


def forget_secret(pid: str, key: str) -> bool:
    """The secret KEY of plugin PID forgotten (its file removed); False when none was saved."""
    f = _secret_file(pid, key)
    try:
        f.unlink()
        return True
    except FileNotFoundError:
        return False


def forget(pid: str):
    """Plugin PID as if not loaded yet in this process, so switching it on loads it again (after a
    failure, or once its files were fixed). Its script functions stay registered until replaced."""
    _LOADED.pop(pid, None)
    _PROCESSORS[:] = [(p, f) for p, f in _PROCESSORS if p != pid]
    name = f"tagmender_plugin_{pid.replace('-', '_')}"
    for m in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[m]                    # (its files read again, not the copy from before)
    for cache in (plugins_dir() / pid).rglob("__pycache__"):   # (an edit of the same size within a
        shutil.rmtree(cache, ignore_errors=True)               #  second: never the old bytecode)
    importlib.invalidate_caches()


def _guarded(plugin: Plugin, fn):
    """fn, switched off with the error kept when it raises (the script then gets "")."""
    import functools

    @functools.wraps(fn)                      # (__wrapped__: the script checks fn's own argument count)
    def call(*a, **kw):
        if plugin.error:
            return ""
        try:
            got = fn(*a, **kw)
        except Exception as exc:              # (a plugin's mistake never stops a run)
            _fail(plugin, f"{type(exc).__name__}: {exc}")
            return ""
        if got is None:
            return ""
        if not isinstance(got, str):          # (a script function gives text: else it is switched off)
            _fail(plugin, f"${getattr(fn, '__name__', 'function')} gave {type(got).__name__}, not text")
            return ""
        return got
    return call


_FAILURES: list = []                    # (plugin id, why, thread), every failure in this process, in order


def failures() -> list:
    """Every (plugin id, why) a plugin failed with so far on this thread (a plan counts its own:
    a look-up or the watcher on another thread is not its album)."""
    import threading
    me = threading.get_ident()
    return [(pid, why) for pid, why, th in _FAILURES if th == me]


def working() -> set:
    """The plugins loaded and working now (taken when an album's planning starts: broken_since)."""
    return {pid for pid, p in _LOADED.items() if p.loaded and not p.error}


def broken_since(was: set) -> list:
    """(plugin id, why) of the plugins in WAS that have failed since, on whichever thread: an
    album planned while one of them stopped has its changes on some tracks only."""
    return [(pid, _LOADED[pid].error) for pid in sorted(was) if _LOADED.get(pid) and _LOADED[pid].error]


def _fail(plugin: Plugin, why: str):
    import threading
    _FAILURES.append((plugin.id, why, threading.get_ident()))
    plugin.error = why
    log.warning("plugin %s switched off: %s", plugin.id, why)
    _PROCESSORS[:] = [(pid, f) for pid, f in _PROCESSORS if pid != plugin.id]


def disabled_by_environment() -> bool:
    return os.environ.get("TAGMENDER_NO_PLUGINS", "") not in ("", "0")


def load_enabled() -> list[Plugin]:
    """Load every plugin that is switched on (once per process). Returns them, each with its
    error ("" when loaded)."""
    if disabled_by_environment():
        return []
    out = []
    for p in installed():
        if p.builtin or not p.enabled or p.id in _LOADED:
            continue
        _LOADED[p.id] = p
        out.append(p)
        if p.error:
            continue
        gone = missing_modules(p.manifest)
        if gone:
            p.error = f"needs the Python module(s) {', '.join(gone)}"
            continue
        try:
            name = f"tagmender_plugin_{p.id.replace('-', '_')}"
            spec = importlib.util.spec_from_file_location(name, p.folder / "__init__.py",
                                                          submodule_search_locations=[str(p.folder)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "register", None)):
                raise PluginError("__init__.py has no register(api)")
            mod.register(Api(p))
            p.loaded = True
        except Exception as exc:              # (a plugin that fails is switched off, never fatal)
            _fail(p, f"{type(exc).__name__}: {exc}")
    return out


def has_tag_processors() -> bool:
    return bool(_PROCESSORS)


def process_tags(tags: dict, info: dict) -> dict:
    """The tags after every loaded plugin's tag processor, in load order."""
    for pid, fn in list(_PROCESSORS):
        try:
            got = fn({k: list(v) for k, v in tags.items()}, dict(info))
            if not isinstance(got, dict):
                raise PluginError("a tag processor must return the tags (a dict)")
            tags = {str(k): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in got.items()}
        except Exception as exc:
            owner = _LOADED.get(pid) or Plugin(pid, pid, "?", plugins_dir() / pid, {})   # (forgotten meanwhile)
            _fail(owner, f"{type(exc).__name__}: {exc}")
    return tags


def status(pid: str) -> str:
    """"on", "off" or "error: ..." for the plugin PID."""
    p = next((x for x in installed() if x.id == pid), None)
    if p is None:
        return "not installed"
    if p.error:
        return f"error: {p.error}"
    return "on" if p.enabled else "off"


# ---- for authors ------------------------------------------------------------------------------
TEMPLATE_TOML = '''id = "{id}"
name = {name}
version = "0.1.0"
api = {api}
author = ""
description = "What this plugin adds, in one sentence."
license = "GPL-2.0-or-later"
homepage = ""
requires = []

# settings shown under the plugin in Preferences > Plugins (kind: text, secret or switch)
# [[settings]]
# key = "style"
# label = "Style"
# kind = "text"
# default = ""
# help = "What it changes."
'''

TEMPLATE_INIT = '''"""{name}: a TagMender plugin (docs/PLUGINS.md)."""


def register(api):
    # a script function: ${fn}(text) -> TEXT! (named after the plugin: two plugins never share one)
    api.naming_function("{fn}", lambda parser, text: text.upper() + "!")

    # change an album's tags before they are written (the change shows in the plan)
    def tidy(tags, info):
        if "title" in tags:
            tags["title"] = [t.strip() for t in tags["title"]]
        return tags

    api.tag_processor(tidy)
'''


def new(name: str, where=".") -> Path:
    """A new plugin folder NAME under WHERE, with a plugin.toml and an __init__.py to start from."""
    pid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    if not ID_RE.match(pid):
        raise PluginError(f"{name!r} gives the id {pid!r}: use letters and digits, starting with a letter")
    d = Path(where).expanduser() / pid
    if d.exists():
        raise PluginError(f"{d} is there already")
    d.mkdir(parents=True)
    import json
    (d / MANIFEST).write_text(TEMPLATE_TOML.format(id=pid, name=json.dumps(name, ensure_ascii=False),
                                                   api=API_VERSION), encoding="utf-8")   # (a TOML string)
    fn = re.sub(r"[^a-z0-9_]", "_", pid) + "_shout"
    (d / "__init__.py").write_text(TEMPLATE_INIT.format(name=name.replace('"', "'"), fn=fn), encoding="utf-8")
    return d


def check(folder) -> list[str]:
    """What is wrong with the plugin in FOLDER ([] when it loads and registers cleanly)."""
    try:
        m = read_manifest(Path(folder))
    except PluginError as exc:
        return [str(exc)]
    if m["id"] in BUILTIN:
        return [f"{m['id']!r} is the id of a plugin that comes with TagMender; choose another"]
    gone = missing_modules(m)
    if gone:
        return [f"needs the Python module(s) {', '.join(gone)}"]
    p = Plugin(m["id"], m["name"], m["version"], Path(folder), m)
    name = f"tagmender_check_{m['id'].replace('-', '_')}_{os.getpid()}"
    try:                                      # (loaded as load_enabled does: its own modules import)
        spec = importlib.util.spec_from_file_location(name, Path(folder) / "__init__.py",
                                                      submodule_search_locations=[str(folder)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        if not callable(getattr(mod, "register", None)):
            return ["__init__.py has no register(api)"]
        mod.register(_CheckApi(p))
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]
    return []


class _CheckApi(Api):
    """Api for `plugins check`: registers nothing for real."""

    def naming_function(self, name, fn, eval_args=True, documentation=""):
        import naming
        if not re.match(r"^[a-z_][a-z0-9_]*$", name or "") or name in naming.functions():
            raise PluginError(f"${name} cannot be used as a script function's name")
        if not callable(fn):
            raise PluginError(f"${name}: not a function")

    def tag_processor(self, fn):
        if not callable(fn):
            raise PluginError("tag_processor: not a function")
