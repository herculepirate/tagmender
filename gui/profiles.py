"""gui/profiles.py - The Profiles page: settings per profile, its Ignore/Skip rules and the rule tester."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import icons, sheets
from .common import ex, heading, left_header, pf, plural, popup_menu, separator
from .i18n import N_, tr
from .profile_editor import (ART_SOURCE_CHOICES, LEVEL_WORDS, NAMING_WORDS, OPERATION_WORDS,  # noqa: F401
                             SETTINGS, SOURCE_WORDS, UNRESOLVED_WORDS, PresetSheet, ProfileEditor, preset_data)
from .widgets import FlowLayout
from .work import start

# what the rule sheet's combo boxes show for each key the rules store (sentence case, no slugs)
ACTION_WORDS = {"ignore": N_("Ignore"), "skip": N_("Skip")}
TARGET_WORDS = {"album": N_("Album"), "file": N_("File")}
FIELD_WORDS = {"album": N_("Album"), "title": N_("Title"), "artist": N_("Artist"), "albumartist": N_("Album artist"),
               "genre": N_("Genre"),
               "filename": N_("File name"), "folder": N_("Folder"), "path": N_("Path"), "format": N_("Format")}
OP_WORDS = {"contains": N_("Contains"), "equals": N_("Is"), "starts_with": N_("Starts with"),
            "ends_with": N_("Ends with"),
            "glob": N_("Matches pattern (glob)"), "regex": N_("Matches regular expression")}


def _fill_combo(box, keys, words):
    """Items show words and keep the key as their data (read and saved with currentData())."""
    for k in keys:
        box.addItem(tr(words.get(k, k)), k)


def _set_data(box, key):
    """Select the item for key; a key this version has no word for is added as it is."""
    i = box.findData(key)
    if i < 0 and key is not None:
        box.addItem(str(key), key)
        i = box.count() - 1
    box.setCurrentIndex(max(0, i))


class RuleDialog(QDialog):
    """Add or edit one Ignore/Skip rule: conditions joined by all (AND) or any (OR)."""

    def __init__(self, parent, rule=None):
        super().__init__(parent.window() if parent is not None else None)
        sheets.prepare(self, tr("Rule"))
        self.resize(640, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        head = QLabel(tr("Edit the rule") if rule else tr("New rule"))
        head.setProperty("role", "headline")
        lay.addWidget(head)
        form = QFormLayout()
        self.name = QLineEdit(rule.name if rule else "")
        self.action = QComboBox()
        _fill_combo(self.action, ("ignore", "skip"), ACTION_WORDS)
        self.action.setToolTip(tr("Ignore: left alone in every run while the rule applies. Skip: this run only."))
        self.target = QComboBox()
        _fill_combo(self.target, ("album", "file"), TARGET_WORDS)
        self.match = QComboBox()
        self.match.addItems([tr("all conditions (AND)"), tr("any condition (OR)")])
        for w, n in ((self.name, tr("Rule name")), (self.action, tr("Action")), (self.target, tr("Applies to")),
                     (self.match, tr("Match"))):
            w.setAccessibleName(n)
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Action"), self.action)
        form.addRow(tr("Applies to"), self.target)
        form.addRow(tr("Match"), self.match)
        lay.addLayout(form)
        self.conds = QTableWidget(0, 5)                       # (a row of real controls per condition)
        self.conds.setHorizontalHeaderLabels([tr("Field"), tr("Operator"), tr("Text"), tr("Case-sensitive"), ""])
        left_header(self.conds)
        self.conds.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.conds.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.conds.verticalHeader().hide()
        self.conds.setShowGrid(False)
        self.conds.setTabKeyNavigation(False)               # Tab goes Field, Operator, Text, Case-sensitive
        self.conds.setFocusPolicy(Qt.NoFocus)
        self.conds.setSelectionMode(QAbstractItemView.NoSelection)
        self.conds.setAccessibleName(tr("Conditions"))
        lay.addWidget(self.conds)
        row = QHBoxLayout()
        self.add_btn = b = QPushButton(tr("Add Condition"))        # (each condition has its own "−" to remove it)
        b.setAccessibleName(tr("Add condition"))
        b.clicked.connect(lambda: self.add_cond())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel(tr("Text is matched literally and ignoring case unless glob or regex is chosen: "
                         "[no vox] means exactly those characters."))
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        row, _ok = sheets.button_row(self, tr("Save Rule"), self.accept)   # (Cancel, then Save Rule on the right)
        lay.addLayout(row)
        self.rule_id = rule.id if rule else None
        self.enabled = rule.enabled if rule else True
        if rule:
            _set_data(self.action, rule.action)
            _set_data(self.target, rule.target)
            key = "any" if "any" in rule.when else "all"
            self.match.setCurrentIndex(1 if key == "any" else 0)
            for c in rule.when.get(key, []):
                if "field" in c:
                    self.add_cond(c)
        else:
            self.add_cond()

    def add_cond(self, c=None):
        c = c or {"field": "album", "op": "contains", "value": ""}
        r = self.conds.rowCount()
        self.conds.insertRow(r)
        f = QComboBox()
        f.setAccessibleName(tr("Field"))
        f.setEditable(True)                                  # (or a tag: "tag:media")
        _fill_combo(f, ex.FIELDS, FIELD_WORDS)
        f.addItem("tag:", None)
        f.setToolTip(tr("A field, or tag: and a tag's name, e.g. tag:media"))
        i = f.findData(c["field"])
        f.setCurrentIndex(i) if i >= 0 else f.setCurrentText(c["field"])
        o = QComboBox()
        o.setAccessibleName(tr("Operator"))
        _fill_combo(o, ex.OPS, OP_WORDS)
        _set_data(o, c.get("op", "contains"))
        t = QLineEdit(str(c.get("value", "")))
        t.setAccessibleName(tr("Text"))
        t.setFrame(False)
        cs = QCheckBox()
        cs.setAccessibleName(tr("Case-sensitive"))
        cs.setChecked(bool(c.get("case_sensitive")))
        rm = QToolButton()                                   # this condition's own Remove
        rm.setProperty("role", "tool")
        rm.setIcon(icons.icon("minus", "ink"))
        rm.setFocusPolicy(Qt.StrongFocus)
        rm.clicked.connect(lambda _=False, b=rm: self.remove_cond(b))
        self.conds.setCellWidget(r, 0, f)
        self.conds.setCellWidget(r, 1, o)
        self.conds.setCellWidget(r, 2, t)
        self.conds.setCellWidget(r, 3, cs)
        self.conds.setCellWidget(r, 4, rm)
        self.order_tabs()

    def order_tabs(self):
        """Tab: the rule's fields, then each condition's controls left to right, row by row; each
        row's "−" is named for its row."""
        for r in range(self.conds.rowCount()):
            b = self.conds.cellWidget(r, 4)
            b.setAccessibleName(tr("Remove condition {0}").format(r + 1))
            b.setToolTip(tr("Remove condition {0}").format(r + 1))
        chain = [self.match] + [self.conds.cellWidget(r, c) for r in range(self.conds.rowCount()) for c in range(5)] \
            + [self.add_btn]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    def remove_cond(self, button=None):
        """The condition whose "−" was pressed (no button: the last one)."""
        rows = range(self.conds.rowCount())
        row = next((r for r in rows if self.conds.cellWidget(r, 4) is button), self.conds.rowCount() - 1) \
            if button is not None else self.conds.rowCount() - 1
        if row >= 0:
            self.conds.removeRow(row)
            self.order_tabs()

    @staticmethod
    def field_of(box):
        """The field key: an item's data, or what was typed (tag:...)."""
        i = box.findText(box.currentText())
        return (box.itemData(i) if i >= 0 and box.itemData(i) else box.currentText()).strip()

    def rule(self) -> ex.Rule:
        conds = []
        for r in range(self.conds.rowCount()):
            c = {"field": self.field_of(self.conds.cellWidget(r, 0)),
                 "op": self.conds.cellWidget(r, 1).currentData(),
                 "value": self.conds.cellWidget(r, 2).text()}
            if self.conds.cellWidget(r, 3).isChecked():
                c["case_sensitive"] = True
            if c["value"]:
                conds.append(c)
        name = self.name.text().strip() or "rule"
        return ex.Rule.from_dict({"id": self.rule_id or ex._slug(name), "name": name,
                                  "action": self.action.currentData(), "target": self.target.currentData(),
                                  "enabled": self.enabled,
                                  "when": {("any" if self.match.currentIndex() else "all"): conds}})


class Profiles(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.addLayout(heading(tr("Profiles"), tr("How each profile tags, names and files music.")))
        lay = QHBoxLayout()
        lay.setSpacing(16)
        outer.addLayout(lay, 1)
        left = QVBoxLayout()
        # (a search field over the list, as in System Settings: it finds a setting in the
        # profile shown - and profiles by name - like the toolbar's search on this page)
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("Search settings"))
        self.find.setAccessibleName(tr("Search settings"))
        self.find.setClearButtonEnabled(True)
        self.find.setMaximumWidth(self.LIST_WIDE)
        self.find.textChanged.connect(self.filter)
        left.addWidget(self.find)
        self.list = QListWidget()                             # (a source list, as the sidebar)
        self.list.setObjectName("profileList")
        self.list.setAccessibleName(tr("Profiles"))
        self.list.setMaximumWidth(self.LIST_WIDE)
        self.list.setMinimumWidth(120)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self.row_changed)
        self.shown_name = None                                # the profile the form shows (None: global rules)
        self.shown_row = -1
        self.saved_values = None
        left.addWidget(self.list, 1)
        nb = QPushButton(tr("New Profile…"))
        nb.setAccessibleName(tr("New profile"))
        nb.setToolTip(tr("New profile (Ctrl+N)"))
        nb.clicked.connect(self.new)
        left.addWidget(nb)
        self.dup_btn = QPushButton(tr("Duplicate Profile…"))
        self.dup_btn.setAccessibleName(tr("Duplicate the profile shown"))
        self.dup_btn.clicked.connect(self.duplicate)
        left.addWidget(self.dup_btn)
        manage = QHBoxLayout()                               # (rename and delete the profile shown)
        manage.setSpacing(8)
        self.rename_btn = QPushButton(tr("Rename…"))
        self.rename_btn.setAccessibleName(tr("Rename the profile shown"))
        self.rename_btn.clicked.connect(self.rename)
        manage.addWidget(self.rename_btn)
        self.delete_btn = QPushButton(tr("Delete…"))
        self.delete_btn.setAccessibleName(tr("Delete the profile shown"))
        self.delete_btn.clicked.connect(self.delete)
        manage.addWidget(self.delete_btn)
        left.addLayout(manage)
        lay.addLayout(left, 1)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName(tr("Profile settings and rules"))
        lay.addWidget(self.tabs, 3)
        # settings tab: the editor, and under a hairline the one prominent action, Save Profile
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        self.editor = ProfileEditor(self)
        self.editor.edited.connect(self.changed)
        self.editor.apply_all.connect(self.apply_to_all)
        self.editor.open_rules.connect(lambda: self.tabs.setCurrentIndex(1))
        self.editor.open_script.connect(self.edit_script)     # (Script Editor: editable, a live preview)
        tl.addWidget(self.editor, 1)
        tl.addWidget(separator())
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 0, 16, 4)
        self.dirty_note = QLabel("")
        self.dirty_note.setProperty("role", "hint")
        srow.addWidget(self.dirty_note, 1)
        self.save_btn = save = QPushButton(tr("Save Profile"))
        save.setProperty("role", "primary")
        save.setAccessibleName(tr("Save profile"))
        save.setToolTip(tr("Save profile (Ctrl+S)"))
        save.clicked.connect(self.save_clicked)
        save.setEnabled(False)                                # (until something is changed)
        srow.addWidget(save)
        tl.addLayout(srow)
        self.settings = self.editor.body
        for spec in SETTINGS:                                 # (the controls' older names)
            if spec.attr:
                setattr(self, spec.attr, self.editor.controls[spec.key])
        self.aud = {k.split(".", 1)[1]: c for k, c in self.editor.controls.items() if k.startswith("audiophile.")}
        self.picard_btn = self.editor.picard_btn
        self.tabs.addTab(tab, tr("Settings"))
        # rules tab
        rt = QWidget()
        rl = QVBoxLayout(rt)
        hint = QLabel(tr("Rules here add to the global rules (shown greyed). Ignored items are left alone in every "
                         "run while the rule applies; skipped items only in this run."))
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        self.rules = QTableWidget(0, 5)
        self.rules.setHorizontalHeaderLabels([tr("On"), tr("Name"), tr("Action"), tr("Applies to"), tr("When")])
        self.rules.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rules.verticalHeader().hide()
        self.rules.itemChanged.connect(self.toggle_rule)
        self.rules.activated.connect(lambda *_: self.edit_rule())      # (Return, or a double click)
        left_header(self.rules)
        self.rules.setAccessibleName(tr("Ignore and skip rules"))
        self.rules.setShowGrid(False)
        self.rules.setAlternatingRowColors(True)
        self.rules.itemSelectionChanged.connect(self.win.update_album_actions)
        self.rules.itemSelectionChanged.connect(self.update_rule_buttons)
        self.rules.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rules.customContextMenuRequested.connect(self.rules_menu)
        rl.addWidget(self.rules, 1)
        row = FlowLayout()                                    # (the menu's words: one name per command)
        self.rule_btns = {}
        for key, text, fn in (("new", tr("New Rule…"), lambda: self.add_rule()),
                              ("edit", tr("Edit Rule…"), self.edit_rule),
                              ("remove", tr("Remove Rule…"), self.remove_rule),
                              ("test", tr("Test on a Folder…"), self.test_rules)):
            b = QPushButton(text)
            b.setAccessibleName({"test": tr("Test the rules on a folder")}.get(key, text.rstrip("…")))
            b.clicked.connect(fn)
            row.addWidget(b)
            self.rule_btns[key] = b
        row.addStretch(1)
        rl.addLayout(row)
        self.test_out = QPlainTextEdit()
        self.test_out.setReadOnly(True)
        self.test_out.setAccessibleName(tr("Rule test results"))
        rl.addWidget(self.test_out, 1)
        self.tabs.addTab(rt, tr("Ignore and skip rules"))
        self.reload()

    LIST_WIDE, LIST_NARROW = 200, 160                     # the profile list's widest, on a wide and a narrow page
    NARROW = 800                                            # page widths under this: the narrow layout

    def resizeEvent(self, e):
        """The profile list gives the settings room first on a narrow window."""
        super().resizeEvent(e)
        cap = self.LIST_NARROW if self.width() < self.NARROW else self.LIST_WIDE
        if self.list.maximumWidth() != cap:
            self.list.setMaximumWidth(cap)
            self.find.setMaximumWidth(cap)

    def filter(self, text):
        """The toolbar search: the profiles whose name holds the text (all of them when no name
        does), and in the profile shown only the settings whose words hold it (their sections
        and Advanced open)."""
        text = (text or "").casefold()
        names = [i for i in range(self.list.count()) if text in self.list.item(i).text().casefold()]
        for i in range(self.list.count()):
            self.list.item(i).setHidden(bool(text) and bool(names) and i not in names)
        self.editor.search(text)

    def rules_menu(self, pos):
        popup_menu(self, self.rules, pos, [self.win.act(k) for k in ("new_rule", "edit_rule", "toggle_rule",
                                                                     "delete_rule")])

    def scope(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else "global"

    def reload(self):
        if not self.may_leave():
            return
        cur = self.scope() if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        g = QListWidgetItem(tr("Global rules"))                  # (not a profile: set apart in italics)
        g.setData(Qt.UserRole, "global")
        g.setToolTip(tr("The Ignore and Skip rules every profile uses"))
        f = g.font()
        f.setItalic(True)
        g.setFont(f)
        self.list.addItem(g)
        for n in pf.names():
            it = QListWidgetItem(n)
            it.setData(Qt.UserRole, n)
            self.list.addItem(it)
        self.list.blockSignals(False)
        keys = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        row = keys.index(cur) if cur in keys else 0
        self.shown_row = -1                                   # (read again from disk)
        self.list.setCurrentRow(row)
        if self.shown_row != row:                             # (the same row: no signal)
            self.row_changed(row)


    # ---- unsaved changes ---------------------------------------------------
    def values(self):
        """The form as it is now (to tell whether something was changed)."""
        return self.editor.snapshot()

    def dirty(self) -> bool:
        return self.shown_name is not None and self.saved_values is not None and self.values() != self.saved_values

    def changed(self, *_):
        d = self.dirty()
        n = getattr(self.editor, "problem_count", 0) if self.shown_name else 0
        self.save_btn.setEnabled(d or bool(n))           # (with problems: it shows the first one)
        self.save_btn.setToolTip(tr("Save profile (Ctrl+S)") if not n else
                                 plural(n, tr("problem"), tr("problems")) + tr(": see the red lines"))
        note = (plural(n, tr("problem"), tr("problems")) if n else "") if d or n else ""
        if d:
            note = (note + " · " if note else "") + tr("Unsaved changes")
        self.dirty_note.setText(note)
        if self.win is not None:
            self.win.setWindowModified(d)                     # (the title shows "TagMender*")


    def reload_shown(self):
        """The profile shown, as its file says (what was changed and not saved is gone)."""
        name = self.shown_name
        if name:
            try:
                raw = pf.raw(name)
                self.editor.load(pf.rename_legacy(raw), pf.from_dict(raw, check=False).data,
                                 pf.from_dict({"name": name}, check=False).data)
            except pf.ProfileError:
                pass
        self.saved_values = self.values()
        self.changed()

    def may_leave(self) -> bool:
        """Before another profile is shown or the window closes: save, discard or stay. Offscreen
        (tests) the changes are discarded, as before."""
        if not self.dirty():
            return True
        ans = sheets.ask(self, tr("Save changes to “{shown_name}”?").format(shown_name=self.shown_name),
                         tr("Your changes to this profile are lost if you don't save them."),
                         buttons=(tr("Cancel"), tr("Don't Save"), tr("Save")), default=tr("Save"),
                         offscreen=tr("Don't Save"))
        if ans == tr("Save"):
            return self.save()
        if ans == tr("Don't Save"):                       # (the form back to the file: the page lives on
            self.reload_shown()                           #  in Preferences after it closes)
            return True
        return False

    def row_changed(self, row):
        if row < 0:
            return
        if row != self.shown_row and self.shown_row >= 0 and not self.may_leave():
            self.list.blockSignals(True)                       # (Cancel: the list goes back)
            self.list.setCurrentRow(self.shown_row)
            self.list.blockSignals(False)
            return
        self.shown_row = row
        self.show(self.list.item(row).data(Qt.UserRole))

    def show(self, name):
        if not name:
            return
        settings_on = name != "global"
        self.shown_name = name if settings_on else None
        self.tabs.setTabEnabled(0, settings_on)
        for b in (self.dup_btn, self.rename_btn, self.delete_btn):
            b.setEnabled(settings_on)
        if settings_on and getattr(self, "_rules_forced", False):
            self.tabs.setCurrentIndex(0)                      # (back from the global rules)
        self._rules_forced = not settings_on
        if settings_on:
            try:                                              # (a profile that still has problems,
                raw = pf.rename_legacy(pf.raw(name))          #  such as an example without its library
                full = pf.from_dict(pf.raw(name), check=False).data   # folder, opens with them in red)
            except pf.ProfileError as exc:
                sheets.warning(self, tr("The profile “{name}” cannot be read.").format(name=name), str(exc))
                return
            self.editor.load(raw, full, pf.from_dict({"name": name}, check=False).data)
            self.saved_values = self.values()
        else:
            self.tabs.setCurrentIndex(1)
            self.saved_values = None
        self.changed()
        self.load_rules()

    def load_rules(self):
        scope = self.scope()
        rules = ex.load_rules(None if scope == "global" else scope, include_disabled=True)
        self.rules.blockSignals(True)
        self.rules.setRowCount(len(rules))
        import tagmender
        for i, r in enumerate(rules):
            other = r.scope != scope                          # a global rule, shown greyed in a profile
            tip = tr("A global rule: every profile uses it. Change it under Global rules.") if other else ""
            on = QTableWidgetItem()
            # (read-only here: ticking it would turn the rule off for every profile, out of sight)
            on.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.NoItemFlags if other else Qt.ItemIsUserCheckable))
            on.setCheckState(Qt.Checked if r.enabled else Qt.Unchecked)
            on.setData(Qt.UserRole, (r.id, r.scope))
            on.setToolTip(tip)
            self.rules.setItem(i, 0, on)
            for j, v in enumerate((r.name, tr("Ignore") if r.action == "ignore" else tr("Skip"),
                                   tr("Album") if r.target == "album" else tr("File"), tagmender.describe(r.when)), 1):
                it = QTableWidgetItem(v)
                if other:
                    it.setForeground(self.palette().color(QPalette.PlaceholderText))
                    it.setToolTip(tip)
                self.rules.setItem(i, j, it)
        self.rules.blockSignals(False)
        self.update_rule_buttons()
        if self.win is not None:                              # (the rows changed under the selection)
            self.win.update_album_actions()

    def own_rule(self) -> bool:
        """The selected rule belongs to the list shown (a greyed global rule is changed under
        Global rules, not from a profile)."""
        r = self.rules.currentRow()
        it = self.rules.item(r, 0) if r >= 0 else None
        return it is not None and it.data(Qt.UserRole)[1] == self.scope()

    def update_rule_buttons(self):
        if not hasattr(self, "rule_btns"):
            return
        sel = self.rules.currentRow() >= 0 and self.rules.selectionModel().hasSelection() and self.own_rule()
        for k in ("edit", "remove"):
            self.rule_btns[k].setEnabled(sel)

    def toggle_rule(self, item):
        """The On box clicked: the rule turned on or off (a global rule after a sheet)."""
        if item.column() != 0:
            return
        rid, scope = item.data(Qt.UserRole)
        if scope != self.scope():                             # (never from a profile's list: see load_rules)
            return
        on = item.checkState() == Qt.Checked
        if not self.set_rule(rid, scope, on):
            self.rules.blockSignals(True)                     # (Cancel: the box goes back)
            item.setCheckState(Qt.Unchecked if on else Qt.Checked)
            self.rules.blockSignals(False)
        if self.win is not None:
            self.win.update_album_actions()

    def set_rule(self, rid, scope, on) -> bool:
        """Turn a rule on or off; a global one changes every profile, so a sheet asks first."""
        if scope == "global":
            r = next((x for x in ex.load_rules(None, include_disabled=True) if x.id == rid and x.scope == scope), None)
            name = r.name if r else rid
            if not sheets.confirm(self, tr("Turn the global rule “{name}” {0}?").format('on' if on else 'off',
                                                                                        name=name),
                                  (tr("Every profile uses “{name}” again: what it matches is left alone by the "
                                      "next run.").format(name=name) if on else
                                   tr("Every profile stops using “{name}”: what it excluded is processed "
                                      "again by the next run that finds it.").format(name=name)),
                                  tr("Turn it {0} again here (Tools > Turn Rule On).").format('off' if on else 'on'),
                                  tr("Turn Rule On") if on else tr("Turn Rule Off")):
                return False
        ex.set_enabled(rid, on, scope)
        return True

    def rule_on(self) -> bool:
        r = self.rules.currentRow()
        it = self.rules.item(r, 0) if r >= 0 else None
        return it is not None and it.checkState() == Qt.Checked

    def toggle_selected_rule(self):
        """Tools > Turn Rule On (Ctrl+Alt+E): the selected rule, from the keyboard or the menu."""
        r = self.rules.currentRow()
        it = self.rules.item(r, 0) if r >= 0 else None
        if it is None or not self.own_rule():
            return
        rid, scope = it.data(Qt.UserRole)
        if self.set_rule(rid, scope, it.checkState() != Qt.Checked):
            self.load_rules()
            self.rules.selectRow(r)
        if self.win is not None:
            self.win.update_album_actions()

    def current_rule(self):
        r = self.rules.currentRow()
        if r < 0:
            return None
        rid, scope = self.rules.item(r, 0).data(Qt.UserRole)
        return next((x for x in ex.load_rules(None if scope == "global" else scope, include_disabled=True)
                     if x.id == rid and x.scope == scope), None)

    def add_rule(self, rule=None):
        dlg = RuleDialog(self, rule)
        while sheets.run(dlg, "rule"):
            try:
                r = dlg.rule()
                ex.save_rule(r, rule.scope if rule else self.scope())
                break
            except ex.RuleError as exc:
                sheets.warning(self, tr("The rule was not saved."), str(exc))
        self.load_rules()

    def edit_rule(self):
        r = self.current_rule()
        if r and self.own_rule():
            self.add_rule(r)

    def remove_rule(self):
        r = self.current_rule()
        if not r or not self.own_rule():
            return
        glob = r.scope == "global"
        if sheets.confirm(self, tr("Remove the global rule “{name}”?")
                          .format(name=r.name) if glob else tr("Remove the rule “{name}”?").format(name=r.name),
                          (tr("Every profile stops using it: what") if glob else tr("What")) +
                          tr(" it excluded is processed again by the next run that finds it."),
                          tr("To have it back, add the rule again (New Rule…)."), tr("Remove Rule"),
                          destructive=True):
            ex.remove_rule(r.id, r.scope)
            self.load_rules()

    def test_rules(self):
        d = sheets.choose_folder(self, tr("Choose a folder to test the rules on"))
        if not d:
            return
        scope = self.scope()

        def work(_w):
            import tagmender
            rules = ex.load_rules(None if scope == "global" else scope)
            ov = ex.State().overrides()
            lines, n_files, n_ex = [], 0, 0
            for folder, files in tagmender.album_dirs(d):
                dec = ex.evaluate(files, rules, overrides=ov)
                n_files += len(files)
                n_ex += len(dec.excluded)
                whole = dec.summary()
                if whole:
                    lines.append(tr("⊘ {label:<8} {folder}   (rule “{name}”, matched {where})")
                                 .format(label=whole.label, folder=folder, name=whole.rule.name, where=whole.where))
                elif dec.excluded:
                    lines.append(f"◐ partly   {folder}")
                    lines += [tr("     ⊘ {label:<8} {name}  (rule “{0}”)").format(e.rule.name, label=e.label,
                                                                                  name=p.name) for p,
                              e in dec.excluded.items()]
            return "\n".join(lines + [tr("\n{n_ex} of {n_files} files would be excluded. Nothing was changed.")
                                      .format(n_ex=n_ex, n_files=n_files)])
        self.test_out.setPlainText(tr("Testing…"))
        start(self, work, self.test_out.setPlainText)

    # ---- new, duplicate, the one-time built-in sheet ---------------------------------
    def new(self):
        """New Profile…: a name and what it starts from (Typical Library, Collector, Keep My
        Files as They Are)."""
        if not self.room_for_one():
            return
        dlg = PresetSheet(self, tr("New profile"), with_name=True, action=tr("Create Profile"))
        dlg.name.setText(pf.next_name())
        dlg.name.selectAll()
        if sheets.run(dlg, "new_profile") and dlg.name.text().strip():
            self.create(dlg.name.text().strip(), dlg.preset())

    def create(self, name, preset="typical", base=None) -> bool:
        """Save a new profile NAME from a preset, or as a copy of the profile `base`."""
        if not self.room_for_one():
            return False
        problem = pf.name_problem(name)
        if problem:
            sheets.warning(self, tr("The profile was not created."), problem[0].upper() + problem[1:] + ".")
            return False
        try:
            if base:
                data = pf.rename_legacy(pf.raw(base))
                data["name"] = name
                data.pop("sources", None)                     # (a folder belongs to one profile)
            else:
                data = preset_data(name, preset)
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile was not created."), str(exc))
            return False
        self.reload()
        rows = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if name in rows:
            self.list.setCurrentRow(rows.index(name))
        if self.win is not None and hasattr(self.win, "pages"):
            self.win.pages["Overview"].refresh_profiles()
        return True

    def room_for_one(self) -> bool:
        """False, having said so, when this computer already has MAX_PROFILES profiles."""
        if pf.next_name():
            return True
        sheets.warning(self, tr("No more profiles can be made."),
                       tr("{n} profiles is the most one computer keeps. Delete one you no longer use, "
                          "then try again.").format(n=pf.MAX_PROFILES))
        return False

    def duplicate(self):
        base = self.shown_name
        if not base or not self.room_for_one():
            return
        name, ok = sheets.get_text(self, tr("Duplicate “{name}”").format(name=base),
                                   tr("Every setting is copied except its folders: a folder belongs to one "
                                      "profile."), text=pf.next_name(),
                                   action=tr("Duplicate"), placeholder=tr("Profile name"))
        if ok and name.strip():
            self.create(name.strip(), base=base)

    def rename(self):
        old = self.shown_name
        if not old or not self.may_leave():
            return
        name, ok = sheets.get_text(self, tr("Rename “{name}”").format(name=old),
                                   tr("Its settings, folders and rules stay the same."), text=old,
                                   action=tr("Rename"), placeholder=tr("Profile name"))
        name = name.strip()
        if not ok or not name or name == old:
            return
        try:
            pf.rename(old, name)
        except (pf.ProfileError, OSError) as exc:
            sheets.warning(self, tr("The profile was not renamed."), str(exc))
            return
        self.shown_name = None
        self._after_change(name)

    def delete(self):
        name = self.shown_name
        if not name:
            return
        if not sheets.confirm(self, tr("Delete the profile “{name}”?").format(name=name),
                              tr("Its settings and rules are removed, and its folders are no longer looked after. "
                                 "Your music and the job history are not touched."),
                              tr("A copy is kept in the profiles folder, under .deleted, to bring it back."),
                              tr("Delete Profile"), destructive=True):
            return
        try:
            pf.delete(name)
        except (pf.ProfileError, OSError) as exc:
            sheets.warning(self, tr("The profile was not deleted."), str(exc))
            return
        self.saved_values = self.values()                     # (its unsaved changes went with it)
        self.shown_name = None
        self._after_change(None)

    def _after_change(self, select):
        """The list read again after a rename or delete, SELECT shown; Overview told."""
        self.reload()
        rows = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if select in rows:
            self.list.setCurrentRow(rows.index(select))
        if self.win is not None and hasattr(self.win, "pages"):
            self.win.pages["Overview"].refresh_profiles()

    def picard_script_text(self) -> str:
        import naming
        try:
            tpl = naming.PRESETS.get(self.naming.currentData(), "")
        except AttributeError:
            tpl = ""
        return naming.tagscript(self.editions.currentData(), self.album_editions.currentData(), tpl)

    def edit_script(self):
        """The profile's naming script in Script Editor, where it is edited with a live preview
        (the script for another tagger stays under Tools > Tagging Script for This Profile…)."""
        if self.win is None or not self.shown_name or not self.may_leave():
            return
        from . import script_editor
        script_editor.open_editor(self.win, self.shown_name, None)

    def show_picard_script(self):
        text = self.picard_script_text()
        if not text:
            return
        sheets.show_text(self, tr("Tagging script for editions"), text,
                         tr("For another tagger that runs TagMender scripts to name and tag as this profile does. "
                            "(Such a tagger works the edition out per track: a set mixing media, such as a CD with "
                            "a 7\" bonus disc, gets two folders there; TagMender keeps it in one.)"), copy=True,
                         preview=self.editor.examples())

    def save_clicked(self):
        """Save Profile with problems shown: the first one is scrolled to instead."""
        if getattr(self.editor, "problem_count", 0):
            self.editor.reveal_first_problem()
            return
        self.save()

    def save(self) -> bool:
        """The profile the form shows (True when saved). Only what was changed is written."""
        name = self.shown_name
        if not name:
            return False
        # (in today's form, the shared values filled in: a change of one switch of a shared
        # setting keeps the rest of it)
        data = self.editor.write(pf.with_shared(pf.upgrade(pf.raw(name))))
        try:
            checked = pf.from_dict(data)                     # (checked before anything is written)
            problems = pf.check_template(checked.data)
            if problems:
                raise pf.ProfileError("; ".join(problems))
            # (from the edited data: the shared values were filled in before the edit, so a
            # setting set back to Automatic - not stated - is no longer there)
            shared = self.editor.shared_changes(data)
            if shared:                                       # (for every profile not keeping its own)
                broken = pf.shared_problems(shared, but=name)
                if broken:                                   # (nothing written: another would stop working)
                    raise pf.ProfileError(
                        tr("This change applies to every profile, and another would stop working with "
                           "it. Choose Only This Profile for it, or change that profile first.")
                        + "\n\n" + "\n".join(broken))
                pf.adopt_shared()
                pf.set_shared(shared)
            pf.save(pf.from_dict(data))
            if self.win is not None:
                self.win.statusBar().showMessage(tr("Saved profile {name}").format(name=name), 4000)
                self._tell_overview()
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile “{name}” was not saved.").format(name=name), str(exc))
            return False
        self.editor.load(pf.rename_legacy(pf.raw(name)), pf.load(name).data,
                         pf.from_dict({"name": name}, check=False).data)
        self.saved_values = self.values()
        self.changed()
        return True


    def apply_to_all(self, key) -> bool:
        """Apply to All Profiles…: this setting, as the form shows it, into every profile. Only
        that setting changes in them; a profile keeping its own value of a shared setting follows
        the shared one again. This profile's unsaved changes are saved first; nothing is written
        when any profile could not take it (True when applied)."""
        from .profile_editor import BY_KEY, SHARED_ROWS, ProfileEditor, words_of
        name = self.shown_name
        if not name:
            return False
        spec, value = BY_KEY[key], self.editor.value(key)
        label, words = tr(spec.label), words_of(spec, value)
        others = [n for n in pf.names() if n != name]
        if not others:
            sheets.information(self, tr("There are no other profiles."),
                               tr("“{setting}” is set in this profile only because it is the only one.")
                               .format(setting=label))
            return False
        body = tr("{setting}: {value}, in {n} other profiles ({names}). Only this setting changes in them; "
                  "a profile that kept its own value gets this one.").format(
            setting=label, value=words, n=len(others), names=", ".join(others))
        if self.dirty():
            body += "\n\n" + tr("Your other changes to “{name}” are saved first.").format(name=name)
        if not sheets.confirm(self, tr("Use “{setting}: {value}” in all profiles?").format(setting=label, value=words),
                              body, tr("Each profile's settings can be changed back here."),
                              tr("Apply to All Profiles")):
            return False
        if self.dirty() and not self.save():
            return False
        work = ProfileEditor(self)                            # (each profile as the form would save it)
        work.hide()
        datas, shared, broken = {}, {}, []
        try:
            for n in [name] + others:
                try:
                    raw = pf.raw(n)
                    work.load(pf.rename_legacy(raw), pf.from_dict(raw, check=False).data,
                              pf.from_dict({"name": n}, check=False).data)
                    if key in SHARED_ROWS and work.is_own(key):
                        work.set_scope(key, False)
                    work.set_value(key, value)
                    data = work.write(pf.with_shared(pf.upgrade(pf.raw(n))))
                    problems = pf.check_template(pf.from_dict(data).data)
                    if problems:
                        raise pf.ProfileError("; ".join(problems))
                    shared.update(work.shared_changes(data))
                    datas[n] = data
                except Exception as exc:                      # (one that cannot be read: named, nothing written)
                    broken.append(f"{n}: {exc}")
        finally:
            work.deleteLater()
        if broken:
            sheets.warning(self, tr("“{setting}” was not applied to any profile.").format(setting=label),
                           tr("These profiles could not take it:") + "\n\n" + "\n".join(broken))
            return False
        done, failed = [], None
        try:
            if shared:
                pf.adopt_shared()
                pf.set_shared(shared)
            for n, data in datas.items():
                pf.save(pf.from_dict(data))
                done.append(n)
        except (pf.ProfileError, OSError) as exc:              # (a disk full or read-only part-way)
            failed = exc
        self.reload_shown()
        if failed is not None:
            left = [n for n in datas if n not in done]
            said = str(failed) + "\n\n" + tr("Not changed: {names}").format(names=", ".join(left))
            if shared:                                         # (the shared value itself was changed)
                said += "\n" + tr("The value shared by all profiles was changed; those profiles follow it "
                                   "unless they keep their own.")
            sheets.warning(self, tr("“{setting}” was not applied to every profile.").format(setting=label), said)
            return False
        if self.win is not None:
            self.win.statusBar().showMessage(tr("“{setting}” applied to all profiles").format(setting=label), 4000)
        self._tell_overview()
        return True

    def _tell_overview(self):
        """A profile changed: the Overview says which albums were looked up with its old settings."""
        ov = (getattr(self.win, "pages", None) or {}).get("Overview")
        if ov is not None and hasattr(ov, "check_stale"):
            ov.check_stale()


def offer_previous_builtin(win) -> bool:
    """Once, on a computer that ran an earlier TagMender: the built-in settings (used when no
    profile is chosen) have new defaults, and the old ones can be kept as a profile. True when
    they were saved."""
    if not pf.builtin_changed_note():
        return False
    ans = sheets.ask(win, tr("The built-in settings have new defaults."),
                     tr("They are used when no profile is chosen: MusicBrainz first, \"Artist/1991 - Album/01 - "
                        "Title\" names, no artist photos, your own covers kept, and folders whose names "
                        "start with _ are no longer skipped (only recycle bins and TagMender's own folders "
                        "are).\n\nSave the old ones as the "
                        "profile “{name}” to keep working as before. You can also do it later from the command "
                        "line: tagmender profiles save-previous-builtin").format(name=pf.PREVIOUS_BUILTIN_NAME),
                     buttons=(tr("Use the New Defaults"), tr("Save the Old Settings")),
                     default=tr("Save the Old Settings"), offscreen=tr("Use the New Defaults"))
    if ans != tr("Save the Old Settings"):
        return False
    try:
        pf.save_previous_builtin()
    except (pf.ProfileError, OSError) as exc:
        sheets.warning(win, tr("The old settings were not saved."), str(exc))
        return False
    page = (getattr(win, "pages", None) or {}).get("Profiles")
    if page is not None:
        page.reload()
        win.pages["Overview"].refresh_profiles()
    if hasattr(win, "statusBar"):
        win.statusBar().showMessage(tr("Saved the old built-in settings as “{name}”")
                                    .format(name=pf.PREVIOUS_BUILTIN_NAME), 6000)
    return True
