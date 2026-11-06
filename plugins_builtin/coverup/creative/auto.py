# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Unattended creative fallback (used by "Auto-apply"): try the user's method order for the front cover."""

from __future__ import annotations

import logging

from ..model import ArtType, Candidate
from .generate import ConsentRequired, CreativeOptions, generate, resolve_template

log = logging.getLogger("coverup.creative")


def pick_logo(res, settings) -> Candidate | None:
    if not res.logos:
        return None
    chosen = settings.logo_choice(res.query.artist_mbid)
    for c in res.logos:
        if chosen and c.url == chosen:
            return c
    return res.logos[0]


def auto_creative(pipeline, res, settings, template_dirs=()) -> tuple[Candidate | None, str]:
    """Return (generated or fan-art candidate, explanation). Never raises."""
    go, why = res.creative.get(ArtType.FRONT, (False, ""))
    if not go:
        return None, ""
    notes = []
    for method in settings["creative_method_order"] or ["template"]:
        try:
            if method == "fanart":
                fan = [c for c in res.of_type(ArtType.FRONT) if c.provider == "fanart" and not c.rejected_reason]
                if fan:
                    best = max(fan, key=lambda c: (c.likes, c.score))
                    pipeline.fetch_full(best)
                    return best, f"{why}; used fan art from fanart.tv"
                notes.append("no fan art")
            elif method in ("ai", "template"):
                if method == "ai" and settings["ai_backend"] == "none":
                    notes.append("no AI backend configured")
                    continue
                logo = pick_logo(res, settings) if settings["creative_overlay_logo"] else None
                opts = CreativeOptions(
                    method=method, template=resolve_template(settings, template_dirs), count=1,
                    show_logo=bool(settings["creative_overlay_logo"]), show_title=bool(settings["creative_overlay_title"]),
                    position=settings["creative_logo_position"],
                )
                if logo is not None:
                    opts.logo_data = logo.data or pipeline.fetch_full(logo)
                if method == "ai" and settings["ai_img2img"]:
                    win = res.selections.get(ArtType.FRONT)
                    if win and win.winner is not None:
                        opts.reference = win.winner.data or win.winner.thumb_data
                made = generate(settings, pipeline.http, res.query, opts)
                if made:
                    return made[0], f"{why}; generated ({made[0].extra.get('method')}, style {opts.template.name})"
        except ConsentRequired as exc:
            notes.append(f"AI service {exc.host} not yet approved (generate once from the dialog to approve)")
        except Exception as exc:  # creative art must never break a batch
            log.warning("Creative method %s failed: %s", method, exc)
            notes.append(f"{method} failed: {exc}")
    return None, f"{why}; creative fallback produced nothing ({'; '.join(notes)})"
