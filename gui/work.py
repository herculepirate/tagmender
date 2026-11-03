"""gui/work.py - Background work: run a function on a thread and hand its result back on the UI thread."""
from __future__ import annotations

import gc
import traceback

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QWidget

from . import sheets
from .i18n import tr


# --------------------------------------------------------------------------
# background work
# --------------------------------------------------------------------------
class Worker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancelled = False

    def run(self):
        try:
            out = self.fn(self)
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=3)}")
            return
        self.finished.emit(out)


def _alive(th) -> bool:
    try:
        return th.isRunning()
    except RuntimeError:                      # (deleted once it finished)
        return False


class _Relay(QObject):
    """Calls fn on the thread it lives on (the UI thread): a plain Python function connected
    to a worker's signal would otherwise run on the worker's thread, where no widget may be
    touched."""
    def __init__(self, parent, fn):
        super().__init__(parent)
        self.fn = fn

    @Slot(object)
    def one(self, x):
        self.fn(x)

    @Slot(int, int, str)
    def three(self, a, b, c):
        self.fn(a, b, c)


_gc_timer = None


def _collect():
    """Python's cycle collection, done here on the UI thread: run on a worker, it may free a
    dropped window's widgets there, and a widget deleted off the UI thread can deadlock Qt
    against the UI thread (seen with the search sheets). The generations are collected as
    Python itself would, when their counts pass the thresholds."""
    counts, limits = gc.get_count(), gc.get_threshold()
    for gen in (2, 1, 0):
        if counts[gen] > limits[gen]:
            gc.collect(gen)
            break


def collect_on_ui_thread():
    """From the first background job on: no automatic collection (it runs on whichever thread
    allocates), a timer on the UI thread collects instead."""
    global _gc_timer
    app = QCoreApplication.instance()
    if _gc_timer is not None or app is None:
        return
    gc.disable()
    _gc_timer = QTimer(app)
    _gc_timer.timeout.connect(_collect)
    _gc_timer.start(500)


def start(owner, fn, done, progress=None, failed=None):
    """Run fn(worker) on a thread; done(result), failed(message) and progress(...) on the UI
    thread."""
    collect_on_ui_thread()
    th = QThread(owner)
    w = Worker(fn)
    w.moveToThread(th)
    th.started.connect(w.run)
    relays = [_Relay(owner, done), _Relay(owner, failed or (lambda msg: sheets.warning(
        owner if isinstance(owner, QWidget) else None, tr("That did not work."), msg)))]
    w.finished.connect(relays[0].one)
    w.failed.connect(relays[1].one)
    if progress:
        relays.append(_Relay(owner, progress))
        w.progress.connect(relays[2].three)
    w._relays = relays                                  # (kept alive with the worker)
    for sig in (w.finished, w.failed):
        sig.connect(th.quit)
    th.finished.connect(th.deleteLater)
    for r in relays:                                    # (after the result was handled: queued in order)
        th.finished.connect(r.deleteLater)
    owner._threads = [(t, x) for t, x in getattr(owner, "_threads", []) if _alive(t)] + [(th, w)]
    th.start()
    return w
