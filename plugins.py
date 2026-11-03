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
    import tomllib                      # (Python 3.11+; on 3.10 plugins are not available)
except ImportError:                     # pragma: no cover
    tomllib = None

API_VERSION = 1
MANIFEST = "plugin.toml"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
SETTING_KINDS = ("text", "secret", "switch")
log = logging.getLogger("tagmender.plugins")


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
    error: str = ""
    adds: list = field(default_factory=list)       # what register() added, in words


# ---- reading and checking -------------------------------------------------------------------
def read_manifest(folder: Path) -> dict:
    """The plugin.toml in FOLDER, checked; PluginError says what is wrong."""
    f = Path(folder) / MANIFEST
    if tomllib is None:
        raise PluginError("plugins need Python 3.11 or later")
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
        if s.get("kind", "text") not in SETTING_KINDS:
            p.append(f"settings[{i}].kind must be one of {', '.join(SETTING_KINDS)}")
        if not isinstance(s.get("label"), str) or not s["label"].strip():
            p.append(f"settings[{i}] needs a label")
    return p


def missing_modules(data: dict) -> list[str]:
    return [m for m in data.get("requires", []) if importlib.util.find_spec(m) is None]


def enabled_ids() -> list[str]:
    import app_settings
    v = app_settings.get("plugins.enabled") or []
    return [x for x in v if isinstance(x, str)]


def installed() -> list[Plugin]:
    """Every plugin in plugins_dir(), with whether it is on and why it cannot be used."""
    on = set(enabled_ids())
    out = []
    root = plugins_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            m = read_manifest(d)
            p = Plugin(m["id"], m["name"], m["version"], d, m, enabled=m["id"] in on)
            if m["id"] != d.name:
                p.error = f"its folder is named {d.name!r}, not its id {m['id']!r}"
        except PluginError as exc:
            p = Plugin(d.name, d.name, "?", d, {}, error=str(exc))
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
        if src.is_file() and zipfile.is_zipfile(src):
            tmp = plugins_dir() / f".unpack-{os.getpid()}"
            shutil.rmtree(tmp, ignore_errors=True)
            with zipfile.ZipFile(src) as z:
                for n in z.namelist():                 # (nothing outside the folder it unpacks to)
                    if n.startswith("/") or ".." in Path(n).parts:
                        raise PluginError(f"the zip holds an unsafe path: {n}")
                z.extractall(tmp)
            found = [m.parent for m in tmp.rglob(MANIFEST) if len(m.relative_to(tmp).parts) <= 2]
            if len(found) != 1:
                raise PluginError(f"the zip must hold one plugin ({MANIFEST} at its top or in one folder)")
            src_dir = found[0]
        elif src.is_dir():
            src_dir = src
        else:
            raise PluginError(f"{src} is not a plugin folder or a .zip")
        m = read_manifest(src_dir)
        dst = plugins_dir() / m["id"]
        if dst.exists():
            _set_aside(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        set_enabled(m["id"], False)
        return Plugin(m["id"], m["name"], m["version"], dst, m)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def remove(pid: str) -> Path:
    """Switch the plugin off and move its folder to plugins/.removed/ (put it back by moving it)."""
    d = plugins_dir() / pid
    if not d.is_dir():
        raise PluginError(f"no plugin {pid!r} is installed")
    set_enabled(pid, False)
    return _set_aside(d)


def _set_aside(d: Path) -> Path:
    keep = d.parent / ".removed" / f"{d.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(keep))
    return keep


def set_enabled(pid: str, on: bool):
    import app_settings
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
            raise PluginError(f"${name} is a script function TagMender already has")
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


def _guarded(plugin: Plugin, fn):
    """fn, switched off with the error kept when it raises (the script then gets "")."""
    def call(*a, **kw):
        if plugin.error:
            return ""
        try:
            return fn(*a, **kw)
        except Exception as exc:              # (a plugin's mistake never stops a run)
            _fail(plugin, f"{type(exc).__name__}: {exc}")
            return ""
    call.__name__ = getattr(fn, "__name__", "plugin_function")
    call.__doc__ = getattr(fn, "__doc__", "")
    return call


def _fail(plugin: Plugin, why: str):
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
        if not p.enabled or p.id in _LOADED:
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
            _fail(_LOADED[pid], f"{type(exc).__name__}: {exc}")
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
name = "{name}"
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
    # a script function: $shout(text) -> TEXT!
    api.naming_function("shout", lambda parser, text: text.upper() + "!")

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
    (d / MANIFEST).write_text(TEMPLATE_TOML.format(id=pid, name=name, api=API_VERSION), encoding="utf-8")
    (d / "__init__.py").write_text(TEMPLATE_INIT.format(name=name), encoding="utf-8")
    return d


def check(folder) -> list[str]:
    """What is wrong with the plugin in FOLDER ([] when it loads and registers cleanly)."""
    try:
        m = read_manifest(Path(folder))
    except PluginError as exc:
        return [str(exc)]
    gone = missing_modules(m)
    if gone:
        return [f"needs the Python module(s) {', '.join(gone)}"]
    p = Plugin(m["id"], m["name"], m["version"], Path(folder), m)
    try:
        spec = importlib.util.spec_from_file_location(f"tagmender_check_{os.getpid()}", Path(folder) / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
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
