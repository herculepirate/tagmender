"""gui/profile_editor.py - The Profile editor: every setting of one profile as grouped inset lists.

The editor reads top to bottom in the order the work happens: where music comes from (Source),
how albums are identified, what is written (Tags, Artwork), where files go (Target), what
happens to albums nothing identified, and what watched folders do (Automation).

SETTINGS lists each setting once: its key in the profile, label, footnote, control, the group
it sits in, when it is shown or greyed out (and why). Everything else - change markers, Restore
Defaults, the presets, the search field, problems shown under their row, the naming example -
works from that one list.

  ProfileEditor.load(raw, full, defaults)   show a profile (its file, its values, the defaults)
  ProfileEditor.write(data)                 put what was changed into the profile's data
  ProfileEditor.problems()                  what would stop it saving, by row
  ProfileEditor.apply_preset(key)           Typical Library, Collector, Keep My Files as They Are
  ProfileEditor.restore_defaults(section)   the defaults for a section (None: everything)
  ProfileEditor.search(text)                show only the rows whose words hold the text
"""
from __future__ import annotations

import copy
import html
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QRadioButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import naming
import profiles as pf

from . import sheets
from .common import _gui_prefs, _set_gui_pref
from .i18n import N_, tr
from .rows import (Disclosure, FolderField, Group, ListField, ListSheet, Popup, Row, SectionFold, Segmented, Stepper,
                   Switch, caption)
from .widgets import FlowLayout

FOLDED_PREF = "profile_sections_folded"              # gui.json: the sections folded the last time

# ---- the words each choice shows (sentence case; the key behind it is saved) ------------------
OPERATION_WORDS = {"in_place": N_("Fix in place"), "copy": N_("Copy into the library"),
                   "move": N_("Move into the library")}
OPERATION_SHORT = {"in_place": N_("Fix in place"), "copy": N_("Copy"), "move": N_("Move")}
SOURCE_WORDS = {"musicbrainz_only": N_("MusicBrainz only"), "musicbrainz_preferred": N_("MusicBrainz preferred"),
                "discogs_preferred": N_("Discogs preferred"), "discogs_only": N_("Discogs only")}
UNRESOLVED_WORDS = {"leave": N_("Leave alone"), "mark": N_("Mark Unresolved"), "move": N_("Move aside")}
LEVEL_WORDS = {"basic": N_("Basic"), "enthusiast": N_("Enthusiast"), "audiophile": N_("Audiophile")}
NAMING_WORDS = {"tagmender": N_("Short names (initials)"), "standard": N_("Standard"),
                "artist-album-track": N_("Artist / Year - Album / 01 - Title"),
                "singleton": N_("Singles"), "classical": N_("Classical, by composer"), "all-music": N_("All music"),
                "various-artists": N_("Various artists"), "classical-performer": N_("Classical, by performer")}
# the cover sources, each on or off and in the order they are asked
ART_SOURCE_WORDS = {"caa": N_("Cover Art Archive (MusicBrainz)"), "discogs": N_("Discogs"),
                    "coverup": N_("CoverUp: fanart.tv, TheAudioDB, Last.fm, shops"),
                    "web": N_("Shops and web image search"), "local": N_("The album's own scans")}
ART_SOURCE_CHOICES = (
    (N_("CoverUp, then Cover Art Archive, then Discogs"), ["coverup", "caa", "discogs"]),
    (N_("Cover Art Archive, then Discogs, then CoverUp"), ["caa", "discogs", "coverup"]),
    (N_("CoverUp only (all its sources)"), ["coverup"]),
    (N_("MusicBrainz: Cover Art Archive"), ["caa"]),
    (N_("Discogs"), ["discogs"]),
    (N_("MusicBrainz, then Discogs"), ["caa", "discogs"]),
    (N_("Web: shops and web image search"), ["web"]),
    (N_("The album's own scans only"), ["local"]),
)
AUTO_ON_OFF = ((None, N_("Automatic")), (True, N_("On")), (False, N_("Off")))
DETAIL_CHOICES = ((True, N_("On")), (False, N_("Off")), ("audiophile", N_("At the Audiophile level")),
                  ("tagger", N_("Same as my existing tagger")))
WEB_SOURCE_WORDS = {"deezer": N_("Deezer"), "itunes": N_("Apple (iTunes)"), "bandcamp": N_("Bandcamp"),
                    "wikipedia": N_("Wikipedia")}
ENGINE_WORDS = {"mojeek": N_("Mojeek"), "marginalia": N_("Marginalia"), "tavily": N_("Tavily (paid AI search)")}
FORMAT_WORDS = {f: f.upper() for f in pf.LOSSLESS + pf.LOSSY}
CHOOSE = "__choose__"                                     # a pop-up's "Choose…": opens a list sheet

# ---- sections and groups (in reading order) ---------------------------------------------------
SECTIONS = (
    ("general", N_("General"), ""),
    ("source", N_("Source"), N_("Which folders are read, and which files in them.")),
    ("identification", N_("Identification"), N_("How each album is matched to a release in the music databases.")),
    ("tags", N_("Tags"), N_("What is written into the files' tags.")),
    ("artwork", N_("Artwork"), N_("Front covers and artist photos: where they come from and where they go.")),
    ("target", N_("Target"), N_("Where the files end up and what they are called.")),
    ("unresolved", N_("Albums nothing identified"), N_("What happens to an album no database can identify for sure.")),
    ("automation", N_("Automation"), N_("What happens to albums dropped into this profile's watched folders.")),
)
# group id -> (section, title, footnote, advanced)
GROUPS = {
    "general": ("general", "", "", False),
    "folders": ("source", N_("Folders"), N_("Which of them are watched for new music is chosen on the Watched folders page."), False),
    "audio": ("source", N_("Audio files"), N_("Files in other formats are left where they are. Nothing is converted."), False),
    "source_adv": ("source", "", "", True),
    "databases": ("identification", N_("Databases"), N_("Partial: the album is known but not its exact release, so only the details that are certain "
                    "are written. Partial albums always wait for your review."), False),
    "evidence": ("identification", N_("Evidence"),
                 N_("Extra clues for albums whose tags and file names are not enough. Each one sends only what "
                    "it names, and nothing is sent to an account."), False),
    "releases": ("identification", N_("Preferred releases"),
                 N_("When several pressings of an album fit equally well, these decide which one is used."), False),
    "ident_adv": ("identification", "", "", True),
    "written": ("tags", N_("What is written"), "", False),
    "audiophile": ("tags", N_("Extras at the Audiophile level"), N_("Only when the tagging level is Audiophile."),
                   False),
    "genres": ("tags", N_("Genres"), "", False),
    "artists": ("tags", N_("Artists and languages"),
                N_("Automatic: what your existing tagger does, when its settings are used (Tags › Advanced), "
                   "else TagMender's default."), False),
    "credits": ("tags", N_("Credits"), "", False),
    "versions": ("tags", N_("Dates and versions"), "", False),
    "discogs": ("tags", N_("Discogs details"),
                N_("Extra details from Discogs, each in a tag of its own. At the Audiophile level: only when the "
                   "tagging level is Audiophile."), False),
    "lyrics": ("tags", N_("Lyrics"), N_("From LRCLIB, a free lyrics database."), False),
    "loudness": ("tags", N_("Loudness"),
                 N_("ReplayGain: volume tags that let players play every track equally loud. Measured at the "
                    "Audiophile level."), False),
    "notes": ("tags", N_("TagMender notes in files"), "", False),
    "tags_adv": ("tags", "", "", True),
    "covers": ("artwork", N_("Covers"),
               N_("Cover Art Archive: the covers people add to MusicBrainz. CoverUp, the cover finder that comes "
                  "with TagMender, searches fanart.tv, TheAudioDB, Last.fm and shops by the album's name."), False),
    "artist_photos": ("artwork", N_("Artist photos"), N_("At the Enthusiast and Audiophile levels."), False),
    "art_adv": ("artwork", "", "", True),
    "files": ("target", N_("Files"), "", False),
    "names": ("target", N_("Names"), "", False),
    "subfolders": ("target", N_("Pictures and logs"), "", False),
    "companions": ("target", N_("Files that go with the album"), "", False),
    "existing": ("target", N_("When a file is already there"),
                 N_("A file of the same name is already where the new one would go."), False),
    "target_adv": ("target", "", "", True),
    "unres": ("unresolved", "", "", False),
    "watch": ("automation", N_("Watched folders"),
              N_("Notifications, and watching while TagMender is open, are in Preferences › General."), False),
}


# ---- reading and writing one setting by its dotted key --------------------------------------
def dig(d, key, default=None):
    for k in key.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def plant(d, key, value):
    parts = key.split(".")
    for k in parts[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[parts[-1]] = copy.deepcopy(value)


def same(a, b) -> bool:
    """Equal values of one kind (True is not 1 here)."""
    return (isinstance(a, bool) == isinstance(b, bool)) and a == b


@dataclass
class S:
    """One setting. get(data) -> value; put(data, value, values) writes it; show(values) and
    enable(values) -> None (enabled) or the reason it is greyed out ("" for none said)."""
    key: str
    label: str
    kind: str                       # switch popup segmented stepper folder text list
    group: str
    foot: str = ""
    choices: tuple = ()
    get: object = None
    put: object = None
    show: object = None
    enable: object = None
    note: object = None             # (values) -> a line under the row that does not grey it out
    attr: str = ""                  # the Profiles page's older name for the control
    rng: tuple = (0, 100)
    suffix: str = ""
    custom: str = ""                # popup: "list" or "check" - its Choose… opens a list sheet; list: "check", or
                                    # "folders" (Choose Folder… beside the typed entry), or "sources"
                                    # (folders, each with where its albums go: SourcesSheet)
    known: tuple = ()
    words: dict = field(default_factory=dict)
    personal: bool = False          # folders and words: kept by presets and Restore Defaults

    def value_of(self, data):
        return self.get(data) if self.get else copy.deepcopy(dig(data, self.key))

    def write(self, data, value, values):
        if self.put:
            self.put(data, value, values)
        else:
            plant(data, self.key, value)


def _tagger_get(key):
    def get(d):
        return copy.deepcopy((d.get("tagger") or {}).get(key))
    return get


def _tagger_put(key):
    def put(d, v, _vals):
        t = d.setdefault("tagger", {})
        if v is None:
            t.pop(key, None)
        else:
            t[key] = v
    return put


def _id3_get(key):
    def get(d):
        v = (d.get("tagger") or {}).get(key, "unset")
        return None if v == "unset" else ("keep" if v is None else v)
    return get


def _id3_put(key):
    def put(d, v, _vals):
        t = d.setdefault("tagger", {})
        if v is None:
            t.pop(key, None)
        else:
            t[key] = None if v == "keep" else v
    return put


def _acoustid_get(d):
    return d.get("acoustid", "unsure") if dig(d, "providers.acoustid.enabled", True) else "off"


def _acoustid_put(d, v, _vals):
    plant(d, "providers.acoustid.enabled", v != "off")
    d["acoustid"] = v


def _artist_file_put(d, v, _vals):
    was = dig(d, "artwork.artist.file") or ""
    plant(d, "artwork.artist.file", (was or "artist.jpg") if v else "")


def _art_when_get(d):
    """Look for covers: "small" is "needed" with small covers replaced (artwork.replace)."""
    if dig(d, "artwork.when", "needed") == "always":
        return "always"
    return "small" if dig(d, "artwork.replace", "never") == "larger_only" else "needed"


def _art_when_put(d, v, _vals):
    plant(d, "artwork.when", "always" if v == "always" else "needed")
    plant(d, "artwork.replace", "larger_only" if v == "small" else "never")


def _sources_get(d):
    """Each folder with its own destination ("" = the profile's)."""
    return [{"path": s.get("path", ""), "destination": s.get("destination") or ""}
            for s in d.get("sources") or [] if isinstance(s, dict)]


def _sources_put(d, v, vals):
    was = {s.get("path"): s for s in d.get("sources") or [] if isinstance(s, dict)}
    deep = vals.get("_recursive", True)
    out = []
    for item in v:
        path, dest = (item.get("path", ""), item.get("destination") or "") if isinstance(item, dict) else (item, "")
        s = dict(was.get(path) or {"path": path, "watch": False}, path=path, recursive=deep)
        if dest:
            s["destination"] = dest
        else:
            s.pop("destination", None)                    # (the profile's destination)
        out.append(s)
    d["sources"] = out


def _recursive_get(d):
    srcs = [s for s in d.get("sources") or [] if isinstance(s, dict)]
    return all(s.get("recursive", True) for s in srcs) if srcs else True


def _recursive_put(d, v, _vals):
    for s in d.get("sources") or []:
        if isinstance(s, dict):
            s["recursive"] = v


def _lossy_put(d, v, vals):
    plant(d, "destinations.lossy", v if vals.get("_lossy_on") else "")


def _lossy_on_put(d, v, _vals):
    if not v:
        plant(d, "destinations.lossy", "")


def _merge_get(d):
    return (d.get("discogs_enrichment") or {}).get("merge_mode", "")


def _merge_put(d, v, _vals):
    de = d.setdefault("discogs_enrichment", {})
    if v:
        de["merge_mode"] = v
    else:
        de.pop("merge_mode", None)


def _genres_put(d, v, _vals):
    plant(d, "genres.musicbrainz", v)
    (d.get("tagger") or {}).pop("use_genres", None)       # (the choice made here is the one used)


def _engines_put(d, v, _vals):
    search = dig(d, "providers.web.search")
    if not isinstance(search, dict):
        search = {"engine": search} if isinstance(search, str) else {}
    search.pop("engine", None)
    search["engines"] = list(v)
    plant(d, "providers.web.search", search)


def _engines_get(d):
    search = dig(d, "providers.web.search") or {}
    if isinstance(search, str):
        return [] if search == "none" else [search]
    out = list(search.get("engines") or [])
    if not out and search.get("engine") not in (None, "none"):
        out = [search["engine"]]
    return out


def _bool_put(key, on_value, off_value):
    def put(d, v, _vals):
        plant(d, key, on_value if v else off_value)
    return put


def _is(key, *wanted):
    return lambda v: v.get(key) in wanted


def _needs(*ids, words):
    """Greyed out with words (and a Set Up… link) while a key or program is missing."""
    def check(_v):
        import requirements as rq
        return None if all(_ready(rq, i) for i in ids) else words
    return check


_READY = {}


def _ready(rq, id) -> bool:
    if id not in _READY:
        try:
            _READY[id] = rq.ready(id)
        except Exception:
            _READY[id] = False
    return _READY[id]


def _when(cond, reason=""):
    return lambda v: None if cond(v) else reason


def _audiophile_on(item):
    """The Audiophile level with that measurement on (levels.items): only then is it made."""
    return lambda v: v.get("tag_level") == "audiophile" and v.get(f"audiophile.{item}") is not False


REPLAYGAIN_ONLY = N_("Only at the Audiophile level, with ReplayGain on.")


def _source_note(v):
    import requirements as rq
    if v.get("source") in ("discogs_preferred", "discogs_only") and not _ready(rq, "discogs"):
        return tr("Discogs needs a free personal token: Preferences › Keys.")
    return ""


def _naming_note(v):
    return tr("A naming script of this profile's own is used instead (Script Editor).") \
        if v.get("_template") else ""


def _db_reason(name):
    def check(v):
        src = v.get("source") or pf.DEFAULTS["source"]
        if pf.source_allows(src, name):
            return None
        return tr("Not asked: the preferred database is {mode}.").format(mode=tr(SOURCE_WORDS.get(src, src)))
    return check


# the folder layouts whose album folder can be cut to a number of words
WORD_PRESETS = tuple(k for k, t in naming.PRESETS.items() if "$words(%album%,5)" in t or "$set(_maxwords,5)" in t)


def _dest_reason(v):
    return None if v.get("operation") in ("copy", "move") else tr("Used when files are copied or moved.")


def _recursive_reason(v):
    # (the choice is kept on each folder: with none, there is nothing to keep it on yet)
    return None if v.get("_sources") else tr("Add a folder first.")


SETTINGS = [
    # General
    S("description", N_("Description"), "text", "general", personal=True, attr="desc"),
    # Source
    S("_sources", N_("Folders this profile covers"), "list", "folders", get=_sources_get, put=_sources_put,
      custom="sources", personal=True, foot=N_("The music this profile looks after. A preview can still be run on any other folder.")),
    S("_recursive", N_("Include subfolders"), "switch", "folders", get=_recursive_get, put=_recursive_put,
      enable=_recursive_reason, foot=N_("Albums in folders inside these are found too.")),
    S("skip_folders", N_("Skip these folders"), "list", "folders",
      foot=N_("By name, such as #recycle, or _* for every name starting with _. Never read or changed. Hidden folders and TagMender's own "
              "Unidentified and Duplicates folders are always skipped.")),
    S("formats", N_("Audio files"), "popup", "audio", attr="formats", custom="check",
      choices=(([], N_("All formats")), (["lossless"], N_("Lossless only")), (["lossy"], N_("Lossy only"))),
      known=pf.LOSSLESS + pf.LOSSY, words=FORMAT_WORDS,
      foot=N_("Lossless: FLAC, ALAC, WAV and the like. Lossy: MP3, AAC, Opus and the like. Told by the "
              "sound inside, so an .m4a file holding ALAC counts as lossless.")),
    S("_lossy_on", N_("Put lossy files in a different folder"), "switch", "audio",
      get=lambda d: bool(dig(d, "destinations.lossy")), put=_lossy_on_put, personal=True, enable=_dest_reason,
      foot=N_("Lossy files go into a library of their own instead of the library folder.")),
    S("destinations.lossy", N_("Folder for lossy files"), "folder", "audio", put=_lossy_put, personal=True,
      show=lambda v: v.get("_lossy_on"), enable=_dest_reason,
      foot=N_("Named the same way as in the library folder.")),
    S("quality_action", N_("Fake lossless files"), "popup", "audio",
      choices=(("note", N_("Note it")), ("hold", N_("Hold for review")), ("move", N_("Move aside"))),
      enable=_when(_audiophile_on("quality"), N_("Only at the Audiophile level, with Dynamic range and quality on.")),
      foot=N_("A lossless file made from an MP3 or other lossy file. Move aside puts the album in the folder "
              "for albums nothing identified; a profile that copies holds it for review instead.")),
    S("exclude_paths", N_("Skip these paths"), "list", "source_adv",
      foot=N_("Patterns matched against the whole path, such as */Downloads/*. Never read or changed.")),
    S("grouping.join_deluxe_folders", N_("Join \"(Deluxe Edition)\" folders"), "switch", "source_adv",
      foot=N_("A folder beside the album holding only its bonus tracks is tagged as part of the album.")),
    S("on_unwritable", N_("When a file can't be written"), "popup", "source_adv",
      choices=(("skip_album", N_("Leave the whole album")), ("skip_file", N_("Leave only that file"))),
      foot=N_("Such as a read-only file. Left albums are tried again on the next run.")),
    # Identification
    S("source", N_("Preferred database"), "popup", "databases", attr="source",
      choices=tuple((k, SOURCE_WORDS[k]) for k in pf.SOURCES), note=_source_note,
      foot=N_("Which database identifies albums, and whose tags win. MusicBrainz needs no key and gives "
              "the IDs media servers use.")),
    S("providers.musicbrainz.enabled", N_("Ask MusicBrainz"), "switch", "databases", attr="mb",
      enable=_db_reason("musicbrainz"), foot=N_("Free, and needs no key.")),
    S("providers.discogs.enabled", N_("Ask Discogs"), "switch", "databases", attr="dg",
      enable=_db_reason("discogs"), foot=N_("Needs a free Discogs token (Preferences › Keys).")),
    S("identification.accept", N_("Tag an album when"), "popup", "databases",
      choices=(("exact", N_("Exact release is known")), ("album", N_("Album is known, pressing unsure")),
               ("partial", N_("Some details are certain (Partial)"))),
      foot=N_("The lower the bar, the more albums get tags. Albums below it are handled as albums "
              "nothing identified.")),
    S("acoustid", N_("Audio fingerprints"), "popup", "evidence", get=_acoustid_get, put=_acoustid_put,
      choices=(("unsure", N_("Only when unsure")), ("always", N_("Every album")), ("off", N_("Never"))),
      enable=_needs("fpcalc", "acoustid", words=N_("Needs fpcalc and an AcoustID key.")),
      foot=N_("Identifies tracks by their sound. Sends AcoustID a fingerprint: a short code worked out from "
              "the audio, not the audio itself.")),
    S("ocr", N_("Read scans and booklets"), "popup", "evidence",
      choices=(("auto", N_("When the album has scans")), ("on", N_("Every album")), ("off", N_("Never"))),
      enable=_needs("tesseract", words=N_("Needs Tesseract OCR.")),
      foot=N_("Text recognition (OCR) finds barcodes and catalogue numbers on cover scans and booklets. "
              "Runs on this computer.")),
    S("providers.web.gnudb", N_("Look up discs on gnudb"), "switch", "evidence",
      enable=_needs("gnudb", words=N_("Needs your gnudb e-mail (Preferences › Keys).")),
      foot=N_("gnudb (formerly freedb) lists what others typed in for a CD. Sends only the disc's track "
              "lengths.")),
    S("providers.web.shops", N_("Look up barcodes in music stores"), "switch", "evidence",
      foot=N_("When the barcode search found nothing sure. Sends only the barcode, to Deezer and Apple.")),
    S("providers.web.enabled", N_("Web fallback"), "switch", "evidence", attr="web",
      foot=N_("When no database has the album, store and encyclopaedia pages are checked instead.")),
    S("providers.web.sources", N_("Web fallback sources"), "list", "evidence", custom="check",
      known=pf.WEB_SOURCES, words=WEB_SOURCE_WORDS, show=lambda v: v.get("providers.web.enabled"),
      foot=N_("The sites checked. Only the album's name and barcode are sent.")),
    S("providers.web.partial", N_("Use store pages for Partial tags"), "switch", "evidence", attr="web_partial",
      enable=_when(lambda v: v.get("providers.web.enabled")),
      foot=N_("An album found only on a store page gets the tags that name no release: no release IDs, "
              "barcode or catalogue number.")),
    S("singles.identify", N_("Identify loose tracks"), "switch", "evidence",
      foot=N_("A track on its own, not part of an album, is matched to its recording by its fingerprint.")),
    S("matching.preferred_countries", N_("Preferred release countries"), "popup", "releases", custom="list",
      choices=(("auto", N_("Automatic")), ("none", N_("No preference"))),
      foot=N_("Automatic: your region, then Worldwide, then Europe.")),
    S("matching.preferred_release_types", N_("Prefer these release types"), "list", "releases",
      foot=N_("Such as Album, EP, Compilation.")),
    S("matching.discouraged_release_types", N_("Avoid these release types"), "list", "releases",
      foot=N_("Such as Bootleg, Live.")),
    S("matching.preferred_formats", N_("Prefer these formats"), "list", "releases",
      foot=N_("Such as CD, Vinyl, Digital Media.")),
    S("discogs_enrichment.merge_mode", N_("When both databases have a value"), "popup", "ident_adv",
      get=_merge_get, put=_merge_put,
      choices=(("", N_("Preferred database wins")), ("fill_gaps", N_("Fill only empty tags from Discogs")),
               ("override", N_("Discogs wins")), ("combine", N_("Both values"))),
      foot=N_("When MusicBrainz and Discogs disagree on a tag.")),
    S("_engines", N_("Web search"), "list", "ident_adv", custom="check", get=_engines_get, put=_engines_put,
      known=pf.WEB_ENGINES[1:], words=ENGINE_WORDS,
      foot=N_("A general web search for the codes read from the scans, such as the catalogue number. Off "
              "unless an engine is ticked; keys go in Preferences › Keys.")),
    S("matching.extra_careful", N_("Extra-careful matching"), "switch", "ident_adv",
      foot=N_("Needs more proof before tagging, so more albums wait for your review.")),
    # Tags
    S("tag_level", N_("Tagging level"), "popup", "written", attr="level",
      choices=tuple((k, LEVEL_WORDS[k]) for k in ("basic", "enthusiast", "audiophile")),
      foot=N_("Basic: names, numbers, date, genre. Enthusiast: everything the databases give. "
              "Audiophile: also loudness, lyrics, fingerprints, rip log, dynamic range.")),
    S("field_policy.default", N_("Existing tags"), "popup", "written",
      choices=(("replace", N_("Replace")), ("fill_missing", N_("Fill in missing only")),
               ("preserve", N_("Keep"))),
      foot=N_("Tags the files already have. Replace: the databases' values win. Fill in missing only: "
              "only empty tags get a value. Keep: they are left as they are.")),
    *[S(f"audiophile.{k}", label, "switch", "audiophile", enable=_when(_is("tag_level", "audiophile")), foot=foot)
      for k, label, foot in (
          ("replaygain", N_("ReplayGain"), N_("Volume tags that let players play every track equally loud.")),
          ("lyrics", N_("Lyrics"), N_("Looked up on LRCLIB and saved as set under Lyrics.")),
          ("acoustid", N_("AcoustID"), N_("Each track's fingerprint ID, checked against its MusicBrainz "
                                          "recording.")),
          ("rip_info", N_("Rip log"), N_("The disc ID and the rip log's verdict: the ripper, and whether the "
                                         "rip was error-free.")),
          ("quality", N_("Dynamic range and quality"), N_("A dynamic range (DR) value, and a warning when a "
                                                          "lossless file was made from a lossy one.")))],
    S("genres.musicbrainz", N_("Genres from MusicBrainz"), "popup", "genres", put=_genres_put,
      choices=(("auto", N_("Automatic")), (True, N_("On")), (False, N_("Off")),
               ("tagger", N_("Same as my existing tagger"))),
      foot=N_("The genres people voted for on MusicBrainz. Automatic: on, or as your existing tagger does "
              "when its settings are used.")),
    S("genres.max", N_("At most"), "stepper", "genres", rng=(1, 20), suffix=N_(" genres"),
      enable=_when(lambda v: v.get("genres.musicbrainz") is not False),
      foot=N_("Per album. Any more are left out.")),
    S("genres.discogs_styles", N_("Discogs styles"), "popup", "genres",
      choices=(("genre_if_none", N_("Genre, only when no other genre")), ("own_tag", N_("Own tag (DISCOGS_STYLE)")),
               ("genre", N_("Always in Genre")), ("both", N_("Both: Genre and own tag")),
               ("tagger", N_("Same as my existing tagger"))),
      foot=N_("Styles are Discogs's finer genres, such as Deep House or Shoegaze.")),
    S("genres.web", N_("Genres from the web"), "segmented", "genres",
      choices=(("off", N_("Off")), ("store", N_("Store genres")), ("keywords", N_("Also keywords"))),
      foot=N_("For albums found on store pages: the store's genres, and with Also keywords, Bandcamp's tags "
              "too.")),
    S("tagger.standardize_artist_names", N_("Artist names"), "popup", "artists", attr="pic_names",
      get=_tagger_get("standardize_artist_names"), put=_tagger_put("standardize_artist_names"),
      choices=((None, N_("Automatic")), ("none", N_("As credited on the release")),
               ("variations", N_("Fix spelling variations")),
               ("all", N_("Always the standard name"))),
      foot=N_("The standard name is the one MusicBrainz lists for the artist. Always: also for releases "
              "made under a former name.")),
    S("tagger.translate_artist_names", N_("Translate artist names"), "popup", "artists",
      get=_tagger_get("translate_artist_names"), put=_tagger_put("translate_artist_names"), choices=AUTO_ON_OFF,
      foot=N_("The name in your system language, when MusicBrainz knows one.")),
    S("tagger.convert_punctuation", N_("Plain punctuation"), "popup", "artists",
      get=_tagger_get("convert_punctuation"), put=_tagger_put("convert_punctuation"), choices=AUTO_ON_OFF,
      foot=N_("Curly quotes and dashes become plain ones.")),
    S("tagger.release_ars", N_("Credits on the album"), "popup", "credits", attr="pic_rel",
      get=_tagger_get("release_ars"), put=_tagger_put("release_ars"), choices=AUTO_ON_OFF,
      foot=N_("Producer, engineer, mixer and others credited for the whole album.")),
    S("tagger.track_ars", N_("Credits on each track"), "popup", "credits", attr="pic_trk",
      get=_tagger_get("track_ars"), put=_tagger_put("track_ars"), choices=AUTO_ON_OFF,
      foot=N_("Composer, lyricist, performers and their instruments.")),
    S("tagger.standardize_instruments", N_("Standard instrument names"), "popup", "credits",
      get=_tagger_get("standardize_instruments"), put=_tagger_put("standardize_instruments"), choices=AUTO_ON_OFF,
      foot=N_("Instruments as MusicBrainz names them, rather than as each credit words them.")),
    S("dates.date_tag", N_("Date holds"), "popup", "versions",
      choices=(("release", N_("This pressing's date")), ("original", N_("The first release date")),
               ("tagger", N_("Same as my existing tagger"))),
      foot=N_("The first release date is always written as Original Date too.")),
    S("keep_versions", N_("Separate versions"), "switch", "versions", attr="versions",
      foot=N_("A remaster or remix keeps its version in the album title, so media servers list it apart "
              "from the original.")),
    S("versions.style", N_("Show the version"), "popup", "versions",
      choices=(("paren", N_("Album (Remaster 2024)")), ("bracket", N_("Album [Remaster 2024]")),
               ("tags_only", N_("Album, with the version in its own tags"))), show=lambda v: v.get("keep_versions"),
      foot=N_("How a remaster, remix or deluxe edition is told apart from the original.")),
    S("versions.extra_tags", N_("Write version tags"), "switch", "versions", show=lambda v: v.get("keep_versions"),
      foot=N_("ALBUMVERSION and RELEASECOMMENT tags, read by LMS and other media servers.")),
    S("split_versions", N_("Split two versions found in one folder"), "switch", "versions", attr="split_box",
      foot=N_("Such as a remaster beside a remix: each goes into a folder of its own, tagged from its own "
              "release.")),
    *[S(f"discogs_details.{k}", label, "popup", "discogs", choices=DETAIL_CHOICES, foot=foot)
      for k, label, foot in (
          ("performers", N_("Performers"), N_("Musicians and their instruments, as Discogs credits them.")),
          ("companies", N_("Companies"), N_("Labels, studios, pressing plants and publishers.")),
          ("identifiers", N_("Matrix and other codes"), N_("The codes stamped or printed on the disc and "
                                                           "sleeve, such as the matrix in the run-out groove.")),
          ("marketplace", N_("Marketplace numbers"), N_("How many Discogs users have and want it, its rating, "
                                                        "copies for sale and the lowest price.")),
          ("notes", N_("Release notes"), N_("The free-text notes on the Discogs release page.")))],
    S("lyrics.enabled", N_("Find lyrics"), "switch", "lyrics", foot=N_("Saved where chosen below.")),
    S("lyrics.sidecar", N_("Next to the track (.lrc)"), "switch", "lyrics", show=lambda v: v.get("lyrics.enabled"),
      foot=N_("A .lrc file beside each track, read by Plex and Jellyfin. Synced lyrics only.")),
    S("lyrics.embed", N_("In the file"), "switch", "lyrics", show=lambda v: v.get("lyrics.enabled"),
      foot=N_("In the track's tags, read by Navidrome and phones.")),
    S("lyrics.synced", N_("Prefer synced lyrics"), "switch", "lyrics", show=lambda v: v.get("lyrics.enabled"),
      foot=N_("Lines timed to the music, which players scroll along. Off: the plain text, in the file.")),
    S("loudness.replace_existing", N_("Recalculate ReplayGain"), "switch", "loudness",
      enable=_when(_audiophile_on("replaygain"), REPLAYGAIN_ONLY),
      foot=N_("Measures every track again and replaces values other programs wrote. Off: those are kept.")),
    S("mark_resolved", N_("Mark identified albums"), "switch", "notes",
      foot=N_("Writes TAGMENDER=Resolved into each file, so re-runs, checks and retries can tell finished "
              "albums.")),
    S("cleanup.comments", N_("Comments"), "segmented", "tags_adv",
      choices=(("keep", N_("Keep")), ("remove", N_("Remove"))),
      foot=N_("The files' comment tags, often a note from the ripper or the shop.")),
    S("loudness.album_gain", N_("Album gain"), "switch", "tags_adv",
      enable=_when(_audiophile_on("replaygain"), REPLAYGAIN_ONLY),
      foot=N_("A ReplayGain value for the whole album, which keeps quiet tracks quieter than loud ones.")),
    S("loudness.opus_r128", N_("Opus gain tags"), "switch", "tags_adv",
      enable=_when(_audiophile_on("replaygain"), REPLAYGAIN_ONLY),
      foot=N_("The R128 volume tags Opus players read, as well as ReplayGain.")),
    S("tagger.write_id3v23", N_("MP3 tag version"), "popup", "tags_adv", attr="pic_v2",
      get=_id3_get("write_id3v23"), put=_id3_put("write_id3v23"),
      choices=((None, N_("Automatic")), ("keep", N_("Keep each file's")), (False, N_("Newest (ID3v2.4)")),
               (True, N_("For older players (ID3v2.3)"))),
      foot=N_("ID3 is the tag format of MP3 files. Some older players and car stereos read only version "
              "2.3.")),
    S("tagger.write_id3v1", N_("Old MP3 tag (ID3v1)"), "popup", "tags_adv", attr="pic_v1",
      get=_id3_get("write_id3v1"), put=_id3_put("write_id3v1"),
      choices=((None, N_("Automatic")), ("keep", N_("Update one that is there")), (True, N_("Write")),
               (False, N_("Remove"))),
      foot=N_("A tiny, very old tag that only a few old players still need.")),
    S("tagger.remove_id3_from_flac", N_("Remove stray MP3 tags from FLAC"), "popup", "tags_adv",
      get=_tagger_get("remove_id3_from_flac"), put=_tagger_put("remove_id3_from_flac"), choices=AUTO_ON_OFF,
      foot=N_("FLAC files have tags of their own; MP3-style tags left in them confuse some players.")),
    S("tagger.remove_ape_from_mp3", N_("Remove APE tags from MP3"), "popup", "tags_adv",
      get=_tagger_get("remove_ape_from_mp3"), put=_tagger_put("remove_ape_from_mp3"), choices=AUTO_ON_OFF,
      foot=N_("APE is an older tag format some programs add to MP3s; it can disagree with the main tags.")),
    S("follow_tagger_settings", N_("Use my existing tagger's settings"), "switch", "tags_adv",
      foot=N_("When another tagger is installed on this computer, its settings file is read on each run, "
              "so the tags come out the way it writes them. Anything you set here wins.")),
    # Artwork
    S("artwork.enabled", N_("Find front covers"), "switch", "covers", attr="art",
      foot=N_("Looks for the album's front cover in the sources below.")),
    S("artwork.sources", N_("Cover sources"), "list", "covers", attr="art_src", custom="check",
      known=tuple(ART_SOURCE_WORDS), words=ART_SOURCE_WORDS,
      foot=N_("Tick the sources to ask and put them in order: the first one with a big enough cover wins.")),
    S("artwork.when", N_("Look for covers"), "popup", "covers", attr="art_when", get=_art_when_get, put=_art_when_put,
      choices=(("needed", N_("Only when an album has no cover")),
               ("small", N_("Also when its cover is too small")),
               ("always", N_("For every album"))),
      foot=N_("Too small: below the smallest cover size. A cover that is replaced is backed up first, so "
              "Undo puts it back.")),
    S("artwork.min_px", N_("Smallest cover size"), "popup", "covers",
      choices=tuple((n, f"{n} px") for n in (500, 600, 1000, 1200)),
      foot=N_("Measured on the short side. A cover found smaller than this is not used.")),
    S("artwork.enlarge", N_("Enlarge small covers"), "switch", "covers", attr="art_enlarge",
      foot=N_("When only a smaller cover is found (250 px or more), it is enlarged to the smallest size "
              "instead of being left out.")),
    S("artwork.embed_front", N_("Put the cover in each track"), "switch", "covers",
      foot=N_("Players and phones then show it even without the cover file.")),
    S("artwork.embed_max_side", N_("Cover size in tracks"), "popup", "covers",
      choices=tuple((n, f"{n} px") for n in (600, 1000, 1200, 1600)) + ((0, N_("As found")),),
      show=lambda v: v.get("artwork.embed_front"),
      foot=N_("Bigger covers are shrunk to this, on the long side, before they go into the tracks.")),
    S("artwork.files", N_("Save a cover file"), "switch", "covers", foot=N_("cover.jpg in the album folder, which most media servers show.")),
    S("artwork.artist.enabled", N_("Artist photos"), "switch", "artist_photos", attr="art_artist",
      foot=N_("Asks fanart.tv, TheAudioDB, Wikimedia Commons or Discogs once per artist.")),
    S("artwork.artist.file", N_("Save artist.jpg"), "switch", "artist_photos",
      attr="art_artist_file", get=lambda d: bool(dig(d, "artwork.artist.file")), put=_artist_file_put,
      enable=_when(lambda v: v.get("artwork.artist.enabled")),
      foot=N_("In the artist folder, where Plex, Jellyfin, Navidrome and LMS look for it. One already there "
              "is never replaced.")),
    S("artwork.artist.embed", N_("Put it in each track"), "switch", "artist_photos",
      enable=_when(lambda v: v.get("artwork.artist.enabled")),
      foot=N_("Some players then show the artist instead of the cover.")),
    S("artwork.generate", N_("Make a cover when none is found"), "popup", "art_adv", attr="art_gen",
      choices=(("off", N_("Never: leave the album without one")),
               ("template", N_("Draw one with the artist and album name (on this computer)")),
               ("ai", N_("Ask the AI image service you set up in CoverUp")),
               ("auto", N_("Try both, in the order set in CoverUp"))),
      foot=N_("A last resort when no source has a cover. A made cover never replaces a real one, and "
              "nothing is sent anywhere unless you set up an AI service yourself.")),
    # Target
    S("operation", N_("What happens to files"), "segmented", "files", attr="op",
      choices=tuple((k, OPERATION_SHORT[k]) for k in pf.OPERATIONS),
      foot=N_("Fix in place: the files stay where they are. Copy or move: into the library folder, named as "
              "below.")),
    S("destination", N_("Library folder"), "folder", "files", attr="dest", personal=True, enable=_dest_reason,
      foot=N_("Where copied or moved albums go.")),
    S("rename_in_place", N_("Rename files where they are"), "switch", "files",
      show=_is("operation", "in_place"), foot=N_("The files get the names set below, without making a second "
                                                "library.")),
    S("naming.preset", N_("Folder layout"), "popup", "names", attr="naming", note=_naming_note,
      choices=tuple((k, NAMING_WORDS.get(k, k)) for k in naming.PRESETS),
      foot=N_("How the folders and files in the library are named.")),
    S("filenames.max_album_words", N_("Shorten album folder names"), "popup", "names",
      choices=((0, N_("Off")), (5, N_("5 words")), (8, N_("8 words"))),
      enable=lambda v: None if v.get("naming.preset") in WORD_PRESETS and not v.get("_template") else
      N_("This folder layout always uses the whole album title."),
      foot=N_("Long album titles are cut to this many words in the folder name. The tags keep the whole "
              "title.")),
    S("naming.editions", N_("Editions in folder names"), "popup", "names", attr="editions",
      choices=(("off", N_("Off")), ("edition", N_("Edition ([Vinyl], [WEB])")),
               ("edition_year", N_("Edition and reissue year ([2019 Vinyl])"))),
      foot=N_("Tells a vinyl or download apart from the CD in the folder name.")),
    S("naming.album_editions", N_("Edition in the Album tag"), "popup", "names", attr="album_editions",
      choices=(("off", N_("Off")), ("edition", N_("Edition ([Vinyl], [WEB])")),
               ("edition_year", N_("Edition and reissue year ([2019 Vinyl])"))),
      foot=N_("The same, added to the album title in the tags.")),
    S("subfolders.covers", N_("Pictures in their own folder"), "switch", "subfolders", attr="sub_covers",
      foot=N_("Scans and booklets go into it. The front cover stays beside the music.")),
    S("subfolders.covers_name", N_("Pictures folder name"), "text", "subfolders", attr="sub_covers_name",
      enable=_when(lambda v: v.get("subfolders.covers"))),
    S("subfolders.misc", N_("Rip logs in their own folder"), "switch", "subfolders",
      attr="sub_misc", foot=N_("Rip logs, notes and checksums go into it. Cue sheets and playlists stay "
                               "beside the audio they name.")),
    S("subfolders.misc_name", N_("Logs and notes folder name"), "text", "subfolders", attr="sub_misc_name",
      enable=_when(lambda v: v.get("subfolders.misc"))),
    S("subfolders.where", N_("For multi-disc albums"), "popup", "subfolders", attr="sub_where",
      choices=(("album", N_("One for the album")), ("disc", N_("One in each disc"))),
      foot=N_("Where the pictures and logs folders go.")),
    S("companions", N_("Files that go with the album"), "list", "companions",
      foot=N_("Files with these extensions, such as .cue and .log, are copied or moved with the music.")),
    S("companions_rewrite_cues", N_("Update cue sheets and playlists"), "switch", "companions",
      foot=N_("The new file names are written into them, so they still find the music.")),
    S("remove_empty_dirs", N_("Remove empty folders after moving"), "switch", "companions",
      show=_is("operation", "move"), foot=N_("Folders left empty once the music is moved out are removed.")),
    S("preserve_timestamps", N_("Keep file dates"), "popup", "companions", choices=AUTO_ON_OFF,
      foot=N_("Files keep their modified date, so \"Recently added\" in players stays as it was. Automatic: as "
              "your existing tagger is set.")),
    S("on_existing", N_("Then"), "popup", "existing",
      choices=(("keep_both", N_("Keep both")), ("skip", N_("Skip it")), ("ask", N_("Ask me"))),
      foot=N_("Keep both: the new file gets a second name, as below. Watched folders that apply without asking "
              "always keep both.")),
    S("naming.duplicate_suffix", N_("Name the second copy"), "segmented", "existing",
      choices=(("number", N_("Name (2)")), ("code", N_("Short code"))),
      foot=N_("Short code: a few letters and digits, such as Name ~1a2b.")),
    S("filenames.compat", N_("Names safe for"), "popup", "target_adv",
      choices=(("windows", N_("All systems")), ("linux", N_("Linux and Mac only"))),
      foot=N_("All systems: also Windows and network shares (NAS), so no characters such as : or ?.")),
    S("filenames.fat", N_("Also USB sticks and car players"), "switch", "target_adv",
      enable=_when(_is("filenames.compat", "windows"), N_("Needs names safe for all systems.")),
      foot=N_("Shorter paths, and no dots or spaces at the end of names.")),
    S("tagger.ascii_filenames", N_("Plain letters only (ASCII)"), "popup", "target_adv",
      get=_tagger_get("ascii_filenames"), put=_tagger_put("ascii_filenames"), choices=AUTO_ON_OFF,
      foot=N_("Accented and other letters become plain ones in file names, such as é to e.")),
    S("tagger.replace_spaces_with_underscores", N_("Spaces become underscores"), "popup", "target_adv",
      get=_tagger_get("replace_spaces_with_underscores"), put=_tagger_put("replace_spaces_with_underscores"),
      choices=AUTO_ON_OFF, foot=N_("In file and folder names.")),
    S("filenames.max_name_bytes", N_("Longest file name"), "stepper", "target_adv", rng=(16, 255),
      suffix=N_(" bytes"),
      foot=N_("Longer names are shortened. Most drives allow 255; accented and non-Latin letters take two or "
              "more bytes each.")),
    S("destinations.mounted_only", N_("Write only when mounted"), "popup", "target_adv",
      choices=(("auto", N_("For shares and drives")), ("always", N_("Always (never the system disk)"))),
      foot=N_("When the share or drive holding the library is not connected, nothing is written, so a "
              "copy never fills the system disk by mistake.")),
    S("allow_unverified_writes", N_("Write formats that can't be checked"), "switch", "target_adv",
      foot=N_("TagMender checks the audio is unchanged after every write. Off: formats it cannot check are "
              "left untouched.")),
    # Albums nothing identified
    S("unresolved.action", N_("Albums nothing identified"), "popup", "unres", attr="unres",
      choices=tuple((k, UNRESOLVED_WORDS[k]) for k in pf.UNRESOLVED_ACTIONS),
      foot=N_("Mark Unresolved: TAGMENDER=Unresolved is written, and the album is listed under Needs "
              "attention and tried again later. Move aside: moved to the folder below.")),
    S("unresolved.move_to", N_("Move to"), "folder", "unres", personal=True,
      show=lambda v: v.get("unresolved.action") == "move" or v.get("quality_action") == "move",
      foot=N_("Albums moved aside wait here, out of the library.")),
    S("retry.after_days", N_("Try again"), "popup", "unres",
      choices=((7, N_("Every week")), (30, N_("Every month")), (0, N_("Never"))),
      foot=N_("Marked albums are looked up again, as the databases keep growing.")),
    S("retry.max_attempts", N_("Up to"), "stepper", "unres", rng=(1, 20), suffix=N_(" tries"),
      show=lambda v: v.get("retry.after_days") != 0, foot=N_("Per album. Then it is left for you.")),
    # Automation
    S("unattended", N_("Apply without asking"), "switch", "watch", attr="unattended",
      foot=N_("Albums dropped into this profile's watched folders are fixed without waiting for you. "
              "Partial albums still wait for your review.")),
    S("watch.settle_minutes", N_("Wait until unchanged for"), "popup", "watch",
      choices=((2, N_("2 min")), (5, N_("5 min")), (10, N_("10 min")), (30, N_("30 min"))),
      foot=N_("So a copy still in progress is never touched.")),
    S("watch.interval_seconds", N_("Look every"), "popup", "watch",
      choices=((60, N_("1 min")), (300, N_("5 min")), (900, N_("15 min")), (3600, N_("60 min"))),
      foot=N_("How often the watched folders are checked for new music.")),
]
BY_KEY = {s.key: s for s in SETTINGS}

# ---- presets: what each one sets on top of the defaults --------------------------------------
PRESETS = {
    "typical": (N_("Typical Library"),
                N_("The defaults: MusicBrainz first, readable names, your own covers kept."), {}),
    "collector": (N_("Collector"),
                  N_("Discogs first, every detail and code, artist photos, editions in folder names, "
                     "asks before a second copy."),
                  {"source": "discogs_preferred", "tag_level": "audiophile", "keep_versions": True,
                   "discogs_details.companies": True, "discogs_details.identifiers": True,
                   "artwork.artist.enabled": True, "artwork.artist.embed": True, "artwork.when": "small",
                   "artwork.enlarge": True, "naming.editions": "edition_year", "on_existing": "ask",
                   "mark_resolved": True}),
    "keep": (N_("Keep My Files as They Are"),
             N_("Fixed in place, nothing renamed, only missing tags filled in, covers and comments kept."),
             {"operation": "in_place", "rename_in_place": False, "field_policy.default": "fill_missing",
              "artwork.when": "needed", "artwork.enlarge": False, "artwork.artist.enabled": False,
              "cleanup.comments": "keep", "split_versions": False,
              "tagger.remove_id3_from_flac": False, "tagger.remove_ape_from_mp3": False}),
}

# settings a preset leaves as they are unless it names them
PRESET_KEEPS = ("operation", "rename_in_place", "unattended")

# a problem from profiles.validate() -> the row it is about, when its words do not start with a key
PROBLEM_ROWS = (("operation '", "destination"), ("destination must", "destination"),
                ("no provider", "providers.musicbrainz.enabled"), ("source is", "source"),
                ("unresolved.action 'move'", "unresolved.move_to"), ("quality_action 'move'", "unresolved.move_to"),
                ("thresholds.", "matching.extra_careful"), ("sources[", "_sources"),
                ("providers.web.search", "_engines"), ("exclude_paths", "exclude_paths"),
                ("discogs_enrichment", "discogs_enrichment.merge_mode"), ("artwork.artist", "artwork.artist.enabled"),
                ("subfolders.covers_name", "subfolders.covers_name"), ("subfolders.misc_name", "subfolders.misc_name"),
                ("subfolders", "subfolders.covers"), ("filenames.fat", "filenames.fat"))
# a few problems in friendlier words
PROBLEM_WORDS = {"needs a destination": N_("Choose the library folder: files are copied or moved into it."),
                 "needs unresolved.move_to": N_("Choose where albums are moved aside to.")}

# the example album the naming row shows
SAMPLE = {"albumartist": ["Ada Lark & Milo Fen"], "artist": ["Ada Lark & Milo Fen"],
          "_albumartists": ["Ada Lark", "Milo Fen"], "albumartistsort": ["Lark, Ada & Fen, Milo"],
          "album": ["Songs From the Long Winter Evenings Under Northern Skies"], "title": ["The Lighthouse Keeper"],
          "tracknumber": ["3"], "discnumber": ["2"], "totaldiscs": ["2"], "date": ["1994-05-02"],
          "originaldate": ["1991-03-01"], "media": ["CD"]}


# the Tagging Script preview's albums: an original CD, a vinyl reissue, a two-disc set
EXAMPLES = (
    (N_("An original CD"), dict(SAMPLE, discnumber=["1"], totaldiscs=["1"], date=["1991-03-01"])),
    (N_("A vinyl reissue"), dict(SAMPLE, discnumber=["1"], totaldiscs=["1"], date=["2019-11-08"], media=["12\" Vinyl"])),
    (N_("Disc 2 of a two-CD set"), SAMPLE),
)


def _index_of(box, value) -> int:
    for i in range(box.count()):
        if same(box.itemData(i), value):
            return i
    return -1


def _word(spec, x) -> str:
    """One item of a list value in the words its control shows (translated)."""
    return tr(spec.words[x]) if isinstance(x, str) and x in spec.words else str(x)


def words_of(spec, value) -> str:
    """A value in the words its control shows (for the sheets that list changes)."""
    if spec.kind == "switch":
        return tr("On") if value else tr("Off")
    for v, w in spec.choices:
        if same(v, value):
            return tr(w)
    if isinstance(value, list):
        return ", ".join(_word(spec, x) for x in value) or tr("None")
    return str(value) if value not in (None, "") else tr("None")


def preset_data(name: str, preset: str) -> dict:
    """A new profile's data: its name and what the preset sets."""
    data = {"name": name}
    full = pf.from_dict({"name": name}, check=False).data
    vals = {s.key: s.value_of(full) for s in SETTINGS}
    vals.update(PRESETS.get(preset, PRESETS["typical"])[2])
    for key, v in PRESETS.get(preset, PRESETS["typical"])[2].items():
        BY_KEY[key].write(data, v, vals)
    return data


class PresetSheet(QDialog):
    """Start From…: the three presets as cards, each with what it does."""

    def __init__(self, parent, headline, with_name=False, action=None):
        super().__init__(parent.window() if parent is not None else None)
        sheets.prepare(self, headline)
        self.resize(520, 360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        head = QLabel(headline)
        head.setProperty("role", "headline")
        lay.addWidget(head)
        self.name = None
        if with_name:
            self.name = QLineEdit()
            self.name.setAccessibleName(tr("Profile name"))
            self.name.setPlaceholderText(tr("A name, such as Library or Inbox"))
            lay.addWidget(self.name)
            lay.addWidget(caption(tr("Start from:"), "hint"))
        self.cards = {}
        for key, (title, about, _v) in PRESETS.items():
            card = QWidget()
            v = QVBoxLayout(card)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            rb = QRadioButton(tr(title))
            rb.setAccessibleName(tr(title))
            rb.setAccessibleDescription(tr(about))
            v.addWidget(rb)
            note = caption(tr(about))
            note.setContentsMargins(24, 0, 0, 0)
            v.addWidget(note)
            lay.addWidget(card)
            self.cards[key] = rb
        self.cards["typical"].setChecked(True)
        lay.addStretch(1)
        row, _ok = sheets.button_row(self, action or tr("Continue"), self.accept)
        lay.addLayout(row)

    def preset(self) -> str:
        return next(k for k, rb in self.cards.items() if rb.isChecked())


class ProfileEditor(QScrollArea):
    """Every setting of one profile, in sections of grouped inset lists."""
    edited = Signal()
    open_rules = Signal()
    open_script = Signal()

    def __init__(self, page=None):
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAccessibleName(tr("Profile settings"))
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 8, 16, 16)            # (16 on the right: never under the scroll bar)
        self.lay.setSpacing(16)
        self.banner = caption("", "error")
        self.banner.hide()
        self.lay.addWidget(self.banner)
        tools = FlowLayout(spacing=8)
        self.start_btn = QPushButton(tr("Start From…"))
        self.start_btn.setAccessibleName(tr("Start from a preset"))
        self.start_btn.setAutoDefault(False)
        self.start_btn.clicked.connect(self.choose_preset)
        self.reset_btn = QPushButton(tr("Restore Defaults…"))
        self.reset_btn.setAccessibleName(tr("Restore the defaults of every setting"))
        self.reset_btn.setAutoDefault(False)
        self.reset_btn.clicked.connect(lambda: self.ask_restore(None))
        tools.addWidget(self.start_btn)
        tools.addWidget(self.reset_btn)
        for text, name, on in ((tr("Collapse All"), tr("Fold every section"), False),
                               (tr("Expand All"), tr("Unfold every section"), True)):
            b = QPushButton(text)
            b.setAccessibleName(name)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, o=on: self.fold_all(o))
            tools.addWidget(b)
        self.lay.addLayout(tools)
        self.rows, self.controls, self.groups, self.sections, self.advanced = {}, {}, {}, {}, {}
        self.folds = {}
        self.loaded, self.defaults, self.raw = {}, {}, {}
        self._loading = False
        for sid, title, hint in SECTIONS:
            self._section(sid, title, hint)
        self.lay.addStretch(1)
        self.setWidget(body)
        self.body = body
        for sid in _gui_prefs().get(FOLDED_PREF) or []:      # (folded the last time)
            if sid in self.folds:
                self.folds[sid].setChecked(False)
        self.load({}, pf.from_dict({"name": "x"}, check=False).data, pf.from_dict({"name": "x"}, check=False).data)

    # ---- building ----------------------------------------------------------------------
    def _section(self, sid, title, hint):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        outer = v
        if sid != "general":                                 # (a heading that folds what follows it)
            inner = QWidget()
            fold = SectionFold(tr(title), inner)
            fold.toggled_by_user.connect(lambda _on: self._remember_folds())
            outer.addWidget(fold, 0, Qt.AlignLeft)
            outer.addWidget(inner)
            self.folds[sid] = fold
            v = QVBoxLayout(inner)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(12)
            if hint:
                v.addWidget(caption(tr(hint), "hint"))
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(12)
        for gid, (gsec, gtitle, gfoot, adv) in GROUPS.items():
            if gsec != sid:
                continue
            g = Group(tr(gtitle) if gtitle else "", tr(gfoot) if gfoot else "")
            self.groups[gid] = g
            for spec in SETTINGS:
                if spec.group == gid:
                    self._row(g, spec)
            if gid == "names":
                self.preview = caption("", "hint")
                self.preview.setAccessibleName(tr("Example"))
                self.preview.setTextFormat(Qt.RichText)
                g.add_widget(self.preview)
                self.picard_btn = QPushButton(tr("Edit Naming Script…"))
                self.picard_btn.setAccessibleName(tr("Edit this profile's naming script, with a live preview"))
                self.picard_btn.setAutoDefault(False)
                self.picard_btn.clicked.connect(self.open_script.emit)
                g.add_widget(self.picard_btn).layout().setAlignment(self.picard_btn, Qt.AlignLeft)
            if gid == "folders":
                rules = QPushButton(tr("Ignore and Skip Rules…"))
                rules.setAccessibleName(tr("Show the ignore and skip rules"))
                rules.setAutoDefault(False)
                rules.clicked.connect(self.open_rules.emit)
                g.add_widget(rules).layout().setAlignment(rules, Qt.AlignLeft)
            if gid == "files":
                g.foot.setVisible(True)
                self.undo_note = g.foot
            (hv if adv else v).addWidget(g)
        if any(GROUPS[g][3] for g in GROUPS if GROUPS[g][0] == sid):
            more = Disclosure(tr("Advanced"), holder)
            more.setAccessibleName(tr("Advanced settings: {0}").format(tr(title)))
            v.addWidget(more, 0, Qt.AlignLeft)
            v.addWidget(holder)
            self.advanced[sid] = more
        if sid != "general":
            reset = QPushButton(tr("Restore Section Defaults…"))
            reset.setAccessibleName(tr("Restore the defaults of {0}").format(tr(title)))
            reset.setAutoDefault(False)
            reset.clicked.connect(lambda _=False, s=sid: self.ask_restore(s))
            v.addWidget(reset, 0, Qt.AlignLeft)
        self.sections[sid] = box
        self.lay.addWidget(box)

    def _control(self, spec):
        label = tr(spec.label)
        if spec.kind == "switch":
            c = Switch(label)
            c.toggled.connect(self._changed)
        elif spec.kind in ("popup", "segmented"):
            c = Popup() if spec.kind == "popup" else Segmented(label)
            for v, w in spec.choices:
                c.addItem(tr(w), v)
            if spec.custom:
                c.addItem(tr("Choose…"), CHOOSE)
            if spec.kind == "popup":
                c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
                c.setMaximumWidth(320)                          # (longer words are cut, never the window)
                c.setMinimumWidth(120)
                c.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                c.activated.connect(lambda i, s=spec, box=c: self._picked(s, box, i))
            c.currentIndexChanged.connect(self._changed)
        elif spec.kind == "stepper":
            c = Stepper(spec.rng[0], spec.rng[1], tr(spec.suffix) if spec.suffix else "")
            c.valueChanged.connect(self._changed)
        elif spec.kind == "folder":
            c = FolderField(label, tr("Choose: {0}").format(label))
            c.textChanged.connect(self._changed)
        elif spec.kind == "list":
            c = ListField(label, tr(spec.foot) if spec.foot else "", checkable=spec.custom == "check",
                          known=list(spec.known), words={k: tr(w) for k, w in spec.words.items()},
                          folders=spec.custom if spec.custom in ("folders", "sources") else False)
            c.changed.connect(self._changed)
        else:
            c = QLineEdit()
            c.setMinimumWidth(120)
            c.textChanged.connect(self._changed)
        c.setAccessibleName(label)
        return c

    def _row(self, group, spec):
        c = self._control(spec)
        row = Row(tr(spec.label), c, tr(spec.foot) if spec.foot else "")
        row.reason.linkActivated.connect(self.open_setup)
        group.add(row)
        self.rows[spec.key] = row
        self.controls[spec.key] = c

    def _picked(self, spec, box, i):
        """A pop-up's Choose…: a sheet for the list (formats, countries); Cancel goes back."""
        if box.itemData(i) != CHOOSE:
            box.setProperty("was", i)
            return
        was = box.property("was") if box.property("was") is not None else 0
        cur = box.itemData(was)
        dlg = ListSheet(self, tr(spec.label), tr(spec.foot) if spec.foot else "",
                        cur if isinstance(cur, list) else [], checkable=spec.custom == "check",
                        known=list(spec.known), words={k: tr(w) for k, w in spec.words.items()})
        if sheets.run(dlg, "list") and dlg.items():
            self._set(spec, dlg.items())
        else:
            box.setCurrentIndex(was)

    # ---- values -----------------------------------------------------------------------------
    def value(self, key):
        spec, c = BY_KEY[key], self.controls[key]
        if spec.kind == "switch":
            return c.isChecked()
        if spec.kind in ("popup", "segmented"):
            v = c.currentData()
            return copy.deepcopy(v) if v != CHOOSE else self.loaded.get(key)
        if spec.kind == "stepper":
            return c.value()
        if spec.kind == "list":
            return c.value()
        return c.text().strip() if spec.kind == "folder" else c.text()

    def values(self) -> dict:
        out = {k: self.value(k) for k in self.controls}
        out["_template"] = bool(dig(self.raw, "naming.template"))
        return out

    def _set(self, spec, v):
        c = self.controls[spec.key]
        if spec.kind == "switch":
            c.setChecked(bool(v))
        elif spec.kind in ("popup", "segmented"):
            if spec.kind == "popup":                            # (a value shown for another profile: gone)
                for n in reversed(range(c.count())):
                    d = c.itemData(n)
                    if d != CHOOSE and not same(d, v) and not any(same(d, x) for x, _w in spec.choices):
                        c.removeItem(n)
            i = _index_of(c, v)
            if i < 0 and spec.kind == "popup":                  # (a value this version has no words for)
                text = ", ".join(_word(spec, x) for x in v) if isinstance(v, list) else str(v)
                at = c.count() - (1 if spec.custom else 0)
                c.insertItem(at, text, copy.deepcopy(v))
                i = at
            c.setCurrentIndex(max(0, i))
            if spec.kind == "popup":
                c.setProperty("was", c.currentIndex())
        elif spec.kind == "stepper":
            try:
                n = int(v)
            except (TypeError, ValueError):
                return
            # (a hand-written value outside the usual range is shown as it is, never cut: a
            # save of another setting must not change it)
            c.setRange(min(spec.rng[0], n), max(spec.rng[1], n))
            c.setValue(n)
        elif spec.kind == "list":
            c.set_value(v if isinstance(v, list) else [])
        else:
            c.setText("" if v is None else str(v))

    def set_value(self, key, v):
        """Change one setting as the person would (tests, presets)."""
        self._set(BY_KEY[key], v)
        self._changed()

    def load(self, raw, full, defaults):
        """Show a profile: raw (its file), full (its values with defaults), defaults (a new
        profile's values, for the change markers and Restore Defaults)."""
        _READY.clear()
        self._loading = True
        self.raw = copy.deepcopy(raw)
        self.loaded = {s.key: s.value_of(full) for s in SETTINGS}
        self.defaults = {s.key: s.value_of(defaults) for s in SETTINGS}
        for s in SETTINGS:
            self._set(s, self.loaded[s.key])
        self._loading = False
        self.refresh()

    def changed_keys(self) -> list:
        return [k for k in self.controls if not same(self.value(k), self.loaded.get(k))]

    def write(self, data) -> dict:
        """Put what was changed into data (a profile's file): only those settings, so what the
        file says about the rest - a hand-written value too - stays as it was."""
        vals = self.values()
        for key in self.changed_keys():
            BY_KEY[key].write(data, vals[key], vals)
        return data

    def snapshot(self) -> list:
        return [repr(self.value(k)) for k in self.controls]

    # ---- reacting ---------------------------------------------------------------------------
    def _changed(self, *_):
        if self._loading:
            return
        self.refresh()
        self.edited.emit()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loading and getattr(self, "loaded", None):
            self.refresh()                  # (back from Setup: a key added there counts now)

    def refresh(self):
        _READY.clear()                      # (asked again: a key or program may have been added)
        vals = self.values()
        self.controls["_sources"].fallback = vals.get("destination") or ""   # (named in its Moves to column)
        for s in SETTINGS:
            row = self.rows[s.key]
            row.setVisible(bool(s.show(vals)) if s.show else True)
            reason = s.enable(vals) if s.enable else None
            text = self._reason_text(reason)
            if reason is None:
                row.set_reason("")
            else:
                row.set_reason(text)
                row.control.setEnabled(False)
            if s.note and reason is None:
                note = s.note(vals)
                row.reason.setText(note)
                row.reason.setVisible(bool(note))
            row.set_changed(not s.personal and not same(vals[s.key], self.defaults.get(s.key)))
        for g in self.groups.values():
            g.tidy()
        self.show_preview(vals)
        self.show_problems()
        if self._search:
            self.search(self._search)

    _search = ""

    @staticmethod
    def _reason_text(reason):
        if not reason:
            return ""
        text = html.escape(tr(reason))
        if "Keys" in reason or "Needs" in reason:
            text += " " + tr('<a href="setup">Set Up…</a>')
        return text

    def open_setup(self, _link=""):
        win = self.window()
        from . import setup
        setup.open_setup(getattr(win, "win", None) or win)

    def problems(self) -> dict:
        """{setting key or "": [problems]} for the profile as the form says it."""
        data = self.write(copy.deepcopy(self.raw) or {"name": "x"})
        data.setdefault("name", "x")
        out = {}
        for msg in pf.validate(data):
            out.setdefault(self._row_for(msg), []).append(self._friendly(msg))
        vals = self.values()
        if vals.get("_lossy_on") and not vals.get("destinations.lossy"):
            out.setdefault("destinations.lossy", []).append(tr("Choose the folder for lossy files."))
        return out

    @staticmethod
    def _row_for(msg) -> str:
        special = next((key for start, key in PROBLEM_ROWS if msg.startswith(start)), "")
        if special:
            return special
        keys = sorted((k for k in BY_KEY if not k.startswith("_") and
                       (msg.startswith(k + " ") or msg.startswith(k + ":"))), key=len, reverse=True)
        return keys[0] if keys else ""

    @staticmethod
    def _friendly(msg) -> str:
        return next((tr(w) for part, w in PROBLEM_WORDS.items() if part in msg), msg)

    def show_problems(self):
        found = self.problems()
        for key, row in self.rows.items():
            row.set_problem("\n".join(found.get(key, [])))
        rest = found.get("", [])
        self.banner.setText("\n".join(rest))
        self.banner.setVisible(bool(rest))
        self.problem_count = sum(len(v) for v in found.values())
        self.first_problem_key = next((k for k in self.rows if k in found), None)
        self.first_problem = self.rows[self.first_problem_key] if self.first_problem_key else None

    def fold_all(self, on):
        for f in self.folds.values():
            f.setChecked(on)
        self._remember_folds()

    def _remember_folds(self):
        _set_gui_pref(FOLDED_PREF, [s for s, f in self.folds.items() if not f.isChecked()])

    def reveal_first_problem(self):
        """Scroll to the first row with a problem, opening its section and its Advanced fold."""
        key = getattr(self, "first_problem_key", None)
        if not key:
            return
        g = GROUPS[BY_KEY[key].group] if key in BY_KEY else None
        if g and g[0] in self.folds:
            self.folds[g[0]].setChecked(True)
        if g and g[3] and g[0] in self.advanced:
            self.advanced[g[0]].setChecked(True)
        self.ensureWidgetVisible(self.rows[key])

    @staticmethod
    def _name_settings(vals) -> dict:
        """The two name settings the plan's names take from the tagger settings: the profile's
        own, else (Automatic) what the other tagger says when the profile follows it."""
        out = {}
        for key in ("ascii_filenames", "replace_spaces_with_underscores"):
            v = vals.get(f"tagger.{key}")
            if v is None and vals.get("follow_tagger_settings", True):
                try:
                    import tagger_settings
                    v = tagger_settings.picard_bool(key, False)
                except Exception:
                    v = False
            out[key] = bool(v)
        return out

    def examples(self) -> list:
        """(what, path) for each example album, named as this profile names files now."""
        vals = self.values()
        nm = {"preset": vals.get("naming.preset"), "editions": vals.get("naming.editions"),
              "template": dig(self.raw, "naming.template") or ""}
        fn = {"max_album_words": vals.get("filenames.max_album_words", 0)}
        out = []
        for what, tags in EXAMPLES:
            try:
                comps = naming.render(naming.effective_template(nm, fn), tags, self._name_settings(vals),
                                      vals.get("filenames.compat") or "windows")
                out.append((tr(what), "/".join(comps) + ".flac"))
            except Exception as exc:                            # (a script of the profile's own that fails)
                out.append((tr(what), tr("No example: {0}").format(exc)))
        return out

    def show_preview(self, vals):
        """The Names group's example: the sample album's path, the second copy's name, and a
        word when a name is too long for the limits."""
        nm = {"preset": vals.get("naming.preset"), "editions": vals.get("naming.editions"),
              "template": dig(self.raw, "naming.template") or ""}
        fn = {"max_album_words": vals.get("filenames.max_album_words", 0)}
        compat = vals.get("filenames.compat") or "windows"
        try:
            comps = naming.render(naming.effective_template(nm, fn), SAMPLE, self._name_settings(vals), compat)
        except Exception as exc:                                # (a script of the profile's own that fails)
            self.preview.setText(html.escape(tr("No example: {0}").format(exc)))
            return
        limit = vals.get("filenames.max_name_bytes") or 255
        long = [c for c in comps if len(c.encode("utf-8")) > limit - 5]
        last = comps[-1] if comps else ""
        second = last + (" (2)" if vals.get("naming.duplicate_suffix") == "number" else " ~1a2b")
        text = tr("Example: {0}").format(html.escape("/".join(comps) + ".flac"))
        text += "<br>" + tr("A second copy: {0}").format(html.escape(second + ".flac"))
        if long:
            from . import style
            text += "<br><span style=\"color:{0}\">{1}</span>".format(
                style.color("attention"), html.escape(tr("Too long for the limit, so it is shortened: {0}")
                                                      .format(long[0])))
        self.preview.setText(text)
        self.preview.setAccessibleDescription(self.preview.text())
        self.picard_btn.setEnabled(True)                     # (the naming script: always yours to edit)
        try:
            import app_settings
            days = app_settings.get("backups.keep_days", 30)
        except Exception:
            days = 30
        self.undo_note.setText(tr("Every change can be undone for {0} days (Preferences › File Safety).")
                               .format(days) if days else
                               tr("Every change can be undone (Preferences › File Safety)."))

    # ---- search --------------------------------------------------------------------------
    def search(self, text):
        """Only rows whose label, footnote or reason hold the text; their sections and
        Advanced open. "" shows everything again."""
        self._search = text = (text or "").casefold().strip()
        vals = self.values()
        for sid, box in self.sections.items():
            hit = adv_hit = False
            for s in SETTINGS:
                if GROUPS[s.group][0] != sid:
                    continue
                row = self.rows[s.key]
                shown = bool(s.show(vals)) if s.show else True
                match = not text or text in row.words() or text in tr(GROUPS[s.group][1]).casefold()
                row.setVisible(shown and match)
                if shown and match and text:
                    hit = True
                    if GROUPS[s.group][3] and sid in self.advanced:
                        adv_hit = True
                        self.advanced[sid].setChecked(True)
            for gid, g in self.groups.items():
                if GROUPS[gid][0] == sid:
                    g.tidy()
                    for extra in g.rows:
                        if not isinstance(extra, Row):
                            extra.setVisible(not text)
                    g.tidy()
            if sid in self.advanced:
                self.advanced[sid].setVisible(not text or adv_hit)
            if text and hit and sid in self.folds:           # (a match in a folded section shows)
                self.folds[sid].setChecked(True)
            box.setVisible(not text or hit)
        return text

    # ---- presets and defaults --------------------------------------------------------------
    def _differences(self, target: dict) -> list:
        out = []
        for key, v in target.items():
            spec = BY_KEY[key]
            if not same(self.value(key), v):
                out.append((key, tr(spec.label), words_of(spec, self.value(key)), words_of(spec, v)))
        return out

    def preset_values(self, preset) -> dict:
        """The defaults with what the preset sets; folders, words and what happens to files
        (unless the preset says) stay as they are."""
        target = {k: v for k, v in self.defaults.items() if not BY_KEY[k].personal and k not in PRESET_KEEPS}
        target.update(PRESETS[preset][2])
        return target

    def defaults_for(self, section=None) -> dict:
        """What Restore Defaults sets: never folders, words, or what happens to the files
        (PRESET_KEEPS), as the confirmation says."""
        return {k: v for k, v in self.defaults.items() if not BY_KEY[k].personal and k not in PRESET_KEEPS and
                (section is None or GROUPS[BY_KEY[k].group][0] == section)}

    def apply(self, target: dict):
        self._loading = True
        for key, v in target.items():
            self._set(BY_KEY[key], v)
        self._loading = False
        self._changed()

    def apply_preset(self, preset):
        self.apply(self.preset_values(preset))

    def restore_defaults(self, section=None):
        self.apply(self.defaults_for(section))

    def _ask_changes(self, headline, target, action) -> bool:
        diff = self._differences(target)
        if not diff:
            sheets.information(self, tr("Nothing would change."), tr("These settings already have those values."))
            return False
        items = "".join(f"<li>{html.escape(label)}: {html.escape(old)} → {html.escape(new)}</li>"
                        for _k, label, old, new in diff[:20])
        if len(diff) > 20:
            items += "<li>" + html.escape(tr("and {0} more").format(len(diff) - 20)) + "</li>"
        return sheets.confirm(self, headline, f"<ul>{items}</ul>",
                              tr("Folders and the description are kept. Nothing is saved until you press "
                                 "Save Profile, and a saved profile's earlier file is kept as a backup."),
                              action)

    def ask_restore(self, section=None):
        title = next((tr(t) for s, t, _h in SECTIONS if s == section), "")
        headline = tr("Restore the defaults of {0}?").format(title) if section else \
            tr("Restore the defaults of every setting?")
        target = self.defaults_for(section)
        if self._ask_changes(headline, target, tr("Restore Defaults")):
            self.apply(target)

    def choose_preset(self):
        dlg = PresetSheet(self, tr("Start from a preset"), action=tr("Continue"))
        if not sheets.run(dlg, "preset"):
            return
        key = dlg.preset()
        target = self.preset_values(key)
        if self._ask_changes(tr("Start from “{0}”?").format(tr(PRESETS[key][0])), target, tr("Use These Settings")):
            self.apply(target)

    def label_of(self, key) -> str:
        return self.rows[key].label.text()
