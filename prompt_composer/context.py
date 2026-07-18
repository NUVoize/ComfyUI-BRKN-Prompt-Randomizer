"""Settings and the mutable resolution context.

Two things here carry real weight:

* **Locks.** Decision B7: an explicit non-random selection *is* a lock. A locked
  entry is never silently replaced -- if it conflicts with a hard rule we keep it
  and emit a high-severity PCC300 naming the conflicting field, the rule, and a
  suggested fix (blueprint 19).

* **Revision cascade.** Blueprint 18 says changing the environment must clear the
  location, action, pose and props. Rather than a bespoke invalidation graph, a
  section's *effective* revision is its own revision plus its ancestors'. Bumping
  `environment_rev` therefore perturbs every descendant's sub-seed automatically,
  while bumping `fashion_rev` perturbs nothing but fashion. The dependency edges
  already live in the content-type registry, so this stays correct when new types
  are added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping, Sequence

from .constants import (
    CHARACTER_FACETS,
    CONDITIONS_SECTION,
    CompatibilityMode,
    DEFAULT_COMPATIBILITY_MODE,
    NONE_OPTION,
    PromptLength,
    RANDOM_OPTION,
    TYPES_BY_SECTION,
    TargetModel,
)
from .errors import Diagnostic, ErrorCode, Severity
from .models import Entry
from .rules import build_match_context, MatchContext
from .seeds import section_seed


@dataclass(frozen=True)
class Settings:
    """Everything the node hands the engine. No ComfyUI types leak in here."""

    seed: int = 0
    compatibility_mode: CompatibilityMode = DEFAULT_COMPATIBILITY_MODE
    prompt_length: PromptLength = PromptLength.STANDARD
    target_model: TargetModel = TargetModel.GENERIC

    # An explicit id locks the section; RANDOM_OPTION leaves it free.
    locks: Mapping[str, str] = field(default_factory=dict)
    revisions: Mapping[str, int] = field(default_factory=dict)

    skipped: frozenset[str] = frozenset()
    """Sections switched off entirely: nothing is rolled and nothing is written.

    Not the same as a section that rolled and found nothing. This one was never
    asked. It exists because a character LoRA already carries the face, and a
    rolled hairstyle then fights the weights instead of helping them -- the pixie
    cut that ruins the render was put there by the prompt, not by the model.
    """

    enabled_packs: frozenset[str] | None = None
    excluded_tags: frozenset[str] = frozenset()
    excluded_ids: frozenset[str] = frozenset()

    # section -> {metadata field: required value}. Used by the node's category
    # dropdowns (visual category, action family) without teaching the engine
    # about either of them by name.
    metadata_filters: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    time: str = RANDOM_OPTION
    season: str = NONE_OPTION
    weather: str = NONE_OPTION

    prefix: str = ""
    suffix: str = ""
    required_terms: str = ""
    negative_prompt: str = ""

    def lock_for(self, section: str) -> str | None:
        value = self.locks.get(section, RANDOM_OPTION)
        if not value or value in (RANDOM_OPTION, NONE_OPTION):
            return None
        return value

    def is_skipped(self, section: str) -> bool:
        """True when this section was switched off -- directly or by its parent.

        The character facets cascade. `character: none` that still rolls a body,
        a skin tone, an eye colour and a hairstyle is not "no character": it is a
        different character, described anonymously, and it will fight any LoRA
        you point at it. Ask for no person, get no person.
        """
        if section in self.skipped:
            return True
        return section in CHARACTER_FACETS and "character" in self.skipped

    def revision_for(self, section: str) -> int:
        return int(self.revisions.get(section, 0))

    def effective_revision(self, section: str) -> int:
        """Own revision plus every ancestor's, so rerolls cascade downstream."""
        return _effective_revision(section, tuple(sorted(self.revisions.items())))

    def section_seed(self, section: str) -> int:
        return section_seed(self.seed, section, self.effective_revision(section))


@lru_cache(maxsize=512)
def _effective_revision(section: str, revisions: tuple[tuple[str, int], ...]) -> int:
    table = dict(revisions)

    def walk(name: str, seen: frozenset[str]) -> int:
        if name in seen:  # guards against a malformed dependency cycle
            return 0
        spec = TYPES_BY_SECTION.get(name)
        total = int(table.get(name, 0))
        if spec is None:
            return total
        for parent in spec.depends_on:
            total += walk(parent, seen | {name})
        return total

    return walk(section, frozenset())


@dataclass
class SectionResult:
    """What happened in one section, for metadata and for the warnings output."""

    section: str
    entries: list[Entry] = field(default_factory=list)
    locked: bool = False
    skipped: bool = False
    fallback_step: int = 0
    candidate_count: int = 0
    pool_total: int = 0
    """Entries of this type that were enabled, before any filtering."""
    seed: int = 0
    revision: int = 0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def entry(self) -> Entry | None:
        return self.entries[0] if self.entries else None

    @property
    def resolved(self) -> bool:
        return bool(self.entries)


@dataclass
class ResolutionContext:
    """Mutable state threaded through the resolver."""

    settings: Settings
    selections: dict[str, list[Entry]] = field(default_factory=dict)
    results: dict[str, SectionResult] = field(default_factory=dict)
    conditions: dict[str, str] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    absent: set[str] = field(default_factory=set)
    """Sections with no enabled entries at all -- the axis does not exist here.

    Distinct from a section that was rolled and legitimately selected nothing.
    Props coming up empty means "this action needs no prop", and an outfit that
    requires a prop tag should indeed fail. But a pack that ships no environments
    at all has no environment *axis*, and a location that requires an environment
    tag must not be punished for a dimension that was never on the table -- or
    every location in the pack becomes unreachable at once, in silence.
    """

    # -- selections --------------------------------------------------------

    def set_selection(self, section: str, entries: Sequence[Entry]) -> None:
        self.selections[section] = list(entries)

    def selected(self, section: str) -> Entry | None:
        entries = self.selections.get(section) or []
        return entries[0] if entries else None

    def selected_entries(self) -> list[Entry]:
        """Every selected entry, in resolution order. These are the rule parents."""
        out: list[Entry] = []
        for section in self.selections:
            out.extend(self.selections[section])
        return out

    def match_context(self) -> MatchContext:
        condition_tags = {
            f"{key}_tags": [value.replace(" ", "_")]
            for key, value in self.conditions.items()
            if value
        }
        selections: dict[str, Entry | list[Entry] | None] = {}
        for section, entries in self.selections.items():
            spec = TYPES_BY_SECTION.get(section)
            if spec is None or section in self.absent:
                continue
            selections[section] = entries if spec.multi else (entries[0] if entries else None)
        return build_match_context(selections, condition_tags)

    # -- diagnostics -------------------------------------------------------

    def warn(
        self,
        code: ErrorCode,
        severity: Severity,
        message: str,
        *,
        section: str | None = None,
        entry_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.diagnostics.append(
            Diagnostic(
                code=code,
                severity=severity,
                message=message,
                section=section,
                entry_id=entry_id,
                detail=detail or {},
            )
        )

    def lock_conflict(
        self,
        section: str,
        entry: Entry,
        reasons: Sequence[str],
        suggestion: str,
    ) -> None:
        """Blueprint 19: preserve the lock, report loudly, never substitute."""
        self.warn(
            ErrorCode.COMPATIBILITY_CONFLICT,
            Severity.HIGH,
            f"Locked {section} '{entry.label}' conflicts with the current scene: "
            + "; ".join(reasons)
            + f". The lock was preserved. {suggestion}",
            section=section,
            entry_id=entry.id,
            detail={"conflicts": list(reasons), "suggestion": suggestion},
        )

    @property
    def has_conflicts(self) -> bool:
        return any(d.severity is Severity.HIGH for d in self.diagnostics)


CONDITION_KEYS: tuple[str, ...] = ("time", "season", "weather")

__all__ = [
    "CONDITIONS_SECTION",
    "CONDITION_KEYS",
    "ResolutionContext",
    "SectionResult",
    "Settings",
]
