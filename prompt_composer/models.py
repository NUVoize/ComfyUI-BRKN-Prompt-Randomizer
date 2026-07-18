"""Immutable data model for packs and entries.

Rule blocks stay as raw dicts here; rules.py normalises them in Phase 3. Keeping
the loader ignorant of rule semantics means a rule-vocabulary change never
touches pack loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    ENTRY_ID_PARTS,
    ENTRY_ID_SEPARATOR,
    PACK_MULTIPLIER_MAX,
    PACK_MULTIPLIER_MIN,
    PACK_PRIORITY_BASELINE,
)
from .errors import Diagnostic

RuleBlock = dict[str, Any]


@dataclass(frozen=True)
class Manifest:
    pack_id: str
    name: str
    version: str
    schema_version: str
    content_types: tuple[str, ...]
    author: str = ""
    description: str = ""
    enabled_by_default: bool = True
    priority: int = int(PACK_PRIORITY_BASELINE)
    dependencies: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def multiplier(self) -> float:
        """Pack weighting derived from priority (decision M1)."""
        raw = self.priority / PACK_PRIORITY_BASELINE
        return min(max(raw, PACK_MULTIPLIER_MIN), PACK_MULTIPLIER_MAX)


@dataclass(frozen=True)
class Entry:
    """One content item. Shape is universal across every type (blueprint 11)."""

    id: str
    type: str
    label: str
    pack_id: str
    source_file: str
    enabled: bool = True
    weight: float = 1.0
    prompt: str = ""
    negative_prompt: str = ""
    is_fallback: bool = False
    tags: tuple[str, ...] = ()
    requires: RuleBlock = field(default_factory=dict)
    allows: RuleBlock = field(default_factory=dict)
    prefers: RuleBlock = field(default_factory=dict)
    avoids: RuleBlock = field(default_factory=dict)
    excludes: RuleBlock = field(default_factory=dict)
    model_prompts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def entry_name(self) -> str:
        return self.id.split(ENTRY_ID_SEPARATOR, ENTRY_ID_PARTS - 1)[-1]

    def prompt_for_model(self, template_id: str) -> str:
        """Entry-level model override beats the generic fragment (FR17)."""
        return self.model_prompts.get(template_id) or self.prompt

    def tag_set(self) -> frozenset[str]:
        return frozenset(self.tags)


@dataclass(frozen=True)
class Pack:
    manifest: Manifest
    path: Path
    entries: tuple[Entry, ...]

    @property
    def pack_id(self) -> str:
        return self.manifest.pack_id

    @property
    def priority(self) -> int:
        return self.manifest.priority


@dataclass
class LoadReport:
    """Observability surface for pack loading (blueprint 6)."""

    packs_loaded: list[str] = field(default_factory=list)
    packs_failed: list[str] = field(default_factory=list)
    packs_disabled: list[str] = field(default_factory=list)
    entry_counts: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    duration_ms: float = 0.0

    def add(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    @property
    def total_entries(self) -> int:
        return sum(self.entry_counts.values())

    def summary(self) -> str:
        lines = [
            f"packs loaded: {len(self.packs_loaded)}",
            f"packs failed: {len(self.packs_failed)}",
            f"packs disabled: {len(self.packs_disabled)}",
            f"entries: {self.total_entries}",
            f"load time: {self.duration_ms:.1f} ms",
        ]
        lines.extend(d.format() for d in self.diagnostics)
        return "\n".join(lines)
