"""stagetimes.py - where an album's look-up spends its time, for the log (PRF-1).

While album() is active on a thread, stage(name) blocks charge the seconds spent in them to that
name; a stage inside another is charged to itself only (the outer one pauses meanwhile), so the
stages of an album add up to its wall time and what no stage covers is "other". Outside album()
every call is a no-op costing one context-variable lookup, so the hooks stay in place for good.

The engine writes one line per album to the log (line()), e.g.
  timing /music/Band/2005 - Album: 12.41 s: musicbrainz 5.10, musicbrainz back-off 3.00, tags 0.40, other 3.91
which tools/perf_check.py and a release check read back (parse()).

Stage names: scan (finding the albums), tags (reading a file's tags), ocr (reading scans),
fingerprint (fpcalc), musicbrainz / discogs / web (live requests, with the host's pacing),
"<provider> back-off" (waiting before a retry: HTTP 429/503, network errors), ai (a language
model's answer, when one is asked).
"""
from __future__ import annotations

import contextvars
import re
import threading
import time
from contextlib import contextmanager

STAGES = ("scan", "tags", "ocr", "fingerprint", "musicbrainz", "musicbrainz back-off", "discogs",
          "discogs back-off", "web", "web back-off", "ai")

_CUR = contextvars.ContextVar("tagmender_stage_times", default=None)


class _Times:
    __slots__ = ("secs", "stack", "since")

    def __init__(self):
        self.secs = {}            # stage -> seconds charged
        self.stack = []           # the stages open now, innermost last
        self.since = 0.0          # when the innermost one was last resumed

    def charge(self, now):
        if self.stack:
            name = self.stack[-1]
            self.secs[name] = self.secs.get(name, 0.0) + (now - self.since)
        self.since = now


@contextmanager
def album():
    """Times what runs inside the block on this thread; yields the {stage: seconds} it collects
    (complete once the block is left)."""
    t = _Times()
    token = _CUR.set(t)
    try:
        yield t.secs
    finally:
        t.charge(time.monotonic())
        _CUR.reset(token)


@contextmanager
def stage(name):
    t = _CUR.get()
    if t is None or (t.stack and t.stack[-1] == name):   # (not timed, or the same stage nested)
        yield
        return
    t.charge(time.monotonic())
    t.stack.append(name)
    try:
        yield
    finally:
        t.charge(time.monotonic())
        t.stack.pop()


# Who is told of a back-off as it starts (the app's status line): fn(provider, seconds), from
# whichever thread waits; provider as the "<provider> back-off" stage names it (provider_of).
_BACK_OFF = []
_BACK_OFF_LOCK = threading.Lock()


def on_back_off(fn):
    """Tell fn(provider, seconds) of each back-off from now on. Returns a function that stops
    it (as off_back_off(fn) does)."""
    with _BACK_OFF_LOCK:
        _BACK_OFF.append(fn)
    return lambda: off_back_off(fn)


def off_back_off(fn) -> None:
    """Stop telling fn of back-offs (once for each time it was registered; never raises)."""
    with _BACK_OFF_LOCK:
        try:
            _BACK_OFF.remove(fn)
        except ValueError:
            pass


def back_off(provider, seconds) -> None:
    """A wait of SECONDS before retrying PROVIDER starts: each listener told; a listener's
    error never reaches the request."""
    with _BACK_OFF_LOCK:
        fns = list(_BACK_OFF)
    for fn in fns:
        try:
            fn(provider, seconds)
        except Exception:
            pass


def provider_of(host) -> str:
    """The stage a request to HOST is charged to."""
    return {"mb": "musicbrainz", "discogs": "discogs"}.get(host, "web")


def line(folder, total, secs) -> str:
    """The log line for one album: its wall time, then each stage that took 0.005 s or more,
    longest first, then what no stage covered."""
    parts = [(n, s) for n, s in sorted(secs.items(), key=lambda x: -x[1]) if s >= 0.005]
    other = max(0.0, total - sum(secs.values()))
    parts.append(("other", other))
    return f"timing {folder}: {total:.2f} s: " + ", ".join(f"{n} {s:.2f}" for n, s in parts)


_LINE = re.compile(r"timing (?P<folder>.+): (?P<total>\d+(?:\.\d+)?) s: (?P<rest>.*)$")
_PART = re.compile(r"(?P<name>[a-z][a-z -]*?) (?P<secs>\d+(?:\.\d+)?)$")


def parse(text):
    """(folder, total seconds, {stage: seconds}) of a log line line() wrote, or None."""
    m = _LINE.search(text.rstrip("\n"))
    if not m:
        return None
    secs = {}
    for part in m["rest"].split(", "):
        p = _PART.match(part.strip())
        if p:
            secs[p["name"]] = float(p["secs"])
    return m["folder"], float(m["total"]), secs
