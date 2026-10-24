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
    profile's primary provider gives the base and field_sources decide named fields;
    other fields are only filled where the base has none. Releases that are not the same
    pressing are never mixed.

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

DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*\d+\b|^\d{1,2}$", re.I)


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
    def albums_in(self, path) -> list[Group]:
        path = Path(path).expanduser()
        if path.is_file():
            return [Group(path.parent, [path], [], single=True)]
        folders = {}
        for dirpath, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d != "2delete")
            d = Path(dirpath)
            audio = sorted(d / n for n in names if Path(n).suffix.lower() in ex.AUDIO_EXT and not n.startswith("."))
            if not audio:
                continue
            owner = d.parent if DISC_DIR.match(d.name) and d != path else d
            folders.setdefault(owner, []).extend(audio)
        groups = []
        for folder in sorted(folders):
            files = folders[folder]
            if self.profile.excluded_path(folder):
                continue
            by_album = {}
            for f in files:
                by_album.setdefault(ex.album_key(ex.read_meta(f)), []).append(f)
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
        args = argparse.Namespace(ocr=self.ocr, max_images=15, process_anyway=False, use_mb=self.use_mb,
                                  use_discogs=self.use_discogs, provider_errors=errors, exclude_files=g.others)
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
                              exclude={str(x) for x in g.others})
        files = [t["path"] for t in album["tracks"] if t.get("path")] or [str(f) for f in g.files]
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
        if dgid and self.dm is not None and (self.use_discogs or self.token):
            det = self.http.get_json(f"https://api.discogs.com/releases/{dgid}", "discogs")
            if det:
                tags_dg = discogs_tags(det, self.dm, self.http)
        n = len(files)
        tags_mb = tags_mb if tags_mb and len(tags_mb) == n else None
        tags_dg = tags_dg if tags_dg and len(tags_dg) == n else None
        if not tags_mb and not tags_dg:
            raise RuntimeError("the matched release's tracks could not be read")
        primary = self.profile["primary"]
        order = (["discogs", "musicbrainz"] if primary == "discogs" else ["musicbrainz", "discogs"])
        if primary == "both" or (primary == "discogs" and tags_mb):
            order = ["musicbrainz", "discogs"]                  # Picard + Discogs Enrichment: MB base, Discogs fills
        src = {"musicbrainz": tags_mb, "discogs": tags_dg}
        order = [o for o in order if src[o]]
        targets, prov = [], {}
        for i in range(n):
            merged, where = {}, {}
            for name in order:                                  # base first, others fill gaps only
                for k, v in src[name][i].items():
                    if k.startswith("~") or not v:
                        continue
                    if k not in merged:
                        merged[k], where[k] = v, name
            for fld, prefs in self.profile["field_sources"].items():   # named fields: the profile decides
                for name in prefs:
                    v = (src.get(name) or [{}] * n)[i].get(fld) if src.get(name) else None
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
            if self._art is None:
                self._art = artwork.chain(self.profile, ex.STATE_DIR / "staging" / self.job, self.http,
                                          self.token, self.log)
            got = artwork.find_front(self._art, prop.source_dir, ids, (t.get("albumartist") or [""])[0],
                                     (t.get("album") or [""])[0], self.log)
        except Exception as exc:
            got = {"note": f"artwork: {exc}"}
        if got and got.get("front"):
            prop.artwork = got
        elif got and got.get("note"):
            prop.why = list(prop.why) + [f"no cover: {got['note']}"]

    # ---- everything ---------------------------------------------------------
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
                out.append(prop)
            except Exception as exc:                          # one bad album never stops the run
                self.log.exception("identifying %s failed", g.folder)
                out.append(planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                            status="unresolved", reason=f"error: {exc}", reason_codes=["other"]))
        return out

    def plan(self, path, progress=None, cancel=None) -> dict:
        props = self.proposals(path, progress, cancel)
        p = planmod.build(props, self.profile, self.job, extra_roots=[Path(path).expanduser()])
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


def discogs_tags(det, dm, http=None) -> list[dict]:
    """Per audio track, in the matcher's order: album fields from extract_discogs_fields,
    the master's original year, disc and track fields - as the plugin writes them."""
    album = dict(dm.extract_discogs_fields(det))
    if det.get("master_id") and http is not None:
        try:
            master = http.get_json(f"https://api.discogs.com/masters/{det['master_id']}", "discogs")
            if master:
                album.update(dm.extract_original_year(master))
        except Exception:
            pass
    raw = ma.drop_video_tracks(_flatten_raw(det.get("tracklist")), det)
    cand = ma.discogs_candidate(det, dm)
    discs = [t["disc"] for t in cand["tracks"]]
    if len(discs) != len(raw):
        return []
    total_discs = dm.release_disc_count(det) or max(discs or [1])
    per_disc = {}
    for d in discs:
        per_disc[d] = per_disc.get(d, 0) + 1
    seen, out = {}, []
    for entry, disc in zip(raw, discs):
        seen[disc] = seen.get(disc, 0) + 1
        t = dict(album)
        t.update(dm.extract_disc_fields({"number": disc}, total_discs))
        t.update(dm.extract_track_fields(entry, seen[disc], per_disc[disc]))
        t.setdefault("artist", album.get("albumartist", ""))
        t["discnumber"], t["totaldiscs"] = str(disc), str(total_discs)
        out.append({k: (v if isinstance(v, list) else [str(v)]) for k, v in t.items()
                    if not str(k).startswith("~") and v not in ("", None, [])})
    return out
