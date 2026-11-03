"""tagger_options.py - Picard's settings for standalone recordings, ID3 text encoding and cover
art, read from Picard.ini (through tagger_settings, which finds and parses it; never written).

  settings(profile) -> dict     nat_name, id3v2_encoding, caa_approved_only, caa_image_size,
                                caa_restrict_image_types, caa_image_types,
                                caa_image_types_to_omit, ca_providers [(name, enabled)],
                                cover_image_filename; "notes": what could not be read
  id3_encoding(name, v23) -> int   the ID3 text encoding Picard writes (0 Latin-1, 1 UTF-16,
                                3 UTF-8)

An enabled Picard 3 option profile wins over [setting], as in Picard. A profile with
"follow_picard": false, or no Picard.ini at all, gets TagMender's own behaviour (NO_INI):
covers as before (front, 1200 px, release then release group, cover.jpg), UTF-8, and
Picard's standalone recordings name. A Picard.ini without one of these settings means
Picard's default for it (Picard 2's for a Picard 2 Picard.ini).
"""
from __future__ import annotations

from pathlib import Path

import tagger_settings as pc

# Picard 3's defaults (picard/options.py, picard/const/defaults.py)
DEFAULTS = {
    "nat_name": "[standalone recordings]",
    "id3v2_encoding": "utf-8",                    # utf-8 | utf-16 | iso-8859-1
    "caa_approved_only": False,
    "caa_image_size": 500,                        # 250 | 500 | 1200 | -1 (full size)
    "caa_restrict_image_types": True,
    "caa_image_types": ["front"],
    "caa_image_types_to_omit": ["matrix/runout", "raw/unedited", "watermark"],
    "ca_providers": [("CaaReleaseGroup", True), ("Cover Art Archive", True), ("UrlRelationships", True),
                     ("Local", False)],
    "cover_image_filename": "cover",
}
# Picard 2's, where they differ
PICARD2 = {"ca_providers": [("Cover Art Archive", True), ("UrlRelationships", True), ("CaaReleaseGroup", False),
                            ("Local", False)],
           "caa_image_types_to_omit": []}
# without Picard.ini: what TagMender did before it read these settings
NO_INI = dict(DEFAULTS, caa_image_size=1200, caa_image_types_to_omit=[],
              ca_providers=[("Cover Art Archive", True), ("CaaReleaseGroup", True)])
ENCODINGS = ("utf-8", "utf-16", "iso-8859-1")
CAA_SIZES = (250, 500, 1200, -1)
# Picard's provider names for what TagMender's "caa" source does
CAA_RELEASE, CAA_GROUP = "Cover Art Archive", "CaaReleaseGroup"
LOCAL_NAMES = ("Local", "Local Files")


def id3_encoding(name: str, v23: bool = False) -> int:
    """The ID3 text encoding Picard writes for its id3v2_encoding setting: UTF-8 is ID3v2.4's
    only (Picard's options page turns it into UTF-16 for ID3v2.3, and mutagen writes it so)."""
    enc = {"utf-8": 3, "utf-16": 1, "iso-8859-1": 0}.get(str(name or "").lower(), 3)
    return 1 if enc == 3 and v23 else enc


def _providers(raw, notes) -> list:
    """[(name, enabled)] from Picard's ca_providers: each item a pickled (name, enabled) pair
    in Qt's @Variant form, decoded by tagger_settings without ever unpickling an object (an item
    that is not plain data is skipped, and noted)."""
    vals = []
    if isinstance(raw, str):
        for it in pc._qt_unescape(raw):
            if it.startswith("@Variant(") and it.endswith(")"):
                v = pc._qt_variant(it[9:-1])
                if v is None:
                    notes.append("a cover art provider in your tagger settings could not be read; skipped")
                    continue
                vals += v if v and isinstance(v[0], (list, tuple)) else [v]
            elif it.strip().startswith("@"):
                # (Qt's other markers, such as @Invalid() for a null value: never a provider name)
                notes.append(f"a cover art provider in your tagger settings is Qt's {it.strip()}; skipped")
            elif it.strip():
                vals.append([it.strip(), True])       # (a plain name: enabled)
    elif isinstance(raw, (list, tuple)):
        vals = list(raw)
    out = []
    for x in vals:
        if isinstance(x, (list, tuple)) and len(x) == 2 and isinstance(x[0], str):
            out.append((x[0], bool(x[1])))
        elif isinstance(x, str) and x and not x.strip().startswith("@"):
            out.append((x, True))                     # (Qt's @ markers: never a provider name)
        else:
            notes.append("a cover art provider in your tagger settings is not a (name, enabled) pair; skipped")
    return out


def _parse(k, raw, notes):
    """One setting from its Picard.ini text (or an option profile's value)."""
    want = DEFAULTS[k]
    if k == "ca_providers":
        return _providers(raw, notes)
    if isinstance(want, bool):
        return raw if isinstance(raw, bool) else pc._qt_bool(str(raw))
    if isinstance(want, list):
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return pc._qt_list(str(raw))
    if k == "caa_image_size":
        try:
            v = int(str(raw if not isinstance(raw, str) else pc._qt_value(raw)).strip())
        except ValueError:
            notes.append(f"caa_image_size {raw!r} is not a size; the default used")
            return want
        if v not in CAA_SIZES:
            notes.append(f"caa_image_size {v} is not one of the Cover Art Archive sizes; the default used")
            return want
        return v
    v = raw if not isinstance(raw, str) else pc._qt_text(raw)
    if k == "id3v2_encoding":
        v = str(v).strip().lower()
        if v not in ENCODINGS:
            notes.append(f"id3v2_encoding {v!r} is not one of {', '.join(ENCODINGS)}; UTF-8 used")
            return want
    return str(v)


def _has_items(raw) -> bool:
    """A ca_providers value with something in it (an empty one is every provider off)."""
    if isinstance(raw, str):
        return any(it.strip() for it in pc._qt_unescape(raw))
    return bool(raw)


def _null(raw) -> bool:
    """Qt's null value (@Invalid()): the setting is not set, so Picard's default applies."""
    return isinstance(raw, str) and raw.strip() == "@Invalid()"


def _follows(profile) -> bool:
    """The profile reads the tagger settings (follow_tagger_settings; older "follow_picard")."""
    if hasattr(profile, "follows_tagger"):
        return profile.follows_tagger()
    if not hasattr(profile, "get"):
        return True
    return bool(profile.get("follow_tagger_settings", profile.get("follow_picard", True)))


def settings(profile=None, path: Path | None = None) -> dict:
    """These Picard settings for a profile (see the module's text)."""
    follow = profile is None or _follows(profile)
    p = path if path is not None else pc.ini_path()
    cp = pc._read(p) if follow else None
    notes = []
    if cp is None:
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in NO_INI.items()}
        out["notes"] = notes
        out["source"] = "TagMender's defaults"
        return out
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    ver = pc.picard_version(p)
    if ver and ver[0] < 3:
        out.update({k: list(v) for k, v in PICARD2.items()})
    for k in DEFAULTS:
        found, v = pc._from_profiles(cp, k)
        if not (found and not _null(v)):
            if not (cp.has_option("setting", k) and not _null(cp.get("setting", k))):
                continue
            v = cp.get("setting", k)
        before = len(notes)
        val = _parse(k, v, notes)
        if (k == "ca_providers" and len(notes) > before and _has_items(v)
                and not any(n in (CAA_RELEASE, CAA_GROUP) for n, _on in val)):
            # an item that could not be read is not a provider switched off: when it may have
            # been the Cover Art Archive (none of the items read is), Picard's own order, rather
            # than no Cover Art Archive at all
            notes.append("ca_providers could not be read; the default order used")
            val = [tuple(x) for x in (PICARD2 if ver and ver[0] < 3 else DEFAULTS)["ca_providers"]]
        out[k] = val
    if not str(out["nat_name"]).strip():
        out["nat_name"] = DEFAULTS["nat_name"]       # (Picard's options page puts the default back)
    out["notes"] = notes
    out["source"] = str(p)
    return out


def caa_kinds(s: dict) -> list:
    """What TagMender's Cover Art Archive source asks for, in Picard's provider order:
    "release" (Picard's "Cover Art Archive") and "release-group" ("CaaReleaseGroup"), each when
    enabled there."""
    out = []
    for name, on in s.get("ca_providers") or []:
        if on and name == CAA_RELEASE and "release" not in out:
            out.append("release")
        elif on and name == CAA_GROUP and "release-group" not in out:
            out.append("release-group")
    return out


def unused_providers(s: dict) -> list:
    """Picard's enabled cover art providers TagMender has no source for (URL relationships,
    Amazon, plugin providers ...): listed, never used."""
    return [n for n, on in s.get("ca_providers") or [] if on and n not in (CAA_RELEASE, CAA_GROUP) + LOCAL_NAMES]
