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


# what a scan read from the sleeve, in a word (the kinds the plan names)
SCAN_KINDS = {"barcode": N_("Barcode"), "catalogue_number": N_("Cat. no."), "label": N_("Label"),
              "country": N_("Country"), "matrix": N_("Matrix"), "format": N_("Format")}
SCAN_MARK = {"proven": "✓", "agrees": "~", "differs": "✗"}
SCAN_TIPS = {"proven": N_("Your scans show the barcode and catalogue number of this release: the pressing is proven"),
             "agrees": N_("Your scans agree with this release, but do not prove which pressing it is"),
             "differs": N_("Your scans name another pressing: decide in Needs attention"),
             "unread": N_("Scans were read but no barcode or catalogue number was found")}
# how a source was reached, after the database's name
VIA_WORDS = {"disc_id": N_("disc ID"), "link": N_("your files' link"), "scan_search": N_("web search of your scans"),
             "barcode_lookup": N_("barcode lookup"), "fingerprint": N_("fingerprint")}


def _kinds(kinds) -> str:
    return ", ".join(tr(SCAN_KINDS[k]) if k in SCAN_KINDS else str(k) for k in kinds or [])


def scans_text(sc) -> str:
    """The short text of an album's scans result ("✓ Barcode, Cat. no."); "" when there is none."""
    st = (sc or {}).get("state")
    if st in ("proven", "agrees"):
        return f"{SCAN_MARK[st]} {_kinds(sc.get('found'))}".rstrip()
    if st == "differs":
        return tr("{0} {1} differs").format(SCAN_MARK[st], _kinds(sc.get("against"))).replace("  ", " ")
    if st == "unread":
        return tr("— Nothing read")
    return ""


def scans_tip(sc) -> str:
    st = (sc or {}).get("state")
    return tr(SCAN_TIPS[st]) if st in SCAN_TIPS else ""


def source_text(ident) -> str:
    """The Source column: the database(s), and how it was reached ("MusicBrainz · disc ID")."""
    prov = (ident.get("provider") or "")
    if "web" in prov:
        text = tr("Web")
    else:
        text = prov.replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")
    via = ident.get("via") or ""
    if via in VIA_WORDS:
        text = f"{text} · {tr(VIA_WORDS[via])}" if text else tr(VIA_WORDS[via])
    return text


def changes_text(files) -> str:
    """What Apply will do with the album's tracks, before it is done: "10 tracks, will move",
    "8 tracks, 3 will be copied", "15 tracks, stay in their folder" (nothing for an album left
    as it is)."""
    n = sum(1 for fe in files if fe["op"] != "none")
    if not n:
        return ""
    head = plural(n, tr("track"), tr("tracks"))
    moved = sum(1 for fe in files if fe["op"] == "move")
    copied = sum(1 for fe in files if fe["op"] == "copy")
    parts = []
    if moved:
        parts.append(tr("will move") if moved == n else tr("{0} will move").format(moved))
    if copied:
        parts.append(tr("will be copied") if copied == n else tr("{0} will be copied").format(copied))
    if not parts:
        parts.append(tr("stay in their folder") if n > 1 else tr("stays in its folder"))
    return tr("{0}, {1}").format(head, ", ".join(parts))


# --------------------------------------------------------------------------
# the plan as a table
# --------------------------------------------------------------------------
# the Score cell's colour: how far to trust the match, from the score AND what proved it (the
# score alone is how well it fits, not a probability: a 90 that the scans contradict is red)
SCORE_BANDS = {
    "excellent": ((48, 209, 88), N_("Excellent: the release fits and its pressing is proven (disc ID, your "
                                     "files' own link, or the scans), or it fits almost perfectly.")),
    "good": ((10, 132, 255), N_("Good: the release fits well; nothing proves the pressing, nothing argues against it.")),
    "check": ((255, 159, 10), N_("Worth a look: the album is known but not its exact pressing, or the fit is weaker.")),
    "investigate": ((255, 59, 48), N_("Needs investigation: nothing identified it for sure, or the evidence "
                                       "disagrees (the scans name another pressing, or a conflict).")),
}


def score_band(a):
    """"excellent", "good", "check" or "investigate" for album a, or None while it has no
    match to judge (not looked up yet, left alone by a rule)."""
    ident = a.get("identification") or {}
    status, tier = a.get("status"), ident.get("tier")
    conf = float(ident.get("confidence") or 0)
    scans = (ident.get("scans") or {}).get("state")
    if status in ("listed", "excluded", "skipped") or (not conf and status not in ("unresolved", "failed")):
        return None
    if status in ("unresolved", "failed", "conflict") or a.get("conflicts") or scans == "differs" or \
            tier in ("unresolved",) or conf < 70:
        return "investigate"
    if tier in ("album_only", "partial", "recording") or conf < 85:
        return "check"
    if tier == "exact" and (scans == "proven" or ident.get("via") in ("disc_id", "link") or conf >= 95):
        return "excellent"
    return "good"


def can_apply(a) -> bool:
    """The album has changes Apply would make (ready, or Unresolved with its notes)."""
    import plan as planmod
    return a["status"] in ("ready", "unresolved") and planmod.has_work(a)


class PlanModel(QAbstractTableModel):
    COLS = (N_("Status"), N_("Album folder"), N_("Source"), N_("Score"), N_("Changes"), N_("Why"), N_("Profile"),
            N_("Scans"))
    SCANS = 7
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
        # set by the page: album -> what is being done with it now ("Waiting…", "Looking up…",
        # "Applying…"; "": nothing), shown in its Status column until it is done (never in the plan)
        self.state_of = None

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

    def replace_rows(self, rows, new) -> int:
        """The albums of ROWS (row numbers) replaced by NEW, put where the first of them was
        (none: at the end); rows not removed keep their places, selection and sorting. The
        row the first of NEW is in."""
        rows = sorted(set(r for r in rows if 0 <= r < len(self.albums)))
        new = list(new)
        if rows and len(rows) == len(new) and rows == list(range(rows[0], rows[0] + len(rows))):
            self.albums[rows[0]:rows[0] + len(rows)] = new        # (as many: in place)
            self.dataChanged.emit(self.index(rows[0], 0), self.index(rows[-1], len(self.COLS) - 1))
            return rows[0]
        at = rows[0] if rows else len(self.albums)
        for r in reversed(rows):
            self.beginRemoveRows(QModelIndex(), r, r)
            del self.albums[r]
            self.endRemoveRows()
        if new:
            self.beginInsertRows(QModelIndex(), at, at + len(new) - 1)
            self.albums[at:at] = new
            self.endInsertRows()
        return at

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
        now = self.state_of(a) if self.state_of and c == 0 and role in (
            Qt.DisplayRole, Qt.ToolTipRole, Qt.AccessibleTextRole) else ""
        if now:
            return now
        if role == Qt.DisplayRole:
            if c == 0:
                return label_for(a)[1]                        # (the icon is the DecorationRole)
            if c == 1:
                return album_words(a["source_dir"])
            if c == 2:
                return source_text(ident)
            if c == self.SCANS:
                return scans_text(ident.get("scans"))
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
        if role == Qt.ForegroundRole and c == 0 and a["status"] in ("blocked", "failed", "conflict"):
            return status_color(a["status"])               # (only what stops Apply in colour; the icon says the rest)
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
        if role == Qt.ToolTipRole and c in (2, 4, 5):
            # (the whole text, wrapped, when the column cuts it: Why in full, every conflict)
            if c == 5 and a["status"] not in ("excluded",) and not a["conflicts"] and \
                    ident.get("status") != "unresolved":
                text = "; ".join(ident.get("why") or [])
            elif c == 5 and len(a["conflicts"]) > 1:
                text = "\n".join(a["conflicts"])
            else:
                text = self.data(idx, Qt.DisplayRole) or ""
            if not text:
                return ""
            import html as _h
            return "<p style='white-space:pre-wrap'>" + _h.escape(text) + "</p>"
        if role == Qt.ToolTipRole and c == self.SCANS:
            return scans_tip(ident.get("scans"))
        if role == Qt.ForegroundRole and c == self.SCANS:
            st = (ident.get("scans") or {}).get("state")
            key = {"proven": "done", "differs": "unresolved"}.get(st)
            return status_color(key) if key else (status_color("excluded") if st else None)
        if role == Qt.AccessibleTextRole and c == self.SCANS:
            return scans_text(ident.get("scans"))
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
        if c == 3 and role in (Qt.BackgroundRole, Qt.ToolTipRole):
            band = score_band(a)
            if band is None:
                return None
            if role == Qt.ToolTipRole:
                return tr(SCORE_BANDS[band][1]) + " " + tr("The score is how well the release fits, not a "
                                                           "probability.")
            from PySide6.QtGui import QBrush, QColor
            from .style import is_dark
            if band in ("excellent", "good"):                # (a fit to trust: plain, so the doubtful stand out)
                return None
            r, g, b = SCORE_BANDS[band][0]
            return QBrush(QColor(r, g, b, 90 if is_dark() else 60))
        if role == Qt.AccessibleTextRole and c == 3 and score_band(a):
            # (the band in words too: its tint alone says nothing to a screen reader)
            return f"{self.data(idx, Qt.DisplayRole) or ''}, {tr(SCORE_BANDS[score_band(a)][1]).split(':')[0]}"
        if role == Qt.AccessibleTextRole and c == 0:
            return label_for(a)[1]
        if role == Qt.CheckStateRole and c == 1 and can_apply(a):
            return Qt.Unchecked if a.get(self.LEAVE_OUT) else Qt.Checked
        if role == Qt.UserRole:
            return a
        return None
