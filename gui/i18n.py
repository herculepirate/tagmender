"""gui/i18n.py - The app in other languages, as Picard does it: Qt's translators.

  tr(text)        the text in the language chosen, or the English as given when there is no
                  translation (so with no translator every word stays as written)
  tr(text, ctx)   the same, with a note telling translators apart two uses of one English text
                  (Qt's disambiguation: "Open" a folder and "Open" as a state)
  N_(text)        marks text where a table defines it (module level, before any translator is
                  in place); it is passed through tr() where it is shown

install() runs once in main(), before the window is made: Qt's own words (standard buttons, the
file chooser) from Qt's qtbase_<lang>.qm, and TagMender's from assets/i18n/tagmender_<lang>.qm
when one is there. The language is the system's, or the one chosen under Preferences > General
(gui.json "language"); a change takes effect the next time TagMender opens.

Every string sits in one context, "", which is what pyside6-lupdate gives a plain tr() call
(tools/i18n_extract.sh; docs/TRANSLATING.md)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

CONTEXT = ""
I18N = Path(__file__).resolve().parent.parent / "assets" / "i18n"
PREF = "language"                                       # gui.json: "system" or a code such as "de"
SYSTEM = "system"
_installed: list = []                                   # (kept: a translator that is collected stops working)
_own = None                                             # TagMender's translator, when one is loaded


def tr(text, context=None) -> str:
    """The text in the chosen language; the text itself when nothing translates it. Asks
    TagMender's own translator only (Qt's words are Qt's): safe on a worker thread, and with no
    translation loaded it calls no Qt at all."""
    own = _own
    if own is None or not text:
        return text
    return own.translate(CONTEXT, text, context) or text


def N_(text):
    """Marks text for the translators where it is defined; tr() it where it is shown."""
    return text


def available() -> list:
    """[(code, name)]: the languages with a translation in assets/i18n, each named in itself."""
    out = []
    for qm in sorted(I18N.glob("tagmender_*.qm")):
        code = qm.stem.split("_", 1)[1]
        name = QLocale(code).nativeLanguageName() or code
        out.append((code, name[:1].upper() + name[1:]))
    return out


def chosen() -> str:
    from .common import _gui_prefs
    return str(_gui_prefs().get(PREF) or SYSTEM)


def locale_for(choice) -> QLocale:
    return QLocale.system() if not choice or choice == SYSTEM else QLocale(choice)


def install(app=None, choice=None):
    """Put the translators for the language chosen (the system's by default) in place. Returns
    the code of TagMender's translation loaded, or None (English)."""
    app = app or QCoreApplication.instance()
    if app is None:
        return None
    global _own
    for t in _installed:
        app.removeTranslator(t)
    _installed.clear()
    _own = None
    choice = chosen() if choice is None else choice
    loc = locale_for(choice)
    if choice != SYSTEM:
        QLocale.setDefault(loc)                          # (dates and numbers follow the language chosen)
    qt = QTranslator(app)
    if qt.load(loc, "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt)
        _installed.append(qt)
    own = QTranslator(app)
    if I18N.is_dir() and own.load(loc, "tagmender", "_", str(I18N)):
        app.installTranslator(own)                       # (and for Qt's own tr() calls)
        _installed.append(own)
        _own = own
        return Path(own.filePath()).stem.split("_", 1)[1] if own.filePath() else loc.name()
    return None
