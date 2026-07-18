"""The resolver (blueprint 17, 19, 20, 29).

Resolution walks the content-type registry in `order`, so adding a content type
inserts it into the pipeline automatically. Each section:

    filter (user exclusions -> hard rules -> parent allowlists)
      -> score (outbound + inbound soft rules)
      -> deterministic weighted choice on the section sub-seed

A locked section skips selection but still runs the filters, purely so that a
conflict can be *reported*. The lock is never replaced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .constants import (
    CHARACTER_FACETS,
    CONDITIONS_ORDER,
    CONDITIONS_SECTION,
    CompatibilityMode,
    ContentTypeSpec,
    FALLBACK_ENABLED_MODES,
    MAX_FALLBACK_ATTEMPTS,
    NONE_OPTION,
    PROP_COUNT_BY_LENGTH,
    RANDOM_OPTION,
    RESOLUTION_ORDER,
    SEASON_OPTIONS,
    TIME_OPTIONS,
    WEATHER_OPTIONS,
)
from .context import CONDITION_KEYS, ResolutionContext, SectionResult, Settings
from .errors import Diagnostic, ErrorCode, Severity
from .health import find_dead_gates
from .models import Entry
from .registry import ContentRegistry
from .rules import (
    evaluate_allowlists,
    evaluate_hard_rules,
    evaluate_user_exclusions,
)
from .scoring import ScoredCandidate, score_all
from .seeds import choice_in_range, index_seed, pick_option, weighted_choice, weighted_sample


@dataclass
class Resolution:
    """Everything the renderer and the node need."""

    settings: Settings
    context: ResolutionContext
    duration_ms: float = 0.0

    @property
    def selections(self) -> dict[str, list[Entry]]:
        return self.context.selections

    @property
    def conditions(self) -> dict[str, str]:
        return self.context.conditions

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return self.context.diagnostics

    def entry(self, section: str) -> Entry | None:
        return self.context.selected(section)

    def entries(self, section: str) -> list[Entry]:
        return self.context.selections.get(section, [])

    def selected_ids(self) -> dict[str, list[str]]:
        return {
            section: [e.id for e in entries]
            for section, entries in self.context.selections.items()
        }

    def unresolved_sections(self) -> list[str]:
        return [
            section
            for section, result in self.context.results.items()
            if not result.resolved
        ]


@dataclass
class _Pool:
    """A filtered candidate pool plus the penalties Loose mode demoted into it."""

    candidates: list[Entry] = field(default_factory=list)
    extra_penalties: dict[str, float] = field(default_factory=dict)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    total: int = 0
    """Enabled entries of this type, before any filtering."""
    skipped_keys: set[str] = field(default_factory=set)
    """Hard-rule keys skipped because their section is unresolved."""


def resolve_prompt(
    registry: ContentRegistry,
    settings: Settings,
    previous: Mapping[str, Sequence[str]] | None = None,
) -> Resolution:
    """Resolve every section in order. Never raises on content problems."""
    started = time.perf_counter()
    context = ResolutionContext(settings=settings)
    previous_ids = {k: list(v) for k, v in (previous or {}).items()}

    # Seed-independent, so it is the same answer on every roll. Reported once,
    # up front, because a gate no roll can satisfy is a fact about the packs and
    # not about this generation.
    if settings.enabled_packs:
        unknown = sorted(settings.enabled_packs - registry.pack_ids())
        if unknown:
            context.warn(
                ErrorCode.PACK_LOAD,
                Severity.HIGH,
                f"enabled_packs names {', '.join(repr(u) for u in unknown)}, which "
                f"{'is' if len(unknown) == 1 else 'are'} not loaded. "
                "Nothing from "
                f"{'it' if len(unknown) == 1 else 'them'} can be selected.",
            )

    context.diagnostics.extend(
        find_dead_gates(registry, settings.enabled_packs, settings.skipped)
    )

    conditions_done = False
    for spec in RESOLUTION_ORDER:
        if not conditions_done and spec.order > CONDITIONS_ORDER:
            _resolve_conditions(context)
            conditions_done = True
        _resolve_section(registry, context, spec, previous_ids)

    if not conditions_done:
        _resolve_conditions(context)

    duration = (time.perf_counter() - started) * 1000.0
    return Resolution(settings=settings, context=context, duration_ms=duration)


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------


def _resolve_conditions(context: ResolutionContext) -> None:
    """Time, season and weather are settings, not entries (see constants.py)."""
    settings = context.settings
    seed = settings.section_seed(CONDITIONS_SECTION)

    vocabularies = {
        "time": TIME_OPTIONS,
        "season": SEASON_OPTIONS,
        "weather": WEATHER_OPTIONS,
    }

    for offset, key in enumerate(CONDITION_KEYS):
        chosen = getattr(settings, key)
        if chosen == NONE_OPTION or not chosen:
            context.conditions[key] = ""
        elif chosen == RANDOM_OPTION:
            context.conditions[key] = pick_option(
                index_seed(seed, offset), vocabularies[key]
            )
        else:
            context.conditions[key] = chosen

    context.results[CONDITIONS_SECTION] = SectionResult(
        section=CONDITIONS_SECTION,
        seed=seed,
        revision=settings.effective_revision(CONDITIONS_SECTION),
    )


# --------------------------------------------------------------------------
# One section
# --------------------------------------------------------------------------


def _resolve_section(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
    previous_ids: Mapping[str, Sequence[str]],
) -> None:
    section = spec.section or spec.type_name
    settings = context.settings
    seed = settings.section_seed(section)

    result = SectionResult(
        section=section,
        seed=seed,
        revision=settings.effective_revision(section),
    )
    context.results[section] = result

    # A self-contained character has already described her hair, her face and her
    # body. Rolling the facets on top would not add detail, it would add a second
    # person -- "long wavy blonde hair ... with jet black hair in a buzz cut".
    if section in CHARACTER_FACETS:
        chosen = context.selected("character")
        if chosen is not None and chosen.metadata.get("self_contained"):
            result.skipped = True
            context.absent.add(section)
            context.set_selection(section, [])
            return

    if settings.is_skipped(section):
        # Switched off. Nothing rolled, nothing written, and -- crucially -- the
        # section is absent rather than resolved-empty, so a child's rule that
        # points at it is skipped rather than violated. Otherwise setting
        # `environment` to none would empty every location pool beneath it.
        result.skipped = True
        context.absent.add(section)
        context.set_selection(section, [])
        return

    locked_id = settings.lock_for(section)
    if locked_id is not None:
        entry = _apply_lock(registry, context, spec, section, locked_id)
        if entry is not None:
            result.locked = True
            result.entries = [entry]
            result.candidate_count = 1
            context.set_selection(section, [entry])
            return
        # A missing or malformed lock cannot be preserved (blueprint 19); fall
        # through and resolve normally, having already warned.

    selected, step, pool, reasons = _run_fallback_ladder(
        registry, context, spec, section, seed, previous_ids
    )

    result.entries = selected
    result.fallback_step = step
    result.candidate_count = len(pool.candidates)
    result.pool_total = pool.total
    result.score_reasons = reasons

    if pool.total == 0:
        # Not "rolled and came up empty" -- there was nothing here to roll. The
        # axis is absent from the enabled packs, so rules that point at it are
        # inapplicable rather than violated.
        context.absent.add(section)
    context.set_selection(section, selected)

    _report_pool(context, spec, section, pool)

    if not selected and not spec.multi and not settings.is_skipped(section):
        if pool.total == 0:
            # No entries of this type exist in the enabled packs. The axis is not
            # present, which is not the same as failing to find a candidate on it.
            # A pack may ship any subset of the content types; the summary records
            # the absence, and nothing is broken.
            pass
        else:
            context.warn(
                ErrorCode.EMPTY_CANDIDATE_POOL,
                Severity.HIGH,
                f"Every one of the {pool.total} '{section}' entries was filtered "
                "out. Relax an exclusion, unlock an earlier section, or switch to "
                "a looser compatibility mode.",
                section=section,
            )


def _report_pool(
    context: ResolutionContext, spec: ContentTypeSpec, section: str, pool: _Pool
) -> None:
    """Report hard rules that were skipped because their section never resolved."""
    if pool.skipped_keys:
        context.warn(
            ErrorCode.UNRESOLVED_RULE_KEY,
            Severity.INFO,
            f"In '{section}', these hard rules were skipped because the section "
            f"they point at was never resolved: {', '.join(sorted(pool.skipped_keys))}. "
            "The gate did not fire. If that was not intended, the pack is missing "
            "a content type or the section is set to 'none'.",
            section=section,
        )



def _apply_lock(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
    section: str,
    locked_id: str,
) -> Entry | None:
    entry = registry.get(locked_id)
    if entry is None or entry.type != spec.type_name:
        context.warn(
            ErrorCode.MISSING_REFERENCE,
            Severity.HIGH,
            f"Locked {section} '{locked_id}' does not exist (or is the wrong type). "
            "A missing entry cannot be preserved, so this section was resolved "
            "normally. Reload your packs or clear the lock.",
            section=section,
            entry_id=locked_id,
        )
        return None

    # The lock stands. We still evaluate the rules so the user learns *why* it is
    # a problem, rather than getting a silently incoherent prompt.
    reasons: list[str] = []
    user = evaluate_user_exclusions(entry, context.settings.excluded_tags,
                                    context.settings.excluded_ids)
    reasons += user.reasons

    match_context = context.match_context()
    hard = evaluate_hard_rules(entry, match_context, context.settings.compatibility_mode)
    reasons += hard.reasons

    allow = evaluate_allowlists(entry, spec, context.selected_entries())
    reasons += allow.reasons

    if reasons:
        context.lock_conflict(
            section,
            entry,
            reasons,
            suggestion=f"Unlock '{section}', or change the section it conflicts with.",
        )
    return entry


# --------------------------------------------------------------------------
# Fallback ladder (blueprint 20)
# --------------------------------------------------------------------------


def _run_fallback_ladder(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
    section: str,
    seed: int,
    previous_ids: Mapping[str, Sequence[str]],
) -> tuple[list[Entry], int, _Pool, list[str]]:
    """Returns (entries, fallback_step_used, pool, score_reasons).

    Step 1 normal, 2 drop preferences, 3 also drop avoidance, 4 generic fallback
    entry, 5 reuse previous, 6 give up.

    Note on steps 2 and 3: blueprint 15 removes hard conflicts from the pool
    rather than down-weighting them, so relaxing *soft* scoring can never refill
    an empty pool. They therefore only change which candidate is picked, not
    whether one exists. Implemented faithfully, and the step is recorded, but an
    empty pool goes straight to step 4.
    """
    settings = context.settings
    pool = _build_pool(registry, context, spec)

    if pool.candidates:
        for step, (no_prefs, no_avoid) in enumerate(
            ((False, False), (True, False), (True, True)), start=1
        ):
            if step > MAX_FALLBACK_ATTEMPTS:
                break
            scored = score_all(
                pool.candidates,
                context.match_context(),
                settings.compatibility_mode,
                spec=spec,
                parents=context.selected_entries(),
                pack_multipliers={
                    p: registry.pack_multiplier(c)
                    for c in pool.candidates
                    for p in (c.pack_id,)
                },
                extra_penalties=pool.extra_penalties,
                ignore_preferences=no_prefs,
                ignore_avoidance=no_avoid,
            )
            if not _is_degenerate(scored) or step == 3:
                picked = _select(scored, spec, section, seed, context)
                reasons = [r for c in picked for r in _reasons_for(scored, c)]
                return picked, step, pool, reasons

    if spec.multi:
        # Props are optional by nature. An empty pool means "this action needs no
        # prop", which is a legitimate outcome -- not something to paper over.
        return [], 1, pool, []

    # Step 4: the generic fallback entry, if this mode permits one.
    if settings.compatibility_mode in FALLBACK_ENABLED_MODES:
        fallback = _pick_fallback(registry, context, spec)
        if fallback is not None:
            context.warn(
                ErrorCode.EMPTY_CANDIDATE_POOL,
                Severity.WARN,
                f"No '{section}' candidate satisfied the rules; the generic "
                f"fallback '{fallback.label}' was used.",
                section=section,
                entry_id=fallback.id,
            )
            return [fallback], 4, pool, []

    # Step 5: reuse the previous compatible entry, if we were given one.
    reused = _reuse_previous(registry, context, spec, previous_ids.get(section, ()))
    if reused:
        context.warn(
            ErrorCode.EMPTY_CANDIDATE_POOL,
            Severity.WARN,
            f"No '{section}' candidate satisfied the rules; the previous "
            "compatible selection was reused.",
            section=section,
            entry_id=reused[0].id,
        )
        return reused, 5, pool, []

    # Step 6: unresolved. The caller warns; props are allowed to be empty.
    return [], 6, pool, []


def _build_pool(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
) -> _Pool:
    settings = context.settings
    mode = settings.compatibility_mode
    match_context = context.match_context()
    parents = context.selected_entries()

    pool = _Pool()
    candidates = registry.get_enabled_entries(
        spec.type_name, enabled_packs=settings.enabled_packs
    )
    pool.total = len(candidates)
    filters = settings.metadata_filters.get(spec.section or "", {})

    for candidate in candidates:
        if not _matches_metadata(candidate, filters):
            pool.rejected[candidate.id] = [f"metadata filter {filters}"]
            continue

        user = evaluate_user_exclusions(
            candidate, settings.excluded_tags, settings.excluded_ids
        )
        if not user.accepted:
            pool.rejected[candidate.id] = user.reasons
            continue

        hard = evaluate_hard_rules(candidate, match_context, mode)
        pool.skipped_keys.update(hard.skipped_keys)
        if not hard.accepted:
            pool.rejected[candidate.id] = hard.reasons
            continue

        allow = evaluate_allowlists(candidate, spec, parents)
        if not allow.accepted:
            pool.rejected[candidate.id] = allow.reasons
            continue

        pool.candidates.append(candidate)
        if hard.demoted_penalty:
            pool.extra_penalties[candidate.id] = hard.demoted_penalty

    return pool


def _matches_metadata(candidate: Entry, filters: Mapping[str, str]) -> bool:
    for field_name, required in filters.items():
        if str(candidate.metadata.get(field_name, "")) != required:
            return False
    return True


def _is_degenerate(scored: Sequence[ScoredCandidate]) -> bool:
    """True when every candidate has been penalised down to the floor.

    That is the only situation where dropping soft scoring changes anything, so
    it is what ladder steps 2 and 3 actually respond to.
    """
    from .constants import MINIMUM_SCORE

    return bool(scored) and all(c.score <= MINIMUM_SCORE for c in scored)


def _select(
    scored: list[ScoredCandidate],
    spec: ContentTypeSpec,
    section: str,
    seed: int,
    context: ResolutionContext,
) -> list[Entry]:
    if not spec.multi:
        return [weighted_choice(scored, seed).entry]

    low, high = PROP_COUNT_BY_LENGTH[context.settings.prompt_length]
    action = context.selected("action")
    if action is not None and action.metadata.get("requires_prop"):
        low = max(low, 1)  # the action names a prop; do not render it empty-handed

    count = choice_in_range(seed, low, min(high, len(scored)))
    return [c.entry for c in weighted_sample(scored, seed, count)]


def _reasons_for(scored: Sequence[ScoredCandidate], entry: Entry) -> list[str]:
    for candidate in scored:
        if candidate.id == entry.id:
            return candidate.reasons
    return []


def _pick_fallback(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
) -> Entry | None:
    settings = context.settings
    match_context = context.match_context()
    parents = context.selected_entries()

    for candidate in registry.get_fallback_entries(
        spec.type_name, enabled_packs=settings.enabled_packs
    ):
        # A fallback still respects user exclusions and structure. It is a relief
        # valve for over-restrictive *content* rules, not a way around the user.
        if not evaluate_user_exclusions(
            candidate, settings.excluded_tags, settings.excluded_ids
        ).accepted:
            continue
        if not evaluate_hard_rules(
            candidate, match_context, settings.compatibility_mode
        ).accepted:
            continue
        if not evaluate_allowlists(candidate, spec, parents).accepted:
            continue
        return candidate
    return None


def _reuse_previous(
    registry: ContentRegistry,
    context: ResolutionContext,
    spec: ContentTypeSpec,
    previous: Sequence[str],
) -> list[Entry]:
    settings = context.settings
    match_context = context.match_context()
    parents = context.selected_entries()

    reused: list[Entry] = []
    for entry_id in previous:
        entry = registry.get(entry_id)
        if entry is None or entry.type != spec.type_name:
            continue
        if not evaluate_user_exclusions(
            entry, settings.excluded_tags, settings.excluded_ids
        ).accepted:
            continue
        if not evaluate_hard_rules(
            entry, match_context, settings.compatibility_mode
        ).accepted:
            continue
        if not evaluate_allowlists(entry, spec, parents).accepted:
            continue
        reused.append(entry)
        if not spec.multi:
            break
    return reused
