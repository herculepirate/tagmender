# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""The artwork pipeline: identify -> discover -> fingerprint -> select -> plan (design section 4).

Blocking; run it in a worker thread. It has no Picard imports: the HTTP client and the image
fingerprint function are injected, so the whole pipeline is unit-testable.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .creative.trigger import CreativeSettings, Situation, Trigger, should_generate
from .diagnostics.logging_setup import log_context
from .imageinfo import probe
from .matching.disc import assign_disc_numbers
from .matching.scoring import Selection, check_limits, limits_from_settings, score_candidate, select_best
from .model import ArtType, Candidate, Query, Tier
from .net import Cancelled, HttpClient, HttpError
from .providers import REGISTRY
from .providers.musicbrainz import enrich_query
from .settings import ARTIST_TYPES, Settings

log = logging.getLogger("coverup.pipeline")

MAX_FINGERPRINTS = 120
THUMB_MAX_BYTES = 4 * 1024 * 1024
PROBE_BYTES = 65536

# fingerprint(data) -> object with .phash .blank .width .height, or None
FingerprintFn = Callable[[bytes], object]


@dataclass
class PlanItem:
    art_type: ArtType
    candidate: Candidate
    disc_number: int | None = None
    page: int | None = None


@dataclass
class AlbumResult:
    query: Query
    run_id: str
    candidates: list[Candidate] = field(default_factory=list)
    selections: dict[ArtType, Selection] = field(default_factory=dict)
    discs: dict[int, Selection] = field(default_factory=dict)
    booklet: list[Candidate] = field(default_factory=list)
    artist: dict[ArtType, Candidate | None] = field(default_factory=dict)
    logos: list[Candidate] = field(default_factory=list)
    provider_status: dict[str, str] = field(default_factory=dict)
    creative: dict[ArtType, tuple[bool, str]] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    cancelled: bool = False

    def of_type(self, art_type: ArtType, include_unlabelled: bool = False) -> list[Candidate]:
        out = [c for c in self.candidates if c.art_type == art_type]
        if include_unlabelled:
            out += [c for c in self.candidates if c.art_type == ArtType.OTHER]
        return out

    @property
    def summary(self) -> str:
        front = self.selections.get(ArtType.FRONT)
        if not front or not front.winner:
            return "No front cover found"
        if front.conflict:
            return "Needs review (sources disagree)"
        return front.rationale


def _creative_settings(s: Settings) -> CreativeSettings:
    trig = Trigger.NEVER
    for name in s["creative_triggers"] or []:
        trig |= {"no_cover": Trigger.NO_COVER, "below_min": Trigger.BELOW_MIN_RESOLUTION, "low_confidence": Trigger.LOW_CONFIDENCE}.get(
            name, Trigger.NEVER
        )
    # generated art is produced for the front cover (back/disc/booklet stay real scans)
    return CreativeSettings(triggers=trig, always_generate=bool(s["creative_always"]), art_types=(ArtType.FRONT,), apply_min_side=int(s["apply_min_side"]))


class Pipeline:
    def __init__(self, settings: Settings, http: HttpClient, fingerprint: FingerprintFn | None = None, cancel: threading.Event | None = None):
        self.s = settings
        self.http = http
        self.fingerprint = fingerprint
        self.cancel = cancel or http.cancel

    # ---------------------------------------------------------------------------------------------
    def run(self, q: Query, run_id: str | None = None) -> AlbumResult:
        run_id = run_id or uuid.uuid4().hex[:8]
        res = AlbumResult(query=q, run_id=run_id)
        with log_context(run=run_id, album=q.release_mbid or q.label_text[:40]):
            try:
                self._run(q, res)
            except Cancelled:
                res.cancelled = True
                log.info("Cancelled")
        return res

    def _run(self, q: Query, res: AlbumResult) -> None:
        t0 = time.monotonic()
        log.info("Searching artwork for %s (release %s)", q.label_text, q.release_mbid or "-")
        enrich_query(self.http, q)
        self._format = q.format or ""
        res.timings["identify"] = time.monotonic() - t0

        t1 = time.monotonic()
        res.candidates = self.discover(q, res.provider_status)
        res.timings["discover"] = time.monotonic() - t1

        t2 = time.monotonic()
        self.measure(res.candidates)
        assign_disc_numbers(res.candidates, q.medium_count or 1)
        res.timings["fingerprint"] = time.monotonic() - t2

        t3 = time.monotonic()
        self.select(res)
        res.timings["select"] = time.monotonic() - t3
        log.info(
            "Done: %d candidates; %s; timings %s",
            len(res.candidates), res.summary, {k: round(v, 2) for k, v in res.timings.items()},
        )

    # ---------------------------------------------------------------------------------------------
    def discover(self, q: Query, status: dict[str, str]) -> list[Candidate]:
        providers = []
        for pid in self.s.enabled_providers():
            cls = REGISTRY.get(pid)
            if not cls:
                continue
            prov = cls(self.http, self.s)
            ok, why = prov.available()
            if not ok:
                status[pid] = f"skipped: {why}"
                log.info("Provider %s skipped: %s", pid, why)
                continue
            providers.append(prov)
        found: list[Candidate] = []
        ctx = _copy_context()
        with ThreadPoolExecutor(max_workers=max(1, min(6, len(providers)))) as pool:
            futures = {pool.submit(ctx_run(ctx, self._safe_search), p, q, status): p for p in providers}
            for fut in as_completed(futures):
                found.extend(fut.result())
        if self.cancel.is_set():
            raise Cancelled()
        seen: set[str] = set()
        unique = []
        for c in found:
            key = c.url
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        log.info("Discovered %d candidates (%s)", len(unique), ", ".join(f"{k}: {v}" for k, v in sorted(status.items())))
        return unique

    def _safe_search(self, prov, q: Query, status: dict[str, str]) -> list[Candidate]:
        started = time.monotonic()
        try:
            cands = prov.search(q)
            status[prov.ID] = f"{len(cands)} found"
            log.info("Provider %s: %d candidates in %.2fs", prov.ID, len(cands), time.monotonic() - started)
            return cands
        except Cancelled:
            status[prov.ID] = "cancelled"
            return []
        except HttpError as exc:
            status[prov.ID] = f"error: {exc}"
            log.warning("Provider %s failed: %s", prov.ID, exc)
            return []
        except Exception as exc:  # a broken provider must never break the run
            status[prov.ID] = f"error: {exc.__class__.__name__}"
            log.exception("Provider %s crashed", prov.ID)
            return []

    # ---------------------------------------------------------------------------------------------
    def measure(self, cands: list[Candidate]) -> None:
        """Fetch thumbnails, compute perceptual hashes and learn real sizes where unknown."""
        todo = [c for c in cands if c.art_type not in ARTIST_TYPES or c.art_type == ArtType.ARTIST_THUMB][:MAX_FINGERPRINTS]
        ctx = _copy_context()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(ctx_run(ctx, self._measure_one), todo))
        # artist logos/backgrounds/banners: thumbnails only (for the dialog), no hashing needed
        rest = [c for c in cands if c not in todo and c.thumb_data is None and c.thumb_url]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(ctx_run(ctx, self._thumb_only), rest[:40]))
        if self.cancel.is_set():
            raise Cancelled()

    def _thumb_only(self, c: Candidate) -> None:
        try:
            c.thumb_data = self.http.get(c.thumb_url, max_bytes=THUMB_MAX_BYTES).data
        except (HttpError, Cancelled):
            pass

    def _measure_one(self, c: Candidate) -> None:
        if self.cancel.is_set():
            return
        try:
            if c.thumb_data is None and c.thumb_url:
                c.thumb_data = self.http.get(c.thumb_url, max_bytes=THUMB_MAX_BYTES).data
        except (HttpError, Cancelled) as exc:
            log.debug("Thumbnail failed for %s: %s", c.provider, exc)
        if c.thumb_data and self.fingerprint:
            fp = self.fingerprint(c.thumb_data)
            if fp is None:
                c.rejected_reason = "Not a valid image"
                log.debug("Undecodable thumbnail from %s", c.provider)
            else:
                c.phash, c.blank = fp.phash, fp.blank
                if c.thumb_url == c.url and not c.dims_known:
                    c.width, c.height, c.dims_known = fp.width, fp.height, True
        if not c.dims_known and c.url.startswith("http"):
            try:
                head = self.http.get(c.url, range_bytes=PROBE_BYTES).data
                info = probe(head)
                if info:
                    c.width, c.height, c.dims_known = info[0], info[1], True
            except (HttpError, Cancelled) as exc:
                log.debug("Size probe failed for %s: %s", c.provider, exc)
        log.log(
            5, "Candidate %s %s %s %dx%d tier=%s hash=%s blank=%s",
            c.provider, c.art_type.value, c.provider_id, c.width, c.height, c.tier.value,
            f"{c.phash:016x}" if c.phash is not None else "-", c.blank,
        )

    # ---------------------------------------------------------------------------------------------
    def _select(self, cands: list[Candidate], art: ArtType) -> Selection:
        valid = [c for c in cands if c.rejected_reason != "Not a valid image"]
        sel = select_best(
            valid, art, limits_from_settings(self.s, art, getattr(self, "_format", "")),
            hamming_threshold=int(self.s["hamming_threshold"]), conflict_min_sources=int(self.s["conflict_min_sources"]),
        )
        for c in sel.rejected:
            log.debug("Rejected %s %s: %s", c.provider, art.value, c.rejected_reason)
        if sel.winner:
            log.info("%s: %s (score %.1f, %s)", art.value, sel.rationale, sel.winner.score, sel.winner.breakdown and {k: round(v, 1) for k, v in sel.winner.breakdown.items()})
        else:
            log.info("%s: %s", art.value, sel.rationale)
        return sel

    def select(self, res: AlbumResult) -> None:
        q = res.query
        for art in (ArtType.FRONT, ArtType.BACK):
            res.selections[art] = self._select(res.of_type(art), art)
        # unlabelled images (e.g. Discogs secondary) are scored for display but never auto-selected
        other_lim = limits_from_settings(self.s, ArtType.BOOKLET)
        for c in res.of_type(ArtType.OTHER):
            c.rejected_reason = check_limits(c, other_lim)
            score_candidate(c, other_lim)
        # discs
        n = max(1, q.medium_count or 1)
        discs = res.of_type(ArtType.DISC)
        for d in range(1, n + 1):
            subset = [c for c in discs if c.disc_number == d or (c.disc_number is None and n == 1)]
            res.discs[d] = self._select(subset, ArtType.DISC)
        # booklet: all eligible pages from the most trusted source
        lim = limits_from_settings(self.s, ArtType.BOOKLET)
        pages = []
        for c in res.of_type(ArtType.BOOKLET):
            c.rejected_reason = c.rejected_reason or check_limits(c, lim)
            score_candidate(c, lim)
            if not c.rejected_reason:
                pages.append(c)
        if pages:
            groups: dict[tuple[str, Tier], list[Candidate]] = {}
            for c in pages:
                groups.setdefault((c.provider, c.tier), []).append(c)
            best = max(groups.items(), key=lambda kv: (kv[0][1].rank, len(kv[1])))
            res.booklet = sorted(best[1], key=lambda c: c.page)
            log.info("booklet: %d page(s) from %s (tier %s)", len(res.booklet), best[0][0], best[0][1].value)
        # artist images
        for art in ARTIST_TYPES:
            pool = [c for c in res.of_type(art)]
            lim = limits_from_settings(self.s, art)
            for c in pool:
                c.rejected_reason = check_limits(c, lim) if art == ArtType.ARTIST_THUMB else None
                score_candidate(c, lim)
            ok = [c for c in pool if not c.rejected_reason]
            res.artist[art] = max(ok, key=lambda c: (c.tier.rank, c.type_confidence, c.likes, c.width * c.height), default=None)
        res.logos = sorted(res.of_type(ArtType.ARTIST_LOGO), key=lambda c: (c.type_confidence, c.likes), reverse=True)
        # the user's own files are their choice: shown whatever their size or shape (auto-pick is already decided above)
        for c in res.candidates:
            if c.provider == "local":
                c.rejected_reason = None
        # creative fallback evaluation
        cs = _creative_settings(self.s)
        for art in (ArtType.FRONT,):
            sel = res.selections.get(art)
            win = sel.winner if sel else None
            sit = Situation(
                art,
                found=win is not None,
                best_side=max(win.width, win.height) if win else 0,
                conflict_or_tier_c_only=bool(sel and (sel.conflict or (win and win.tier == Tier.C))),
            )
            res.creative[art] = should_generate(sit, cs)
            if res.creative[art][0]:
                log.info("Creative fallback for %s: %s", art.value, res.creative[art][1])

    # ---------------------------------------------------------------------------------------------
    def auto_plan(self, res: AlbumResult, types: set[ArtType] | None = None) -> tuple[list[PlanItem], list[str]]:
        """What "auto-apply" would apply, plus reasons for everything skipped."""
        types = types if types is not None else self.s.apply_art_types()
        plan: list[PlanItem] = []
        notes: list[str] = []
        min_apply = int(self.s["apply_min_side"])

        def ok(sel: Selection | None, label: str) -> Candidate | None:
            if not sel or not sel.winner:
                notes.append(f"{label}: nothing found")
                return None
            w = sel.winner
            if sel.conflict:
                notes.append(f"{label}: {sel.rationale}")
                return None
            if self.s["auto_apply_mode"] == "never":
                notes.append(f"{label}: auto-apply is off")
                return None
            if w.provider == "local":
                notes.append(f"{label}: the existing local file is already the best match")
                return None
            if w.dims_known and max(w.width, w.height) < min_apply and not self.s["upscale_to_minimum"]:
                notes.append(f"{label}: best image is {w.width}x{w.height}, below the {min_apply}px apply minimum")
                return None
            return w

        for art in (ArtType.FRONT, ArtType.BACK):
            if art in types and (w := ok(res.selections.get(art), art.value)):
                plan.append(PlanItem(art, w))
        if ArtType.DISC in types:
            for d, sel in sorted(res.discs.items()):
                if w := ok(sel, f"disc {d}"):
                    plan.append(PlanItem(ArtType.DISC, w, disc_number=d))
        if ArtType.BOOKLET in types:
            if res.booklet and res.booklet[0].tier == Tier.A:
                plan += [PlanItem(ArtType.BOOKLET, c, page=i + 1) for i, c in enumerate(res.booklet)]
            elif res.booklet:
                notes.append("booklet: pages are not from the exact release; choose them in the dialog")
        for art in ARTIST_TYPES:
            if art in types and res.artist.get(art):
                plan.append(PlanItem(art, res.artist[art]))
        return plan, notes

    def fetch_full(self, c: Candidate) -> bytes:
        """Download the full-size image (once) and record its real size.

        If the original cannot be fetched (a busy archive answering HTTP 500 for a huge scan, say), the
        candidate's smaller renditions are tried in turn, so a server hiccup costs resolution, not the cover.
        """
        if c.data is None:
            last: HttpError | None = None
            urls = [c.url, *(c.extra.get("fallback_urls") or [])]
            for n, url in enumerate(urls):
                try:
                    c.data = self.http.get(url, cache=False).data
                except HttpError as exc:
                    last = exc
                    log.warning("Download failed (%s); %s", exc, "trying a smaller version" if n < len(urls) - 1 else "no more versions")
                    continue
                if url != c.url:
                    log.info("Used a smaller version of %s: %s", c.url, url)
                    c.url = url
                break
            if c.data is None:
                raise last or HttpError(0, c.url, "no data")
        c.bytes = len(c.data)
        info = probe(c.data[:PROBE_BYTES])
        if info:
            c.width, c.height, c.dims_known = info[0], info[1], True
        return c.data

    def fetch_items(self, items: list) -> tuple[list, list[tuple[object, Exception]]]:
        """Download each plan item. Returns (items that downloaded, [(item, error)]); one failure never blocks the rest."""
        ok, failed = [], []
        for it in items:
            try:
                if it.candidate.data is None:
                    self.fetch_full(it.candidate)
                ok.append(it)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - reported per image
                log.warning("Skipping %s from %s: %s", it.art_type.value, it.candidate.provider, exc)
                failed.append((it, exc))
        return ok, failed


# ---- context propagation into worker threads (so log lines keep their run/album IDs) --------------

def _copy_context():
    import contextvars

    return contextvars.copy_context()


def ctx_run(ctx, fn):
    def wrapper(*a, **k):
        return ctx.copy().run(fn, *a, **k)

    return wrapper
