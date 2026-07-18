"""Candidate scoring (blueprint 15).

    final_score = base_weight
                x pack_multiplier
                x user_category_multiplier
                + preference_bonuses
                - avoidance_penalties

Hard conflicts are removed from the pool by rules.py, never down-weighted, so
nothing here can resurrect a candidate that a hard rule rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .constants import (
    CompatibilityMode,
    ContentTypeSpec,
    MINIMUM_SCORE,
)
from .models import Entry
from .rules import MatchContext, inbound_soft_rules, matches, parse_rule_block

# Blueprint 14: Strict uses "strong" preference scoring, Balanced "standard".
# Loose widens the pool, so it steers more weakly and lets variety through.
PREFERENCE_STRENGTH: dict[CompatibilityMode, float] = {
    CompatibilityMode.STRICT: 1.5,
    CompatibilityMode.BALANCED: 1.0,
    CompatibilityMode.LOOSE: 0.75,
}


@dataclass
class ScoredCandidate:
    entry: Entry
    score: float
    base_weight: float = 0.0
    pack_multiplier: float = 1.0
    category_multiplier: float = 1.0
    bonus: float = 0.0
    penalty: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.entry.id


def preference_bonus(
    candidate: Entry, context: MatchContext, strength: float
) -> tuple[float, list[str]]:
    total = 0.0
    reasons: list[str] = []
    for rule in parse_rule_block(candidate.prefers):
        if not context.is_resolved(rule.key):
            continue
        if matches(rule, context.get(rule.key)):
            total += rule.bonus * strength
            reasons.append(f"+{rule.bonus * strength:.2f} prefers.{rule.raw_key}")
    return total, reasons


def avoidance_penalty(
    candidate: Entry, context: MatchContext, strength: float
) -> tuple[float, list[str]]:
    total = 0.0
    reasons: list[str] = []
    for rule in parse_rule_block(candidate.avoids):
        if not context.is_resolved(rule.key):
            continue
        if matches(rule, context.get(rule.key)):
            total += rule.penalty * strength
            reasons.append(f"-{rule.penalty * strength:.2f} avoids.{rule.raw_key}")
    return total, reasons


def inbound_adjustments(
    candidate: Entry,
    spec: ContentTypeSpec,
    parents: Sequence[Entry],
    strength: float,
) -> tuple[float, float, list[str]]:
    """Soft rules aimed at this candidate by entries already selected.

    This is what makes "the chosen action prefers standing poses" actually raise
    the score of standing poses.
    """
    preferred, avoided = inbound_soft_rules(candidate, spec, parents)
    bonus = 0.0
    penalty = 0.0
    reasons: list[str] = []
    for parent, rule in preferred:
        bonus += rule.bonus * strength
        reasons.append(f"+{rule.bonus * strength:.2f} {parent.id} prefers.{rule.raw_key}")
    for parent, rule in avoided:
        penalty += rule.penalty * strength
        reasons.append(f"-{rule.penalty * strength:.2f} {parent.id} avoids.{rule.raw_key}")
    return bonus, penalty, reasons


def score_candidate(
    candidate: Entry,
    context: MatchContext,
    mode: CompatibilityMode,
    *,
    spec: ContentTypeSpec | None = None,
    parents: Sequence[Entry] = (),
    pack_multiplier: float = 1.0,
    category_multiplier: float = 1.0,
    extra_penalty: float = 0.0,
    ignore_preferences: bool = False,
    ignore_avoidance: bool = False,
) -> ScoredCandidate:
    """Score one candidate, in both rule directions.

    Outbound: the candidate's own prefers/avoids, matched against the context.
    Inbound: prefers/avoids that already-selected entries aim at this type.

    `ignore_preferences` and `ignore_avoidance` exist for fallback ladder steps 2
    and 3 (blueprint 20), which relax soft scoring before touching hard rules.
    `extra_penalty` carries a Loose-mode demoted exclusion across from the rule
    engine.
    """
    strength = PREFERENCE_STRENGTH[mode]

    bonus, bonus_reasons = (
        (0.0, []) if ignore_preferences else preference_bonus(candidate, context, strength)
    )
    penalty, penalty_reasons = (
        (0.0, []) if ignore_avoidance else avoidance_penalty(candidate, context, strength)
    )

    if spec is not None and parents:
        in_bonus, in_penalty, in_reasons = inbound_adjustments(
            candidate, spec, parents, strength
        )
        if not ignore_preferences:
            bonus += in_bonus
            bonus_reasons += [r for r in in_reasons if r.startswith("+")]
        if not ignore_avoidance:
            penalty += in_penalty
            penalty_reasons += [r for r in in_reasons if r.startswith("-")]

    penalty += extra_penalty

    base = candidate.weight * pack_multiplier * category_multiplier
    score = max(base + bonus - penalty, MINIMUM_SCORE)

    return ScoredCandidate(
        entry=candidate,
        score=score,
        base_weight=candidate.weight,
        pack_multiplier=pack_multiplier,
        category_multiplier=category_multiplier,
        bonus=bonus,
        penalty=penalty,
        reasons=bonus_reasons + penalty_reasons,
    )


def score_all(
    candidates: list[Entry],
    context: MatchContext,
    mode: CompatibilityMode,
    *,
    spec: ContentTypeSpec | None = None,
    parents: Sequence[Entry] = (),
    pack_multipliers: dict[str, float] | None = None,
    extra_penalties: dict[str, float] | None = None,
    ignore_preferences: bool = False,
    ignore_avoidance: bool = False,
) -> list[ScoredCandidate]:
    """Score a pool, preserving the caller's (id-sorted) order.

    Order matters: the deterministic weighted choice in seeds.py walks this list,
    so it must not depend on dict iteration or filesystem order.
    """
    multipliers = pack_multipliers or {}
    penalties = extra_penalties or {}
    return [
        score_candidate(
            candidate,
            context,
            mode,
            spec=spec,
            parents=parents,
            pack_multiplier=multipliers.get(candidate.pack_id, 1.0),
            extra_penalty=penalties.get(candidate.id, 0.0),
            ignore_preferences=ignore_preferences,
            ignore_avoidance=ignore_avoidance,
        )
        for candidate in candidates
    ]
