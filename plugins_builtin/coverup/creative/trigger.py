# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Fallback-trigger evaluation for creative/AI art (design section 3.6). Pure logic, no generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Flag, auto

from ..model import ArtType


class Trigger(Flag):
    NEVER = 0
    NO_COVER = auto()
    BELOW_MIN_RESOLUTION = auto()
    LOW_CONFIDENCE = auto()


@dataclass
class CreativeSettings:
    triggers: Trigger = Trigger.NEVER
    always_generate: bool = False  # "Always use generated art", even when real art exists
    art_types: tuple[ArtType, ...] = (ArtType.FRONT,)  # which art types may be generated
    apply_min_side: int = 500  # "minimum to apply": best art below this counts as too low-res


@dataclass
class Situation:
    """What the matching stage found for one art type."""

    art_type: ArtType
    found: bool = False  # any eligible real candidate
    best_side: int = 0  # longest side of the best real candidate
    conflict_or_tier_c_only: bool = False


def should_generate(s: Situation, cfg: CreativeSettings) -> tuple[bool, str]:
    """Decide whether to run the creative methods for this art type, with a reason for the log."""
    if s.art_type not in cfg.art_types:
        return False, f"{s.art_type.value} is not enabled for creative art"
    if cfg.always_generate:
        return True, "Always use generated art is on"
    if Trigger.NO_COVER in cfg.triggers and not s.found:
        return True, "No cover found"
    if Trigger.BELOW_MIN_RESOLUTION in cfg.triggers and s.found and s.best_side < cfg.apply_min_side:
        return True, f"Best cover is {s.best_side}px, below the {cfg.apply_min_side}px minimum"
    if Trigger.LOW_CONFIDENCE in cfg.triggers and s.found and s.conflict_or_tier_c_only:
        return True, "Only conflicting or unanchored art was found"
    return False, "No fallback trigger fired" if cfg.triggers else "Creative art is off"
