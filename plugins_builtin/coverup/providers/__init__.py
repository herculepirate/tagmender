# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Provider registry. Adding a source = one module with a Provider subclass + one line here."""

from .audiodb import TheAudioDB
from .base import Provider
from .caa import CoverArtArchive
from .discogs import Discogs
from .fanart import FanartTv
from .local import LocalFiles
from .stores import Deezer, ITunes, LastFm
from .websearch import WebSearch
from .wikimedia import Wikimedia

REGISTRY: dict[str, type[Provider]] = {
    cls.ID: cls for cls in (CoverArtArchive, Discogs, Wikimedia, FanartTv, TheAudioDB, ITunes, Deezer, LastFm, WebSearch, LocalFiles)
}

__all__ = ["REGISTRY", "Provider"]
