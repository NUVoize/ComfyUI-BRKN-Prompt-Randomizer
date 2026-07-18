"""Pack health checks that need no seed and no generation.

A rule that gates candidates out on a given roll is usually the system working:
a kitchen is *supposed* to exclude garage actions. That is contextual narrowing,
it happens on every roll, and warning about it would be noise.

A rule that no enabled entry could satisfy *in any roll* is something else
entirely. Eighteen coats requiring an outdoor location, in a pack whose every
location is indoors, are unreachable forever. The pool never empties, so the
fallback ladder never runs; nothing warns, nothing errors, and the coats simply
never appear. You find out weeks later, by noticing.

That class of bug is invisible at runtime and obvious at rest, so this module
looks at it at rest.
"""

from __future__ import annotations

from collections.abc import Iterable

from .constants import RESOLUTION_ORDER, ContentTypeSpec
from .errors import Diagnostic, ErrorCode, Severity
from .models import Entry
from .registry import ContentRegistry
from .rules import MatchMode, parse_rule_block

# section -> the spec that owns it, for turning a rule key back into a content type
_SPEC_BY_TAG_KEY: dict[str, ContentTypeSpec] = {
    spec.tag_key: spec for spec in RESOLUTION_ORDER if spec.tag_key
}
_SPEC_BY_ID_KEY: dict[str, ContentTypeSpec] = {
    spec.id_key: spec for spec in RESOLUTION_ORDER if spec.id_key
}


def _reachable_tags(entries: Iterable[Entry]) -> frozenset[str]:
    return frozenset(tag for e in entries for tag in e.tags)


def _reachable_ids(entries: Iterable[Entry]) -> frozenset[str]:
    return frozenset(e.id for e in entries)


def find_dead_gates(
    registry: ContentRegistry,
    enabled_packs: frozenset[str] | None = None,
    skipped: frozenset[str] = frozenset(),
) -> list[Diagnostic]:
    """Entries whose `requires` rules no enabled entry can ever satisfy.

    A rule pointing at a content type with *no* enabled entries at all is not a
    dead gate -- it is a skipped gate, reported separately (PCR401) at resolve
    time, because a pack may legitimately omit a type and the rule is then simply
    not applicable. A dead gate is narrower and worse: the target type exists,
    has entries, and not one of them carries what the rule demands.
    """
    reachable: dict[str, frozenset[str]] = {}
    present: dict[str, bool] = {}
    for spec in RESOLUTION_ORDER:
        entries = registry.get_enabled_entries(spec.type_name, enabled_packs=enabled_packs)
        section = spec.section or spec.type_name
        # A section the user switched off is absent, not unsatisfiable. Rules
        # pointing at it are inapplicable (PCR401), not dead (PCR402).
        present[spec.type_name] = bool(entries) and section not in skipped
        if spec.tag_key:
            reachable[spec.tag_key] = _reachable_tags(entries)
        if spec.id_key:
            reachable[spec.id_key] = _reachable_ids(entries)

    # cause -> (section, [entry labels]) so eighteen coats are one line, not eighteen
    grouped: dict[tuple[str, str], list[str]] = {}
    totals: dict[str, int] = {}

    for spec in RESOLUTION_ORDER:
        section = spec.section or spec.type_name
        if section in skipped:
            continue
        candidates = registry.get_enabled_entries(
            spec.type_name, enabled_packs=enabled_packs
        )
        totals[section] = len(candidates)

        for entry in candidates:
            for rule in parse_rule_block(entry.requires):
                target = _SPEC_BY_TAG_KEY.get(rule.key) or _SPEC_BY_ID_KEY.get(rule.key)
                if target is None or not present.get(target.type_name, False):
                    continue  # skipped gate, not a dead one -- PCR401 covers it
                available = reachable.get(rule.key, frozenset())
                if rule.mode is MatchMode.ANY:
                    satisfiable = bool(rule.values & available)
                elif rule.mode is MatchMode.ALL:
                    satisfiable = rule.values <= available
                else:  # NONE -- vacuously satisfiable; something always fails to match
                    continue
                if satisfiable:
                    continue
                cause = f"{rule.raw_key} = {sorted(rule.values)}"
                grouped.setdefault((section, cause), []).append(entry.label)

    out: list[Diagnostic] = []
    for (section, cause), labels in sorted(grouped.items()):
        total = totals.get(section, 0)
        shown = ", ".join(sorted(labels)[:4])
        more = f", +{len(labels) - 4} more" if len(labels) > 4 else ""
        out.append(
            Diagnostic(
                code=ErrorCode.DEAD_GATE,
                severity=Severity.WARN,
                message=(
                    f"{len(labels)} of {total} '{section}' entries require "
                    f"{cause}, which no enabled entry provides. They can never be "
                    f"selected, in any roll. ({shown}{more})"
                ),
                section=section,
            )
        )
    return out
