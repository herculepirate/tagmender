"""gui/preferences.py - The Preferences window (Ctrl+,): settings for this computer.

One non-modal window, opened or raised by Main.preferences(). A toolbar of exclusive tabs (icon
above label) switches panes; the window title follows the tab and the last tab is remembered.
  General      appearance, language, reduce motion; the music folder and Skip By; keep watching
               while open and the notifications
  Keys         provider keys: entered, never shown again, saved only on this computer
  Account      your own MusicBrainz application (client ID and secret), Log In / Log Out and your
               collections; your AcoustID user key; browser integration (on or off, its port); the CD drive
  Network      Network & Privacy: Test Connection, the proxy, saved lookups, reading scans
  Files        File Safety: undo backups (how long they are kept, cleaned automatically) and the
               folder duplicate copies go into
  Advanced     the plugin folder; TagMender's checks (on a worker) and a diagnostics export without
               keys or tokens ("Checks" opens it too)
The panes of settings are grouped inset lists (gui/rows.py); each change is saved at once."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolBar, QVBoxLayout,
                               QWidget)

from . import i18n, icons, sheets, style
from .common import _gui_prefs, _set_gui_pref, heading, plural, section, separator
from .i18n import N_, tr
from .rows import FolderField, Group, ListField, Popup, Row, Segmented, StatusLight, Switch
from .work import start

# (key, the tab's words, icon); the key is what show_tab() takes ("Checks" still opens Advanced)
PANES = (("General", N_("General"), "settings-2"), ("Keys", N_("Keys"), "key-round"),
         ("Account", N_("Account"), "circle-user-round"), ("Network", N_("Network & Privacy"), "globe"),
         ("Files", N_("File Safety"), "history"), ("Advanced", N_("Advanced"), "stethoscope"),
         ("Plugins", N_("Plugins"), "puzzle"))
GUIDE = "https://github.com/herculepirate/tagmender/blob/main/docs/PLUGINS.md"
PANE_ALIASES = {"Checks": "Advanced"}
# Keep Undo Backups For: (days, words); 0 is for ever
KEEP_CHOICES = ((7, N_("1 Week")), (30, N_("30 Days")), (90, N_("90 Days")), (0, N_("Forever")))
# each key: a short name beside the field, and under it what it is for and where to get it
KEYS = {"discogs": (N_("Discogs token"), N_("Lets TagMender look albums up on Discogs. At "
                                             "discogs.com/settings/developers, choose Generate new token and paste "
                                             "it here.")),
        "acoustid": (N_("AcoustID application key"),
                     N_("Lets TagMender identify tracks by their sound.\n"
                        "1. Log in at acoustid.org (free).\n"
                        "2. At acoustid.org/new-application, register an application: any name, such as "
                        "TagMender.\n"
                        "3. Paste the API key it shows here.\n"
                        "This is not your user key: that one goes under Account, and only for sending "
                        "fingerprints.")),
        "fanart": (N_("fanart.tv key"), N_("For CoverUp's covers and artist photos: fanart.tv/get-an-api-key")),
        "audiodb": (N_("TheAudioDB key"), N_("More covers and artist photos for CoverUp (optional)")),
        "lastfm": (N_("Last.fm key"), N_("More covers for CoverUp: last.fm/api/account/create (optional)")),
        "gnudb": (N_("gnudb e-mail"), N_("The e-mail you registered at gnudb.org, for disc lookups (optional)")),
        "mojeek": (N_("Mojeek key"), N_("Mojeek Search API: mojeek.com/services/search/web-search-api (optional)")),
        "marginalia": (N_("Marginalia key"), N_("Web search; without a key the shared public key is used (optional)")),
        "tavily": (N_("Tavily key"), N_("A paid AI search, used only when a profile lists tavily (optional)")),
        "mb_client_id": (N_("Client ID"),
                         N_("Of your own MusicBrainz application (step 1 above)")),
        "mb_client_secret": (N_("Client secret"), N_("Of the same application")),
        "acoustid_user": (N_("AcoustID user key"),
                          N_("Only for sending an album's fingerprints back to AcoustID (Album › Submit "
                             "AcoustIDs). Identifying tracks works without it.\n"
                             "1. Log in at acoustid.org.\n"
                             "2. Open acoustid.org/api-key and paste the key shown there.\n"
                             "This is not the application key under Keys."))}
KEYS.update({
    "image_api_key": (N_("Image service key"),
                      N_("For AI covers (Album › Artwork › Find Covers…): the API key of your own image "
                         "service, such as an OpenAI key from platform.openai.com/api-keys. Each cover "
                         "made is a request it may charge for; nothing is sent until you confirm.")),
    "image_api_url": (N_("Image service address"),
                      N_("Leave empty for OpenAI (https://api.openai.com/v1), or give another service that "
                         "speaks the same images API.")),
    "image_model": (N_("Image model"), N_("Leave empty for gpt-image-1."))})
# the keys entered on the Account pane, not under Keys (yours, not a provider's)
ACCOUNT_KEYS = ("mb_client_id", "mb_client_secret", "acoustid_user")
SOURCE = {"file": N_("Saved on this computer"), "environment": N_("Set by the environment"),
          "env": N_("Set by the environment")}
# the keys in groups, the ones that matter first (a key credentials.py knows and no group names: "Other")
KEY_GROUPS = ((N_("Identification"), ("discogs", "acoustid", "gnudb")),
              (N_("Covers and artist photos"), ("fanart", "audiodb", "lastfm")),
              (N_("Web search (optional)"), ("mojeek", "marginalia", "tavily")),
              (N_("AI covers (optional)"), ("image_api_key", "image_api_url", "image_model")))
# a check's state as every status in the app says it: icon, STATUS colour key and word
CHECK_STATE = {"ok": ("circle-check", "done", N_("OK")), "warn": ("circle-alert", "unresolved", N_("Needs a look")),
               "fail": ("circle-x", "failed", N_("Problem"))}
CD_DRIVE = "cd_drive"                                  # gui.json: the drive File > Look Up CD reads
PREFS_TITLE = N_("{} — TagMender Preferences")             # (on Linux the taskbar shows this, not a menu bar)


def language_choices() -> list:
    """[(code, name)] for Preferences > General > Language: the system's, English (the app's own
    words) and every translation in assets/i18n; a language chosen earlier whose translation is
    gone stays listed, so the choice is not lost."""
    out = [(i18n.SYSTEM, tr("System")), ("en", "English")]
    out += [(code, name) for code, name in i18n.available() if code != "en"]
    cur = i18n.chosen()
    if cur not in {c for c, _n in out}:
        out.append((cur, tr("{0} (no translation)").format(cur)))
    return out


def _caption(text):
    lb = QLabel(text)
    lb.setProperty("role", "caption")
    lb.setWordWrap(True)
    return lb


def _with_caption(field, text):
    """A field with its explanation under it (the label beside it stays short)."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    v.addWidget(field)
    v.addWidget(_caption(text))
    return box


def _row_label(text, field):
    """A form label whose first line sits on its field's first line (the label is aligned to the
    top of a field with a caption under it, and moved down by half the field's extra height)."""
    lb = QLabel(text)
    top = max(0, (field.sizeHint().height() - lb.sizeHint().height()) // 2)
    lb.setContentsMargins(0, top, 0, 0)
    lb.setBuddy(field)
    return lb


def _show_stored(control, name):
    """A setting that could not be saved: its control shows the stored value again."""
    import app_settings
    v = app_settings.get(name)
    control.blockSignals(True)
    try:
        if isinstance(control, QCheckBox):
            control.setChecked(bool(v))
        elif hasattr(control, "set_value"):
            control.set_value(v if isinstance(v, list) else [])
        elif hasattr(control, "findData"):
            control.setCurrentIndex(max(0, control.findData(v)))
    finally:
        control.blockSignals(False)


def _save_setting(pane, name, value, control=None) -> bool:
    """Save one setting; on a refusal say why and put the control back."""
    import app_settings
    try:
        app_settings.set_value(name, value)
    except (OSError, ValueError) as exc:
        sheets.warning(pane, tr("That setting could not be saved."), str(exc))
        if control is not None:
            _show_stored(control, name)
        return False
    pane.said.setText(tr("Saved."))
    return True


def _popup(name, choices, current):
    box = Popup()
    box.setAccessibleName(name)
    for value, words in choices:
        box.addItem(tr(words), value)
    i = next((n for n in range(box.count()) if box.itemData(n) == current
              and isinstance(box.itemData(n), bool) == isinstance(current, bool)), -1)
    if i < 0 and current is not None:
        box.addItem(str(current), current)
        i = box.count() - 1
    box.setCurrentIndex(max(0, i))
    box.setSizeAdjustPolicy(QComboBox.AdjustToContents)    # (a pop-up as wide as its words)
    box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return box


class SettingsPane(QWidget):
    """A Preferences pane of grouped lists, each change saved to this computer's settings at
    once (app_settings), with a word when it could not be."""

    def __init__(self, title, hint):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        outer.addLayout(heading(title, hint))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName(title)
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 8, 8, 8)
        self.lay.setSpacing(16)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.said = QLabel("")
        self.said.setProperty("role", "hint")
        self.said.setWordWrap(True)
        outer.addWidget(self.said)

    def finish(self):
        self.lay.addStretch(1)

    def save(self, name, value, control=None) -> bool:
        return _save_setting(self, name, value, control)


class GeneralPane(SettingsPane):
    def __init__(self, win):
        super().__init__(tr("General"), tr("How TagMender looks, where your music is, and what it tells you."))
        import app_settings
        self.win = win
        data = app_settings.load()
        look = Group(tr("Appearance"))
        self.appearance = _popup(tr("Appearance"), ((("system"), N_("System")), ("light", N_("Light")),
                                                    ("dark", N_("Dark"))),
                                 _gui_prefs().get("appearance", "system"))
        self.appearance.currentIndexChanged.connect(self.set_appearance)
        look.add(Row(tr("Appearance"), self.appearance))
        self.language = QComboBox()
        self.language.setAccessibleName(tr("Language"))
        for code, name in language_choices():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.chosen())))
        self.language.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.language.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.language.currentIndexChanged.connect(self.set_language)
        look.add(Row(tr("Language"), self.language, tr("Takes effect the next time TagMender opens.")))
        from .common import reduced_motion
        self.motion = Switch(tr("Reduce motion"))
        self.motion.setChecked(reduced_motion())
        self.motion.toggled.connect(lambda on: _set_gui_pref("reduce_motion", bool(on)))
        look.add(Row(tr("Reduce motion"), self.motion, tr("No fades or slide-ins for sheets and the inspector.")))
        self.lay.addWidget(look)
        lib = Group(tr("Folders and playback"))
        self.music = FolderField(tr("Folder choosers open in"), tr("Choose where folder choosers open"))
        self.music.setText(str(data.get("library_folder") or ""))
        self.music.edit.setPlaceholderText(tr("The system Music folder"))
        self.music.edit.editingFinished.connect(self.set_music)
        self.music.button.clicked.connect(self.set_music)
        lib.add(Row(tr("Folder choosers open in"), self.music,
                    tr("Only where Choose Folder… windows start. It changes nothing in your music, and it is not "
                       "where music is copied: that is each profile's library folder.")))
        self.adding = _popup(tr("When you add a folder"), (("look_up", N_("Look its albums up at once")),
                                                           ("list", N_("List its albums first"))),
                             data.get("add_folder", "look_up"))
        self.adding.currentIndexChanged.connect(lambda _i: self.save("add_folder", self.adding.currentData(),
                                                                     self.adding))
        lib.add(Row(tr("When you add a folder"), self.adding,
                    tr("List first: the albums are shown without asking any database; select some and choose "
                       "Look Up (all sources, MusicBrainz, Discogs or the web), or Preview for all of them.")))
        self.skip = _popup(tr("Skip by"), ((5, N_("5 seconds")), (10, N_("10 seconds")), (15, N_("15 seconds"))),
                           (data.get("player") or {}).get("skip_seconds", 10))
        self.skip.currentIndexChanged.connect(lambda _i: self.save("player.skip_seconds", self.skip.currentData(),
                                                                          self.skip))
        lib.add(Row(tr("Skip by"), self.skip, tr("How far Controls › Skip Forward and Skip Back jump in the playing "
                                                 "track.")))
        self.lay.addWidget(lib)
        tell = Group(tr("Watching and notifications"), tr("Notifications come only while TagMender is in the "
                                                          "background."))
        self.watch = Switch(tr("Keep watching while TagMender is open"))
        live = self._live()
        self.watch.setChecked(bool(live.isChecked()) if live else bool(_gui_prefs().get("watch_in_app")))
        self.watch.toggled.connect(self.set_watch)
        tell.add(Row(tr("Keep watching while TagMender is open"), self.watch,
                     tr("Every few minutes. To keep watching when TagMender is closed, use Run in the "
                        "Background… on the Watched folders page.")))
        notes = data.get("notifications") or {}
        self.notify = Switch(tr("When a preview or apply finishes"))
        self.notify.setChecked(bool(notes.get("finished", True)))
        self.notify.toggled.connect(lambda on: self.save("notifications.finished", bool(on), self.notify))
        tell.add(Row(tr("When a preview or apply finishes"), self.notify, tr("A desktop notification says what was "
                                                                              "done.")))
        self.notify_watch = Switch(tr("When watched folders file or hold albums"))
        self.notify_watch.setChecked(bool(notes.get("watch", True)))
        self.notify_watch.toggled.connect(lambda on: self.save("notifications.watch", bool(on), self.notify_watch))
        tell.add(Row(tr("When watched folders file or hold albums"), self.notify_watch,
                     tr("When an album dropped into a watched folder is filed, or held for your review.")))
        self.lay.addWidget(tell)
        self.finish()

    def _live(self):
        page = (getattr(self.win, "pages", None) or {}).get("Watched folders")
        return getattr(page, "live", None)

    def set_watch(self, on):
        live = self._live()
        if live is not None:
            live.setChecked(on)                               # (the page saves it and starts or stops)
        else:
            _set_gui_pref("watch_in_app", bool(on))

    def set_language(self, _i):
        _set_gui_pref(i18n.PREF, self.language.currentData())

    def set_appearance(self, _i):
        key = self.appearance.currentData()
        _set_gui_pref("appearance", key)
        style.apply(None, key)

    def set_music(self):
        import app_settings
        text = self.music.text().strip()
        if text == (app_settings.get("library_folder") or ""):
            return
        if not self.save("library_folder", text):
            self.music.setText(app_settings.get("library_folder") or "")


class NetworkPane(SettingsPane):
    """Network and Privacy: Test Connection, the proxy, saved lookups and scans."""

    def __init__(self):
        super().__init__(tr("Network and Privacy"), tr("What TagMender asks on the internet, and what it keeps."))
        import app_settings
        from .setup import ConnectionPanel
        data = app_settings.load()
        conn = Group(tr("Connection"), tr("TagMender uses the proxy set in the http_proxy and https_proxy environment "
                                          "variables, as most Linux programs do; without them it connects directly. "
                                          "It names itself to each service as TagMender and its version, never "
                                          "as you."))
        self.connection = ConnectionPanel(self)
        conn.add_widget(self.connection)
        self.lay.addWidget(conn)
        saved = Group(tr("Saved lookups"), tr("Each database answer is kept on this computer, so processing an album "
                                              "again is quick and the services are not asked twice."))
        self.refresh = _popup(tr("Look up again after"), ((0, N_("Never")), (7, N_("1 week")), (30, N_("30 days")),
                                                      (90, N_("90 days"))),
                              (data.get("network") or {}).get("refresh_days", 30))
        self.refresh.currentIndexChanged.connect(lambda _i: self.save("network.refresh_days",
                                                                      self.refresh.currentData(), self.refresh))
        saved.add(Row(tr("Look up again after"), self.refresh,
                      tr("An older saved answer is asked for again, so database corrections reach your tags. Only "
                         "when an album is processed again, never in the background. Never: saved answers are "
                         "kept for good.")))
        self.lay.addWidget(saved)
        scans = Group(tr("Reading scans"), tr("Text recognition (OCR) reads barcodes and catalogue numbers off cover "
                                              "scans and booklets, on this computer; nothing is sent."))
        langs = list((data.get("ocr") or {}).get("languages") or [])
        self.langs = ListField(tr("Scan languages"), tr("Tesseract language codes, such as eng, deu or jpn."))
        self.langs.set_value(langs)
        self.langs.changed.connect(lambda: self.save("ocr.languages", self.langs.value(), self.langs))
        scans.add(Row(tr("Scan languages"), self.langs, tr("The languages printed on your scans. None listed: English "
                                                          "and your system language.")))
        self.effort = Segmented(tr("Scan reading"))
        for key, words in (("quick", N_("Quick")), ("standard", N_("Standard")), ("thorough", N_("Thorough"))):
            self.effort.addItem(tr(words), key)
        self.effort.setCurrentIndex(max(0, self.effort.findData((data.get("ocr") or {}).get("effort", "standard"))))
        self.effort.currentIndexChanged.connect(lambda _i: self.save("ocr.effort", self.effort.currentData(),
                                                                                       self.effort))
        scans.add(Row(tr("Scan reading"), self.effort,
                      tr("How many images are read per album: Quick up to 8, Standard 15, Thorough 40 (that can "
                         "take minutes per album).")))
        self.lay.addWidget(scans)
        self.finish()


class FilesPane(SettingsPane):
    """File Safety: this computer's undo backups and where duplicate copies go (app_settings)."""

    def __init__(self, prefs=None):
        super().__init__(tr("File Safety"), tr("How long changes can be undone on this computer, and where duplicate "
                                               "copies go. Every change is checked against the audio first."))
        import app_settings
        self.prefs = prefs
        data = app_settings.load()
        undo = Group(tr("Undo"))
        self.keep = _popup(tr("Keep undo backups for"), KEEP_CHOICES, data["backups"]["keep_days"])
        self.keep.activated.connect(self.set_keep)
        undo.add(Row(tr("Keep undo backups for"), self.keep, tr("Backups are copies of the files as they were, so Undo "
                                                               "works for this long after a change.")))
        self.auto = Switch(tr("Delete old backups automatically"))
        self.auto.setChecked(bool(data["backups"]["auto_clean"]))
        self.auto.clicked.connect(self.set_auto)
        undo.add(Row(tr("Delete old backups automatically"), self.auto,
                     tr("After each apply, backups older than this are deleted, and those changes can no longer be "
                        "undone. Off: they stay until you delete them.")))
        self.lay.addWidget(undo)
        dup = Group(tr("Duplicates"), tr("When two copies of the same tracks are found, the one not kept is moved "
                                         "out of the way. Nothing is ever deleted."))
        self.dupes = QLineEdit(str(data["duplicates"]["folder_name"]))
        self.dupes.setAccessibleName(tr("Duplicate copies folder"))
        self.dupes.setMinimumWidth(160)
        self.dupes.editingFinished.connect(self.set_dupes)
        dup.add(Row(tr("Duplicates folder"), self.dupes,
                    tr("Duplicate copies are moved into a folder of this name at the top of the library, keeping "
                       "their artist and album folders. Look through it and empty it yourself.")))
        self.lay.addWidget(dup)
        self.finish()

    def _losing(self, days, auto) -> list:
        """The jobs whose Undo would end at once with these settings."""
        import executor
        return executor.would_prune(days) if days and auto else []

    def _confirm_loss(self, days) -> bool:
        """Shortening the backups (or turning on cleaning) ends Undo for older jobs: a sheet says
        which, before anything is saved."""
        gone = self._losing(days, True)
        if not gone:
            return True
        listed = ", ".join(gone[:5]) + (tr(" and {0} more").format(len(gone) - 5) if len(gone) > 5 else "")
        return sheets.confirm(self, tr("Delete the backups of older jobs?"),
                              (tr("{n} job is older than this: {0}.") if len(gone) == 1 else
                               tr("{n} jobs are older than this: {0}.")).format(listed, n=len(gone)),
                              tr("Their backups are deleted after the next apply, and they can no longer be "
                                 "undone."),
                              tr("Delete Old Backups"), destructive=True)

    def set_keep(self, _i):
        import app_settings
        days = self.keep.currentData()
        was = app_settings.get("backups.keep_days", 30)
        shorter = days and (not was or days < was)
        if shorter and self.auto.isChecked() and not self._confirm_loss(days):
            self.keep.setCurrentIndex(max(0, self.keep.findData(was)))
            return
        self.save("backups.keep_days", days)

    def set_auto(self, on):
        if on and not self._confirm_loss(self.keep.currentData()):
            self.auto.setChecked(False)
            return
        self.save("backups.auto_clean", bool(on))

    def set_dupes(self):
        import app_settings
        name = self.dupes.text().strip()
        if name == app_settings.duplicates_folder():
            return
        if not self.save("duplicates.folder_name", name):
            self.dupes.setText(app_settings.duplicates_folder())


class PluginsPane(SettingsPane):
    """Plugins: what other people wrote to add to TagMender (plugins.py, docs/PLUGINS.md). Each
    installed plugin with its light and switch, its settings, Remove; Install from File…."""

    def __init__(self):
        super().__init__(tr("Plugins"), tr("Plugins add script functions and tag changes written by other people. "
                                           "A plugin runs with your permissions: install only ones you trust."))
        tools = QHBoxLayout()
        for text, name, slot in ((tr("Install from File…"), tr("Install a plugin from its .zip file"), self.install),
                                 (tr("Open Plugins Folder"), tr("Show the folder plugins are kept in"), self.open_folder),
                                 (tr("Plugin Guide"), tr("How plugins work and how to write one (online)"),
                                  lambda: self._open_url(GUIDE))):
            b = QPushButton(text)
            b.setAccessibleName(name)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addStretch(1)
        self.lay.addLayout(tools)
        self.holder = QVBoxLayout()
        self.holder.setSpacing(16)
        self.lay.addLayout(self.holder)
        self.finish()
        self.reload()

    @staticmethod
    def _open_url(url):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def reload(self):
        import plugins
        while self.holder.count():
            w = self.holder.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        rows = plugins.installed()
        if not rows:
            g = Group(tr("Installed"), tr("No plugins installed. Install from File… adds one; the Plugin Guide "
                                          "says how to write your own."))
            self.holder.addWidget(g)
            return
        for pl in rows:
            self.holder.addWidget(self._group(pl))

    def _group(self, pl):
        import plugins
        g = Group(pl.name, pl.manifest.get("description", ""))
        if pl.error:
            state, words = "error", tr("Not working: {0}").format(pl.error)
        elif pl.enabled and pl.loaded:
            state, words = "ok", tr("On and working")
        elif pl.enabled:
            state, words = "warn", tr("On: it starts the next time TagMender opens")
        else:
            state, words = "off", tr("Off")
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        sw = Switch(tr("Use {0}").format(pl.name))
        sw.setChecked(pl.enabled)
        sw.setEnabled(not pl.manifest == {})                # (a folder that is no plugin cannot be switched on)
        sw.clicked.connect(lambda on, pid=pl.id: self.switch(pid, on))
        h.addWidget(sw)
        rm = QPushButton(tr("Remove…"))
        rm.setAccessibleName(tr("Remove {0}").format(pl.name))
        rm.setAutoDefault(False)
        rm.clicked.connect(lambda _=False, p_=pl: self.remove(p_))
        h.addWidget(rm)
        bits = [f"{tr('Version')} {pl.version}"] + ([pl.manifest["author"]] if pl.manifest.get("author") else [])
        g.add(Row(pl.name, line, " · ".join(bits)))
        light = StatusLight(state, words)                    # (its own line: an error's words can be long)
        light.setWordWrap(True)
        light.setContentsMargins(16, 0, 16, 8)
        g.add_widget(light)
        for spec in pl.manifest.get("settings", []):
            g.add(self._setting_row(pl, spec, plugins))
        return g

    def _setting_row(self, pl, spec, plugins):
        kind, key = spec.get("kind", "text"), spec["key"]
        try:
            value = plugins.setting_value(pl.id, spec)
        except plugins.PluginError as exc:
            value, problem = "", str(exc)
        else:
            problem = ""
        if kind == "switch":
            c = Switch(spec["label"])
            c.setChecked(bool(value))
            c.clicked.connect(lambda on, pid=pl.id, k=key: self.set_value(pid, k, bool(on)))
        else:
            c = QLineEdit("" if kind == "secret" else str(value or ""))
            c.setAccessibleName(spec["label"])
            if kind == "secret":
                c.setEchoMode(QLineEdit.Password)
                c.setPlaceholderText(tr("Saved on this computer") if value else tr("Not set"))
                c.editingFinished.connect(lambda e=c, pid=pl.id, k=key: self.set_secret(pid, k, e))
            else:
                c.editingFinished.connect(lambda e=c, pid=pl.id, k=key: self.set_value(pid, k, e.text().strip()))
        row = Row(spec["label"], c, spec.get("help", ""))
        if problem:
            row.set_problem(problem)
        return row

    def switch(self, pid, on):
        import plugins
        plugins.set_enabled(pid, on)
        if on:
            plugins.load_enabled()                           # (on now; off takes effect at the next start)
        self.said.setText(tr("Switched on.") if on else tr("Switched off: it stops the next time TagMender opens."))
        self.reload()

    def set_value(self, pid, key, value):
        import app_settings
        allv = dict(app_settings.get("plugins.settings") or {})
        allv[pid] = dict(allv.get(pid) or {}, **{key: value})
        self.save("plugins.settings", allv)

    def set_secret(self, pid, key, edit):
        import plugins
        text = edit.text().strip()
        if text:
            plugins.store_secret(pid, key, text)
            edit.clear()
            edit.setPlaceholderText(tr("Saved on this computer"))
            self.said.setText(tr("Saved."))

    def install(self):
        import plugins
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getOpenFileName(self, tr("Install a plugin"), str(Path.home()),
                                               tr("Plugins (*.zip)"))
        if not path:
            return
        try:
            pl = plugins.install(path)
        except (plugins.PluginError, OSError) as exc:
            sheets.warning(self, tr("The plugin was not installed."), str(exc))
            return
        self.said.setText(tr("Installed {0}. It is off until you switch it on.").format(pl.name))
        self.reload()

    def remove(self, pl):
        import plugins
        if not sheets.confirm(self, tr("Remove the plugin “{0}”?").format(pl.name),
                              tr("It is switched off and its folder is moved aside."),
                              tr("It is kept in the plugins folder, under .removed, to bring it back."),
                              tr("Remove Plugin"), destructive=True):
            return
        try:
            plugins.remove(pl.id)
        except (plugins.PluginError, OSError) as exc:
            sheets.warning(self, tr("The plugin was not removed."), str(exc))
            return
        self.reload()

    def open_folder(self):
        import plugins
        from .common import open_folder
        d = plugins.plugins_dir()
        d.mkdir(parents=True, exist_ok=True)
        open_folder(d)


class KeysPane(QWidget):
    def __init__(self):
        super().__init__()
        import credentials as cr
        self.cr = cr
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        lay.addLayout(heading(tr("Provider keys"), tr("Stored only on this computer, readable by you alone, and never "
                                                "shown again or written to logs.")))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)
        self.fields = {}
        grouped = [n for _t, names in KEY_GROUPS for n in names] + list(ACCOUNT_KEYS)
        groups = [(t, [n for n in names if n in cr.FILES]) for t, names in KEY_GROUPS] + \
            [(N_("Other"), [n for n in cr.FILES if n not in grouped])]
        order = []
        for title, names in groups:                   # every key credentials.py knows, web search engines too
            if names:
                order.append(title)
                order += names
        for name in order:
            if name not in cr.FILES:
                form.addRow(section(tr(name)))
                continue
            short, note = (tr(x) for x in KEYS.get(name, (cr.LABELS[name], "")))
            e = QLineEdit()
            e.setEchoMode(QLineEdit.Password)
            e.setMinimumWidth(260)
            e.setAccessibleName(short)
            e.setAccessibleDescription(cr.LABELS[name])
            e.setToolTip(cr.LABELS[name])
            e.setPlaceholderText(self.placeholder(name))
            e.returnPressed.connect(self.save)              # (Return saves, as the default button would)
            self.fields[name] = e
            form.addRow(short, _with_caption(e, note) if note else e)
        scroll = QScrollArea()                                # (ten keys: at 520 px high they scroll)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAccessibleName(tr("Provider keys"))
        body = QWidget()
        body.setLayout(form)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        lay.addWidget(separator())                            # (the footer under a hairline: the list scrolls above it)
        row = QHBoxLayout()
        self.saved = QLabel("")
        self.saved.setProperty("role", "hint")
        self.saved.setAccessibleDescription(tr("Saved keys"))   # (no fixed name: "Saved 2 keys." is what is read)
        row.addWidget(self.saved, 1)
        sb = QPushButton(tr("Save Keys"))
        sb.setProperty("role", "primary")
        sb.setAccessibleName(tr("Save keys"))
        sb.setDefault(True)
        sb.clicked.connect(self.save)
        row.addWidget(sb)
        lay.addLayout(row)

    def typed(self) -> int:
        """Keys typed or pasted and not saved yet."""
        return sum(1 for e in self.fields.values() if e.text().strip())

    def discard(self):
        for e in self.fields.values():
            e.clear()

    def placeholder(self, name):
        src = self.cr.source(name)
        return (tr(SOURCE[src]) if src in SOURCE else tr("Set ({src})").format(src=src)) if src else tr("Not set")

    def save(self):
        n = 0
        for name, e in self.fields.items():
            if e.text().strip():
                self.cr.store(name, e.text())
                e.clear()
                e.setPlaceholderText(self.placeholder(name))
                n += 1
        self.saved.setText(tr("Saved {0}.").format(plural(n, tr("key"), tr("keys"))) if n else
                           tr("Nothing to save: type a key first."))


def key_field(cr, name, save):
    """A key's field: typed as dots, its placeholder says whether one is saved (never the key)."""
    short = tr(KEYS.get(name, (cr.LABELS[name], ""))[0])
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setMinimumWidth(260)
    e.setAccessibleName(short)
    e.setAccessibleDescription(cr.LABELS[name])
    e.setToolTip(cr.LABELS[name])
    src = cr.source(name)
    e.setPlaceholderText((tr(SOURCE[src]) if src in SOURCE else tr("Set ({src})")
                          .format(src=src)) if src else tr("Not set"))
    e.returnPressed.connect(save)
    return e


class AccountPane(QWidget):
    """Your own logins and keys, and what may reach TagMender from outside: MusicBrainz (your
    application's client ID and secret, Log In / Log Out, your collections), your AcoustID user
    key, browser integration and the CD drive. Nothing here sends anything by itself: Log In says
    what is sent first (Album > Collections… acts on the album selected, so it is not here)."""

    def __init__(self, win, owner=None):
        super().__init__()
        import credentials as cr
        self.cr, self.win, self.owner = cr, win, owner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        outer.addLayout(heading(tr("Account"), tr("Your own MusicBrainz and AcoustID logins: nothing is bundled, and "
                                           "nothing is sent until you choose to send it.")))
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(style.SPACE["s"])
        self.fields = {}
        # MusicBrainz: your application, then the login
        lay.addLayout(section(tr("MusicBrainz")))
        from .account import AccountPane as LoginPane
        self.login = LoginPane(win, keys_here=True)            # (its words: the client ID and secret are here)
        self.login.layout().setContentsMargins(0, 0, 0, 0)
        forms = [self.login.form]                            # (your application's keys head the login's form)
        for i, name in enumerate(("mb_client_id", "mb_client_secret")):
            e = self.fields[name] = key_field(cr, name, self.save)
            short, note = (tr(x) for x in KEYS[name])
            forms[0].insertRow(i, short, _with_caption(e, note))
        # (its own title and its "Setting it up" heading: this pane's MusicBrainz section says it, so the
        # numbered steps sit right under it)
        for head in (self.login.layout().itemAt(0).layout(), self.login.how_head):
            for i in range(head.count()):
                if head.itemAt(i).widget() is not None:
                    head.itemAt(i).widget().hide()
        self.login.how_head.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.login)
        # AcoustID
        lay.addLayout(section(tr("AcoustID")))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        e = self.fields["acoustid_user"] = key_field(cr, "acoustid_user", self.save)
        form.addRow(tr("User key"), _with_caption(e, tr(KEYS["acoustid_user"][1])))
        lay.addLayout(form)
        forms.append(form)
        # what may reach TagMender from outside: the browser, a CD
        lay.addLayout(section(tr("Browser and CD")))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        from . import browser
        self.browser = browser.add_preferences(form, win)
        lb = form.labelForField(self.browser)                # (its first line beside the check box, not the caption)
        if lb is not None:
            lb.setContentsMargins(0, max(0, (self.browser._spin.sizeHint().height() - lb.sizeHint().height()) // 2),
                                  0, 0)
        self.drive = QComboBox()
        self.drive.setAccessibleName(tr("CD drive"))
        self.drive.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.drive.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.drive_note = _caption("")
        self.fill_drives()
        self.drive.currentIndexChanged.connect(self.drive_changed)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(self.drive)
        v.addWidget(self.drive_note)
        form.addRow(_row_label(tr("CD drive"), self.drive), box)
        lay.addLayout(form)
        forms.append(form)
        lay.addStretch(1)
        labels = [f.itemAt(r, QFormLayout.LabelRole).widget() for f in forms for r in range(f.rowCount())
                  if f.itemAt(r, QFormLayout.LabelRole) is not None]
        most = max((lb.sizeHint().width() for lb in labels if lb is not None), default=0)
        for lb in labels:                                    # (one label column down the pane)
            if lb is not None:
                lb.setMinimumWidth(most)
                lb.setAlignment(Qt.AlignRight | (lb.alignment() & Qt.AlignVertical_Mask))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAccessibleName(tr("Account"))
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        outer.addWidget(separator())
        foot = QHBoxLayout()
        self.saved = QLabel("")
        self.saved.setProperty("role", "hint")
        self.saved.setAccessibleDescription(tr("Saved keys"))
        foot.addWidget(self.saved, 1)
        sb = self.save_button = QPushButton(tr("Save"))
        sb.setProperty("role", "primary")                    # (the pane's one prominent button)
        sb.setAccessibleName(tr("Save the client ID, client secret and AcoustID user key"))
        sb.setToolTip(tr("Save the client ID, client secret and AcoustID user key on this computer"))
        sb.clicked.connect(self.save)
        foot.addWidget(sb)
        outer.addLayout(foot)
        self.first = self.fields["mb_client_id"]
        self.order_tabs()

    def order_tabs(self):
        """Tab in reading order: the how-to's link, your application's ID and secret (made after the
        login's own widgets, so not first by themselves), the login, AcoustID, browser, CD, Save."""
        lg, br = self.login, self.browser
        chain = [lg.how, self.fields["mb_client_id"], self.fields["mb_client_secret"], lg.server, lg.keys_button,
                 lg.out_button, lg.in_button, self.fields["acoustid_user"], br._check, br._spin, self.drive,
                 self.save_button]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    # ---- the keys ---------------------------------------------------------
    def typed(self) -> int:
        return sum(1 for e in self.fields.values() if e.text().strip())

    def discard(self):
        for e in self.fields.values():
            e.clear()

    def save(self):
        n = 0
        for name, e in self.fields.items():
            if e.text().strip():
                self.cr.store(name, e.text())
                e.clear()
                src = self.cr.source(name)
                e.setPlaceholderText((tr(SOURCE[src]) if src in SOURCE else tr("Set ({src})")
                                      .format(src=src)) if src else tr("Not set"))
                n += 1
        self.saved.setText(tr("Saved {0}.").format(plural(n, tr("key"), tr("keys"))) if n else
                           tr("Nothing to save: type a key first."))
        self.login.refresh()                                 # (Log In needs the client ID and secret)

    # ---- the CD drive ---------------------------------------------------------
    def fill_drives(self):
        import cdlookup
        want = _gui_prefs().get(CD_DRIVE, "") or ""
        self.drive.blockSignals(True)
        self.drive.clear()
        self.drive.addItem(tr("The default drive"), "")
        for d in cdlookup.drives():
            self.drive.addItem(d, d)
        if want and self.drive.findData(want) < 0:
            self.drive.addItem(tr("{want} (not found now)").format(want=want), want)
        self.drive.setCurrentIndex(max(0, self.drive.findData(want)))
        self.drive.blockSignals(False)
        can = cdlookup.can_read_drive()
        from . import browser
        self.drive.setEnabled(can)
        self.drive_note.setText(tr("File > Look Up CD reads the disc in this drive; a rip log or a pasted table of "
                                   "contents works without one.") if can else tr(browser.NO_DISCID))

    def drive_changed(self, _i):
        _set_gui_pref(CD_DRIVE, self.drive.currentData() or "")


class CheckList(QScrollArea):
    """The checks as rows: the status icon in its colour and a word, what was found, and the fix
    as a caption under it (monospace is for the exported text only)."""

    def __init__(self, open_keys=None, open_setup=None):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAccessibleName(tr("Check results"))
        self.open_keys, self.open_setup = open_keys, open_setup
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.setSpacing(12)
        self.setWidget(self.body)
        self.text = ""
        self.message(tr("Run Checks to see what is set up."))

    def clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget() is not None:                    # (hidden at once: deleted later)
                it.widget().hide()
                it.widget().deleteLater()

    def message(self, text):
        """One line in place of the rows ("Running checks…", a failure)."""
        self.clear()
        lb = QLabel(text)
        lb.setProperty("role", "hint")
        lb.setWordWrap(True)
        self.lay.addWidget(lb)
        self.lay.addStretch(1)
        self.text = text

    def show_found(self, found):
        self.clear()
        lines = []
        for c in found:
            icon, key, word = CHECK_STATE.get(c.status, CHECK_STATE["warn"])
            word = tr(word)
            row = QHBoxLayout()
            row.setSpacing(8)
            pic = QLabel()
            pic.setPixmap(icons.icon(icon, key, 16).pixmap(16, 16))
            pic.setAlignment(Qt.AlignTop)
            pic.setAccessibleName(word)
            row.addWidget(pic, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            colour = style.status_color(key).name()          # (the word in the status colour, beside the icon)
            head = QLabel(tr('<span style="color:{colour}; font-weight:600">{0}</span>&nbsp;&nbsp;'
                             "{1}: {2}").format(html.escape(word), html.escape(c.name), html.escape(c.detail),
                                             colour=colour))
            head.setTextFormat(Qt.RichText)
            head.setWordWrap(True)
            head.setTextInteractionFlags(Qt.TextSelectableByMouse)
            head.setAccessibleName(f"{word}: {c.name}: {c.detail}")
            col.addWidget(head)
            if c.fix:
                link = ""
                if "Setup" in c.fix and self.open_setup:          # (a Setup notice: its row on the page)
                    link = tr(' · <a href="setup">Open Setup</a>')
                elif "Keys" in c.fix and self.open_keys:
                    link = tr(' · <a href="keys">Open Keys</a>')
                fix = QLabel(html.escape(c.fix) + link)
                fix.setTextFormat(Qt.RichText)
                fix.setProperty("role", "caption")
                fix.setWordWrap(True)
                fix.setTextInteractionFlags(Qt.TextBrowserInteraction)
                fix.setAccessibleName(tr("Fix: {fix}").format(fix=c.fix))
                fix.linkActivated.connect(lambda lk, cf=c: self.follow(lk, cf))
                col.addWidget(fix)
            row.addLayout(col, 1)
            w = QWidget()
            w.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.lay.addWidget(w)
            lines.append(f"{word} {c.name}: {c.detail}" + (f"\n    fix: {c.fix}" if c.fix else ""))
        self.lay.addStretch(1)
        self.text = "\n".join(lines)

    def follow(self, link, check):
        if link == "setup":
            self.open_setup(_setup_id(check))
        elif self.open_keys:
            self.open_keys()

    # (the old text box's reading, for callers and tests)
    def toPlainText(self):
        return self.text

    def setPlainText(self, text):
        self.message(text)


def _setup_id(check):
    """The Setup row a doctor check is about (its old check name, requirements.Entry.check)."""
    import requirements as rq
    return next((e.id for e in rq.ENTRIES if e.check == check.name), None)


class ChecksPane(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        lay.addLayout(heading(tr("Checks"), tr("What TagMender needs (ffmpeg, the Discogs Enrichment and CoverUp code, keys, folders) and "
                                        "whether it is there.")))
        self.checks = CheckList(lambda: owner.show_tab("Keys") if hasattr(owner, "show_tab") else None,
                                self.open_setup)
        lay.addWidget(self.checks, 1)
        lay.addWidget(separator())                            # (the buttons under a hairline: the list scrolls)
        self.anon = QCheckBox(tr("Hide folder and file names in the export"))   # (its own row, over Export)
        self.anon.setAccessibleName(tr("Hide folder and file names in the export"))
        self.anon.setToolTip(tr("The export names folders and files by codes instead"))
        lay.addWidget(self.anon, 0, Qt.AlignRight)
        row = QHBoxLayout()
        self.export_btn = xb = QPushButton(tr("Export Diagnostics…"))
        xb.setAccessibleName(tr("Export diagnostics"))
        xb.clicked.connect(self.export)
        self.note = QLabel("")
        self.note.setProperty("role", "hint")
        self.run_btn = rb = QPushButton(tr("Run Checks"))       # (the pane's one prominent action)
        rb.setAccessibleName(tr("Run checks"))
        rb.setProperty("role", "primary")
        rb.setDefault(True)
        rb.clicked.connect(self.run_checks)
        row.addWidget(xb)
        row.addWidget(self.note, 1)
        row.addWidget(rb)
        lay.addLayout(row)
        self.first = rb                                      # (the focus when the pane opens, not the log)

    def open_setup(self, id=None):
        """A check's Open Setup: the main window's Setup page, at the row the check is about."""
        from . import setup
        setup.open_setup(getattr(self.owner, "win", None) or self.owner, id)

    def run_checks(self):
        """doctor's checks on a worker thread: they read every profile, key file and cache, which
        could hold the window up. Run Checks is off until they are back (a second run would
        overwrite the first)."""
        import doctor
        if not self.run_btn.isEnabled():
            return

        def show(found):
            self.run_btn.setEnabled(True)
            self.checks.show_found(found)

        def failed(msg):
            self.run_btn.setEnabled(True)
            self.checks.message(tr("The checks failed: {msg}").format(msg=msg))
        self.run_btn.setEnabled(False)
        self.checks.message(tr("Running checks…"))
        start(self.owner, lambda _w: doctor.checks(), show, failed=failed)

    def export(self):
        """The zip is built on a worker (a large state folder takes a while)."""
        import doctor
        path = sheets.save_file(self, tr("Save the diagnostics for a problem report"),
                                str(Path.home() / "tagmender-diagnostics.zip"), tr("Zip (*.zip)"))
        if not path:
            return
        anon = self.anon.isChecked()
        self.export_btn.setEnabled(False)
        self.note.setText(tr("Exporting the diagnostics…"))

        def done(_r):
            self.export_btn.setEnabled(True)
            self.note.setText("")
            sheets.information(self, tr("Saved {name}.").format(name=Path(path).name),
                               tr("{path}\nIt holds no keys or tokens.").format(path=path))

        def failed(msg):
            self.export_btn.setEnabled(True)
            self.note.setText("")
            sheets.warning(self, tr("The diagnostics were not saved."), msg)
        start(self.owner, lambda _w: doctor.export(path, anonymize_paths=anon), done, failed=failed)


class AdvancedPane(ChecksPane):
    """Advanced: the plugin folder (code that runs inside TagMender, so this computer's own
    setting, never a profile's), then TagMender's checks and the diagnostics export."""

    def __init__(self, owner):
        super().__init__(owner)
        import app_settings
        lay = self.layout()
        head = lay.itemAt(0).layout()
        head.itemAt(0).widget().setText(tr("Advanced"))
        head.itemAt(1).widget().setText(tr("The plugin folder, and TagMender's checks: what it needs (ffmpeg, the "
                                           "Discogs Enrichment and CoverUp code, keys, folders) and whether it is "
                                           "there."))
        group = Group(tr("Plugins"), tr("Code in this folder runs inside TagMender with your rights. Choose only a "
                                        "folder you trust. Empty: the copies that come with TagMender."))
        self.plugins = FolderField(tr("Plugin folder"), tr("Choose the plugin folder"))
        self.plugins.setText(str(app_settings.get("plugins.folder") or ""))
        self.plugins.edit.setPlaceholderText(tr("The copies that come with TagMender"))
        self.plugins.edit.editingFinished.connect(self.set_plugins)
        self.plugins.button.clicked.connect(self.set_plugins)
        group.add(Row(tr("Plugin folder"), self.plugins,
                      tr("Holds the Discogs Enrichment and CoverUp code, each in a folder of its own.")))
        lay.insertWidget(1, group)
        self.said = QLabel("")
        self.said.setProperty("role", "hint")
        lay.insertWidget(2, self.said)
        checks = QLabel(tr("Checks").upper())
        checks.setProperty("role", "grouptitle")
        checks.setAccessibleName(tr("Checks"))
        lay.insertWidget(3, checks)
        self.anon.setChecked(bool(app_settings.get("diagnostics.hide_names", True)))
        self.anon.toggled.connect(lambda on: self.save("diagnostics.hide_names", bool(on), self.anon))

    def save(self, name, value, control=None) -> bool:
        return _save_setting(self, name, value, control)

    def set_plugins(self):
        import app_settings
        text = self.plugins.text().strip()
        if text == (app_settings.get("plugins.folder") or ""):
            return
        if not self.save("plugins.folder", text):
            self.plugins.setText(app_settings.get("plugins.folder") or "")


class Preferences(QMainWindow):
    def __init__(self, win=None):
        super().__init__(None)
        self.win = win
        self.setObjectName("preferences")
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setMinimumWidth(640)
        self.resize(640, self.TALL)
        bar = QToolBar(tr("Preferences"))
        bar.setObjectName("prefsbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        bar.setIconSize(icons.size(24))
        bar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(bar)
        self.bar = bar
        self.stack = QStackedWidget()
        self.general = GeneralPane(win)
        self.keys = KeysPane()
        self.account = AccountPane(win, self)
        self.network = NetworkPane()
        self.files = FilesPane(self)
        self.checks_pane = AdvancedPane(self)
        self.plugins = PluginsPane()
        self.panes = {"General": self.general, "Keys": self.keys, "Account": self.account,
                      "Network": self.network, "Files": self.files, "Advanced": self.checks_pane,
                      "Plugins": self.plugins}
        group = QActionGroup(self)
        group.setExclusive(True)
        self.tabs = {}
        for i, (name, label, icon) in enumerate(PANES):
            a = QAction(icons.icon(icon, "ink", 24), tr(label).replace("&", "&&"), self)   # (no mnemonic)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            a.setToolTip(tr("{name} (Ctrl+{0})").format(i + 1, name=tr(label)))
            a.triggered.connect(lambda _=False, n=name: self.show_tab(n))
            group.addAction(a)
            bar.addAction(a)
            b = bar.widgetForAction(a)
            b.setAccessibleName(tr(label))
            b.setFocusPolicy(Qt.TabFocus)                     # (a toolbar's buttons take no focus otherwise)
            self.tabs[name] = a
            self.stack.addWidget(self.panes[name])
        close = QAction(tr("Close"), self)
        close.setShortcuts([QKeySequence("Ctrl+W"), QKeySequence("Esc")])
        close.triggered.connect(self.close)
        self.addAction(close)
        if win is not None:                                  # (Ctrl+Q and the other app-wide keys work here too)
            for key in ("quit", "preferences", "help", "shortcuts"):
                self.addAction(win.commands[key])
        self.setCentralWidget(self.stack)
        last = PANE_ALIASES.get(_gui_prefs().get("preferences_tab", "General"),
                                _gui_prefs().get("preferences_tab", "General"))
        self.show_tab(last if last in self.panes else "General")

    # the old Settings page's names (tests and callers use them)
    @property
    def fields(self):
        """Every key's field: the providers' (Keys) and yours (Account)."""
        return {**self.keys.fields, **self.account.fields}

    @property
    def checks(self):
        return self.checks_pane.checks

    @property
    def anon(self):
        return self.checks_pane.anon

    def save(self):
        self.keys.save()
        if self.account.typed():
            self.account.save()

    def run_checks(self):
        self.checks_pane.run_checks()

    def export(self):
        self.checks_pane.export()

    def show_tab(self, name):
        name = PANE_ALIASES.get(name, name)
        self.tabs[name].setChecked(True)
        self.stack.setCurrentWidget(self.panes[name])
        self.setWindowTitle(tr(PREFS_TITLE).format(tr(next(lb for n, lb, _i in PANES if n == name))))
        self.fit_pane(name)
        _set_gui_pref("preferences_tab", name)
        first = getattr(self.panes[name], "first", None)
        if first is not None:
            first.setFocus()

    TALL = 600                                              # every pane scrolls inside this height

    def fit_pane(self, name):
        """The panes scroll, so the window keeps one height; the width stays the person's."""
        self.setMinimumHeight(320)
        self.resize(max(self.width(), 640), self.TALL)

    def current_tab(self):
        return next(n for n, p in self.panes.items() if p is self.stack.currentWidget())

    def retint(self):
        for name, _label, icon in PANES:
            self.tabs[name].setIcon(icons.icon(icon, "ink", 24))

    def closeEvent(self, e):
        """A key typed and not saved is never dropped without a word: Save, Don't Save or stay."""
        n = self.keys.typed() + self.account.typed()
        if n:
            ans = sheets.ask(self, tr("Save the {0} you typed?").format(plural(n, tr('key'), tr('keys'))),
                             tr("Keys are kept only when saved. Don't Save clears what was typed."),
                             buttons=(tr("Cancel"), tr("Don't Save"), tr("Save")), default=tr("Save"),
                             offscreen=tr("Don't Save"))
            if ans == tr("Save"):
                self.keys.save()
                self.account.save()
            elif ans == tr("Don't Save"):
                self.keys.discard()
                self.account.discard()
            else:
                e.ignore()
                return
        e.accept()

