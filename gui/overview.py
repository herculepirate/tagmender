"""gui/overview.py - The Overview page: drop or choose a folder, pick a profile, Preview, then Apply."""
from __future__ import annotations

import html
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import icons, setup, sheets, style
from .common import (album_words, counts_by_label, counts_text, ex, load_profile, notify, pf, planmod, plural,
                     popup_menu, profile_names, suggested_pressing)
from .i18n import N_, tr
from .work import start
from .models import PlanModel, SortedRows, can_apply
from .widgets import ApplyProgress, ElidedPath as _Elided, SleeveDrop
from .preview_panes import _held

STREAM_MS = 100                                       # ms: albums read meanwhile are put in the list this often
APPLY_LISTED = 5                                     # albums named in the Apply sheet (then "and N more")


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def run_report(plan, res):
    """The report of what Apply just did (report.build), shown once it is done; None when it
    cannot be made (the job still is, under Jobs)."""
    import time

    import report
    try:
        rep = report.build(plan, res["albums"], job={"status": res.get("status") or "done"})
    except Exception:
        return None
    rep["job"], rep["started"] = res.get("job") or rep["job"], time.time()
    return rep


# what an album that changed nothing ended as: a job of only these has nothing to undo
NOTHING_DONE = {"conflict", "failed", "cancelled", "unchanged"}


def planned_files(albums) -> list:
    """Each album's files Apply is to write, move or copy, as planned (the status bar's progress
    counts them; the executor tells the count it has as each album starts)."""
    return [sum(1 for fe in a.get("files") or [] if fe.get("op", "none") != "none") for a in albums]


def merge_results(plans, run, cancelled=lambda: False) -> dict:
    """One Apply's result from running PLANS (one per profile, each a job of its own) in order:
    the albums' indexes count on through them. A plan whose run cannot start (another job is
    running or waits to be recovered) fails its albums; once cancelled, the rest wait."""
    jobs, counts, albums, off, partial = [], {}, [], 0, False
    for at, plan in enumerate(plans):
        n = len(plan["albums"])
        if cancelled():                                     # (the rest not started: counted cancelled,
            left = sum(len(p["albums"]) for p in plans[at:])  # as the executor counts its own)
            if left:
                counts["cancelled"] = counts.get("cancelled", 0) + left
                partial = True
            break
        try:
            res = run(plan)
        except Exception as exc:
            if len(plans) == 1:
                raise
            partial = True
            for k, a in enumerate(plan["albums"]):
                albums.append({"index": off + k, "album": a["source_dir"], "status": "failed",
                               "label": "Failed", "detail": str(exc).splitlines()[0] if str(exc) else ""})
            counts["failed"] = counts.get("failed", 0) + n
            off += n
            continue
        jobs.append(res["job"])
        partial = partial or res.get("status") != "done"
        for k, v in (res.get("counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
        for r in res.get("albums") or []:
            albums.append(dict(r, index=off + r["index"]) if "index" in r else r)
        off += n
    return {"job": ", ".join(jobs), "status": "partial" if partial else "done", "counts": counts, "albums": albums}


def run_batches(batches, run, cancelled, partial, progress, workers=None) -> int:
    """The look-up's albums, in batches (a profile and the folder they are read from), at most
    WORKERS albums at once in all: one batch shares them among its albums, several (albums dropped
    from different folders) run side by side. run(batch, report, workers) -> albums looked up;
    report(what, args) from any thread, emitted here, on the look-up's own thread, in order."""
    import engine
    import queue
    import threading
    workers = workers or engine.LOOKUP_WORKERS
    if len(batches) < 2:
        n = 0
        for b in batches:
            if not cancelled():
                n += run(b, lambda what, a: (partial if what == "partial" else progress)(a), workers)
        return n
    each = max(1, workers // len(batches))
    todo, out = queue.SimpleQueue(), queue.SimpleQueue()
    for b in batches:
        todo.put(b)
    failed = threading.Event()

    def side():
        try:
            while not cancelled() and not failed.is_set():
                try:
                    b = todo.get_nowait()
                except queue.Empty:
                    break
                out.put(("count", run(b, lambda what, a: out.put((what, a)), each)))
        except BaseException as exc:              # (raised below, on the look-up's thread)
            failed.set()
            out.put(("failed", exc))
        finally:
            out.put(("ended", None))
    threads = [threading.Thread(target=side, name=f"look-up-batch-{i}", daemon=True)
               for i in range(min(workers, len(batches)))]
    for t in threads:
        t.start()
    n, error, live = 0, None, len(threads)
    while live:
        what, a = out.get()
        if what == "ended":
            live -= 1
        elif what == "failed":
            error = error or a
        elif what == "count":
            n += a
        else:
            (partial if what == "partial" else progress)(a)
    for t in threads:
        t.join()
    if error is not None:
        raise error
    return n


def plan_each(e, path, keys, emit, progress=None, cancel=None, meta=None) -> int:
    """Engine E's plan of KEYS an album at a time (engine.Engine.plan_each)."""
    return e.plan_each(path, keys, emit, progress=progress, cancel=cancel, meta=meta)


# Look Up's sources (List first): the profile's, or one of them alone
LOOK_UP = (("profile", N_("Profile's sources")), ("musicbrainz_only", N_("MusicBrainz only")),
           ("musicbrainz_preferred", N_("MusicBrainz preferred")), ("discogs_preferred", N_("Discogs preferred")),
           ("discogs_only", N_("Discogs only")), ("web", N_("Web only")))


# The files' metadata as List first, Look Up and Preview last read it (ex.meta_memo): an
# unchanged file is not opened again to find the albums. Handed to the engine explicitly (its
# work runs on worker threads, where a context variable starts empty).
META_STORE: dict = {}


def _takes_on_album(fn) -> bool:
    """The engine's listing can send each album as soon as it is read (on_album)."""
    import inspect
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "on_album" in ps or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in ps.values())


def listing(e, path, within=None, meta=None, on_album=None) -> dict:
    """Engine E's listing of PATH; ON_ALBUM(album) called (on this thread) with each album as soon
    as its folder is read, when the engine can (one that cannot: the list whole, at the end)."""
    kw = {"within": within} if within is not None else {}
    if on_album is not None and _takes_on_album(e.listing):
        try:
            return e.listing(path, meta=meta, on_album=on_album, **kw)
        except TypeError as exc:                          # (an engine whose listing takes no on_album)
            if "on_album" not in str(exc):
                raise
    return e.listing(path, meta=meta, **kw)


def read_folders(prof, folders, on_album=None) -> list:
    """[(folder, album)] of FOLDERS, each read on its own (no wider scan). What they remember goes
    into META_STORE beside the rest: a listing given META_STORE itself would forget every album
    outside its folder, and the albums of the list would be opened again. ON_ALBUM(folder, album):
    each as soon as it is read (listing)."""
    import engine
    out = []
    for f in folders:
        seen = {}
        each = (lambda x, f=f: on_album(f, x)) if on_album is not None else None
        out += [(f, x) for x in listing(engine.Engine(prof), f, meta=seen, on_album=each).get("albums") or []]
        META_STORE.update(seen)
    return out


def dropped_folders(paths) -> list:
    """The folders read for PATHS dropped (a file: its folder), once each, in order."""
    return list(dict.fromkeys(os.path.abspath(p if os.path.isdir(p) else os.path.dirname(p)) for p in paths))


class Batches:
    """What a worker sends as it goes (its partial signal: in order, and before its result), put
    to use on the UI thread every STREAM_MS ms at most, not item by item: USE(items). Closed: the
    rest is dropped (the worker's result says it all)."""

    def __init__(self, owner, use):
        self.use, self.items, self.closed, self.shown = use, [], False, []
        self.timer = QTimer(owner)
        self.timer.setSingleShot(True)
        self.timer.setInterval(STREAM_MS)
        self.timer.timeout.connect(self.flush)

    def add(self, item):
        if self.closed:
            return
        self.items.append(item)
        if not self.timer.isActive():
            # (the first at once: a row as soon as there is one; the rest every STREAM_MS ms)
            self.timer.start(0 if not self.shown else STREAM_MS)

    def flush(self):
        items, self.items = self.items, []
        if items and not self.closed:
            self.shown += list(self.use(items) or [])

    def close(self):
        self.closed = True
        self.timer.stop()
        self.items = []


# what is being done with an album now, in its Status column (the page's, never the plan's)
ROW_STATES = {"waiting": N_("Waiting…"), "looking": N_("Looking up…"), "applying": N_("Applying…")}


RETRY_CODES = {"musicbrainz_unavailable", "discogs_unavailable", "web_unavailable", "rate_limited",
               "missing_credentials", "lookup_failed"}


def release_pages(a) -> list:
    """[(words, url)] for the release album A was looked up as: its MusicBrainz and Discogs pages
    (the IDs its files are given, or keep), else the page it was found on (a shop, a site)."""
    ids = {"musicbrainz_albumid": "", "discogs_release_id": ""}
    for fe in a.get("files") or []:
        if not isinstance(fe, dict):
            continue
        for k in ids:
            v = (fe.get("changes") or {}).get(k)
            if not v:
                row = next((r for r in fe.get("diff") or [] if isinstance(r, dict) and r.get("field") == k), None)
                v = row and (row.get("after") or row.get("before"))
            if v and not ids[k]:
                ids[k] = str(v[0] if isinstance(v, list) else v).strip()
        if all(ids.values()):
            break
    ident = a.get("identification") or {}
    out = []
    mb, dg = ids["musicbrainz_albumid"], ids["discogs_release_id"]
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", mb):
        out.append((tr("Open on MusicBrainz"), f"https://musicbrainz.org/release/{mb}"))
    if dg.isdigit():
        out.append((tr("Open on Discogs"), f"https://www.discogs.com/release/{dg}"))
    url = str(ident.get("url") or "")
    if not out and url.startswith(("https://", "http://")):
        out.append((tr("Open Source Page"), url))
    return out


def _dropped_names(paths) -> str:
    """The folders dropped, named as the status bar names them."""
    from .manual_match import _names
    return _names(paths)


def _list_first() -> bool:
    """Preferences › General › When you add a folder: List its albums first."""
    try:
        import app_settings
        return app_settings.get("add_folder") == "list"
    except Exception:
        return False


def profile_for(prof, mode):
    """PROF as Look Up uses it: as it is ("profile"), or asking only MusicBrainz, only Discogs, or
    no database but the web (its store and encyclopaedia pages)."""
    if mode == "profile":
        return prof
    import copy
    d = copy.deepcopy(prof.data)
    prov = d.setdefault("providers", {})
    if mode in ("musicbrainz", "discogs"):                 # (older names: one database alone)
        mode = f"{mode}_only"
    if mode in pf.SOURCES:                                  # (the four Sources choices of a profile)
        d["source"] = mode
        for n in ("musicbrainz", "discogs"):
            if mode.startswith(n) or mode.endswith("preferred"):
                prov.setdefault(n, {}).update(enabled=True, identify=True)
    elif mode == "web":
        for n in ("musicbrainz", "discogs"):
            prov.setdefault(n, {})["identify"] = False
        prov.setdefault("web", {})["enabled"] = True
    return pf.from_dict(d)


def library_for(prof, folder):
    """PROF with FOLDER as the library albums go to (this preview and Apply only): the profile's
    destination and every source folder's own destination (which would win over it) replaced."""
    if not folder:
        return prof
    import copy
    d = copy.deepcopy(prof.data)
    d["destination"] = folder
    for s in d.get("sources") or []:
        if isinstance(s, dict):
            s.pop("destination", None)
    return pf.from_dict(d)


# the providers a look-up waits for after a refusal (HTTP 429 or 503, or no answer), as named on screen
WAIT_TITLES = {"musicbrainz": "MusicBrainz", "mb": "MusicBrainz", "discogs": "Discogs", "web": N_("the web")}


class BackOff(QObject):
    """A look-up waiting before it asks a provider again (it said it is busy, or did not answer):
    report(provider, seconds) from any thread; waiting(provider, seconds) on the UI thread."""
    waiting = Signal(str, float)

    def report(self, provider, seconds):
        try:
            self.waiting.emit(str(provider), float(seconds))
        except (RuntimeError, TypeError, ValueError):        # (the page went: nobody to tell)
            pass


def listen_for_back_off(fn):
    """FN(provider, seconds) called (on the request's thread) as each wait of a provider's
    back-off starts (stagetimes.on_back_off: HTTP 429 or 5xx, or no answer; never the usual
    pacing). Returns what stops it, or None when the matcher has no such hook."""
    import stagetimes
    hook = getattr(stagetimes, "on_back_off", None)
    if not callable(hook):
        return None
    try:
        stop = hook(fn)
    except Exception:
        return None
    off = getattr(stagetimes, "off_back_off", None)
    return stop if callable(stop) else (lambda: off(fn)) if callable(off) else (lambda: None)


def file_anyway_words(a) -> str:
    """The Album menu's MOV-5 command, worded as the album's operation: a copy profile's album is
    copied into the library, a move profile's moved into its layout."""
    if ((a or {}).get("below_floor") or {}).get("op") == "copy":
        return tr("Cop&y into the Library Anyway")
    return tr("Move into the Library La&yout Anyway")


class Overview(QWidget):
    """The profile picker, search field, progress bar and Preview / Apply / Cancel live in the
    window's toolbar (the window makes them); the album actions (Change Release: a link, Search for
    Release, Other Versions, Choose Your Pressing, Info, Tags from File Names ...) in the Album and
    Tools menus, the table's context menu and the inspector's header."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plan = None
        self.path = None
        self.worker = None
        self.forced = {}
        # MOV-5: the albums you asked to have filed whatever the score: {audio file: profile name}
        # (an ask is the album's, under the profile it was made with: another profile, no ask)
        self.file_anyway = {}
        self.library = ""           # the library folder chosen for this preview ("": the profile's)
        self.looked = {}            # album key -> how it was looked up (profile, sources): not again
        self.removed = set()        # folders of albums taken out of the list (Remove from List)
        # several albums, each with a profile of its own: the one chosen above unless you chose
        # another in its row (album key -> profile name); the profile each album's preview was
        # made with, and that preview's plan (without its albums) per profile, for Apply
        self.album_profiles = {}
        self.planned_with = {}
        self.headers = {}
        self.used_jobs = set()      # (jobs Apply ran: the next Apply of the same preview is a new job)
        self._lookup_n = 0
        # Look Up while a look-up runs: album key -> (sources, again), looked up when it ends;
        # and the albums the running look-up has (album key -> (profile, sources))
        self.queue = {}
        self.looking = {}
        # APL-1: the Apply run beside a look-up (its worker, its albums in order) and whether the
        # look-up ended meanwhile (the work ends with the Apply then)
        self.apply_worker, self._side, self._ended_meanwhile = None, None, False
        # folders dropped (or chosen with Add Folder) while work runs: their albums join this list
        # when it ends, and are looked up then (List first: listed only)
        self.later = []
        self._later_list_only = False
        # folders dropped while Apply runs, being read now (their albums join the list at once)
        self._reading = []
        # folders dropped while a listing or a preview runs (its list replaces this one): read at
        # once, folder -> [(folder, album)] (None: being read), their rows shown meanwhile
        # (id(album) -> album) and put under the new list when the work ends, not read again
        self._early, self._provisional = {}, {}
        # the work running: "listing", "preview", "refile", "adding" (folders added to the list),
        # "lookup" or "apply"; None when nothing runs
        self._work_kind = None
        self._listing_rows = None                   # (the albums of the listing running, as they come)
        # a provider's back-off: the look-up waits before asking again; said in the status bar,
        # counting down (provider, when the wait ends)
        self.back_off, self._waiting = BackOff(self), None
        self.back_off.waiting.connect(self.backing_off)
        self._wait_timer = QTimer(self)
        self._wait_timer.setInterval(1000)
        self._wait_timer.timeout.connect(self.show_wait)
        stop = listen_for_back_off(self.back_off.report)
        if stop:
            self.destroyed.connect(lambda *_: stop())
        # album key -> the folder its look-up reads, for albums outside the folder previewed (a
        # folder dropped meanwhile, an album already applied and given another profile)
        self.roots = {}
        # what is being done with each album now (album key, or id() of a row Apply has): ROW_STATES
        self.states = {}
        self._lookup_order, self._applying = [], False
        self._lookup_i, self._lookup_name = 0, ""
        lay = QVBoxLayout(self)
        self.profile = win.profile_box
        self.search = win.search_box
        self.bar = win.progress
        self.btn_preview, self.btn_apply, self.btn_cancel = (win.act(k) for k in ("preview", "apply", "cancel"))
        self.btn_anyway, self.btn_web, self.btn_open, self.btn_release = (
            win.act(k) for k in ("process_anyway", "search_web", "open_folder", "use_release"))
        self._above = ""                                    # the profile the preview uses (the one "above")
        self._profile_follows = False                       # (the toolbar pop-up shows the selection's profile)
        self._profile_pane = "albums"                       # (the pane whose selection it follows)
        self.views = QStackedWidget()
        lay.addWidget(self.views, 1)
        self.drop_page = QWidget()                          # the empty state fills the page
        dl = QVBoxLayout(self.drop_page)
        dl.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        self.drop = SleeveDrop()
        self.drop.dropped.connect(self.set_path)
        dl.addWidget(self.drop)
        self.views.addWidget(self.drop_page)
        self.results = QWidget()
        rl = QVBoxLayout(self.results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        # 0.8.71: no page title, Sources bar or Library folder bar - the sources and the library are
        # the profile's (Preferences › Profiles); a choice for one look-up is the albums' menu's Look
        # Up ▸, and File › Library Folder for This Preview… The controls stay, out of sight, for the
        # code that reads them (the sources chosen: the profile's own)
        self._held = QWidget(self)
        self._held.hide()
        self.folder_icon = QLabel(self._held)
        self.folder_icon.setPixmap(icons.icon("folder-open", "muted").pixmap(16, 16))
        self.where = _Elided(tr("No folder chosen"))
        self.where.setParent(self._held)
        self.ocr = QCheckBox(tr("Read scans"), self._held)
        self.ocr.setAccessibleName(tr("Read scans for this preview"))
        self.choose_btn = QPushButton(tr("Choose…"), self._held)
        self.choose_btn.clicked.connect(lambda: self.drop.choose())
        self.sources_label = QLabel(tr("Sources"), self._held)
        from .rows import Popup
        self.sources = Popup(self._held)
        self.sources.setAccessibleName(tr("Sources to look up with"))
        for mode, words in LOOK_UP:
            self.sources.addItem(tr(words), mode)
        self.look_btn = QPushButton(tr("Look Up"), self._held)       # (the toolbar's Look Up: win "look_up")
        self.look_btn.clicked.connect(self.look_up_clicked)
        self.refresh_btn = QPushButton(tr("Refresh"), self._held)    # (View › Refresh, Ctrl+R)
        self.refresh_btn.clicked.connect(self.reload)
        self.clear_done_btn = QPushButton(tr("Clear Done"), self._held)   # (Edit › Clear Done Albums)
        self.clear_done_btn.clicked.connect(self.clear_done)
        self.clear_done_btn.setEnabled(False)
        self.keep_link = QLabel("", self._held)
        self.library_label = QLabel(tr("Library folder"), self._held)
        self.library_where = _Elided("")
        self.library_where.setParent(self._held)
        self.library_btn = QPushButton(tr("Choose…"), self._held)     # (File › Library Folder for This Preview…)
        self.library_btn.clicked.connect(lambda: self.choose_library())
        self.library_keep = QLabel("", self._held)
        self.profile.currentTextChanged.connect(self.profile_changed)
        self.profile.activated.connect(self.profile_picked)
        self.sources.currentIndexChanged.connect(lambda _i: self.show_sources_state())
        # notes over the panes, shown only when they have something to say
        notes = QVBoxLayout()
        notes.setContentsMargins(12, 0, 12, 0)
        notes.setSpacing(4)
        self.stale_note = QLabel("")                         # (settings changed since the look-up)
        self.stale_note.setProperty("role", "attention")
        self.stale_note.setTextFormat(Qt.RichText)
        self.stale_note.setWordWrap(True)
        self.stale_note.linkActivated.connect(lambda _h: self.refresh_stale())
        self.stale_note.hide()
        notes.addWidget(self.stale_note)
        # what the preview did without, because a key, login or program is missing (Open Setup: its row)
        self.setup_note = QLabel("")
        self.setup_note.setProperty("role", "hint")
        self.setup_note.setWordWrap(True)
        self.setup_note.setTextFormat(Qt.RichText)
        self.setup_note.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setup_note.linkActivated.connect(lambda link: setup.follow(self.win, link))
        self.setup_note.hide()
        notes.addWidget(self.setup_note)
        # a service busy or not answering while the run asks it (connection.status): a symbol and words
        self.service_note = QLabel("")
        self.service_note.setProperty("role", "attention")
        self.service_note.setWordWrap(True)
        self.service_note.setAccessibleName(tr("Online services"))
        self.service_note.hide()
        notes.addWidget(self.service_note)
        rl.addLayout(notes)
        self._service_checked = 0.0
        self.model = PlanModel()
        # (sorted and filtered here, in one pass with list.sort: Qt's own proxy compared the rows
        # through the model, tens of thousands of calls for a list of thousands of albums)
        self.proxy = SortedRows()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.model.profile_of = lambda a: (self.profile_of(a), bool(self.own_profile(a)))
        self.model.choose_profile = self.set_album_profile
        self.model.state_of = self.state_of
        self.shown_kind = "all"                              # the toolbar's All / Ready / Needs Attention
        # 0.8.71: the list is the albums pane itself (Albums as they will be: an album a row, its
        # discs and tracks under it), beside Your files, over the inspector pane (gui/preview_panes.py)
        from .preview_panes import PreviewPanes
        self.panes = PreviewPanes(self, self.proxy)
        self.panes.album_chosen.connect(self.panes_chose)
        self.panes.folders.profile_of = self.profile_of
        self.table = self.panes.album_view
        self.tree = self.panes.album_model
        # (while the mouse drags a selection, the inspector waits: reading each album it passes
        # over - tags, covers, on a network share - kept the drag from following the mouse)
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(150)
        self._detail_timer.timeout.connect(self.show_detail_after_drag)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        fv = self.panes.folder_view
        fv.setContextMenuPolicy(Qt.CustomContextMenu)
        fv.customContextMenuRequested.connect(lambda pos: self.context_menu(pos, folders=True))
        fv.selectionModel().selectionChanged.connect(self.folders_chosen)
        self.model.ticked.connect(self.win.update_album_actions)
        # the list's own keys, as in a folder: Delete (or Backspace) takes the selected albums out of
        # the list, Escape lets go of the selection. Taken before the menus' shortcuts (Remove Tag is
        # Del too: two shortcuts on one key and Qt runs neither)
        self.table.installEventFilter(self)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_columns)
        self._sized_by_you, self._fitting = set(), False
        self.table.header().sectionResized.connect(self.column_resized)
        for sig in (self.model.modelReset, self.model.rowsInserted, self.model.rowsRemoved):
            sig.connect(lambda *_: self.request_fit())
        for sig in (self.model.modelReset, self.model.rowsInserted, self.model.rowsRemoved, self.model.dataChanged,
                    self.model.ticked):
            sig.connect(self.forget_applicable)
        for sig in (self.model.modelReset, self.model.rowsInserted, self.model.dataChanged):
            sig.connect(lambda *_: self.clear_done_btn.setEnabled(any(a.get("status") == "done"
                                                                      for a in self.model.albums)))
        # (Your files made again in a turn of their own: a preview of thousands of albums shows
        # its list first)
        self.model.modelReset.connect(lambda: self._panes_timer.start())
        self.model.rowsInserted.connect(lambda *_: self._panes_timer.start())
        self.model.rowsRemoved.connect(lambda *_: self._panes_timer.start())
        self.model.dataChanged.connect(self.panes_rows_changed)
        self._panes_timer = QTimer(self)                     # (rows added or taken out: the panes once)
        self._panes_timer.setSingleShot(True)
        self._panes_timer.setInterval(0)
        self._panes_timer.timeout.connect(lambda: (self.panes.set_albums(self.model.albums), self.show_ready()))
        rl.addWidget(self.panes, 1)
        # the status bar's two ends (the window puts them there): what the list holds, and what
        # was last done (the work's progress while it runs)
        self.counts_line = QLabel("")
        self.counts_line.setAccessibleName(tr("Albums in the preview"))
        from .preview_panes import ElidedLabel
        self.summary = ElidedLabel("")
        self.summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.summary.setAccessibleDescription(tr("Summary"))    # (no fixed name: the words are what is read)
        self.ready_line = self.counts_line
        self.apply_bar = ApplyProgress()                  # (the status bar's: an Apply's or Save Tags' progress)
        self.views.addWidget(self.results)
        self.detail = self.panes.evidence.full            # (Why This Release > Full preview text)
        self.refresh_profiles()
        self.show_library_state()
        self.busy(False)

    def filter(self, text):
        self.proxy.setFilterFixedString(text)

    # ---- the toolbar's All / Ready / Needs Attention ------------------------------------------
    @staticmethod
    def needs_you(a) -> bool:
        """The album waits for your decision: held (no release proven, a conflict), its scans name
        another pressing, a tie-break only suggested its pressing (TIE-1: to confirm), or it is
        another copy of an album you have, kept where it is (DSC-12: "attention_decide"). (Asked
        of every album each time the counts are shown: no import in it - an import statement costs
        microseconds under PySide's import hook.)"""
        return _held(a) or ((a.get("identification") or {}).get("scans") or {}).get("state") == "differs" \
            or bool(suggested_pressing(a)) or bool(a.get("attention_decide"))

    @classmethod
    def listed_for_attention(cls, a) -> bool:
        """The Needs Attention filter: albums needing your decision, and those whose notes are for
        your attention only (DSC-12: "disc 2 missing", another pressing kept apart) - counted as
        ready, applied as any other."""
        return cls.needs_you(a) or bool(a.get("attention"))

    @classmethod
    def ready_now(cls, a) -> bool:
        return can_apply(a) and not cls.needs_you(a)

    SHOW = {"all": None, "ready": "ready_now", "attention": "listed_for_attention"}

    def set_show(self, kind):
        """Only the albums ready, or only those needing you, or all (the toolbar's segments)."""
        kind = kind if kind in self.SHOW else "all"
        self.shown_kind = kind
        fn = self.SHOW[kind]
        self.proxy.set_only(getattr(self, fn) if fn else None)
        self.win.show_filter(kind)

    def show_counts_line(self):
        """The status bar's left end, and the toolbar's segment counts."""
        albums = self.model.albums
        # (worked out once per turn of the event loop, and again when a row changes, as
        # applicable: a row chosen asks for the counts several times)
        memo = getattr(self, "_counts_memo", None)
        if memo is not None and memo[0] is albums and memo[1] == len(albums):
            ready, need, listed, files, tip = memo[2]
        else:
            ready = need = listed = 0
            for a in albums:                                # (each album asked once)
                if self.needs_you(a):
                    need += 1
                elif can_apply(a):
                    ready += 1
                if self.listed_for_attention(a):           # (what the Needs Attention filter lists)
                    listed += 1
            # (DSC-12: the files of the album an album joins are not its own)
            files = sum(len([fe for fe in a.get("files") or [] if not fe.get("joined")]) for a in albums)
            tip = counts_by_label(albums) if albums else ""
            self._counts_memo = (albums, len(albums), (ready, need, listed, files, tip))
            QTimer.singleShot(0, self, self.forget_applicable)
        self.counts = {"all": len(albums), "ready": ready, "attention": listed}
        if albums:
            text = " · ".join((tr("1 album ready") if ready == 1 else tr("{0} albums ready").format(ready),
                               tr("1 needs your decision") if need == 1 else tr("{0} need your decision").format(need),
                               plural(files, tr("file"), tr("files"))))
        else:
            text = ""
        self.counts_line.setText(text)
        self.counts_line.setToolTip(tip)
        self.win.show_filter_counts(self.counts)

    # ---- the preview's panes (gui/preview_panes.py) -----------------------------------------
    def panes_rows_changed(self, top, bottom, *_):
        """Rows of the list changed (a look-up, your edit): their albums in Your files too."""
        albums = self.model.albums
        for r in range(max(0, top.row()), min(len(albums) - 1, bottom.row()) + 1):
            self.panes.album_changed(albums[r])

    def panes_chose(self, album):
        """An album chosen in Your files that the list hides (the search, the toolbar's filter):
        shown again, so the list, the inspector and an edit are of the album the panes show."""
        if album is None:
            return
        r = next((i for i, a in enumerate(self.model.albums) if a is album), None)
        if r is None or self.proxy.mapFromSource(self.model.index(r, 0)).isValid():
            return
        if self.proxy.filterRegularExpression().pattern():
            self.search.clear()
            self.filter("")
        if not self.proxy.mapFromSource(self.model.index(r, 0)).isValid() and self.shown_kind != "all":
            self.set_show("all")

    # the list's own keys, as in a folder: Delete or Backspace takes the selected albums out of the
    # list, Escape lets go of the selection
    LIST_KEYS = (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Escape)

    def list_key(self, e) -> bool:
        from PySide6.QtCore import QEvent
        if e.key() not in self.LIST_KEYS or (e.modifiers() & ~Qt.KeypadModifier):
            return False
        if e.key() == Qt.Key_Escape and not self.table.selectionModel().hasSelection():
            return False                                    # (nothing selected: Escape is the window's)
        if e.type() == QEvent.ShortcutOverride:
            e.accept()                                      # (the list's key, before a menu shortcut's)
            return True
        if e.key() == Qt.Key_Escape:
            self.table.clearSelection()
        elif self.running:
            self.win.statusBar().showMessage(tr("Albums can be taken out of the list when the current "
                                                "work ends."), 4000)
        else:
            self.remove_selected()
        return True

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if hasattr(self, "table") and obj is self.table and e.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            if self.list_key(e):
                return True
        return super().eventFilter(obj, e)

    def request_fit(self, delay=None):
        """The Status column fitted to its words, soon (once per turn of the event loop)."""
        if not self._fit_timer.isActive():
            self._fit_timer.start(0 if delay is None else delay)

    STATUS_WIDTH = (130, 220)                            # (the Status column's narrowest and widest)

    def column_resized(self, c, old, new):
        """A column dragged by you keeps the width you gave it (never fitted again)."""
        if not self._fitting and old and new:
            self._sized_by_you.add(c)

    def fit_columns(self):
        """The Status column as wide as its words (STATUS_WIDTH), unless you sized it; the name
        takes what is left, the score its own few digits."""
        from .preview_panes import AlbumsModel
        if AlbumsModel.STATUS in self._sized_by_you:
            return
        lo, hi = self.STATUS_WIDTH
        want = max(lo, min(hi, self.status_wants() + 8))
        if self.table.columnWidth(AlbumsModel.STATUS) != want:
            self._fitting = True
            try:
                self.table.header().resizeSection(AlbumsModel.STATUS, want)
            finally:
                self._fitting = False

    def status_wants(self) -> int:
        """The Status column's widest cell as the view measures it (sizeHintForColumn): a cell of
        each of the words its rows show (album rows; discs and tracks opened) measured - not each
        row, as sizeHintForColumn does (a thousand rows of a big preview, each a call into Python,
        at every row added)."""
        from .preview_panes import AlbumsModel
        v, m, col = self.table, self.table.model(), AlbumsModel.STATUS
        if not isinstance(m, AlbumsModel):
            return v.sizeHintForColumn(col)
        seen = {}

        def walk(nodes):
            for n in nodes:
                idx = m.index_of(n, col)
                k = (n.kind, m.data(idx))
                if k not in seen:
                    seen[k] = idx
                if n.kids and n.kind in m.OPENS and v.isExpanded(m.index_of(n)):
                    walk(n.kids)
        walk(m.top.kids)
        return max((v.sizeHintForIndex(idx).width() for idx in seen.values()), default=0)

    def fill_profile_menu(self, menu, chosen):
        """Profile ▸: As Above (the preview's profile), then each profile, the one CHOSEN albums
        have ticked (none for a mixed selection); choosing one gives it to all of them."""
        menu.clear()
        mine = {self.own_profile(a) for a in chosen}
        names = {self.profile_of(a) for a in chosen}
        used = set()

        def lettered(words):
            """WORDS with an underlined letter none before it in this menu has ("&" doubled)."""
            words = words.replace("&", "&&")
            for i, ch in enumerate(words):
                if ch.isalnum() and ch.lower() not in used and words[max(0, i - 1):i] != "&":
                    used.add(ch.lower())
                    return words[:i] + "&" + words[i:]
            return words
        from .common import profile_colour
        menu.setStyleSheet("QMenu { icon-size: 10px; }")   # (its items' dots: the profiles' colours)
        above = menu.addAction(lettered(tr("As Above ({0})").format(self.above())))
        above.setIcon(style.dot_icon(profile_colour(self.above())))
        above.setIconVisibleInMenu(True)
        above.setCheckable(True)
        above.setChecked(bool(chosen) and mine == {""})
        above.triggered.connect(lambda _=False, c=chosen: self.set_album_profile(c, ""))
        menu.addSeparator()
        for name in profile_names():
            act = menu.addAction(lettered(name))
            act.setIcon(style.dot_icon(profile_colour(name)))
            act.setIconVisibleInMenu(True)
            act.setCheckable(True)
            act.setChecked(bool(chosen) and names == {name} and mine != {""})
            act.triggered.connect(lambda _=False, c=chosen, n=name: self.set_album_profile(c, n))
        on = bool(chosen) and (not self.running or (bool(self.looking) and   # (applied: filed again, after)
                                                    any(a.get("status") != "done" for a in chosen)))
        for a in menu.actions():
            a.setEnabled(on)
        menu.setProperty("checks", True)
        return menu

    def context_menu(self, pos, folders=False):
        """The albums' menu (Your files: for the albums its rows selected hold)."""
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu
        view = self.panes.folder_view if folders else self.table
        chosen = self.panes.folder_albums() if folders else self.selected_albums()
        if folders:
            self._profile_pane = "folders"
            items = []
        else:
            keys = ("@release", "use_suggested_pressing", "match_files", None, "file_info", "tags_from_filenames", None, "process_anyway",
                    "file_anyway", "exclude_again", None, "play_pause", None, "search_web", "open_folder",
                    "copy_path")
            # (the four ways to choose the release: the Album menu's Change Release submenu, as there)
            release = self.win.menus["Change Release"].menuAction() if "Change Release" in getattr(
                self.win, "menus", {}) else None
            items = [release if k == "@release" else self.win.act(k) if k else None
                     for k in keys if k != "@release" or release is not None]
        if chosen:                                     # (what Apply does with the selected rows)
            n = len(chosen)
            look = QMenu(tr("Look Up"), self)
            for mode, words in LOOK_UP:
                act = look.addAction(tr(words))
                act.triggered.connect(lambda _=False, m=mode, c=chosen: self.look_up(m, c))
            look.setEnabled(not self.running or bool(self.looking))     # (while one runs: queued)
            mine = [look.menuAction()]
            again = QAction(tr("Refresh with Current Settings"), self)   # (the profile as it is now)
            again.triggered.connect(lambda _=False, c=chosen: self.reload(c))
            again.setEnabled(not self.running or bool(self.looking))
            mine.append(again)
            # (the profile of the selected albums: theirs, or the one chosen above)
            if self.model.albums:
                pm = self.fill_profile_menu(QMenu(tr("Profile"), self), chosen)
                pm.setEnabled(any(a.isEnabled() for a in pm.actions()))
                self._profile_menu = pm
                mine.append(pm.menuAction())
            for text, slot, on in ((tr("Apply Selected Albums…") if n > 1 else tr("Apply This Album…"),
                                    lambda c=chosen: self.apply_selected(c),
                                    any(can_apply(a) for a in chosen) and self.may_apply_now()),
                                   (tr("Leave Out of Apply"), lambda c=chosen: self.model.set_left_out(c, True),
                                    any(can_apply(a) and not PlanModel.left_out(a) for a in chosen)),
                                   (tr("Include in Apply"), lambda c=chosen: self.model.set_left_out(c, False),
                                    any(PlanModel.left_out(a) for a in chosen)),
                                   (tr("Remove from List"), lambda c=chosen: self._take_out(c), not self.running)):
                act = QAction(text, self)
                act.setEnabled(on)
                act.triggered.connect(lambda _=False, s=slot: s())
                mine.append(act)
            pages = release_pages(chosen[0]) if n == 1 else []
            if pages:                                     # (the release looked up: its page)
                mine.append(None)
                for words, url in pages:
                    act = QAction(words, self)
                    act.triggered.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                    mine.append(act)
            items = items + ([None] if items else []) + mine
        if items:
            self._menu_items = items
            popup_menu(self, view, pos, items)

    def selected_albums(self) -> list:
        """The plan's own entries of the selected rows (a disc or track: its album), once each, in
        the list's order."""
        if not hasattr(self, "table"):
            return []
        tops = self.tree.top
        found = {}
        for idx in self.table.selectionModel().selectedRows():
            n = idx.internalPointer()
            while n is not None and n.parent is not tops:
                n = n.parent
            if n is not None and n.album is not None:
                found.setdefault(id(n.album), (n.row, n.album))
        return [a for _r, a in sorted(found.values(), key=lambda x: x[0])]

    # ---- the profile pop-up follows the selection ----------------------------------------------
    MIXED = "\x00mixed"                                 # (the pop-up's "Mixed" item: never a profile)

    def above(self) -> str:
        """The profile the preview uses: the one chosen above the list (not a selection's)."""
        return self._above or (self.profile.currentText() if not self._profile_follows else "")

    def profile_targets(self) -> list:
        """The albums the toolbar's Profile pop-up is for: the rows you selected in the pane you
        chose them in (Your files: the albums its folders and files hold); none - the preview."""
        if self._profile_pane == "folders" and self.panes.folder_view.selectionModel().hasSelection():
            return self.panes.folder_albums()
        if getattr(self, "_rows_chosen", False):
            return self.selected_albums()
        return []

    def show_profile_choice(self):
        """The Profile pop-up shows the selected albums' profile ("Mixed" when they differ), or with
        none selected the preview's."""
        box = self.profile
        targets = self.profile_targets() if self.model.albums else []
        box.blockSignals(True)
        try:
            i = box.findData(self.MIXED)
            names = {self.profile_of(a) for a in targets}
            if targets and len(names) > 1:
                if i < 0:
                    box.insertItem(0, tr("Mixed"), self.MIXED)
                    item = box.model().item(0) if hasattr(box.model(), "item") else None
                    if item is not None:
                        item.setEnabled(False)
                box.setCurrentIndex(0)
            else:
                if i >= 0:
                    box.removeItem(i)
                want = next(iter(names)) if targets else self._above
                if want and box.findText(want) >= 0:
                    box.setCurrentText(want)
        finally:
            box.blockSignals(False)
        self._profile_follows = bool(targets)
        n = len(targets)
        box.setToolTip(tr("The profile of the {0} selected: choose one to use it for them").format(
            plural(n, tr("album"), tr("albums"))) if n else tr("The profile the preview uses"))
        box.setAccessibleName(tr("Profile of the selected albums") if n else tr("Profile"))

    def profile_changed(self, text):
        """The pop-up's choice changed: the preview's profile, unless it shows a selection's."""
        if getattr(self, "_filling", False) or self._profile_follows or not text or \
                self.profile.currentData() == self.MIXED or text == self._above:
            return
        self._above = text
        self.remember_profile(text)
        self.reset_sources()
        self.reset_library()
        self.drop_asks_of_other_profiles()
        self.model.refresh_rows()

    def profile_picked(self, i):
        """A profile chosen in the pop-up while it shows the selection's: for every selected album
        (looked up again with it); the preview's own is As Above."""
        if not self._profile_follows or self.profile.itemData(i) == self.MIXED:
            return
        targets = self.profile_targets()
        name = self.profile.itemText(i)
        if targets and name:
            self.set_album_profile(targets, "" if name == self._above else name)
        self.show_profile_choice()

    def folders_chosen(self, *_):
        if self.panes._quiet:
            return
        self._profile_pane = "folders"
        self.show_profile_choice()
        self.win.update_album_actions()

    def leave_out(self, out):
        self.model.set_left_out(self.selected_albums(), out)

    def apply_selected(self, chosen=None):
        """Apply only the selected albums: the others are left out of this Apply (not of the list)."""
        chosen = [a for a in (self.selected_albums() if chosen is None else chosen) if can_apply(a)]
        if not chosen:
            return
        ids = {id(a) for a in chosen}
        self.model.set_left_out([a for a in self.model.albums if id(a) not in ids], True)
        self.model.set_left_out(chosen, False)
        if self.running and self.may_apply_now():
            # (an album the look-up brings in meanwhile is not among them: left out when it ends)
            self._apply_only = {self.album_key(a) for a in chosen}
        self.apply()

    def remove_selected(self):
        """Take the selected albums out of this preview (their folders and files are not touched)."""
        self._take_out(self.selected_albums())

    def clear_done(self):
        """Take every album marked Done out of the list (their folders and files are not touched)."""
        self._take_out([a for a in self.model.albums if a.get("status") == "done"])

    def _take_out(self, chosen):
        if not chosen:
            return
        plain = [a for a in chosen if planmod.plain_pending(a)]
        if plain and not self.running:
            # (EDT-2: your edits of them not saved - saved, discarded or kept, as on leaving)
            n = len(plain)
            ans = sheets.ask(self, tr("Save your edits of {0}?").format(plural(n, tr("album"), tr("albums"))),
                             tr("They are taken out of the list; your tag edits of them are lost if you don't "
                                "save them."), buttons=(tr("Cancel"), tr("Don't Save"), tr("Save")),
                             default=tr("Save"), offscreen=tr("Don't Save"))
            if ans == tr("Save"):
                self.save_tags(ask=False, only=plain, then=lambda: self._take_out(chosen))
                return
            if ans != tr("Don't Save"):
                return
        if self.running:
            # CLR-1: while work runs, only albums it has done with (Done) come out: nothing it
            # still has in hand (Apply follows its albums themselves, the look-up its keys)
            chosen = [a for a in chosen if a.get("status") == "done" and not self.album_busy(a)
                      and not any(a is b for b in self.applied_albums() if self.states.get(id(b)))]
            if not chosen:
                return
        insp = self.win.inspector
        for a in chosen:                                     # (edits of an album taken out: gone)
            if a.get("status") == planmod.LISTED and insp.edit_of(a) is not None:
                insp.forget_edits_of(a)
        ids = {id(a) for a in chosen}
        left = [a for a in self.model.albums if id(a) not in ids]
        if self.plan:
            self.plan["albums"] = left
            self.model.set_plan(self.plan)
        else:                                               # (all applied: the rows shown, no plan behind)
            self.model.set_plan({"albums": left})
        self.removed |= {os.path.realpath(a["source_dir"]) for a in chosen}    # (dropped again: listed again)
        self.summary.setText(tr("{0} taken out of this preview; nothing on disk changed.")
                             .format(plural(len(chosen), tr("album"), tr("albums"))))
        self.win.update_album_actions()

    def refresh_profiles(self):
        from .common import forget_profile_colours
        forget_profile_colours()                      # (a profile's colour may have changed)
        cur = self._above or self.starting_profile()
        self._filling = True                          # (filling the list is no choice of yours)
        try:
            self.profile.clear()
            from .common import profile_colour
            for name in profile_names():                # (each with its colour's dot)
                self.profile.addItem(style.dot_icon(profile_colour(name)), name)
            if cur and self.profile.findText(cur) >= 0:
                self.profile.setCurrentText(cur)
        finally:
            self._filling = False
        was, self._above = self._above, self.above()
        self.show_profile_choice()
        if was and was != self._above:                # (the profile above was removed or renamed)
            self.reset_sources()
            self.reset_library()
            self.model.refresh_rows()
        elif hasattr(self, "model"):
            self.model.refresh_rows()                 # (their colours, as the profiles say now)

    @staticmethod
    def starting_profile() -> str:
        """The profile the app starts with (Preferences › default profile): the one last chosen
        ("last"), the built-in one ("builtin"), or a profile named there."""
        try:
            import app_settings
            want = app_settings.get("default_profile") or "last"
            if want == "last":
                return app_settings.get("last_profile") or ""
            return "default" if want == "builtin" else str(want)
        except Exception:
            return ""

    def remember_profile(self, name):
        """The profile you chose: the one the app starts with next time (default_profile last)."""
        if getattr(self, "_filling", False) or not name:
            return
        try:
            import app_settings
            if app_settings.get("last_profile") != name:
                app_settings.set_value("last_profile", name)
        except Exception:
            pass

    def busy(self, on):
        """Preview and Apply are enabled by the window (on this page only); while work runs a
        folder dropped or chosen waits (add_later), and joins the list when the work ends."""
        if not on and getattr(self, "apply_worker", None) is not None:
            # APL-1: the look-up ended, the Apply beside it goes on: the work ends with it
            self._ended_meanwhile = True
            self._lookup_n = 0
            side = {id(a) for a in getattr(self, "_side", None) or []}
            self.states = {k: v for k, v in self.states.items() if k in side}
            self.model.refresh_rows()
            self.win.update_album_actions()
            return
        self.running = on
        ended = None
        if not on:
            ended, self._work_kind = self._work_kind, None
        self.show_ready()
        if hasattr(self, "model"):
            # (no profile chosen while work runs; during a look-up it is: that album is queued)
            self.model.locked = on and not self.looking
        if not on:
            self._lookup_n = 0                                # (the next run says its own progress)
            self._lookup_i, self._lookup_name = 0, ""
            self._applying = False
            if self.states:                                   # (done: each row says what it is)
                self.states = {}
                self.model.refresh_rows()
        if hasattr(self, "proxy") and self.proxy.dynamicSortFilter() != (not on):
            # (while work runs the rows keep their places: a row is not moved as its state changes.
            # Only when it changes: switched on, the proxy sorts every row again, and this is
            # called on each selection)
            self.proxy.setDynamicSortFilter(not on)
        self.choose_btn.setEnabled(not on)
        self.drop.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        self.win.show_progress(on)
        self.win.update_album_actions()
        if not on and getattr(self, "later", None):
            # (after whatever ended has finished: a look-up queued meanwhile goes first)
            QTimer.singleShot(0, self.add_waiting)
        if not on and ended in ("refile", "adding") and self.queue:
            # (albums dropped meanwhile, queued to be looked up: looked up now, unless what ended
            # started a look-up of its own - they wait for that one, as before)
            QTimer.singleShot(0, self.run_queue)

    # ---- folders dropped while work runs ---------------------------------------------------
    def add_later(self, paths) -> list:
        """Folders or audio files dropped (or chosen) while work runs: held, and their albums
        added to this list when the work ends; the albums here keep what was found for them.
        Returns the paths taken (not those already in the list or waiting)."""
        from . import manual_match
        if self.worker is not None and getattr(self.worker, "cancelled", False):
            self.win.statusBar().showMessage(tr("Cancelling; drop the folder again when it has stopped."), 5000)
            return []
        new, present = manual_match.new_paths(self, paths)
        held = {os.path.realpath(str(p)) for p in self.later + self._reading}
        new = [p for p in new if os.path.realpath(str(p)) not in held]
        kind = getattr(self, "_work_kind", None)
        # (EDT-2: Save Tags writes files in the list's folders - what is dropped meanwhile waits for it
        # and is added when it ends, never read while it writes)
        applying = getattr(self, "_applying", False) and kind != "save"
        side = getattr(self, "apply_worker", None) is not None      # (APL-1: an Apply beside the look-up)
        # (a look-up, an Apply not touching them, or folders being added - not overlapping those:
        # read now, put under the list, queued. A refile waits: its albums take their rows' places
        # when it ends, and a folder of them read meanwhile would be listed twice)
        if new and ((self.looking and not (side and self.touches_apply(new))) or
                    (applying and not self.touches_apply(new)) or
                    (kind == "adding" and not applying and not self.looking and
                     not self.overlaps(new, getattr(self, "_adding", ())))) and self.read_now(new):
            msg = (tr("{0}: added to the list; not looked up yet.") if self._later_list_only or _list_first()
                   else tr("{0}: added to the list; looked up when Apply ends.") if applying
                   else tr("{0}: added to the list; looked up next.")).format(manual_match._names(new))
            self.win.statusBar().showMessage(msg, 6000)
            return new
        self.later += new
        if new and kind in ("listing", "preview") and not applying and not self.looking and self.read_early(new):
            # (a listing or preview replaces the list when it ends: their rows shown now, under it;
            # they join the new list then, without being read again)
            msg = tr("{0}: shown in the list; added to it when the current work ends.").format(
                manual_match._names(new))
        elif new:
            msg = tr("{0}: added to the list when the current work ends.").format(manual_match._names(new))
        elif present:
            msg = tr("Already in the preview: {0}.").format(manual_match._names(present))
        else:
            msg = tr("Already waiting to be added.")
        self.win.statusBar().showMessage(msg, 6000)
        if new and self.looking:
            self.summary.setText(self.lookup_words(self._lookup_i, self._lookup_name))
        return new

    def add_to_list(self, paths):
        """Folders or audio files dropped or chosen with a list showing and nothing running: their
        albums join the list; the albums here keep what was found for them."""
        reading = {os.path.realpath(str(p)) for p in self._reading}    # (dropped twice: read once)
        self.later += [p for p in paths if p not in self.later and os.path.realpath(str(p)) not in reading]
        self.add_waiting()

    def add_waiting(self):
        """The work ended: the folders dropped meanwhile join the list under the albums there (each
        read from its own folder: self.roots) and are looked up with the sources chosen above.
        With no list left (all applied), the first is previewed as a drop. A look-up has already
        started (the one the folder was listed for): read now, and queued behind it."""
        if not self.later:
            return
        if self.running:
            if self.looking and self.plan and self.path:
                paths, self.later = list(self.later), []
                early = self.take_early(paths)
                if early is not None:                        # (read while the listing ran: joined now)
                    self.joined(early, paths)
                elif not self.read_now(paths):
                    self.later = paths + self.later
            return
        from . import manual_match
        paths, self.later = list(self.later), []
        early = self.take_early(paths)
        list_only, self._later_list_only = self._later_list_only or _list_first(), False
        new, _ = manual_match.new_paths(self, paths)
        if not new:
            return
        if not self.plan or not self.path:
            manual_match.add_dropped(self.win, new)
            return
        folders = dropped_folders(new)
        if early is not None:                                # (read while the work ran: not again)
            mine = set(folders)
            self.added([(f, a) for f, a in early if f in mine], folders, list_only)
            return
        try:
            prof = load_profile(self.above())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return

        self.busy(True)
        self._work_kind, self._adding = "adding", list(folders)
        self.summary.setText(tr("Adding {0}…").format(manual_match._names(new)))
        rows = Batches(self, self.put_under_list)            # (each album's row as soon as it is read)

        def work(w):
            return read_folders(prof, folders, on_album=lambda f, x: w.partial.emit((f, x)))

        def done(got):
            rows.close()
            self.added(got, folders, list_only, streamed=rows.shown)

        def failed(msg):
            rows.close()
            self.oops(msg)
        self.worker = start(self, work, done, self.progress, failed, partial=rows.add)

    # ---- folders dropped while a listing or a preview runs ------------------------------------
    def read_early(self, paths) -> bool:
        """Folders dropped while a listing or a preview runs (its list replaces the one shown when
        it ends): read now, on a thread of their own, their rows shown under the list as each
        album is read; when the work ends they join its list (add_waiting) without being read
        again, and are looked up as any folder dropped then. Nothing of them is in the work that
        runs. False when they cannot be read now (the profile cannot be loaded)."""
        try:
            prof = load_profile(self.above())
        except pf.ProfileError:
            return False
        folders = [f for f in dropped_folders(paths) if f not in self._early]
        if not folders:
            return True
        token = object()                                     # (this read's own: a later one of the
        for f in folders:                                    # same folder, after Cancel, is another)
            self._early[f] = (token, None)                   # (being read)
        rows = Batches(self, lambda items: self.show_early(items, token))

        def work(w):
            return read_folders(prof, folders, on_album=lambda f, x: w.partial.emit((f, x)))

        def done(got):
            rows.close()
            if all(self._early.get(f, (None, None)) == (token, None) for f in folders):
                for f in folders:
                    self._early[f] = (token, [(r, a) for r, a in got if r == f])
                self.show_early(got, token)
            # (else taken meanwhile - the work ended, or Cancel: read again, or not added)

        def failed(_msg):
            rows.close()
            for f in folders:                                # (read again when the work ends: said then)
                if self._early.get(f, (None, None))[0] is token:
                    self._early.pop(f)
        start(self, work, done, failed=failed, partial=rows.add)
        return True

    def show_early(self, got, token) -> list:
        """Rows for albums GOT ((folder, album) pairs) read early: under the rows shown, until the
        work ends (not in its plan). Returns the albums shown now."""
        if not self.running:
            return []
        have = {self.album_key(a) for a in self.model.albums}
        fresh = []
        for f, a in got:
            k = self.album_key(a)
            if self._early.get(f, (None, None))[0] is token and k not in have:   # (still this read's)
                have.add(k)
                fresh.append(a)
                self._provisional[id(a)] = (a, f)
        if fresh:
            if self.views.currentWidget() is not self.results:
                self.views.setCurrentWidget(self.results)
            self.model.replace_rows([], fresh)
        return fresh

    def take_early(self, paths):
        """The albums read early for PATHS ([(folder, album)]), None unless every folder of them
        was read whole; their rows meanwhile are taken out (put under the list again by whoever
        adds them). What was read for them is forgotten either way."""
        folders = dropped_folders(paths)
        got = [self._early.pop(f, (None, None))[1] for f in folders]
        self.drop_provisional(folders)
        if not folders or any(g is None for g in got):
            return None
        return [x for g in got for x in g]

    def drop_provisional(self, folders=None):
        """Rows shown for albums read early (of FOLDERS; None: all) taken out of the list."""
        if not self._provisional:
            return
        mine = {k: a for k, (a, f) in self._provisional.items() if folders is None or f in folders}
        rows = [r for r, a in enumerate(self.model.albums) if mine.get(id(a)) is a]
        for k in mine:
            self._provisional.pop(k, None)
        if rows:
            self.model.replace_rows(rows, [])

    @staticmethod
    def overlaps(paths, folders) -> bool:
        """A path of PATHS is, holds or sits inside one of FOLDERS."""
        def inside(a, b):
            return a == b or b.startswith(a.rstrip(os.sep) + os.sep)
        fs = [os.path.realpath(f) for f in folders]
        return any(inside(d, f) or inside(f, d) for d in (os.path.realpath(x) for x in dropped_folders(paths))
                   for f in fs)

    def touches_apply(self, paths) -> bool:
        """A folder dropped during Apply that is, holds or sits inside a folder Apply is working
        on (an album's own folder, or where its files go): read only once Apply has finished
        moving them, never while it does."""
        def inside(a, b):
            return a == b or b.startswith(a.rstrip(os.sep) + os.sep)
        busy = set()
        for a in self.applied_albums() + list(getattr(self, "_side", None) or []):
            if True:
                busy.add(os.path.realpath(a["source_dir"]))
                busy.update(os.path.realpath(os.path.dirname(fe["dst"])) for fe in a.get("files") or []
                            if isinstance(fe, dict) and fe.get("dst"))
        for p in paths:
            d = os.path.realpath(p if os.path.isdir(p) else os.path.dirname(p))
            if any(inside(d, b) or inside(b, d) for b in busy):
                return True
        return False

    def read_now(self, paths) -> bool:
        """Folders dropped while Apply runs: read now, on a thread of their own (Apply's worker
        and Cancel are left alone), and their albums put under the list as soon as they are
        found, to be looked up when Apply ends (queued). False when they cannot be (no list, or
        the profile cannot be loaded): then they wait for the end of the work, as before."""
        if not self.plan or not self.path:
            return False
        try:
            prof = load_profile(self.above())
        except pf.ProfileError:
            return False
        folders = dropped_folders(paths)
        self._reading += list(paths)
        # (each album's row as soon as it is read; queued to be looked up once all are)
        rows = Batches(self, lambda items: self.put_under_list(items) if self.plan and self.path else [])

        def work(w):
            return read_folders(prof, folders, on_album=lambda f, x: w.partial.emit((f, x)))

        def finish():
            rows.close()
            self._reading = [p for p in self._reading if p not in paths]

        def done(got):
            finish()
            self.joined(got, paths, streamed=rows.shown)

        def failed(msg):
            finish()
            self.win.statusBar().showMessage(tr("{0} could not be read: {1}").format(
                _dropped_names(paths), msg.split("\n\n")[0]), 8000)
        start(self, work, done, failed=failed, partial=rows.add)
        return True

    def joined(self, got, paths, streamed=()):
        """The albums of folders dropped during Apply, read: under the list at once, and queued to
        be looked up when Apply ends (unless your setting lists added folders first, or Apply
        was cancelled). Apply ended meanwhile: as any folder dropped then."""
        if not self.plan or not self.path:                   # (the list went: Apply applied every album)
            if self.running:
                self.later += [p for p in paths if p not in self.later]
            else:
                from . import manual_match
                manual_match.add_dropped(self.win, paths)
            return
        fresh = self.streamed_still_here(streamed) + self.put_under_list(got)
        if not fresh:
            self.win.statusBar().showMessage(tr("No music found in {0}.").format(_dropped_names(paths)), 6000)
            return
        if getattr(self, "_applying", False):
            self._joined = True                              # (Apply keeps the list for them)
        cancelled = self.worker is not None and getattr(self.worker, "cancelled", False)
        if self._later_list_only or _list_first() or cancelled:
            self.show_ready()
            return
        mode = self.sources.currentData() or "profile"
        if self.running:
            for a in fresh:
                self.queue.setdefault(self.album_key(a), (mode, False))
            self.model.refresh_rows()                        # (their rows: Waiting…)
        else:
            self.look_up(mode, fresh, queued=True)

    def streamed_still_here(self, albums) -> list:
        """Of ALBUMS put under the list as they were read, those it still has (the plan's own)."""
        if not albums or not self.plan:
            return []
        ids = {id(a) for a in self.plan.get("albums") or []}
        return [a for a in albums if id(a) in ids]

    def put_under_list(self, got) -> list:
        """The albums GOT ((folder read, album) pairs) that the list does not have yet: put under
        it, each read from its own folder (self.roots). Returns them."""
        if self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)
        have = {self.album_key(a) for a in self.plan["albums"]}
        fresh = []
        for root, a in got:
            k = self.album_key(a)
            if k not in have:
                have.add(k)
                self.roots[k] = root
                fresh.append(a)
        extra = len({os.path.realpath(r) for r in self.roots.values()})
        self.where.setText(tr("{0} (and {1} more)").format(self.path, plural(extra, tr("folder"), tr("folders"))))
        if fresh:
            self.model.replace_rows([], fresh)               # (fitted as rows come: request_fit)
            self.plan["listed"] = True
        return fresh

    def added(self, got, folders, list_only, streamed=()):
        """The dropped folders' albums, found: put under the albums already listed, then looked up
        (STREAMED: those of them put under the list already, as they were read)."""
        self.busy(False)
        if not self.plan:                                    # (the list went meanwhile)
            return
        fresh = self.streamed_still_here(streamed) + self.put_under_list(got)
        if not fresh:
            self.summary.setText(tr("No music found in the folders dropped."))
            return
        if list_only:
            self.keep_plain_edits(fresh)                     # (EDT-2: your edits of them, shown again)
            self.summary.setText(tr("{0} added, not looked up yet. Select them and press Look Up.").format(
                plural(len(fresh), tr("album"), tr("albums"))))
            self.show_ready()
            return
        self.look_up(self.sources.currentData() or "profile", fresh, queued=True)

    def actionable(self):
        return len(self.to_apply())

    def set_path(self, path, forced=None, within=None) -> bool:
        """Preview a folder; forced: the releases chosen for its albums (engine.Engine.force), as
        the run report's Apply This Release gives one. False when nothing ran."""
        if getattr(self, "running", False):                 # (a second worker would take over Cancel)
            self.win.statusBar().showMessage(tr("Finish or cancel the current work first."), 4000)
            return False
        if not self.may_drop_edits(path, then=lambda: self.set_path(path, forced, within)):
            return False
        if not (self.path and os.path.realpath(str(path)) == os.path.realpath(str(self.path))):
            self.file_anyway = {}                            # (MOV-5: asked for that folder's albums only)
        self.path = path
        self.forced = dict(forced or {})
        self.within = list(within or [])                     # (several folders dropped: only these)
        self.looked = {}                                     # (a new folder: nothing looked up yet)
        self.queue = {}                                      # (nor waiting to be)
        self.roots = {}
        self._early, self._provisional = {}, {}
        self.album_profiles, self.planned_with, self.headers = {}, {}, {}
        self.removed = set()                                 # (albums taken out of this list: their folders)
        self.plan_library = ""
        self.reset_sources()                                 # (a new folder: the profile's sources again)
        self.reset_library()                                 # (and the profile's library folder)
        self.where.setText(path)
        self.views.setCurrentWidget(self.results)
        if self.win.stack.currentWidget() is not self:
            self.win.go("Overview")
        self.win.place_inspector()                          # (something to inspect now)
        self.plan = None
        self.model.set_plan(None)
        self.show_ready()                                    # (the folder before's count: gone)
        self.detail.clear()
        self.busy(False)
        if self.forced:
            self.preview()
        else:                                                # (listed first, so more can join it at once)
            self.list_albums(look_up=not (_list_first() or self.within))
        return True

    # ---- List first: the albums shown, looked up when you choose ---------------------------
    def list_albums(self, look_up=False):
        """The folder's albums, not looked up yet: nothing is asked of any database. LOOK_UP:
        then looked up, an album at a time, in their rows."""
        try:
            prof = load_profile(self.above())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        path, within = self.path, list(getattr(self, "within", []) or [])
        # (each album's row as soon as its folder is read; the listing's own list at its end)
        rows = self._listing_rows = Batches(self, self.listed_some)

        def work(w):
            import engine
            return listing(engine.Engine(prof), path, within=within or None, meta=META_STORE, on_album=w.partial.emit)

        def failed(msg):
            self.end_listing_rows()
            if self.plan is None:                            # (no list: the rows shown so far go)
                self.model.set_plan(None)
            self.oops(msg)
        self.busy(True)
        self._work_kind = "listing"
        self.summary.setText(tr("Finding the albums… nothing is looked up yet."))
        self.worker = start(self, work, lambda plan: self.listed(plan, look_up), self.progress, failed,
                            partial=rows.add)

    def listed_some(self, albums) -> list:
        """Albums of the listing running, read: their rows now, under those shown (nothing is
        looked up yet; the listing's own list takes their place when it ends)."""
        if self.plan is not None or not self.running:
            return []
        have = {self.album_key(a) for a in self.model.albums}
        fresh = []
        for a in albums:
            k = self.album_key(a)
            if k not in have:
                have.add(k)
                fresh.append(a)
        if fresh:
            self.model.replace_rows([], fresh)
        return fresh

    def end_listing_rows(self):
        if self._listing_rows is not None:
            self._listing_rows.close()
            self._listing_rows = None

    def adopt_listing(self, plan):
        """PLAN's albums in the list. When the rows shown are the listing's own, as it sent them
        (the same albums in the same order, nothing else among them), its list takes their place
        row for row - the selection and the scrolling stay; else the list is made again."""
        new, old = plan["albums"], self.model.albums
        if old and old is not new and len(old) == len(new) and \
                all(self.album_key(a) == self.album_key(b) for a, b in zip(old, new)):
            self.model.adopt(new)
        else:
            self.model.set_plan(plan)

    def keep_plain_edits(self, albums):
        """EDT-2: albums listed again (the same folder added or listed once more) that your edits not
        saved are of: the edits applied to them again, so they show, Save Tags writes them and
        leaving asks - never kept out of sight."""
        insp = self.win.inspector
        if not insp.edits:
            return
        for a in albums:
            if a.get("status") != planmod.LISTED or os.path.realpath(a["source_dir"]) not in insp.edits:
                continue
            planmod.plain_files(a)
            if insp.edit_of(a) is not None and insp.can_edit(a):
                insp.plan = self.plan                       # (the plan the album is of now)
                insp.reapply(a)

    def listed(self, plan, look_up=False):
        self.end_listing_rows()
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.adopt_listing(plan)
        self.request_fit()
        self.busy(False)
        if not look_up:
            self.keep_plain_edits(plan["albums"])          # (EDT-2: once nothing runs - they can be edited)
        n = len(plan["albums"])
        if look_up and n:
            self.look_up(self.sources.currentData() or "profile", list(plan["albums"]), queued=True)
            return
        if look_up:
            self.summary.setText(tr("No music found in this folder."))
            return
        self.summary.setText(tr("{0} found, not looked up yet. Select the albums you want and press Look Up "
                                "(none selected: all of them), or edit their tags below and save them as they "
                                "are.").format(plural(n, tr("album"), tr("albums"))))
        if self.proxy.rowCount():
            self.show_row(0)

    def look_up_clicked(self):
        """Look Up: the selected albums, or all of them, with the sources chosen beside it."""
        mode = self.sources.currentData() or "profile"
        chosen = self.selected_albums() if self.table.selectionModel().hasSelection() and \
            len(self.selected_albums()) < len((self.plan or {}).get("albums") or []) else None
        if self.plan and chosen:
            self.look_up(mode, chosen)
        elif self.plan:
            self.look_up(mode, list(self.plan["albums"]))
        elif self.path:
            self.preview()

    def reset_sources(self):
        """Back to the profile's own sources: an override is for one look-up only."""
        if hasattr(self, "sources"):
            self.name_profile_sources()
            self.sources.setCurrentIndex(0)
            self.show_sources_state()

    def show_sources_state(self):
        """↺ beside Sources while the choice is not the profile's, and the link that keeps it."""
        if not hasattr(self, "sources"):
            return
        mode = self.sources.currentData() or "profile"
        other = mode != "profile"
        self.sources_label.setText(tr("Sources ↺") if other else tr("Sources"))
        tip = tr("For this look-up only; the profile uses {0}.").format(self.sources_words("profile")) if other else \
            tr("The profile's own sources. Choose another for one look-up; it goes back for the next folder.")
        self.sources_label.setToolTip(tip)
        self.sources.setToolTip(tip)
        self.keep_link.setVisible(other and mode in pf.SOURCES and self.above() not in ("", "default"))

    def keep_sources(self):
        """Save the chosen sources as the profile's own (after asking)."""
        mode = self.sources.currentData()
        name = self.above()
        if mode not in pf.SOURCES or not name or name == "default":
            return
        if not sheets.question(self, tr("Make {0} the sources of “{1}”?").format(self.sources_words(mode), name),
                               tr("Every look-up with this profile, and its watched folders, then use them."),
                               action=tr("Save to Profile")):
            return
        try:
            data = pf.rename_legacy(pf.raw(name))
            data["source"] = mode
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile was not changed."), str(exc))
            return
        self.reset_sources()
        if "Profiles" in getattr(self.win, "pages", {}):
            self.win.pages["Profiles"].reload()

    # ---- Library folder: where copied or moved albums go, for this preview and Apply ----------
    def covering_source(self, data):
        """The profile's source entry holding the folder previewed (the deepest), or None."""
        if not self.path:
            return None
        real = os.path.realpath(str(self.path))
        best = None
        for s in data.get("sources") or []:
            if not isinstance(s, dict) or not s.get("path"):
                continue
            r = os.path.realpath(os.path.expanduser(s["path"])).rstrip(os.sep)
            if (real == r or real.startswith(r + os.sep)) and (best is None or len(r) > len(best[0])):
                best = (r, s)
        return best[1] if best else None

    def profile_destination(self) -> str:
        """Where the profile files the folder previewed: the destination of the profile's folder
        holding it, if that has its own, else the profile's."""
        try:
            data = load_profile(self.above()).data
        except Exception:
            return ""
        src = self.covering_source(data)
        return (src or {}).get("destination") or data.get("destination") or ""

    def in_place(self, name=None) -> bool:
        try:
            return load_profile(name or self.above()).data.get("operation") == "in_place"
        except Exception:
            return False

    def planning_profile(self, mode="profile", name=None):
        """The profile Preview, Look Up and (through the plan) Apply use: the Sources choice and
        the library folder chosen here applied to the profile as saved (NAME: an album's own
        profile; none: the one chosen above)."""
        name = name or self.above()
        prof = load_profile(name)
        if self.library and not self.in_place(name):
            prof = library_for(prof, self.library)
        return profile_for(prof, mode)

    # ---- a profile per album ----------------------------------------------------------------
    @staticmethod
    def album_key(a) -> str:
        k = a.get("key")
        if k:                                               # (no import asked first: called per row, often)
            return k
        import engine
        return engine.group_key(a["source_dir"], a.get("album_files") or [])

    def profile_of(self, a) -> str:
        """The profile this album is looked up and applied with: its own, or the one above."""
        return (self.album_profiles and self.album_profiles.get(self.album_key(a))) or self.above()

    def own_profile(self, a) -> str:
        """The profile chosen in the album's own row ("": the one chosen above). (No album has
        one: not asked - an album's key resolves each of its files' paths.)"""
        return self.album_profiles.get(self.album_key(a), "") if self.album_profiles else ""

    def set_album_profile(self, albums, name):
        """Use profile NAME ("": the one chosen above) for ALBUMS; those already looked up are
        looked up again with it (their destinations and tags are that profile's)."""
        if self.running and not self.looking:              # (during a look-up: looked up after it)
            return
        if name and name not in profile_names():
            return
        # (the versions of one folder are applied together: they share their profile)
        ofs = {(a.get("split") or {}).get("of") for a in albums} - {None, ""}
        albums = list(albums) + [x for x in self.model.albums if (x.get("split") or {}).get("of") in ofs
                                 and not any(x is y for y in albums)]
        import engine
        for a in albums:                                   # (MOV-5: an ask was made under the profile it had)
            for f in engine.plan_entry_files(a):
                self.file_anyway.pop(os.path.realpath(f), None)
        again, done = [], []
        for a in albums:
            if a.get("status") == "done":                  # (applied already: filed again, from where it is)
                done.append(a)
                continue
            k = self.album_key(a)
            if name:
                self.album_profiles[k] = name
            else:
                self.album_profiles.pop(k, None)
            if (a.get("status") != "listed" or k in self.looking) and \
                    self.planned_with.get(k) != self.profile_of(a):
                again.append(a)
        self.model.refresh_rows()
        self.show_profile_choice()                          # (the pop-up says the profile they have now)
        if again:
            self.look_up(self.sources.currentData() or "profile", again)
        if done and not self.running:
            self.refile(done, name)
        elif done:
            self.win.statusBar().showMessage(tr("Albums already applied can take another profile when this "
                                                "look-up ends."), 6000)

    def refile(self, done, name):
        """Albums already applied, given another profile (NAME; "": the one chosen above): their
        rows become the albums where Apply put them, looked up there with NAME, so Apply files
        them as NAME does (a job of its own: Undo in Jobs). The tags written name the release
        found, so it is found again."""
        items = []
        for a in done:
            files = [fe["dst"] for fe in a.get("files") or [] if fe.get("dst") and os.path.isfile(fe["dst"])]
            if files:
                items.append((a, os.path.commonpath([os.path.dirname(f) for f in files]), files))
        if not items:
            self.win.statusBar().showMessage(tr("The applied files are no longer where Apply put them."), 6000)
            return
        try:
            prof = load_profile(name or self.above())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return

        def work(_w):
            import engine
            out = []
            for a, folder, files in items:
                want = {os.path.realpath(f) for f in files}
                found = [x for x in engine.Engine(prof).listing(folder, meta=META_STORE).get("albums") or []
                         if want & {os.path.realpath(f) for f in x.get("album_files") or []}]
                out.append((a, folder, found))
            return out
        self.busy(True)
        self._work_kind = "refile"
        self.summary.setText(tr("Reading {0} where Apply put them…").format(
            plural(len(items), tr("album"), tr("albums"))))
        self.worker = start(self, work, lambda got: self.refound(got, name), self.progress, self.oops)

    def refound(self, got, name):
        """The applied albums, found where they are now: in their rows' place, then looked up."""
        self.busy(False)
        if not self.plan:                                   # (all applied: the rows shown are the list)
            if not self.model.albums:
                return
            self.plan = {"job": "", "profile": self.above(), "operation": "",
                         "albums": self.model.albums, "listed": True, "provider_notes": {}}
        if self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)
        fresh, missing = [], 0
        for a, folder, found in got:
            row = next((i for i, x in enumerate(self.model.albums) if x is a), None)
            if row is None or not found:
                missing += row is not None
                continue
            # (an album the list has already - its folder dropped meanwhile - is not listed twice:
            # the row there stays, and is looked up with this profile)
            have = {self.album_key(x): x for x in self.model.albums if x is not a}
            listed = [have[self.album_key(x)] for x in found if self.album_key(x) in have]
            found = [x for x in found if self.album_key(x) not in have]
            for x in found + listed:
                k = self.album_key(x)
                if not any(x is y for y in listed):            # (a row there keeps the folder it is read from)
                    self.roots[k] = folder
                if name:
                    self.album_profiles[k] = name
                else:
                    self.album_profiles.pop(k, None)
            self.model.replace_rows([row], found)
            fresh += found + listed
        if missing:
            self.win.statusBar().showMessage(tr("{0} not found where Apply put them.").format(
                plural(missing, tr("album"), tr("albums"))), 6000)
        self.plan["listed"] = any(a.get("status") == "listed" for a in self.model.albums)
        self.request_fit()
        if fresh:
            self.look_up(self.sources.currentData() or "profile", fresh, again=True, queued=True)

    def with_versions(self, albums) -> list:
        """ALBUMS and the other versions of a folder split per version (looked up together)."""
        ofs = {(a.get("split") or {}).get("of") for a in albums} - {None, ""}
        return list(albums) + [x for x in (self.plan or {}).get("albums") or []
                               if (x.get("split") or {}).get("of") in ofs and not any(x is y for y in albums)]

    def plan_for(self, albums) -> dict:
        """The plan of ALBUMS as their profile's preview made it (its job, rules and folders)."""
        name = self.planned_with.get(self.album_key(albums[0])) if albums else None
        head = self.headers.get(name) or self.plan or {}
        # (a list made here - albums applied and filed again - has no job of its own yet)
        out = {"job": "", "profile": self.above(), "operation": ""}
        out.update(head)
        out["albums"] = list(albums)
        return out

    def stale_profiles(self, albums) -> list:
        """ALBUMS whose preview was made with another profile than the one now chosen for them."""
        return [a for a in albums if self.planned_with.get(self.album_key(a), self.profile_of(a)) != self.profile_of(a)]

    def reset_library(self):
        """Back to the profile's own library folder: a choice is for one preview only."""
        self.library = ""
        self.show_library_state()

    def show_library_state(self):
        if not hasattr(self, "library_label"):
            return
        name = self.above()
        mine = self.profile_destination()
        other = bool(self.library) and self.library != mine
        stays = self.in_place()
        self.library_label.setText(tr("Library folder ↺") if other else tr("Library folder"))
        shown = self.library or mine
        self.library_where.setText(shown or tr("None chosen"))
        if stays:
            tip = tr("This profile does not move files, so it has no library folder.")
        elif other:
            tip = tr("For this preview and Apply only; the profile uses {0}.").format(mine or tr("no folder"))
        else:
            tip = tr("The folder copied or moved albums go to: the profile's own. Choose another for this "
                     "preview and Apply; it goes back for the next folder.")
        for w in (self.library_label, self.library_where, self.library_btn):
            w.setToolTip(tip)
            w.setEnabled(not stays)
        self.library_keep.setVisible(other and not stays and name not in ("", "default"))

    def choose_library(self):
        if self.in_place() or getattr(self, "running", False):
            return
        folder = sheets.choose_folder(self, tr("Library folder"), self.library or self.profile_destination())
        if not folder:
            return
        if not os.path.isdir(folder):
            sheets.warning(self, tr("That folder does not exist."), tr("Library folder kept: {0}").format(
                self.library or self.profile_destination() or tr("none")))
            return
        folder = os.path.abspath(folder)
        if folder == (self.library or self.profile_destination()):
            return
        self.library = "" if folder == self.profile_destination() else folder
        self.show_library_state()
        self.replan_after_library()

    def replan_after_library(self):
        """The albums already looked up were planned for the other folder: look them up again."""
        if not self.plan or not self.path:
            return
        if self.forced:                                    # (a release you chose: kept, so previewed again)
            self.preview()
            return
        again = [a for a in self.plan.get("albums") or [] if a.get("status") not in ("listed", "done")]
        if again:
            self.look_up(self.sources.currentData() or "profile", again, again=True)
            if self.running:
                return
        self.summary.setText(tr("Library folder changed: destinations change at the next Preview or Look Up."))

    def keep_library(self):
        """Save the chosen library folder as the profile's destination (after asking)."""
        name = self.above()
        if not self.library or not name or name == "default":
            return
        if not sheets.question(self, tr("Make {0} the library folder of “{1}”?").format(self.library, name),
                               tr("Every copy or move with this profile, and its watched folders, then go there."),
                               action=tr("Save to Profile")):
            return
        try:
            data = pf.rename_legacy(pf.raw(name))
            data["destination"] = self.library
            src = self.covering_source(data)
            if src is not None:                            # (its own folder would win over it)
                src.pop("destination", None)
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile was not changed."), str(exc))
            return
        if getattr(self, "plan_library", "") == self.library:
            # (the preview went to this folder, now the profile's own: it still stands for Apply)
            self.plan_library = ""
        self.reset_library()
        if "Profiles" in getattr(self.win, "pages", {}):
            self.win.pages["Profiles"].reload()

    def name_profile_sources(self):
        """The first choice of Look up with says what the profile's sources are."""
        if hasattr(self, "sources"):
            words = self.sources_words("profile")
            self.sources.setItemText(0, words[:1].upper() + words[1:])

    def sources_words(self, mode=None) -> str:
        mode = mode or self.sources.currentData() or "profile"
        if mode == "profile":
            from .profile_editor import SOURCE_WORDS
            try:
                src = load_profile(self.above())["source"]
            except Exception:
                src = ""
            return tr("{0} (profile)").format(tr(SOURCE_WORDS.get(src, src)) or "?")
        from .profile_editor import SOURCE_WORDS
        if mode in SOURCE_WORDS:
            return tr(SOURCE_WORDS[mode])
        return {"musicbrainz": tr("MusicBrainz only"), "discogs": tr("Discogs only"),
                "web": tr("the web only (store and encyclopaedia pages)")}.get(mode, mode)

    def lookup_stamp(self, prof, mode, name=None) -> str:
        """What a look-up depends on (the profile as it is now, and the sources): the same again
        finds the same, so it is not asked of the databases twice."""
        import json
        # (the profile's settings themselves: a changed setting is a new look-up)
        import companions
        settings = prof.to_dict() if hasattr(prof, "to_dict") else prof
        if isinstance(settings, dict) and "colour" in settings:     # (its rows' tint: not a look-up's)
            settings = {k: v for k, v in settings.items() if k != "colour"}
        # (and the built-in plugins' switches: Discogs or CoverUp switched on or off is a new look-up)
        return json.dumps([name or self.above(), mode, self.ocr.isChecked(), settings,
                           companions.discogs_on(), companions.coverup_on()], sort_keys=True, default=str)

    def stale_albums(self) -> list:
        """The albums looked up before their profile (or the sources, or reading scans) changed:
        Refresh looks them up again with the profile as it is now."""
        if not self.plan or not self.looked:
            return []
        mode = self.sources.currentData() or "profile"
        now, out = {}, []
        for a in self.plan.get("albums") or []:
            k = self.album_key(a)
            if k not in self.looked or a.get("status") == "done":
                continue
            name = self.profile_of(a)
            if name not in now:
                try:
                    now[name] = self.lookup_stamp(self.planning_profile(mode, name), mode, name)
                except pf.ProfileError:
                    now[name] = None
            if now[name] is not None and self.looked[k] != now[name]:
                out.append(a)
        return out

    def check_stale(self):
        """Say when albums were looked up with settings since changed, with Refresh for them."""
        if not hasattr(self, "stale_note"):
            return
        n = 0 if self.running else len(self.stale_albums())
        if n:
            self.stale_note.setText(tr("{0} looked up before the profile's settings changed. "
                                       '<a href="refresh">Refresh with the new settings</a>')
                                    .format(tr("1 album was") if n == 1 else tr("{0} albums were").format(n)))
        self.stale_note.setVisible(bool(n))

    def refresh_stale(self):
        """The albums looked up with settings since changed, looked up again with the new ones."""
        stale = self.stale_albums()
        if stale:
            self.look_up(self.sources.currentData() or "profile", stale, again=True)

    def reload(self, chosen=None):
        """Refresh (Ctrl+R): look the selected albums (none selected: all of them) up again, even
        those already looked up the same way (while a look-up runs: queued)."""
        if (self.running and not self.looking) or not self.plan:
            return
        if chosen is None:
            chosen = self.selected_albums() if self.table.selectionModel().hasSelection() else None
        self.look_up(self.sources.currentData() or "profile", chosen or list(self.plan["albums"]), again=True)

    def look_up(self, mode="profile", albums=None, again=False, queued=False):
        """Look up the selected albums (or ALBUMS) with the profile's sources, or only
        MusicBrainz, only Discogs, or only the web; what they find replaces their rows, each
        album's as soon as it is found. An album already looked up the same way is skipped
        unless AGAIN (Refresh). Each album is looked up with its own profile (its row's choice,
        else the one chosen above). Asked while a look-up runs: queued, and looked up when it
        ends (QUEUED: such a look-up, now started; it asks nothing)."""
        import engine
        chosen = albums if albums is not None else self.selected_albums()
        if not chosen or not self.path:
            return
        if self.running:
            if self.looking:                                # (another look-up: this one waits for it)
                self.enqueue(mode, chosen, again)
            return
        if self.plan is not None and self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)                  # (the rows are the plan's own: replaced in place)
        groups = {}                                         # profile name -> album keys, in the list's order
        for a in chosen:
            groups.setdefault(self.profile_of(a), {})[self.album_key(a)] = None
        profs, stamps = {}, {}
        for name in groups:
            try:
                profs[name] = self.planning_profile(mode, name)
            except pf.ProfileError as exc:
                sheets.warning(self, tr("This profile cannot be used."), str(exc))
                return
            stamps[name] = self.lookup_stamp(profs[name], mode, name)
        keys = {k for ks in groups.values() for k in ks}
        done = {k for name, ks in groups.items() for k in ks if self.looked.get(k) == stamps[name]} \
            if not again else set()
        if done and done == keys:
            if queued:                                      # (asked when it was queued: nothing to do)
                return
            n = len(keys)
            if not sheets.question(self, tr("{0} already looked up with {1}.").format(
                    tr("This album is") if n == 1 else tr("These {0} albums are").format(n),
                    self.sources_words(mode)),
                    tr("Looking up again asks the databases once more (Refresh, Ctrl+R, does the same)."),
                    action=tr("Look Up Again")):
                return
            done = set()
        groups = {name: [k for k in ks if k not in done] for name, ks in groups.items()}
        groups = {name: ks for name, ks in groups.items() if ks}
        skipped = len(done)
        path, ocr, edits = self.path, self.ocr.isChecked(), self.win.inspector.edits_for_preview()
        forced = dict(self.forced)                           # (the releases you chose: kept, as Preview)
        file_anyway = dict(self.file_anyway)                 # (MOV-5: the albums you asked to have filed)
        # MOV-4: the album folders the rows not looked up now claim, so an album planned alone
        # is not filed where another row of the list is
        looked_now = {k for ks in groups.values() for k in ks}
        # (never a row left out of Apply: it files nothing)
        seed = [c for a in self.model.albums if self.album_key(a) not in looked_now
                and a.get("status") in ("ready", "unchanged") and not PlanModel.left_out(a)
                for c in a.get("claims") or []]
        total = sum(len(ks) for ks in groups.values())

        roots = dict(getattr(self, "roots", {}))

        at = {k: i for i, k in enumerate(k for ks in groups.values() for k in ks)}    # (its row: _lookup_order)
        every = frozenset(self.album_key(a) for a in self.model.albums)
        unsent = []                                         # (albums an internal error left not looked up)
        self._lookup_unsent = unsent

        def work(w):
            batches = []
            for name, ks in groups.items():
                by_root = {}                                # (the folder each album is read from)
                for k in ks:
                    by_root.setdefault(roots.get(k) or path, []).append(k)
                batches += [(name, where, kr) for where, kr in by_root.items()]

            def one_batch(batch, report, workers):
                name, where, kr = batch
                e = engine.engine_for_thread(profs[name], ocr=True if ocr else None)
                e.edits = edits
                e.force = forced
                e.file_anyway = {f for f, n in file_anyway.items() if n == name}
                e.seed_claims = seed
                e.listed_keys = every                       # (every row's: a changed album never takes another's)
                e.offer_file_anyway = True                  # (MOV-5: here you can ask)
                e.lookup_workers = workers
                told = set()

                def emit(k, plan):
                    told.add(k)
                    report("partial", (name, k, plan))
                # (each album's rows sent as soon as it is found; cancelled: the others stay as they were)
                try:
                    return plan_each(e, where, kr, emit,
                                     progress=lambda i, what: report("progress", (at[kr[i]], total, what)),
                                     cancel=lambda: w.cancelled, meta=META_STORE)
                except Exception as exc:
                    # (an internal error the engine did not keep to one album: logged, and the
                    # look-up goes on with the other folders' albums; the albums of this one it
                    # had not sent keep their rows, not looked up - never another album's error)
                    if not w.cancelled:
                        import logging
                        logging.getLogger("tagmender.gui").error(
                            "looking up the albums in %s stopped: %s: %s", where, type(exc).__name__, exc,
                            exc_info=exc)
                        unsent.extend(k for k in kr if k not in told)
                    return len(told)
            return run_batches(batches, one_batch, lambda: w.cancelled, w.partial.emit,
                               lambda a: w.progress.emit(*a))
        self.looking = {k: (name, mode) for name, ks in groups.items() for k in ks}
        self._lookup_again = again                         # (Refresh: so is what Apply interrupts)
        self._lookup_order = [k for ks in groups.values() for k in ks]
        self.states = dict.fromkeys(self._lookup_order, "waiting")
        self.model.refresh_rows()
        self.busy(True)
        self._work_kind = "lookup"
        self.looked_with = self.sources_words(mode)
        self._lookup_n = total
        self._lookup_skipped = skipped
        self.summary.setText(self.lookup_words(0, ""))
        library = self.library

        def one(part):
            self.plan_library = library
            self.looked_up_one(part[0], part[1], part[2], stamps[part[0]])
        self.worker = start(self, work, lambda _n: self.look_up_ended(), self.progress, self.lookup_failed,
                            partial=one)

    def enqueue(self, mode, albums, again=False):
        """Look Up asked while a look-up runs: the albums wait for it, with these sources (an
        album it is looking up the same way, or already waiting, is not asked twice). After
        Cancel, until the cancelled look-up ends, nothing is queued: Cancel stops it all."""
        if self.worker is not None and getattr(self.worker, "cancelled", False):
            self.win.statusBar().showMessage(tr("Cancelling; Look Up again when it has stopped."), 4000)
            return 0
        n = 0
        for a in albums:
            k = self.album_key(a)
            if self.looking.get(k) == (self.profile_of(a), mode) and k not in self.queue:
                continue
            was = self.queue.pop(k, None)
            self.queue[k] = (mode, again or bool(was and was[1]))
            n += was is None
        if n:
            self.model.refresh_rows()                       # (their rows: Waiting…)
            self.summary.setText(self.lookup_words(self._lookup_i, self._lookup_name))
            self.win.statusBar().showMessage(tr("{0} queued: looked up when this look-up ends.").format(
                plural(n, tr("album"), tr("albums"))), 5000)

    def run_queue(self) -> bool:
        """The look-ups queued while the last one ran: the albums with the same sources at once.
        False when nothing more runs."""
        while self.queue and not self.running:
            mode, again = next(iter(self.queue.values()))
            keys = [k for k, v in self.queue.items() if v == (mode, again)]
            for k in keys:
                self.queue.pop(k)
            albums = [a for a in (self.plan or {}).get("albums") or [] if self.album_key(a) in keys]
            seen, chosen = set(), []
            for a in albums:                                 # (an album planned as several rows: once)
                if self.album_key(a) not in seen:
                    seen.add(self.album_key(a))
                    chosen.append(a)
            if chosen:
                self.look_up(mode, chosen, again, queued=True)
        return self.running

    def lookup_words(self, i, name) -> str:
        """The line under the list while albums are looked up: which one of how many."""
        n = self._lookup_n
        head = tr("Looking up album {0} of {n} with {w}").format(i + 1, n=n, w=self.looked_with) if name else \
            tr("Looking up {0} with {1}").format(plural(n, tr("album"), tr("albums")), self.looked_with)
        tail = tr(": {0}").format(name) if name else ""
        skipped = getattr(self, "_lookup_skipped", 0)
        note = tr(" ({0} already looked up, skipped)").format(skipped) if skipped else ""
        queued = len(getattr(self, "queue", ()))
        then = tr(" Then {0} queued.").format(plural(queued, tr("album"), tr("albums"))) if queued else ""
        later = len(getattr(self, "later", ()))
        if later:
            then += tr(" Then the {0} dropped.").format(plural(later, tr("folder"), tr("folders")))
        return head + tail + "…" + note + then + tr(" Nothing is changed while previewing.")

    def remember_looked(self, albums, stamp):
        """The albums found, as looked up with STAMP (cancelled: only those it reached); not one
        that failed or a database was busy or not answering for: that one is worth asking again."""
        import engine
        if stamp is None:
            return
        import reasons
        for a in albums:
            # (a plan entry keeps them in its identification: the reason with its codes in front)
            ident = a.get("identification") or {}
            codes = set(a.get("reason_codes") or ident.get("reason_codes") or [])
            words = reasons.parse(ident["reason"])[1] if ident.get("reason") else a.get("reason", "")
            if a.get("status") == "listed" or codes & RETRY_CODES or str(words).startswith("error:") or \
                    str(a.get("reason", "")).startswith("error:"):
                continue
            self.looked[a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or [])] = stamp

    def looked_up_one(self, name, key, new, stamp=None):
        """Album KEY looked up with profile NAME (NEW: its plan, its rows): they take the
        places of the album's rows at once; the other rows stay as they were."""
        had = (self.headers.get(name) or {}).get("roots") or []     # (albums read from other folders: theirs too)
        self.headers[name] = {k: v for k, v in new.items() if k != "albums"}
        if had or new.get("roots"):
            self.headers[name]["roots"] = list(dict.fromkeys(list(had) + list(new.get("roots") or [])))
        self.states.pop(key, None)
        found = list(new["albums"])
        keys = {key} | {self.album_key(a) for a in found}
        side = getattr(self, "_side", None) or []
        if any(self.album_key(a) in keys and (a.get("status") == "done" or any(a is b for b in side))
               for a in self.model.albums):
            # APL-1: an album applied beside the look-up (or applied already) keeps its row: a
            # look-up's word on it is from before Apply wrote it
            return
        gone = getattr(self, "removed", set()) or set()
        if found and not any(self.album_key(a) in keys for a in self.model.albums) and \
                all(os.path.realpath(a["source_dir"]) in gone for a in found):
            return                                        # (CLR-1: taken out meanwhile - Clear Done)
        for a in found:
            self.planned_with[self.album_key(a)] = name
        self.remember_looked(found, stamp)
        if self.plan is None:
            self.plan = dict(self.headers[name], albums=self.model.albums)
        head = self.headers.get(self.above()) or self.headers[name]
        self.plan.update(head, albums=self.model.albums)
        was = self.selected()
        rows = [r for r, a in enumerate(self.model.albums) if self.album_key(a) in keys]
        out = {self.album_key(self.model.albums[r]) for r in rows if self.model.albums[r].get(PlanModel.LEAVE_OUT)}
        for a in found:
            if self.album_key(a) in out:
                a[PlanModel.LEAVE_OUT] = True
        mine = was is not None and any(was is self.model.albums[r] for r in rows)
        at = self.model.replace_rows(rows, found)
        self.plan["listed"] = any(a["status"] == "listed" for a in self.model.albums)
        if mine:                                          # (the album shown: shown as found now)
            if found and not self.table.selectionModel().hasSelection():
                self.show_row(self.proxy.mapFromSource(self.model.index(at, 0)).row())
            self.show_detail()
        self.win.update_album_actions()

    def look_up_ended(self):
        """The look-up ended (done or cancelled): the counts, then the look-ups queued meanwhile
        (stopped for Apply: the albums it did not reach wait, and Apply asks first)."""
        apply_next, self._apply_next = getattr(self, "_apply_next", False), False
        only, self._apply_only = getattr(self, "_apply_only", None), None
        if apply_next:
            left = [k for k in getattr(self, "_lookup_order", []) if k in self.states and k in self.looking]
            again = bool(getattr(self, "_lookup_again", False))
            ahead = dict(self.queue)
            self.queue = {k: (self.looking[k][1], again) for k in left}
            for k, (mode, ag) in ahead.items():          # (asked meanwhile: theirs wins, Refresh kept)
                was = self.queue.pop(k, None)
                self.queue[k] = (mode, ag or bool(was and was[1]))
        self.looking = {}
        unsent, self._lookup_unsent = getattr(self, "_lookup_unsent", None) or [], []
        if unsent:
            self.win.statusBar().showMessage(tr("{0} could not be looked up: internal error (see log).").format(
                plural(len(set(unsent)), tr("album"), tr("albums"))), 10000)
        self.request_fit()                                 # (the rows' words now: fitted once)
        self.check_stale()
        if getattr(self, "apply_worker", None) is None:
            self.sort_by_status()
        # (APL-1: an Apply beside it goes on - the rows stay put, not sorted: sorted once it ends)
        self.show_counts()
        self.refresh_overrides()
        self.busy(False)
        self.service_status(force=True)
        if apply_next:
            self.model.refresh_rows()                     # (the albums not reached: Waiting…)
            if only is not None:                           # (Apply Selected: only the albums chosen)
                self.model.set_left_out([a for a in self.model.albums if self.album_key(a) not in only], True)
            if self.actionable():
                self.apply()
            if not self.running:                           # (Apply not asked after all: on with the look-up)
                self.run_queue()
            return
        if self.run_queue():                               # (the next one says what it does)
            return
        self.measure_copies()
        notify(self.win, "finished", tr("Preview finished"), counts_by_label((self.plan or {}).get("albums") or []))
        if self.proxy.rowCount() and not self.table.selectionModel().hasSelection():
            self.show_row(0)
        self.win.refresh_attention_badge(self.path)

    def measure_copies(self):
        """VIN-6: two or more copies of one release among the albums looked up (or one filed
        before): each measured - dynamic range, clipping - on a worker of its own, after the
        look-up (never in its way), then shown with each copy's row (its tooltip, Why This
        Release). Nothing is chosen or changed by it."""
        plan = self.plan
        if not plan or (plan.get("profile_data") or {}).get("compare_copies") is False:
            return                                         # (the profile's compare_copies off)
        if getattr(self, "_copies_worker", None) is not None:
            self._copies_again = True                      # (another plan meanwhile: measured after)
            return
        albums = list(plan.get("albums") or [])
        # (what the worker reads, copied here: the albums are the UI thread's)
        snap = {"albums": [{"identification": dict(a.get("identification") or {}), "status": a.get("status"),
                            "source_dir": a.get("source_dir"),
                            "files": [{"src": fe.get("src"), "dst": fe.get("dst"),
                                       "discnumber": (fe.get("changes") or {}).get("discnumber") or next(
                                           (r.get("after") for r in fe.get("diff") or ()
                                            if r.get("field") == "discnumber"), None)}
                                      for fe in a.get("files") or ()]}
                           for a in albums]}

        def work(_w):
            import dynamics
            if not dynamics.copies(snap, filed="cached"):
                return {}
            return dynamics.measure(snap, cancel=lambda: self.plan is not plan, filed="cached")

        def done(found):
            self._copies_worker = None
            if self.plan is not plan or getattr(self, "_copies_again", False):
                self._copies_again = False
                self.measure_copies()                      # (the plan shown now)
                return
            if not found:
                return
            for i, rows in found.items():
                if 0 <= i < len(albums):
                    albums[i]["copies"] = rows
            self.model.refresh_rows()
            shown = getattr(self.panes, "scope", None)
            if shown and shown[0] is not None and shown[0].get("copies"):
                self.panes.show_why(shown[0])

        def failed(_msg):
            self._copies_worker = None                     # (a measure is only shown: nothing to tell)
            if getattr(self, "_copies_again", False):
                self._copies_again = False
                self.measure_copies()
        self._copies_worker = start(self, work, done, failed=failed)

    def lookup_failed(self, msg):
        self._apply_next, self._apply_only = False, None
        self.looking, self.queue = {}, {}                  # (nothing queued starts after a failure)
        self._later_list_only = bool(self.later)           # (folders dropped meanwhile: listed, not looked up)
        self.oops(msg)

    def preview_release(self, folder, ref, files=None, only=False) -> bool:
        """Preview the album in `folder` with the release `ref` (a MusicBrainz ID or
        "discogs:123"); files: the album's own files, for one album of a folder holding several;
        only: those files alone (one version of a folder holding two), the rest left out.
        Only the preview: nothing is written until Apply, which asks first."""
        import engine
        if not folder or engine.parse_release_ref(ref)[0] is None:
            return False
        key = os.path.realpath(str(folder))
        choice = [dict({"ref": ref, "files": list(files)}, **({"only": True} if only else {}))] if files else ref
        mine = {os.path.realpath(str(f)) for f in files or ()}
        listed = [a for a in (self.plan or {}).get("albums") or []
                  if os.path.realpath(a["source_dir"]) == key
                  and (not mine or mine & {os.path.realpath(f) for f in engine.plan_entry_files(a)})]
        if listed and self.path:
            # (the album is in the list already: it alone is looked up again, the list stays)
            self.forced[key] = choice
            if self.win.stack.currentWidget() is not self:
                self.win.go("Overview")
            self.look_up(self.sources.currentData() or "profile", self.with_versions(listed), again=True)
        elif not self.set_path(str(folder), {key: choice}):
            return False
        self.win.statusBar().showMessage(tr("Previewing {name} with the release you chose; nothing is written "
                                            "until you apply.").format(name=Path(str(folder)).name), 10000)
        return True

    def may_drop_edits(self, path, then=None) -> bool:
        """Another folder is previewed: your edits of this one are dropped, after asking (they
        are of the preview only; nothing was written). The same folder again keeps them. Plain
        edits of albums not looked up (EDT-2) are offered to be saved first (THEN: after it)."""
        insp = self.win.inspector
        same = bool(self.path) and os.path.realpath(str(path)) == os.path.realpath(str(self.path))
        if same:
            return True
        if self.plain_albums():
            return self.may_drop_plain(then)
        if insp.edits:
            n = len(insp.edits)
            where = Path(str(self.path)).name or str(self.path) if self.path else tr("this preview")
            if not sheets.confirm(self, tr("Discard your edits of {0}?").format(plural(n, tr("album"), tr("albums"))),
                                  tr("Your tag and cover edits of {where} are not kept when you preview another "
                                     "folder; nothing was written.").format(where=where),
                                  tr("Cancel keeps them: apply this preview first to write them."), tr("Discard Edits"),
                                  destructive=True):
                return False
        insp.forget_edits()                                 # (your edits were of the other folder)
        return True

    def service_status(self, force=False):
        """Say under the folder when MusicBrainz, Discogs, AcoustID or the Cover Art Archive is
        busy or not answering this run's requests (asked at most every 10 seconds)."""
        import time
        import connection
        if not force and time.monotonic() - self._service_checked < 10:
            return
        self._service_checked = time.monotonic()
        st = connection.status(ex.STATE_DIR / "cache.sqlite")
        lines = []
        for svc, how in sorted(st.items()):
            name = connection.TITLES.get(svc, svc)
            if how == "busy":
                lines.append(tr("{0} is busy: TagMender waits and tries again, so this takes longer.").format(name))
            elif svc == "coverartarchive":            # (it proves no album: only its covers wait)
                lines.append(tr("{0} is not answering: covers from it are skipped for now.").format(name))
            elif svc == "acoustid":
                lines.append(tr("{0} is not answering: fingerprints are not looked up for now.").format(name))
            else:
                lines.append(tr("{0} is not answering: albums only it can prove are left to try again later.")
                             .format(name))
        if lines:                                     # (a symbol with the words, never colour alone)
            self.service_note.setText("⚠ " + "  ".join(lines))
        self.service_note.setVisible(bool(lines))
        self.service_note.setAccessibleDescription(" ".join(lines))
        badge = getattr(self.win, "service_badge", None)
        if badge is not None:                         # (the same, short, on every page)
            short = [tr("{0} busy").format(connection.TITLES.get(s, s)) if how == "busy" else
                     tr("{0} not answering").format(connection.TITLES.get(s, s)) for s, how in sorted(st.items())]
            badge.setText("⚠ " + ", ".join(short) if short else "")
            badge.setToolTip(" ".join(lines))
            badge.setAccessibleDescription(" ".join(lines))
            badge.setVisible(bool(short))

    def backing_off(self, provider, seconds):
        """A look-up waits SECONDS before asking PROVIDER again: said in the status bar, counting
        down, until it asks again or the work ends."""
        if not getattr(self, "running", False) or seconds <= 0:
            return
        self._waiting = (provider, time.monotonic() + seconds)
        self.show_wait()
        self._wait_timer.start()

    def wait_words(self) -> str:
        """"Waiting for MusicBrainz (busy, retrying in 12 s)", or "" when no wait runs."""
        if not self._waiting or not getattr(self, "running", False):
            return ""
        provider, until = self._waiting
        left = until - time.monotonic()
        if left <= 0:
            return ""
        name = WAIT_TITLES.get(provider.lower().replace(" back-off", ""), provider)
        return tr("Waiting for {0} (busy, retrying in {1} s)").format(tr(name), max(1, round(left)))

    def show_wait(self):
        words = self.wait_words()
        bar = self.win.statusBar()
        if words:
            bar.showMessage(words, 1500)                   # (said again each second while it lasts)
            self._wait_said = words
            return
        if getattr(self, "_wait_said", "") and bar.currentMessage() == self._wait_said:
            bar.clearMessage()                             # (over: not left saying it waits)
        self._waiting, self._wait_said = None, ""
        self._wait_timer.stop()

    def progress(self, i, n, what):
        if getattr(self, "_lookup_n", 0) and self.running and not self._applying:
            # (several albums at once: i says which row is being looked up, the bar how many have started)
            order = self._lookup_order
            if i < len(order) and self.states.get(order[i]) == "waiting":
                self.states[order[i]] = "looking"
                key = order[i]
                self.refresh_rows_of(lambda a: self.album_key(a) == key)    # (its row only: Looking up…)
            i = max(0, sum(1 for k in order if self.states.get(k) != "waiting") - 1)
        self.service_status()
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i + 1)
        self.win.show_count(tr("{0} of {n}").format(i + 1, n=n))     # (beside the bar: it is too thin for words)
        words = tr("{0} of {n}: {name}").format(i + 1, n=n, name=Path(what).name)
        self.bar.setToolTip(words)
        self.bar.setAccessibleDescription(words)
        if self._applying and self.running:                 # (Apply: which album; file_progress: which file)
            self.applying_row(i)
            self.apply_bar.album_started(i)
            self._apply_at = (i, n, Path(what).name)
            self.summary.setText(tr("Applying album {0} of {n}: {name}…").format(i + 1, n=n, name=Path(what).name))
        elif getattr(self, "_lookup_n", 0) and self.running:  # (Look Up: which album, under the list)
            self._lookup_i, self._lookup_name = i, Path(what).name
            self.summary.setText(self.lookup_words(i, Path(what).name))
        elif self.running:
            self.summary.setText(tr("Identifying album {0} of {n}: {name}… nothing is changed while previewing.")
                                 .format(i + 1, n=n, name=Path(what).name))

    def apply_news(self, news):
        """What Apply's worker tells as it goes: ("file", album, j, n, path), the next file of an
        album; ("album", album, result), an album finished."""
        if news and news[0] == "album":
            self.album_applied(*news[1:])
        elif news and news[0] == "file":
            self.file_progress(*news[1:])

    def applied_albums(self) -> list:
        """The albums Apply was given, in the executor's order: the album entries themselves, so
        rows taken out meanwhile (Clear Done) never shift them."""
        mine = getattr(self, "_applied", None)
        if isinstance(mine, list):
            return list(mine)
        rows = getattr(self, "_applied_rows", None)                 # (an older caller: row numbers)
        rows = rows if isinstance(rows, list) else []
        albums = (self.plan or {}).get("albums") or []
        return [albums[i] for i in rows if 0 <= i < len(albums)]

    def album_applied(self, i, r):
        """Apply's album I (of the rows it was given) is finished: its row says so now (Done,
        Failed...), not once the whole job ends. applied() says it again, the same."""
        mine = self.applied_albums()
        if not (self._applying and 0 <= i < len(mine)):
            return
        self.mark_applied(mine[i], r)
        self.apply_bar.album_done(i)
        self.model.refresh_rows()

    def file_progress(self, i, j, n, path):
        """Apply: file J (0-based) of the N its album I has to write or move, next."""
        if not (self._applying and self.running):
            return
        self.applying_row(i)
        self.apply_bar.file(i, j, n)
        _i, total, name = getattr(self, "_apply_at", (i, 0, ""))
        words = tr("Applying album {0} of {n}: {name} — file {1} of {m}: {file}").format(
            i + 1, j + 1, n=total, name=name, m=n, file=Path(path).name)
        self.summary.setText(words)
        self.bar.setToolTip(words)
        self.bar.setAccessibleDescription(words)

    def applying_row(self, i):
        """Apply's album I (of the rows it was given) is the one being applied now."""
        mine = self.applied_albums()
        if not 0 <= i < len(mine):
            return
        me = id(mine[i])
        if self.states.get(me) != "applying":
            was = {k for k, v in self.states.items() if v == "applying"}
            self.states = {k: v for k, v in self.states.items() if v != "applying"}   # (the one before: done)
            self.states[me] = "applying"
            self.refresh_rows_of(lambda a: id(a) == me or id(a) in was or self.album_key(a) in was)

    def refresh_rows_of(self, pick):
        """The rows of the albums PICK takes told changed (their state shown: Looking up…,
        Applying…), not every row: each row told changed is asked again, and the albums pane's
        tree answers each row it is told of with calls into Python - with a look-up running on
        another thread, each a wait for Python's lock (a list of a hundred albums held the UI
        thread up for tens of milliseconds at each album started)."""
        m = self.model
        last = m.columnCount() - 1
        rows = [r for r, a in enumerate(m.albums) if pick(a)]
        if len(rows) > 50:
            m.refresh_rows()
            return
        for r in rows:
            m.dataChanged.emit(m.index(r, 0), m.index(r, last))

    def state_of(self, a) -> str:
        """What is being done with album A now (its Status column), or ""."""
        if not self.states and not self.queue:
            return ""
        k = self.states.get(id(a))
        if k is None:
            key = self.album_key(a)
            k = self.states.get(key) or ("waiting" if key in self.queue else None)
        return tr(ROW_STATES[k]) if k else ""

    def set_folders(self, folders) -> bool:
        """Several folders dropped at once: every album in them listed together, under the folder
        that holds them all; nothing is looked up until you choose Look Up or Preview."""
        folders = [os.path.abspath(str(f)) for f in folders]
        root = os.path.commonpath(folders) if len(folders) > 1 else folders[0]
        if root in folders and len(folders) > 1:            # (one folder inside another: its parent)
            root = os.path.dirname(root)
        if not self.set_path(root, within=folders):
            return False
        self.where.setText(tr("{0} ({1} folders)").format(root, len(folders)))
        return True

    def preview(self):
        if not self.path:
            return
        if (getattr(self, "within", None) or self.album_profiles or getattr(self, "roots", None)) and self.plan:
            # (several folders: just the albums listed; albums with a profile of their own: each with it)
            self.look_up(self.sources.currentData() or "profile", list(self.plan["albums"]))
            return
        mode = self.sources.currentData() or "profile"
        try:
            prof = self.planning_profile(mode)   # (Look up with: its choice)
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        self.looked_with = self.sources_words(mode)
        path, ocr, forced = self.path, self.ocr.isChecked(), dict(self.forced)
        file_anyway = {f for f, n in self.file_anyway.items() if n == self.above()}
        edits = self.win.inspector.edits_for_preview()     # (your edits from the inspector: kept)

        def work(w):
            import engine
            e = engine.Engine(prof, ocr=True if ocr else None)    # unticked: the profile's "ocr"
            e.force = forced
            e.edits = edits
            e.file_anyway = file_anyway
            e.offer_file_anyway = True                      # (MOV-5: here you can ask)
            return e.plan(path, progress=w.progress.emit, cancel=lambda: w.cancelled, meta=META_STORE)
        self.busy(True)
        self._work_kind = "preview"
        self.summary.setText(tr("Identifying with {0}… nothing is changed while previewing.").format(self.looked_with))
        stamp = None if forced else self.lookup_stamp(prof, mode)    # (a release you chose: not the same)

        library = self.library
        name = self.above()

        def done(plan):
            self.looked = {}
            self.album_profiles = {}                         # (every album previewed afresh: with the one above)
            self.headers = {name: {k: v for k, v in plan.items() if k != "albums"}}
            self.planned_with = {self.album_key(a): name for a in plan.get("albums") or []}
            self.plan_library = library
            self.remember_looked(plan.get("albums") or [], stamp)
            self.planned(plan)
        self.worker = start(self, work, done, self.progress, self.oops)

    def planned(self, plan):
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.set_sorted_plan(plan)
        self.show_counts()
        self.refresh_overrides()
        self.busy(False)
        self.service_status(force=True)
        notify(self.win, "finished", tr("Preview finished"), counts_by_label(plan["albums"]))
        self.request_fit()                                 # (fitted once it is shown, not before)
        self.show_first_soon(plan)
        self.win.refresh_attention_badge(self.path)

    def set_sorted_plan(self, plan):
        """PLAN in the list, sorted by Status, and the rows' order kept up to date from now on
        (as busy(False) would): sorted once. (The proxy sorts every row each time its sorting is
        asked for or switched on: a preview of thousands of albums was sorted three times.)"""
        p = self.proxy
        if p.sortColumn() != 0 or p.sortOrder() != Qt.AscendingOrder or not p.dynamicSortFilter():
            self.model.set_plan(None)                      # (sorted with no rows: costs nothing)
            p.setDynamicSortFilter(True)
            self.table.sortByColumn(self.tree.STATUS, Qt.AscendingOrder)
        self.model.set_plan(plan)

    def sort_by_status(self):
        """The rows sorted by Status, and kept so from now on (as busy(False) would): one sort,
        not one for the column and another as the order is switched back on."""
        p = self.proxy
        if not p.dynamicSortFilter():
            p.setDynamicSortFilter(True)                    # (sorts by the column it had)
        if p.sortColumn() != 0 or p.sortOrder() != Qt.AscendingOrder:
            self.table.sortByColumn(self.tree.STATUS, Qt.AscendingOrder)

    def show_first_soon(self, plan):
        """The first row shown in the inspector once the list is on screen (its own turn of the
        event loop: the list of a big preview appears first), unless a row was chosen meanwhile
        or the list changed."""
        def first():
            if self.plan is plan and self.proxy.rowCount() and not self.table.selectionModel().hasSelection():
                self.show_row(0)
        QTimer.singleShot(0, self, first)

    def show_counts(self):
        plan = self.plan or {"albums": []}
        notes = "; ".join(setup.other_notes(plan))
        said = setup.plan_notices(plan)
        self.setup_note.setText("<br>".join(setup.rich(t, i) for i, t in said))
        self.setup_note.setAccessibleName(" ".join(t for _i, t in said))
        self.setup_note.setVisible(bool(said))
        # (counted as the rows name them: an album shown "Ignored" is not "Excluded" here)
        said_with = getattr(self, "looked_with", "")
        self.summary.setText(tr("Nothing has been changed yet.") + (f" ({notes})" if notes else "") +
                             (tr(" Looked up with {0}.").format(said_with) if said_with else ""))
        self.show_ready()

    def show_ready(self):
        """The counts (the status bar's left end, the toolbar's segments) and how many albums Apply
        would carry out now (the toolbar's Apply button: the ticked ones, or of the rows you
        highlighted those)."""
        if not hasattr(self, "counts_line"):
            return
        self.show_counts_line()

    def edited(self, album):
        """Your edit in the inspector changed an album of the preview: its row, the counts and Apply."""
        if not self.plan or self.album_busy(album) or not any(a is album for a in self.plan["albums"]):
            return                                        # (EDT-1: an album the work has not in hand: now)
        for r, a in enumerate(self.model.albums):
            if a is album:
                self.model.dataChanged.emit(self.model.index(r, 0),
                                            self.model.index(r, self.model.columnCount() - 1))
        self.request_fit()
        self.show_counts()
        self.win.update_album_actions()

    def oops(self, msg):
        self.busy(False)
        self.summary.setText(tr("Stopped: {head}").format(head=(msg.splitlines() or [""])[0]))
        head, _, rest = msg.partition("\n")
        sheets.warning(self, tr("Stopped: {head}").format(head=head), rest.strip())

    def applicable(self) -> list:
        """The plan's albums with changes, not left out - worked out once per turn of the event
        loop, and again when a row changes (asked a dozen times as a row is chosen)."""
        memo = getattr(self, "_applicable", None)
        albums = (self.plan or {}).get("albums", [])
        if memo is not None and memo[0] is albums and memo[1] == len(albums):
            return list(memo[2])
        out = [a for a in albums if can_apply(a) and not PlanModel.left_out(a)]
        self._applicable = (albums, len(albums), out)
        QTimer.singleShot(0, self, self.forget_applicable)
        return list(out)

    def forget_applicable(self, *_):
        self._applicable = None
        self._counts_memo = None

    def to_apply(self):
        """The albums Apply carries out: those with changes, unless left out (unticked); with
        rows you highlighted, only those of them, even one (the others stay as they are: a row
        highlighted only to show it, show_row, chooses nothing; nor does one just unticked)."""
        albums = self.applicable()
        chosen = self.selected_albums() if hasattr(self, "table") and getattr(self, "_rows_chosen", False) else []
        if chosen and not all(PlanModel.left_out(a) for a in chosen):
            ids = {id(a) for a in chosen}
            albums = [a for a in albums if id(a) in ids]
        return albums

    def apply_words(self):
        """The Apply sheet: (headline, what changes, how to undo, button), from each album's own
        operations - an album that stays in its folder is not said to be moved."""
        from .inspector import where_files_go
        albums = self.to_apply()
        n = len(albums)
        ops = {fe["op"] for a in albums for fe in a.get("files") or [] if fe.get("op") != "none"}
        moved = {"move": tr("moved"), "copy": tr("copied")}
        how = tr(" and ").join(moved[o] for o in ("move", "copy") if o in ops)
        def outcome(a):
            w = where_files_go(a) or tr("Stays in its folder, tags only")
            return w[:1].lower() + w[1:]
        names = {self.profile_of(a) for a in albums}
        if len(names) > 1:                            # (several profiles: each album says its own)
            plain = outcome

            def outcome(a):
                return tr("{0} (profile {1})").format(plain(a), self.profile_of(a))
        outcomes = {outcome(a) for a in albums}
        stays = {w[:1].lower() + w[1:] for w in (tr("Stays in its folder, names unchanged"),
                                                  tr("Stays in its folder, tags only"))}
        if outcomes <= stays:                         # (the sentence above says it: the albums by name)
            what = tr("Their tags are written; each album stays in its own folder:")
            items = [html.escape(album_words(a["source_dir"])) for a in albums[:APPLY_LISTED]]
        else:
            what = tr("Their tags are written and their files {how} as listed:").format(how=how) if how else \
                tr("Their tags are written; each album stays in its own folder, as listed:")
            items = [f"{html.escape(album_words(a['source_dir']))}: {html.escape(outcome(a))}"
                     for a in albums[:APPLY_LISTED]]
        if n > APPLY_LISTED:
            items.append(html.escape(tr("and {0} more").format(n - APPLY_LISTED)))
        # (rich text: a wrapped line hangs under its words, not under the bullet)
        body = (f"<p>{html.escape(what)}</p><ul style='margin-left:-24px; margin-top:0'>"
                + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        mine = sum(1 for a in albums if a.get("edited") and not planmod.edit_empty(a["edited"]))
        if mine:                                      # (values you typed or a cover you chose, not the match's)
            body += tr("<p>Includes your own edits of {0}: tags or a cover you chose, marked with a pencil in the "
                       "inspector.</p>").format(plural(mine, tr("album"), tr("albums")))
        plain = self.plain_albums()
        if plain:                                     # (EDT-2: not applied; kept for Save Tags)
            body += "<p>" + html.escape(tr("Your edits of {0} not looked up are not applied: they stay, to save with "
                                           "File › Save Tags.").format(plural(len(plain), tr("album"),
                                                                              tr("albums")))) + "</p>"
        word = tr("Album") if n == 1 else tr("Albums")
        return (tr("Apply the preview to {n} {0}?").format(word.lower(), n=n), body,
                tr("Every change can be undone: Edit > Undo Last Job, or Jobs."), tr("Apply {n} {0}").format(word, n=n))

    def may_apply_now(self) -> bool:
        """Apply can be asked: nothing runs, or a look-up does (APL-1: alongside it, or it stops
        after its album first); not while an Apply runs alongside a look-up already."""
        if getattr(self, "apply_worker", None) is not None:
            return False
        if not self.running:
            return True
        w = self.worker
        return bool(self.looking) and not getattr(self, "_apply_next", False) and \
            not (w is not None and getattr(w, "cancelled", False))

    def apply_during_look_up(self) -> bool:
        """Apply asked while albums are looked up: the look-up stops after the album it is on;
        the albums not reached wait (queued), Apply asks as usual, then they are looked up."""
        if not self.may_apply_now() or not self.running:
            return False
        if self.apply_alongside():
            return True
        self._apply_next = True
        self.worker.cancelled = True
        self.summary.setText(tr("Stopping the look-up after the current album to apply; the albums not yet "
                                "looked up are looked up afterwards."))
        self.win.update_album_actions()
        return True

    def apply_plans(self, chosen):
        """The plans Apply runs for the albums CHOSEN: one job per profile they were previewed
        with, in order. Returns (plans, the albums in the order the executor gets them)."""
        groups = {}
        for a in chosen:
            groups.setdefault(self.planned_with.get(self.album_key(a)), []).append(a)
        plans = []
        for g in groups.values():
            plan = self.plan_for(g)
            plan["job"] = self.fresh_job(plan.get("job") or "")
            plans.append(plan)
        return plans, [a for g in groups.values() for a in g]

    def album_busy(self, a) -> bool:
        """EDT-1: the work running now has album A in hand, so an edit of it (a cover, a tag)
        waits: an Apply (the whole of it, or A's own beside a look-up), a preview of the folder
        (it replaces the list), or A's own look-up. Another album, a look-up running, can be
        edited: the look-up only replaces the rows it looks up."""
        if not self.running or a is None:
            return False
        if any(a is b for b in getattr(self, "_side", None) or []):
            return True
        if self._applying or not self.looking:
            return True
        return self.album_key(a) in self.still_looked_up() or \
            self.states.get(id(a)) in ("waiting", "looking", "applying")

    def still_looked_up(self) -> set:
        """The keys of the albums the look-up has still to do (waiting, or on it now), and those
        queued after it."""
        return {k for k in self.looking if self.states.get(k) in ("waiting", "looking")} | set(self.queue)

    def looked_up_dirs(self) -> list:
        """The folders of the albums being looked up, or queued to be."""
        keys = self.still_looked_up()
        return [os.path.realpath(a["source_dir"]) for a in self.model.albums if self.album_key(a) in keys]

    def apply_alongside(self) -> bool:
        """APL-1: Apply asked while albums are looked up: the albums ready are applied by a worker
        of their own while the look-up goes on (the network and the disks at work at once). Not
        when an album to apply is in a folder an album still looked up is in, or holds it (its
        scans and logs may move away under the look-up): then the look-up stops first. True
        when Apply was asked here (applied, or not confirmed)."""
        if getattr(self, "apply_worker", None) is not None or not self.running or not self.looking:
            return False
        busy = set(self.looked_up_dirs())
        keys = self.still_looked_up()
        chosen = self.to_apply()
        if not chosen or any(self.album_key(a) in keys for a in chosen):
            return False

        def near(d):
            return any(d == b or d.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(d.rstrip(os.sep) + os.sep)
                       for b in busy)
        if any(near(os.path.realpath(a["source_dir"])) for a in chosen):
            return False
        if getattr(self, "plan_library", "") != (self.library or "") or self.stale_profiles(chosen):
            return False                                  # (apply() says why, once the look-up stopped)
        if not sheets.confirm(self, *self.apply_words()) or self.edit_pending():
            return True
        keys = self.still_looked_up()
        chosen = [a for a in self.to_apply() if self.album_key(a) not in keys]
        if not chosen:
            return True
        busy = set(self.looked_up_dirs())                 # (a look-up started while you were asked)
        if any(near(os.path.realpath(a["source_dir"])) for a in chosen):
            self._apply_next = True                       # (then: stop it first, as before)
            if self.worker is not None:
                self.worker.cancelled = True
            self.summary.setText(tr("Stopping the look-up after the current album to apply; the albums not yet "
                                    "looked up are looked up afterwards."))
            self.win.update_album_actions()
            return True
        if not self.running:                              # (the look-up ended while you were asked)
            self.start_apply(chosen)
            return True
        if not self.looking or self.worker is None or getattr(self.worker, "cancelled", False):
            # (the look-up ended and other work started while you were asked: nothing applied)
            self.win.statusBar().showMessage(tr("Not applied: other work started meanwhile. Apply again when it "
                                                "ends."), 8000)
            return True
        self.release_files()
        plans, order = self.apply_plans(chosen)
        self._side = order
        offs, total = [], 0
        for p in plans:
            offs.append(total)
            total += len(p["albums"])

        def work(w):
            import executor
            at = iter(offs)

            def run(p):
                o = next(at)
                return executor.Executor(p, cancel=lambda: w.cancelled,
                                         progress=lambda i, _n, what: w.partial.emit(("start", o + i, total, what)),
                                         file_progress=lambda i, j, n, f: w.partial.emit(("file", o + i, j, n, f)),
                                         album_done=lambda i, r: w.partial.emit(("album", o + i, r))).run()
            return merge_results(plans, run, lambda: w.cancelled)
        for a in order:
            self.states[id(a)] = "waiting"
        self.model.refresh_rows()
        self.apply_bar.start(planned_files(order))
        self.apply_worker = start(self, work, self.applied_alongside, None, self.apply_alongside_failed,
                                  partial=self.alongside_news)
        self.win.update_album_actions()
        return True

    def alongside_news(self, news):
        """What the Apply beside a look-up tells as it goes (apply_news's, and which album starts)."""
        side = getattr(self, "_side", None) or []
        kind, i = (news[0], news[1]) if news else ("", -1)
        if not 0 <= i < len(side):
            return
        a = side[i]
        if kind == "start":
            for b in side:
                if self.states.get(id(b)) == "applying":
                    self.states.pop(id(b))               # (the one before: finished, said by "album")
            self.states[id(a)] = "applying"
            self.model.refresh_rows()
            self.apply_bar.album_started(i)
            self.win.statusBar().showMessage(tr("Applying album {0} of {n} while the look-up goes on: {name}")
                                             .format(i + 1, n=news[2], name=Path(news[3]).name))
        elif kind == "file":
            _k, _i, j, n, f = news
            self.apply_bar.file(i, j, n)
            self.win.statusBar().showMessage(tr("Applying album {0} of {n} while the look-up goes on: {name} — "
                                                "file {1} of {m}").format(i + 1, j + 1, n=len(side),
                                                                          name=Path(a["source_dir"]).name, m=n))
        elif kind == "album":
            self.mark_applied(a, news[2])
            self.apply_bar.album_done(i)
            self.model.refresh_rows()

    def mark_applied(self, a, r):
        """Album A as Apply left it (r: the executor's result for it)."""
        a["status"] = r.get("status") or a.get("status")
        if r.get("status") in ("failed", "conflict"):
            a["conflicts"] = [r.get("detail") or ""]
        if r.get("label") in ("Ignored", "Skipped"):
            a["label"] = r["label"]
        self.states.pop(id(a), None)

    def alongside_ended(self):
        """The Apply beside the look-up is over: its rows, and the end of the work (busy) when
        the look-up ended meanwhile."""
        side, self._side, self.apply_worker = getattr(self, "_side", None) or [], None, None
        for a in side:
            self.states.pop(id(a), None)
        self.model.refresh_rows()
        if getattr(self, "_ended_meanwhile", False):
            self._ended_meanwhile = False
            self.busy(False)
            if not self.running:
                self.sort_by_status()                     # (the look-up's rows, sorted now)
            self.service_status(force=True)
            self.run_queue()
        self.win.update_album_actions()

    def applied_alongside(self, res):
        side = list(getattr(self, "_side", None) or [])
        by_index = {r["index"]: r for r in res["albums"] if "index" in r}
        for i, a in enumerate(side):
            if i in by_index:
                self.mark_applied(a, by_index[i])
        self.end_bar(res)
        names = [n for n in dict.fromkeys(self.planned_with.get(Overview.album_key(x)) for x in side)
                 if isinstance(n, str) and n]
        rep = run_report(dict(self.plan or {}, albums=side, **({"profile": ", ".join(names)} if names else {})),
                         res) if side else None
        self.alongside_ended()
        words = (tr("Job {0}: ").format(res["job"]) if res.get("job") else "") + \
            (counts_text(res["counts"]) or tr("Nothing was changed"))
        self.win.statusBar().showMessage(words, 10000)
        notify(self.win, "finished", tr("Apply finished"), counts_text(res["counts"]))
        self.win.pages["Jobs"].reload()
        self.win.refresh_attention_badge(self.path)
        if rep is not None:
            self.win.show_run_report(rep)

    def end_bar(self, res):
        """The status bar's progress ends: as many albums applied, failed, or stopped (cancelled)."""
        counts = res.get("counts") or {}
        self.apply_bar.finish(counts.get("done", 0), sum(counts.get(k, 0) for k in ("failed", "conflict")),
                              stopped=bool(counts.get("cancelled")))

    def apply_alongside_failed(self, msg):
        self.apply_bar.finish(0, stopped=True)
        self.alongside_ended()
        sheets.warning(self, tr("That did not work."), msg)

    def apply(self):
        n = self.actionable()
        if not n and self.plain_albums() and not self.running:
            self.save_tags()                                  # (EDT-2: the button says Save Tags then)
            return
        if not n or self.edit_pending():
            return
        if self.running:
            self.apply_during_look_up()
            return
        if getattr(self, "plan_library", "") != (self.library or ""):
            # (the preview was made for another library folder: its files would go there)
            sheets.information(self, tr("Preview again first."),
                               tr("The library folder changed since this preview; its files would go to the "
                                  "folder it was made for. Preview or Look Up again, then apply."))
            return
        stale = self.stale_profiles(self.to_apply())
        if stale:
            # (previewed with another profile than the one now chosen for them)
            sheets.information(self, tr("Preview again first."),
                               tr("The profile of {0} changed since this preview. Preview or Look Up again, "
                                  "then apply.").format(plural(len(stale), tr("album"), tr("albums"))))
            return
        # always asked here: the profile's "Apply without asking" is for its watched folders only
        if not sheets.confirm(self, *self.apply_words()) or self.edit_pending():
            return
        if self.running:
            # (work started while you were asked - a folder dropped meanwhile, looked up: Apply
            # would take its worker over. Nothing applied; the albums chosen stay chosen)
            self.win.statusBar().showMessage(tr("Not applied: other work started meanwhile. Apply again when it "
                                                "ends."), 8000)
            return
        self.start_apply(self.to_apply())

    def start_apply(self, chosen):
        """Apply the albums CHOSEN (confirmed), nothing else running."""
        self.release_files()
        # (only the ticked albums go to the executor, one job per profile they were previewed
        # with; what it reports is mapped back to the list)
        plans, order = self.apply_plans(chosen)
        at = {id(a): i for i, a in enumerate(self.plan["albums"])}
        self._applied = list(order)
        self._applied_rows = [at[id(a)] for a in order]           # (as given: applied_albums reads _applied)

        offs, total = [], 0                               # (album i of all the plans: counted on)
        for p in plans:
            offs.append(total)
            total += len(p["albums"])

        def work(w):
            import executor
            at = iter(offs)

            def run(p):
                o = next(at)
                return executor.Executor(p, cancel=lambda: w.cancelled,
                                         progress=lambda i, _n, what: w.progress.emit(o + i, total, what),
                                         file_progress=lambda i, j, n, f: w.partial.emit(("file", o + i, j, n, f)),
                                         album_done=lambda i, r: w.partial.emit(("album", o + i, r))).run()
            return merge_results(plans, run, lambda: w.cancelled)
        self.states = {id(a): "waiting" for a in self._applied}
        self.model.refresh_rows()
        self.busy(True)
        self._work_kind = "apply"
        self._applying, self._apply_at = True, (0, total, "")
        self.apply_bar.start(planned_files(order))
        self.summary.setText(tr("Applying…"))
        self.worker = start(self, work, self.applied, self.progress, self.apply_failed,
                            partial=self.apply_news)

    def apply_failed(self, msg):
        """Apply stopped with an error: said as any failure; the albums a look-up was
        interrupted for (Apply during a look-up) are looked up still."""
        self._joined = False
        self.apply_bar.finish(0, stopped=True)
        self.oops(msg)
        if self.queue and not self.running:
            self.run_queue()

    def fresh_job(self, job) -> str:
        """JOB, or (Apply ran it already: albums left out of it applied now) a new job named
        after it, which keeps the artwork the preview staged under that name."""
        import executor
        base, n = job, 1
        while not job or job in self.used_jobs or (executor.job_dir(job) / "journal.jsonl").exists():
            n += 1
            job = f"{base}-{n}" if base else time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.used_jobs.add(job)
        return job

    def release_files(self):
        """The player lets go of every file Apply is about to write, move or copy (a track playing
        from the album, or one queued after it, would hold the old file open)."""
        player = getattr(self.win, "player", None)
        if player is None:
            return False
        paths = [p for a in self.to_apply() for fe in a.get("files") or []
                 for p in (fe.get("src"), fe.get("dst")) if p]
        return player.release(paths)

    def edit_pending(self):
        """An edit of yours is still on its way to the preview: Apply would leave it out."""
        if not self.win.inspector.pending():
            return False
        self.win.statusBar().showMessage(tr("Your edit is still being applied to the preview. "
                                            "Apply again in a moment."), 5000)
        return True

    # ---- EDT-2: your plain edits of albums not looked up, written by Save Tags -----------------
    def plain_albums(self) -> list:
        """The albums of the list not looked up that hold an edit of yours not saved yet."""
        return [a for a in (self.plan or {}).get("albums") or [] if planmod.plain_pending(a)]

    def may_save_tags(self) -> bool:
        return bool(self.plain_albums()) and not self.running and getattr(self, "apply_worker", None) is None

    def save_words(self, albums):
        """The Save Tags sheet: (headline, which albums and what is written, how to undo, button)."""
        from .models import album_changes_text
        n = len(albums)
        items = [f"{html.escape(album_words(a['source_dir']))}: {html.escape(album_changes_text(a))}"
                 for a in albums[:APPLY_LISTED]]
        if n > APPLY_LISTED:
            items.append(html.escape(tr("and {0} more").format(n - APPLY_LISTED)))
        body = (f"<p>{html.escape(tr('Only the tags and covers you changed are written, each file where it is:'))}</p>"
                "<ul style='margin-left:-24px; margin-top:0'>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"
                f"<p>{html.escape(tr('Nothing is looked up, renamed or moved.'))}</p>")
        return (tr("Save your edits of {0}?").format(plural(n, tr("album"), tr("albums"))), body,
                tr("Every change can be undone: Edit > Undo Last Job, or Jobs."),
                tr("Save Tags") if n == 1 else tr("Save {0} Albums").format(n))

    def save_tags(self, ask=True, then=None, only=None) -> bool:
        """Save Tags (EDT-2): your edits of the albums not looked up written as one job through the
        executor - staged, the audio verified, the tags read back, journaled, undoable from Jobs -
        and nothing else: no look-up, no rename or move, no companion, no TagMender mark. The
        watcher is told (watcher.remember_saved), so this write never has it look at the album.
        THEN: called once every album is saved (a folder to show next, the window to close).
        ONLY: these albums alone (else every album with edits not saved). True when the save started."""
        ids = None if only is None else {id(a) for a in only}
        albums = [a for a in self.plain_albums() if ids is None or id(a) in ids]
        if not albums:
            return False
        if self.running or getattr(self, "apply_worker", None) is not None:
            self.win.statusBar().showMessage(tr("Finish or cancel the current work first, then save."), 5000)
            return False
        if self.edit_pending():
            return False
        if ask and not sheets.confirm(self, *self.save_words(albums)):
            return False
        if self.running or self.edit_pending():               # (work started while you were asked)
            return False
        albums = [a for a in self.plain_albums() if ids is None or id(a) in ids]
        if not albums:
            return False
        name = self.above() or "default"
        plan = planmod.plain_plan(albums, self.fresh_job(""), name)
        player = getattr(self.win, "player", None)
        if player is not None:                                # (a track playing would hold the old file open)
            player.release([fe["src"] for a in plan["albums"] for fe in a["files"]])
        every = [(a["source_dir"], list(a.get("album_files") or [fe["src"] for fe in a.get("files") or []]))
                 for a in albums]

        def work(w):
            import executor
            import logsetup
            import watcher
            # (held until the watcher is told: "a job is changing files" keeps its pass off these
            # albums until their records say what the save left - never looked at for it)
            with executor.changing_files():
                res = executor.Executor(plan, cancel=lambda: w.cancelled,
                                        progress=lambda i, n, what: w.progress.emit(i, n, what)).run()
                done = {r["index"] for r in res["albums"] if r.get("status") == "done" and "index" in r}
                try:                                          # (the watcher's memory: never fails the save)
                    watcher.remember_saved([every[i] for i in sorted(done)])
                except Exception:
                    logsetup.setup("apply", job=plan["job"]).exception("the watcher was not told of the saved albums")
            # (the files each album's job wrote: one your rules exclude now is left as it was, as is
            # one whose only change was a cover image gone since; None: the journal could not be
            # read - what was written is not known, so no edit is taken for written)
            wrote = set()
            try:
                for e in executor.Journal(executor.job_dir(plan["job"])).read():
                    if e.get("ev") == "file" and e.get("state") == "committed":
                        wrote.add((e.get("album"), e.get("idx")))
            except Exception:
                logsetup.setup("apply", job=plan["job"]).exception("the saved job's journal could not be read")
                wrote = None
            if wrote is None:
                res["unwritten"] = None
            else:
                res["unwritten"] = {}
                for i, x in enumerate(plan["albums"]):
                    art = x.get("artwork") or {}
                    gone = bool(art.get("embed_into")) and executor._embed_image(art) is None
                    res["unwritten"][i] = [(fe["src"], "cover" if gone and not fe.get("changes") else "rules")
                                           for j, fe in enumerate(x["files"]) if (i, j) not in wrote]
            return res
        self._saving, self._save_then = list(albums), then
        self.states = {id(a): "waiting" for a in albums}
        self.model.refresh_rows()
        self.busy(True)
        self._work_kind = "save"
        self._applying = True                                 # (EDT-1: no edit of any album while it runs)
        self.apply_bar.start([None] * len(albums), kind="save")
        self.summary.setText(tr("Saving your edits…"))
        self.worker = start(self, work, self.saved, self.saving_progress, self.save_failed)
        return True

    def saving_progress(self, i, n, what):
        mine = getattr(self, "_saving", None) or []
        if 0 <= i < len(mine):
            self.states = {k: v for k, v in self.states.items() if v != "applying"}
            self.states[id(mine[i])] = "applying"
            self.model.refresh_rows()
            self.apply_bar.album_started(i)
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i + 1)
        self.win.show_count(tr("{0} of {n}").format(i + 1, n=n))
        self.summary.setText(tr("Saving your edits: album {0} of {n}: {name}…").format(i + 1, n=n,
                                                                                      name=Path(what).name))

    def saved(self, res):
        """Save Tags ended: each album saved is shown again as it is now, its edit forgotten (a
        later look-up plans it afresh: the database's values win, EDT-2); one not saved keeps your
        edit, read again against the file as it is now."""
        albums, then = getattr(self, "_saving", None) or [], getattr(self, "_save_then", None)
        self._saving, self._save_then = None, None
        by = {r["index"]: r for r in res.get("albums") or [] if "index" in r}
        insp = self.win.inspector
        ok, bad, some = [], [], []
        unknown = res.get("unwritten", {}) is None
        for i, a in enumerate(albums):
            r = by.get(i)
            left = (res.get("unwritten") or {}).get(i) or []
            if r and r.get("status") == "done" and unknown:
                # (what was written cannot be told: every edit kept, read again against the files)
                some.append((a, dict(r, detail=tr("what was written could not be read from the job's record; "
                                                   "your edits are kept and read again against the files"))))
            elif r and r.get("status") == "done" and left:
                # (some files written; the others not - their edits stay)
                ruled = [f for f, why in left if why == "rules"]
                parts = []
                if ruled:
                    parts.append(tr("{0} left as they were: your rules exclude them now").format(
                        plural(len(ruled), tr("file"), tr("files"))))
                if len(ruled) < len(left):
                    parts.append(tr("{0} left as they were: the cover image you chose is gone; choose it "
                                    "again").format(plural(len(left) - len(ruled), tr("file"), tr("files"))))
                some.append((a, dict(r, detail="; ".join(parts))))
            else:
                (ok if r and r.get("status") == "done" else bad).append((a, r))
        for a, _r in some:                                   # (the files written read again: no change left)
            for fe in a.get("files") or []:
                fe.pop("fingerprint", None)
            insp.reapply(a)
        bad += some
        for a, _r in ok:
            insp.forget_edits_of(a)
            planmod.apply_edits(a, None)                     # (as listed: its edit's values gone)
            a["files"] = []                                  # (its files read again when shown)
            for k in ("fingerprint", "renames"):
                a.pop(k, None)
        self.apply_bar.finish(len(ok), len(bad))
        self.busy(False)
        for a, _r in ok + bad:
            self.panes.album_changed(a)
        self.model.refresh_rows()
        job = res.get("job") or ""
        words = tr("Saved your edits of {0}").format(plural(len(ok), tr("album"), tr("albums"))) if ok else \
            tr("Nothing was saved")
        self.summary.setText(words + (tr(" (job {0}). Undo it from Jobs.").format(job) if ok and job else "."))
        self.win.statusBar().showMessage(self.summary.text(), 10000)
        self.win.pages["Jobs"].reload()
        if bad:
            lines = []
            for a, r in bad:
                why = (r or {}).get("detail") or ((r or {}).get("label") or tr("not reached (cancelled)"))
                lines.append(f"{album_words(a['source_dir'])}: {why}")
                if (r or {}).get("status") == "conflict" and planmod.editable(a):
                    for fe in a.get("files") or []:          # (your edit, against the file as it is now)
                        fe.pop("fingerprint", None)
                    insp.reapply(a)
            sheets.warning(self, tr("{0} not saved.").format(plural(len(bad), tr("album"), tr("albums"))),
                           "\n".join(lines) + "\n\n" + tr("Your edits of what was not written are kept, to save "
                                                           "again, change or take back (Edit › Undo Edit)."))
        elif then is not None:
            then()
        self.win.update_album_actions()

    def save_failed(self, msg):
        self._saving, self._save_then = None, None
        self.apply_bar.finish(0, stopped=True)
        self.oops(msg)

    def may_drop_plain(self, then=None) -> bool:
        """Before the list goes (another folder, the window closing): your plain edits not saved
        are saved, discarded or kept (Cancel), as a document's are. Save: the save starts, and
        THEN follows it once every album is saved; False is returned (wait for it)."""
        albums = self.plain_albums()
        if not albums:
            return True
        n = len(albums)
        mine = {os.path.realpath(a["source_dir"]) for a in albums}
        others = [k for k in self.win.inspector.edits if k not in mine]
        body = tr("Your tag edits of albums not looked up are lost if you don't save them.")
        if others:                                            # (edits of looked-up albums: Apply writes them)
            body += " " + tr("Your edits of {0} looked up are not saved here: apply them first, or they are "
                             "discarded too.").format(plural(len(others), tr("album"), tr("albums")))
        ans = sheets.ask(self, tr("Save your edits of {0}?").format(plural(n, tr("album"), tr("albums"))), body,
                         buttons=(tr("Cancel"), tr("Don't Save"), tr("Save")), default=tr("Save"),
                         offscreen=tr("Don't Save"))
        if ans == tr("Save"):
            self.save_tags(ask=False, then=then)
            return False
        if ans == tr("Don't Save"):
            self.win.inspector.forget_edits()
            return True
        return False

    def applied(self, res):
        given = Overview.applied_albums(self) or list(self.plan["albums"])   # (none: the whole plan was given)
        self._applied, self._applied_rows = None, None
        done_albums = given
        names = [n for n in dict.fromkeys((getattr(self, "planned_with", None) or {}).get(Overview.album_key(x))
                                          for x in done_albums) if isinstance(n, str) and n]
        rep = run_report(dict(self.plan, albums=done_albums, **({"profile": ", ".join(names)} if names else {})), res)
        # (the executor's index is into the albums it was given)
        by_index = {id(given[r["index"]]): r for r in res["albums"] if "index" in r and r["index"] < len(given)}
        by = {a["album"]: a for a in res["albums"]}
        for a in self.plan["albums"]:
            r = by_index.get(id(a)) if by_index else by.get(a["source_dir"])
            if r:
                a["status"] = {"done": "done"}.get(r["status"], r["status"])
                if r["status"] in ("failed", "conflict"):
                    a["conflicts"] = [r["detail"]]
                if r.get("label") in ("Ignored", "Skipped"):
                    a["label"] = r["label"]
        self.model.set_plan(self.plan)
        self.end_bar(res)
        many = ", " in (res.get("job") or "")
        self.summary.setText((tr("Jobs {0}: ").format(res["job"]) if many else f"Job {res['job']}: " if res.get("job")
                              else "") + (counts_text(res["counts"]) or tr("Nothing was changed")) +
                             ((tr(". Undo them from Jobs.") if many else tr(". Undo it from Jobs."))
                              if res.get("job") and set(res["counts"]) - NOTHING_DONE
                              else "."))           # (no job, or nothing changed by it: nothing to undo)
        left = [a for a in self.plan["albums"] if can_apply(a)]
        plain = [a for a in self.plan["albums"] if planmod.plain_pending(a)]     # (EDT-2: Save Tags writes them)
        if left:                                          # (albums left out stay, to apply later)
            self.summary.setText(self.summary.text() + " " + tr("{0} left to apply.").format(
                plural(len(left), tr("album"), tr("albums"))))
        if plain:
            self.summary.setText(self.summary.text() + " " + tr("Your edits of {0} not looked up are not saved yet "
                                                                "(File › Save Tags).").format(
                plural(len(plain), tr("album"), tr("albums"))))
        if not (left or plain) and not self.queue and not getattr(self, "_joined", False) and not self._reading:
            self.plan = None                              # (albums dropped meanwhile: the list stays)
            self.show_ready()
        self._joined = False
        self.busy(False)
        self.service_status(force=True)
        notify(self.win, "finished", tr("Apply finished"), counts_text(res["counts"]))
        self.win.pages["Jobs"].reload()
        self.win.refresh_attention_badge(self.path)          # (Apply marks albums Unresolved on disk)
        if rep is not None:
            self.win.show_run_report(rep)                  # (what was done, what is left for you)
        if self.queue:                                     # (stopped for this Apply: on with the look-up)
            self.run_queue()

    def cancel(self):
        if getattr(self, "apply_worker", None) is not None:
            self.apply_worker.cancelled = True              # (APL-1: the Apply beside it stops too)
        if self.worker:
            self.worker.cancelled = True
            self._apply_next, self._apply_only = False, None   # (Cancel stops it all, Apply too)
            n = len(self.queue)
            self.queue = {}                                # (nor is what waited for it looked up)
            if self.later:                                 # (nor are the folders dropped meanwhile added)
                from .manual_match import _names
                self.win.statusBar().showMessage(tr("Not added: {0}.").format(_names(self.later)), 6000)
                self.later = []
            self._early = {}                               # (nor what was read of them: their rows go)
            self.drop_provisional()
            self.states = {k: v for k, v in self.states.items() if v != "waiting"}
            self.model.refresh_rows()
            self.summary.setText(tr("Cancelling after the current album…") + (tr(
                " The {0} queued are not looked up.").format(plural(n, tr("album"), tr("albums"))) if n else ""))

    def selected(self):
        idx = self.table.currentIndex() if hasattr(self, "table") and self.table.selectionModel().hasSelection() \
            else None
        if not idx or not idx.isValid():
            return None
        # the plan's own entry (a disc's or track's: its album's), not data(UserRole): Qt hands a
        # dict back as a copy, and an edit (the inspector, Tags from File Names) must change the
        # album the plan applies
        n = idx.internalPointer()
        return n.album if n is not None else None

    def show_row(self, row):
        """Highlight ROW only to show it in the inspector: nobody chose it for Apply."""
        self._showing_row = True
        try:
            self.table.selectRow(row)
        finally:
            self._showing_row = False

    def selection_changed(self, *_):
        from PySide6.QtWidgets import QApplication
        self._rows_chosen = not getattr(self, "_showing_row", False) and self.table.selectionModel().hasSelection()
        if self._rows_chosen:
            self._profile_pane = "albums"
        self.show_profile_choice()
        self.show_ready()                                 # (what Apply would carry out has changed)
        self.win.update_album_actions()
        if QApplication.mouseButtons() & Qt.LeftButton:
            self._detail_timer.start()
        else:
            self._detail_timer.stop()
            self.show_detail()

    def show_detail_after_drag(self):
        from PySide6.QtWidgets import QApplication
        if QApplication.mouseButtons() & Qt.LeftButton:     # (still dragging)
            self._detail_timer.start()
        else:
            self.show_detail()

    def show_detail(self, *_):
        a = self.selected()
        if a is None:
            self.detail.clear()
            self.win.inspector.clear()
        else:
            one = dict(self.plan_for([a]), summary={a["status"]: 1})
            self.detail.setPlainText(planmod.preview(one, verbose=False))
            self.win.inspector.show_album(a, self.plan)
        if hasattr(self, "panes"):
            # (the panes follow in a turn of their own: finding the album among thousands in
            # their trees is the other half of the work of showing a row)
            if not getattr(self, "_panes_follow", False):
                self._panes_follow = True
                QTimer.singleShot(0, self, self.panes_follow)
        self.busy(bool(getattr(self, "running", False)))

    def panes_follow(self):
        self._panes_follow = False
        self.panes.show_album(self.selected())

    def choose_release(self):
        a = self.selected()
        if not a:
            return
        import engine
        key = os.path.realpath(a["source_dir"])
        files = engine.plan_entry_files(a)
        mine = {os.path.realpath(f) for f in files}
        choices = [c for c in (self.forced.get(key) or []) if isinstance(c, dict)]
        was = next((c for c in choices if mine & {os.path.realpath(f) for f in c.get("files") or []}), None)
        ref, ok = sheets.get_text(self, tr("Use another release for {name}").format(name=Path(a['source_dir']).name),
                                  tr("Paste a MusicBrainz or Discogs release link, or its ID. The album is "
                                     "previewed again with it; leave it empty to let TagMender choose."),
                                  text=(was or {}).get("ref", ""), action=tr("Use Release"),
                                  placeholder="https://musicbrainz.org/release/…")
        if not ok:
            return
        ref = ref.strip()
        if ref and engine.parse_release_ref(ref)[0] is None:
            sheets.warning(self, tr("That is not a MusicBrainz or Discogs release link."),
                           tr("Copy the address of the release page (musicbrainz.org/release/… or "
                              "discogs.com/release/…), or just its ID."))
            return
        # (this album only: a folder may hold several, each with its own choice)
        choices = [c for c in choices if c is not was]
        if ref:
            choices.append(dict({"ref": ref, "files": files}, **({"only": True} if a.get("split") else {})))
        if choices:
            self.forced[key] = choices
        else:
            self.forced.pop(key, None)
        # (this album only - not every album listed - looked up again with the release chosen)
        self.look_up(self.sources.currentData() or "profile", self.with_versions([a]), again=True)

    # ---- MOV-5: filed though the score is below the floor, because you ask -----------------
    def may_file_anyway(self, a) -> bool:
        """The album was tagged where it is (or not copied) only because its score is below the
        profile's floor for filing (plan entry "below_floor"), and is not being worked on."""
        return bool(a and a.get("below_floor") and a.get("status") not in ("done",)
                    and not (getattr(self, "running", False) and not getattr(self, "looking", None)))

    def drop_asks_of_other_profiles(self):
        """MOV-5: the profile chosen above changed: an ask made under another profile is dropped
        (a move asked for is no copy asked for). An album with a profile of its own keeps its."""
        own = set(self.album_profiles.values())
        self.file_anyway = {f: n for f, n in self.file_anyway.items()
                            if n == self.above() or n in own}

    def file_anyway_selected(self):
        """Album > Move into the Library Layout Anyway: the selected albums kept in place for their
        score alone are looked up again with the floor lifted for their folders only (the rest of
        the list stays as it is). Nothing is written until Apply."""
        chosen = [a for a in self.selected_albums() if self.may_file_anyway(a)]
        if not chosen:
            return
        import engine
        for a in chosen:                                     # (the album's own files: not its folder's others)
            for f in engine.plan_entry_files(a):
                self.file_anyway[os.path.realpath(f)] = self.profile_of(a)
        self.look_up(self.sources.currentData() or "profile", self.with_versions(chosen), again=True)
        self.win.statusBar().showMessage(
            tr("Looking up {0} again, to be filed into the library as you asked; nothing is written until you "
               "apply.").format(plural(len(chosen), tr("album"), tr("albums"))), 10000)

    def process_anyway(self):
        a = self.selected()
        if a and a["status"] == "excluded":
            folder = Path(a["source_dir"])
            mine = {fe["src"] for fe in a.get("files") or []} | {s.get("file") for s in a.get("skipped") or []} \
                | set(a.get("excluded_files") or [])
            mine.discard(None)
            # only the album's own files, never its folder: loose tracks have a watched folder or
            # a library root for theirs, and the rules would be off for all dropped there later
            if mine:
                for f in sorted(mine):
                    ex.State().allow(f)
                what = Path(sorted(mine)[0]).name if len(mine) == 1 else \
                    tr("{0} files in {name}").format(len(mine), name=folder.name)
            else:
                ex.State().allow(folder)
                what = folder.name
            self.summary.setText(tr("Rules will not exclude {what}. Preview again to include it; to exclude it "
                                    "again, choose Album > Exclude Again.").format(what=what))
            self.refresh_overrides()

    # ---- Process Anyway, undone ------------------------------------------------
    def refresh_overrides(self):
        """The paths Process Anyway let through (read once per preview, not per selection)."""
        try:
            self.overrides = {os.path.abspath(str(p)) for p in ex.State().overrides()}
        except Exception:
            self.overrides = set()
        self.win.update_album_actions()

    def _album_paths(self, a):
        folder = a["source_dir"]
        mine = {fe["src"] for fe in a.get("files") or []} | {s.get("file") for s in a.get("skipped") or []} \
            | set(a.get("excluded_files") or []) | {folder}
        mine.discard(None)
        return {os.path.abspath(str(p)) for p in mine}

    def overridden(self, a=None):
        """The selected album's paths that Process Anyway let through (none: Exclude Again is off)."""
        a = a or self.selected()
        return sorted(self._album_paths(a) & getattr(self, "overrides", set())) if a else []

    def exclude_again(self):
        a = self.selected()
        paths = self.overridden(a)
        if not paths:
            return
        st = ex.State()
        for p in paths:
            st.disallow(p)
        self.refresh_overrides()
        self.summary.setText(tr("The rules apply to {name} again. Preview again to see it "
                                "excluded.").format(name=Path(a['source_dir']).name))
