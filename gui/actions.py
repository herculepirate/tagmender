"""gui/actions.py - Every command once: its words, shortcut, status tip, accessible name and icon,
and where it is shown (menu bar, toolbar, context menus). The toolbar is never the only way to
anything: each command is in a menu, and has a shortcut or is reached through the menu bar's
underlined letters (Alt+F, then the item's own letter: every item has one, unique in its menu).
The "&" is in the menu text only: plain(text) is what buttons, tooltips and screen readers get."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QLabel, QSizePolicy, QToolBar, QWidget

from . import acoustid_submit as _acoustid
from . import browser as _browser
from . import icons
from . import manual_match as _match
from . import search as _search
from .i18n import N_, tr

# key: (menu text with its "&" letter, shortcut, icon, status tip). A shortcut of None: in a menu only.
COMMANDS = {
    # File
    "add_folder": (N_("&Add Folder…"), "Ctrl+O", "folder-plus", N_("Choose a music folder to preview")),
    "preview": (N_("&Preview"), "Ctrl+P", "eye", N_("Work out what would change; nothing is written")),
    "apply": (N_("Appl&y"), "Ctrl+Return", "check", N_("Make the previewed changes (they can be undone)")),
    "cancel": (N_("&Cancel"), "Ctrl+.", "x", N_("Stop after the album being worked on")),
    "read_scans": (N_("&Read Scans for This Preview"), None, None,
                   N_("Read barcodes and catalog numbers from every album's cover scans (slower)")),
    "watch_folder": (N_("&Watch a Folder…"), "Ctrl+Shift+N", "folder-sync",
                     N_("Have a profile process music dropped into a folder")),
    "new_profile": (N_("&New Profile…"), "Ctrl+N", "plus", N_("Make a profile with the default settings")),
    "save_profile": (N_("&Save Profile"), "Ctrl+S", None, N_("Save the profile shown under Profiles")),
    "close_window": (N_("C&lose Window"), "Ctrl+W", None, N_("Close this window")),
    "quit": (N_("&Quit TagMender"), "Ctrl+Q", None, N_("Finish the work in progress, then quit")),
    # Edit
    "undo_edit": (N_("U&ndo Edit"), "Ctrl+Z", "undo-2",
                  N_("Take back your last edit of the preview (a tag, a cover, tags from file names)")),
    "undo_job": (N_("&Undo Last Job…"), "Ctrl+Alt+Z", None, N_("Put back everything the newest job changed")),
    "undo_selected": (N_("Undo Selected &Job…"), None, None,
                      N_("Put back everything the job selected under Jobs changed")),
    "show_job_albums": (N_("Show the Job's &Albums"), None, None, N_("List the albums the selected job changed")),
    "job_details": (N_("Job &Details…"), "Ctrl+Shift+J", None,
                    N_("What the selected job did, file by file and tag by tag, with Undo")),
    "show_report": (N_("Show &Report…"), "Ctrl+Shift+B", "list-checks",
                    N_("What the selected job or the preview did, what needs your decision and what could not "
                       "finish")),
    "cut": (N_("Cu&t"), QKeySequence.StandardKey.Cut, None, N_("Cut the selected text")),
    "copy": (N_("&Copy"), QKeySequence.StandardKey.Copy, None, N_("Copy the selection")),
    "paste": (N_("&Paste"), QKeySequence.StandardKey.Paste, None, N_("Paste")),
    "select_all": (N_("Select A&ll"), QKeySequence.StandardKey.SelectAll, None, N_("Select all")),
    "copy_path": (N_("Copy Album Pat&h"), "Ctrl+Shift+C", None, N_("Copy the selected album's folder")),
    "find": (N_("&Find"), "Ctrl+F", "search", N_("Search the page shown")),
    "preferences": (N_("Pr&eferences…"), "Ctrl+,", "settings-2", N_("Appearance, provider keys and checks")),
    # View: the pages are added by the window (Alt+1 ...)
    "show_sidebar": (N_("Show &Sidebar"), "Ctrl+Alt+S", None, N_("Show or hide the sidebar")),
    "show_inspector": (N_("Show &Inspector"), "Ctrl+Alt+I", "panel-right", N_("Show or hide the inspector")),
    "tab_tags": (N_("&Tags"), "Ctrl+Alt+1", "tag", N_("The selected album's tags, original and new")),
    "tab_artwork": (N_("&Artwork"), "Ctrl+Alt+2", "image", N_("The selected album's cover, original and new")),
    "tab_evidence": (N_("&Evidence"), "Ctrl+Alt+3", "info", N_("Why TagMender chose this release, and its doubts")),
    "refresh": (N_("&Refresh"), "Ctrl+R", "refresh-cw", N_("Read the page's list again")),
    # Album
    "use_release": (N_("&Paste a Release Link…"), "Ctrl+Shift+R", "disc-3",
                    N_("Tag this album from a MusicBrainz or Discogs release link you paste")),
    # (Search MusicBrainz and Discogs…, Other Versions…, Choose Your Pressing…: gui.search.COMMANDS,
    # below; the four are Album > Change Release, and the inspector's Change Release button)
    "file_info": (N_("&Info…"), "Ctrl+I", "info", N_("The selected file's format, length, every tag and picture")),
    # Album > Tag and Album > Artwork: the inspector's edits (the Tag keys work in its Tags table)
    "edit_tag": (N_("&Edit Value…"), "F2", "pencil", N_("Type a new value for the tag selected in the inspector")),
    "revert_tag": (N_("&Revert to Original"), "Ctrl+Alt+R", "history", N_("The file's own value for the selected tag")),
    "use_suggested": (N_("Use &Suggested Value"), "Ctrl+Alt+U", "sparkles",
                      N_("Forget your edit: use the value the preview suggested")),
    "remove_tag": (N_("Re&move Tag"), "Del", "minus", N_("Remove the selected tag from the files")),
    "add_tag": (N_("&Add Tag…"), "Ctrl+Alt+A", "plus", N_("Add a tag to the album's files")),
    # Album > Tag > Rating: the inspector's stars for the file shown (all files: each), as your edit
    "rate_none": (N_("&No Rating"), None, None, N_("Take your rating off the file shown in the inspector")),
    "rate_1": (N_("&1 Star"), None, None, N_("Rate the file shown in the inspector 1 star of 5")),
    "rate_2": (N_("&2 Stars"), None, None, N_("Rate the file shown in the inspector 2 stars of 5")),
    "rate_3": (N_("&3 Stars"), None, None, N_("Rate the file shown in the inspector 3 stars of 5")),
    "rate_4": (N_("&4 Stars"), None, None, N_("Rate the file shown in the inspector 4 stars of 5")),
    "rate_5": (N_("&5 Stars"), None, None, N_("Rate the file shown in the inspector 5 stars of 5")),
    "choose_cover": (N_("&Choose Image…"), "Ctrl+Alt+C", "image", N_("Use an image file as the album's new cover")),
    "cover_link": (N_("Use Image &Link…"), None, "globe", N_("Use an image from the web as the album's new cover")),
    "suggested_cover": (N_("Use &Suggested Cover"), None, "sparkles",
                        N_("Forget your cover: use the one the preview found")),
    "process_anyway": (N_("&Process Anyway"), "Ctrl+Shift+A", None,
                       N_("Rules will not exclude this album; preview again to include it")),
    "exclude_again": (N_("&Exclude Again"), None, None,
                      N_("The rules exclude this album again (undoes Process Anyway)")),
    "retry": (N_("&Retry in Overview"), "Ctrl+Shift+T", "refresh-cw", N_("Preview this album again in Overview")),
    "search_web": (N_("&Search the Web"), "Ctrl+Shift+W", "globe", N_("Search for this album in your browser")),
    "open_folder": (N_("&Open Folder"), "Ctrl+Shift+O", "folder-open",
                    N_("Open the album's folder in the file manager")),
    # Tools
    "check_now": (N_("&Check Watched Folders Now"), "Ctrl+Shift+K", "folder-sync",
                  N_("Look in the watched folders now")),
    "change_watch": (N_("C&hange Watched Folder…"), None, None,
                     N_("Change the selected watched folder's profile or timing")),
    "stop_watch": (N_("S&top Watching Folder…"), "Ctrl+Backspace", None, N_("Stop watching the selected folder")),
    "run_in_background": (N_("Run Watcher in the &Background…"), None, None,
                          N_("Keep watching the folders when TagMender is closed (a systemd user service)")),
    "show_plan": (N_("Show &Waiting Changes…"), None, None, N_("What applying the selected waiting album changes")),
    "apply_plan": (N_("&Apply Waiting Album…"), "Ctrl+Shift+Return", None,
                   N_("Apply the changes to the selected album waiting for your approval")),
    "apply_all_plans": (N_("Apply A&ll Waiting Albums…"), None, None,
                        N_("Apply the changes to every album waiting for your approval")),
    "dismiss_plan": (N_("&Dismiss Waiting Album…"), None, None,
                     N_("Drop the selected album's waiting changes; the album stays as it is")),
    "new_rule": (N_("&New Rule…"), "Ctrl+Alt+N", None, N_("Add an Ignore or Skip rule to the profile shown")),
    "edit_rule": (N_("&Edit Rule…"), None, None, N_("Change the selected rule")),
    "toggle_rule": (N_("T&urn Rule On"), "Ctrl+Alt+E", None, N_("Turn the selected rule on or off")),
    "delete_rule": (N_("&Remove Rule…"), "Del", None, N_("Remove the selected rule")),
    "test_rules": (N_("Test Rules on a &Folder…"), None, None,
                   N_("See what the rules would exclude; nothing changes")),
    "tagscript": (N_("&Tagging Script for This Profile…"), None, None,
                      N_("The naming and tagger script lines, in the tagging script language, that do the same")),
    "edit_scripts": (N_("&Script Editor…"), "Ctrl+Shift+E", None,
                     N_("Edit the profile's file naming script and tagger scripts, with a preview")),
    "tags_from_filenames": (N_("Ta&gs from File Names…"), "Ctrl+Shift+G", None,
                            N_("Read tags from the selected album's file names into the preview")),
    "recover": (N_("Rec&over Interrupted Jobs…"), None, "rotate-ccw",
                N_("Put back albums a stopped job left half-done")),
    "run_checks": (N_("Run Chec&ks"), "Ctrl+Shift+D", "stethoscope", N_("Check what TagMender needs is set up")),
    "export": (N_("E&xport Diagnostics…"), None, "download",
               N_("Save a zip for a problem report (no keys or tokens)")),
    # Window
    "minimize": (N_("&Minimize"), "Ctrl+M", None, N_("Minimize this window")),
    "zoom": (N_("Ma&ximize"), None, None, N_("Fill the screen, or go back")),
    "main_window": (N_("&TagMender"), None, None, N_("Bring the main window to the front")),
    "show_log": (N_("&Log"), "Ctrl+Shift+Y", None,
                 N_("TagMender's log: search it, show warnings only, follow new lines")),
    # Help
    "help": (N_("TagMender &Help"), "F1", None, N_("First steps: preview, apply, undo")),
    "setup": (N_("&Set Up TagMender…"), None, "key-round",
              N_("Every key, login and optional program: what each is for and where to get it")),
    "shortcuts": (N_("&Keyboard Shortcuts"), "Ctrl+/", None, N_("Every shortcut")),
    "report": (N_("&Report a Problem…"), None, None, N_("Export diagnostics and open the issue tracker")),
    "about": (N_("&About TagMender"), None, None, N_("Version and licence")),
}
COMMANDS.update(_search.COMMANDS)                       # (the search sheets' Album commands)
COMMANDS.update(_browser.COMMANDS)                      # (File > Look Up CD…, Ctrl+K; "&U": File's C is Cancel)
COMMANDS["lookup_cd"] = (N_("Look &Up CD…"),) + _browser.COMMANDS["lookup_cd"][1:]
COMMANDS.update(_match.COMMANDS)                        # (Ctrl+Shift+M: Ctrl+M stays Window > Minimize)
COMMANDS.update({
    # Album: your MusicBrainz account and AcoustID (each sends only after a sheet says what). No keys,
    # on purpose: a send is reached through the menu only (the app's design rules, "Sending")
    "add_to_collection": (N_("Co&llections…"), None, None,
                          N_("Choose the MusicBrainz collections that hold this album's release")),
    "submit_ratings": (N_("Submit Ratin&gs…"), None, None,
                       N_("Send your ratings of the album's tracks to MusicBrainz")),
    "submit_acoustids": (N_("Submit AcoustI&Ds…"), None, None, N_("Send the album's fingerprints to AcoustID")),
    # Controls: the player (Space plays or pauses where the album tables have focus; Ctrl+arrows as
    # in Music, and a text field keeps them for moving by words, a table (KeepViewKeys) for moving the
    # current row; Ctrl+. is Cancel, so Stop is Ctrl+Alt+.)
    "play_pause": (N_("&Play/Pause"), "Space", None, N_("Play or pause the selected album")),
    "next_track": (N_("&Next Track"), "Ctrl+Right", None, N_("Play the album's next track")),
    "previous_track": (N_("P&revious Track"), "Ctrl+Left", None, N_("Play the track before, or this one again")),
    "stop_playing": (N_("&Stop"), "Ctrl+Alt+.", None, N_("Stop playing and close the player")),
    "skip_forward": (N_("Skip &Forward 5 s"), "Ctrl+Shift+Right", None, N_("Go 5 seconds further into the track")),
    "skip_back": (N_("Skip &Back 5 s"), "Ctrl+Shift+Left", None, N_("Go back 5 seconds in the track")),
    "volume_up": (N_("&Volume Up"), "Ctrl+Up", None, N_("Play louder")),
    "volume_down": (N_("Volume &Down"), "Ctrl+Down", None, N_("Play more quietly")),
    "mute": (N_("&Mute"), "Ctrl+Alt+M", None, N_("Mute or unmute the player")),
})

# a menu's item: a command's key, None (a separator) or (submenu title, its keys)
RATING_MENU = ("rate_none", None, "rate_1", "rate_2", "rate_3", "rate_4", "rate_5")
RATINGS = {"rate_none": None, "rate_1": 1, "rate_2": 2, "rate_3": 3, "rate_4": 4, "rate_5": 5}
TAG_MENU = ("edit_tag", "revert_tag", "use_suggested", "remove_tag", None, "add_tag", None,
            (N_("Ra&ting"), RATING_MENU))
COVER_MENU = ("choose_cover", "cover_link", "suggested_cover")
# the Album > Change Release submenu (and the inspector's Change Release button)
RELEASE_MENU = ("use_release", *_search.MENU)
# commands whose keys act only where focus is in the inspector's Tags (Delete and F2 elsewhere stay
# theirs: a rule's Delete, a cell's F2), or in the album tables (Space still presses a focused button
# or ticks a box elsewhere); the other commands' keys work in the whole window
SCOPED = ("edit_tag", "remove_tag", "play_pause")    # (Space: the album tables and the player bar only)
MENUS = (
    (N_("&File"), ("add_folder", "lookup_cd", None, "preview", "apply", "cancel", "read_scans", None, "watch_folder",
               "new_profile", "save_profile", None, "close_window", "quit")),
    (N_("&Edit"),
     ("undo_edit", "undo_job", "undo_selected", "show_job_albums", "job_details", "show_report", None, "cut", "copy",
      "paste", "select_all", None, "copy_path", "find", None, "preferences")),
    (N_("&View"), ("@pages", None, "show_sidebar", "show_inspector", None, "tab_tags", "tab_artwork", "tab_evidence",
               None, "refresh")),
    (N_("&Album"), ((N_("&Change Release"), RELEASE_MENU), "match_files", None, (N_("&Tag"), TAG_MENU),
                (N_("Artwor&k"), COVER_MENU), None, "process_anyway", "exclude_again", "retry", None,
                "add_to_collection", "submit_ratings", "submit_acoustids", None, "file_info", "search_web",
                "open_folder", "copy_path")),
    (N_("&Controls"), ("play_pause", "next_track", "previous_track", "stop_playing", None, "skip_forward",
                       "skip_back", None, "volume_up", "volume_down", "mute")),
    (N_("&Tools"), ("check_now", (N_("&Watched Folders"), ("change_watch", "stop_watch", "run_in_background", None,
                                                   "show_plan", "apply_plan", "apply_all_plans", "dismiss_plan")),
                (N_("&Rules"), ("new_rule", "edit_rule", "toggle_rule", "delete_rule", None, "test_rules")), None,
                "edit_scripts", "tags_from_filenames", "tagscript", None,
                "recover", None, "run_checks", "export")),
    (N_("&Window"), ("minimize", "zoom", None, "main_window", None, "show_log")),
    (N_("&Help"), ("help", "setup", "shortcuts", None, "report", "about")),
)
CHECKABLE = ("read_scans", "show_sidebar", "show_inspector", "toggle_rule")
TOOLBAR = ("add_folder", "preview", "apply", "cancel")
TOOLBAR_TEXT = {"add_folder": N_(" Add Folder")}           # (its icon fills its box: a space keeps the gap)
ICON_ROLE = {"apply": "on_accent"}                      # (the prominent button: its icon as its label)
# the pages in the View menu, with their letters (the page's name stays the key)
PAGE_TEXT = {"Overview": N_("&Overview"), "Needs attention": N_("&Needs attention"), "History": N_("&History"),
             "Watched folders": N_("&Watched folders"), "Jobs": N_("&Jobs"), "Profiles": N_("&Profiles"),
             "Setup": N_("Set&up")}


# the Controls keys a list or table uses itself (Ctrl+Up/Down moves the current row, Ctrl+Left/Right the
# current column, without changing the selection): there they stay the view's
VIEW_KEYS = (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right)


class KeepViewKeys(QObject):
    """Install on the window's lists and tables: Ctrl+arrows go to the focused one, not to the window's
    Controls shortcuts (Next Track, Volume Up ...), which still work everywhere else."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, e):
        if e.type() == QEvent.ShortcutOverride and e.key() in VIEW_KEYS \
                and (e.modifiers() & ~Qt.KeypadModifier) == Qt.ControlModifier \
                and isinstance(obj, QAbstractItemView) and obj.window() is self.window:
            e.accept()                                   # (the view gets the key press, not the shortcut)
            return False
        return False


def plain(text) -> str:
    """Menu text without its underlined letter ("&&" stays a literal "&")."""
    return (text or "").replace("&&", "\0").replace("&", "").replace("\0", "&")


def accessible(a: QAction) -> str:
    return a.property("accessible_name") or plain(a.text()).rstrip("…")


def retitle(a: QAction, text):
    """New menu text (with its "&" letter): the tooltip and accessible name follow."""
    a.setText(text)
    sc = a.shortcut().toString(QKeySequence.NativeText)
    words = plain(text).rstrip("…")
    a.setToolTip(f"{words} ({sc})" if sc else words)
    a.setProperty("accessible_name", words)


def make(win, key, text, shortcut, icon, tip) -> QAction:
    a = QAction(text, win)
    a.setObjectName(key)
    if shortcut is not None:
        a.setShortcut(QKeySequence(shortcut))
    a.setStatusTip(tip)
    retitle(a, text)
    a.setProperty("icon_name", icon or "")
    a.setProperty("icon_role", ICON_ROLE.get(key, "ink"))
    if icon:
        a.setIcon(icons.icon(icon, ICON_ROLE.get(key, "ink")))
    a.setCheckable(key in CHECKABLE)
    a.setIconVisibleInMenu(False)                        # (as macOS menus: icons on the toolbar only)
    a.setShortcutContext(Qt.WindowShortcut)
    return a


def build(win, pages):
    """win.actions: every QAction by key, the pages' ("page:<name>") too."""
    acts = {}
    for key, (text, sc, icon, tip) in COMMANDS.items():
        acts[key] = make(win, key, tr(text), sc, icon, tr(tip))
        if key in SCOPED:                                # (the window adds these to the inspector's Tags)
            acts[key].setShortcutContext(Qt.WidgetWithChildrenShortcut)
        else:
            win.addAction(acts[key])
    for i, (name, icon) in enumerate(pages):
        a = make(win, f"page:{name}", tr(PAGE_TEXT.get(name, name)), f"Alt+{i + 1}", icon,
                 tr("Show {0}").format(tr(name)))
        a.setProperty("icon_name", icon)
        acts[f"page:{name}"] = a
        win.addAction(a)
    return acts


def build_menus(win, acts, pages):
    """win.menus: each menu by its title, a submenu too ("Tag", "Watched Folders" ...)."""
    bar = win.menuBar()
    menus = {}

    def fill(m, keys):
        for k in keys:
            if k is None:
                m.addSeparator()
            elif isinstance(k, tuple):
                sub = m.addMenu(tr(k[0]))
                sub.setObjectName(k[0].replace("&", "").lower())
                menus[k[0].replace("&", "")] = sub
                fill(sub, k[1])
            elif k == "@pages":
                for name, _icon in pages:
                    m.addAction(acts[f"page:{name}"])
            else:
                m.addAction(acts[k])
        mac_menu(m)
    for title, keys in MENUS:
        m = bar.addMenu(tr(title))
        m.setObjectName(title.replace("&", "").lower())
        menus[title.replace("&", "")] = m
        fill(m, keys)
    return menus


def menu_keys(keys):
    """The command keys of a menu's items, its submenus' too, in order."""
    for k in keys:
        if isinstance(k, tuple):
            yield from menu_keys(k[1])
        elif k:
            yield k


def mac_menu(m):
    """A menu with one narrow check column, as on a Mac: a menu with check marks says so (its
    items then start right after the marks: style.py)."""
    m.setProperty("checks", any(a.isCheckable() for a in m.actions()))
    return m


def build_toolbar(win, acts):
    bar = QToolBar(tr("Toolbar"))
    bar.setObjectName("toolbar")
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    bar.setIconSize(icons.size(16))
    bar.setFixedHeight(44)
    bar.setContextMenuPolicy(Qt.PreventContextMenu)
    for k in TOOLBAR:
        a = acts[k]
        bar.addAction(a)
        words = tr(TOOLBAR_TEXT[k]) if k in TOOLBAR_TEXT else plain(a.text()).rstrip("…")
        a.setIconText(words)                              # (what the button shows, kept on every change)
        b = bar.widgetForAction(a)
        b.setText(words)
        b.setAccessibleName(accessible(a))
        b.setFocusPolicy(Qt.TabFocus)
        if k == "apply":
            b.setProperty("role", "primary")
    return bar


def toolbar_end(bar, widgets, acts):
    """After the main actions: the progress bar, a spring, then the profile picker, the search
    field and the inspector toggle."""
    for w in widgets[:1]:
        bar.addWidget(w)
    spring = QWidget()
    spring.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    bar.addWidget(spring)
    for w in widgets[1:]:
        if isinstance(w, str):
            lb = QLabel(w)
            lb.setProperty("role", "hint")
            bar.addWidget(lb)
        else:
            bar.addWidget(w)
    a = acts["show_inspector"]
    bar.addAction(a)
    b = bar.widgetForAction(a)
    b.setToolButtonStyle(Qt.ToolButtonIconOnly)
    b.setAccessibleName(tr("Show Inspector"))
    b.setFocusPolicy(Qt.TabFocus)


def shortcut_text(acts) -> str:
    """The Keyboard Shortcuts sheet: each menu's commands that have one."""
    lines = []
    for title, keys in MENUS:
        rows = []
        for k in menu_keys(keys):
            if k == "@pages":
                rows += [a for n, a in acts.items() if n.startswith("page:")]
            else:
                rows.append(acts[k])
        rows = [a for a in rows if not a.shortcut().isEmpty()]
        if rows:
            lines.append(plain(tr(title)))
            lines += [f"  {a.shortcut().toString(QKeySequence.NativeText):<16} {plain(a.text()).rstrip('…')}"
                      for a in rows]
            lines.append("")
    for title, keys in SHEET_KEYS:                       # (keys of a sheet or a table, not in a menu)
        lines.append(tr(title))
        lines += [f"  {tr(k):<16} {tr(what)}" for k, what in keys]
        lines.append("")
    return "\n".join(lines).rstrip()


# keys that work inside one sheet, window or table (listed under Help > Keyboard Shortcuts)
SHEET_KEYS = (
    (N_("Inspector Tags"), ((N_("Return or F2"), N_("Edit the selected value")),
                        (N_("Delete or Backspace"), N_("Remove the selected tag")))),
    (N_("Search for Release"), tuple((f"Ctrl+{i + 1}", label) for i, (_k, label) in enumerate(_search.SEARCH_KINDS))),
    (N_("Script Editor"),
     (("Ctrl+N", N_("Add a tagger script")), (N_("Delete"), N_("Remove the tagger script (in the list)")),
                       ("Ctrl+1", N_("Preview")), ("Ctrl+2", N_("Function Help")), ("Ctrl+S", N_("Save Scripts")))),
    (N_("Log"), (("Ctrl+F", N_("Search the log")), ("Ctrl+R", N_("Reload")), ("Ctrl+W or Esc", N_("Close the log")))),
)
