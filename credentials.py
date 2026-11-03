"""credentials.py - provider tokens and keys, kept out of logs, command lines and exports.

Looked up in this order, the first found wins:
  1. the environment: DISCOGS_TOKEN, TAGMENDER_ACOUSTID_KEY, TAGMENDER_GNUDB_USER, ...
  2. a file only you can read: ~/.config/tagmender/discogs_token, .../acoustid_key, .../gnudb_user,
     and for the optional web search: .../mojeek_key, .../marginalia_key, .../tavily_key
     (written by `tagmender setup`, mode 0600; a file others can read is refused)

Your own MusicBrainz application (client ID and secret: mb_client_id, mb_client_secret) and your
AcoustID user key (acoustid_user_key, for sending fingerprints) are kept the same way. The
MusicBrainz login itself (the OAuth access and refresh tokens) is kept by mb_account.py in
~/.config/tagmender/musicbrainz_login.json, 0600 too (TOKEN_FILES); Log Out deletes it.

They are never passed as command-line arguments, written to the log, or included in a
diagnostics export.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import exclusions as ex

ENV = {"discogs": "DISCOGS_TOKEN", "acoustid": "TAGMENDER_ACOUSTID_KEY",
       "fanart": "TAGMENDER_FANART_KEY", "audiodb": "TAGMENDER_AUDIODB_KEY", "lastfm": "TAGMENDER_LASTFM_KEY",
       "gnudb": "TAGMENDER_GNUDB_USER",
       # the optional web search engines (match_albums.WEB_ENGINES; a profile lists them in order)
       "mojeek": "TAGMENDER_MOJEEK_KEY", "marginalia": "TAGMENDER_MARGINALIA_KEY", "tavily": "TAVILY_API_KEY",
       # your MusicBrainz account (mb_account.py) and sending fingerprints (acoustid_submit.py)
       "mb_client_id": "TAGMENDER_MB_CLIENT_ID", "mb_client_secret": "TAGMENDER_MB_CLIENT_SECRET",
       "acoustid_user": "TAGMENDER_ACOUSTID_USER_KEY"}
FILES = {"discogs": "discogs_token", "acoustid": "acoustid_key",
         "fanart": "fanart_key", "audiodb": "audiodb_key", "lastfm": "lastfm_key", "gnudb": "gnudb_user",
         "mojeek": "mojeek_key", "marginalia": "marginalia_key", "tavily": "tavily_key",
         "mb_client_id": "mb_client_id", "mb_client_secret": "mb_client_secret", "acoustid_user": "acoustid_user_key"}
# saved logins (JSON, not one secret a file): kept 0600 by their own module, checked by doctor
TOKEN_FILES = {"musicbrainz_login": "musicbrainz_login.json"}
# CoverUp's own setting names for the keys it can take from here when Picard.ini has none
COVERUP_KEYS = {"discogs": "discogs_token", "fanart": "fanart_api_key", "audiodb": "audiodb_api_key",
                "lastfm": "lastfm_api_key"}
LABELS = {"discogs": "Discogs personal access token (discogs.com/settings/developers)",
          "acoustid": "AcoustID application key (acoustid.org/new-application)",
          "fanart": "fanart.tv API key, for CoverUp (fanart.tv/get-an-api-key)",
          "audiodb": "TheAudioDB API key, for CoverUp (optional)",
          "lastfm": "Last.fm API key, for CoverUp (optional)",
          "gnudb": "gnudb.org registered e-mail, for disc lookups (gnudb.org; optional)",
          "mojeek": "Mojeek Search API key (mojeek.com/services/search/web-search-api; optional)",
          "marginalia": "Marginalia Search API key (optional; without one the shared \"public\" key)",
          "tavily": "Tavily API key (a paid AI search; optional, used only when a profile lists tavily)",
          "mb_client_id": "MusicBrainz application client ID (musicbrainz.org/account/applications, "
                          "an \"Installed application\"; optional, for collections and ratings)",
          "mb_client_secret": "MusicBrainz application client secret (the same application; optional)",
          "acoustid_user": "AcoustID user key, for sending fingerprints (acoustid.org/api-key; "
                           "not the application key; optional)"}


class CredentialError(RuntimeError):
    pass


def path_of(name: str) -> Path:
    return ex.CONFIG_DIR / FILES[name]


def get(name: str) -> str:
    """The secret, or "" when none is set up. Raises CredentialError for a file that others
    can read (fix: chmod 600)."""
    v = os.environ.get(ENV[name], "").strip()
    if v:
        return v
    p = path_of(name)
    if not p.exists():
        return ""
    mode = p.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialError(f"{p} can be read by other users; run: chmod 600 '{p}'")
    return p.read_text(encoding="utf-8").strip()


def token_file_state(name: str) -> str:
    """A saved login's state: "" (none), "ok" (only you can read it) or "open" (others can read
    it: refused until chmod 600). Never reads what is in it."""
    p = ex.CONFIG_DIR / TOKEN_FILES[name]
    if not p.exists():
        return ""
    return "open" if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) else "ok"


def source(name: str) -> str:
    """Where the secret comes from ("environment", "file", or "") - never the value."""
    if os.environ.get(ENV[name], "").strip():
        return "environment"
    return "file" if path_of(name).exists() else ""


def store(name: str, value: str) -> Path:
    p = path_of(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value.strip() + "\n")
    os.replace(p.with_suffix(".tmp"), p)
    os.chmod(p, 0o600)
    return p
