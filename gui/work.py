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
    partial = Signal(object)                # (a part of the result, ready before the rest)
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
            log_failure(exc)
            self.failed.emit(f"{exc or type(exc).__name__}\n\n{short_traceback(exc)}")
            return
        self.finished.emit(out)


SHOWN_FRAMES = 15                   # the frames nearest the error a failure shows (the log has all)
_CYCLE_MAX = 8                      # the longest run of frames recognised as repeating


def _collapse(frames) -> tuple:
    """FRAMES (traceback.FrameSummary) as lines, a frame or a run of frames repeated one after
    the other said once, then "[previous line repeated N more times]" (a recursion of one
    function, or of several calling each other in turn). Returns (lines, where each run said
    once starts among them)."""
    keys = [(f.filename, f.lineno, f.name) for f in frames]
    out, starts, i = [], [], 0
    while i < len(frames):
        best = None
        for n in range(1, _CYCLE_MAX + 1):
            reps = 1
            while i + (reps + 1) * n <= len(keys) and keys[i + reps * n:i + (reps + 1) * n] == keys[i:i + n]:
                reps += 1
            if reps > 1 and (best is None or reps * n > best[0] * best[1]):
                best = (reps, n)
        if best is None:
            out.append(frames[i])
            i += 1
            continue
        reps, n = best
        starts.append(len(out))
        out += frames[i:i + n]
        what = "previous line" if n == 1 else f"previous {n} lines"
        out.append(f"  [{what} repeated {reps - 1} more time{'s' if reps - 1 != 1 else ''}]\n")
        i += reps * n
    return out, starts


def short_traceback(exc, keep=SHOWN_FRAMES) -> str:
    """The frames nearest where EXC was raised (the last KEEP, repeats collapsed - and the
    repeating frames themselves, when a recursion ran out of room), then the error: where it
    went wrong, never only where the work started."""
    tb = traceback.TracebackException.from_exception(exc, capture_locals=False)
    lines, starts = _collapse(list(tb.stack))
    frame_at = [i for i, x in enumerate(lines) if not isinstance(x, str)]
    first = frame_at[-keep] if len(frame_at) > keep else 0
    if starts and starts[-1] < first:
        first = starts[-1]                                  # (the frames that repeat, shown too)
    shown = lines[first:]
    head = "Traceback (most recent call last):\n"
    if first:
        head += f"  [{first} earlier lines not shown; the log has them all]\n"
    body = "".join(x if isinstance(x, str) else "".join(traceback.StackSummary.from_list([x]).format())
                   for x in shown)
    return head + body + "".join(tb.format_exception_only())


def log_failure(exc):
    """The failure's whole traceback, in the TagMender log."""
    import logging
    try:
        log = logging.getLogger("tagmender")
        if not log.handlers:
            import logsetup
            logsetup.setup("gui")
        logging.getLogger("tagmender.gui").error("background work failed: %s", exc or type(exc).__name__,
                                                 exc_info=exc)
    except Exception:                         # (the log is no reason to lose the message shown)
        pass


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
SWITCH_INTERVAL = 0.0002            # s: the longest a thread keeps Python's lock while another waits
FULL_EVERY = 600.0                  # s: the objects kept since are looked through again this often
_last_full = None


def _collect():
    """Python's cycle collection, done here on the UI thread: run on a worker, it may free a
    dropped window's widgets there, and a widget deleted off the UI thread can deadlock Qt
    against the UI thread (seen with the search sheets). The generations are collected as
    Python itself would, when their counts pass the thresholds.

    The oldest generation is collected among what was made since its last collection only:
    what outlived that one is set aside (gc.freeze), and looked through again every FULL_EVERY
    seconds. Looking through every object each time - a preview of thousands of albums is
    millions of them - held the UI thread up for 70 ms and more every few seconds."""
    global _last_full
    import time
    counts, limits = gc.get_count(), gc.get_threshold()
    for gen in (2, 1, 0):
        if counts[gen] > limits[gen]:
            if gen == 2:
                now = time.monotonic()
                if _last_full is None or now - _last_full > FULL_EVERY:
                    gc.unfreeze()                     # (everything, now and then)
                    _last_full = now
                gc.collect(2)
                gc.freeze()
            else:
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
    # (the UI thread waits for Python's lock each time Qt calls into Python - a cell drawn, a
    # row asked for - and a worker holds it for up to the switch interval, 5 ms by default: a
    # list drawn while a look-up works took hundreds of ms. A shorter turn hands it over sooner;
    # the workers' own pace was measured the same)
    import sys
    sys.setswitchinterval(min(sys.getswitchinterval(), SWITCH_INTERVAL))
    _gc_timer = QTimer(app)
    _gc_timer.timeout.connect(_collect)
    _gc_timer.start(500)


def start(owner, fn, done, progress=None, failed=None, partial=None):
    """Run fn(worker) on a thread; done(result), failed(message), progress(...) and partial(part)
    (each worker.partial.emit(part) of fn, in order and before done) on the UI thread."""
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
        w.progress.connect(relays[-1].three)
    if partial:
        relays.append(_Relay(owner, partial))
        w.partial.connect(relays[-1].one)
    w._relays = relays                                  # (kept alive with the worker)
    for sig in (w.finished, w.failed):
        sig.connect(th.quit)
    th.finished.connect(th.deleteLater)
    for r in relays:                                    # (after the result was handled: queued in order)
        th.finished.connect(r.deleteLater)
    owner._threads = [(t, x) for t, x in getattr(owner, "_threads", []) if _alive(t)] + [(th, w)]
    th.start()
    return w
