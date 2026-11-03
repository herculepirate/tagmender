"""connection.py - Test Connection: does each online database answer, and how fast.

One short, polite request to each service TagMender identifies with (MusicBrainz, Discogs,
AcoustID, the Cover Art Archive), with TagMender's own User-Agent and a short timeout. The
answer is one of:

  ok           answered in N ms
  busy         the service is overloaded or limits requests (HTTP 503 or 429): try later
  unreachable  no answer (no network, a name that does not resolve, a timeout, an error page)
  refused      it answered, but refused the key or token you set up
  no_key       it answers; no key is set up, so what needs one is not checked

Keys are read from credentials.py and sent only to the service they belong to; they are
never shown, logged or put in the result. Used by the app's Setup page and `tagmender doctor
--connection`. Nothing here is asked during a run (the run's own requests say when a
service is busy or down: see status()).

  test(name) -> Result         one service
  test_all() -> [Result]       every service, one after the other
  words(result) -> str         "answered in 230 ms", "busy (503)", "unreachable", "key refused"
  status(cache_path) -> dict   {service: "busy" | "down"} from the run's own recent requests
  record(host, error)          note one request's outcome for status() (the covers' requests)
"""
from __future__ import annotations

import http.client
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import credentials
from version import VERSION

# MusicBrainz asks every program for its name, version and a way to reach its makers: the
# project's page, never the user's own name or e-mail
USER_AGENT = f"TagMender/{VERSION} ( https://github.com/herculepirate/tagmender )"
TIMEOUT = 6.0                    # seconds; a test never waits long
SERVICES = ("musicbrainz", "discogs", "acoustid", "coverartarchive")
TITLES = {"musicbrainz": "MusicBrainz", "discogs": "Discogs", "acoustid": "AcoustID",
          "coverartarchive": "Cover Art Archive"}
# a small, fixed lookup each service answers quickly (no search: searches are slower and
# count against limits more)
_MB_URL = "https://musicbrainz.org/ws/2/artist/89ad4ac3-39f7-470e-963a-56509c546377?fmt=json"   # Various Artists
_DISCOGS_ROOT = "https://api.discogs.com/"
_DISCOGS_IDENTITY = "https://api.discogs.com/oauth/identity"
_ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
# a release ID that cannot exist: the archive answers 400 or 404 at once, with no redirect
# to the image host
_CAA_URL = "https://coverartarchive.org/release/00000000-0000-0000-0000-000000000000"
OK, BUSY, UNREACHABLE, REFUSED, NO_KEY = "ok", "busy", "unreachable", "refused", "no_key"


@dataclass
class Result:
    service: str
    state: str                   # OK | BUSY | UNREACHABLE | REFUSED | NO_KEY
    ms: int = 0                  # how long the answer took
    code: int = 0                # the HTTP status, when there was one
    detail: str = ""             # in plain words; never a key

    @property
    def title(self) -> str:
        return TITLES.get(self.service, self.service)


def _ask(url, headers=None, data=None, timeout=TIMEOUT, opener=None):
    """(HTTP status, body bytes, milliseconds). Raises OSError for no answer at all."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                          **(headers or {})})
    open_ = opener or urllib.request.urlopen
    started = time.monotonic()
    try:
        with open_(req, timeout=timeout) as r:
            body = r.read(200_000)
            code = getattr(r, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(200_000) or b""
        except Exception:
            body = b""
        code = exc.code
    return code, body, int(round((time.monotonic() - started) * 1000))


class _BadKey(ValueError):
    """A key that cannot be sent as it is (spaces, line breaks): said without the key."""


def _key(name) -> tuple[str, str]:
    """(the key, "" or why it cannot be used). A key holding spaces, line breaks or other
    control characters is never sent (_BadKey): a second line pasted into its file."""
    try:
        key = credentials.get(name)
    except credentials.CredentialError:
        return "", "the key file can be read by other users (chmod 600 it)"
    if key and any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key):
        raise _BadKey("key refused: the key or token you set up holds spaces or line breaks; set it up again")
    return key, ""


def _by_status(service, code, ms, refused=(), extra=""):
    if code in (429, 503):
        return Result(service, BUSY, ms, code, f"busy ({code}): it limits requests or is overloaded; try later")
    if code in refused:
        return Result(service, REFUSED, ms, code, f"key refused ({code})")
    if 200 <= code < 500:
        return Result(service, OK, ms, code, f"answered in {ms} ms" + (f"; {extra}" if extra else ""))
    return Result(service, UNREACHABLE, ms, code, f"unreachable: it answered with an error ({code})")


def _json(body):
    """The answer's JSON object, or None (an error page, a Wi-Fi login page)."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _service_answer(service, code, body, ms, needs=""):
    """A lookup that must come back as the service's own JSON (needs: a key it holds): a
    refusal (401, 403, 407: a block, a proxy) or any other page is not an answer."""
    if code in (429, 503):
        return _by_status(service, code, ms)
    if code in (401, 403, 407):
        return Result(service, UNREACHABLE, ms, code,
                      f"unreachable: it refused TagMender's requests ({code}; a block or a proxy)")
    data = _json(body) if code == 200 else None
    if data is None or needs and needs not in data:
        return Result(service, UNREACHABLE, ms, code,
                      f"unreachable: the answer was not {TITLES[service]}'s" + (f" ({code})" if code != 200 else ""))
    return Result(service, OK, ms, code, f"answered in {ms} ms")


def _musicbrainz(opener):
    code, body, ms = _ask(_MB_URL, opener=opener)
    return _service_answer("musicbrainz", code, body, ms, needs="id")


def _coverartarchive(opener):
    code, _body, ms = _ask(_CAA_URL, opener=opener)
    return _by_status("coverartarchive", code, ms)


def _discogs(opener):
    token, bad = _key("discogs")
    if not token:
        code, body, ms = _ask(_DISCOGS_ROOT, opener=opener)
        r = _service_answer("discogs", code, body, ms)
        if r.state == OK:
            return Result("discogs", NO_KEY, ms, code,
                          f"answered in {ms} ms; {bad or 'no Discogs token set up, so it is not used to identify'}")
        return r
    code, _body, ms = _ask(_DISCOGS_IDENTITY, headers={"Authorization": f"Discogs token={token}"}, opener=opener)
    return _by_status("discogs", code, ms, refused=(401, 403), extra="token accepted")


def _acoustid(opener):
    key, bad = _key("acoustid")
    # (a lookup with no fingerprint: AcoustID checks the application key first, then says the
    # fingerprint is missing - nothing about any music is sent)
    query = {"format": "json", "duration": "1", "fingerprint": ""}
    if key:
        query["client"] = key
    code, body, ms = _ask(_ACOUSTID_URL + "?" + urllib.parse.urlencode(query), opener=opener)
    if code in (429, 503) or code >= 500:
        return _by_status("acoustid", code, ms)
    try:
        err = (json.loads(body.decode("utf-8", "replace")) or {}).get("error") or {}
    except ValueError:
        return Result("acoustid", UNREACHABLE, ms, code, "unreachable: the answer was not AcoustID's")
    if key and (err.get("code") == 4 or "api key" in str(err.get("message", "")).lower()):
        return Result("acoustid", REFUSED, ms, code, "key refused: AcoustID does not know this application key")
    if err.get("code") == 14 or "rate" in str(err.get("message", "")).lower():
        return Result("acoustid", BUSY, ms, code, "busy: it limits requests; try later")
    if not key:
        return Result("acoustid", NO_KEY, ms, code,
                      f"answered in {ms} ms; {bad or 'no AcoustID application key set up, so fingerprints are not looked up'}")
    return Result("acoustid", OK, ms, code, f"answered in {ms} ms; key accepted")


_TESTS = {"musicbrainz": _musicbrainz, "discogs": _discogs, "acoustid": _acoustid,
          "coverartarchive": _coverartarchive}


def _why(exc) -> str:
    """What went wrong, in words that never hold a key: the network's own reason (no such
    host, refused, timed out) or only the kind of error - an error's text can quote the
    request's headers, and so a token."""
    reason = getattr(exc, "reason", None)
    if isinstance(exc, OSError) and isinstance(reason, (str, OSError)):
        return str(reason)
    return type(exc).__name__


def test(name: str, opener=None) -> Result:
    """One service's answer. opener: urllib.request.urlopen's stand-in (tests)."""
    if name not in _TESTS:
        raise ValueError(f"unknown service {name!r} (use {', '.join(SERVICES)})")
    try:
        return _TESTS[name](opener)
    except _BadKey as exc:
        return Result(name, REFUSED, 0, 0, str(exc))
    except (OSError, ValueError, http.client.HTTPException) as exc:   # no network, no such host, a timeout,
        return Result(name, UNREACHABLE, 0, 0, f"unreachable ({_why(exc)})")    # SSL, a broken answer


def test_all(names=SERVICES, opener=None, pause=0.0) -> list[Result]:
    """Every service, one after the other (pause: seconds between them). One service's
    failure never hides the others' answers."""
    out = []
    for i, n in enumerate(names):
        if i and pause:
            time.sleep(pause)
        try:
            out.append(test(n, opener))
        except Exception as exc:                      # (anything test() did not expect)
            out.append(Result(n, UNREACHABLE, 0, 0, f"unreachable ({type(exc).__name__})"))
    return out


def words(r: Result) -> str:
    """The result in a few words, as the app and doctor show it."""
    return {OK: f"answered in {r.ms} ms", BUSY: f"busy ({r.code})" if r.code else "busy",
            UNREACHABLE: "unreachable", REFUSED: "key refused",
            NO_KEY: f"answered in {r.ms} ms (no key set up)"}.get(r.state, r.state)


# ---- during a run: what the run's own requests found ------------------------------------
# the matcher's provider_health table names hosts; the services they belong to
HOSTS = {"mb": "musicbrainz", "musicbrainz": "musicbrainz", "musicbrainz.org": "musicbrainz",
         "discogs": "discogs", "api.discogs.com": "discogs", "acoustid": "acoustid",
         "api.acoustid.org": "acoustid", "coverartarchive": "coverartarchive",
         "coverartarchive.org": "coverartarchive", "caa": "coverartarchive"}
RECENT = 15 * 60                 # seconds: a failure older than this says nothing about now
# failures in a row before a service counts as down: a cover's request is not retried, so
# one slow image is not enough
DOWN_AFTER = {"coverartarchive": 3}


def status(cache_path, now=None) -> dict:
    """{service: "busy" | "down"} for each service the run's own recent requests could not
    reach (the matcher's provider_health table): "busy" when it asked us to slow down,
    "down" when a request failed after all its retries and nothing has answered since (a
    request is retried for minutes before it counts, so one is enough). {} when all is well."""
    path = Path(cache_path)
    if not path.exists():
        return {}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            rows = db.execute("SELECT host, fails, last_error, last_fail, last_ok FROM provider_health").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    now = time.time() if now is None else now
    out = {}
    for host, fails, err, last_fail, last_ok in rows:
        svc = HOSTS.get(str(host))
        if not svc or not last_fail or (last_ok or 0) >= last_fail or now - last_fail > RECENT:
            continue
        text = str(err or "").lower()
        if "rate" in text or "503" in text or "429" in text or "busy" in text:
            out[svc] = "busy"
        elif re.search(r"\bhttp 4\d\d\b", text):
            continue                    # (it answered, and refused that one request: not down)
        elif (fails or 0) >= DOWN_AFTER.get(svc, 1):
            out[svc] = "down"
    return out


def status_words(st: dict) -> str:
    """One line for the app's status indicator ("" when every service answers)."""
    parts = [f"{TITLES.get(s, s)} is {'busy' if v == 'busy' else 'not answering'}" for s, v in sorted(st.items())]
    return "; ".join(parts)


def record(host: str, error=None, cache_path=None):
    """Note one request's outcome where status() reads it (provider_health, as the matcher
    records its own): error None for an answer, else a few words (never a URL or a key).
    A record never stops the request it is about."""
    import exclusions as ex
    path = Path(cache_path) if cache_path else ex.STATE_DIR / "cache.sqlite"
    now = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(path), timeout=2)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS provider_health (host TEXT PRIMARY KEY, fails INT, "
                       "last_error TEXT, last_fail REAL, last_ok REAL)")
            if error is None:
                db.execute("INSERT INTO provider_health (host, fails, last_error, last_fail, last_ok) "
                           "VALUES (?, 0, '', 0, ?) ON CONFLICT(host) DO UPDATE SET fails=0, last_ok=?",
                           (host, now, now))
            else:
                error = str(error)[:200]
                db.execute("INSERT INTO provider_health (host, fails, last_error, last_fail, last_ok) "
                           "VALUES (?, 1, ?, ?, 0) ON CONFLICT(host) DO UPDATE SET fails=fails+1, "
                           "last_error=?, last_fail=?", (host, error, now, error, now))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        pass
