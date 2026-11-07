# Plugins

A plugin is a small piece of Python that adds to TagMender: a new function for naming and tagger
scripts, or a change to the tags an album gets. Anyone can write one, and you install one from a
file or a folder.

- [Using plugins](#using-plugins)
- [Writing a plugin](#writing-a-plugin)
- [plugin.toml](#plugintoml)
- [What a plugin can add](#what-a-plugin-can-add)
- [Settings](#settings)
- [When a plugin fails](#when-a-plugin-fails)
- [Sharing a plugin](#sharing-a-plugin)

## Using plugins

**Built in:** two plugins come with TagMender and are on from the start: **Discogs** (identifies
albums on Discogs and fills in their tags; needs a Discogs token, set in Setup) and **CoverUp**
(finds, checks and chooses cover art). Their switches are in Preferences › Plugins, or
`tagmender plugins disable coverup`. A built-in plugin can be switched off but not removed, and the
change applies from the next look-up. With Discogs off, albums are identified with MusicBrainz
only; with CoverUp off, covers come from the Cover Art Archive and Discogs only, and covers already
there are kept. `TAGMENDER_NO_PLUGINS` does not switch them off. The switches apply to the app, the
watcher, `tagmender plan`/`run` and Find Covers…; with Discogs off, `tagmender inbox` and
`tagmender match` (which identify with Discogs) stop and say so, and `tagmender web` and
`tagmender enrich` leave Discogs out. `tagmender coverup`, the CoverUp tool itself, run by name,
uses CoverUp's code anyway.

**Install:** Preferences › Plugins › **Install from File…** (the plugin's `.zip`) or **Install from
Folder…**. Or on the command line, with a `.zip` or a folder:

```bash
tagmender plugins install ~/Downloads/roman-numerals.zip
tagmender plugins list
tagmender plugins enable roman-numerals
```

A new plugin is **off** until you switch it on; installing a newer copy of one that is on keeps it
on. A plugin runs with your permissions, like any program you install, so only install plugins
from people you trust. Plugins you install need **Python 3.11** or later, or on Python 3.10 the
`tomli` module (`sudo apt install python3-tomli`, or `pip install tomli`); the AppImage and the
Flatpak have it. Modules a plugin lists under `requires` are not installed for you: install them
the same way.

- Plugins live in `~/.local/share/tagmender/plugins/<id>/` (`$XDG_DATA_HOME/tagmender/plugins`).
  Preferences › Plugins › Open Plugins Folder shows it.
- **Remove** switches the plugin off and moves its folder to `plugins/.removed/<id>-<date-time>/`.
  To bring it back, move that folder back to `plugins/<id>` (its name is the id again) and switch
  it on.
- Switching a plugin on takes effect at once; switching one off, at the next start (a watched-folder
  service: restart it).
- To run once without the plugins you installed: `tagmender --no-plugins ...` (before the command,
  in any order with `--profile`), or `TAGMENDER_NO_PLUGINS=1 tagmender ...`. A naming script that
  uses a plugin's `$function` is then an unknown function; a tagger script skips it.
- Plugins never write to your files themselves. What they change is part of the plan: you see it in
  the preview, and Undo puts it back.

Which plugins are on is a setting of this computer, not of a profile.

## Writing a plugin

Start one with:

```bash
tagmender plugins new "My Plugin"        # makes ./my-plugin/ (or --where DIR) with the two files below
tagmender plugins check my-plugin        # says what is wrong, or "Ready" when it loads cleanly
tagmender plugins install my-plugin      # copies it into the plugins folder; then switch it on
```

A plugin is a folder holding `plugin.toml` and `__init__.py`; it may hold more Python files, which
`__init__.py` imports relatively (`from . import helper`). Installing copies the folder, so after
editing your copy, install it again (it stays on if it was on) and start TagMender again.

`__init__.py` has one function, `register(api)`, which says what the plugin adds:

```python
def register(api):
    api.naming_function("my_plugin_shout", lambda parser, text: text.upper() + "!")
```

Give script functions names of your plugin's own (the scaffold names its example after the plugin):
two plugins cannot both add `$shout`. The [Roman numerals example](../contrib/plugin-example/roman-numerals/)
is a complete plugin to copy from.

## plugin.toml

```toml
id = "my-plugin"              # required: 2-40 lower-case letters, digits, - or _, starting with a letter
name = "My Plugin"            # required
version = "0.1.0"             # required
api = 1                       # required: the number 1 (the plugin API it was written for), not "1"
description = "What this plugin adds, in one sentence."   # required
author = "Your Name"          # optional
license = "GPL-2.0-or-later"  # optional
homepage = "https://example.org/my-plugin"                 # optional
requires = []                 # optional: Python modules it imports, such as ["requests"]; checked, not installed
```

Installing puts the plugin in a folder named after its `id`. The ids `discogs` and `coverup` belong
to the built-in plugins and are refused.

## What a plugin can add

`api` also has `api.api_version` (1) and the plugin's settings and logger, below.

### A script function: `api.naming_function(name, fn, eval_args=True, documentation="")`

Adds `$name(...)` to naming scripts and tagger scripts (see [SCRIPTING.md](SCRIPTING.md)).

- `name`: lower-case letters, digits and `_`, starting with a letter or `_`. A name TagMender or
  another plugin already has (`$if`, `$upper`...) is refused, and the message says whose it is.
- `fn(parser, arg1, arg2, ...)` returns **text** (a `str`; `None` counts as empty text). Each
  argument is already worked out as text. `parser` is the script parser; `parser.context` holds the
  track's tags (`parser.context.get("artist")`).
- The number of arguments is checked against `fn`'s own: `$dotted()` for a function that takes one
  is the script's error ("Wrong number of arguments"), not the plugin's. Arguments with defaults
  are optional, and `*args` takes any number.
- `eval_args=False`: the arguments arrive as script expressions, worked out only when you call
  `arg.eval(parser)` (for a function such as `$if` that evaluates only some of them).
- `documentation`: the text shown for it; by default "From the plugin …".

```python
def register(api):
    def initials_dotted(parser, text):
        return ".".join(w[0] for w in text.split() if w) + "."

    api.naming_function("dotted", initials_dotted)     # $dotted(%artist%) -> "D.B."
```

### Tag changes: `api.tag_processor(fn)`

`fn(tags, info)` gets one file's tags as the plan will write them and returns the tags to write.

- It runs for each file of an **identified** album, after the profile's tagger scripts, every time
  a preview or plan is built: keep it quick and free of side effects (no files, no network).
  Several plugins' processors run one after another, in the order the plugins load (by folder name).
- `tags` is `{name: [values]}` with TagMender's tag names (lower case: `title`, `artist`,
  `albumartist`, `tracknumber`, `discsubtitle` ...; a custom tag spelled as in the file). Values
  are lists of text; a single value or a number you return is put in a list and made text (`None`
  is no value, never the text "None").
- **What you return is what is written:** a tag you leave out of the returned dict is removed from
  the file (return `tags` with your changes, not a new dict of only what you changed).
- `info`: `file` (the file's path), `album_folder` (the album's folder, also for an album in
  CD1/CD2 folders) and `profile` (the profile's name).
- The profile's tagging level and field policies still apply to what it returns.

```python
def register(api):
    def no_feat_in_titles(tags, info):
        tags["title"] = [t.replace(" (feat. ", " (with ") for t in tags.get("title", [])]
        return tags

    api.tag_processor(no_feat_in_titles)
```

### Logging: `api.log`

A logger of the plugin's own (`tagmender.plugin.<id>`); what it logs appears in TagMender's log
(Window › Log, or `~/.local/state/tagmender/logs/tagmender.log`). Debug messages are kept only with
`TAGMENDER_LOG_LEVEL=DEBUG`.

## Settings

A plugin can ask for settings. They appear under the plugin in Preferences › Plugins:

```toml
[[settings]]
key = "style"                 # required: lower-case letters, digits and _, once each
label = "Style"               # required
kind = "text"                 # text (the default), secret or switch
default = ""                  # text for text and secret; true or false for a switch
help = "What it changes."     # optional
```

Read one with `api.setting("style")`; it reads the value as it is now, each time (so call it when
you need it, not once in `register`). A key that is not among the plugin's settings raises.

A **secret** (a key or a password) is kept in a file only you can read:
`~/.config/tagmender/plugins/<id>/<key>` (mode 600). `api.setting` refuses one other users can read.
In Preferences, type it to save it and **Forget** removes it. Removing the plugin keeps its secrets;
delete that folder to remove them too.

## When a plugin fails

A plugin that fails to load, raises, or gives the wrong kind of answer is switched off for the rest
of that run, and the run carries on without it:

- Preferences › Plugins shows a red light with the error; switching it off and on there tries it
  again (after you fixed it). `tagmender plugins check <its folder>` says why one cannot load.
- A tag processor that fails while an album is planned: the album is planned again without it, so
  no album gets its changes on some tracks only, and the plan's notes name the plugin.
- A script function that fails while an album's files are named: that album is held back (not
  applied) with a note naming the plugin; preview it again for names made without it.
- During a command-line or watched-folder run, failures are written to the log.
- The plugin stays **on** in your settings: it is tried again at the next start.

## Sharing a plugin

Zip the plugin's folder (`zip -r my-plugin.zip my-plugin`) and share the file, for example as a
release of your plugin's own repository. A zip holds one plugin, with `plugin.toml` at its top or
in one folder; paths outside it are refused. People install it with Install from File….

When TagMender's plugin API changes in a way that would break plugins, `api` goes up, and a plugin
written for another API is refused with a message saying so rather than failing halfway.
