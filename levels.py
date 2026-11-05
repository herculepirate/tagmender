"""levels.py - how much TagMender writes: Basic, Enthusiast or Audiophile.

  basic        album artist, artist, album, title, track and disc numbers and totals, date,
               genre. Comments are never written (Picard preserves them too).
  enthusiast   everything MusicBrainz and Discogs supply, as Picard + Discogs Enrichment
               write it (IDs, label, catalogue number, barcode, credits, styles ...).
  audiophile   enthusiast, plus what is worked out from your files:
                 replaygain   ReplayGain 2.0 track and album gain and true peak (R128 gains
                              for Opus), measured with ffmpeg's EBU R128 meter
                 lyrics       LRCLIB: time-synced lyrics as .lrc files next to the tracks,
                              plain lyrics embedded (profile "lyrics" says where)
                 acoustid     the AcoustID of each track, confirmed against its MusicBrainz
                              recording (needs fpcalc and an AcoustID key)
                 rip_info     MusicBrainz disc ID from the rip log's table of contents, and
                              the log's verdict (ripper, AccurateRip, errors)
                 quality      dynamic range (DR, as the DR meter measures it) and a warning
                              when a "lossless" file was made from a lossy one

Partial albums (TAGMENDER=Partial: the album is established, its release is not; see
engine.Engine._partial) are written from PARTIAL_FIELDS only, at every level:
  basic        the Basic fields (date is the album's original date)
  enthusiast   + sort names, original date/year, release type, recording-level credits and
               works, MusicBrainz artist / release group / recording IDs, the Discogs master
               and artist IDs (never a release ID, barcode, catalogue number, label, country,
               media, pressing date, ASIN, script or release status)
  audiophile   enthusiast + the measurements (ReplayGain, dynamic range, quality, rip log
               verdict and disc ID of your own rip, AcoustID)
Lyrics are written at every level where they are looked up (Audiophile, or the profile's
"lyrics": {"enabled": true} at Basic and Enthusiast), Partial albums too.

Identification is the same at every level; the level only decides what is written. Lowering
a level never removes anything: fields a level does not write are left as they are.
Measurements never replace a value the file already has, and are cached by the audio's own
digest, so planning an album a second time is quick.

Profile:
  "tag_level": "basic" | "enthusiast" | "audiophile"
  "level_fields": {"add": ["label"], "drop": ["discogs_notes"]}   add to Basic / never write
  "audiophile": {"replaygain": true, "lyrics": true, "acoustid": true, "rip_info": true,
                 "quality": true}
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
from pathlib import Path

LEVELS = ("basic", "enthusiast", "audiophile")
BASIC_FIELDS = ("albumartist", "artist", "album", "title", "tracknumber", "totaltracks", "discnumber",
                "totaldiscs", "date", "genre")
# TagMender's own markers (tagmender_source: the web page a Partial album's tags came from)
ALWAYS = ("tagmender", "tagmender_reason", "tagmender_missing", "tagmender_source")
NEVER_AT_BASIC = ("comment",)
AUDIOPHILE_ITEMS = ("replaygain", "lyrics", "acoustid", "rip_info", "quality")
LOSSLESS = (".flac", ".wav", ".aiff", ".aif", ".wv", ".ape", ".dsf", ".dff")


def is_lossless(path) -> bool:
    """The file holds lossless audio: by its extension, and an .m4a / .mp4 by its codec
    (ALAC is lossless, AAC is not)."""
    if Path(path).suffix.lower() in LOSSLESS:
        return True
    import profiles
    return Path(path).suffix.lower() in (".m4a", ".mp4") and profiles.audio_kind(path) == "lossless"

# What a Partial album writes at each level (engine.Engine._partial gathers them; the plan
# marks the album TAGMENDER=Partial). Names ending in ":" stand for every field they begin
# (performer:guitar). Lyrics are written at any level that enables them.
PARTIAL_BASIC = BASIC_FIELDS
PARTIAL_ENTHUSIAST = PARTIAL_BASIC + (
    "albumartists", "albumartistsort", "artists", "artistsort", "originaldate", "originalyear", "releasetype",
    "compilation", "composer", "composersort", "lyricist", "writer", "arranger", "conductor", "performer:",
    "producer", "engineer", "mixer", "remixer", "djmixer", "orchestra", "chorusmaster", "work", "movement",
    "movementnumber", "movementtotal", "showmovement", "isrc", "discogs_style",
    "musicbrainz_artistid", "musicbrainz_albumartistid", "musicbrainz_releasegroupid", "musicbrainz_recordingid",
    "musicbrainz_workid", "musicbrainz_composerid", "discogs_master_id", "discogs_artist_id",
    "discogs_albumartist_id")
PARTIAL_MEASURED = ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain",
                    "replaygain_album_peak", "r128_track_gain", "r128_album_gain", "dynamic_range",
                    "album_dynamic_range", "tagmender_quality", "tagmender_rip", "musicbrainz_discid", "acoustid_id")
PARTIAL_FIELDS = {"basic": PARTIAL_BASIC, "enthusiast": PARTIAL_ENTHUSIAST,
                  "audiophile": PARTIAL_ENTHUSIAST + PARTIAL_MEASURED}

RG_FIELDS = ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak",
             "r128_track_gain", "r128_album_gain")


def level(profile) -> str:
    return profile.get("tag_level") or "enthusiast"


def items(profile) -> dict:
    """Which Audiophile measurements this profile makes. Below Audiophile none, except lyrics
    when the profile's "lyrics" says {"enabled": true}."""
    if level(profile) != "audiophile":
        return {"lyrics": True} if (profile.get("lyrics") or {}).get("enabled") else {}
    on = {k: True for k in AUDIOPHILE_ITEMS}
    on.update({k: bool(v) for k, v in (profile.get("audiophile") or {}).items() if k in on})
    return on


def _lyrics(k: str) -> bool:
    return k == "lyrics" or k.startswith("lyrics:")


def _compilation_fields(profile) -> set:
    """What the profile's compilations options write (compilations.py): kept at every level."""
    import compilations
    return compilations.written_fields(profile)


def partial_field(name: str, profile) -> bool:
    """Whether a Partial album writes this field at the profile's level (lyrics: they are only
    there when the level or the profile looks them up)."""
    k = name.lower()
    if k in ALWAYS or _lyrics(k) or k in _compilation_fields(profile):
        return True
    return any(k == f or (f.endswith(":") and k.startswith(f)) for f in PARTIAL_FIELDS[level(profile)])


# tiers whose release is not proven: they write fields of the album only (PARTIAL_FIELDS)
ALBUM_TIERS = ("partial", "album_only")


def album_field(name: str) -> bool:
    """A field of the album, not of one pressing: TagMender's markers, lyrics, or one of the
    widest level's PARTIAL_FIELDS. What a Partial or an album-only album may carry, from
    any source (engine._partial_kept, engine._album_only, web_tag.album_only_tags)."""
    k = name.lower()
    if k in ALWAYS or _lyrics(k):
        return True
    return any(k == f or (f.endswith(":") and k.startswith(f)) for f in PARTIAL_FIELDS["audiophile"])


def restrict(target: dict, profile, tier: str = "") -> dict:
    """The part of one file's tags this profile's level writes (tiers "partial" and
    "album_only": of its PARTIAL_FIELDS only, what a tagger script set included)."""
    if tier in ALBUM_TIERS:
        target = {k: v for k, v in target.items() if k.startswith("~") or partial_field(k, profile)}
        if target.get("~remove"):
            target["~remove"] = [x for x in target["~remove"] if partial_field(str(x), profile)]
    lf = profile.get("level_fields") or {}
    drop = {f.lower() for f in lf.get("drop") or []}
    if level(profile) == "basic":
        keep = set(BASIC_FIELDS) | set(ALWAYS) | {f.lower() for f in lf.get("add") or []} | _compilation_fields(profile)
        lyr = bool(items(profile).get("lyrics"))   # (looked up at Basic too when the profile says so)
        out = {k: v for k, v in target.items() if (k.lower() in keep and k.lower() not in NEVER_AT_BASIC)
               or (lyr and _lyrics(k.lower()))
               or k.startswith("~")}            # (Picard's hidden variables: for naming, never written)
        if out.get("~remove"):
            # (a comment is never written at Basic, but cleanup.comments remove takes it off)
            off = keep | ({"comment"} if (profile.get("cleanup") or {}).get("comments") == "remove" else set())
            out["~remove"] = [x for x in out["~remove"] if str(x).lower() in off]
    else:
        out = dict(target)
    out = {k: v for k, v in out.items() if k.lower() not in drop or k.lower() in ALWAYS}
    if out.get("~remove"):                       # (what Picard deletes: never a field this level leaves alone)
        out["~remove"] = [x for x in out["~remove"] if str(x).lower() not in drop or str(x).lower() in ALWAYS]
    return out


# --------------------------------------------------------------------------
# a small cache of measurements, keyed by the audio itself
# --------------------------------------------------------------------------
class Cache:
    def __init__(self, path: Path | None):
        self.db = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=60)
            self.db.execute("CREATE TABLE IF NOT EXISTS analysis (key TEXT PRIMARY KEY, value TEXT)")

    def get(self, kind, path, compute):
        key = f"{kind}:{_audio_key(path)}"
        if self.db is not None:
            row = self.db.execute("SELECT value FROM analysis WHERE key=?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
        value = compute()
        if self.db is not None and value is not None:
            self.db.execute("INSERT OR REPLACE INTO analysis VALUES (?, ?)", (key, json.dumps(value)))
            self.db.commit()
        return value

    def close(self):
        if self.db is not None:
            self.db.close()


def _audio_key(path):
    import payload
    try:
        d = payload.digest(path)
    except Exception:
        d = None
    if d:
        return d
    st = Path(path).stat()
    return f"{path}|{st.st_size}|{st.st_mtime_ns}"


# --------------------------------------------------------------------------
# measurements
# --------------------------------------------------------------------------
def loudness(path):
    """[integrated loudness LUFS, true peak dBFS, seconds] or None."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
    text = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", text)
    p = re.search(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS", text)
    if not i:
        return None
    dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    secs = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 1.0
    peak = float(p.group(1)) if p and p.group(1) != "-inf" else -120.0
    return [float(i.group(1)), peak, max(secs, 1.0)]


def replaygain(measured: dict) -> dict:
    """{file: tags} from {file: [LUFS, peak dBFS, seconds]}: ReplayGain 2.0 (reference
    -18 LUFS). The album's loudness is the duration-weighted energy mean of its tracks."""
    if not measured:
        return {}
    total = sum(m[2] for m in measured.values())
    album_i = 10 * math.log10(sum(m[2] * 10 ** (m[0] / 10) for m in measured.values()) / total)
    album_peak = max(m[1] for m in measured.values())
    out = {}
    for f, (i, peak, _d) in measured.items():
        t = {"replaygain_track_gain": f"{-18 - i:.2f} dB", "replaygain_track_peak": f"{10 ** (peak / 20):.6f}",
             "replaygain_album_gain": f"{-18 - album_i:.2f} dB",
             "replaygain_album_peak": f"{10 ** (album_peak / 20):.6f}"}
        if Path(f).suffix.lower() == ".opus":             # Opus players read R128 gains (-23 LUFS, Q7.8)
            t["r128_track_gain"] = str(round((-23 - i) * 256))
            t["r128_album_gain"] = str(round((-23 - album_i) * 256))
        out[f] = t
    return out


LOUDNESS_TAGS = ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak",
                 "r128_track_gain", "r128_album_gain")


def loudness_choices(rg: dict, opts: dict) -> dict:
    """replaygain()'s tags as the profile's loudness settings say: album_gain off - no album
    gain or peak; opus_r128 off - no R128 gains in Opus files."""
    out = {}
    for f, t in rg.items():
        t = dict(t)
        if opts.get("album_gain") is False:
            for k in ("replaygain_album_gain", "replaygain_album_peak", "r128_album_gain"):
                t.pop(k, None)
        if opts.get("opus_r128") is False:
            t.pop("r128_track_gain", None)
            t.pop("r128_album_gain", None)
        out[f] = t
    return out


def dynamic_range(path):
    """The DR meter's value for a track (integer dB) or None. Per channel, in 3-second
    blocks: RMS (x sqrt 2) and peak; DR = second-highest peak over the RMS of the loudest
    20% of blocks, averaged over the channels."""
    try:
        import numpy as np
    except ImportError:
        return None
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=sample_rate", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    m = re.search(r"\d+", probe.stdout)
    if not m:
        return None
    rate = int(m.group(0))
    block = 3 * rate
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-f", "f32le", "-"],
                            stdout=subprocess.PIPE)
    rms, peaks = [], []
    try:
        while True:
            raw = proc.stdout.read(block * 2 * 4)
            if not raw:
                break
            x = np.frombuffer(raw[:len(raw) - len(raw) % 8], dtype=np.float32).reshape(-1, 2).astype(np.float64)
            if len(x) < rate // 10:
                break
            rms.append(np.sqrt(2 * np.mean(x ** 2, axis=0)))
            peaks.append(np.max(np.abs(x), axis=0))
    finally:
        proc.stdout.close()
        proc.wait()
    if not rms:
        return None
    rms, peaks = np.array(rms), np.array(peaks)
    out = []
    for ch in range(2):
        r = np.sort(rms[:, ch])[::-1]
        top = r[:max(1, int(round(len(r) * 0.2)))]
        p = np.sort(peaks[:, ch])[::-1]
        p2 = p[1] if len(p) > 1 else p[0]
        loud = math.sqrt(float(np.mean(top ** 2)))
        if loud <= 0 or p2 <= 0:
            continue
        out.append(20 * math.log10(p2 / loud))
    return int(round(sum(out) / len(out))) if out else None


# --------------------------------------------------------------------------
# rip logs
# --------------------------------------------------------------------------
def log_verdict(text: str) -> str:
    """'EAC V1.6; AccurateRip: 8/8 accurate; no errors' from an EAC or XLD log."""
    parts = []
    m = re.search(r"Exact Audio Copy (V[\w.\- ]+?)(?: from|\s*$)", text, re.M)
    x = re.search(r"X Lossless Decoder version ([\w.\- ]+)", text)
    if m:
        parts.append(f"EAC {m.group(1).strip()}")
    elif x:
        parts.append(f"XLD {x.group(1).strip()}")
    tracks = len(set(re.findall(r"^\s*Track\s+(\d+)\s*$", text, re.M | re.I)))
    accurate = len(re.findall(r"Accurately ripped", text, re.I))
    if re.search(r"All tracks accurately ripped", text, re.I):
        parts.append("AccurateRip: all tracks accurate")
    elif accurate and tracks:
        parts.append(f"AccurateRip: {min(accurate, tracks)}/{tracks} accurate")
    elif re.search(r"not present in the AccurateRip database", text, re.I):
        parts.append("AccurateRip: not in database")
    if re.search(r"No errors occurr?ed", text, re.I):
        parts.append("no errors")
    elif re.search(r"There were errors|Suspicious position|Read error", text, re.I):
        parts.append("errors reported")
    return "; ".join(parts)


def rip_info(files) -> dict:
    """{file: {musicbrainz_discid, tagmender_rip}} from the rip log in each disc's folder,
    only where the log's table of contents fits the folder's files, track by track
    (match_albums.toc_fits_audio, R10): a log of another rip or pressing beside a download
    (Cynic - Traced in Air) gives its disc ID to no file."""
    import match_albums as ma
    out = {}
    by_dir = {}
    for f in files:
        by_dir.setdefault(Path(f).parent, []).append(f)
    for folder, fs in by_dir.items():
        logs = sorted(folder.glob("*.log"))
        if len(logs) != 1:
            continue
        log = next((x for x in ma.rip_logs(folder) if Path(x["path"]) == logs[0]), None)
        toc = (log or {}).get("toc")
        if not toc or int(toc.split()[1]) != len(fs):
            continue
        # in track order: an untagged file's number from its name ("2 - x" before "10 - x")
        infos = [(f, ma.read_file(Path(f))) for f in fs]
        ma.names_from_files(infos)
        infos.sort(key=lambda x: (x[1]["num"] or 9999, ma._natural_key(str(x[0]))))
        if not ma.fitting_logs(infos, [log])[0]:
            continue                                     # made of other files: not this disc's ID
        tags = {"musicbrainz_discid": ma.mb_discid(toc)}
        try:
            verdict = log_verdict(ma._read_log(logs[0]))
        except OSError:
            verdict = ""
        if verdict:
            tags["tagmender_rip"] = verdict
        for f in fs:
            out[f] = dict(tags)
    return out


# --------------------------------------------------------------------------
# lyrics
# --------------------------------------------------------------------------
INSTRUMENTAL = ("ambient", "drone", "new age", "instrumental", "soundtrack", "score", "classical",
                "neoclassical", "minimal", "field recording", "electroacoustic", "space music",
                "berlin school", "meditation", "post-rock")


def instrumental(folder, target) -> bool:
    words = " ".join(str(v) for k in ("genre", "discogs_style") for v in target.get(k) or []).casefold()
    if any(g in words for g in INSTRUMENTAL):
        return True
    path = str(folder).casefold()
    return "ambient" in path or bool(re.search(r"no ?vox|instrumental", path))


def lyrics_for(http, target, seconds, synced=True):
    """(text, synced) from LRCLIB for one track, or ("", False). synced False: the plain
    text when LRCLIB has it (the time-synced text only when it has nothing else). Raises
    when LRCLIB cannot be reached."""
    import urllib.parse as up
    artist = (target.get("artist") or target.get("albumartist") or [""])[0]
    title = (target.get("title") or [""])[0]
    album = (target.get("album") or [""])[0]
    if not artist or not title:
        return "", False
    d = http.get_json(f"https://lrclib.net/api/get?artist_name={up.quote(artist)}&track_name={up.quote(title)}"
                      f"&album_name={up.quote(album)}&duration={round(seconds)}", "lrclib") or {}
    if d.get("instrumental"):
        return "", False
    if not synced and d.get("plainLyrics"):
        return d["plainLyrics"], False
    if d.get("syncedLyrics"):
        return d["syncedLyrics"], True
    return d.get("plainLyrics") or "", False


def _seconds(path) -> float:
    try:
        import mutagen
        return float(mutagen.File(path).info.length or 0)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# AcoustID
# --------------------------------------------------------------------------
def acoustid_for(http, path, recording_id, db=None):
    """The AcoustID whose recordings include this MusicBrainz recording, or ""."""
    import match_albums as ma
    import urllib.parse as up
    key = ma.acoustid_key()
    fp = ma.fingerprint(str(path), db)
    if not key or not fp:
        return ""
    data = http.get_json(f"https://api.acoustid.org/v2/lookup?client={up.quote(key)}&format=json"
                         f"&meta=recordingids&duration={fp['duration']}"
                         f"&fingerprint={up.quote(fp['fingerprint'])}", "acoustid") or {}
    for res in sorted(data.get("results") or [], key=lambda r: -r.get("score", 0)):
        if res.get("score", 0) >= 0.8 and any(r.get("id") == recording_id for r in res.get("recordings") or []):
            return res["id"]
    return ""


# --------------------------------------------------------------------------
# what is missing, said with the Setup notice (which program or key, where to get it)
# --------------------------------------------------------------------------
def _ffmpeg_note(what):
    """"<what>: <ffmpeg's notice>" when ffmpeg is not installed, else ""."""
    import shutil
    import requirements as rq
    if shutil.which("ffmpeg"):
        return ""
    return f"{what}. {rq.problem('ffmpeg')}"


def _fingerprint_note():
    """Why AcoustID IDs are not added: fpcalc, the AcoustID application key, or both."""
    import requirements as rq
    found = [n for n in (rq.problem("fpcalc"), rq.problem("acoustid")) if n]
    return " ".join(["AcoustID IDs not added."] + (found or ["Fingerprints are not ready."]))


# --------------------------------------------------------------------------
# putting it together for one identified album
# --------------------------------------------------------------------------
def audiophile(prop, profile, http, cache: Cache, log=None) -> list[str]:
    """Adds the Audiophile measurements to prop.targets (and lyrics files to prop.sidecars).
    Returns notes for the plan ("why"), e.g. what could not be measured."""
    import tagio
    on = items(profile)
    if not on or prop.status != "identified" or not prop.targets:
        return []
    files = [Path(f) for f in prop.files]
    have = []
    for f in files:
        try:
            have.append(tagio.read(f))
        except Exception:
            have.append({})
    notes = []
    extra = [dict() for _ in files]

    if on.get("replaygain"):
        measured = {}
        for f in files:
            m = cache.get("loudness", f, lambda f=f: loudness(f))
            if m:
                measured[str(f)] = m
        if len(measured) == len(files):
            rg = loudness_choices(replaygain(measured), profile.get("loudness") or {})
            for i, f in enumerate(files):
                extra[i].update(rg.get(str(f), {}))
        else:
            notes.append(_ffmpeg_note("ReplayGain not written") or
                         "ReplayGain not written: ffmpeg could not measure every track")

    if on.get("quality"):
        drs = [cache.get("dr", f, lambda f=f: dynamic_range(f)) for f in files]
        if all(d is not None for d in drs):
            album_dr = int(round(sum(drs) / len(drs)))
            for i, d in enumerate(drs):
                extra[i]["dynamic_range"] = str(d)
                extra[i]["album_dynamic_range"] = str(album_dr)
        else:
            notes.append("dynamic range not measured (needs numpy and ffmpeg)")
        lossless = [f for f in files if is_lossless(f)]
        if lossless:
            import enrich
            # lossy-source check: a brick-wall cutoff (~16-20 kHz) in a "lossless" file. A note
            # and tagmender_quality only; identification never uses it.
            if enrich.spectrum_tools():
                # "lossy-check", not "quality": older runs cached "" for albums they could not
                # measure. quality() gives None then, which the cache never stores.
                finding = cache.get("lossy-check", lossless[0], lambda: enrich.quality(lossless))
                if finding is None:
                    notes.append("lossy-source check skipped: a track could not be measured")
                finding = finding or ""
            else:
                finding = ""
                notes.append(_ffmpeg_note("lossy-source check skipped") or
                             "lossy-source check skipped: needs ffprobe (it comes with ffmpeg)")
            if finding:
                for i in range(len(files)):
                    extra[i]["tagmender_quality"] = finding
                notes.append(f"quality: {finding}")
                prop.suspect_lossy = finding          # (the plan holds or moves it: quality_action)

    if on.get("rip_info"):
        found = rip_info(files)
        for i, f in enumerate(files):
            extra[i].update(found.get(f, {}))

    if on.get("acoustid"):
        import match_albums as ma
        if not ma.fingerprint_ready():
            notes.append(_fingerprint_note())
        else:
            for i, f in enumerate(files):
                rec = (prop.targets[i].get("musicbrainz_recordingid") or [""])[0]
                if rec:
                    aid = cache.get(f"acoustid-{rec}", f, lambda f=f, rec=rec: acoustid_for(http, f, rec, http.db))
                    if aid:
                        extra[i]["acoustid_id"] = aid

    sidecars = {}
    if on.get("lyrics"):
        opts = profile.get("lyrics") or {}
        if instrumental(prop.source_dir, prop.targets[0]):
            notes.append("lyrics not looked up: instrumental album")
        else:
            misses = 0
            for i, f in enumerate(files):
                if any(k in ("lyrics", "unsyncedlyrics") or k.startswith("lyrics:") for k in have[i]) or f.with_suffix(".lrc").exists():
                    continue
                secs = _seconds(f)
                try:
                    text, synced = lyrics_for(http, prop.targets[i], secs, bool(opts.get("synced", True)))
                except Exception as exc:
                    notes.append(f"lyrics: LRCLIB could not be reached ({exc}); try again later")
                    break
                if not text:
                    misses += 1
                    if misses >= 3 and not sidecars and not any("lyrics" in e for e in extra):
                        notes.append("lyrics: none found for the first tracks; not looked up further")
                        break
                    continue
                if synced and opts.get("sidecar", True):
                    sidecars[str(f)] = text
                if opts.get("embed", False) or not synced or not opts.get("sidecar", True):
                    extra[i]["lyrics"] = text

    # measurements never replace what the file already has - but ReplayGain does when the
    # profile says to recalculate it (loudness.replace_existing)
    again = (profile.get("loudness") or {}).get("replace_existing") is True
    for i in range(len(files)):
        for k, v in extra[i].items():
            renew = again and k in LOUDNESS_TAGS
            if (renew or not have[i].get(k)) and not (k == "lyrics" and any(x.startswith("lyrics:") or
                                                                           x == "unsyncedlyrics" for x in have[i])):
                prop.targets[i][k] = v if isinstance(v, list) else [v]
    if sidecars:
        prop.sidecars = sidecars
    return notes
