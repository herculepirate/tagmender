# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Candidate limits, scoring and cluster selection (design sections 3.2, 3.5, 5.2, 5.3).

Every rejection and every score component is recorded so the UI and the log can explain a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import ArtType, Candidate, Tier
from .cluster import Cluster, cluster_candidates


@dataclass
class ResolutionLimits:
    """Search/acceptance limits for one art type (apply limits live in the apply layer)."""

    min_width: int = 0
    min_height: int = 0
    max_width: int = 0  # 0 = no limit
    max_height: int = 0
    min_bytes: int = 0
    max_bytes: int = 0
    aspect_min: float = 0.0  # 0 = no limit
    aspect_max: float = 0.0
    min_long_side: int = 0  # minimum for the longer side (used for back covers and scans)
    target_size: int = 1200  # preferred longest side for the "closest to target" preference
    prefer_largest: bool = False


DEFAULT_LIMITS: dict[ArtType, ResolutionLimits] = {
    ArtType.FRONT: ResolutionLimits(min_width=500, min_height=500, aspect_min=0.95, aspect_max=1.05),
    ArtType.BACK: ResolutionLimits(min_width=300, min_height=300, aspect_min=0.5, aspect_max=2.0),
    ArtType.DISC: ResolutionLimits(min_width=300, min_height=300, aspect_min=0.9, aspect_max=1.1),
    ArtType.BOOKLET: ResolutionLimits(min_width=300, min_height=300),
}


# Release formats whose front cover is not square (cassette J-cards, DVD/Blu-ray/VHS cases, ...)
NON_SQUARE_FORMATS = ("cassette", "dvd", "blu-ray", "bluray", "vhs", "hd-dvd", "umd", "8-track", "minidisc", "betamax", "laserdisc")


def is_non_square_format(fmt: str | None) -> bool:
    f = (fmt or "").lower()
    return any(k in f for k in NON_SQUARE_FORMATS)


def limits_from_settings(settings, art_type: ArtType, release_format: str = "") -> ResolutionLimits:
    """Build search limits for one art type from a ``Settings`` object.

    For releases whose front cover is not square (cassettes, DVDs, ...), the square rule is replaced by
    a wide aspect range and the minimum applies to the longer side, so those covers are not rejected.
    """
    mn = settings.min_side_for(art_type)
    mx = int(settings["max_side"] or 0)
    lim = ResolutionLimits(
        min_width=mn,
        min_height=mn if art_type in (ArtType.FRONT, ArtType.DISC, ArtType.ARTIST_THUMB) else 0,
        max_width=mx,
        max_height=mx,
        max_bytes=int(settings["max_file_mb"] or 0) * 1024 * 1024,
        target_size=int(settings["target_size"] or 1200),
        prefer_largest=bool(settings["prefer_largest"]),
    )
    if art_type == ArtType.FRONT and is_non_square_format(release_format):
        lim.aspect_min, lim.aspect_max = 0.4, 1.6
        lim.min_width = lim.min_height = 0
        lim.min_long_side = mn
    elif art_type in (ArtType.FRONT, ArtType.DISC):
        tol = float(settings["front_aspect_tolerance"] or 0.05)
        lim.aspect_min, lim.aspect_max = 1.0 - tol, 1.0 + tol
    elif art_type in (ArtType.BACK, ArtType.BOOKLET):
        lim.aspect_min, lim.aspect_max = 0.4, 2.5
        # back covers and scans are often wider or taller than square: apply the minimum to the longer side
        lim.min_width = lim.min_height = 0
        lim.min_long_side = mn
    return lim


def limits_for(art_type: ArtType, overrides: dict[ArtType, ResolutionLimits] | None = None) -> ResolutionLimits:
    if overrides and art_type in overrides:
        return overrides[art_type]
    return DEFAULT_LIMITS.get(art_type, ResolutionLimits())


def check_limits(c: Candidate, lim: ResolutionLimits) -> str | None:
    """Return a human-readable rejection reason, or None if the candidate is eligible.

    Candidates whose dimensions are still unknown (0x0) are not rejected on size here: the pipeline
    measures them first, and the apply step re-checks real decoded sizes.
    """
    if not (c.width and c.height):
        if lim.max_bytes and c.bytes > lim.max_bytes:
            return f"File too large: {c.bytes} bytes > {lim.max_bytes}"
        return None
    if lim.min_width and c.width < lim.min_width or lim.min_height and c.height < lim.min_height:
        return f"Too small: {c.width}x{c.height} < {lim.min_width}x{lim.min_height}"
    if lim.min_long_side and max(c.width, c.height) < lim.min_long_side:
        return f"Too small: {c.width}x{c.height}, longer side < {lim.min_long_side}"
    if lim.max_width and c.width > lim.max_width or lim.max_height and c.height > lim.max_height:
        return f"Too large: {c.width}x{c.height} > {lim.max_width}x{lim.max_height}"
    if lim.min_bytes and c.bytes and c.bytes < lim.min_bytes:
        return f"File too small: {c.bytes} bytes < {lim.min_bytes}"
    if lim.max_bytes and c.bytes > lim.max_bytes:
        return f"File too large: {c.bytes} bytes > {lim.max_bytes}"
    if c.height and (lim.aspect_min or lim.aspect_max):
        a = c.aspect
        if lim.aspect_min and a < lim.aspect_min or lim.aspect_max and a > lim.aspect_max:
            return f"Aspect ratio {a:.2f} outside {lim.aspect_min:.2f}-{lim.aspect_max:.2f}"
    return None


DEFAULT_WEIGHTS = {
    "anchor": 40.0,
    "consensus": 20.0,
    "resolution": 15.0,
    "aspect": 10.0,
    "reliability": 10.0,
    "quality": 5.0,
}

ANCHOR_VALUE = {Tier.A: 1.0, Tier.B: 0.6, Tier.C: 0.2, Tier.G: 0.0}

DEFAULT_RELIABILITY = {
    "caa": 1.0,
    "discogs": 0.9,
    "fanart": 0.8,
    "audiodb": 0.7,
    "itunes": 0.7,
    "deezer": 0.6,
    "lastfm": 0.5,
    "web": 0.3,
}


def _resolution_fit(c: Candidate, lim: ResolutionLimits) -> float:
    side = max(c.width, c.height)
    if side <= 0:
        return 0.3  # size unknown
    if lim.prefer_largest:
        return min(1.0, side / 3000.0)
    target = max(1, lim.target_size)
    if side >= target:
        # mild penalty for being far above the useful size
        return max(0.6, 1.0 - 0.1 * (side / target - 1.0))
    return side / target


def _aspect_fit(c: Candidate, art_type: ArtType) -> float:
    if not c.height:
        return 0.5  # size unknown
    if art_type in (ArtType.FRONT, ArtType.DISC):
        return max(0.0, 1.0 - abs(c.aspect - 1.0) * 4.0)
    return 1.0


def score_candidate(
    c: Candidate,
    lim: ResolutionLimits,
    cluster_sources: int = 1,
    weights: dict[str, float] | None = None,
    reliability: dict[str, float] | None = None,
) -> float:
    """Score 0-100. Fills ``c.breakdown`` and ``c.score``."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    rel = {**DEFAULT_RELIABILITY, **(reliability or {})}
    parts = {
        "anchor": w["anchor"] * ANCHOR_VALUE[c.tier],
        "consensus": w["consensus"] * min(max(cluster_sources, 1), 4) / 4.0,
        "resolution": w["resolution"] * _resolution_fit(c, lim),
        "aspect": w["aspect"] * _aspect_fit(c, c.art_type),
        "reliability": w["reliability"] * rel.get(c.provider, 0.5),
        "quality": w["quality"] * (0.0 if c.blank else 1.0) * max(0.0, min(1.0, c.type_confidence)),
    }
    if c.blank:
        parts["placeholder_penalty"] = -30.0
    c.breakdown = parts
    c.score = max(0.0, min(100.0, sum(parts.values())))
    return c.score


@dataclass
class Selection:
    winner: Candidate | None = None
    conflict: bool = False  # tiers disagree: do not auto-apply
    auto_apply: bool = False
    rationale: str = ""
    clusters: list[Cluster] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)


def select_best(
    candidates: list[Candidate],
    art_type: ArtType,
    limits: ResolutionLimits | None = None,
    hamming_threshold: int = 10,
    conflict_min_sources: int = 3,
    weights: dict[str, float] | None = None,
    reliability: dict[str, float] | None = None,
) -> Selection:
    """Pick the best candidate of one art type using the tiered, consensus-aware rules.

    1. Apply the search limits (rejections are kept with their reason).
    2. Cluster survivors by visual similarity.
    3. Prefer a cluster with a Tier A anchor, else Tier B, else the cluster with the most distinct
       providers (largest image as tie-break). Generated (tier G) art is never chosen here.
    4. If an anchored cluster and a large unanchored consensus cluster disagree, flag a conflict and
       do not auto-apply.
    """
    lim = limits or limits_for(art_type)
    sel = Selection()
    pool = [c for c in candidates if c.art_type == art_type and not c.generated]
    eligible: list[Candidate] = []
    for c in pool:
        reason = check_limits(c, lim)
        c.rejected_reason = reason
        if reason:
            sel.rejected.append(c)
        else:
            eligible.append(c)
    if not eligible:
        sel.rationale = "No eligible candidates" + (f" ({len(sel.rejected)} rejected by limits)" if sel.rejected else "")
        return sel

    clusters = cluster_candidates(eligible, hamming_threshold)
    sel.clusters = clusters
    for cl in clusters:
        n = len(cl.providers)
        for m in cl.members:
            score_candidate(m, lim, n, weights, reliability)

    def cluster_key(cl: Cluster):
        return (cl.best_tier.rank, len(cl.providers), cl.best_pixels)

    ranked = sorted(clusters, key=cluster_key, reverse=True)
    top = ranked[0]
    # strongest unanchored consensus cluster (tier C) that differs from the winner
    rival = next(
        (cl for cl in ranked[1:] if cl.best_tier == Tier.C and len(cl.providers) >= conflict_min_sources),
        None,
    )
    sel.winner = max(top.members, key=lambda m: (m.score, m.width * m.height))
    anchored = top.best_tier in (Tier.A, Tier.B)
    if anchored and rival is not None:
        sel.conflict = True
        sel.auto_apply = False
        sel.rationale = (
            f"Conflict: tier {top.best_tier.value} art ({', '.join(sorted(top.providers))}) disagrees with a "
            f"visual consensus of {len(rival.providers)} sources ({', '.join(sorted(rival.providers))}); "
            "needs review"
        )
    else:
        sel.auto_apply = True
        if anchored:
            sel.rationale = (
                f"Chose tier {top.best_tier.value} art from {sel.winner.provider}; "
                f"{len(top.providers)} source(s) agree"
            )
        else:
            sel.rationale = (
                f"No exact-release art; chose the largest visual-agreement cluster "
                f"({len(top.providers)} sources: {', '.join(sorted(top.providers))})"
            )
    return sel
