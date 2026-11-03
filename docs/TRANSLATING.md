# Translating TagMender

The app's words (menus, buttons, sheets, pages, the inspector, Preferences) can be translated
with Qt's translation files. The command line, the logs and the
engine's own messages (reason codes, check results) stay in English for now.

## How it works

- Every text a person reads in `gui/` goes through `tr()` from `gui/i18n.py`. Without a
  translation `tr()` gives the English as written, so the app reads the same as before.
- A text in a table at the top of a module (the menu commands, status words, column names) is
  marked with `N_()` where it is defined and passed through `tr()` where it is shown. Only the
  words are translated: the table's keys, and the page and menu names the code looks things up
  by, stay English.
- The Setup page's items (what each key or program is for, what stops without it) and the
  "not used: no ... key" notices are defined in `requirements.py`, outside `gui/`, and marked
  with `N_()` there too; `tools/i18n_extract.sh` reads that file with `gui/`. In a terminal
  (`tagmender doctor`, `tagmender setup`) they stay English.
- Text with values in it uses `str.format` placeholders, translated whole:
  `tr("Saved {name}.").format(name=path.name)`. Keep every `{name}` and `{0}` in the
  translation; they may move.
- `tr(text, context)`: the second argument is a note for translators telling apart two uses of
  the same English text (Qt calls it the disambiguation). Every text is in one Qt context, `""`.
- At start (`gui.window.main`), `gui.i18n.install()` loads, for the language chosen:
  `assets/i18n/tagmender_<lang>.qm` (TagMender's words) and Qt's own `qtbase_<lang>.qm` (the
  file chooser, standard buttons). The language is the system's, or the one chosen under
  Preferences > General > Language (saved as `"language"` in `~/.config/tagmender/gui.json`);
  a change takes effect the next time TagMender opens.

## Translating into a language

You need PySide6 (it brings `pyside6-lupdate` and `pyside6-lrelease`) and Qt Linguist, or any
editor for `.ts` files.

1. Start the language's file (the code as Qt names locales: `de`, `fr`, `pt_BR`):

       tools/i18n_extract.sh --new de

   This writes `assets/i18n/tagmender.ts` (every text, untranslated: the template) and
   `assets/i18n/tagmender_de.ts`.
2. Translate `tagmender_de.ts` in Qt Linguist (`linguist assets/i18n/tagmender_de.ts`). Keep:
   - the `{…}` placeholders, and the `&` before a menu item's underlined letter (choose a letter
     not used by another item of the same menu),
   - `…` at the end of a command that opens a sheet,
   - the meaning of anything that says what is sent to MusicBrainz or AcoustID: those sheets are
     the person's consent, so they must stay exact. The request lines themselves (`POST
     https://…`, form field names) are not translated.
3. Compile it and try it:

       tools/i18n_extract.sh --release
       LANGUAGE=de LANG=de_DE.UTF-8 python3 -m gui

   or choose the language under Preferences > General and open TagMender again.

After the app's words change, run `tools/i18n_extract.sh` again: new texts are added to every
`tagmender_<lang>.ts` and finished translations are kept.

## For developers

- Wrap new user-visible text in `tr()` at the place it is shown; in a module-level table use
  `N_()` and `tr()` at the use. Never `tr()` at import time: the translator is installed after
  the modules are imported.
- Don't compare a button's or a combo box's shown text with English: compare with the same
  `tr()` text (`ans == tr("Save")`), or better, with the item's data.
- An f-string cannot be translated: write `tr("… {name} …").format(name=…)`.
- Not translated on purpose: log messages, tag names written into files, keys and IDs, the
  exact requests a confirmation sheet lists, and the command line.
- Tests compare English strings; they pass because no translation is loaded. `tests/test_gui_i18n.py`
  compiles a small catalogue in a temp folder to check that one is used when chosen.
