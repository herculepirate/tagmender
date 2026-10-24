"""credentials.py - provider tokens and keys, kept out of logs, command lines and exports.

Looked up in this order, the first found wins:
  1. the environment: DISCOGS_TOKEN, TAGMENDER_ACOUSTID_KEY
  2. a file only you can read: ~/.config/tagmender/discogs_token, .../acoustid_key
     (written by `tagmender setup`, mode 0600; a file others can read is refused)

They are never passed as command-line arguments, written to the log, or included in a
diagnostics export.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import exclusions as ex

ENV = {"discogs": "DISCOGS_TOKEN", "acoustid": "TAGMENDER_ACOUSTID_KEY",
       "fanart": "TAGMENDER_FANART_KEY", "audiodb": "TAGMENDER_AUDIODB_KEY", "lastfm": "TAGMENDER_LASTFM_KEY"}
FILES = {"discogs": "discogs_token", "acoustid": "acoustid_key",
         "fanart": "fanart_key", "audiodb": "audiodb_key", "lastfm": "lastfm_key"}
# CoverUp's own setting names for the keys it can take from here when Picard.ini has none
COVERUP_KEYS = {"discogs": "discogs_token", "fanart": "fanart_api_key", "audiodb": "audiodb_api_key",
                "lastfm": "lastfm_api_key"}
LABELS = {"discogs": "Discogs personal access token (discogs.com/settings/developers)",
          "acoustid": "AcoustID application key (acoustid.org/new-application)",
          "fanart": "fanart.tv API key, for CoverUp (fanart.tv/get-an-api-key)",
          "audiodb": "TheAudioDB API key, for CoverUp (optional)",
          "lastfm": "Last.fm API key, for CoverUp (optional)"}


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
