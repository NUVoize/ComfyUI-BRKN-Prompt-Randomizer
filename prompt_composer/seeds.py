"""Deterministic seeding (blueprint 16, decision 8).

The whole determinism guarantee reduces to two frozen contracts:

1. Sub-seed:  sha256(f"{global_seed}|{section}|{revision}") -> first 8 bytes,
   big-endian, unsigned. Python's built-in hash() is salted per process and is
   never used.
2. Choice:    candidates arrive sorted by id, scores are summed in that order,
   and the walk lands on target = (sub_seed / 2**64) * total_score.

Both must stay byte-stable across machines, Python versions and pack load order.
Change either and every saved workflow silently produces different images.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from .errors import ResolutionError
from .scoring import ScoredCandidate

U64_RANGE: int = 2**64
_SEPARATOR = "|"


def stable_u64(*parts: object) -> int:
    """SHA-256 of the joined parts; first 8 bytes as an unsigned 64-bit integer."""
    payload = _SEPARATOR.join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def section_seed(global_seed: int, section: str, revision: int = 0) -> int:
    """Sub-seed for one section. Rerolling a section changes only its revision."""
    return stable_u64(global_seed, section, revision)


def index_seed(section_seed_value: int, index: int) -> int:
    """Sub-seed for the nth pick within a multi-select section (props)."""
    return stable_u64(section_seed_value, "index", index)


def uniform(seed: int) -> float:
    """Map a 64-bit seed into [0, 1)."""
    return seed / U64_RANGE


def weighted_choice(
    candidates: Sequence[ScoredCandidate],
    seed: int,
) -> ScoredCandidate:
    """Deterministic weighted selection.

    Callers must pass an id-sorted sequence: the cumulative walk depends on it,
    so a differently ordered pool would select a different entry from the same
    seed. registry.get_enabled_entries and scoring.score_all both preserve it.
    """
    if not candidates:
        raise ResolutionError("weighted_choice called with an empty candidate pool")

    total = 0.0
    for candidate in candidates:
        total += candidate.score

    if total <= 0.0:  # every score floored away; fall back to a flat draw
        return candidates[seed % len(candidates)]

    target = uniform(seed) * total
    cumulative = 0.0
    for candidate in candidates:
        cumulative += candidate.score
        if target < cumulative:
            return candidate

    return candidates[-1]  # float drift only; the walk is otherwise exhaustive


def weighted_sample(
    candidates: Sequence[ScoredCandidate],
    seed: int,
    count: int,
) -> list[ScoredCandidate]:
    """Pick `count` distinct candidates, deterministically, without replacement."""
    pool = list(candidates)
    picked: list[ScoredCandidate] = []
    for index in range(min(count, len(pool))):
        chosen = weighted_choice(pool, index_seed(seed, index))
        picked.append(chosen)
        pool = [c for c in pool if c.id != chosen.id]
    return picked


def choice_in_range(seed: int, low: int, high: int) -> int:
    """Deterministic integer in [low, high]."""
    if high <= low:
        return low
    return low + (seed % (high - low + 1))


def pick_option(seed: int, options: Sequence[str]) -> str:
    """Deterministic pick from a flat, unweighted vocabulary (time, weather...)."""
    if not options:
        return ""
    return options[seed % len(options)]
