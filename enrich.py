#!/usr/bin/env python3
"""enrich.py - finishing touches for tagged albums.

  --replaygain   loudness (ReplayGain 2.0, EBU R128 via ffmpeg) per track and album,
                 so albums play at an even volume (R128_* too for Opus)
  --genres       genres from the album's MusicBrainz release group or Discogs
                 release (styles), mapped to your own list (~/.config/tagmender/genres.json)
  --lyrics       lyrics from LRCLIB (free, no key), time-synced where available;
                 --lrc also saves .lrc files next to the tracks
  --quality      finds "lossless" files transcoded from MP3/AAC (nothing above
                 16-19.5 kHz) and "hi-res" files upsampled from CD audio (nothing
                 above 22 kHz); writes TAGMENDER_QUALITY and a report
  --navidrome    asks Navidrome to rescan (~/.config/tagmender/navidrome.json:
                 {"url": "http://server:4533", "user": "...", "password": "..."})

Existing values are kept unless --overwrite. Dry run by default; --apply writes;
--undo <run> restores the tags exactly.

  python3 enrich.py /media/unraidmedia2/music/extreme_music --replaygain --genres --lyrics --quality
  python3 enrich.py /media/unraidmedia2/music/extreme_music --replaygain --genres --lyrics --quality --apply
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import tag_albums as ta  # noqa: E402
import exclusions  # noqa: E402

import mutagen  # noqa: E402

CONFIG = Path("~/.config/tagmender").expanduser()
q = urllib.parse.quote


# --------------------------------------------------------------------------
# Reading and writing a few tags in any format
# --------------------------------------------------------------------------
def get(path, name):
    """One tag's value (Vorbis name, e.g. GENRE, LYRICS, MUSICBRAINZ_RELEASEGROUPID)."""
    f = mutagen.File(path)
    tags = getattr(f, "tags", None) or {}
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    if isinstance(tags, ID3):
        frames = {"GENRE": "TCON", "LYRICS": "USLT", "TITLE": "TIT2", "ARTIST": "TPE1", "ALBUM": "TALB"}
        if name in frames:
            fr = tags.getall(frames[name])
            return str(fr[0].text if name == "LYRICS" else fr[0].text[0]) if fr else ""
        names = {"MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
                 "MUSICBRAINZ_ALBUMID": "MusicBrainz Album Id"}
        fr = tags.getall(f"TXXX:{names.get(name, name)}")
        return str(fr[0].text[0]) if fr else ""
    if isinstance(f, MP4):
        atoms = {"GENRE": "\xa9gen", "LYRICS": "\xa9lyr", "TITLE": "\xa9nam", "ARTIST": "\xa9ART", "ALBUM": "\xa9alb"}
        names = {"MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
                 "MUSICBRAINZ_ALBUMID": "MusicBrainz Album Id"}
        v = tags.get(atoms.get(name) or f"----:com.apple.iTunes:{names.get(name, name)}")
        if not v:
            return ""
        v = v[0]
        return v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)
    low = {str(k).upper(): v for k, v in tags.items()}
    v = low.get(name)
    return str(v[0] if isinstance(v, list) else v) if v else ""


def put(path, values):
    """Write {VORBIS_NAME: value or [values]} in the file's own format."""
    from mutagen.id3 import ID3, TXXX, TCON, USLT
    from mutagen.mp4 import MP4, MP4FreeForm
    f = mutagen.File(path)
    if f.tags is None:
        f.add_tags()
    vals = {k: (v if isinstance(v, list) else [v]) for k, v in values.items() if v not in (None, "", [])}
    if isinstance(f.tags, ID3):
        for k, v in vals.items():
            if k == "GENRE":
                f.tags.setall("TCON", [TCON(encoding=3, text=v)])
            elif k == "LYRICS":
                f.tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=v[0])])
            else:
                f.tags.delall(f"TXXX:{k}")
                f.tags.add(TXXX(encoding=3, desc=k, text=[str(x) for x in v]))
    elif isinstance(f, MP4):
        for k, v in vals.items():
            if k == "GENRE":
                f.tags["\xa9gen"] = v
            elif k == "LYRICS":
                f.tags["\xa9lyr"] = v
            else:
                f.tags[f"----:com.apple.iTunes:{k}"] = [MP4FreeForm(str(x).encode()) for x in v]
    else:
        for k, v in vals.items():
            f.tags[k] = [str(x) for x in v]
    f.save()


# --------------------------------------------------------------------------
# ReplayGain (EBU R128 measured by ffmpeg)
# --------------------------------------------------------------------------
def loudness(path):
    """(integrated loudness LUFS, true peak dBFS) of a file."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    text = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", text)
    p = re.search(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS", text)
    if not i:
        return None
    peak = float(p.group(1)) if p and p.group(1) != "-inf" else -120.0
    return float(i.group(1)), peak


def replaygain(files):
    """{file: tags} with track and album gain (reference -18 LUFS) and peaks."""
    measured = {}
    for p in files:
        m = loudness(p)
        if m:
            measured[p] = (m[0], m[1], max(mutagen.File(p).info.length or 1.0, 1.0))
    if not measured:
        return {}
    total = sum(d for _i, _p, d in measured.values())
    album_i = 10 * __import__("math").log10(sum(d * 10 ** (i / 10) for i, _p, d in measured.values()) / total)
    album_peak = max(p for _i, p, _d in measured.values())
    out = {}
    for p, (i, peak, _d) in measured.items():
        tags = {"REPLAYGAIN_TRACK_GAIN": f"{-18 - i:.2f} dB", "REPLAYGAIN_TRACK_PEAK": f"{10 ** (peak / 20):.6f}",
                "REPLAYGAIN_ALBUM_GAIN": f"{-18 - album_i:.2f} dB",
                "REPLAYGAIN_ALBUM_PEAK": f"{10 ** (album_peak / 20):.6f}"}
        if p.suffix.lower() == ".opus":                     # Opus players read R128 gains (ref. -23 LUFS, Q7.8)
            tags["R128_TRACK_GAIN"] = str(round((-23 - i) * 256))
            tags["R128_ALBUM_GAIN"] = str(round((-23 - album_i) * 256))
        out[p] = tags
    return out


# --------------------------------------------------------------------------
# Genres
# --------------------------------------------------------------------------
def genre_map():
    """~/.config/tagmender/genres.json: {"map": {"funeral doom metal": "Doom Metal"},
    "allowed": ["Doom Metal", ...], "max": 3}. Without it, the top genres as written."""
    try:
        return json.loads((CONFIG / "genres.json").read_text())
    except (OSError, ValueError):
        return {}


def map_genres(names, conf):
    mapping = {mc.fold(k): v for k, v in (conf.get("map") or {}).items()}
    allowed = {mc.fold(a): a for a in conf.get("allowed") or []}
    out = []
    for n in names:
        g = mapping.get(mc.fold(n), n)
        if allowed and mc.fold(g) not in allowed:
            continue
        g = allowed.get(mc.fold(g), g if any(c.isupper() for c in g) else g.title())
        if g not in out:
            out.append(g)
    return out[:int(conf.get("max", 3))]


def genres_for(http, files):
    """Genres of the album: MusicBrainz release group (by votes), else Discogs styles."""
    f0 = files[0]
    rg = get(f0, "MUSICBRAINZ_RELEASEGROUPID")
    names = []
    if rg:
        d = http.get_json(f"https://musicbrainz.org/ws/2/release-group/{rg}?inc=genres&fmt=json", "mb") or {}
        names = [g["name"] for g in sorted(d.get("genres") or [], key=lambda g: -g.get("count", 0))]
    if not names:
        did = get(f0, "DISCOGS_RELEASE_ID")
        if did:
            d = http.get_json(f"https://api.discogs.com/releases/{did}", "discogs") or {}
            names = (d.get("styles") or []) + (d.get("genres") or [])
    return map_genres(names, genre_map())


# --------------------------------------------------------------------------
# Lyrics (LRCLIB)
# --------------------------------------------------------------------------
# Genres whose albums have no words to look up (add your own in genres.json: "instrumental")
INSTRUMENTAL_GENRES = ("ambient", "drone", "dark ambient", "new age", "instrumental", "soundtrack", "score",
                       "classical", "neoclassical", "minimal", "field recording", "electroacoustic",
                       "space music", "berlin school", "meditation", "post-rock")


def _rules():
    try:
        return json.loads((CONFIG / "rules.json").read_text())
    except (OSError, ValueError):
        return {}


def remember_instrumental(artist):
    """An artist whose album had no lyrics anywhere: skipped from now on
    (stored in rules.json as "no_lyrics_artists"; delete an entry to look again)."""
    if not artist:
        return
    rules = _rules()
    names = rules.setdefault("no_lyrics_artists", [])
    if mc.fold(artist) not in {mc.fold(n) for n in names}:
        names.append(artist)
        try:
            CONFIG.mkdir(parents=True, exist_ok=True)
            (CONFIG / "rules.json").write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n")
        except OSError:
            pass


def instrumental(folder, files):
    """No lyrics to look for: an instrumental genre in the tags or the library's
    name ("ambient_music"), a 'no vox' edition, or an artist known to have none."""
    genre = mc.fold(get(files[0], "GENRE"))
    extra = [mc.fold(g) for g in (genre_map().get("instrumental") or [])]
    if any(g in genre for g in [mc.fold(x) for x in INSTRUMENTAL_GENRES] + extra if g):
        return True
    path = mc.fold(str(folder))
    if "ambient" in path or re.search(r"no ?vox|instrumental", path):
        return True
    artist = mc.fold(get(files[0], "ALBUMARTIST") or get(files[0], "ARTIST") or folder.parent.name)
    return artist in {mc.fold(n) for n in _rules().get("no_lyrics_artists") or []}


def lyrics_for(http, path):
    """(synced or plain lyrics, synced?) for one track, or ("", False) - also when
    LRCLIB is down or busy (the next run tries again)."""
    try:
        return _lyrics_for(http, path)
    except Exception as exc:
        print(f"    lyrics lookup failed for {Path(path).name}: {exc}", file=sys.stderr)
        return "", False


def _lyrics_for(http, path):
    artist, title, album = get(path, "ARTIST"), get(path, "TITLE"), get(path, "ALBUM")
    if not artist or not title:
        return "", False
    dur = round(mutagen.File(path).info.length or 0)
    d = http.get_json(f"https://lrclib.net/api/get?artist_name={q(artist)}&track_name={q(title)}"
                      f"&album_name={q(album)}&duration={dur}", "lrclib") or {}
    if d.get("instrumental"):
        return "", False
    if d.get("syncedLyrics"):
        return d["syncedLyrics"], True
    return d.get("plainLyrics") or "", False


# --------------------------------------------------------------------------
# Quality: transcodes and upsampling
# --------------------------------------------------------------------------
def spectrum_cliff(path, seconds=40):
    """(sample rate, frequency where the spectrum falls off a cliff) or None.

    Lossy encoders cut everything above a fixed frequency (a "brick wall", e.g.
    16, 19 or 20 kHz); real recordings fade gently and only stop at the
    converter's own limit near half the sample rate. Needs numpy."""
    try:
        import numpy as np
    except ImportError:
        return None
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=sample_rate", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    m = re.search(r"\d+", probe)                       # "44100" or "44100," depending on the file
    rate = int(m.group(0)) if m else 0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "20", "-t", str(seconds), "-i", str(path), "-ac", "1",
                          "-f", "f32le", "-"], capture_output=True).stdout
    if not raw:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1",
                              "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = 8192
    frames = len(x) // n
    if not rate or frames < 4:
        return None
    win = np.hanning(n)
    power = np.zeros(n // 2 + 1)
    for i in range(frames):
        power += np.abs(np.fft.rfft(x[i * n:(i + 1) * n] * win)) ** 2
    db = 10 * np.log10(power / frames + 1e-20)
    freqs = np.fft.rfftfreq(n, 1 / rate)
    k = max(1, int(100 / (rate / n)))
    db = np.convolve(db, np.ones(k) / k, mode="same")
    ref = float(np.median(db[(freqs > 2000) & (freqs < 8000)]))
    alive = np.where(db > ref - 40)[0]
    if not len(alive):
        return None
    if rate > 48000:
        # hi-res: a real recording keeps (quiet) content past 24 kHz; CD audio
        # resampled up has nothing there at all
        mid = db[(freqs >= 16000) & (freqs < 20000)]
        high = db[(freqs >= 30000) & (freqs < 40000)]
        if len(mid) and len(high) and float(np.mean(mid)) - float(np.mean(high)) > 70:
            return rate, 22050.0
    top = float(freqs[alive[-1]])
    above = db[(freqs > top + 300) & (freqs < top + 1300)]
    if top < rate / 2 - 1500 and len(above) and float(np.mean(above)) < ref - 70:
        return rate, top
    return rate, None


def quality(files):
    """A finding for the album, or "": checked on up to three lossless tracks."""
    lossless = [p for p in files if p.suffix.lower() in (".flac", ".wav", ".aiff", ".wv", ".ape")]
    if not lossless:
        return ""
    picks = lossless[:: max(1, len(lossless) // 3)][:3]
    cliffs = [spectrum_cliff(p) for p in picks]
    if not cliffs or any(c is None for c in cliffs):
        return ""
    rate = cliffs[0][0]
    tops = [c[1] for c in cliffs]
    if all(tops) and max(tops) < 20300:
        return f"transcoded: everything above {min(tops) / 1000:.1f} kHz is cut, as lossy encoders (MP3/AAC) do"
    if rate > 48000 and all(tops) and max(tops) <= 24000:
        return f"upsampled: nothing above {max(tops) / 1000:.1f} kHz, so CD-rate audio in a {rate // 1000} kHz file"
    return ""


# --------------------------------------------------------------------------
# Navidrome
# --------------------------------------------------------------------------
def navidrome_rescan():
    """Ask Navidrome to rescan (Subsonic API startScan). True when asked."""
    try:
        conf = json.loads((CONFIG / "navidrome.json").read_text())
    except (OSError, ValueError):
        return False
    import urllib.request
    salt = secrets.token_hex(6)
    token = hashlib.md5((conf["password"] + salt).encode()).hexdigest()
    url = (f"{conf['url'].rstrip('/')}/rest/startScan?u={q(conf['user'])}&t={token}&s={salt}"
           f"&v=1.16.1&c=tagmender&f=json")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode()).get("subsonic-response", {}).get("status") == "ok"
    except Exception:
        return False


# --------------------------------------------------------------------------
def album_dirs(roots):
    for root in roots:
        for dirpath, dirs, names in os.walk(Path(root).expanduser()):
            dirs[:] = sorted(d for d in dirs if d.lower() != "covers" and not d.startswith("."))
            files = sorted(Path(dirpath) / n for n in names if Path(n).suffix.lower() in ta.AUDIO_EXT)
            if files:
                yield Path(dirpath), files


def enrich_album(folder, files, args, http, run_dir, blob_dir, journal, report):
    dec = exclusions.gate(files, "enrich", process_anyway=args.process_anyway)
    if dec.all_excluded:                                  # no ReplayGain, genres, lyrics or quality tags
        report.append({"album": str(folder), "changes": dec.summary().reason(), "quality": ""})
        print(f"  × {folder.name}: {dec.summary().reason()}")
        return
    files = dec.kept
    planned = {p: {} for p in files}
    created = []
    if args.replaygain and (args.overwrite or not get(files[0], "REPLAYGAIN_ALBUM_GAIN")):
        if args.apply:
            for p, tags in replaygain(files).items():
                planned[p].update(tags)
        else:                                       # measuring takes a while: only when writing
            for p in files:
                planned[p]["REPLAYGAIN_*"] = "measured with --apply"
    if args.genres and (args.overwrite or not get(files[0], "GENRE")):
        g = genres_for(http, files)
        if g:
            for p in files:
                planned[p]["GENRE"] = g
    if args.lyrics and not instrumental(folder, files):
        found_any = False
        for i, p in enumerate(files):
            if args.overwrite or not get(p, "LYRICS"):
                text, synced = lyrics_for(http, p)
                if text:
                    found_any = True
                    planned[p]["LYRICS"] = text
                    if synced and args.lrc and not p.with_suffix(".lrc").exists():
                        created.append((p.with_suffix(".lrc"), text))
                elif i == 2 and not found_any:
                    break                                 # three tracks without lyrics: an instrumental album
        if not found_any:
            remember_instrumental(get(files[0], "ALBUMARTIST") or get(files[0], "ARTIST") or folder.parent.name)
    finding = quality(files) if args.quality else ""
    if finding:
        for p in files:
            planned[p]["TAGMENDER_QUALITY"] = finding
    changes = {p: v for p, v in planned.items() if v}
    summary = sorted({k for v in changes.values() for k in v})
    report.append({"album": str(folder), "changes": ", ".join(summary), "quality": finding})
    print(f"  {'+' if args.apply else '?'} {folder.name}: {', '.join(summary) or 'nothing to add'}"
          + (f"  [{finding}]" if finding else ""))
    if args.apply and changes:
        recs = [{"path": str(p), "new_path": str(p), "key": "", "backup": ta.backup_tags(p, blob_dir)}
                for p in changes]
        journal.append({"album": str(folder), "mbid": None, "files": recs, "extras": [], "moves": [],
                        "created": [str(c) for c, _t in created]})
        with open(run_dir / "journal.json", "w") as fh:
            json.dump(journal, fh)
        for p, tags in changes.items():
            put(p, tags)
        for c, text in created:
            c.write_text(text, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*")
    for opt in ("replaygain", "genres", "lyrics", "quality", "navidrome"):
        ap.add_argument(f"--{opt}", action="store_true")
    ap.add_argument("--lrc", action="store_true", help="also save time-synced lyrics as .lrc files")
    ap.add_argument("--overwrite", action="store_true", help="replace values the files already have")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--process-anyway", action="store_true",
                    help="also albums an Ignore/Skip rule excludes (this run only)")
    ap.add_argument("--undo", help="restore a run")
    ap.add_argument("--only", help="only folders whose path contains this text")
    ap.add_argument("--out-dir", default="~/Downloads/tagging")
    ap.add_argument("--cache-db", default="~/Downloads/matching/match_state.db")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.undo:
        journal = json.loads((out_dir / f"run-{args.undo}" / "journal.json").read_text())
        for e in journal:
            for created in e.get("created", []):
                Path(created).unlink(missing_ok=True)
        return ta.undo(out_dir, args.undo)
    if args.navidrome and not args.roots:
        print("Navidrome rescan " + ("requested" if navidrome_rescan() else "failed (see navidrome.json)"))
        return 0

    db = sqlite3.connect(Path(args.cache_db).expanduser(), timeout=60)
    http = ma.Http(db, dict(mc.DEFAULT_CFG), ma._discogs_token())
    run_dir = blob_dir = None
    if args.apply:
        run_dir = out_dir / f"run-enrich-{time.strftime('%Y%m%d-%H%M%S')}"
        blob_dir = run_dir / "blobs"
        blob_dir.mkdir(parents=True)
    journal, report = [], []
    for folder, files in album_dirs(args.roots):
        if args.only and args.only.casefold() not in str(folder).casefold():
            continue
        try:
            enrich_album(folder, files, args, http, run_dir, blob_dir, journal, report)
        except Exception as exc:                          # one album's problem never stops the run
            print(f"  ! {folder.name}: skipped ({type(exc).__name__}: {exc})")
            report.append({"album": str(folder), "changes": f"skipped: {exc}", "quality": ""})
    path = (run_dir or out_dir) / "enrich_report.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["album", "changes", "quality"])
        w.writeheader()
        w.writerows(report)
    print(f"\n{len(report)} album(s). Report: {path}")
    if args.apply:
        print(f"Undo: python3 enrich.py --undo {run_dir.name[len('run-'):]}")
        if args.navidrome:
            print("Navidrome rescan " + ("requested" if navidrome_rescan() else "failed (see navidrome.json)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
