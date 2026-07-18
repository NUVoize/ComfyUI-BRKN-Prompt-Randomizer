"""Error codes, severities and structured diagnostics (blueprint 30).

Nothing in the engine raises bare exceptions across a module boundary: pack
problems become PackIssue records so a broken pack can be isolated and reported
without stopping valid packs from loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    VALIDATION = "PCV100"
    """Schema or structural validation failure."""

    MISSING_REFERENCE = "PCR200"
    """A rule points at an entry ID that does not exist."""

    COMPATIBILITY_CONFLICT = "PCC300"
    """A locked selection conflicts with a hard rule."""

    EMPTY_CANDIDATE_POOL = "PCR400"
    """No candidate survived filtering, even after the fallback ladder."""

    UNRESOLVED_RULE_KEY = "PCR401"
    """A hard rule pointed at a section that was never resolved, and was skipped.

    Not an error: a pack may legitimately omit a content type, and a section may
    be deliberately set to `none`. But it means a gate you authored did not fire,
    so it is reported rather than swallowed.
    """

    DEAD_GATE = "PCR402"
    """An entry's `requires` rule can never be satisfied by any enabled entry.

    Not "was not satisfied this roll" -- a kitchen excluding garage actions is
    the system working. This is a gate that is unsatisfiable in *every* roll:
    eighteen coats requiring an outdoor location when no enabled location is
    outdoors. The entry is unreachable, the pool is never empty, the fallback
    ladder never runs, and nothing, anywhere, ever mentions it.

    Seed-independent, so it is found by inspection rather than by luck.
    """

    TEMPLATE = "PCT500"
    """Template is malformed or uses unsupported syntax."""

    PACK_LOAD = "PCP600"
    """Pack could not be read, or its manifest/dependencies are unusable."""


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARN: 1,
    Severity.HIGH: 2,
}


@dataclass(frozen=True)
class Diagnostic:
    """One machine-readable problem, surfaced to the user and to metadata."""

    code: ErrorCode
    severity: Severity
    message: str
    pack_id: str | None = None
    source_file: str | None = None
    entry_id: str | None = None
    section: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        """Single-line human-readable form. Never relies on colour alone."""
        location = " ".join(
            part
            for part in (
                f"pack={self.pack_id}" if self.pack_id else "",
                f"file={self.source_file}" if self.source_file else "",
                f"entry={self.entry_id}" if self.entry_id else "",
                f"section={self.section}" if self.section else "",
            )
            if part
        )
        prefix = f"[{self.code.value}][{self.severity.value.upper()}]"
        return f"{prefix} {self.message}" + (f" ({location})" if location else "")

    @property
    def is_blocking(self) -> bool:
        return self.severity is Severity.HIGH


def sort_diagnostics(items: list[Diagnostic]) -> list[Diagnostic]:
    """Highest severity first, then stable by code and message."""
    return sorted(
        items,
        key=lambda d: (-_SEVERITY_ORDER[d.severity], d.code.value, d.message),
    )


class PromptComposerError(Exception):
    """Base class. Only raised for programmer errors and unusable input."""

    code: ErrorCode = ErrorCode.VALIDATION

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_diagnostic(self, severity: Severity = Severity.HIGH) -> Diagnostic:
        return Diagnostic(
            code=self.code,
            severity=severity,
            message=self.message,
            detail=self.detail,
        )


class PackLoadError(PromptComposerError):
    code = ErrorCode.PACK_LOAD


class SchemaValidationError(PromptComposerError):
    code = ErrorCode.VALIDATION


class TemplateError(PromptComposerError):
    code = ErrorCode.TEMPLATE


class ResolutionError(PromptComposerError):
    code = ErrorCode.EMPTY_CANDIDATE_POOL
