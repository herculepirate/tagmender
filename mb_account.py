"""mb_account.py - Your MusicBrainz account, as Picard has it: log in, collections and ratings.

Logging in is OAuth2 for an installed application, with YOUR OWN application: nothing is bundled.
Register one (once) as an "Installed application" at https://musicbrainz.org/account/applications
(or, for the MetaBrainz sign-in Picard 3 uses, on your metabrainz.org account), then enter its
client ID and secret in Setup (or `tagmender setup mb_client_id`). They are kept as credentials.py keeps every key
(the environment, else a 0600 file in ~/.config/tagmender: mb_client_id, mb_client_secret).

The flow is Picard's out-of-band one (picard/oauth.py): the browser opens
  https://<host>/oauth2/authorize?response_type=code&client_id=..&redirect_uri=urn:ietf:wg:oauth:2.0:oob
      &scope=..&access_type=offline   (+ code_challenge, code_challenge_method=S256 on metabrainz.org)
MusicBrainz shows a code; you paste it into TagMender, which exchanges it at /oauth2/token for an
access token and a refresh token, then asks /oauth2/userinfo for your user name. The tokens are
saved in ~/.config/tagmender/musicbrainz_login.json (0600, refused when others can read it),
refreshed when the access token expires, and revoked at /oauth2/revoke and deleted on Log Out.
Tokens and codes are never printed, logged or put in an error message.

With a login (each one an explicit action the app confirms first, saying what is sent):
  collections()                   your release collections (GET /ws/2/collection, every page of 100)
  add_to_collection / remove_from_collection   PUT / DELETE /ws/2/collection/<id>/releases/<mbid;..>
  submit_ratings({recording: stars})           POST /ws/2/rating, the XML Picard sends
  user_genres(release)            your own genres and tags of a release (inc=user-genres+user-tags)
Every MusicBrainz web-service call carries ?client=TagMender-<version>; every request to
musicbrainz.org or metabrainz.org (the sign-in ones too) keeps to 1 a second.

Ratings in files, as Picard keeps them (picard/formats/{id3,vorbis,asf}.py): 0-5 stars,
ID3 POPM with the e-mail of Picard's "Email for saving ratings" (rating_user_email, default
users@musicbrainz.org; 0-255), Vorbis RATING:<e-mail> (or RATING without one; 0.0-1.0), WMA
WM/SharedUserRating (0-99). MP4 and APEv2 have none (Picard keeps none there either).
A rating you set in the inspector is an edit of the plan ("~rating", Picard's name), written by
the executor like any edit (staged, verified, journaled, undoable): plan_rating (plan.apply_edits:
the change and its row); write_rating, which tagio.write calls on the file it has open for a
"~rating" change, then checks with rating_reads_back once the file is saved (the executor's own
read-backs skip "~rating", a lenient field; its crash recovery calls rating_reads_back too).
save_rating is a standalone helper: write_rating on a file at a path, saved."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import quoteattr

import credentials as cr
import exclusions as ex
import requirements as rq
from version import VERSION

OOB_URI = "urn:ietf:wg:oauth:2.0:oob"
# The two sign-in servers: MusicBrainz's own (where its applications page is), and MetaBrainz's,
# which Picard 3 uses (PKCE and "musicbrainz:" scopes, picard/const METABRAINZ_OAUTH_*)
PROVIDERS = {
    "musicbrainz": {"host": "musicbrainz.org", "pkce": False,
                    "scopes": "profile tag rating collection",    # (tag: user_genres reads your tags)
                    "register": "https://musicbrainz.org/account/applications",
                    "label": "musicbrainz.org"},
    "metabrainz": {"host": "metabrainz.org", "pkce": True,
                   "scopes": "profile musicbrainz:tag musicbrainz:rating musicbrainz:collection",
                   "register": "https://metabrainz.org (your account's applications)",
                   "label": "metabrainz.org (PKCE sign-in)"},
}
DEFAULT_PROVIDER = "musicbrainz"
HOSTS = {p["host"] for p in PROVIDERS.values()}
WS = "https://musicbrainz.org/ws/2"
CLIENT = f"TagMender-{VERSION}"
USER_AGENT = f"TagMender/{VERSION} ( https://github.com/herculepirate/tagmender )"
MIN_INTERVAL = 1.0                     # MusicBrainz: one request a second
PAGE = 100                             # collections in one request (the most MusicBrainz sends)
MAX_COLLECTIONS = 10000                # (a stop, should a server keep counting)
BATCH = 400                            # releases in one collection request (as Picard)
TOKEN_FILE = "musicbrainz_login.json"
MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# your application's client ID and secret, kept by credentials.py
SECRETS = {"mb_client_id": ("TAGMENDER_MB_CLIENT_ID", "mb_client_id",
                            "MusicBrainz application client ID (musicbrainz.org/account/applications, "
                            "an \"Installed application\")"),
           "mb_client_secret": ("TAGMENDER_MB_CLIENT_SECRET", "mb_client_secret",
                                "MusicBrainz application client secret (the same application)")}


def _register():
    """Make credentials.py know the two names (until it lists them itself)."""
    for name, (env, fname, label) in SECRETS.items():
        cr.ENV.setdefault(name, env)
        cr.FILES.setdefault(name, fname)
        cr.LABELS.setdefault(name, label)


_register()


class AccountError(RuntimeError):
    """Something a person can act on; the message is plain and never holds a token."""


class NotLoggedIn(AccountError):
    pass


# ---- HTTP (stubbed in tests: Account(http=...)) ----------------------------------------------
def http_call(method, url, headers, body=None, timeout=30):
    """(status, body bytes). An HTTP error status comes back as one; no answer at all raises."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return e.code, data
    except (urllib.error.URLError, OSError) as e:
        raise AccountError(f"MusicBrainz could not be reached ({getattr(e, 'reason', e)}).") from None


_lock = threading.Lock()
_last = [0.0]
_token_lock = threading.Lock()         # (one refresh at a time: a refresh token may be good only once)


def _throttle():
    with _lock:
        wait = _last[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _error_text(status, data, sent=()) -> str:
    """The server's reason, in words; never what was sent (sent: the code, tokens and secret
    of that request, taken out wherever the answer echoes them) nor anything token-like."""
    try:
        got = json.loads(data or b"{}")
        why = got.get("error_description") or got.get("error") or ""
    except (ValueError, AttributeError):
        why = ""
    why = str(why)
    for bit in sorted({str(b) for b in sent if b and len(str(b)) >= 3}, key=len, reverse=True):
        why = why.replace(bit, "…")
    why = re.sub(r"[A-Za-z0-9_\-]{24,}", "…", why)[:200]           # (the backstop: anything token-like)
    return f"{why} (HTTP {status})" if why else f"HTTP {status}"


# ---- the saved login (0600) -------------------------------------------------------------------
def token_path():
    return ex.CONFIG_DIR / TOKEN_FILE


def _load() -> dict:
    p = token_path()
    if not p.exists():
        return {}
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise cr.CredentialError(f"{p} can be read by other users; run: chmod 600 '{p}'")
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(data: dict):
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _forget():
    token_path().unlink(missing_ok=True)


class Account:
    """Your MusicBrainz login. One instance per window (it holds the PKCE verifier between
    authorize_url() and exchange()). Every method that talks to MusicBrainz blocks: call it on
    a worker (gui.work.start)."""

    def __init__(self, http=None, provider=None, clock=time.time):
        self.http = http or http_call
        self.clock = clock
        self._verifier = None
        saved = self._saved()
        self.provider = provider or saved.get("provider") or DEFAULT_PROVIDER
        if self.provider not in PROVIDERS:
            self.provider = DEFAULT_PROVIDER

    # -- state
    def _saved(self) -> dict:
        try:
            return _load()
        except cr.CredentialError:
            return {}

    @property
    def server(self) -> dict:
        return PROVIDERS[self.provider]

    def logged_in(self) -> bool:
        s = self._saved()
        return bool(s.get("refresh_token") and s.get("username"))

    @property
    def username(self) -> str:
        return self._saved().get("username", "") if self.logged_in() else ""

    def client(self) -> tuple[str, str]:
        try:
            cid, secret = cr.get("mb_client_id"), cr.get("mb_client_secret")
        except cr.CredentialError as e:                # (a key file others can read: the chmod hint)
            raise AccountError(str(e)) from None
        if not cid or not secret:
            missing = "mb_client_id" if not cid else "mb_client_secret"
            if self.provider == DEFAULT_PROVIDER or "musicbrainz.org" in PROVIDERS[self.provider]["register"]:
                raise AccountError(rq.notice(missing, state=rq.NOT_SET))
            raise AccountError(f"{rq.entry(missing).off}: no {rq.entry(missing).title}. Register an "
                               "\"Installed application\" at " + PROVIDERS[self.provider]["register"]
                               + " and enter its client ID and secret in Setup.")
        return cid, secret

    def _oauth(self, path, host=None):
        return f"https://{host or self.server['host']}{path}"

    def login_host(self) -> str:
        """The server the saved login came from (its refresh and revoke go there), else this one's."""
        return saved_host(self._saved(), self.server)

    def _form(self, path, params, secret_fields=("code", "refresh_token", "client_secret",
                                                 "code_verifier"), host=None) -> dict:
        sent = [params.get(k) for k in secret_fields]
        _throttle()
        status, data = self.http("POST", self._oauth(path, host), {
            "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"}, urllib.parse.urlencode(params).encode())
        if status == 400:
            raise _Refused(_error_text(status, data, sent))
        if status != 200:
            raise AccountError(f"MusicBrainz refused: {_error_text(status, data, sent)}.")
        try:
            return json.loads(data)
        except ValueError:
            raise AccountError("MusicBrainz sent an answer TagMender could not read.") from None

    # -- log in
    def authorize_url(self) -> str:
        """The page to open in the browser: MusicBrainz asks whether TagMender may use your account."""
        cid, _secret = self.client()
        params = {"response_type": "code", "client_id": cid, "redirect_uri": OOB_URI,
                  "scope": self.server["scopes"], "access_type": "offline"}
        if self.server["pkce"]:
            self._verifier = _b64url(secrets.token_bytes(32))
            params["code_challenge_method"] = "S256"
            params["code_challenge"] = _b64url(hashlib.sha256(self._verifier.encode("ascii")).digest())
        return self._oauth("/oauth2/authorize") + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

    def exchange(self, code: str) -> str:
        """The code MusicBrainz showed, exchanged for tokens (saved 0600); your user name."""
        code = (code or "").strip()
        if not code:
            raise AccountError("Paste the code MusicBrainz showed after you allowed access.")
        cid, secret = self.client()
        params = {"grant_type": "authorization_code", "code": code, "client_id": cid,
                  "client_secret": secret, "redirect_uri": OOB_URI}
        if self.server["pkce"]:
            if not self._verifier:
                raise AccountError("Start the login again: this code belongs to another login page.")
            params["code_verifier"] = self._verifier
        try:
            got = self._form("/oauth2/token", params)
        except _Refused as e:
            raise AccountError(f"MusicBrainz did not accept that code: {e}. Log in again for a new one.") from None
        if not got.get("access_token") or not got.get("refresh_token"):
            raise AccountError("MusicBrainz sent no login in its answer.")
        login = {"provider": self.provider, "host": self.server["host"], "scopes": self.server["scopes"],
                 "access_token": got["access_token"], "refresh_token": got["refresh_token"],
                 "expires": int(self.clock() + int(got.get("expires_in") or 3600) - 60)}
        self._verifier = None
        login["username"] = self._userinfo(login["access_token"])
        _save(login)
        return login["username"]

    def _userinfo(self, token) -> str:
        _throttle()
        status, data = self.http("GET", self._oauth("/oauth2/userinfo"), {
            "User-Agent": USER_AGENT, "Accept": "application/json", "Authorization": "Bearer " + token}, None)
        if status != 200:
            raise AccountError(f"MusicBrainz did not say who you are: {_error_text(status, data, [token])}.")
        try:
            got = json.loads(data)
            return str(got.get("username") or got["sub"])
        except (ValueError, KeyError, TypeError):
            raise AccountError("MusicBrainz did not say who you are.") from None

    def access_token(self, force=False, refused=None) -> str:
        """A valid access token, refreshed when it expired (a new refresh token is kept).
        force: refresh it even though it has not expired - unless the token MusicBrainz just
        refused (refused) was already replaced by another worker's refresh. One refresh at a
        time: two at once would send the same refresh token twice, and a server that hands
        out a new one each time refuses the second (which would log you out)."""
        with _token_lock:
            s = self._saved()
            if not s.get("refresh_token"):
                raise NotLoggedIn(rq.notice("musicbrainz_login", state=rq.NOT_SET))
            fresh = s.get("access_token") and self.clock() < float(s.get("expires") or 0)
            if fresh and (not force or (refused is not None and s["access_token"] != refused)):
                return s["access_token"]
            if s.get("provider") in PROVIDERS:          # (another window may have logged in elsewhere)
                self.provider = s["provider"]
            cid, secret = self.client()
            try:                                       # (refreshed where the token came from)
                got = self._form("/oauth2/token", {"grant_type": "refresh_token",
                                                   "refresh_token": s["refresh_token"],
                                                   "client_id": cid, "client_secret": secret},
                                 host=saved_host(s, self.server))
            except _Refused:                           # (RFC 6749: no retry helps; as Picard, logged out)
                _forget()
                raise NotLoggedIn("MusicBrainz no longer accepts your login. Log in again.") from None
            if not got.get("access_token"):
                raise AccountError("MusicBrainz sent no access token.")
            s["access_token"] = got["access_token"]
            s["expires"] = int(self.clock() + int(got.get("expires_in") or 3600) - 60)
            if got.get("refresh_token"):
                s["refresh_token"] = got["refresh_token"]
            _save(s)
            return s["access_token"]

    def logout(self) -> bool:
        """Your login revoked on MusicBrainz (the whole grant: the refresh token) and deleted here.
        True when MusicBrainz confirmed; the login is deleted from this computer either way."""
        s = self._saved()
        ok = False
        try:
            if s.get("refresh_token"):
                cid, secret = self.client()
                host = saved_host(s, self.server)
                _throttle()
                status, _data = self.http("POST", f"https://{host}/oauth2/revoke", {
                    "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
                    urllib.parse.urlencode({"token": s["refresh_token"], "client_id": cid,
                                            "client_secret": secret}).encode())
                ok = status == 200
        except AccountError:
            ok = False
        finally:
            _forget()
        return ok

    # -- the web service
    def _ws(self, method, path, query=None, body=None, ctype=None, auth=True):
        q = dict(query or {})
        if method != "GET":
            q["client"] = CLIENT
        else:
            q.setdefault("fmt", "json")
        url = f"{WS}{path}?" + urllib.parse.urlencode(q, safe="+;")
        force, refused, busy = False, None, False
        while True:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if ctype:
                headers["Content-Type"] = ctype
            token = None
            if auth:
                token = self.access_token(force=force, refused=refused)
                headers["Authorization"] = "Bearer " + token
            _throttle()
            status, data = self.http(method, url, headers, body)
            if status == 401 and auth and not force:
                force, refused = True, token           # (expired early: refreshed once)
                continue
            if status == 503 and not busy:             # (busy: once more, with the same login)
                busy = True
                time.sleep(MIN_INTERVAL)
                continue
            break
        if status == 401:
            raise NotLoggedIn("MusicBrainz did not accept your login. Log in again.")
        if status not in (200, 201):
            raise AccountError(f"MusicBrainz refused: {_error_text(status, data)}.")
        if method == "GET":
            try:
                return json.loads(data)
            except ValueError:
                raise AccountError("MusicBrainz sent an answer TagMender could not read.") from None
        return data

    def collections(self) -> list[dict]:
        """Your release collections: [{"id", "name", "count", "editor"}], by name. Every page of
        them (PAGE a request, offset onward, until collection-count are in)."""
        seen, offset = [], 0
        while True:
            got = self._ws("GET", "/collection", collections_query(offset))
            page = got.get("collections") or []
            seen += page
            offset += len(page)
            total = int(got.get("collection-count") or 0)
            if not page or offset >= total or offset >= MAX_COLLECTIONS:
                break
        out, ids = [], set()
        for c in seen:
            if c.get("entity-type") == "release" and c.get("id") and c["id"] not in ids:
                ids.add(c["id"])
                out.append({"id": c["id"], "name": c.get("name") or "",
                            "count": int(c.get("release-count") or 0), "editor": c.get("editor") or ""})
        return sorted(out, key=lambda c: c["name"].casefold())

    def release_collections(self, release: str) -> set:
        """Ids of your collections that hold the release (inc=user-collections)."""
        _check(release)
        got = self._ws("GET", f"/release/{release}", {"inc": "user-collections"})
        return {c["id"] for c in got.get("collections") or [] if c.get("id")}

    def add_to_collection(self, collection: str, releases) -> int:
        return self._collection("PUT", collection, releases)

    def remove_from_collection(self, collection: str, releases) -> int:
        return self._collection("DELETE", collection, releases)

    def _collection(self, method, collection, releases) -> int:
        n = 0
        for path in collection_paths(collection, releases):
            self._ws(method, path, body=b"" if method == "PUT" else None)
            n += path.rsplit("/", 1)[1].count(";") + 1
        return n

    def submit_ratings(self, ratings: dict) -> int:
        """{recording MBID: 0-5 stars} sent as Picard sends them; how many."""
        if not ratings:
            return 0
        self._ws("POST", "/rating", body=ratings_xml(ratings).encode("utf-8"),
                 ctype="application/xml; charset=utf-8")
        return len(ratings)

    def user_genres(self, release: str) -> dict:
        """Your own genres and tags of a release: {"genres": [...], "tags": [...]} (most voted first)."""
        _check(release)
        got = self._ws("GET", f"/release/{release}", {"inc": "user-genres+user-tags"})

        def names(key):
            return [t["name"] for t in sorted(got.get(key) or [], key=lambda t: -int(t.get("count") or 1))
                    if t.get("name")]
        return {"genres": names("user-genres"), "tags": names("user-tags")}


def saved_host(saved: dict, default: dict) -> str:
    """The host a saved login came from (its provider's, else the host saved with it)."""
    p = PROVIDERS.get(saved.get("provider"))
    return p["host"] if p else (saved.get("host") if saved.get("host") in HOSTS else default["host"])


def collections_query(offset=0) -> dict:
    q = {"limit": PAGE}
    if offset:
        q["offset"] = offset
    return q


def collections_requests(release) -> list[str]:
    """What Collections… reads, word for word: the first page of your collections, and which
    of them hold the release (more pages of collections follow when you have over PAGE)."""
    _check(release)
    q = urllib.parse.urlencode({**collections_query(), "fmt": "json"})
    return [f"GET {WS}/collection?{q}",
            f"GET {WS}/release/{release}?" + urllib.parse.urlencode({"inc": "user-collections", "fmt": "json"})]


def refresh_request(host) -> str:
    """The request sent first when the login expired (the new access token comes back)."""
    return (f"POST https://{host}/oauth2/token\n  form fields: grant_type=refresh_token, "
            "refresh_token, client_id, client_secret")


class _Refused(AccountError):
    """The token endpoint's 400: the code or refresh token is no good."""


def _check(mbid):
    if not MBID.match(str(mbid or "")):
        raise AccountError(f"{mbid!r} is not a MusicBrainz ID.")


def collection_paths(collection, releases, batch=BATCH) -> list[str]:
    """/collection/<id>/releases/<mbid;mbid..> in batches of 400, as Picard sends them."""
    _check(collection)
    ids = list(dict.fromkeys(releases))
    for r in ids:
        _check(r)
    return [f"/collection/{collection}/releases/" + ";".join(ids[i:i + batch]) for i in range(0, len(ids), batch)]


def collection_requests(collection, releases, add=True) -> list[str]:
    """What a collection change sends, word for word: ["PUT https://...?client=TagMender-x", ...]."""
    method = "PUT" if add else "DELETE"
    return [f"{method} {WS}{p}?client={CLIENT}" for p in collection_paths(collection, releases)]


def ratings_xml(ratings: dict) -> str:
    """Picard's _xml_ratings: each recording's stars x 20 (0 removes your rating)."""
    rows = "".join("<recording id=%s><user-rating>%d</user-rating></recording>" % (quoteattr(r), int(s) * 20)
                   for r, s in ratings.items())
    for r, s in ratings.items():
        _check(r)
        if not 0 <= int(s) <= 5:
            raise AccountError(f"A rating is 0 to 5 stars, not {s}.")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<metadata xmlns="http://musicbrainz.org/ns/mmd-2.0#"><recording-list>%s</recording-list></metadata>'
            % rows)


def ratings_request() -> str:
    return f"POST {WS}/rating?client={CLIENT}"


# ---- ratings in files (Picard's mapping) -----------------------------------------------------
RATING_STEPS = 6                        # 0..5 stars (picard/const RATING_STEPS)
DEFAULT_EMAIL = "users@musicbrainz.org"
_VORBIS_BAD = re.compile("([^\x20-\x7d]|=)")


def rating_email() -> str:
    """Picard's "Email for saving ratings" (an enabled option profile's, else [setting]), else its default."""
    try:
        import tagger_settings as pc
        cp = pc._read(pc.ini_path())
        if cp is not None:
            found, v = pc._from_profiles(cp, "rating_user_email")
            if found:
                return pc._qt_text(str(v)).strip()
            if cp.has_option("setting", "rating_user_email"):
                return pc._qt_text(cp.get("setting", "rating_user_email")).strip()
    except Exception:
        pass
    return DEFAULT_EMAIL


def stars(value):
    """0-5 from an edit's value (["4"], "4", 4), None for none."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(round(float(str(value).strip())))
    except ValueError:
        raise AccountError(f"A rating is 0 to 5 stars, not {value!r}.") from None
    if not 0 <= n <= RATING_STEPS - 1:
        raise AccountError(f"A rating is 0 to 5 stars, not {value!r}.")
    return n


def _vorbis_key(email):
    email = _VORBIS_BAD.sub("", email or "")
    return f"rating:{email}" if email else "rating"


def holds_rating(path) -> bool:
    import tagio
    try:
        return tagio.family(tagio.open_file(path)) in ("id3", "vorbis", "asf")
    except Exception:
        return False


def read_rating(path_or_file, email=None):
    """The file's rating in stars (0-5), as Picard reads it, or None."""
    import tagio
    f = tagio.open_file(path_or_file) if isinstance(path_or_file, (str, os.PathLike)) else path_or_file
    email = rating_email() if email is None else email
    try:
        fam = tagio.family(f)
    except tagio.Unsupported:
        return None
    tags = f.tags
    if tags is None:
        return None
    try:
        if fam == "id3":
            for fr in tags.getall("POPM"):
                if fr.email == email:
                    return int(round(fr.rating / 255.0 * (RATING_STEPS - 1)))
        elif fam == "vorbis":
            want = _vorbis_key(email)
            for k, v in tags.items():
                if k.lower() == want.lower() and v:
                    return int(round(float(v[0]) * (RATING_STEPS - 1)))
        elif fam == "asf":
            v = tags.get("WM/SharedUserRating")
            if v:
                return int(round(int(str(v[0].value)) / 99 * (RATING_STEPS - 1)))
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def write_rating(f, value, email=None):
    """The rating (0-5, None removes it) set on the opened file f (tagio.open_file), as Picard
    saves it; the caller saves f. Raises tagio.Unsupported for MP4 and APEv2."""
    import tagio
    n = stars(value)
    email = rating_email() if email is None else email
    fam = tagio.family(f)
    if f.tags is None:
        f.add_tags()
    tags = f.tags
    if fam == "id3":
        from mutagen.id3 import POPM
        count = 0
        for fr in tags.getall("POPM"):
            if fr.email == email:
                count = getattr(fr, "count", 0) or 0
        for key in [k for k in tags if k.startswith("POPM:") and tags[k].email == email]:
            del tags[key]
        if n is not None:
            tags.add(POPM(email=email, rating=int(round(n * 255 / (RATING_STEPS - 1))), count=count))
    elif fam == "vorbis":
        want = _vorbis_key(email)
        for k in [k for k in tags.keys() if k.lower() == want.lower()]:
            del tags[k]
        if n is not None:
            tags[want.upper()] = [str(float(n) / (RATING_STEPS - 1))]   # (as Picard: upper-cased)
    elif fam == "asf":
        from mutagen.asf import ASFDWordAttribute
        if "WM/SharedUserRating" in tags:              # (ASFTags.pop takes no default)
            del tags["WM/SharedUserRating"]
        if n is not None:
            tags["WM/SharedUserRating"] = [ASFDWordAttribute(n * 99 // (RATING_STEPS - 1))]
    else:
        raise tagio.Unsupported("the MusicBrainz tag mapping keeps no rating in this kind of file (MP4 or APEv2)")


def plan_rating(fe, value):
    """A file entry of a plan (fe: src, changes, diff) with your rating: value as an edit holds
    it (["4"], None to remove, plan.KEEP). changes["~rating"] = ["4"] | None, and a "rating" row."""
    import plan as planmod
    src = fe["src"]
    try:
        had = read_rating(src)
    except Exception:
        had = None
    before = [] if had is None else [str(had)]
    changes = dict(fe.get("changes") or {})
    rows = [r for r in fe.get("diff") or [] if r.get("field") != "rating"]
    changes.pop("~rating", None)
    if value == planmod.KEEP:
        if before:
            rows.append({"field": "rating", "before": before, "after": before, "action": "kept (your edit)",
                         "by": planmod.BY_YOU})
    elif not holds_rating(src):
        pass                                           # (MP4, APEv2: no place for it)
    else:
        n = stars(value)
        after = [] if n is None else [str(n)]
        if after != before:
            changes["~rating"] = after or None
            rows.append({"field": "rating", "before": before, "after": after,
                         "action": "removed" if not after else ("changed" if before else "added"),
                         "by": planmod.BY_YOU})
        elif before:
            rows.append({"field": "rating", "before": before, "after": before, "action": "kept (your edit)",
                         "by": planmod.BY_YOU})
    fe["changes"], fe["diff"] = changes, rows
    return fe


def album_ratings(album: dict, edit: dict | None = None) -> list[dict]:
    """What "Submit Ratings" would send for an album of a plan: [{"file", "title", "recording",
    "stars"}] for each file with a recording ID and a rating: your edit's (as plan.apply_edits
    takes it: a file's own edit over the album's, matched by real path), else - with no edit of
    the rating, or one that keeps it - the file's. A rating your edit removes is not sent (the
    file will have none; MusicBrainz keeps yours until you send 0 stars there)."""
    import plan as planmod
    import tagio
    edit = edit or {}
    per_file = {os.path.realpath(k): v for k, v in (edit.get("file_tags") or {}).items()}
    out = []
    for fe in album.get("files") or []:
        src = fe["src"]
        mine = dict(edit.get("tags") or {}, **(per_file.get(os.path.realpath(src)) or {}))
        try:
            tags = tagio.read(src)
        except Exception:
            tags = {}
        planned = fe.get("changes") or {}
        rec = (planned.get("musicbrainz_recordingid") or tags.get("musicbrainz_recordingid") or [""])[0]
        title = (planned.get("title") or tags.get("title") or [os.path.basename(src)])[0]
        value = mine.get("~rating", planmod.KEEP)
        if value == planmod.KEEP:
            try:
                n = read_rating(src)
            except Exception:
                n = None
        else:
            n = stars(value)                           # (None or []: removed - nothing to send)
        if n is not None and MBID.match(str(rec)):
            out.append({"file": src, "title": str(title), "recording": str(rec), "stars": n})
    return out


# ---- a rating on a file at a path: written and saved, or checked after a write -----------------
def save_rating(path, value, email=None):
    """write_rating on the file at path, saved (a standalone helper: tagio.write calls write_rating
    on the file it already has open)."""
    import tagio
    f = tagio.open_file(path)
    write_rating(f, value, email)
    if tagio.family(f) == "id3":
        tagio.save_id3(f, f.tags)                      # (the tag's own version kept, as tagio writes it)
    else:
        f.save()


def rating_reads_back(path, value, email=None) -> bool:
    """Whether the file at path holds the rating a "~rating" change wrote (["4"], None: none).
    tagio.write checks it after saving; executor._recover checks it for a file it finds written."""
    try:
        return read_rating(path, email) == stars(value)
    except Exception:
        return False
