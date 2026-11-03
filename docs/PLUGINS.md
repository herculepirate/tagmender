# Plugins

A plugin is a small piece of Python that adds to TagMender: a new function for naming and tagger
scripts, or a change to the tags an album gets. Anyone can write one, and you install one from a
file.

- [Using plugins](#using-plugins)
- [Writing a plugin](#writing-a-plugin)
- [What a plugin can add](#what-a-plugin-can-add)
- [Settings](#settings)
- [Sharing a plugin](#sharing-a-plugin)

## Using plugins

**Install:** Preferences › Plugins › Install from File…, and choose the plugin's `.zip` (or its
folder). Or on the command line:

```bash
tagmender plugins install ~/Downloads/roman-numerals.zip
tagmender plugins list
tagmender plugins enable roman-numerals
```

A new plugin is **off** until you switch it on. A plugin runs with your permissions, like any
program you install, so only install plugins from people you trust.

- Plugins live in `~/.local/share/tagmender/plugins/<id>/`. Preferences › Plugins › Open Plugins
  Folder shows it.
- **Remove** moves the plugin to `plugins/.removed/`; move it back to undo.
- A plugin that fails to load, or fails while it runs, is switched off and Preferences shows why
  (a red light). Your run carries on without it.
- To start without any plugins: `TAGMENDER_NO_PLUGINS=1 tagmender ...` or `tagmender --no-plugins`.
- Plugins never write to your files themselves. What they change is part of the plan: you see it in
  the preview, and Undo puts it back.

Which plugins are on is a setting of this computer, not of a profile.

## Writing a plugin

Start one with:

```bash
tagmender plugins new "My Plugin"        # makes ./my-plugin/ with the two files below
tagmender plugins check my-plugin        # says what is wrong, or nothing when it is ready
tagmender plugins install my-plugin      # then switch it on
```

A plugin is a folder named after its id, holding two files.

**`plugin.toml`** describes it:

```toml
id = "my-plugin"              # 2-40 lower-case letters, digits, - or _; the folder's name
name = "My Plugin"
version = "0.1.0"
api = 1                       # the plugin API it was written for
author = "Your Name"
description = "What this plugin adds, in one sentence."
license = "GPL-2.0-or-later"
homepage = "https://example.org/my-plugin"
requires = []                 # Python modules it needs, such as ["requests"]
```

**`__init__.py`** has one function, `register(api)`, which says what the plugin adds:

```python
def register(api):
    api.naming_function("shout", lambda parser, text: text.upper() + "!")
```

The [Roman numerals example](../contrib/plugin-example/roman-numerals/) is a complete plugin to copy
from.

## What a plugin can add

### A script function: `api.naming_function(name, fn)`

Adds `$name(...)` to naming scripts and tagger scripts (see [SCRIPTING.md](SCRIPTING.md)). `fn` is
called as `fn(parser, arg1, arg2, ...)` with each argument already worked out as text, and returns
text.

```python
def register(api):
    def initials_dotted(parser, text):
        return ".".join(w[0] for w in text.split() if w) + "."

    api.naming_function("dotted", initials_dotted)     # $dotted(%artist%) -> "D.B."
```

A name TagMender already has (`$if`, `$upper`...) is refused. When the function raises, the
plugin is switched off and the script gets an empty text.

### Tag changes: `api.tag_processor(fn)`

`fn(tags, info)` gets one file's tags as the plan will write them, as `{name: [values]}`, and
returns the tags to write. `info` has `file`, `album_folder` and `profile`. It runs after the
profile's tagger scripts.

```python
def register(api):
    def no_feat_in_titles(tags, info):
        tags["title"] = [t.replace(" (feat. ", " (with ") for t in tags.get("title", [])]
        return tags

    api.tag_processor(no_feat_in_titles)
```

### Logging: `api.log`

A logger of the plugin's own; what it logs appears in TagMender's log (Window › Log).

## Settings

A plugin can ask for settings. They appear under the plugin in Preferences › Plugins:

```toml
[[settings]]
key = "style"                 # lower-case letters, digits and _
label = "Style"
kind = "text"                 # text, secret (stored in a file only you can read) or switch
default = ""
help = "What it changes."
```

Read one with `api.setting("style")`.

## Sharing a plugin

Zip the plugin's folder (`zip -r my-plugin.zip my-plugin`) and share the file, for example as a
release of your plugin's own repository. People install it with Install from File….

When TagMender's plugin API changes in a way that would break plugins, `api` goes up, and a plugin
written for another API is refused with a message saying so rather than failing halfway.
