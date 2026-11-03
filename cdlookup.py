"""cdlookup.py - Picard's Lookup CD, without Qt: a disc's table of contents (TOC) from the CD in
an optical drive, an EAC or XLD rip log, or a TOC pasted as text; its MusicBrainz disc ID; and the
releases that have that disc, through the matcher's own disc lookup (match_albums.toc_releases:
MusicBrainz by disc ID, exact or fuzzy, and the CUETools database's MusicBrainz and Discogs links).

Reading a CD needs python-discid (libdiscid). It is optional and imported only when a drive is
read: without it, a rip log or a pasted TOC still works (NO_DISCID says what to install).

Nothing here writes a music file. Looking up a disc sends its disc ID and TOC to MusicBrainz and
the CUETools database, then the release IDs found to MusicBrainz (and to Discogs when it is on;
sent_text says all of it, for the sheet that asks first); a release chosen from the
rows goes through the forced-release flow (gui.search.force_release) and is previewed like any
other. Run read_drive, read_log and lookup on a worker (gui.work.start): they wait on the drive,
the disk or the network.

A disc is a dict: toc (MusicBrainz's form, "1 N leadout offset1 ... offsetN"), id (the disc ID),
tracks, seconds, source (where the TOC came from, in words), mcn (the barcode a drive reads, or
""), isrcs ({track: ISRC}). lookup() gives the rows of gui.versions (source, id, ref, title, ...)
with "fit" saying how each release was found."""
from __future__ import annotations

import os
import re
from pathlib import Path

import match_albums as ma
import requirements

# (the app says it in plain words, gui.browser.NO_DISCID; the install details are in docs/FIRST_RUN.md)
NO_DISCID = (requirements.notice("discid", state=requirements.MISSING)
             + " A rip log or a pasted table of contents works without it.")
# What Look Up sends, said in the sheet before anything is sent (sent_text puts it together; every
# request lookup() makes is named here)
SENT = ("Look Up sends the disc ID {id} and its table of contents ({tracks} track offsets and the disc's "
        "length) to MusicBrainz and to the CUETools database (db.cue.tools), then asks MusicBrainz for the "
        "details of the releases found, by their release IDs.")
SENT_DISCOGS = (" It also asks Discogs (api.discogs.com) for the releases the CUETools database links, by "
                "their Discogs release IDs, with your Discogs token.")
SENT_END = " Nothing else is sent."
SENT_OFF = "MusicBrainz is off in this profile, so Look Up sends nothing."
MAX_SECTORS = 99 * 60 * 75 + 150     # (no audio CD is longer than 99 minutes)
EXTRA_RELEASES = 15                  # releases only CTDB names, read one by one (one request a second)
FOUND = {"exact": "This disc ID", "fuzzy": "Same track layout", "ctdb": "CUETools database link"}
FIT_COL = ("fit", "Found by")


class CdError(ValueError):
    """A TOC that cannot be read or used, a drive that cannot be read: the message says why in
    plain words."""


# ---- the drive ----------------------------------------------------------------
def discid_module():
    """python-discid, or None when it (or libdiscid under it) is not installed."""
    try:
        import discid
    except (ImportError, OSError):               # (OSError: the module is there, libdiscid is not)
        return None
    return discid


def can_read_drive() -> bool:
    return discid_module() is not None


def drives() -> list:
    """The optical drives to offer: libdiscid's default first, then /dev/sr0 ... ([] without
    python-discid: there is nothing to read them with)."""
    dm = discid_module()
    if dm is None:
        return []
    out = []
    try:
        out.append(dm.get_default_device())
    except Exception:                            # (libdiscid knows no default here)
        pass
    out = [d for d in out if d]
    seen = {os.path.realpath(d) for d in out}      # (/dev/cdrom is often a link to /dev/sr0: one drive)
    for p in sorted(Path("/dev").glob("sr[0-9]*")) + [Path("/dev/cdrom")]:
        real = os.path.realpath(p)
        if p.exists() and real not in seen:
            seen.add(real)
            out.append(str(p))
    return out


def read_drive(device=None) -> dict:
    """The disc in `device` (libdiscid's default drive when None), with its barcode (MCN) and
    ISRCs when the drive reads them. CdError when python-discid is missing or the drive has no
    audio CD."""
    dm = discid_module()
    if dm is None:
        raise CdError(NO_DISCID)
    want = [f for f in ("mcn", "isrc") if f in getattr(dm, "FEATURES", ())]
    try:
        d = dm.read(device or None, features=want)
    except getattr(dm, "DiscError", OSError) as exc:
        raise CdError(f"The CD in {device or 'the drive'} could not be read: {exc}") from None
    isrcs = {}
    for t in getattr(d, "tracks", None) or ():
        code = (getattr(t, "isrc", "") or "").strip()
        if code and code.strip("0"):
            isrcs[int(t.number)] = code
    mcn = str(getattr(d, "mcn", "") or "").strip()
    out = disc(d.toc_string, f"the CD in {device or 'the drive'}", mcn=mcn if mcn.strip("0") else "", isrcs=isrcs)
    if getattr(d, "id", "") and d.id != out["id"]:        # (never: the same sum of the same TOC)
        raise CdError(f"The drive's disc ID {d.id} differs from the one worked out from its TOC.")
    return out


# ---- a TOC from text or a log -------------------------------------------------
_TOC_IN_URL = re.compile(r"[?&]toc=([0-9+ %2Bb]+)", re.I)


def parse_toc(text) -> str:
    """A TOC in MusicBrainz's form from what a person pastes: the numbers ("1 12 242457 150 ...",
    spaces or "+"), a MusicBrainz link carrying ?toc=, a CUETools TOC ("0:17287:...:242307",
    sectors from 0, the lead-out last) or the text of an EAC/XLD log. CdError otherwise."""
    text = (text or "").strip()
    if not text:
        raise CdError("Paste a table of contents, or the text of a rip log.")
    if "|" in text and "\n" in text:                       # (a log's TOC table)
        toc, _data = ma._log_toc(text)
        if not toc:
            raise CdError("No table of contents found in that text. EAC and XLD logs carry one near the top.")
        return check_toc(toc)
    m = _TOC_IN_URL.search(text)
    if m:
        text = m.group(1).replace("%2B", " ").replace("%2b", " ")
    if re.fullmatch(r"\d+(?::\d+)+", text):                # CUETools DB: offsets, then the lead-out
        nums = [int(x) + 150 for x in text.split(":")]
        return check_toc(" ".join(str(x) for x in [1, len(nums) - 1, nums[-1]] + nums[:-1]))
    if re.fullmatch(r"[A-Za-z0-9._]{27}-", text):
        raise CdError("That is a disc ID. Its table of contents is needed too: paste the numbers "
                      "(\"1 12 242457 150 …\") or the rip log.")
    if not re.fullmatch(r"\d+(?:[\s+]+\d+)*", text):
        raise CdError("That is not a table of contents: it should be numbers, such as \"1 12 242457 150 17437 …\".")
    return check_toc(" ".join(re.split(r"[\s+]+", text)))


def check_toc(toc) -> str:
    """`toc` when MusicBrainz can take it (first track 1, a start for each track, rising, inside
    the disc), as one line of single spaces; CdError with what is wrong otherwise."""
    nums = [int(x) for x in str(toc).split()]
    if len(nums) < 4:
        raise CdError("A table of contents needs the first and last track, the disc's length and each "
                      "track's start.")
    first, last, leadout, offsets = nums[0], nums[1], nums[2], nums[3:]
    if first != 1:
        raise CdError("The table of contents must start at track 1.")
    if not 1 <= last <= 99:
        raise CdError("A CD has 1 to 99 tracks.")
    if len(offsets) != last - first + 1:
        raise CdError(f"The table of contents names {last} tracks but gives {len(offsets)} track starts.")
    if offsets[0] < 150 or any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise CdError("The track starts must rise, from 150 sectors (the first track's pregap).")
    if not offsets[-1] < leadout <= MAX_SECTORS:
        raise CdError("The disc's length must come after the last track's start, within 99 minutes.")
    return " ".join(str(x) for x in nums)


def read_log(path) -> dict:
    """The disc of an EAC or XLD rip log (an Enhanced CD's data track left out, as MusicBrainz
    counts it)."""
    p = Path(path)
    try:
        text = ma._read_log(p)
    except OSError as exc:
        raise CdError(f"{p.name} could not be read: {exc.strerror or exc}") from None
    toc, _data = ma._log_toc(text)
    if not toc:
        raise CdError(f"No table of contents found in {p.name}. EAC and XLD logs carry one; a log of an "
                      "older EAC, or of another program, may not.")
    return disc(check_toc(toc), f"the rip log {p.name}")


def disc(toc, source="", mcn="", isrcs=None) -> dict:
    """A disc from a checked TOC."""
    toc = check_toc(toc)
    nums = [int(x) for x in toc.split()]
    return {"toc": toc, "id": ma.mb_discid(toc), "tracks": nums[1], "seconds": (nums[2] - 150) // 75,
            "source": source, "mcn": mcn or "", "isrcs": dict(isrcs or {})}


def from_text(text) -> dict:
    return disc(parse_toc(text), "the table of contents you pasted")


def length_text(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def describe(d) -> str:
    """"12 tracks, 52:41, from the rip log x.log" (plain words; the disc ID is shown on its own)."""
    words = [f"{d['tracks']} track{'s' if d['tracks'] != 1 else ''}", length_text(d["seconds"])]
    if d.get("mcn"):
        words.append(f"barcode {d['mcn']}")
    if d.get("isrcs"):
        words.append(f"{len(d['isrcs'])} ISRC{'s' if len(d['isrcs']) != 1 else ''}")
    return ", ".join(words) + (f", from {d['source']}" if d.get("source") else "")


def sent_text(d, mb=True, discogs=False) -> str:
    """What Look Up sends for disc `d`: `mb` and `discogs` say whether the profile uses them (pass
    the same `discogs` to lookup(), so it asks Discogs only when the sheet said so)."""
    if not mb:
        return SENT_OFF
    return SENT.format(id=d["id"], tracks=d["tracks"]) + (SENT_DISCOGS if discogs else "") + SENT_END


def submit_url(d) -> str:
    """MusicBrainz's page to attach this disc ID to a release (Picard's Submit disc ID): opened in
    the browser, where the person chooses the release and signs in; TagMender sends nothing."""
    return (f"https://musicbrainz.org/cdtoc/attach?id={d['id']}&tracks={d['tracks']}"
            f"&toc={d['toc'].replace(' ', '+')}")


# ---- the lookup ---------------------------------------------------------------
def lookup(lk, d, cancel=None, discogs=True) -> dict:
    """The releases with this disc: {"rows" (exact disc ID first, then the same track layout,
    then CUETools database links), "notes" (lookups that failed, providers that are off),
    "attached" (True when MusicBrainz has this disc ID, None when that is unknown), "disc"}.
    `lk` is a gui.versions.Lookup; a failed lookup is a note, never an exception. Discogs is
    asked only when `lk` uses it and `discogs` is true (False: the sheet did not say it would be)."""
    from gui import versions
    cancel = cancel or (lambda: False)
    out = {"rows": [], "notes": [], "attached": None, "disc": d}
    if not lk.use_mb:
        out["notes"].append("MusicBrainz is off in this profile, so the disc is not looked up.")
        return out
    album = {"tocs": {1: d["toc"]}}
    mb_ids = ma.toc_releases(lk.http, album)               # (MusicBrainz exact or fuzzy, and CTDB)
    out["notes"] += ma.clue_failure_notes(album)
    exact = album.get("exact_toc_releases") or set()
    out["attached"] = (album.get("discids") or [(None, None, None)])[0][2]
    rows, how = {}, {}
    if mb_ids and not cancel():                             # (one more request, with the artists and labels)
        try:
            data = lk.http.get_json(f"https://musicbrainz.org/ws/2/discid/{d['id']}?toc={d['toc'].replace(' ', '+')}"
                                    "&cdstubs=no&inc=artist-credits+labels&fmt=json", "mb") or {}
        except ma._CLUE_ERRORS as exc:
            data = {}
            out["notes"].append(f"MusicBrainz's release details did not come ({ma._clue_error_text(exc)}).")
        for r in data.get("releases") or ():
            if r.get("id") in mb_ids and r["id"] not in rows:
                rows[r["id"]] = versions.mb_release_row(r)
                how[r["id"]] = "exact" if r["id"] in exact else "fuzzy"
    rest = sorted(mb_ids - set(rows))
    for rid in rest[:EXTRA_RELEASES]:                        # (named by CTDB only)
        if cancel():
            break
        try:
            r = lk.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}?inc=artist-credits+labels+media"
                                 "&fmt=json", "mb")
        except ma._CLUE_ERRORS:
            r = None
        row = versions.mb_release_row(r) if r else dict(versions.mb_release_row({"id": rid}),
                                                        title="(details did not come)")
        rows[rid] = row
        how[rid] = "exact" if rid in exact else "ctdb"
    if len(rest) > EXTRA_RELEASES:
        out["notes"].append(f"{len(rest) - EXTRA_RELEASES} more MusicBrainz releases linked by the CUETools "
                            "database are not listed.")
    ask_discogs, discogs = discogs, sorted(album.get("toc_discogs") or (), key=int)
    if discogs and not (lk.use_discogs and ask_discogs):
        why = (lk.notes.get("discogs", "the profile does not use it") if not lk.use_discogs else
               "Look Up did not say it would be asked")
        why = why.split(": ", 1)[1] if why.startswith("missing_credentials: ") else why
        links = f"The CUETools database also links {len(discogs)} Discogs release{'s' if len(discogs) != 1 else ''}"
        # (a Setup notice is a sentence of its own: "Discogs is not used: no Discogs token. Add one ...")
        out["notes"].append(f"{links}. {why}" if why.startswith("Discogs is not used")
                            else f"{links}; Discogs is off: {why}.")
    elif discogs:
        stopped = False
        for rid in discogs[:EXTRA_RELEASES]:
            if cancel():
                stopped = True
                break
            try:
                rel = lk.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            except ma.AuthError:                          # (the token was refused: ask no more)
                out["notes"].append("Discogs rejected your token, so its releases are not listed.")
                stopped = True
                break
            except ma._CLUE_ERRORS:
                rel = None
            row = versions.discogs_release_row(rel, lk.dm) if rel else dict(
                versions.discogs_result_row({"id": rid}), title="(details did not come)")
            rows[f"discogs:{rid}"] = row
            how[f"discogs:{rid}"] = "ctdb"
        if len(discogs) > EXTRA_RELEASES and not stopped:
            out["notes"].append(f"{len(discogs) - EXTRA_RELEASES} more Discogs releases linked by the CUETools "
                                "database are not listed.")
    order = {"exact": 0, "fuzzy": 1, "ctdb": 2}
    for key in sorted(rows, key=lambda k: order[how[k]]):     # (stable: MusicBrainz's order within each)
        out["rows"].append(dict(rows[key], fit=FOUND[how[key]]))
    return out


def result_text(res) -> str:
    """The status line after a lookup: what was found, then the notes."""
    rows = res.get("rows") or []
    n_exact = sum(1 for r in rows if r.get("fit") == FOUND["exact"])
    if not rows:
        head = ("No release has this disc on MusicBrainz or the CUETools database. Submit Disc ID… opens "
                "MusicBrainz to add it to its release.")
    elif n_exact:
        head = (f"{len(rows)} release{'s' if len(rows) != 1 else ''} found, {n_exact} with this very disc ID.")
    else:
        head = (f"{len(rows)} release{'s' if len(rows) != 1 else ''} found with the same track layout; none has "
                "this disc ID yet (Submit Disc ID… adds it).")
    return " ".join([head] + list(res.get("notes") or []))
