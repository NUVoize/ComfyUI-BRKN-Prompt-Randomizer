"""Rule parsing and evaluation (blueprint 12-14).

Two evaluation directions, and keeping them straight is the whole trick:

* Candidate-side rules -- `requires`, `excludes`, `prefers`, `avoids` live on the
  candidate and are matched against the *context* built from what is already
  resolved.
* Parent allowlists -- `allows` lives on an already-selected entry and is matched
  against the *candidate*. A location that allows kitchen actions is restricting
  its children, not describing itself.

Unresolved context keys are the sharp edge here. An action resolves before an
outfit, so an action rule that mentions `outfit_tags` has nothing to match
against. Decision B4 governs that case, and it is implemented in `_evaluate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .constants import (
    CompatibilityMode,
    ContentTypeSpec,
    DEFAULT_AVOIDANCE_PENALTY,
    DEFAULT_PREFERENCE_BONUS,
    LOOSE_DEMOTED_EXCLUSION_PENALTY,
    MATCH_SUFFIXES,
    RULE_KEYS,
    TYPES_BY_SECTION,
)
from .models import Entry, RuleBlock

# Keys that express parent-child structure. Blueprint 14: these are never
# loosened, in any compatibility mode.
STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {"environment_ids", "parent_environment_ids", "environment_tags"}
)


class MatchMode(str, Enum):
    ANY = "any"
    ALL = "all"
    NONE = "none"


@dataclass(frozen=True)
class Rule:
    """One normalised rule. Both authoring shapes collapse to this (decision 3)."""

    key: str
    mode: MatchMode
    values: frozenset[str]
    bonus: float = DEFAULT_PREFERENCE_BONUS
    penalty: float = DEFAULT_AVOIDANCE_PENALTY
    raw_key: str = ""

    @property
    def is_structural(self) -> bool:
        return self.key in STRUCTURAL_KEYS


@dataclass(frozen=True)
class MatchContext:
    """What is currently known, keyed by rule vocabulary.

    `resolved` is deliberately separate from `values`: a section that resolved to
    nothing (no props selected) is not the same as a section that has not been
    reached yet, and the two must behave differently under Strict.
    """

    values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    resolved: frozenset[str] = frozenset()

    def get(self, key: str) -> frozenset[str]:
        return self.values.get(key, frozenset())

    def is_resolved(self, key: str) -> bool:
        return key in self.resolved


@dataclass
class RuleVerdict:
    """Outcome of applying every hard rule to one candidate."""

    accepted: bool = True
    demoted_penalty: float = 0.0
    reasons: list[str] = field(default_factory=list)
    skipped_keys: list[str] = field(default_factory=list)
    """Rule keys that could not be evaluated because their section is unresolved."""

    def reject(self, reason: str) -> None:
        self.accepted = False
        self.reasons.append(reason)

    def demote(self, reason: str, penalty: float) -> None:
        self.demoted_penalty += penalty
        self.reasons.append(f"{reason} (demoted to penalty in loose mode)")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def split_rule_key(raw_key: str) -> tuple[str, MatchMode]:
    """`location_tags_any` -> ("location_tags", ANY). No suffix defaults to ANY."""
    for suffix in MATCH_SUFFIXES:
        if raw_key.endswith(suffix):
            base = raw_key[: -len(suffix)]
            if base in RULE_KEYS:
                return base, MatchMode(suffix[1:])
    return raw_key, MatchMode.ANY


def rule_values(raw_value: Any) -> list[str]:
    """Rule values are a bare list or an object wrapper. Both are accepted."""
    if isinstance(raw_value, list):
        return [v for v in raw_value if isinstance(v, str)]
    if isinstance(raw_value, Mapping):
        values = raw_value.get("values", [])
        if isinstance(values, list):
            return [v for v in values if isinstance(v, str)]
    return []


def parse_rule_block(block: RuleBlock) -> tuple[Rule, ...]:
    """Normalise one rule block. Unknown keys are dropped; the registry has
    already reported them as warnings, so silently ignoring them here keeps a
    typo from behaving like a filter that matches nothing."""
    rules: list[Rule] = []
    for raw_key in sorted(block):  # sorted: rule order must not vary
        raw_value = block[raw_key]
        key, mode = split_rule_key(raw_key)
        if key not in RULE_KEYS:
            continue
        values = rule_values(raw_value)
        if not values:
            continue
        bonus = DEFAULT_PREFERENCE_BONUS
        penalty = DEFAULT_AVOIDANCE_PENALTY
        if isinstance(raw_value, Mapping):
            bonus = float(raw_value.get("bonus", DEFAULT_PREFERENCE_BONUS))
            penalty = float(raw_value.get("penalty", DEFAULT_AVOIDANCE_PENALTY))
        rules.append(
            Rule(
                key=key,
                mode=mode,
                values=frozenset(values),
                bonus=bonus,
                penalty=penalty,
                raw_key=raw_key,
            )
        )
    return tuple(rules)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def matches(rule: Rule, available: frozenset[str]) -> bool:
    """Match semantics (blueprint 12, decision M4)."""
    if rule.mode is MatchMode.ANY:
        return bool(rule.values & available)
    if rule.mode is MatchMode.ALL:
        return rule.values <= available
    return not (rule.values & available)  # NONE


def _evaluate(rule: Rule, context: MatchContext, mode: CompatibilityMode) -> bool:
    """Evaluate one rule, handling the unresolved-key case.

    Supersedes decision B4, which rejected an unresolved `requires` under Strict.

    A rule against an unresolved key is *skipped*, in every mode. The reading is
    conditional, not existential: `requires: {environment_tags_any: [home]}` means
    "if there is an environment, it must be home", not "there must be an
    environment". The alternative reading is unusable, because it means a section
    set to `none` -- or simply absent from the pack -- empties every child pool
    beneath it, and the fallback ladder then hides the wreckage.

    A skip is recorded, not swallowed: the caller warns (PCR401), because a gate
    you authored quietly not firing is exactly the kind of thing you need told.
    """
    if not context.is_resolved(rule.key):
        return True
    return matches(rule, context.get(rule.key))


# --------------------------------------------------------------------------
# Hard rules
# --------------------------------------------------------------------------


def evaluate_hard_rules(
    candidate: Entry,
    context: MatchContext,
    mode: CompatibilityMode,
) -> RuleVerdict:
    """Apply `requires` and `excludes`.

    `requires` stays hard in every mode. It is how parent-child filtering is
    expressed (an action requires its location's tag), and blueprint 14 forbids
    loosening parent-child relationships. Only `excludes` on non-structural keys
    softens into a penalty under Loose.
    """
    verdict = RuleVerdict()

    for rule in parse_rule_block(candidate.requires):
        if not context.is_resolved(rule.key):
            verdict.skipped_keys.append(f"requires.{rule.raw_key}")
            continue
        if not _evaluate(rule, context, mode):
            verdict.reject(f"requires.{rule.raw_key} not satisfied")

    for rule in parse_rule_block(candidate.excludes):
        # An exclusion fires when its rule *matches* the context.
        if not context.is_resolved(rule.key):
            continue
        if not matches(rule, context.get(rule.key)):
            continue
        if mode is CompatibilityMode.LOOSE and not rule.is_structural:
            verdict.demote(
                f"excludes.{rule.raw_key} matched", LOOSE_DEMOTED_EXCLUSION_PENALTY
            )
        else:
            verdict.reject(f"excludes.{rule.raw_key} matched")

    return verdict


def candidate_values(candidate: Entry, spec: ContentTypeSpec) -> dict[str, frozenset[str]]:
    """The candidate expressed in rule vocabulary, for rules aimed *at* it."""
    values: dict[str, frozenset[str]] = {}
    if spec.id_key:
        values[spec.id_key] = frozenset({candidate.id})
    if spec.tag_key:
        values[spec.tag_key] = candidate.tag_set()
    return values


def evaluate_allowlists(
    candidate: Entry,
    spec: ContentTypeSpec,
    parents: Iterable[Entry],
) -> RuleVerdict:
    """Apply every selected entry's `allows` block to this candidate.

    Allowlists are parent-child structure, so they are enforced in all three
    modes -- including Loose (blueprint 14: parent-child is never loosened).
    """
    verdict = RuleVerdict()
    values = candidate_values(candidate, spec)
    if not values:
        return verdict

    for parent in parents:
        for rule in parse_rule_block(parent.allows):
            if rule.key not in values:
                continue  # this allowlist targets a different child type
            if not matches(rule, values[rule.key]):
                verdict.reject(
                    f"{parent.id} allows.{rule.raw_key} does not admit this candidate"
                )
    return verdict


def inbound_soft_rules(
    candidate: Entry,
    spec: ContentTypeSpec,
    parents: Iterable[Entry],
) -> tuple[list[tuple[Entry, Rule]], list[tuple[Entry, Rule]]]:
    """Soft rules that already-selected entries aim at this candidate's type.

    Blueprint 8: "Action prefers Poses and Props", "Style influences Camera,
    Lighting...". Those relationships point *forward* in the resolution order, so
    they can only fire when the child is being scored and the parent is already
    chosen. Evaluating `prefers`/`avoids` candidate-side only would leave every
    such rule permanently inert -- including the blueprint's own section 28
    example.
    """
    values = candidate_values(candidate, spec)
    preferred: list[tuple[Entry, Rule]] = []
    avoided: list[tuple[Entry, Rule]] = []
    if not values:
        return preferred, avoided

    for parent in parents:
        for rule in parse_rule_block(parent.prefers):
            if rule.key in values and matches(rule, values[rule.key]):
                preferred.append((parent, rule))
        for rule in parse_rule_block(parent.avoids):
            if rule.key in values and matches(rule, values[rule.key]):
                avoided.append((parent, rule))
    return preferred, avoided


def evaluate_user_exclusions(
    candidate: Entry,
    excluded_tags: frozenset[str],
    excluded_ids: frozenset[str],
) -> RuleVerdict:
    """User exclusions are absolute. Never softened, in any mode (blueprint 14)."""
    verdict = RuleVerdict()
    if candidate.id in excluded_ids:
        verdict.reject("excluded by the user")
    hit = candidate.tag_set() & excluded_tags
    if hit:
        verdict.reject("excluded by user tag: " + ", ".join(sorted(hit)))
    return verdict


# --------------------------------------------------------------------------
# Context construction
# --------------------------------------------------------------------------

# Global keys are read out of the selected entry's metadata rather than its tags.
_GLOBAL_KEY_SOURCES: dict[str, tuple[str, str]] = {
    "visual_categories": ("style", "visual_category"),
    "fashion_categories": ("fashion", "fashion_category"),
    "environment_categories": ("environment", "environment_category"),
    "action_families": ("action", "action_family"),
}


def build_match_context(
    selections: Mapping[str, Entry | Sequence[Entry] | None],
    condition_tags: Mapping[str, Iterable[str]] | None = None,
) -> MatchContext:
    """Turn the current selections into rule-vocabulary values.

    Driven entirely by the content-type registry, so a new content type feeds the
    rule engine the moment its row exists -- no edits here.
    """
    values: dict[str, frozenset[str]] = {}
    resolved: set[str] = set()

    for section, spec in TYPES_BY_SECTION.items():
        if section not in selections:
            continue
        picked = selections[section]
        entries: list[Entry]
        if picked is None:
            entries = []
        elif isinstance(picked, Entry):
            entries = [picked]
        else:
            entries = list(picked)

        if spec.id_key:
            values[spec.id_key] = frozenset(e.id for e in entries)
            resolved.add(spec.id_key)
        if spec.tag_key:
            tags: set[str] = set()
            for entry in entries:
                tags |= entry.tag_set()
            values[spec.tag_key] = frozenset(tags)
            resolved.add(spec.tag_key)

    # Locations address their parent through either spelling.
    if "environment_ids" in values:
        values["parent_environment_ids"] = values["environment_ids"]
        resolved.add("parent_environment_ids")

    for global_key, (section, meta_field) in _GLOBAL_KEY_SOURCES.items():
        if section not in selections or selections[section] is None:
            continue
        picked = selections[section]
        entries = [picked] if isinstance(picked, Entry) else list(picked)
        found = {
            str(e.metadata[meta_field]) for e in entries if meta_field in e.metadata
        }
        values[global_key] = frozenset(found)
        resolved.add(global_key)

    for key, tags in (condition_tags or {}).items():
        values[key] = frozenset(tags)
        resolved.add(key)

    return MatchContext(values=values, resolved=frozenset(resolved))
