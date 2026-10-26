# Shared setup for the review-28 B tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the review-28 src snapshot) on sys.path, no Picard.ini of this machine;
# small MusicBrainz release JSONs; helpers to make blank audio files (ffmpeg), pre-tag them,
# and plan + run one album with tagger scripts and Picard settings. No network, no Picard.
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm28B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm28B-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm28B-ini-"), "no-Picard.ini")
_SNAP = str(Path(__file__).resolve().parent.parent)
REPO = Path(_SNAP)
sys.path.insert(0, str(REPO))

import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
          ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"], ".wma": ["-c:a", "wmav2"],
          ".opus": ["-c:a", "libopus"], ".wav": [], ".aiff": []}
_BLANK = {}
VA = {"id": "89ad4ac3-39f7-470e-963a-56509c546377", "name": "Various Artists", "sort-name": "Various Artists",
      "disambiguation": ""}


# ---- release JSON (as the MusicBrainz web service returns it) --------------------------
def uid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "r28b" + seed))


def artist(name, sort=None, aliases=None, genres=None, tags=None):
    a = {"id": uid("artist" + name), "name": name, "sort-name": sort or name, "disambiguation": ""}
    if aliases is not None:
        a["aliases"] = aliases
    if genres is not None:
        a["genres"] = genres
    if tags is not None:
        a["tags"] = tags
    return a


def alias(name, sort, locale, primary=True):
    return {"name": name, "sort-name": sort, "locale": locale, "primary": primary,
            "type-id": "894afba6-2816-3c24-8072-eadb66bd04bc", "type": "Artist name", "ended": False,
            "begin": None, "end": None}


def credit(*parts):
    """credit(("Name", artist_node, " feat. "), ...)"""
    return [{"name": n, "artist": a, "joinphrase": j} for n, a, j in parts]


def url_rel(typ, url):
    return {"type": typ, "type-id": uid("urltype" + typ), "direction": "forward", "target-type": "url",
            "attributes": [], "attribute-values": {}, "begin": None, "end": None, "ended": False,
            "url": {"id": uid(url), "resource": url}, "target-credit": "", "source-credit": ""}


def recording(title, ac, relations=None, isrcs=None, genres=None, tags=None):
    r = {"id": uid("rec" + title), "title": title, "length": 200000, "artist-credit": ac, "video": False,
         "disambiguation": "", "relations": relations or []}
    if isrcs is not None:
        r["isrcs"] = isrcs
    if genres is not None:
        r["genres"] = genres
    if tags is not None:
        r["tags"] = tags
    return r


def track(pos, title, ac, rec):
    return {"id": uid("track%d%s" % (pos, title)), "position": pos, "number": str(pos), "title": title,
            "length": 200000, "artist-credit": ac, "recording": rec}


def release(seed, title, ac, tracks, secondary=(), relations=None, date="2005-01-01"):
    rg = {"id": uid("rg" + seed), "title": title, "primary-type": "Album", "secondary-types": list(secondary),
          "first-release-date": date, "disambiguation": "", "artist-credit": ac, "relations": [],
          "genres": [], "tags": []}
    return {"id": uid("rel" + seed), "title": title, "artist-credit": ac, "status": "Official", "date": date,
            "country": "GB", "barcode": "", "asin": None, "disambiguation": "", "annotation": "",
            "text-representation": {"language": "eng", "script": "Latn"}, "aliases": [],
            "release-events": [{"date": date, "area": {"iso-3166-1-codes": ["GB"], "name": "UK", "id": uid("gb")}}],
            "label-info": [], "relations": relations or [], "genres": [], "tags": [], "release-group": rg,
            "media": [{"position": 1, "title": "", "format": "CD", "track-count": len(tracks), "track-offset": 0,
                       "tracks": tracks}]}


def write_ini(text):
    """A Picard.ini with this text; its path."""
    p = Path(tempfile.mkdtemp(prefix="tm28B-picardini-")) / "Picard.ini"
    p.write_text(text, encoding="utf-8")
    return p


# ---- files ------------------------------------------------------------------------------
def make(path):
    """A one-second sine file with no tags."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix
    if ext not in _BLANK:
        b = Path(tempfile.mkdtemp(prefix="tm28B-blank-")) / ("blank" + ext)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", *CODECS[ext],
                        "-map_metadata", "-1", "-y", str(b)], check=True)
        _BLANK[ext] = b
    shutil.copy(_BLANK[ext], path)
    return path


def album(ext, n=2):
    d = Path(tempfile.mkdtemp(prefix="tm28B-lib-"))
    return [make(d / "lib" / "album" / ("track%02d%s" % (i + 1, ext))) for i in range(n)]


def pretag_numbers(path, track="5", tracks="9", disc="2", discs="3"):
    """Give a file the numbers an earlier tagging left: "5/9", disc "2/3"."""
    import mutagen
    f = mutagen.File(str(path))
    if f.tags is None:
        f.add_tags()
    ext = Path(path).suffix
    if ext == ".mp3":
        from mutagen import id3
        f.tags.add(id3.TRCK(encoding=3, text=[f"{track}/{tracks}"]))
        f.tags.add(id3.TPOS(encoding=3, text=[f"{disc}/{discs}"]))
    elif ext in (".flac", ".ogg"):
        f.tags["TRACKNUMBER"], f.tags["TRACKTOTAL"] = [track], [tracks]
        f.tags["DISCNUMBER"], f.tags["DISCTOTAL"] = [disc], [discs]
    elif ext == ".m4a":
        f.tags["trkn"], f.tags["disk"] = [(int(track), int(tracks))], [(int(disc), int(discs))]
    else:
        raise ValueError(ext)
    f.save()


# ---- plan + run -------------------------------------------------------------------------
TARGET = {"title": ["Speak to Me"], "album": ["The Dark Side of the Moon"], "artist": ["Pink Floyd"],
          "albumartist": ["Pink Floyd"], "date": ["1973-03-24"], "tracknumber": ["1"], "totaltracks": ["2"],
          "discnumber": ["1"], "totaldiscs": ["1"],
          "musicbrainz_albumid": ["b84ee12a-09ef-421b-82de-0441a926375b"]}


def fresh_state():
    tmp = Path(tempfile.mkdtemp(prefix="tm28B-"))
    ex.CONFIG_DIR, ex.STATE_DIR = tmp / "config", tmp / "state"
    ex._write(ex.rules_file(), {"rules": []})
    logsetup.reset()
    return tmp


def profile(lib, scripts=(), picard=None, **kw):
    st = {"enable_tagger_scripts": bool(scripts), "list_of_scripts": list(scripts), "write_id3v1": False}
    st.update(picard or {})
    d = {"name": "t", "sources": [{"path": str(lib)}], "thresholds": {"organize_min_confidence": 95},
         "artwork": {"enabled": False}, "mark_resolved": False, "picard": st}
    d.update(kw)
    return pf.from_dict(d)


def proposal(files, targets=None):
    if targets is None:
        targets = []
        for i, _f in enumerate(files):
            t = {k: list(v) for k, v in TARGET.items()}
            t.update(tracknumber=[str(i + 1)], totaltracks=[str(len(files))], title=["Track %d" % (i + 1)])
            targets.append(t)
    return pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=targets,
                       provider="musicbrainz", release_id=TARGET["musicbrainz_albumid"][0], confidence=100.0,
                       margin=50.0, evidence=["x"],
                       release={"album": TARGET["album"], "albumartist": TARGET["albumartist"]})


def targets_of(rel, settings):
    """Proposal targets from a release JSON, as the matcher hands them over (mb_tags)."""
    import mb_tags
    out = []
    for t in mb_tags.release_tags(rel, settings):
        merged = {k: v for k, v in t.items() if not k.startswith("~") and v}
        hidden = {k: v for k, v in t.items() if k.startswith("~")}
        remove = list(hidden.pop("~remove", []))
        if remove:
            merged["~remove"] = remove
        for k, v in hidden.items():
            if k not in merged and v not in (None, "", []):
                merged[k] = v
        out.append({k: (v if isinstance(v, list) else [v]) for k, v in merged.items()})
    return out


def move_run(files, naming, scripts=(), picard=None, targets=None):
    """Plan + run one album moved into a fresh destination named by `naming`.
    (plan, result, destination root)."""
    fresh_state()
    lib = Path(files[0]).parent.parent
    dest = Path(tempfile.mkdtemp(prefix="tm28B-dest-"))
    prof = profile(lib, scripts, picard, operation="move", destination=str(dest), naming={"template": naming})
    p = pl.build([proposal(files, targets)], prof)
    res = executor.Executor(p).run()
    return p, res, dest


def run(files, scripts=(), picard=None, targets=None):
    """Plan + run one album in place. (plan, result)."""
    fresh_state()
    lib = Path(files[0]).parent.parent
    prof = profile(lib, scripts, picard)
    p = pl.build([proposal(files, targets)], prof)
    res = executor.Executor(p).run()
    return p, res


def names(dest):
    return sorted(str(x.relative_to(dest)) for x in Path(dest).rglob("*") if x.is_file())


def detail(p, res):
    return f"plan: {[(a.get('status'), a.get('conflicts')) for a in p['albums']]}; result: {res.get('counts')} " \
           f"{[a.get('detail') for a in res.get('albums') or []]}"
