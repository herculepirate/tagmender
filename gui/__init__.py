"""gui - TagMender's desktop app (PySide6). The same engine, planner, executor, profiles
and rules as the CLI; work runs on a background thread so the window never freezes.

A Mac-style window (Apple HIG adapted to Qt): a toolbar (Add folder, Preview, Apply, Cancel, the
profile and a search field), a sidebar, the page, and an inspector on the right.

  Library     Overview          drop a folder (or choose one), pick a profile, Preview, then Apply
              Needs attention   albums marked Unresolved, by reason; retry, search the web, open
              History           what rules excluded
  Automation  Watched folders   the folders profiles keep processed; check them now
              Jobs              what ran; undo a job, recover an interrupted one
  Setup       Profiles          settings per profile, and its Ignore/Skip rules with a rule tester
  Preferences (Ctrl+,)          appearance, provider keys (entered, never shown), your account (your own
                                MusicBrainz application, Log In / Log Out, collections; your AcoustID user
                                key; browser integration and its port; the CD drive), checks, diagnostics

Picard's tools, as sheets of the window (nothing is written until the preview is applied):
Album > Change Release: Paste a Release Link (Ctrl+Shift+R), Search MusicBrainz and Discogs
(Ctrl+Shift+F), Other Versions of This Album (Ctrl+Shift+L), Choose Your Pressing (Ctrl+Shift+P);
Album > Info (Ctrl+I); Tools > Script Editor (Ctrl+Shift+E), Tags from File Names (Ctrl+Shift+G);
Edit > Job Details (Ctrl+Shift+J); Window > Log (Ctrl+Shift+Y: Ctrl+Alt+L locks the screen on
Cinnamon, KDE and Xfce). The inspector edits
tags, your rating and the cover as your edits of the preview.

M8 (Picard's other tools): File > Look Up CD (Ctrl+K); Album > Match Files Manually (Ctrl+M);
Album > Add to Collection, Submit Ratings, Submit AcoustIDs (each sends only after a sheet says
exactly what, with your own login or key); Controls > Play/Pause (Space in the album tables),
Next, Previous, Stop, for the player bar under the pages; musicbrainz.org's tagger buttons
(browser integration, off until Preferences > Account turns it on); folders dropped anywhere on
the window.

Keyboard: every command is in the menu bar; Ctrl+O add a folder, Ctrl+P preview,
Ctrl+Return apply, Ctrl+. cancel, Ctrl+F search the page, Alt+1..6 pages, Ctrl+Alt+I inspector,
Ctrl+/ every shortcut. Status is always shown as an icon or symbol and a word, never by
colour alone; light/dark follows the desktop unless Preferences say otherwise.

Modules: common (shared helpers), style (tokens, palettes, stylesheet), icons (tinted Lucide
icons), i18n (tr() and the translators: docs/TRANSLATING.md), work (background threads), sheets
(window-modal sheets), models (the plan as a table),
widgets (drop area, sidebar, empty states), one module per page (overview, attention, history,
watched, jobs, profiles), inspector, preferences, actions (every command, the menus and the
toolbar), search and versions (the search, other-versions and pressing sheets), script_editor,
dialogs (Tags from File Names, File Info, the log viewer, a job's details), manual_match, browser
(Look Up CD and the browser integration), account (MusicBrainz login, collections, ratings),
acoustid_submit, player and window (the main window and main()). Run with `tagmender gui` or `python3 -m gui`.

The names below are re-exported so `import gui; gui.Main()` and `gui.start(...)` keep
working; patch a module-level function where it lives (gui.<module>.<name>).
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox  # noqa: F401  (gui.X in tests)

from .common import (APP, HERE, _fill, _gui_prefs, _set_gui_pref, _table, ex, heading, label_for,  # noqa: F401
                     load_profile, open_folder, pf, planmod, profile_names, reasons, search_web, watch_does)
from .style import STATUS, TIER_LABELS, is_dark, status_color, stylesheet  # noqa: F401
from . import actions, icons, sheets, style  # noqa: F401
from .work import Worker, _alive, _Relay, start  # noqa: F401
from .models import PlanModel  # noqa: F401
from .widgets import EmptyState, SleeveDrop, SourceList  # noqa: F401
from .overview import Overview  # noqa: F401
from .jobs import Jobs  # noqa: F401
from .watched import WatchDialog, Watched  # noqa: F401
from .profiles import ART_SOURCE_CHOICES, Profiles, RuleDialog  # noqa: F401
from .attention import Attention  # noqa: F401
from .history import History  # noqa: F401
from .inspector import Inspector  # noqa: F401
from .preferences import Preferences  # noqa: F401
from .window import PAGES, SECTIONS, Main, main  # noqa: F401
from . import dialogs, script_editor, search, versions  # noqa: F401,E402  (gui.search.open_search ...)
from . import account, acoustid_submit, browser, manual_match, player  # noqa: F401,E402  (M8)
from .player import PlayerBar  # noqa: F401,E402
