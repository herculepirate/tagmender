"""gui/versions.py - What the search and "Other versions" sheets show, without Qt: searches of
MusicBrainz and Discogs, every release of an album's MusicBrainz release group and Discogs master,
how each agrees with the files (match_core.score_candidate, read only), and which details tell
tied pressings apart.

Nothing here writes: lookups go through match_albums.Http (its cache and rate limits), the files
are only read (match_albums.read_album), and a release chosen from these lists is used through
the Overview's forced-release flow (gui.search.force_release), which plans the album again.

A row is a dict: source ("mb" | "discogs"), id, url, ref (what engine.parse_release_ref takes),
title, artist, date, country, label, catno, barcode, format, tracks (int or None); a scored row
adds score (0-100), fit (words), notes and n_tracks. Run every function here on a worker
(gui.work.start): each one may wait on the network."""
from __future__ import annotations

import os
import sqlite3
from collections import Counter
from pathlib import Path

import match_albums as ma
import match_core as mc

from .i18n import N_, tr

COLS = (("source", N_("Source")), ("title", N_("Title")), ("artist", N_("Artist")), ("date", N_("Date")),
        ("country", N_("Country")),
        ("label", N_("Label")), ("catno", N_("Catalog #")), ("barcode", N_("Barcode")), ("format", N_("Format")),
        ("tracks", N_("Tracks")))
FIT_COLS = (("score", N_("Fit")), ("fit", N_("Agrees with your files")))
SOURCES = {"mb": "MusicBrainz", "discogs": "Discogs"}
SEARCH_LIMIT = 25
MB_PAGE = 100                        # releases asked for per MusicBrainz browse page
DISCOGS_DETAILS = 25                 # Discogs versions read in full (track list, barcode) and scored
# the details that tell pressings apart, in the order a person can check them on the case
DIFF_ORDER = ("barcode", "catno", "label", "country", "format", "date")
CHECK = {"barcode": N_("Check the barcode on your case"),
         "catno": N_("Check the catalog number on the spine or the disc"),
         "label": N_("Check the label printed on the back"),
         "country": N_("Check the country printed on the back or the disc"),
         "format": N_("Check what your copy is"),
         "date": N_("Check the year printed on the back")}


class Lookup:
    """The network side of a search: the matcher's Http (cache, rate limits), its settings and
    the Discogs Enrichment code (None without it). use_mb / use_discogs follow the profile;
    notes says why a provider is off."""

    def __init__(self, http, cfg=None, dm=None, use_mb=True, use_discogs=True, notes=None):
        self.http, self.dm = http, dm
        self.cfg = cfg or dict(mc.DEFAULT_CFG)
        self.use_mb, self.use_discogs = use_mb, use_discogs and dm is not None
        self.notes = dict(notes or {})
        if use_discogs and dm is None:
            self.notes.setdefault("discogs", tr("the Discogs plugin code was not found"))

    def close(self):
        """Close the cache connection make_lookup opened (on the thread that made it)."""
        db = getattr(self.http, "db", None)
        if db is not None:
            try:
                db.close()
            except Exception:                          # (already closed: nothing to do)
                pass


# Every lookup of the app shares one pace per host: a new search or versions sheet makes a new
# Http, whose own last-call times would start empty, and searches started in quick succession
# (switching tabs, the versions sheet open during a Look Up) would ask MusicBrainz at the same
# moment, past its one request a second. The pace is the process's own (match_albums.share_pace),
# which every Engine's Http shares too: one pace per host however many look-ups run.
_LAST: dict = ma._SHARED_LAST        # host -> when it was last asked, as Http.last


def paced(http):
    """`http` (a match_albums.Http) sharing the process's last-call times and per-host locks
    (match_albums.share_pace): its live requests to a host are taken one at a time, spaced by
    the host's delay, across every lookup and Engine of the app."""
    return ma.share_pace(http)


def make_lookup(profile_name="default") -> Lookup:
    """A Lookup with the profile's providers and thresholds, sharing the app's HTTP cache.
    Made on the worker that uses it (its SQLite connection belongs to that thread). Tests
    patch gui.versions.make_lookup."""
    import credentials
    import companions
    import exclusions as ex
    from .common import load_profile
    prof = load_profile(profile_name or "default")
    cfg = prof.matcher_cfg(dict(mc.DEFAULT_CFG))
    try:
        import json
        cfg["artist_aliases"] = json.loads((ex.CONFIG_DIR / "rules.json").read_text()).get("artist_aliases") or {}
    except (OSError, ValueError):
        pass
    notes, token, dm = {}, "", None
    use_mb, use_discogs = prof.provider_on("musicbrainz"), prof.provider_on("discogs")
    if use_discogs and not companions.discogs_on():
        notes["discogs"] = tr("the Discogs plugin is switched off (Preferences › Plugins)")
        use_discogs = False
    if use_discogs:
        try:
            token = credentials.get("discogs")
        except credentials.CredentialError as exc:
            notes["discogs"] = str(exc)
        if not token:
            import requirements
            notes.setdefault("discogs", requirements.notice("discogs", "app", requirements.NOT_SET))
            use_discogs = False
    if use_discogs:
        try:
            dm = ma.load_plugin(str(companions.discogs_dir()))
        except SystemExit:
            use_discogs = False
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ex.STATE_DIR / "cache.sqlite", timeout=60)
    return Lookup(paced(ma.Http(db, cfg, token)), cfg, dm, use_mb, use_discogs, notes)


def discogs_off(lookup) -> str:
    """Why Discogs is not asked: a Setup notice ("Discogs is not used: no Discogs token. Add one in
    Setup ...") reads on its own; any other reason follows "Discogs is off: "."""
    why = lookup.notes.get("discogs", tr("the profile does not use it"))
    return why if why.startswith("Discogs is not used") else tr("Discogs is off: ") + why


# ---- rows ---------------------------------------------------------------------
def _count_formats(names) -> str:
    """["CD", "CD", "DVD-Video"] -> "2×CD + DVD-Video"."""
    c = Counter(n for n in names if n)
    return " + ".join(f"{k}" if v == 1 else f"{v}×{k}" for k, v in c.items())


def _join(values) -> str:
    return "; ".join(dict.fromkeys(str(v).strip() for v in values if str(v or "").strip()))


def mb_release_row(r: dict) -> dict:
    """A MusicBrainz release (a search hit, a release group's member or a full release)."""
    media = r.get("media") or []
    infos = r.get("label-info") or []
    tracks = r.get("track-count")
    if tracks is None and media:
        tracks = sum(int(m.get("track-count") or len(m.get("tracks") or [])) for m in media)
    return {"source": "mb", "id": r.get("id") or "", "ref": r.get("id") or "",
            "url": f"https://musicbrainz.org/release/{r.get('id')}",
            "title": r.get("title") or "", "artist": ma.mb_credit(r) if r.get("artist-credit") else "",
            "date": str(r.get("date") or ""), "country": r.get("country") or "",
            "label": _join((i.get("label") or {}).get("name") for i in infos),
            "catno": _join(i.get("catalog-number") for i in infos), "barcode": str(r.get("barcode") or ""),
            "format": _count_formats(m.get("format") or tr("Unknown") for m in media), "tracks": tracks,
            "group": (r.get("release-group") or {}).get("id") or ""}


def discogs_result_row(r: dict) -> dict:
    """A Discogs search result or master version (no track list: tracks unknown)."""
    rid = str(r.get("id") or "")
    title = str(r.get("title") or "")
    artist = ""
    if " - " in title and "artist" not in r:                     # (search results: "Artist - Title")
        artist, title = title.split(" - ", 1)
    label = r.get("label")
    fmt = r.get("format")
    return {"source": "discogs", "id": rid, "ref": f"discogs:{rid}", "url": f"https://www.discogs.com/release/{rid}",
            "title": title, "artist": artist or str(r.get("artist") or ""),
            "date": str(r.get("released") or r.get("year") or ""), "country": str(r.get("country") or ""),
            "label": _join(label) if isinstance(label, list) else str(label or ""),
            "catno": str(r.get("catno") or ""), "barcode": _join(r.get("barcode") or []),
            "format": _join(fmt) if isinstance(fmt, list) else str(fmt or ""), "tracks": None,
            "master": str(r.get("master_id") or "") if r.get("master_id") else ""}


def discogs_release_row(rel: dict, dm=None) -> dict:
    """A full Discogs release."""
    rid = str(rel.get("id") or "")
    clean = dm.clean_artist_name if dm is not None else (lambda s: str(s or ""))
    artist = (dm.format_artist_credit(rel.get("artists")) if dm is not None else "") or \
        " & ".join(clean(a.get("name")) for a in rel.get("artists") or [] if isinstance(a, dict))
    labels = rel.get("labels") or []
    fmts = []
    for f in rel.get("formats") or []:
        n = str(f.get("name") or "")
        q = str(f.get("qty") or "1")
        fmts.append(n if q in ("", "1") else f"{q}×{n}")
    tracks = None
    if dm is not None:
        tracks = len(ma.drop_video_tracks(ma.flatten_tracklist(rel.get("tracklist"), dm), rel))
    return {"source": "discogs", "id": rid, "ref": f"discogs:{rid}",
            "url": rel.get("uri") or f"https://www.discogs.com/release/{rid}",
            "title": rel.get("title") or "", "artist": artist,
            "date": str(rel.get("released") or rel.get("year") or ""),
            "country": rel.get("country") or "", "label": _join(clean(l.get("name")) for l in labels),
            "catno": _join(l.get("catno") for l in labels if str(l.get("catno") or "").casefold() != "none"),
            "barcode": _join(i.get("value") for i in rel.get("identifiers") or [] if i.get("type") == "Barcode"),
            "format": " + ".join(fmts), "tracks": tracks, "master": str(rel.get("master_id") or "")}


def recording_rows(data: dict) -> list:
    """MusicBrainz recording hits, one row per release they are on (the release is what is used)."""
    out = []
    for rec in (data or {}).get("recordings") or []:
        credit = ma.mb_credit(rec) if rec.get("artist-credit") else ""
        secs = (rec.get("length") or 0) / 1000.0
        length = f"{int(secs // 60)}:{int(secs % 60):02d}" if secs else ""
        for rel in rec.get("releases") or [{}]:
            row = mb_release_row(rel) if rel else {"source": "mb", "id": "", "ref": "", "url": "", "title": "",
                                                    "artist": "", "date": "", "country": "", "label": "",
                                                    "catno": "", "barcode": "", "format": "", "tracks": None}
            row.update(recording=rec.get("title") or "", length=length, artist=credit or row["artist"],
                       recording_id=rec.get("id") or "")
            out.append(row)
    return out


def artist_rows(data: dict) -> list:
    out = []
    for a in (data or {}).get("artists") or []:
        life = a.get("life-span") or {}
        years = "–".join(x[:4] for x in (life.get("begin") or "", life.get("end") or "") if x)
        out.append({"source": "mb", "id": a.get("id") or "", "ref": "",
                    "url": f"https://musicbrainz.org/artist/{a.get('id')}",
                    "name": a.get("name") or "", "sort": a.get("sort-name") or "", "type": a.get("type") or "",
                    "country": a.get("country") or "", "years": years,
                    "about": a.get("disambiguation") or ""})
    return out


# ---- searches -----------------------------------------------------------------
def search(lookup: Lookup, kind: str, text: str) -> dict:
    """kind: "release", "recording", "artist" (MusicBrainz) or "discogs". Returns {"rows",
    "note"}; a provider that is off or failed gives no rows and a note, never an exception."""
    text = (text or "").strip()
    if not text:
        return {"rows": [], "note": tr("Type what to search for.")}
    try:
        if kind == "discogs":
            if not lookup.use_discogs:
                return {"rows": [], "note": discogs_off(lookup)}
            data = lookup.http.get_json(f"https://api.discogs.com/database/search?type=release&per_page={SEARCH_LIMIT}"
                                        f"&q={ma.q(text)}", "discogs")
            return {"rows": [discogs_result_row(r) for r in (data or {}).get("results") or []], "note": ""}
        if not lookup.use_mb:
            return {"rows": [], "note": tr("MusicBrainz is off in this profile.")}
        data = lookup.http.get_json(f"https://musicbrainz.org/ws/2/{kind}/?query={ma.q(text)}&fmt=json"
                                    f"&limit={SEARCH_LIMIT}", "mb")
        if kind == "recording":
            return {"rows": recording_rows(data), "note": ""}
        if kind == "artist":
            return {"rows": artist_rows(data), "note": ""}
        return {"rows": [mb_release_row(r) for r in (data or {}).get("releases") or []], "note": ""}
    except ma.ProviderUnavailable as exc:
        return {"rows": [], "note": tr("{0} did not answer ({1}). "
                                       "Try again in a minute.").format(SOURCES.get(exc.host, exc.host),
                                                                     exc.kind.replace('_', ' '))}
    except OSError as exc:
        return {"rows": [], "note": tr("The search did not reach the server: {exc}").format(exc=exc)}


def query_for(album: dict) -> str:
    """What to search for an album: its identified album and artist, else its folder's names."""
    rel = album.get("release") or {}
    words = " ".join(filter(None, [(rel.get("albumartist") or [""])[0], (rel.get("album") or [""])[0]]))
    if words:
        return words
    folder = Path(album.get("source_dir") or "")
    return f"{folder.parent.name} {folder.name}".strip()


# ---- the album's versions -------------------------------------------------------
WRITTEN = ("added", "changed")        # (plan.diff_tags: the rows whose "after" is the release's value)


def _values(album: dict, field: str) -> list:
    """A field's values the album's plan writes. A row it does not write (locked, preserved,
    kept below the replace threshold) holds what the files already have, which may be an earlier
    wrong tagging's, and a removed row nothing: neither is the identified release's."""
    out = []
    for fe in album.get("files") or []:
        for d in fe.get("diff") or []:
            if str(d.get("field") or "").lower() == field and d.get("action") in WRITTEN:
                out += [str(v) for v in (d.get("after") or []) if str(v).strip()]
    return list(dict.fromkeys(out))


def held_ids(album: dict, files_album: dict | None = None) -> dict:
    """The IDs the album's files already hold, which a plan lists only when they change:
    {"group", "master", "mb_release", "discogs_release"} ("" when none). The release IDs
    come from match_albums.read_album, the release group and master from the first file's
    tags that has them (tagio, read only)."""
    import tagio
    fa = files_album or {}
    out = {"mb_release": next(iter(sorted(fa.get("tag_release_mb") or ())), ""),
           "discogs_release": next(iter(sorted(fa.get("tag_release_discogs") or ())), ""), "group": "", "master": ""}
    paths = [t.get("path") for t in fa.get("tracks") or [] if t.get("path")] or list(album.get("album_files") or [])
    for p in paths:
        try:
            tags = tagio.read(p)
        except Exception:                              # (an unreadable file holds no IDs)
            continue
        for key, field in (("group", "musicbrainz_releasegroupid"), ("master", "discogs_master_id")):
            if not out[key]:
                out[key] = next((str(v).strip() for v in tags.get(field) or [] if str(v).strip()), "")
        if out["group"] and out["master"]:
            break
    return out


def anchors(lookup: Lookup, album: dict, held: dict | None = None) -> dict:
    """The album's MusicBrainz release group and Discogs master. With a release identified:
    that release's (the tags the plan writes from it, else a lookup of it); the IDs already in
    the files (`held`, from held_ids) may be an earlier wrong tagging's and are not used. With
    none (an album known only by its files): the plan's tags, else those held. {"group",
    "master", "release"}."""
    ident = album.get("identification") or {}
    held = {} if ident.get("release_id") else (held or {})
    out = {"group": "", "master": "", "release": ""}
    rid, prov = str(ident.get("release_id") or ""), str(ident.get("provider") or "")
    group = _values(album, "musicbrainz_releasegroupid")
    master = _values(album, "discogs_master_id")
    out["group"] = group[0] if group else (held.get("group") or "")
    out["master"] = master[0] if master else (held.get("master") or "")
    mb_rel = rid if rid and prov.startswith("musicbrainz") else \
        next(iter(_values(album, "musicbrainz_albumid")), "") or held.get("mb_release") or ""
    dg_rel = rid if rid and prov.startswith("discogs") else \
        next(iter(_values(album, "discogs_release_id")), "") or held.get("discogs_release") or ""
    out["release"] = (f"discogs:{dg_rel}" if dg_rel else "") or mb_rel
    if not out["group"] and mb_rel and lookup.use_mb:
        det = _quiet(lookup.http, f"https://musicbrainz.org/ws/2/release/{mb_rel}"
                                  "?inc=recordings+artist-credits+labels+url-rels+release-groups&fmt=json", "mb")
        out["group"] = ((det or {}).get("release-group") or {}).get("id") or ""
        if not dg_rel and det:
            dg_rel = ma.mb_linked_discogs_id(det) or ""
    if not out["master"] and dg_rel and lookup.use_discogs:
        det = _quiet(lookup.http, f"https://api.discogs.com/releases/{dg_rel}", "discogs")
        out["master"] = str((det or {}).get("master_id") or "") if (det or {}).get("master_id") else ""
    return out


def _quiet(http, url, host):
    """get_json, None on any failure (an anchor is a convenience, not the search)."""
    try:
        return http.get_json(url, host)
    except Exception:
        return None


def read_files(album: dict) -> dict:
    """The album's own files read as the matcher reads them (other albums in its folder left out)."""
    folder = Path(album["source_dir"])
    mine = {os.path.abspath(p) for p in album.get("album_files") or []} | \
        {os.path.abspath(fe["src"]) for fe in album.get("files") or [] if fe.get("src")}
    others = set()
    if mine and folder.is_dir():
        for dp, _d, names in os.walk(folder):
            for n in names:
                p = os.path.abspath(os.path.join(dp, n))
                if Path(n).suffix.lower() in ma.AUDIO_EXT and p not in mine:
                    others.add(p)
    return ma.read_album(folder, folder.parent.name, folder.name, exclude=others)


def fit_words(files_album: dict, sc: dict, cfg: dict) -> str:
    """How a scored candidate agrees with the files, in words: tracks, lengths, codes."""
    cand = sc["cand"]
    n_file, n_cand = len(files_album.get("tracks") or []), len(cand.get("tracks") or [])
    parts = [tr("same track count") if sc.get("tracks_ok") else tr("{n_cand} tracks, you have {n_file}")
             .format(n_cand=n_cand, n_file=n_file)]
    if sc.get("tracks_ok"):
        known = [(t["secs"], c["secs"]) for t, c in zip(files_album["tracks"], cand["tracks"]) if c.get("secs")]
        if known:
            good = sum(1 for f, c in known if mc.duration_ok(f, c, cfg))
            parts.append(tr("every length agrees") if good == len(known) else
                         tr("lengths {good} of {0} agree").format(len(known), good=good))
        else:
            parts.append(tr("no lengths listed"))
    elif sc.get("aligned"):
        parts.append(tr("lengths line up in order"))
    notes = sc.get("notes") or []
    if sc.get("code"):
        parts.append(tr("{code} agrees").format(code=sc['code']))
    elif sc.get("conflict"):
        parts.append(tr("barcode differs"))
    elif "catalogue number differs" in notes:
        parts.append(tr("catalog number differs"))
    return "; ".join(parts)


def score_row(row: dict, cand: dict, files_album: dict, cfg: dict, keep=None) -> dict:
    """The row with how the candidate agrees with the files; the score is appended to `keep`.
    formats, year_ok and pressing_year are what tied() narrows by, as match_core.album_only."""
    sc = mc.score_candidate(files_album, cand, cfg)
    if keep is not None:
        keep.append(sc)
    row = dict(row, score=float(sc.get("confidence") or 0), fit=fit_words(files_album, sc, cfg),
               notes=list(sc.get("notes") or []), n_tracks=len(cand.get("tracks") or []),
               title_sim=sc.get("title_sim"), formats=sorted(cand.get("formats") or ()),
               year_ok=bool(sc.get("year_ok")), pressing_year=sc.get("pressing_year"))
    if row.get("tracks") is None:
        row["tracks"] = row["n_tracks"]
    return row


def versions(lookup: Lookup, album: dict, progress=None, cancel=None, files_album=None) -> dict:
    """Every release of the album's MusicBrainz release group and Discogs master, scored
    against its files. {"rows", "notes", "anchors", "n_files"}; rows best fit first."""
    progress = progress or (lambda *a: None)
    cancel = cancel or (lambda: False)
    notes = []
    progress(0, 3, tr("Reading your files"))
    files_album = files_album if files_album is not None else read_files(album)
    found = anchors(lookup, album, held_ids(album, files_album))
    rows, scored = [], []
    if found["group"] and lookup.use_mb:
        progress(1, 3, tr("Reading the MusicBrainz release group"))
        listed, count = [], None
        try:
            # (a browse with recordings is cut to a track budget: fewer than `limit` may come back)
            while not cancel():
                data = lookup.http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={found['group']}"
                                            f"&inc=recordings+artist-credits+labels+media&limit={MB_PAGE}"
                                            f"&offset={len(listed)}&fmt=json", "mb") or {}
                page = data.get("releases") or []
                listed += page
                count = data.get("release-count") if isinstance(data.get("release-count"), int) else len(listed)
                if not page or len(listed) >= count:
                    break
                progress(1, 3, tr("Reading the MusicBrainz release group ({0} of {count})").format(len(listed),
                                                                                                   count=count))
        except ma.ProviderUnavailable as exc:
            notes.append(tr("MusicBrainz did not answer ({0}).").format(exc.kind.replace('_', ' ')))
        for r in listed:
            if cancel():
                break
            det = dict(r, **{"release-group": {"id": found["group"]}})
            rows.append(score_row(mb_release_row(det), ma.mb_candidate(det), files_album, lookup.cfg, scored))
        if isinstance(count, int) and count > len(listed):
            notes.append(tr("MusicBrainz lists {count} releases of this album; {0} are shown.").format(len(listed),
                                                                                                       count=count))
    elif lookup.use_mb:
        notes.append(tr("No MusicBrainz release group is known for this album."))
    if found["master"] and lookup.use_discogs:
        try:
            data = lookup.http.get_json(f"https://api.discogs.com/masters/{found['master']}/versions?per_page=100",
                                        "discogs")
            listed = (data or {}).get("versions") or []
            if int(((data or {}).get("pagination") or {}).get("pages") or 1) > 1:
                notes.append(tr("Discogs lists more versions than the first 100 shown."))
            for i, v in enumerate(listed):
                if cancel():
                    break
                row = discogs_result_row(dict(v, artist=""))
                if i < DISCOGS_DETAILS:
                    progress(2, 3, tr("Checking Discogs version {0} of {1}").format(i + 1, min(len(listed),
                                                                                               DISCOGS_DETAILS)))
                    det = _quiet(lookup.http, f"https://api.discogs.com/releases/{row['id']}", "discogs")
                    if det:
                        row = discogs_release_row(det, lookup.dm)
                        try:
                            row = score_row(row, ma.discogs_candidate(det, lookup.dm), files_album, lookup.cfg,
                                            scored)
                        except Exception:
                            pass                   # (an odd release is listed without a fit)
                rows.append(row)
            if len(listed) > DISCOGS_DETAILS:
                notes.append(tr("The first {DISCOGS_DETAILS} Discogs versions are compared with your files; "
                                "the others are listed without a fit.").format(DISCOGS_DETAILS=DISCOGS_DETAILS))
        except ma.ProviderUnavailable as exc:
            notes.append(tr("Discogs did not answer ({0}).").format(exc.kind.replace('_', ' ')))
    elif lookup.use_discogs:
        notes.append(tr("No Discogs master is known for this album."))
    elif "discogs" in lookup.notes:
        notes.append(discogs_off(lookup))
    progress(3, 3, "")
    rows.sort(key=lambda r: -(r.get("score") if r.get("score") is not None else -1))
    # (what the files were made from narrows the tied pressings, as the matcher's album_only)
    media = ma.tie_source(files_album, {"scored": scored})
    return {"rows": rows, "notes": notes, "anchors": found, "n_files": len(files_album.get("tracks") or []),
            "media": media}


# ---- tied pressings ------------------------------------------------------------
def pressing_tied(album: dict) -> bool:
    """The album is known but not its pressing (Album only), or only established (Partial)."""
    return (album.get("identification") or {}).get("tier") in ("album_only", "partial")


def tied(rows: list, cfg: dict, media: str = "") -> list:
    """The pressings that fit the files as well as the best one, as match_core.album_only
    ties them: from the best one's source only, within the profile's margin, the same title
    and number of tracks; then only the edition the folder names (year) and only pressings of
    what the files were made from (`media`: "cd", "vinyl", "digital"; "" when unknown). [] when
    none of them is what the files are."""
    scored = [r for r in rows if r.get("score") is not None]
    if not scored:
        return []
    best = max(scored, key=lambda r: r["score"])
    margin = float(cfg.get("auto_min_margin") or mc.DEFAULT_CFG["auto_min_margin"])
    out = [r for r in scored if r.get("source") == best.get("source") and best["score"] - r["score"] < margin
           and r.get("n_tracks") == best.get("n_tracks")
           and mc.title_sim(r.get("title") or "", best.get("title") or "") >= 0.85]
    if best.get("pressing_year") and best.get("year_ok"):
        out = [r for r in out if r.get("year_ok")]
    if media:
        same_kind = [r for r in out if media in (r.get("formats") or ())]
        if not same_kind and any(r.get("formats") for r in out):
            return []
        out = same_kind or out
    return out


COUNTRY_SAME = {"UK": "GB"}           # (Discogs writes UK where MusicBrainz writes GB)


def _same_form(key: str, value) -> str:
    """A detail as it is compared between pressings: codes without spaces and dashes, one
    form of each country; "" when not listed."""
    v = str(value or "").strip()
    if key in ("barcode", "catno"):
        return "; ".join(sorted({mc.norm_code(x) for x in v.split(";") if x.strip()}))
    if key == "country":
        code = mc.country_code(v) or v.upper()
        return COUNTRY_SAME.get(code, code)
    return v.casefold()


def _forms(rows: list, key: str) -> list:
    """Each row's compared form of `key`. Dates by year when any of them gives only a year."""
    vals = [_same_form(key, r.get(key)) for r in rows]
    if key == "date" and any(len(v) == 4 for v in vals if v):
        vals = [v[:4] for v in vals]
    return vals


def differing(rows: list) -> list:
    """The detail columns whose values are not the same on every row, in DIFF_ORDER."""
    if len(rows) < 2:
        return []
    return [k for k in DIFF_ORDER if len(set(_forms(rows, k))) > 1]


def hint(rows: list, limit=3) -> str:
    """What to look at on your copy to tell these pressings apart (the first detail that differs)."""
    diff = differing(rows)
    if not rows:
        return ""
    if not diff:
        return tr("These pressings list the same details: either one gives your files the same album tags.")
    k = diff[0]
    shown_by = {}                                    # (one value shown per compared form)
    for r, form in zip(rows, _forms(rows, k)):
        shown_by.setdefault(form, str(r.get(k) or "").strip() or tr("none listed"))
    vals = list(shown_by.values())
    shown = tr(" vs ").join(vals[:limit]) + (tr(" (or {0} more)")
                                             .format(len(vals) - limit) if len(vals) > limit else "")
    return f"{tr(CHECK[k])}: {shown}."
