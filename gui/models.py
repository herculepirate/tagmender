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
    COLS = (N_("Status"), N_("Album folder"), N_("Source"), N_("Score"), N_("Changes"), N_("Why"))
    LEAVE_OUT = "_leave_out"           # (an album's own mark: unticked, so Apply leaves it as it is)
    ticked = Signal()

    def __init__(self):
        super().__init__()
        self.albums = []

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.column() == 1 and can_apply(self.albums[idx.row()]):
            f |= Qt.ItemIsUserCheckable                  # (ticked: applied; unticked: left out)
        return f

    def setData(self, idx, value, role=Qt.EditRole):
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
        if role == Qt.TextAlignmentRole and c == 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.AccessibleTextRole and c == 0:
            return label_for(a)[1]
        if role == Qt.CheckStateRole and c == 1 and can_apply(a):
            return Qt.Unchecked if a.get(self.LEAVE_OUT) else Qt.Checked
        if role == Qt.UserRole:
            return a
        return None
