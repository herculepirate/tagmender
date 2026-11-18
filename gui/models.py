"""gui/models.py - The plan as a table."""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractItemModel, QAbstractProxyModel, QAbstractTableModel, QModelIndex, Qt, Signal

from .common import album_words, label_for, planmod, plural, reasons, suggested_pressing
from .i18n import N_, tr
from .style import status_color, status_icon

# the item roles as plain ints: a comparison with one of Qt's enum members costs microseconds,
# and a list of thousands of albums asks data() hundreds of thousands of times as it is drawn
# and fitted
R_DISPLAY = int(Qt.DisplayRole)
R_FOREGROUND = int(Qt.ForegroundRole)
R_DECORATION = int(Qt.DecorationRole)
R_TOOLTIP = int(Qt.ToolTipRole)
R_ACCESSIBLETEXT = int(Qt.AccessibleTextRole)
R_EDIT = int(Qt.EditRole)
R_FONT = int(Qt.FontRole)
R_TEXTALIGNMENT = int(Qt.TextAlignmentRole)
R_BACKGROUND = int(Qt.BackgroundRole)
R_CHECKSTATE = int(Qt.CheckStateRole)
R_USER = int(Qt.UserRole)


def fill_roles(data, idx, span):
    """QAbstractItemModel.multiData by DATA(idx, role) for each role asked: a view drawing a cell
    asks for ten roles or so in one call, so the cell costs one call into Python, not ten - and,
    with work on other threads, one wait for Python's lock, not ten."""
    for rd in span:
        v = data(idx, rd.role())
        if v is None:
            rd.clearData()
        else:
            rd.setData(v)


# what a tier word in the status column means (its tooltip; the evidence is in the inspector)
TIER_TIPS = {"partial": N_("Partial: the release is known, but which pressing is not proven"),
             "album_only": N_("Album only: the album matched, but not every track did"),
             "recording": N_("Recording only: the recordings matched, but not one release")}
# TIE-1: an album-only album with a pressing a tie-break proposed
SUGGESTED_TIP = N_("Album known: nothing of your copy tells its pressings apart; one is suggested "
                   "(Album › Use Suggested Pressing tags it in full)")


# what a scan read from the sleeve, in a word (the kinds the plan names)
SCAN_KINDS = {"barcode": N_("Barcode"), "catalogue_number": N_("Cat. no."), "label": N_("Label"),
              "country": N_("Country"), "matrix": N_("Matrix"), "format": N_("Format")}
SCAN_MARK = {"proven": "✓", "agrees": "~", "differs": "✗"}
SCAN_TIPS = {"proven": N_("Your scans show the barcode and catalogue number of this release: the pressing is proven"),
             "agrees": N_("Your scans agree with this release, but do not prove which pressing it is"),
             "differs": N_("Your scans name another pressing: choose the release with Album › Change Release"),
             "unread": N_("Scans were read but no barcode or catalogue number was found")}
# how a source was reached, after the database's name
VIA_WORDS = {"disc_id": N_("disc ID"), "link": N_("your files' link"), "scan_search": N_("web search of your scans"),
             "barcode_lookup": N_("barcode lookup"), "fingerprint": N_("fingerprint"),
             "mb_link": N_("MusicBrainz's link")}


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


# an album applied (its status "done"): its row's words, by where its files went
APPLIED = N_("Applied")
APPLIED_MOVED = N_("Applied · moved")
APPLYING = N_("Applying…")                 # (the page's row state while its files are written: overview.ROW_STATES)


def applied_moved(a) -> bool:
    """The album's files were moved or copied to another folder by Apply (a file's dst in a
    folder other than its src's); False when it was tagged (or renamed) in place."""
    for fe in (a or {}).get("files") or []:
        if fe.get("op") in ("move", "copy") and fe.get("dst") and fe.get("src") and \
                os.path.dirname(os.path.normpath(fe["dst"])) != os.path.dirname(os.path.normpath(fe["src"])):
            return True
    return False


def applied_folder(a) -> str:
    """The folder album A lives in once applied: where its files went (a set's Disc 01, Disc 02
    folders: the album's folder that holds them), or its own folder when tagged in place."""
    if not applied_moved(a):
        return (a or {}).get("source_dir") or ""
    from .jobs import DISC_DIR
    dsts = [os.path.dirname(fe["dst"]) for fe in a.get("files") or []
            if fe.get("op") in ("move", "copy") and fe.get("dst")]
    try:
        dest = os.path.commonpath(dsts)
    except ValueError:
        dest = dsts[0]
    while DISC_DIR.match(os.path.basename(dest)):
        dest = os.path.dirname(dest)
    return dest


def status_words(a) -> str:
    """The Status column's words for album A: "Applied · moved" / "Applied" once it is applied,
    else its status as label_for says it."""
    if a.get("status") == "done":
        return tr(APPLIED_MOVED) if applied_moved(a) else tr(APPLIED)
    return label_for(a)[1]


def applied_tip(a) -> str:
    folder = applied_folder(a)
    return (tr("Applied: moved to {0}") if applied_moved(a) else tr("Applied: tagged in place, in {0}")).format(folder)


def below_floor_text(a) -> str:
    """MOV-5: an album tagged where it is (or not copied) only because its score is below the
    profile's floor for filing: "not moved (score 92 < 95)". "" for any other album."""
    bf = (a or {}).get("below_floor") or {}
    if not bf or (a or {}).get("status") == "done":
        return ""
    if bf.get("reason") == "partial":                 # (MOV-6: no release proven)
        return tr("not copied (no release proven)") if bf.get("op") == "copy" else tr("not moved (no release proven)")
    words = tr("not copied (score {0:.0f} < {1:g})") if bf.get("op") == "copy" else \
        tr("not moved (score {0:.0f} < {1:g})")
    return words.format(float(bf.get("confidence") or 0), float(bf.get("floor") or 0))


def offered(a) -> bool:
    """MOV-5/MOV-6: the album was kept where it is (or not copied) only for want of evidence - its
    score below the floor, or no release proven - and filing it is yours to ask: its row's box
    (ticking it is the ask), Move into the Library Layout Anyway."""
    return bool((a or {}).get("below_floor")) and (a or {}).get("status") != "done"


def best_candidate(a) -> dict:
    """MOV-6: an Unresolved album's closest release the sources found (its identification's first
    candidate with a release link: {"ref", "title", "score", ...}); {} when there is none."""
    if (a or {}).get("status") != "unresolved":
        return {}
    for c in ((a.get("identification") or {}).get("candidates") or []):
        if isinstance(c, dict) and c.get("ref"):
            return c
    return {}


def release_offered(a) -> bool:
    """MOV-6: an Unresolved album whose closest release can be shown and filed - only by its
    own command ("Use … and File It…"), after its tags are shown; never by its box."""
    return bool(best_candidate(a))


def candidate_words(c) -> str:
    """A candidate release in a few words: "Real Band – Real Album (2005, CD, Germany, CAT-1)"."""
    import re
    title = str(c.get("title") or c.get("ref") or "")
    m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", title)
    head, year = (m.group(1), m.group(2)) if m else (title, "")
    head = head.replace(" - ", " – ", 1)
    facts = [year] + [x.strip() for x in str(c.get("details") or "").split("·")]
    facts = [x for x in facts if x]
    return f"{head} ({', '.join(facts)})" if facts else head


def use_candidate_words(a) -> str:
    """The command that shows an Unresolved album's closest release, to file it: "Use … and File It…"."""
    c = best_candidate(a)
    return tr("Use {0} &and File It…").format(candidate_words(c).replace("&", "&&")) if c else ""


UNRESOLVED_ACTION_WORDS = {"mark": N_("Apply marks it Unresolved where it is"),
                           "move": N_("Apply moves it to your unresolved folder"),
                           "leave": N_("Apply leaves it as it is")}


def unresolved_reason(a, action="") -> str:
    """Why an Unresolved album is not tagged from a release, and how to have it filed."""
    if (a or {}).get("status") != "unresolved":
        return ""
    c = best_candidate(a)
    did = tr(UNRESOLVED_ACTION_WORDS[action]) + ". " if action in UNRESOLVED_ACTION_WORDS else ""
    if not c:
        return tr("Unresolved: no release the sources found fits it, so there are no tags to file it with. "
                  "Choose its release with Change Release.") + (" " + did.strip() if did else "")
    return tr("Unresolved: no release is proven; the closest found is {0} (score {1:.0f}). ").format(
        candidate_words(c), float(c.get("score") or 0)) + did + \
        tr("To file it with that release's tags, choose “Use … and File It…”: its tags are shown first.")


ASK_WORDS = N_("Tick it, or choose Move into Library Anyway, to have it filed.")
ASK_WORDS_COPY = N_("Tick it, or choose Copy into Library Anyway, to have it copied.")
# (an album Apply tags where it is: its box says that already, the command files it)
ASK_WORDS_TAGGED = N_("Choose Move into Library Anyway to have it filed.")


def not_filed_reason(a) -> str:
    """Why an offered album (offered) is not filed unasked, in plain words; "" for any other."""
    if not offered(a):
        return ""
    bf = a["below_floor"]
    copy = bf.get("op") == "copy"
    where = tr("not copied into your library") if copy else tr("not moved into your library")
    tagged = tr("Tagged as far as the sources allow") if a.get("status") != "skipped" else tr("Nothing written")
    if bf.get("reason") == "partial":
        prov = str((a.get("identification") or {}).get("provider") or "")
        why = tr("it isn't on MusicBrainz or Discogs") if ("web" in prov or not prov) else \
            tr("no single release of it is proven")
    else:
        why = tr("its score {0:.0f} is below the {1:g} this profile asks for to file an album").format(
            float(bf.get("confidence") or 0), float(bf.get("floor") or 0))
    ask = ASK_WORDS_COPY if copy else ASK_WORDS_TAGGED if can_apply(a) else ASK_WORDS
    if can_apply(a):                                   # (its tags not written yet: Apply writes them there)
        tagged = tr("Apply tags it where it is")
    return tr("{0}; {1} because {2}.").format(tagged, where, why) + " " + tr(ask)


def offer_of(a, page=None):
    """What the bar over an album's tags (and the Inspector) offers: (words, button, enabled,
    kind) - kind "file" (MOV-5/MOV-6: Move/Copy into Library Anyway) or "use" (an Unresolved
    album's closest release, shown to be filed); ("", "", False, "") for none. PAGE: the Overview."""
    if a is None:
        return "", "", False, ""
    words = not_filed_reason(a)
    if words:
        copy = (a.get("below_floor") or {}).get("op") == "copy"
        return (words, tr("Copy into Library Anyway") if copy else tr("Move into Library Anyway"),
                bool(page is not None and page.may_file_anyway(a)), "file")
    if release_offered(a):
        action = page.unresolved_action(a) if page is not None and hasattr(page, "unresolved_action") else ""
        return (unresolved_reason(a, action), use_candidate_words(a),
                bool(page is not None and page.may_use_candidate(a)), "use")
    return "", "", False, ""


def tickable(a) -> bool:
    """MOV-6: the row's box can be ticked: it has changes Apply would make (can_apply), or its
    filing is offered (ticking it asks for it). An Unresolved album's box keeps its meaning
    (ticked: its unresolved action; unticked: nothing done)."""
    return can_apply(a) or offered(a)


def album_changes_text(a) -> str:
    """The Changes column: changes_text, and what MOV-5 kept from happening (below_floor_text)."""
    if a.get("status") == planmod.LISTED:              # (EDT-2: your plain edits, until Save Tags)
        n = sum(1 for fe in a.get("files") or [] if fe.get("op", "none") != "none")
        return tr("{0} edited, not saved").format(plural(n, tr("track"), tr("tracks"))) if n and \
            planmod.plain_pending(a) else ""
    parts = [t for t in (changes_text(a.get("files") or []), below_floor_text(a)) if t]
    return "; ".join(parts)


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


CALM_BANDS = ("excellent", "good")          # (a light wash; the others strong and bold)


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
    """The album has changes Apply would make (ready, or Unresolved with its notes). (plan is
    imported once, above: an import here, asked of every row many times a second, went through
    the Qt bindings' import hook each time)"""
    return a["status"] in ("ready", "unresolved") and planmod.has_work(a)


class PlanModel(QAbstractTableModel):
    COLS = (N_("Status"), N_("Album folder"), N_("Source"), N_("Score"), N_("Changes"), N_("Why"), N_("Profile"),
            N_("Scans"))
    SCANS = 7
    PROFILE = 6                        # (the album's profile: its own, or the one chosen above)
    LEAVE_OUT = "_leave_out"           # (an album's own mark: unticked, so Apply leaves it as it is)
    ticked = Signal()
    file_asked = Signal(list)          # (MOV-6: offered albums you ticked - to be filed as you ask)
    ASKED = "_asked_file"              # (ticked, its filing asked for: shown ticked while planned again)

    def __init__(self):
        super().__init__()
        self.albums = []
        self._cells = {}                # (row, column) -> its QModelIndex: cell()
        self.modelReset.connect(self._cells.clear)
        self.rowsRemoved.connect(lambda *_: self._cells.clear())
        # set by the page that shows a Profile column: album -> (profile name, chosen for it),
        # and (albums, name) for a profile chosen in a row ("": the one chosen above)
        self.profile_of = None
        self.choose_profile = None
        self.locked = False             # (busy: no profile is chosen while albums are looked up)
        # set by the page: album -> what is being done with it now ("Waiting…", "Looking up…",
        # "Applying…"; "": nothing), shown in its Status column until it is done (never in the plan)
        self.state_of = None
        self.unresolved_action = None   # (set by the page: album -> its profile's unresolved action)

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.column() == 1 and tickable(self.albums[idx.row()]):
            f |= Qt.ItemIsUserCheckable                  # (ticked: applied; unticked: left out)
        if idx.isValid() and idx.column() == self.PROFILE and self.choose_profile and not self.locked:
            f |= Qt.ItemIsEditable                       # (an album applied too: filed again with it)
        return f

    def cell(self, row, col):
        """The index of ROW and COL, made once (a table's index is its row and column alone, so
        it never goes stale): made by Qt each time, every call let another thread take Python's
        lock and waited for it back - the albums pane asks the list for its rows' words often."""
        got = self._cells.get((row, col))
        if got is None:
            got = self.index(row, col)
            if got.isValid():
                self._cells[(row, col)] = got
        return got

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

    @classmethod
    def left_out(cls, a) -> bool:
        """Unticked: left out by you, or held (WEB-1: planned, unticked until you tick it); an
        album with nothing to apply where it is but its filing offered: until you tick it (MOV-6)."""
        if offered(a) and not can_apply(a):
            return not a.get(cls.ASKED)
        return bool(a.get(cls.LEAVE_OUT)) or planmod.held(a)

    @classmethod
    def shows_ticked(cls, a) -> bool:
        """The row's box shows ticked."""
        return not cls.left_out(a)

    def set_left_out(self, albums, out):
        asked = []
        for a in albums:
            if offered(a) and not can_apply(a):     # (MOV-6: ticked, it is the ask to file it)
                if out:
                    a.pop(self.ASKED, None)
                    a[self.LEAVE_OUT] = True
                elif not a.get(self.ASKED):
                    a[self.ASKED] = True
                    a.pop(self.LEAVE_OUT, None)
                    asked.append(a)
                continue
            if can_apply(a):
                if out:
                    a[self.LEAVE_OUT] = True
                    a.pop("held_ok", None)
                else:
                    a.pop(self.LEAVE_OUT, None)
                    if a.get("held"):          # (WEB-1: ticked - you chose to apply it)
                        a["held_ok"] = True
        self.dataChanged.emit(self.index(0, 0), self.index(max(0, len(self.albums) - 1), len(self.COLS) - 1))
        self.ticked.emit()
        if asked:
            self.file_asked.emit(asked)

    def set_plan(self, plan):
        self.beginResetModel()
        self.albums = plan["albums"] if plan else []
        self.endResetModel()

    def adopt(self, albums):
        """ALBUMS (a list holding the same albums as the rows shown, in their order: another
        list, or other entries of them) shown in their place, row for row, without a reset."""
        self.albums = albums
        self.refresh_rows()

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

    def multiData(self, idx, span):
        fill_roles(self.data, idx, span)

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
        if c == 0 and role == R_DISPLAY:                   # (first: what the list is sorted by)
            return (self.state_of(a) if self.state_of else "") or status_words(a)
        ident = a["identification"]
        now = self.state_of(a) if self.state_of and c == 0 and role in (
            R_DISPLAY, R_TOOLTIP, R_ACCESSIBLETEXT) else ""
        if now:
            return now
        if role == R_DISPLAY:
            if c == 0:
                return status_words(a)                        # (the icon is the DecorationRole)
            if c == 1:
                return album_words(a["source_dir"])
            if c == 2:
                return source_text(ident)
            if c == self.SCANS:
                return scans_text(ident.get("scans"))
            if c == 3:
                return f"{ident['confidence']:.0f}" if ident.get("confidence") else ""
            if c == 4:
                return album_changes_text(a)
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
        if role == R_FOREGROUND and c == 0 and a["status"] in ("blocked", "failed", "conflict"):
            return status_color(a["status"])               # (only what stops Apply in colour; the icon says the rest)
        if role == R_FOREGROUND and c == 0 and a["status"] == "done":
            from PySide6.QtGui import QColor
            from .style import color
            return QColor(color("applied"))
        if role == R_DECORATION and c == 0:
            from . import icons
            if self.state_of and self.state_of(a) == tr(APPLYING):
                return icons.icon("loader-circle", "accent", 14)     # (its files being written now)
            if a["status"] == "done":
                return icons.icon("circle-check", "applied", 14)
            return icons.icon(status_icon(a["status"]), a["status"], 14)
        if role == R_TOOLTIP and c == 0 and a["status"] == "done":
            return applied_tip(a)
        if role == R_TOOLTIP and c == 0 and offered(a):
            return not_filed_reason(a)
        if role == R_TOOLTIP and c == 0 and a["status"] == "unresolved":
            return unresolved_reason(a, self.unresolved_action(a) if self.unresolved_action else "")
        if role == R_TOOLTIP and c == 0:
            tier = (ident or {}).get("tier")
            if a["status"] not in ("unresolved", "excluded", "skipped") and tier in TIER_TIPS:
                if tier == "album_only" and suggested_pressing(a):
                    return tr(SUGGESTED_TIP)
                return tr(TIER_TIPS[tier])
            return label_for(a)[1]                            # (the whole word when the column cuts it)
        if role == R_TOOLTIP and c == 1:
            if a.get("copies"):                            # VIN-6: the copies of its release, side by side
                import dynamics
                return a["source_dir"] + "\n" + dynamics.words(a["copies"])
            return a["source_dir"]
        if role == R_TOOLTIP and c in (2, 4, 5):
            # (the whole text, wrapped, when the column cuts it: Why in full, every conflict)
            if c == 5 and a["status"] not in ("excluded",) and not a["conflicts"] and \
                    ident.get("status") != "unresolved":
                text = "; ".join(ident.get("why") or [])
            elif c == 5 and len(a["conflicts"]) > 1:
                text = "\n".join(a["conflicts"])
            else:
                text = self.data(idx, R_DISPLAY) or ""
            if not text:
                return ""
            import html as _h
            return "<p style='white-space:pre-wrap'>" + _h.escape(text) + "</p>"
        if role == R_TOOLTIP and c == self.SCANS:
            return scans_tip(ident.get("scans"))
        if role == R_FOREGROUND and c == self.SCANS:
            st = (ident.get("scans") or {}).get("state")
            key = {"proven": "done", "differs": "unresolved"}.get(st)
            return status_color(key) if key else (status_color("excluded") if st else None)
        if role == R_ACCESSIBLETEXT and c == self.SCANS:
            return scans_text(ident.get("scans"))
        if role == R_TOOLTIP and c == self.PROFILE and self.profile_of:
            name, own = self.profile_of(a)
            return (tr("Profile {0}, chosen for this album").format(name) if own else
                    tr("Profile {0}, the one chosen above").format(name)) + \
                (tr(". Click to choose another for it.") if self.flags(idx) & Qt.ItemIsEditable else "")
        if role == R_EDIT and c == self.PROFILE and self.profile_of:
            return self.profile_of(a)[0] if self.profile_of(a)[1] else ""
        if role == R_FONT and c == self.PROFILE and self.profile_of and not self.profile_of(a)[1]:
            from PySide6.QtGui import QFont
            f = QFont()
            f.setItalic(True)                             # (the one chosen above: it follows that choice)
            return f
        if role == R_TEXTALIGNMENT and c == 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if c == 3 and role in (R_BACKGROUND, R_TOOLTIP):
            band = score_band(a)
            if band is None:
                return None
            if role == R_TOOLTIP:
                return tr(SCORE_BANDS[band][1]) + " " + tr("The score is how well the release fits, not a "
                                                           "probability.")
            from PySide6.QtGui import QBrush, QColor
            from .style import is_dark
            # a fit to trust: a light wash; a doubtful one: twice as strong, and bold (below), so it
            # stands out in a column of good ones by weight too, not by hue alone
            calm = band in CALM_BANDS
            r, g, b = SCORE_BANDS[band][0]
            return QBrush(QColor(r, g, b, (45 if is_dark() else 30) if calm else (100 if is_dark() else 70)))
        if role == R_FONT and c == 3 and score_band(a) in ("check", "investigate"):
            from PySide6.QtGui import QFont
            f = QFont()
            f.setBold(True)
            return f
        if role == R_ACCESSIBLETEXT and c == 3 and score_band(a):
            # (the band in words too: its tint alone says nothing to a screen reader)
            return f"{self.data(idx, R_DISPLAY) or ''}, {tr(SCORE_BANDS[score_band(a)][1]).split(':')[0]}"
        if role == R_ACCESSIBLETEXT and c == 0:
            return status_words(a)
        if role == R_CHECKSTATE and c == 1 and tickable(a):
            return Qt.Checked if self.shows_ticked(a) else Qt.Unchecked
        if role == R_USER:
            return a
        return None


# --------------------------------------------------------------------------
# the list's rows, sorted and filtered
# --------------------------------------------------------------------------
def sort_key(v):
    """A cell's value as the list sorts it, as Qt's QSortFilterProxyModel compared it: a value
    before none ascending, none first descending (sorted with reverse=True, as in Qt), numbers
    by value, text case-sensitively by its characters."""
    if v is None:
        return (1,)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, 0, v, "")
    return (0, 1, 0, str(v))


class SortedRows(QAbstractProxyModel):
    """The plan's rows as the list shows them: sorted by a column and filtered by words, as a
    QSortFilterProxyModel did it - the same order (stable: rows that tie keep the plan's order),
    the same rows, the same signals for the view's selection - but sorted here, in one pass over
    the rows with list.sort, not through a comparison asked of the model twice per step (a list
    of thousands of albums made tens of thousands of such calls, each into Python, at every sort).
    The plan's own order (the model's rows) is never changed.

    Sorting is kept as rows change while dynamicSortFilter() (the default); switched off (work
    running), the rows keep their places and new ones come at the end, as before."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._rows = []                  # proxy row -> source row
        self._pos = {}                   # source row -> proxy row (shown rows only)
        self._col, self._order = -1, Qt.AscendingOrder
        self._dynamic = True
        self._text = ""                  # the filter's words (case-insensitive), "" for none
        self._cs = Qt.CaseInsensitive
        self._key_col = -1
        self._keys = {}                  # source row -> its sort key, as the rows were last sorted
        self._only = None                # album -> shown (the toolbar's All / Ready / Needs Attention)

    # ---- the source ---------------------------------------------------------------------------
    def setSourceModel(self, m):
        old = self._src
        if old is not None:
            for sig, slot in self._links:
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.beginResetModel()
        super().setSourceModel(m)
        self._src = m
        self._links = [(m.modelAboutToBeReset, self.beginResetModel), (m.modelReset, self._source_reset),
                       (m.rowsAboutToBeRemoved, self._source_removing), (m.rowsRemoved, self._source_removed),
                       (m.rowsInserted, self._source_inserted), (m.dataChanged, self._source_changed),
                       (m.layoutChanged, self._source_relaid), (m.headerDataChanged, self.headerDataChanged)]
        for sig, slot in self._links:
            sig.connect(slot)
        self._rows = self._wanted()
        self._index()
        self.endResetModel()

    def _index(self):
        self._pos = {s: p for p, s in enumerate(self._rows)}

    def _value(self, row, col, role=R_DISPLAY):
        m = self._src
        return m.data(m.index(row, col), role)

    def _accepts(self, row) -> bool:
        if self._only is not None:
            albums = getattr(self._src, "albums", None)
            if albums is not None and 0 <= row < len(albums) and not self._only(albums[row]):
                return False
        if not self._text:
            return True
        cols = range(self._src.columnCount()) if self._key_col < 0 else (self._key_col,)
        want = self._text if self._cs == Qt.CaseSensitive else self._text.casefold()
        for c in cols:
            v = self._value(row, c)
            if v is None:
                continue
            v = str(v)
            if want in (v if self._cs == Qt.CaseSensitive else v.casefold()):
                return True
        return False

    def _key(self, row):
        k = self._keys[row] = sort_key(self._value(row, self._col))
        return k

    def _sorted(self, rows) -> list:
        """ROWS (source rows) in the order the column sorts them; rows that tie keep their order
        in ROWS (Qt's sort is stable over the rows as they were)."""
        rows = list(rows)
        self._keys = {}
        if self._col < 0 or self._src is None:
            return rows
        keys = {r: self._key(r) for r in rows}
        return sorted(rows, key=keys.__getitem__, reverse=self._order == Qt.DescendingOrder)

    def _wanted(self) -> list:
        """The rows from scratch (a reset): the plan's order, filtered, then sorted."""
        n = self._src.rowCount() if self._src is not None else 0
        return self._sorted(r for r in range(n) if self._accepts(r))

    def _placed(self, rows, new) -> list:
        """ROWS (shown, in order) with NEW (source rows, in order) put in: where the sort puts
        each, after the rows it ties with (Qt's upper bound); unsorted, each block after the row
        of the album before it in the plan."""
        if not new:
            return list(rows)
        out = list(rows)
        if self._col < 0:
            for r in sorted(new):
                pos = {s: p for p, s in enumerate(out)}
                before = next((pos[s] for s in range(r - 1, -1, -1) if s in pos), -1)
                out.insert(before + 1, r)
            return out
        import bisect
        rev = self._order == Qt.DescendingOrder
        # (descending: the keys negated by reversing the list, so one bisect serves both orders)
        keys = [self._keys[s] if s in self._keys else self._key(s) for s in out]
        for r in new:
            k = self._key(r)
            if rev:
                i = len(keys) - bisect.bisect_left(keys[::-1], k)
            else:
                i = bisect.bisect_right(keys, k)
            out.insert(i, r)
            keys.insert(i, k)
        return out

    def _move_to(self, target):
        """From the rows shown now to TARGET (source rows, in order), as a view follows it: the
        rows going removed, the ones staying put in their new order (their persistent indexes -
        the selection - moved with them), then the new ones inserted where they belong."""
        keep = set(target)
        gone = [p for p, s in enumerate(self._rows) if s not in keep]
        for start, end in reversed(_runs(gone)):
            self.beginRemoveRows(QModelIndex(), start, end)
            del self._rows[start:end + 1]
            self.endRemoveRows()
        have = set(self._rows)
        staying = [s for s in target if s in have]
        if staying != self._rows:
            self.layoutAboutToBeChanged.emit()
            old = self._rows
            new_pos = {s: p for p, s in enumerate(staying)}
            persistent = self.persistentIndexList()
            moved = [self.index(new_pos[old[i.row()]], i.column()) if 0 <= i.row() < len(old) else QModelIndex()
                     for i in persistent]
            self._rows = staying
            self.changePersistentIndexList(persistent, moved)
            self._index()
            self.layoutChanged.emit()
        if len(self._rows) != len(target):
            at = 0
            while at < len(target):
                if at < len(self._rows) and self._rows[at] == target[at]:
                    at += 1
                    continue
                end = at
                while end < len(target) and target[end] not in have:
                    end += 1
                self.beginInsertRows(QModelIndex(), at, end - 1)
                self._rows[at:at] = target[at:end]
                self.endInsertRows()
                at = end
        self._index()

    def _source_reset(self):
        self._rows = self._wanted()
        self._index()
        self.endResetModel()

    def _source_removing(self, parent, first, last):
        for s in range(first, last + 1):
            self._keys.pop(s, None)
        gone = sorted(self._pos[s] for s in range(first, last + 1) if s in self._pos)
        for start, end in reversed(_runs(gone)):
            self.beginRemoveRows(QModelIndex(), start, end)
            del self._rows[start:end + 1]
            self.endRemoveRows()
        self._index()

    def _source_removed(self, parent, first, last):
        n = last - first + 1
        self._rows = [s - n if s > last else s for s in self._rows]
        self._keys = {(s - n if s > last else s): k for s, k in self._keys.items() if not first <= s <= last}
        self._index()

    def _source_inserted(self, parent, first, last):
        n = last - first + 1
        self._rows = [s + n if s >= first else s for s in self._rows]
        self._keys = {(s + n if s >= first else s): k for s, k in self._keys.items()}
        self._index()
        new = [r for r in range(first, last + 1) if self._accepts(r)]
        if not new:
            return
        if self._dynamic and self._col >= 0:
            self._move_to(self._placed(self._rows, new))       # (each where the sort puts it)
            return
        # (unsorted, or work running: the new rows - one block of the plan - together after the
        # row of the album before them, as QSortFilterProxyModel put them: an album put under
        # the list comes at its end)
        before = next((self._pos[s] for s in range(first - 1, -1, -1) if s in self._pos), -1)
        self._move_to(self._rows[:before + 1] + new + self._rows[before + 1:])

    def _source_changed(self, top, bottom, roles=()):
        changed = range(top.row(), bottom.row() + 1)
        shown = [self._pos[s] for s in changed if s in self._pos]
        if shown:
            self.dataChanged.emit(self.index(min(shown), top.column()), self.index(max(shown), bottom.column()),
                                  list(roles))
        if not self._dynamic:
            return
        rows = list(self._rows)
        new = []
        if self._text or self._only is not None:              # (filtered again: rows in or out)
            out = {s for s in changed if s in self._pos and not self._accepts(s)}
            new = [s for s in changed if s not in self._pos and self._accepts(s)]
            rows = [s for s in rows if s not in out]
        if self._col >= 0 and top.column() <= self._col <= bottom.column():
            # (a row whose value changed is taken out and put back where it now belongs, after
            # the rows it ties with; the others stay put, as with Qt's own proxy)
            moved = []
            for s in rows:
                if top.row() <= s <= bottom.row():
                    old = self._keys.get(s)
                    if old is None or self._key(s) != old:
                        moved.append(s)
            if moved:
                gone = set(moved)
                rows = [s for s in rows if s not in gone]
                new = sorted(set(new) | gone)
            rev = self._order == Qt.DescendingOrder
            ks = [self._keys[s] if s in self._keys else self._key(s) for s in rows]
            if ks != sorted(ks, reverse=rev):                 # (not sorted before: sorted now)
                rows = self._sorted(rows)
        if new or rows != self._rows:
            self._move_to(self._placed(rows, new) if new else rows)

    def _source_relaid(self, *_):
        self._move_to(self._wanted())

    # ---- what the view asks -------------------------------------------------------------------
    def index(self, row, col, parent=QModelIndex()):
        if parent.isValid() or not (0 <= row < len(self._rows)) or not (0 <= col < self.columnCount()):
            return QModelIndex()
        return self.createIndex(row, col)

    def parent(self, idx=QModelIndex()):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self._src is None else self._src.columnCount()

    def hasChildren(self, parent=QModelIndex()):
        return not parent.isValid() and bool(self._rows)

    def mapToSource(self, idx):
        if not idx.isValid() or not (0 <= idx.row() < len(self._rows)):
            return QModelIndex()
        return self._src_index(self._rows[idx.row()], idx.column())

    def mapFromSource(self, idx):
        if not idx.isValid():
            return QModelIndex()
        p = self._pos.get(idx.row())
        return QModelIndex() if p is None else self.createIndex(p, idx.column())

    def data(self, idx, role=R_DISPLAY):
        if not idx.isValid() or not (0 <= idx.row() < len(self._rows)):
            return None
        return self._src.data(self._src_index(self._rows[idx.row()], idx.column()), role)

    def multiData(self, idx, span):
        if not idx.isValid() or not (0 <= idx.row() < len(self._rows)):
            for rd in span:
                rd.clearData()
            return
        fill_roles(self._src.data, self._src_index(self._rows[idx.row()], idx.column()), span)

    def _src_index(self, row, col):
        cell = getattr(self._src, "cell", None)
        return cell(row, col) if cell is not None else self._src.index(row, col)

    def setData(self, idx, value, role=R_EDIT):
        return self._src.setData(self.mapToSource(idx), value, role)

    def flags(self, idx):
        return self._src.flags(self.mapToSource(idx)) if idx.isValid() else Qt.NoItemFlags

    def headerData(self, s, o, role=R_DISPLAY):
        if o == Qt.Vertical:
            return self._src.headerData(self._rows[s], o, role) if 0 <= s < len(self._rows) else None
        return self._src.headerData(s, o, role)

    # ---- sorting and the filter, as QSortFilterProxyModel's -----------------------------------
    def sort(self, column, order=Qt.AscendingOrder):
        order = Qt.SortOrder(order)
        if self._dynamic and (column, order) == (self._col, self._order):
            return                                            # (kept sorted so already)
        self._col, self._order = column, order
        # (stable over the rows as shown: ties keep the order the last sort gave them; no
        # column: the plan's order)
        self._move_to(self._sorted(self._rows) if column >= 0 else sorted(self._rows))

    def sortColumn(self) -> int:
        return self._col

    def sortOrder(self):
        return self._order

    def dynamicSortFilter(self) -> bool:
        return self._dynamic

    def setDynamicSortFilter(self, on):
        self._dynamic = bool(on)
        if self._dynamic and self._col >= 0:                  # (switched on: sorted again)
            self._move_to(self._sorted(self._rows))

    def setFilterFixedString(self, text):
        self._text = str(text or "")
        rows = [r for r in self._rows if self._accepts(r)]
        have = set(self._rows)
        n = self._src.rowCount() if self._src is not None else 0
        self._move_to(self._placed(rows, [r for r in range(n) if r not in have and self._accepts(r)]))

    def set_only(self, fn):
        """Only the albums FN(album) keeps (None: every album), with the words' filter too."""
        self._only = fn
        self.setFilterFixedString(self._text)

    def setFilterCaseSensitivity(self, cs):
        self._cs = cs

    def setFilterKeyColumn(self, col):
        self._key_col = int(col)

    def filterRegularExpression(self):
        from PySide6.QtCore import QRegularExpression
        return QRegularExpression(QRegularExpression.escape(self._text) if self._text else "")


def _runs(rows) -> list:
    """[(first, last)] of the sorted ROWS' runs of neighbours."""
    out = []
    for r in rows:
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out
