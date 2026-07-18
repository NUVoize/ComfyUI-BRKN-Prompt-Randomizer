"""JSON Schema validation and filesystem safety (blueprint 6, decision: Draft 2020-12).

Packs are data, never code. Everything that enters the engine passes through
here first: schema shape, path containment, size limits and JSON nesting depth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    CONTENT_TYPES,
    DEFAULT_LIMITS,
    PackLimits,
    TYPES_BY_CONTENT_TYPE,
    TYPES_BY_NAME,
)
from .errors import Diagnostic, ErrorCode, SchemaValidationError, Severity

try:  # pragma: no cover - exercised only in degraded environments
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    JSONSCHEMA_AVAILABLE = True
    JSONSCHEMA_IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover
    JSONSCHEMA_AVAILABLE = False
    JSONSCHEMA_IMPORT_ERROR = str(exc)


@dataclass(frozen=True)
class ValidationIssue:
    """A single schema failure, addressed to a specific place in a specific file."""

    message: str
    json_path: str
    entry_id: str | None = None


class SchemaValidator:
    """Validates manifests, content-file wrappers and individual entries."""

    def __init__(
        self,
        schemas_dir: Path,
        limits: PackLimits = DEFAULT_LIMITS,
    ) -> None:
        if not JSONSCHEMA_AVAILABLE:
            raise SchemaValidationError(
                "The 'jsonschema' package is required but not installed. "
                "Install the requirements.txt for this custom node.",
                detail={"import_error": JSONSCHEMA_IMPORT_ERROR},
            )

        self.schemas_dir = schemas_dir
        self.limits = limits
        self._schemas: dict[str, dict[str, Any]] = {}
        self._validators: dict[str, Draft202012Validator] = {}
        self._load_schemas()

    # -- setup -------------------------------------------------------------

    def _load_schemas(self) -> None:
        if not self.schemas_dir.is_dir():
            raise SchemaValidationError(
                f"Schema directory not found: {self.schemas_dir}"
            )

        for path in sorted(self.schemas_dir.glob("*.schema.json")):
            self._schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))

        required = {"manifest.schema.json", "content-file.schema.json",
                    "entry-base.schema.json"}
        required |= {spec.schema_file for spec in CONTENT_TYPES}
        missing = sorted(required - set(self._schemas))
        if missing:
            raise SchemaValidationError(
                "Content-type registry references schema files that do not exist: "
                + ", ".join(missing),
                detail={"missing": missing},
            )

        # Resolve $refs by bare filename so schemas stay relocatable.
        registry: Registry = Registry().with_resources(
            [
                (name, Resource.from_contents(schema))
                for name, schema in self._schemas.items()
            ]
        )
        for name, schema in self._schemas.items():
            self._validators[name] = Draft202012Validator(schema, registry=registry)

    # -- filesystem safety -------------------------------------------------

    def check_path_contained(self, candidate: Path, root: Path) -> None:
        """Reject path traversal and (by default) symlinks out of the pack root."""
        resolved = candidate.resolve()
        resolved_root = root.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise SchemaValidationError(
                f"Path escapes its pack root: {candidate}",
                detail={"path": str(candidate), "root": str(root)},
            )
        if not self.limits.follow_symlinks and candidate.is_symlink():
            raise SchemaValidationError(
                f"Symlinks are not permitted inside packs: {candidate}",
                detail={"path": str(candidate)},
            )

    def read_json_file(self, path: Path, root: Path) -> Any:
        """Read one JSON file with every limit enforced before parsing."""
        self.check_path_contained(path, root)

        size = path.stat().st_size
        if size > self.limits.max_file_bytes:
            raise SchemaValidationError(
                f"File exceeds the {self.limits.max_file_bytes} byte limit "
                f"({size} bytes): {path.name}",
                detail={"path": str(path), "bytes": size},
            )

        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(
                f"Malformed JSON in {path.name}: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno})",
                detail={"path": str(path)},
            ) from exc
        except UnicodeDecodeError as exc:
            raise SchemaValidationError(
                f"File is not valid UTF-8: {path.name}", detail={"path": str(path)}
            ) from exc

        depth = _json_depth(document, self.limits.max_json_depth)
        if depth > self.limits.max_json_depth:
            raise SchemaValidationError(
                f"JSON nesting exceeds depth {self.limits.max_json_depth}: {path.name}",
                detail={"path": str(path), "depth": depth},
            )
        return document

    # -- schema validation -------------------------------------------------

    def _validate(self, schema_file: str, document: Any) -> list[ValidationIssue]:
        validator = self._validators[schema_file]
        issues: list[ValidationIssue] = []
        for error in sorted(validator.iter_errors(document), key=str):
            json_path = "/".join(str(part) for part in error.absolute_path) or "<root>"
            issues.append(ValidationIssue(message=error.message, json_path=json_path))
        return issues

    def validate_manifest(self, document: Any) -> list[ValidationIssue]:
        return self._validate("manifest.schema.json", document)

    def validate_content_file(self, document: Any) -> list[ValidationIssue]:
        return self._validate("content-file.schema.json", document)

    def validate_entry(self, entry: Any, type_name: str) -> list[ValidationIssue]:
        spec = TYPES_BY_NAME.get(type_name)
        if spec is None:
            return [
                ValidationIssue(
                    message=f"Unknown content type '{type_name}'. Known types: "
                    + ", ".join(sorted(TYPES_BY_NAME)),
                    json_path="type",
                )
            ]

        issues = self._validate(spec.schema_file, entry)
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        return [
            ValidationIssue(i.message, i.json_path, entry_id=entry_id) for i in issues
        ]

    @staticmethod
    def content_type_is_known(content_type: str) -> bool:
        return content_type in TYPES_BY_CONTENT_TYPE


def issues_to_diagnostics(
    issues: list[ValidationIssue],
    *,
    pack_id: str | None,
    source_file: str | None,
) -> list[Diagnostic]:
    return [
        Diagnostic(
            code=ErrorCode.VALIDATION,
            severity=Severity.HIGH,
            message=f"{issue.json_path}: {issue.message}",
            pack_id=pack_id,
            source_file=source_file,
            entry_id=issue.entry_id,
        )
        for issue in issues
    ]


def _json_depth(node: Any, ceiling: int, current: int = 1) -> int:
    """Depth of a JSON document, short-circuiting once the ceiling is passed."""
    if current > ceiling:
        return current
    if isinstance(node, dict):
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return current
    deepest = current
    for child in children:
        deepest = max(deepest, _json_depth(child, ceiling, current + 1))
        if deepest > ceiling:
            break
    return deepest
