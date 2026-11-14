"""requirements.py - everything a user may set up, in one list: keys, the MusicBrainz login,
optional programs and Python modules.

Each Entry says what it enables, what stops working without it, where to get it and how to
set it. TagMender bundles no keys: every user brings their own. The app's Setup page, the
"not used: no ... key" notices, `tagmender setup` and `tagmender doctor` all read this list.

  ENTRIES                     every Entry, in the order Setup shows them
  ids()                       their ids
  entry(id)                   one Entry (KeyError for an unknown id)
  status(id)                  "set" | "not_set" | "unsafe" (a key file others can read)  for keys and the login
                              "installed" | "missing"                                     for programs and modules
  source(id)                  where a key comes from: "environment", "file" or ""
  ready(id)                   True when it can be used now
  notice(id, where="any")     one plain sentence saying it is not there, what that stops and where to
                              set it (where: "any", "app" or "cli"); for an unsafe file, how to fix it
  problem(id, where="any")    notice(id) when it is not ready, else ""
  report(where="any")         a list of plain dicts, one per entry, for a page or JSON
  nothing_set_up()            True when no key and no login is set up (first run: show Setup)
  store(id, value)            save a key (0600 file in the config folder)
  remove(id)                  delete a key's file (an environment variable stays)

Nothing here ever returns, prints or logs a key, an e-mail address or a login: only whether one
is there and where it comes from.
"""
from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import dataclass

import credentials
import exclusions as ex

def N_(text):
    """Marks text for the app's translators (tools/i18n_extract.sh reads this file too); the
    app passes it through its tr() where it is shown. The text itself here: no Qt needed."""
    return text


# The app sets this to its tr() (gui/setup.py): notices for where="app" are then in the language
# chosen. The command line keeps the English.
translate = None

# statuses
SET, NOT_SET, UNSAFE, INSTALLED, MISSING = "set", "not_set", "unsafe", "installed", "missing"
# groups
RECOMMENDED, OPTIONAL = "recommended", "optional"
GROUP_TITLES = {RECOMMENDED: N_("Required for best results"), OPTIONAL: N_("Optional")}
KINDS = ("key", "login", "program", "module")


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str                    # key | login | program | module
    title: str                   # "Discogs token"
    enables: str                 # what it is for, a short sentence
    without: str                 # what stops working without it
    url: str                     # where to get it (a page to open)
    group: str = OPTIONAL        # recommended | optional
    off: str = ""                # the notice's opening: "Discogs is not used"
    credential: str = ""         # credentials.py name, for keys
    secret: bool = True          # typed without echo (an e-mail address or a URL is not)
    install: str = ""            # for programs and modules: how to install it
    check: str = ""              # doctor's row name (keys: their title)
    web_search: bool = False     # one of the optional web search engines (off by default)
    get_at: str = N_("get it at")  # the notice's words before the URL

    @property
    def env(self) -> str:
        return credentials.ENV.get(self.credential, "") if self.credential else ""

    @property
    def file(self) -> str:
        return credentials.FILES.get(self.credential, "") if self.credential else ""

    def how(self, where="any") -> str:
        """How to set it, in words: the Setup page, `tagmender setup ID`, the environment."""
        t = _translator(where)
        if self.kind == "key":
            app, cli = t(N_("Setup")), f"tagmender setup {self.id}"
            return {"app": app, "cli": cli}.get(where, t(N_("{app} or with `{cli}`")).format(app=app, cli=cli))
        if self.kind == "login":
            return t(N_("Setup (Log In)"))
        return self.install


def _same(text):
    return text


def _translator(where):
    """The app's tr() for notices shown in the app; the English as written everywhere else."""
    return translate if where == "app" and callable(translate) else _same


def _key(id, title, enables, without, url, off, group=OPTIONAL, secret=True, check="", web_search=False,
         get_at=N_("get it at")):
    return Entry(id, "key", title, enables, without, url, group, off, credential=id, secret=secret,
                 check=check or title, web_search=web_search, get_at=get_at)


ENTRIES: list[Entry] = [
    _key("discogs", N_("Discogs token"),
         N_("Looks albums up on Discogs: pressings, labels, catalogue numbers, styles and Discogs tags."),
         N_("Albums are identified with MusicBrainz only; no Discogs tags or Discogs covers."),
         "https://www.discogs.com/settings/developers", N_("Discogs is not used"), RECOMMENDED),
    _key("acoustid", N_("AcoustID application key"),
         N_("Identifies tracks by their sound (audio fingerprints), for albums with poor or no tags."),
         N_("Albums are identified from their tags, file names and scans only; no fingerprints."),
         "https://acoustid.org/new-application", N_("Fingerprints (AcoustID) are not used"), RECOMMENDED),
    _key("acoustid_user", N_("AcoustID user key"),
         N_("Sends an album's fingerprints to AcoustID (Submit AcoustIDs), so others can identify it too."),
         N_("Submit AcoustIDs is not available. Identifying by fingerprints still works."),
         "https://acoustid.org/api-key", N_("Submitting fingerprints is not available")),
    _key("mb_client_id", N_("MusicBrainz client ID"),
         N_("Your own MusicBrainz application (type \"Installed application\"), needed to log in."),
         N_("You cannot log in to MusicBrainz: no collections and no rating submissions."),
         "https://musicbrainz.org/account/applications", N_("MusicBrainz login is not available"),
         secret=False),
    _key("mb_client_secret", N_("MusicBrainz client secret"),
         N_("The secret of the same MusicBrainz application, needed to log in."),
         N_("You cannot log in to MusicBrainz: no collections and no rating submissions."),
         "https://musicbrainz.org/account/applications", N_("MusicBrainz login is not available")),
    Entry("musicbrainz_login", "login", N_("MusicBrainz login"),
          N_("Your MusicBrainz account: collections, and sending your ratings."),
          N_("Collections and Submit Ratings are not available. Looking albums up needs no login."),
          "https://musicbrainz.org/register", OPTIONAL, N_("Collections and ratings are not available"),
          check="MusicBrainz login"),
    _key("gnudb", N_("gnudb e-mail"),
         N_("Asks gnudb.org (freedb) what others typed in for a disc, a clue for albums still unsure. "
            "gnudb answers registered applications only, so lookups can be refused even with an "
            "address set."),
         N_("No gnudb lookups; albums are identified without them."),
         "https://gnudb.org", N_("gnudb lookups are not used"), secret=False,
         get_at=N_("the address you registered at")),
    _key("fanart", N_("fanart.tv API key"),
         N_("Covers and artist photos from fanart.tv."),
         N_("No covers or artist photos from fanart.tv; the other cover sources still work."),
         "https://fanart.tv/get-an-api-key/", N_("fanart.tv is not used")),
    _key("audiodb", N_("TheAudioDB API key"),
         N_("Covers and artist photos from TheAudioDB (a shared test key is used when you have none)."),
         N_("TheAudioDB answers slowly on the shared test key, or not at all when it is turned off."),
         "https://www.theaudiodb.com", N_("TheAudioDB uses its shared test key (slow)")),
    _key("lastfm", N_("Last.fm API key"),
         N_("Covers from Last.fm."),
         N_("No covers from Last.fm; the other cover sources still work."),
         "https://www.last.fm/api/account/create", N_("Last.fm is not used")),
    _key("mojeek", N_("Mojeek API key"),
         N_("A web search through Mojeek's Search API. Off unless a profile lists it."),
         N_("Web searches skip Mojeek and go to the next engine the profile lists."),
         "https://www.mojeek.com/services/search/web-search-api/", N_("Web search (mojeek) is off"),
         web_search=True),
    _key("marginalia", N_("Marginalia API key"),
         N_("A web search through Marginalia. Off unless a profile lists it."),
         N_("Marginalia uses its shared public key, which is often busy."),
         "https://about.marginalia-search.com/article/api/", N_("Marginalia uses its shared key"),
         web_search=True),
    _key("tavily", N_("Tavily API key"),
         N_("A paid web search service, used only when a profile lists tavily, and never alone."),
         N_("Web searches skip Tavily and go to the next engine the profile lists."),
         "https://tavily.com", N_("Web search (tavily) is off"), web_search=True),
    _key("image_api_key", N_("Image service key"),
         N_("Makes covers (Find Covers… › Make Covers…) with your own image generation service; "
            "Tavily, when set up, finds their reference images."),
         N_("Make Covers… is not available; finding covers works as before."),
         "https://platform.openai.com/api-keys", N_("Generated covers are not available")),
    _key("image_api_url", N_("Image service address"),
         N_("Another image generation service than the built-in default (optional)."),
         N_("The built-in default address is used."), "https://platform.openai.com/docs/api-reference/images",
         N_("The built-in default address is used"), secret=False),
    _key("image_model", N_("Image model"),
         N_("Another image model than the built-in default (optional)."), N_("The built-in default model is used."),
         "https://platform.openai.com/docs/models", N_("The built-in default model is used"), secret=False),
    Entry("fpcalc", "program", N_("fpcalc (Chromaprint)"),
          N_("Makes the audio fingerprints AcoustID looks up and receives."),
          N_("No fingerprints: identifying by sound and Submit AcoustIDs do not work."),
          "https://acoustid.org/chromaprint", RECOMMENDED, N_("Fingerprints (AcoustID) are not used"),
          install="sudo apt install libchromaprint-tools", check="fpcalc"),
    Entry("ffmpeg", "program", N_("ffmpeg"),
          N_("Audio checks, ReplayGain and verifying audio in formats other than FLAC and MP3."),
          N_("No ReplayGain or transcode checks; some formats' audio cannot be verified after a write."),
          "https://ffmpeg.org", RECOMMENDED, N_("Audio checks are not done"),
          install="sudo apt install ffmpeg", check="ffmpeg"),
    Entry("tesseract", "program", N_("Tesseract OCR"),
          N_("Reads scans of the sleeve and disc: catalogue numbers, barcodes and matrix codes."),
          N_("Scans are not read."),
          "https://github.com/tesseract-ocr/tesseract", OPTIONAL, N_("Scans are not read"),
          install="sudo apt install tesseract-ocr", check="tesseract"),
    Entry("pyzbar", "module", N_("pyzbar (with libzbar)"),
          N_("Reads barcodes on scans."),
          N_("Scans are not read."),
          "https://pypi.org/project/pyzbar/", OPTIONAL, N_("Scans are not read"),
          # (the AppImage brings pyzbar itself; only the system's zbar library is missing)
          install="sudo apt install libzbar0" if os.environ.get("APPIMAGE") else "sudo apt install python3-pyzbar",
          check="pyzbar"),
    Entry("discid", "module", N_("python-discid (with libdiscid)"),
          N_("Reads a CD's table of contents from the drive (Look Up CD)."),
          N_("Look Up CD cannot read the drive; a rip log or a pasted table of contents still works."),
          "https://pypi.org/project/discid/", OPTIONAL, N_("Reading a CD from the drive is not available"),
          install="sudo apt install libdiscid0 && pip install --user --break-system-packages discid",
          check="discid"),
    Entry("qt_multimedia", "module", N_("Qt Multimedia"),
          N_("Plays tracks in the app (the player bar)."),
          N_("The player bar is hidden; everything else works."),
          "https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/index.html", OPTIONAL,
          N_("The player is not available"), install="pip install --user --break-system-packages PySide6-Addons",
          check="Qt Multimedia"),
]
_BY_ID = {e.id: e for e in ENTRIES}
_MODULES = {"pyzbar": "pyzbar.pyzbar", "discid": "discid", "qt_multimedia": "PySide6.QtMultimedia"}


def ids() -> list[str]:
    return [e.id for e in ENTRIES]


def entry(id: str) -> Entry:
    return _BY_ID[id]


# ---- status (never a value) ---------------------------------------------------------------
def _key_status(name) -> str:
    try:
        return SET if credentials.get(name) else NOT_SET      # (the value is only tested, never kept)
    except credentials.CredentialError:
        return UNSAFE


def _login_status() -> str:
    return {"": NOT_SET, "ok": SET, "open": UNSAFE}[credentials.token_file_state("musicbrainz_login")]


def _module_found(name) -> bool:
    try:
        importlib.import_module(name)
    except (ImportError, OSError):          # (OSError: the module is there, its C library is not)
        return False
    except Exception:
        return False
    return True


def status(id: str) -> str:
    e = entry(id)
    if e.kind == "key":
        return _key_status(e.credential)
    if e.kind == "login":
        return _login_status()
    if e.kind == "program":
        return INSTALLED if shutil.which(id) else MISSING
    return INSTALLED if _module_found(_MODULES[id]) else MISSING


def source(id: str) -> str:
    """"environment", "file" or "" (a key); "" for everything else."""
    e = entry(id)
    if e.kind == "key":
        return credentials.source(e.credential)
    if e.kind == "login":
        return "file" if credentials.token_file_state("musicbrainz_login") else ""
    return ""


# keys whose service still answers on a shared public key when you have none of your own
SHARED_KEY = ("marginalia", "audiodb")


def _usable(id: str, state: str) -> bool:
    if id in SHARED_KEY:
        return state != UNSAFE                   # (a shared public key needs nothing set up)
    return state in (SET, INSTALLED)


def ready(id: str) -> bool:
    return _usable(id, status(id))


def path_of(id: str):
    """The file a key or the login is kept in (it may not exist); None for programs and modules."""
    e = entry(id)
    if e.kind == "key":
        return credentials.path_of(e.credential)
    if e.kind == "login":
        return ex.CONFIG_DIR / credentials.TOKEN_FILES["musicbrainz_login"]
    return None


# ---- notices ------------------------------------------------------------------------------
# templates (translated whole in the app, then filled in): {off} the notice's opening, {title} the
# item, {how} where to set it, {get_at} {url} where to get it, {install} the install command,
# {path} the key's file
NOTICE_UNSAFE = N_("{off}: the file holding your {title} can be read by other users. Run: chmod 600 '{path}'.")
NOTICE_KEY = N_("{off}: no {title}. Add one in {how} ({get_at} {url}).")
NOTICE_SHARED_SEARCH = N_("{off}: no {title}. Add one in {how} for searches of your own (get it at {url}).")
NOTICE_SHARED_TEST = N_("{off}: no {title}. Add your own in {how} for faster answers (get it at {url}).")
NOTICE_MB_APP = N_("{off}: no {title}. Register an \"Installed application\" at {url} and enter its client ID "
                   "and secret in {how}.")
NOTICE_LOGIN = N_("{off}: you are not logged in to MusicBrainz. Log in from Setup.")
NOTICE_PROGRAM = N_("{off}: {title} is not installed. Install it with: {install}. More at {url}.")


def _fill(t, template, words) -> str:
    """The template in the app's language, filled in; the English one when a translation names a
    placeholder that does not exist."""
    try:
        return t(template).format(**words)
    except (KeyError, IndexError, ValueError):
        return template.format(**words)


def notice(id: str, where: str = "any", state: str | None = None) -> str:
    """One plain sentence: what is not used, because which key, login or program is missing,
    and where to set it. where: "app" (the Setup page), "cli" (`tagmender setup ID`) or "any"
    (both). A file others can read gets how to fix it instead. Never holds a value."""
    e = entry(id)
    state = state or status(id)
    t = _translator(where)
    words = {"off": t(e.off), "title": t(e.title), "how": e.how(where), "get_at": t(e.get_at), "url": e.url,
             "install": e.install}
    if state == UNSAFE:
        return _fill(t, NOTICE_UNSAFE, dict(words, path=path_of(id)))
    if e.kind == "key":
        template = {"marginalia": NOTICE_SHARED_SEARCH, "audiodb": NOTICE_SHARED_TEST,
                    "mb_client_id": NOTICE_MB_APP, "mb_client_secret": NOTICE_MB_APP}.get(id, NOTICE_KEY)
        return _fill(t, template, words)
    if e.kind == "login":
        return _fill(t, NOTICE_LOGIN, words)
    return _fill(t, NOTICE_PROGRAM, words)


def problem(id: str, where: str = "any") -> str:
    """notice(id) when it cannot be used now, else ""."""
    state = status(id)
    return "" if _usable(id, state) else notice(id, where, state)


def report(where: str = "any") -> list[dict]:
    """One dict per entry (no values): id, kind, group, title, enables, without, url, how,
    env, file, secret, web_search, status, source, ready, notice ("" when ready)."""
    out = []
    for e in ENTRIES:
        state = status(e.id)
        ok = _usable(e.id, state)
        out.append({"id": e.id, "kind": e.kind, "group": e.group, "title": e.title, "enables": e.enables,
                    "without": e.without, "url": e.url, "how": e.how(where), "env": e.env, "file": e.file,
                    "secret": e.secret, "web_search": e.web_search, "status": state,
                    "source": source(e.id), "ready": ok, "notice": "" if ok else notice(e.id, where, state)})
    return out


def nothing_set_up() -> bool:
    """True when no key and no login is there (nor a key file others can read): the first run."""
    return all(status(e.id) == NOT_SET for e in ENTRIES if e.kind in ("key", "login"))


# ---- saving -------------------------------------------------------------------------------
def store(id: str, value: str):
    """Save a key, mode 0600; returns its path. ValueError for an entry that is not a key or
    an empty value."""
    e = entry(id)
    if e.kind != "key":
        raise ValueError(f"{e.title} is not a key")
    if not (value or "").strip():
        raise ValueError("nothing to save")
    return credentials.store(e.credential, value)


def remove(id: str) -> bool:
    """Delete a key's file; True when there was one. An environment variable is not changed."""
    e = entry(id)
    if e.kind != "key":
        raise ValueError(f"{e.title} is not a key")
    p = credentials.path_of(e.credential)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


# ---- once a run -----------------------------------------------------------------------------
_told: set = set()


def notice_once(id: str, say=None, where: str = "any") -> str:
    """problem(id), handed to say() the first time in this process only ("" afterwards, and
    when nothing is missing): for tools that would otherwise repeat it for every album."""
    text = problem(id, where)
    if not text or id in _told:
        return ""
    _told.add(id)
    if say:
        say(text)
    return text

