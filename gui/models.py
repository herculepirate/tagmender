"""gui/models.py - The plan as a table."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from .common import album_words, label_for, plural, reasons
from .i18n import N_, tr
from .style import status_color, status_icon

# what a tier word in the status column means (its tooltip; the evidence is in the inspector)
TIER_TIPS = {"partial": N_("Partial: the release is known, but which pressing is not proven"),
             "album_only": N_("Album only: the album matched, but not every track did"),
             "recording": N_("Recording only: the recordings matched, but not one release")}


def changes_text(files) -> str:
    """'11 files', '8 files, all moved', '8 files, 3 copied' (nothing for an album left as it is)."""
    n = sum(1 for fe in files if fe["op"] != "none")
    if not n:
        return ""
    out = plural(n, tr("file"), tr("files"))
    for op, done in (("move", "moved"), ("copy", "copied")):
        k = sum(1 for fe in files if fe["op"] == op)
        if k:
            out += f", all {done}" if k == n else f", {k} {done}"
    return out


# --------------------------------------------------------------------------
# the plan as a table
# --------------------------------------------------------------------------
def can_apply(a) -> bool:
    """The album has changes Apply would make (ready, or Unresolved with its notes)."""
    import plan as planmod
    return a["status"] in ("ready", "unresolved") and planmod.has_work(a)


class PlanModel(QAbstractTableModel):
    COLS = (N_("Status"), N_("Album folder"), N_("Source"), N_("Score"), N_("Changes"), N_("Why"), N_("Profile"))
    PROFILE = 6                        # (the album's profile: its own, or the one chosen above)
    LEAVE_OUT = "_leave_out"           # (an album's own mark: unticked, so Apply leaves it as it is)
    ticked = Signal()

    def __init__(self):
        super().__init__()
        self.albums = []
        # set by the page that shows a Profile column: album -> (profile name, chosen for it),
        # and (albums, name) for a profile chosen in a row ("": the one chosen above)
        self.profile_of = None
        self.choose_profile = None
        self.locked = False             # (busy: no profile is chosen while albums are looked up)

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.column() == 1 and can_apply(self.albums[idx.row()]):
            f |= Qt.ItemIsUserCheckable                  # (ticked: applied; unticked: left out)
        if idx.isValid() and idx.column() == self.PROFILE and self.choose_profile and not self.locked and \
                self.albums[idx.row()].get("status") != "done":
            f |= Qt.ItemIsEditable
        return f

    def refresh_rows(self):
        if self.albums:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.albums) - 1, len(self.COLS) - 1))

    def setData(self, idx, value, role=Qt.EditRole):
        if role == Qt.EditRole and idx.column() == self.PROFILE and self.choose_profile:
            self.choose_profile([self.albums[idx.row()]], str(value or ""))
            return True
        if role != Qt.CheckStateRole or idx.column() != 1:
            return False
        self.set_left_out([self.albums[idx.row()]], Qt.CheckState(value) != Qt.Checked)
        return True

    def set_left_out(self, albums, out):
        for a in albums:
            if can_apply(a):
                if out:
                    a[self.LEAVE_OUT] = True
                else:
                    a.pop(self.LEAVE_OUT, None)
        self.dataChanged.emit(self.index(0, 0), self.index(max(0, len(self.albums) - 1), len(self.COLS) - 1))
        self.ticked.emit()

    def set_plan(self, plan):
        self.beginResetModel()
        self.albums = plan["albums"] if plan else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.albums)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(self.COLS[s])
        if o == Qt.Horizontal and role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if s == 3 else Qt.AlignLeft) | Qt.AlignVCenter)
        return None

    def data(self, idx, role=Qt.DisplayRole):
        a = self.albums[idx.row()]
        c = idx.column()
        ident = a["identification"]
        if role == Qt.DisplayRole:
            if c == 0:
                return label_for(a)[1]                        # (the icon is the DecorationRole)
            if c == 1:
                return album_words(a["source_dir"])
            if c == 2:
                return (ident.get("provider") or "").replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")
            if c == 3:
                return f"{ident['confidence']:.0f}" if ident.get("confidence") else ""
            if c == 4:
                return changes_text(a["files"])
            if c == self.PROFILE:
                return self.profile_of(a)[0] if self.profile_of else ""
            if c == 5:
                if a["status"] in ("excluded",):
                    return tr("rule “{0}”").format(a['rule']['name'])
                if a["conflicts"]:
                    return a["conflicts"][0]
                if ident.get("status") == "unresolved":         # (the codes are in Evidence > Codes)
                    codes, words = reasons.parse(ident.get("reason", ""))
                    text = "; ".join(reasons.describe(c) for c in codes if c != "other") or words
                    return text[:1].upper() + text[1:]
                return "; ".join(ident.get("why") or [])[:120]
        if role == Qt.ForegroundRole and c == 0:
            return status_color(a["status"])
        if role == Qt.DecorationRole and c == 0:
            from . import icons
            return icons.icon(status_icon(a["status"]), a["status"], 14)
        if role == Qt.ToolTipRole and c == 0:
            tier = (ident or {}).get("tier")
            if a["status"] not in ("unresolved", "excluded", "skipped") and tier in TIER_TIPS:
                return tr(TIER_TIPS[tier])
            return label_for(a)[1]                            # (the whole word when the column cuts it)
        if role == Qt.ToolTipRole and c == 1:
            return a["source_dir"]
        if role == Qt.ToolTipRole and c == self.PROFILE and self.profile_of:
            name, own = self.profile_of(a)
            return (tr("Profile {0}, chosen for this album").format(name) if own else
                    tr("Profile {0}, the one chosen above").format(name)) + \
                (tr(". Click to choose another for it.") if self.flags(idx) & Qt.ItemIsEditable else "")
        if role == Qt.EditRole and c == self.PROFILE and self.profile_of:
            return self.profile_of(a)[0] if self.profile_of(a)[1] else ""
        if role == Qt.FontRole and c == self.PROFILE and self.profile_of and not self.profile_of(a)[1]:
            from PySide6.QtGui import QFont
            f = QFont()
            f.setItalic(True)                             # (the one chosen above: it follows that choice)
            return f
        if role == Qt.TextAlignmentRole and c == 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.AccessibleTextRole and c == 0:
            return label_for(a)[1]
        if role == Qt.CheckStateRole and c == 1 and can_apply(a):
            return Qt.Unchecked if a.get(self.LEAVE_OUT) else Qt.Checked
        if role == Qt.UserRole:
            return a
        return None
