# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Visual-similarity clustering (union-find over perceptual hashes)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Candidate, Tier
from .phash import hamming

DEFAULT_THRESHOLD = 10  # max Hamming distance (of 64 bits) to treat two images as the same artwork


@dataclass
class Cluster:
    members: list[Candidate] = field(default_factory=list)

    @property
    def providers(self) -> set[str]:
        return {m.provider for m in self.members}

    @property
    def best_tier(self) -> Tier:
        return max((m.tier for m in self.members), key=lambda t: t.rank)

    @property
    def best_pixels(self) -> int:
        return max((m.width * m.height for m in self.members), default=0)


def cluster_candidates(candidates: list[Candidate], threshold: int = DEFAULT_THRESHOLD) -> list[Cluster]:
    """Group candidates whose hashes are within ``threshold`` bits (single linkage).

    Candidates without a hash form their own singleton cluster.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        if candidates[i].phash is None:
            continue
        for j in range(i + 1, n):
            if candidates[j].phash is None:
                continue
            if hamming(candidates[i].phash, candidates[j].phash) <= threshold:
                union(i, j)

    groups: dict[int, Cluster] = {}
    for i, cand in enumerate(candidates):
        groups.setdefault(find(i), Cluster()).members.append(cand)
    return list(groups.values())
