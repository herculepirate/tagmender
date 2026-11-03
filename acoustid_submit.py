"""acoustid_submit.py - Send an album's fingerprints to AcoustID (Picard's "Submit AcoustIDs").

Only ever on the user's explicit request for one album, after a sheet listed what is sent; never
automatically. Two keys, both the user's own, nothing bundled:
  - the application key (credentials "acoustid", acoustid.org/new-application), sent as client=
  - the user key (credentials "acoustid_user", acoustid.org/api-key), sent as user=
Neither is printed, logged or put in an error message.

Per track (as Picard's acoustid.manager.Submission does): fingerprint and duration from fpcalc,
the MusicBrainz recording ID as mbid when the fingerprint's duration is within 30 s of the
MusicBrainz track length (the plan's file entry carries it as "length"; with none known the ID is
sent unchecked, as Picard does, and the confirmation sheet says so for that track); otherwise the track's title, artist, album, album artist,
track and disc number and year in place of the ID. Beyond Picard: the file's format and bitrate
(fileformat=, bitrate=, which the AcoustID web service takes). Nothing else: no file names, no
paths, no other tags. A track without a recording ID is not sent at all (Picard sends only files
matched to a recording too).

The steps, each callable on its own (the GUI runs the slow ones on a worker):
  tracks_of(album) -> [Track]        what the plan or the files say of each track (no network)
  fingerprint(tracks) -> [Track]     fpcalc on each (read-only)
  describe(tracks) -> str            exactly what will be sent, for the confirmation sheet
  submit(tracks) -> [Track]          POST /v2/submit, in batches under 1 MB; submission ids kept
  check_status(tracks) -> [Track]    POST /v2/submission_status for the ids not settled yet
HTTP goes through http_post (a module function: tests patch it). Every request is a POST with a
form body, so the keys are never part of a URL (which proxies and server logs keep)."""
from __future__ import annotations

import json
import logging
import shutil  # noqa: F401  (fpcalc is looked for through it: requirements.status)
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import credentials
import requirements

log = logging.getLogger("tagmender.acoustid_submit")

API = "https://api.acoustid.org/v2"
APP_KEY_URL = "https://acoustid.org/new-application"
USER_KEY_URL = "https://acoustid.org/api-key"
USER = "acoustid_user"                                # the credential's name
MAX_LENGTH_DIFF_MS = 30000                            # (Picard's FINGERPRINT_MAX_ALLOWED_LENGTH_DIFF_MS)
MAX_PAYLOAD = 1000000                                 # AcoustID takes about 1 MB per request
MIN_DELAY = 0.34                                      # 3 requests a second at most
MAX_ATTEMPTS = 3                                      # a batch AcoustID was too busy for: tried again
# a submission's status as AcoustID says it -> plain words
STATUS_WORDS = {"pending": "Waiting to be added", "imported": "Added", "": "Not sent"}
# mutagen's class -> the format name sent as fileformat=
FORMATS = {"FLAC": "FLAC", "MP3": "MP3", "EasyMP3": "MP3", "MP4": "MP4", "EasyMP4": "MP4",
           "OggVorbis": "Ogg Vorbis", "OggOpus": "Opus", "OggFLAC": "Ogg FLAC", "OggSpeex": "Speex",
           "OggTheora": "Ogg Theora", "WavPack": "WavPack", "MonkeysAudio": "Monkey's Audio",
           "Musepack": "Musepack", "ASF": "WMA", "WAVE": "WAV", "AIFF": "AIFF", "TrueAudio": "TTA",
           "OptimFROG": "OptimFROG", "DSF": "DSF", "DSDIFF": "DSDIFF", "AAC": "AAC", "AC3": "AC-3"}


class SubmitError(RuntimeError):
    """A plain-language reason the submission did not happen (never holds a key)."""


# --------------------------------------------------------------------------
# the user key: a credential of its own (the application key is another)
# --------------------------------------------------------------------------
def register():
    """Make the user key known to credentials.py (get/source/store), if it is not already."""
    credentials.ENV.setdefault(USER, "TAGMENDER_ACOUSTID_USER_KEY")
    credentials.FILES.setdefault(USER, "acoustid_user_key")
    credentials.LABELS.setdefault(USER, f"AcoustID user key, for sending fingerprints ({USER_KEY_URL}; "
                                        "not the application key)")


register()


def app_key() -> str:
    return credentials.get("acoustid")


def user_key() -> str:
    return credentials.get(USER)


def store_user_key(value: str):
    """Saved 0600 in TagMender's config folder."""
    return credentials.store(USER, value)


def missing() -> list[str]:
    """What stops a submission, in plain words ([] when all is set up): the Setup notices of the
    program and keys that are not there."""
    return [n for n in (requirements.problem(i) for i in ("fpcalc", "acoustid", USER)) if n]


# --------------------------------------------------------------------------
# tracks
# --------------------------------------------------------------------------
@dataclass
class Track:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    tracknumber: str = ""
    discnumber: str = ""
    year: str = ""
    recording_id: str = ""
    id_from: str = ""                                 # "preview" or "tags": where the ID was read
    length_ms: int | None = None                      # MusicBrainz track length, when known
    fileformat: str = ""
    bitrate: int | None = None                        # kbit/s
    fingerprint: str = ""
    duration: int | None = None                       # seconds, from fpcalc
    problem: str = ""                                 # why it is not sent, in plain words
    submission_id: int | None = None
    status: str = ""                                  # "", pending, imported, or an error's words
    acoustid: str = ""                                # the AcoustID the fingerprint was added to
    extra: dict = field(default_factory=dict)

    @property
    def sendable(self) -> bool:
        return bool(self.recording_id and self.fingerprint and self.duration is not None and not self.problem)

    @property
    def valid_duration(self) -> bool:
        return self.length_ms is None or abs(self.duration * 1000 - self.length_ms) <= MAX_LENGTH_DIFF_MS

    def args(self) -> dict:
        """The fields sent for this track (Picard's Submission.args, with fileformat and bitrate)."""
        a = {"fingerprint": self.fingerprint, "duration": str(self.duration)}
        if self.valid_duration:
            a["mbid"] = self.recording_id
        else:
            a.update(track=self.title, artist=self.artist, album=self.album, albumartist=self.albumartist,
                     trackno=self.tracknumber, discno=self.discnumber)
            if self.year.isdecimal():
                a["year"] = self.year
        if self.fileformat:
            a["fileformat"] = self.fileformat
        if self.bitrate:
            a["bitrate"] = str(self.bitrate)
        return {k: v for k, v in a.items() if v}

    def status_words(self) -> str:
        if self.problem:
            return self.problem
        return STATUS_WORDS.get(self.status, self.status)


def _first(v) -> str:
    if isinstance(v, (list, tuple)):
        return str(v[0]).strip() if v else ""
    return str(v or "").strip()


def _length_ms(text) -> int | None:
    """"3:25" or "1:02:03" (mb_tags' ~length), or milliseconds as a number -> milliseconds."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return int(text) if text > 0 else None
    try:
        parts = [int(p) for p in _first(text).split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs * 1000


def audio_info(path) -> tuple[str, int | None]:
    """(format name, bitrate in kbit/s) of a file; ("", None) when unreadable."""
    try:
        import mutagen
        f = mutagen.File(path)
    except Exception:
        return "", None
    if f is None:
        return "", None
    name = FORMATS.get(type(f).__name__, type(f).__name__)
    if name == "MP4":
        codec = str(getattr(f.info, "codec", "") or "")
        name = "ALAC" if codec.startswith("alac") else "AAC" if codec.startswith("mp4a") else "MP4"
    bps = getattr(f.info, "bitrate", 0) or 0
    return name, (round(bps / 1000) or None)


def _read(path) -> dict:
    try:
        import tagio
        return tagio.read(path)
    except Exception:
        return {}


def track_of(path, changes=None, length=None) -> Track:
    """One file: its own tags, with the preview's new values over them (changes from the plan).
    length: the MusicBrainz track length the plan matched it to ("3:25" or milliseconds), if known."""
    p = Path(path)
    tags = _read(p) if p.exists() else {}
    changes = changes or {}
    merged = dict(tags)
    for k, v in changes.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    rid = _first(merged.get("musicbrainz_recordingid"))
    from_preview = rid and "musicbrainz_recordingid" in changes
    date = _first(merged.get("date")) or _first(merged.get("originaldate"))
    fmt, kbps = audio_info(p) if p.exists() else ("", None)
    t = Track(path=str(p), title=_first(merged.get("title")), artist=_first(merged.get("artist")),
              album=_first(merged.get("album")), albumartist=_first(merged.get("albumartist")),
              tracknumber=_first(merged.get("tracknumber")).split("/")[0],
              discnumber=_first(merged.get("discnumber")).split("/")[0], year=date[:4],
              recording_id=rid, id_from="preview" if from_preview else ("tags" if rid else ""),
              length_ms=_length_ms(length) if length else
              (_length_ms(merged.get("~length")) if merged.get("~length") else None),
              fileformat=fmt, bitrate=kbps)
    if not p.exists():
        t.problem = "The file is not there any more."
    elif not rid:
        t.problem = "No recording ID: not sent."
    return t


def tracks_of(album) -> list[Track]:
    """An album as the plan has it (an Overview entry: files with src, dst and changes), or a list
    of file paths. A file is read where it is: at src while it is still there (the plan not applied
    yet; a file already at dst then is a different one, as in a blocked entry), at dst only once the
    plan moved it there (src gone, op "move")."""
    if isinstance(album, dict):
        out = []
        for fe in album.get("files") or []:
            src, dst = fe.get("src"), fe.get("dst")
            moved = fe.get("op") == "move" and dst and Path(dst).exists() and not (src and Path(src).exists())
            where = dst if moved else src
            out.append(track_of(where, fe.get("changes"), fe.get("length")))
        if not out:
            out = [track_of(p) for p in album.get("album_files") or []]
        return out
    return [track_of(p) for p in album]


def _fpcalc(path):
    import match_albums
    return match_albums.fingerprint(path)


def fingerprint(tracks, progress=None, cancelled=lambda: False, fp=None) -> list[Track]:
    """fpcalc on every track that has a recording ID (read-only). progress(i, n, name)."""
    fp = fp or _fpcalc
    todo = [t for t in tracks if not t.problem]
    for i, t in enumerate(todo):
        if cancelled():
            break
        if progress:
            progress(i, len(todo), Path(t.path).name)
        d = fp(t.path)
        if not d or not d.get("fingerprint"):
            t.problem = "fpcalc could not read the file."
            continue
        t.fingerprint, t.duration = d["fingerprint"], int(d["duration"])
    return tracks


# --------------------------------------------------------------------------
# what is sent
# --------------------------------------------------------------------------
def fallback(tracks) -> list:
    """The sendable tracks whose names go in place of the recording ID (lengths more than 30 s apart)."""
    return [t for t in tracks if t.sendable and "mbid" not in t.args()]


def describe(tracks) -> str:
    """Exactly what a submission sends, word for word as the confirmation sheet shows it: the request
    (address, headers, the form fields; the keys named, never shown), then each track's fields."""
    send = [t for t in tracks if t.sendable]
    lines = []
    if send:
        n = len(batches(send))
        lines += [f"POST {API}/submit" + (f" (in {n} requests: AcoustID takes about 1 MB a request)" if n > 1 else ""),
                  f"  with User-Agent: {_agent()} and Content-Type: application/x-www-form-urlencoded",
                  f"  form fields: format=json, your AcoustID application key (client=), your AcoustID user key "
                  f"(user=), clientversion={_version()},",
                  f"  then for each of {len(send)} track{'s' if len(send) != 1 else ''}:"]
    for t in send:
        a = t.args()
        what = ", ".join(f"{k}={v}" for k, v in a.items() if k != "fingerprint")
        where = {"preview": " (recording ID from the preview, not yet written to the file)",
                 "tags": " (recording ID from the file's tags)"}.get(t.id_from, "") if "mbid" in a else \
            " (lengths more than 30 s apart: the names in place of the recording ID)"
        if "mbid" in a and t.length_ms is None:
            where += " (length not checked: MusicBrainz's track length is not known)"
        lines.append(f"  {t.tracknumber or '?'}. {t.title or Path(t.path).name}: fingerprint "
                     f"({len(t.fingerprint)} characters), {what}{where}")
    if send:
        lines.append("No file names or paths, and no other tags than those listed." if fallback(send)
                     else "No file names, paths or other tags.")
    left = [t for t in tracks if not t.sendable]
    if left:
        lines.append("Not sent:")
        lines += [f"  {t.tracknumber or '?'}. {t.title or Path(t.path).name}: {t.problem or 'no fingerprint'}"
                  for t in left]
    return "\n".join(lines)


def _version() -> str:
    try:
        from version import VERSION
        return str(VERSION)
    except Exception:
        return "0"


def _fields(tracks, app, user) -> list[tuple[str, str]]:
    out = [("format", "json"), ("client", app), ("clientversion", _version()), ("user", user)]
    for i, t in enumerate(tracks):
        out += [(f"{k}.{i}", v) for k, v in t.args().items()]
    return out


def _size(t) -> int:
    """About what one track adds to a request (Picard's Submission.__len__)."""
    return int(sum(len(k) + len(v) + 4 for k, v in t.args().items()) * 1.03)


def batches(tracks, limit=MAX_PAYLOAD) -> list[list[Track]]:
    out, cur, size = [], [], 0
    for t in tracks:
        n = _size(t)
        if cur and size + n >= limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    return out + ([cur] if cur else [])


# --------------------------------------------------------------------------
# HTTP (patched in tests)
# --------------------------------------------------------------------------
def _agent() -> str:
    return f"TagMender/{_version()} ( https://github.com/herculepirate/tagmender )"


def _answer(open_request):
    """The JSON AcoustID answered, error answers included; SubmitError when there is none."""
    try:
        with open_request() as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            doc = json.loads(exc.read().decode("utf-8"))
        except Exception:
            doc = None
        if not isinstance(doc, dict):                     # (no JSON, or not an object: a proxy's page)
            doc = {"status": "error", "error": {"message": f"HTTP {exc.code}"}}
        doc["http"] = exc.code
        return doc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SubmitError(f"AcoustID could not be reached ({getattr(exc, 'reason', exc)}).") from None
    except ValueError:
        raise SubmitError("AcoustID's answer could not be read.") from None
    if not isinstance(doc, dict):                         # (JSON, but not AcoustID's: a list, a string)
        raise SubmitError("AcoustID's answer could not be read.")
    return doc


def http_post(url, fields) -> dict:
    body = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": _agent(), "Content-Type": "application/x-www-form-urlencoded"})
    return _answer(lambda: urllib.request.urlopen(req, timeout=60))


def _scrub(text, *secrets) -> str:
    for s in secrets:
        if s:
            text = str(text).replace(s, "…")
    return str(text)


def _error(doc, *secrets):
    """SubmitError for an answer that is not "ok", in AcoustID's words without the keys."""
    if not isinstance(doc, dict):
        return SubmitError("AcoustID's answer could not be read.")
    if doc.get("status") == "ok":
        return None
    err = doc.get("error")
    err = err if isinstance(err, dict) else {}
    msg = _scrub(err.get("message") or "no reason given", *secrets)
    hint = {4: " Check the AcoustID application key in Setup.",
            6: " Check the AcoustID user key in Setup."}.get(err.get("code"), "")
    return SubmitError(f"AcoustID refused the submission: {msg}.{hint}")


# --------------------------------------------------------------------------
# submit and check
# --------------------------------------------------------------------------
def submit(tracks, app=None, user=None, sleep=time.sleep) -> list[Track]:
    """Send every sendable track, in batches under 1 MB; each gets its submission id and status.
    Raises SubmitError when nothing could be sent (keys missing, AcoustID refused, no network)."""
    app = app if app is not None else app_key()
    user = user if user is not None else user_key()
    if not app or not user:
        raise SubmitError(" ".join(requirements.notice(i, state=requirements.NOT_SET)
                                   for i, have in (("acoustid", app), (USER, user)) if not have))
    send = [t for t in tracks if t.sendable]
    if not send:
        raise SubmitError("No track has both a fingerprint and a recording ID: nothing to send.")
    todo, sent, first, tries = batches(send, MAX_PAYLOAD), 0, True, 0
    while todo:
        batch = todo.pop(0)
        if not first:
            sleep(MIN_DELAY)
        first = False
        doc = http_post(f"{API}/submit", _fields(batch, app, user))
        err = _error(doc, app, user)
        if err is not None:
            doc = doc if isinstance(doc, dict) else {}
            code = doc["error"].get("code") if isinstance(doc.get("error"), dict) else None
            if doc.get("http") == 413 and len(batch) > 1:   # (too large: smaller batches, as Picard does)
                half = len(batch) // 2
                todo = [batch[:half], batch[half:]] + todo
                continue
            if code in (13, 14) and tries < MAX_ATTEMPTS - 1:      # (busy, too many requests: once more, later)
                tries += 1
                sleep(1.0 * tries)
                todo.insert(0, batch)
                continue
            if sent:
                for t in batch + [x for b in todo for x in b]:
                    t.status = "Not sent: " + str(err).removeprefix("AcoustID refused the submission: ")
                return tracks
            raise err
        for s in doc.get("submissions") or []:
            try:
                t = batch[int(s.get("index"))]
            except (TypeError, ValueError, IndexError):
                continue
            t.submission_id, t.status = s.get("id"), str(s.get("status") or "pending")
            _settle(t, s)
        sent += len(batch)
        tries = 0
    log.info("AcoustID: %d fingerprint(s) sent", sent)
    return tracks


def _settle(t, s):
    res = s.get("response") or s.get("result") or {}
    if isinstance(res, dict) and res.get("id"):
        t.acoustid = str(res["id"])


def check_status(tracks, app=None) -> list[Track]:
    """Ask AcoustID about the submissions still waiting (POST /v2/submission_status: the key goes in
    the body, not the URL; the repeated id fields work the same there)."""
    app = app if app is not None else app_key()
    waiting = {t.submission_id: t for t in tracks if t.submission_id is not None and t.status != "imported"}
    if not waiting:
        return tracks
    fields = [("format", "json"), ("client", app), ("clientversion", _version())] + \
        [("id", str(i)) for i in waiting]
    doc = http_post(f"{API}/submission_status", fields)
    err = _error(doc, app)
    if err is not None:
        raise SubmitError(str(err).replace("refused the submission", "did not say how the submission went"))
    for s in doc.get("submissions") or []:
        t = waiting.get(s.get("id"))
        if t is None:
            try:
                t = waiting.get(int(s.get("id")))
            except (TypeError, ValueError):
                continue
        if t is not None:
            t.status = str(s.get("status") or t.status)
            _settle(t, s)
    return tracks


def summary(tracks) -> str:
    """One line: how many were added, are waiting, were not sent."""
    added = sum(1 for t in tracks if t.status == "imported")
    waiting = sum(1 for t in tracks if t.submission_id is not None and t.status not in ("imported",)
                  and not t.status.startswith("Not sent"))
    left = len(tracks) - added - waiting
    parts = [f"{added} added"] if added else []
    if waiting:
        parts.append(f"{waiting} waiting to be added")
    if left:
        parts.append(f"{left} not sent")
    return ", ".join(parts) or "Nothing sent"
