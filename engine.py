"""engine.py - one processing engine for the CLI, the watcher, the GUI and the API.

  eng = Engine(profile)
  eng.albums_in(path)        -> [Group]          what is in a path: albums, disc sets, mixed
                                                  folders split by album, single files
  eng.identify(group)        -> plan.Proposal    MusicBrainz and Discogs (the matcher's own
                                                  evidence and scoring), fingerprints for
                                                  single tracks, the web as a last resort
  eng.plan(path)             -> plan dict        all of the above, then plan.build()
  executor.Executor(plan).run()                  the same executor every interface uses

Identification never depends on Picard. Tags come from the release itself:
  - MusicBrainz: mb_tags (Picard's mapping);
  - Discogs: the Discogs Enrichment plugin's own extraction code (discogs_mapping), so a
    Discogs-only album is tagged exactly as the plugin would tag it, with no MusicBrainz
    ID required or invented;
  - both, when they hold the same pressing (linked, or the matcher's "twin"): the
    MusicBrainz tags are the base and the Discogs release is applied to them the way the
    plugin applies it inside Picard (enrich(): its merge mode, style mode, field filters,
    protected tags, original date, track tags and artist-credit settling), with your
    plugin settings read from Picard.ini (picard_config). A profile's field_sources, if
    set, have the last word on named fields. Releases that are not the same pressing are
    never mixed.

Missing credentials, an outage or throttling at one provider is recorded on the album
(provider_errors) and does not make it unresolved when the other provider identified it.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import companions
import credentials
import exclusions as ex
import logsetup
import match_albums as ma
import match_core as mc
import mb_tags
import plan as planmod
import reasons

DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*(?:\d+|one|two|three|four|five|six|seven|eight|"
                      r"nine|ten|[a-h])\b|^\d{1,2}$", re.I)
_NUMBER_ONLY = re.compile(r"^\d{1,2}$")


_DISC_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
# "(Disc 1)", "[CD 2 of 3]", "(Disc One)", "(Disc 2: Live)" anywhere in the title, and an
# unbracketed "- Disc 2" / "CD1" at its end
_DISC_SUFFIX = re.compile(r"\s*[\(\[\{]\s*(?:disc|disk|cd|dvd)\s*" + _DISC_WORD +
                          r"(?:\s*(?:of|/)\s*\d+)?(?:\s*[:\-\u2013]\s*[^\)\]\}]*)?\s*[\)\]\}]"
                          r"|\s*(?:[-:,\u2013]\s*)?\b(?:disc|disk|cd|dvd)\s*" + _DISC_WORD +
                          r"(?:\s*(?:of|/)\s*\d+)?\s*$", re.I)


def _first(meta, k):
    return ((meta.get(k) or [""])[0] or "").strip()


def _album_title(meta) -> str:
    album = _first(meta, "album").casefold()
    return re.sub(r"\s+", " ", _DISC_SUFFIX.sub("", album)).strip() or album


def _one_compilation(files, metas, title, folder) -> bool:
    """Tracks sharing an album title, by several artists, with no album artist: one
    compilation (a freedb/EAC rip of a Various Artists CD) when they say so, when their
    folder is named after the album, or when their track numbers are a set of one album's."""
    if any(_first(metas[f], "compilation").lower() in ("1", "true", "yes") for f in files):
        return True
    fold = lambda s: re.sub(r"[\W_]+", " ", s.casefold()).strip()  # noqa: E731
    t = fold(title)
    named = re.compile(r"(?:^| )" + re.escape(t) + r"(?: |$)") if t else None
    if named and any(named.search(fold(d.name)) for d in {folder, *{f.parent for f in files}}):
        return True                            # "1999 - Hits 1999"; not "Demos" for "Demo"
    nums, totals = [], set()
    for f in files:
        n, _, tot = _first(metas[f], "tracknumber").partition("/")
        if not n.strip().isdigit():
            return False
        nums.append((_first(metas[f], "discnumber").partition("/")[0].strip(), int(n)))
        totals.add(tot.strip() or _first(metas[f], "totaltracks") or _first(metas[f], "tracktotal"))
    return len(set(nums)) == len(nums) and len(totals) == 1 and "" not in totals


def album_keys(files, folder) -> dict:
    """{file: which album it belongs to} for the audio files of one folder (with its disc
    folders): exclusions.album_key, with the disc left out of the title ("Album (Disc 1)"
    and "Album (Disc Two)" are one album). Within one title, tracks without an album artist
    join the album artist the others have; with no album artist at all, tracks by different
    artists are one compilation only when they look like one (_one_compilation) - otherwise
    "Greatest Hits" by Queen and by ABBA stay two albums."""
    metas = {f: ex.read_meta(f) for f in files}
    out, by_title = {}, {}
    for f in files:
        k = ex.album_key(metas[f])
        if k[0] != "tags":
            out[f] = k
        else:
            by_title.setdefault(_album_title(metas[f]), []).append(f)
    for title, fs in by_title.items():
        aa = {f: _first(metas[f], "albumartist").casefold() for f in fs}
        ar = {f: _first(metas[f], "artist").casefold() for f in fs}
        aas = {v for v in aa.values() if v}
        if aas:
            for f in fs:
                who = aa[f] or (next(iter(aas)) if len(aas) == 1 else ar[f])
                out[f] = ("tags", who, title)
            continue
        artists = set(ar.values())
        if len(artists) > 1 and not _one_compilation(fs, metas, title, Path(folder)):
            for f in fs:
                out[f] = ("tags", ar[f], title)
        else:
            who = next(iter(artists)) if len(artists) == 1 else ""
            for f in fs:
                out[f] = ("tags", who, title)
    return out


def is_disc_dir(d: Path) -> bool:
    """A disc folder ("CD1", "Disc 2", "Disc One", "Side A"). A folder named only by a
    number counts when its siblings are numbered 1, 2, ... too: "Adele/19" and "Adele/21"
    are albums, "Box/1" and "Box/2" are discs."""
    if not DISC_DIR.match(d.name):
        return False
    if not _NUMBER_ONLY.match(d.name):
        return True
    try:
        nums = sorted(int(x.name) for x in d.parent.iterdir() if x.is_dir() and _NUMBER_ONLY.match(x.name))
    except OSError:
        return False
    return len(nums) >= 2 and nums[0] == 1 and nums == list(range(1, len(nums) + 1))


@dataclass
class Group:
    folder: Path
    files: list = field(default_factory=list)
    others: list = field(default_factory=list)     # files in the same folder that belong elsewhere
    single: bool = False


class Engine:
    def __init__(self, profile, *, http=None, dm=None, ocr=False, log=None, job=None, cache=None):
        self.profile = profile
        # the matcher reads the profile's rules from here; the default profile has none of its own,
        # so a name left by an earlier engine (the GUI runs several) must not linger
        if profile.name != "default":
            os.environ["TAGMENDER_PROFILE"] = profile.name
        else:
            os.environ.pop("TAGMENDER_PROFILE", None)
        self.rules = ex.load_rules(profile.name if profile.name != "default" else None)
        self.job = job or logsetup.new_job_id()
        self.log = log or logsetup.setup("engine", job=self.job)
        self.ocr = ocr
        self.notes = {}                           # provider -> why it is off for this run
        cfg = profile.matcher_cfg(dict(mc.DEFAULT_CFG))
        countries = ma.picard_preferred_countries()
        if countries:
            cfg["preferred_countries"] = countries
        try:
            import json
            cfg["artist_aliases"] = json.loads((ex.CONFIG_DIR / "rules.json").read_text()).get("artist_aliases") or {}
        except (OSError, ValueError):
            pass
        self.cfg = cfg
        self.use_mb = profile.provider_on("musicbrainz")
        self.use_discogs = profile.provider_on("discogs")
        token = ""
        if self.use_discogs:
            try:
                token = credentials.get("discogs")
            except credentials.CredentialError as exc:
                self.notes["discogs"] = f"missing_credentials: {exc}"
            if not token and "discogs" not in self.notes:
                self.notes["discogs"] = "missing_credentials: no Discogs token (tagmender setup)"
            if not token:
                self.use_discogs = False
        self.token = token
        if dm is None and (self.use_discogs or token):
            try:
                dm = ma.load_plugin(str(companions.discogs_dir()))
            except SystemExit as exc:
                self.notes["discogs"] = f"discogs-enrichment code not found: {exc}"
                self.use_discogs = False
        self.dm = dm
        if http is None:
            ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(cache or (ex.STATE_DIR / "cache.sqlite"), timeout=60)
            http = ma.Http(db, cfg, token)
        self.http = http
        self._art = None

    # ---- what is in a path ------------------------------------------------
    def albums_in(self, path, skip=()) -> list[Group]:
        """The albums below path; skip: folders not looked into at all (a library inside a
        watched folder: its tracks are never even opened)."""
        path = Path(os.path.abspath(Path(path).expanduser()))  # plans and journals hold absolute paths
        if path.is_file():
            return [Group(path.parent, [path], [], single=True)]
        found = {}
        skip = {os.path.realpath(str(x)) for x in skip}
        for dirpath, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d != "2delete"
                             and os.path.realpath(os.path.join(dirpath, d)) not in skip)
            d = Path(dirpath)
            audio = sorted(d / n for n in names if Path(n).suffix.lower() in ex.AUDIO_EXT and not n.startswith("."))
            if audio:
                found[d] = audio
        # each audio folder's album folder: a disc folder belongs to its parent; a folder
        # inside an album's folder belongs to that album (a bonus disc filed inside it, as
        # match_albums._owners has it) only when both are tagged as that same album -
        # otherwise it is an album of its own
        disc = {d: d != path and is_disc_dir(d) for d in found}
        owner_of = {d: (d.parent if disc[d] else d) for d in found}
        own_key = {}
        for d in found:
            own_key.setdefault(owner_of[d], []).extend(found[d])
        own_key = {o: set(album_keys(fs, o).values()) for o, fs in own_key.items()}
        folders = {}
        for d in sorted(found):
            owner = owner_of[d]
            keys = own_key.get(owner, set())
            up = owner.parent
            while up == path or path in up.parents:      # (the named folder itself too: "run Album")
                if up in own_key and len(keys) == 1 and next(iter(keys))[0] == "tags" and own_key[up] == keys:
                    owner = up
                up = up.parent
            folders.setdefault(owner, []).extend(found[d])
        groups = []
        for folder in sorted(folders):
            files = folders[folder]
            if self.profile.excluded_path(folder, named=[path]):
                continue
            by_album = {}
            keys = album_keys(files, folder)
            for f in files:
                by_album.setdefault(keys[f], []).append(f)
            tagged = [k for k in by_album if k[0] == "tags"]
            spans_discs = len({f.parent for f in files}) > 1
            if len(by_album) > 1 and spans_discs and len(tagged) <= 1:
                by_album = {(tagged or [("folder", str(folder))])[0]: files}   # one disc set, partly tagged
            if len(by_album) == 1:
                groups.append(Group(folder, files, [], single=len(files) == 1))
                continue
            for key, members in by_album.items():            # a mixed folder: one group per album
                others = [f for f in files if f not in members]
                if key[0] == "folder":                       # untagged leftovers: each on its own
                    for m in members:
                        groups.append(Group(folder, [m], [f for f in files if f != m], single=True))
                else:
                    groups.append(Group(folder, members, others, single=len(members) == 1))
        return groups

    @staticmethod
    def _outside(g: Group) -> list:
        """Audio files below the group's folder that are not the group's own: a loose track's
        neighbours in a library root, another album nested inside this one, folders the
        grouping skips. Identification and the plan never see them."""
        mine = {str(f) for f in g.files}
        out = []
        for dirpath, _dirs, names in os.walk(g.folder):
            for n in names:
                p = os.path.join(dirpath, n)
                if Path(n).suffix.lower() in ex.AUDIO_EXT and p not in mine:
                    out.append(Path(p))
        return out

    # ---- identification ------------------------------------------------------
    def identify(self, g: Group) -> planmod.Proposal:
        errors = {k: v.split(":")[0] for k, v in self.notes.items()}
        # the rules first, on what the files themselves say: an excluded album gets no lookup,
        # fingerprint or web search at all (the planner labels it Ignored/Skipped)
        if ex.evaluate(g.files, self.rules, overrides=ex.State().overrides()).all_excluded:
            return planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                    status="unresolved", reason="excluded by a rule", reason_codes=["other"])
        row = {"FullPath": str(g.folder), "AlbumArtistFolder": g.folder.parent.name, "AlbumFolder": g.folder.name,
               "Status": "untagged"}
        outside = self._outside(g)
        args = argparse.Namespace(ocr=self.ocr, max_images=15, process_anyway=False, use_mb=self.use_mb,
                                  use_discogs=self.use_discogs, provider_errors=errors, exclude_files=outside)
        out = None
        if self.use_mb or self.use_discogs:
            try:
                try:
                    out = ma.process(row, self.http, self.cfg, self.dm, args)
                except ma.AuthError as exc:                  # token rejected: carry on without Discogs
                    errors["discogs"] = "missing_credentials"
                    self.notes["discogs"] = f"missing_credentials: Discogs rejected the token ({exc})"
                    self.log.error("Discogs rejected the token: %s", exc, extra={"provider": "discogs"})
                    self.use_discogs = args.use_discogs = False
                    out = ma.process(row, self.http, self.cfg, self.dm, args) if self.use_mb else None
            except ma.ProviderUnavailable as exc:
                errors[{"mb": "musicbrainz"}.get(exc.host, exc.host)] = exc.kind
                self.log.warning("%s", exc, extra={"provider": exc.host})
        album = ma.read_album(g.folder, g.folder.parent.name, g.folder.name,
                              exclude={str(x) for x in outside})
        mine = {str(f) for f in g.files}
        files = [t["path"] for t in album["tracks"] if t.get("path") in mine] or [str(f) for f in g.files]
        base = dict(source_dir=str(g.folder), files=files, provider_errors=errors)
        if out and out.get("status") in ("ignored", "skipped"):   # a rule matched the identified release
            return self._unresolved(out, files, base, errors)
        if out and out.get("status") == "auto" and out.get("best"):
            prov = {"mb": "musicbrainz"}.get(out["best"]["source"], out["best"]["source"])
            try:
                return self._identified(out, album, files, base)
            except ma.ProviderUnavailable as exc:             # fetching the release failed: retry later
                self.log.warning("tags for %s: %s", g.folder, exc)
                errors[prov] = exc.kind
                code = exc.kind if exc.kind == "rate_limited" else f"{prov}_unavailable"
                return planmod.Proposal(**base, status="unresolved", reason_codes=[code],
                                        reason=f"identified, but the release could not be fetched: {exc}")
            except Exception as exc:                          # the release does not fit the files after all
                self.log.warning("tags for %s: %s", g.folder, exc)
                return planmod.Proposal(**base, status="unresolved", reason_codes=["incomplete_album"],
                                        reason=f"matched, but the release's tracks do not line up with the files: {exc}")
        if out and out.get("album_only") and out.get("best"):
            # the album is certain, only its pressing is not: tag what every pressing shares
            try:
                return self._album_only(self._identified(out, album, files, base), out["album_only"])
            except Exception as exc:
                self.log.warning("album-only tags for %s: %s", g.folder, exc)
        if g.single and len(files) == 1 and ma.fingerprint_ready():
            p = self._single(Path(files[0]), base)
            if p:
                return p
        if self.profile.provider_on("web", "enabled") and album["tracks"]:
            p = self._web(out, album, files, base)
            if p:
                return p
        return self._unresolved(out, files, base, errors)

    def _release(self, best):
        return {"album": [best.get("title") or ""], "albumartist": [best.get("artist") or ""],
                "artist": [best.get("artist") or ""]}

    def _identified(self, out, album, files, base):
        best = out["best"]
        mbid = best.get("mb_id") or ""
        dgid = best.get("discogs_id") or (best["id"] if best["source"] == "discogs" else "")
        tags_mb = tags_dg = None
        if mbid and self.use_mb:
            rel = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}?inc={mb_tags.INC}&fmt=json", "mb")
            if rel:
                tags_mb = mb_tags.release_tags(rel)
        es = self.profile.enrichment()
        dparts = None
        if dgid and self.dm is not None and (self.use_discogs or self.token):
            det = self.http.get_json(f"https://api.discogs.com/releases/{dgid}", "discogs")
            if det:
                dparts = discogs_parts(det, self.dm, self.http, es)
        n = len(files)
        tags_mb = tags_mb if tags_mb and len(tags_mb) == n else None
        if dparts and (dparts[1] is None or len(dparts[1]) != n):
            dparts = None if not tags_mb else (dparts[0], None, dparts[2])
        if tags_mb and dparts and es["merge_mode"] == "mb_only":
            dparts = None
        if not tags_mb and dparts and dparts[1] is not None:
            tags_dg = discogs_only_tags(*dparts[:2])
        if not tags_mb and not tags_dg:
            raise RuntimeError("the matched release's tracks could not be read")
        order = ["musicbrainz"] + (["discogs"] if dparts else []) if tags_mb else ["discogs"]
        targets, prov = [], {}
        for i in range(n):
            dg_i = None
            if tags_mb:
                merged = {k: v for k, v in tags_mb[i].items() if not k.startswith("~") and v}
                where = dict.fromkeys(merged, "musicbrainz")
                if dparts:
                    album_f, tracks_f, dcount = dparts
                    track_i = tracks_f[i] if tracks_f else None
                    dg_i = _as_tags({**album_f, **(track_i or {})})
                    merged = enrich(merged, album_f, track_i, es, dcount, self.dm)
                    where = {k: ("discogs" if dg_i.get(k) == v else "musicbrainz") for k, v in merged.items()}
            else:
                dg_i = tags_dg[i]
                merged = {k: v for k, v in dg_i.items() if not k.startswith("~") and v}
                where = dict.fromkeys(merged, "discogs")
            src = {"musicbrainz": tags_mb[i] if tags_mb else {}, "discogs": dg_i or {}}
            for fld, prefs in self.profile["field_sources"].items():   # a profile's explicit choice
                for name in prefs:
                    v = src.get(name, {}).get(fld)
                    if v:
                        merged[fld], where[fld] = v, name
                        break
            if not self.profile["mark_resolved"]:
                merged.pop("tagmender", None)
            targets.append({k: (v if isinstance(v, list) else [v]) for k, v in merged.items()})
            prov.update(where)
        provider = "musicbrainz" if order[0] == "musicbrainz" else "discogs"
        return planmod.Proposal(
            **base, targets=targets, status="identified", provider=provider + (
                "+discogs" if len(order) == 2 and order[1] == "discogs" else
                "+musicbrainz" if len(order) == 2 else ""),
            release_id=mbid if provider == "musicbrainz" else dgid, url=best.get("url", ""),
            confidence=float(best.get("confidence") or 0), margin=out.get("margin"),
            why=list(out.get("why") or []), evidence=list(best.get("notes") or []),
            provenance=prov, release=self._release(best))

    def _album_only(self, p, info):
        """An identified album whose pressing is unknown: no release IDs, catalogue number or
        barcode; label, country, media and date only when every tied pressing agrees."""
        tied = info.get("tied") or []
        p.targets = [mc.album_only_tags(t, tied) for t in p.targets]
        p.release_id, p.url = "", ""
        p.provenance = {k: v for k, v in p.provenance.items() if k not in mc.PRESSING_FIELDS}
        p.why = list(p.why) + [f"pressing unknown ({len(tied)} pressings fit equally): album tags only; "
                               "release IDs, catalogue number and barcode left empty"]
        return p

    def _single(self, path, base):
        """A standalone track: its recording, from the fingerprint. This proves the recording,
        not which album edition the file came from, so no album fields are written."""
        fp = ma.fingerprint(str(path), self.http.db)
        if not fp:
            return None
        key = ma.acoustid_key()
        data = self.http.get_json(f"https://api.acoustid.org/v2/lookup?client={ma.q(key)}&format=json"
                                  f"&meta=recordings&duration={fp['duration']}"
                                  f"&fingerprint={ma.q(fp['fingerprint'])}", "acoustid")
        results = sorted((data or {}).get("results") or [], key=lambda r: -r.get("score", 0))
        if not results or results[0].get("score", 0) < 0.9 or not results[0].get("recordings"):
            return None
        top, rec = results[0], results[0]["recordings"][0]
        if len(results) > 1 and results[1].get("score", 0) > top["score"] - 0.05 and \
                (results[1].get("recordings") or [{}])[0].get("id") != rec.get("id"):
            return None
        names = [a.get("name", "") for a in rec.get("artists") or []]
        phrase = "".join(a.get("name", "") + (a.get("joinphrase") or "") for a in rec.get("artists") or [])
        t = {"title": [rec.get("title", "")], "artist": [phrase] if phrase else [], "artists": names,
             "musicbrainz_recordingid": [rec["id"]], "acoustid_id": [top["id"]],
             "musicbrainz_artistid": [a["id"] for a in rec.get("artists") or [] if a.get("id")]}
        return planmod.Proposal(**base, targets=[{k: v for k, v in t.items() if v}], status="identified",
                                provider="acoustid", release_id=rec["id"],
                                url=f"https://musicbrainz.org/recording/{rec['id']}",
                                confidence=round(top["score"] * 100, 1), evidence=["fingerprint"],
                                why=["recording only: the album edition is not established"],
                                release={"title": [rec.get("title", "")], "artist": [phrase]})

    def _web(self, out, album, files, base):
        import web_tag
        try:
            source, best, why = web_tag.find(self.http, album, self.cfg, "", out or {})
        except Exception as exc:
            self.log.warning("web lookup failed: %s", exc, extra={"provider": "web"})
            return None
        if not source:
            return None
        cache = {}
        tags = web_tag.tags_for(album, best["cand"], self.http, cache, source, self.token)
        targets = [{k: (v if isinstance(v, list) else [v]) for k, v in t.items() if v not in ("", [], None)}
                   for t in tags]
        c = best["cand"]
        return planmod.Proposal(**base, targets=targets, status="identified",
                                provider="discogs" if source == "Discogs" else f"web:{source}",
                                release_id=c.get("id", ""), url=c.get("url", ""),
                                confidence=float(best.get("confidence") or 0), why=list(why or []),
                                evidence=list(best.get("notes") or []),
                                release={"album": [c.get("title", "")], "albumartist": [c.get("artist", "")],
                                         "genre": [c.get("genre", "")]})

    def _unresolved(self, out, files, base, errors):
        status = (out or {}).get("status") or "nomatch"
        why = list((out or {}).get("why") or [])
        codes = []
        for prov, kind in errors.items():
            if kind in ("rate_limited", "missing_credentials"):
                codes.append(kind)
            elif kind in ("outage", "invalid_response"):
                codes.append(f"{prov}_unavailable")
        if out is None:                                      # nobody was asked: not a "no match"
            words = "no provider could be asked (" + ", ".join(f"{k}: {v}" for k, v in errors.items()) + ")"
            codes = codes or ["other"]
        else:
            words = "; ".join(why) or ("no candidates found" if status == "nomatch" else status)
            if status == "review":
                words = "review: " + words
            codes += [c for c in reasons.classify(words) if c != "other" or not codes]
        best = (out or {}).get("best") or {}
        if status in ("ignored", "skipped"):                   # the rules: the planner labels it
            codes = ["other"]
        return planmod.Proposal(**base, status="unresolved", reason=words, reason_codes=list(dict.fromkeys(codes)),
                                provider=best.get("source", ""), release_id=best.get("id", ""),
                                url=best.get("url", ""), confidence=float(best.get("confidence") or 0),
                                why=why, evidence=list(best.get("notes") or []),
                                release=self._release(best) if best else {})

    # ---- artwork -------------------------------------------------------------
    def add_artwork(self, prop: planmod.Proposal):
        """Fetch the front cover into staging (the executor writes it). Never raises."""
        if not self.profile["artwork"]["enabled"] or prop.status != "identified" or not prop.targets:
            return
        t = prop.targets[0]
        ids = {"release_mbid": (t.get("musicbrainz_albumid") or [""])[0],
               "rg_mbid": (t.get("musicbrainz_releasegroupid") or [""])[0],
               "artist_mbid": "; ".join(t.get("musicbrainz_albumartistid") or []),
               "discogs_release_id": (t.get("discogs_release_id") or [""])[0]}
        if not any(ids.values()):
            return
        try:
            import artwork
            folder, files = self._art_view(prop)
            small = artwork.small_covers(folder, files, self.profile)
            if not artwork.needs_art(folder, files, self.profile, small=small):
                return                        # every track and the folder have a cover: nothing to look for
            if self._art is None:
                self._art = artwork.chain(self.profile, ex.STATE_DIR / "staging" / self.job, self.http,
                                          self.token, self.log)
            own = artwork.own_cover(small, ex.STATE_DIR / "staging" / self.job) if small else None
            got = artwork.find_front(self._art, prop.source_dir, ids, (t.get("albumartist") or [""])[0],
                                     (t.get("album") or [""])[0], self.log, files=prop.files, own=own,
                                     keep=bool(small))
            if got and got.get("front") and small:
                # what the larger cover replaces (artwork.replace "larger_only"); backed up, undoable
                got["replace"] = {"file": str(small["file"][0]) if small.get("file") else None,
                                  "embedded": sorted(small.get("embedded") or {})}
        except Exception as exc:
            got = {"note": f"artwork: {exc}"}
        if got and got.get("front"):
            prop.artwork = got
        elif got and got.get("note"):
            prop.why = list(prop.why) + [f"no cover: {got['note']}"]

    def _art_view(self, prop):
        """Where the album's covers are judged: for a copy profile whose copies exist already,
        the copies (what the library has; the source is never changed); else the album itself."""
        if self.profile["operation"] != "copy" or not self.profile["destination"]:
            return prop.source_dir, prop.files
        copies = []
        for f in prop.files:
            c = [x for x in planmod.copies_of(f, under=self.profile["destination"]) if Path(x).is_file()]
            if not c:
                return prop.source_dir, prop.files
            copies.append(Path(c[0]))
        folder = Path(os.path.commonpath([str(c.parent) for c in copies]))
        if is_disc_dir(folder):
            folder = folder.parent
        return folder, copies

    # ---- everything ---------------------------------------------------------
    def apply_level(self, prop: planmod.Proposal):
        """Basic, Enthusiast or Audiophile (levels.py): the Audiophile measurements are added,
        then each file's tags are cut to what the profile's level writes."""
        import levels
        if levels.items(self.profile) and prop.status == "identified":
            if getattr(self, "_analysis", None) is None:
                self._analysis = levels.Cache(ex.STATE_DIR / "analysis.db")
            try:
                prop.why = list(prop.why or []) + levels.audiophile(prop, self.profile, self.http,
                                                                     self._analysis, self.log)
            except Exception as exc:                          # measurements never block tagging
                self.log.warning("audiophile measurements for %s failed: %s", prop.source_dir, exc)
                prop.why = list(prop.why or []) + [f"audiophile measurements failed: {exc}"]
        prop.targets = [levels.restrict(t, self.profile) for t in prop.targets or []]

    def proposals(self, path, progress=None, cancel=None):
        groups = self.albums_in(path)
        out = []
        for i, g in enumerate(groups):
            if cancel and cancel():
                break
            if progress:
                progress(i, len(groups), str(g.folder))
            try:
                prop = self.identify(g)
                self.add_artwork(prop)
                self.apply_level(prop)
                out.append(prop)
            except Exception as exc:                          # one bad album never stops the run
                self.log.exception("identifying %s failed", g.folder)
                out.append(planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                            status="unresolved", reason=f"error: {exc}", reason_codes=["other"]))
        return out

    def plan(self, path, progress=None, cancel=None) -> dict:
        props = self.proposals(path, progress, cancel)
        p = planmod.build(props, self.profile, self.job, extra_roots=[Path(os.path.abspath(Path(path).expanduser()))])
        p["provider_notes"] = self.notes
        return p


# --------------------------------------------------------------------------
# Discogs release -> tags, with the Discogs Enrichment plugin's own code
# --------------------------------------------------------------------------
def _flatten_raw(tracklist, parent_position=None):
    """match_albums.flatten_tracklist, keeping each entry's own fields (artists...)."""
    out = []
    for e in tracklist or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type_") or e.get("type") or "track").strip().casefold()
        if kind == "heading":
            continue
        if kind == "index":
            subs = _flatten_raw(e.get("sub_tracks"), parent_position=e.get("position") or "")
            if subs:
                out.extend(subs)
                continue
        d = dict(e)
        d["position"] = parent_position if parent_position is not None else (e.get("position") or "")
        out.append(d)
    return out


class Meta:
    """The little of Picard's Metadata the plugin's merge code uses, over a tag dict."""

    def __init__(self, tags: dict):
        self.d = {k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in tags.items()}

    def getall(self, k):
        return list(self.d.get(k, []))

    def get(self, k, default=""):
        v = self.d.get(k)
        return v[0] if v else default

    def __setitem__(self, k, v):
        self.d[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]

    def __contains__(self, k):
        return k in self.d

    def delete(self, k):
        self.d.pop(k, None)

    def keys(self):
        return list(self.d)


def shape_fields(fields: dict, s: dict, dm) -> dict:
    """The plugin's _shape_fields: style routing, then the field groups switched off and the
    protected tags dropped; notes only when imported."""
    fields = dm.apply_style_mode(dict(fields), s["style_mode"] or "separate")
    fields = dm.filter_fields(fields, marketplace=bool(s["marketplace"]), performers=bool(s["performers"]),
                              companies=bool(s["companies"]), identifiers=bool(s["identifiers"]),
                              protected=dm.parse_protected_tags(s["protected_tags"]))
    if not s["import_notes"]:
        fields.pop("discogs_notes", None)
    return fields


def discogs_parts(det, dm, http=None, s=None):
    """(album fields, [per-track fields], disc count) of a Discogs release, as the plugin
    builds them: extract_discogs_fields shaped by the settings, then the master's original
    year (and date = original date when prefer_original_date is on). Per-track fields are in
    the matcher's track order, each with its disc fields; None when the tracklist does not
    line up with the matcher's."""
    import picard_config
    s = s or picard_config.enrichment()
    album = shape_fields(dm.extract_discogs_fields(det), s, dm)
    if det.get("master_id") and http is not None:
        try:
            master = http.get_json(f"https://api.discogs.com/masters/{det['master_id']}", "discogs")
            if master:
                album.update(dm.extract_original_year(master))
        except Exception:
            pass
    if s["prefer_original_date"]:
        original = album.get("originaldate") or album.get("originalyear")
        if original:
            album["date"] = original
    raw = ma.drop_video_tracks(_flatten_raw(det.get("tracklist")), det)
    cand = ma.discogs_candidate(det, dm)
    discs = [t["disc"] for t in cand["tracks"]]
    total_discs = dm.release_disc_count(det) or max(discs or [1])
    if len(discs) != len(raw):
        return album, None, total_discs
    per_disc = {}
    for d in discs:
        per_disc[d] = per_disc.get(d, 0) + 1
    seen, tracks = {}, []
    for entry, disc in zip(raw, discs):
        seen[disc] = seen.get(disc, 0) + 1
        t = dict(dm.extract_disc_fields({"number": disc}, total_discs))
        # numbered within each disc: the plugin's automatic path numbers through the whole
        # release, which would give disc 2's first track number 11
        t.update(dm.extract_track_fields(entry, seen[disc], per_disc[disc]))
        t["discnumber"], t["totaldiscs"] = str(disc), str(total_discs)
        tracks.append(t)
    return album, tracks, total_discs


def _as_tags(d: dict) -> dict:
    return {k: (v if isinstance(v, list) else [str(v)]) for k, v in d.items()
            if not str(k).startswith("~") and v not in ("", None, [])}


def discogs_tags(det, dm, http=None, s=None) -> list[dict]:
    """A release tagged from Discogs alone (no MusicBrainz release): every field from Discogs,
    shaped by the plugin's settings."""
    album, tracks, _ = discogs_parts(det, dm, http, s)
    return discogs_only_tags(album, tracks)


def discogs_only_tags(album: dict, tracks) -> list[dict]:
    if tracks is None:
        return []
    out = []
    for t in tracks:
        merged = dict(album)
        merged.update(t)
        merged.setdefault("artist", album.get("albumartist", ""))
        out.append(_as_tags(merged))
    return out


TRACK_ONLY = ("discnumber", "totaldiscs", "disctotal", "discsubtitle")


def enrich(mb: dict, album: dict, track: dict | None, s: dict, disc_count: int, dm) -> dict:
    """One file's MusicBrainz tags with the Discogs release applied, as the plugin applies
    it in Picard (_apply_discogs_fields, then the per-track fields through the merge mode,
    "Discogs only" clearing, then artist credits settled)."""
    meta = Meta(mb)
    mode = s["merge_mode"] or "combine"
    if s["split_collaborations"]:                      # done by the plugin on MusicBrainz's data
        try:
            dm.settle_artist_credits(meta, dm.snapshot_artist_credits(meta), (), True, None)
        except Exception:
            pass
    if mode == "mb_only":
        return meta.d
    names_before = dm.snapshot_artist_credits(meta)
    boxset = dm.is_probable_boxset(meta.getall("album"), album.get("album"), disc_count)
    for f, v in album.items():
        if dm.is_supplementary_field(f):
            meta[f] = v
            continue
        if f == "album":
            for tf, tv in dm.resolve_album_and_subtitle(meta.getall("album"), v,
                                                        meta.getall("discsubtitle"), mode).items():
                meta[tf] = tv
            continue
        eff = "fill_gaps" if (boxset and f in dm.PROTECTED_IN_BOXSET) else mode
        r = dm.resolve_field_value(meta.getall(f), v, eff, field=f)
        if r is not None:
            meta[f] = r
    per = {}
    if s["track_tags"] and track:
        # the automatic path writes track identity only; disc numbers come from MusicBrainz
        per = {k: v for k, v in track.items() if k not in TRACK_ONLY}
        for f, v in per.items():
            if f.startswith("~"):
                continue
            r = dm.resolve_field_value(meta.getall(f), v, mode, field=f)
            if r is not None:
                meta[f] = r
    supplied = set(album) | set(per)
    if mode == "discogs_only":
        for name in dm.tags_to_clear(meta.keys(), supplied, dm.parse_protected_tags(s["protected_tags"])):
            meta.delete(name)
    try:
        dm.settle_artist_credits(meta, names_before, supplied, mode != "discogs_only", None)
    except Exception:
        pass
    return meta.d
