"""Pack discovery and loading (blueprint 10, FR13-FR15).

Isolation policy: a pack is loaded whole or not at all. Any schema failure in any
of its files rejects that pack alone -- other packs are untouched. Fail-closed
avoids half-loaded packs whose entries reference siblings that never registered.
"""

from __future__ import annotations

import time
from pathlib import Path

from .constants import (
    DEFAULT_LIMITS,
    ENTRY_ID_SEPARATOR,
    MANIFEST_FILENAME,
    PackLimits,
    TYPES_BY_CONTENT_TYPE,
)
from .errors import Diagnostic, ErrorCode, SchemaValidationError, Severity
from .models import Entry, LoadReport, Manifest, Pack
from .validator import SchemaValidator, issues_to_diagnostics


class PackLoader:
    """Scans pack roots and produces validated Pack objects plus a LoadReport."""

    def __init__(
        self,
        validator: SchemaValidator,
        limits: PackLimits = DEFAULT_LIMITS,
    ) -> None:
        self.validator = validator
        self.limits = limits

    def load_all(self, roots: list[Path]) -> tuple[list[Pack], LoadReport]:
        started = time.perf_counter()
        report = LoadReport()
        packs: list[Pack] = []

        for pack_dir in self._discover(roots, report):
            pack = self._load_pack(pack_dir, report)
            if pack is None:
                report.packs_failed.append(pack_dir.name)
                # Fail-closed is right -- half a pack is worse than no pack. But a
                # failure this total must be legible. Without this line, a single
                # bad field reads as one small warning while every entry in the
                # pack quietly disappears and the prompts fall back to a fixture.
                report.diagnostics.append(
                    Diagnostic(
                        code=ErrorCode.PACK_LOAD,
                        severity=Severity.HIGH,
                        message=(
                            f"PACK '{pack_dir.name}' IS NOT LOADED. Every entry in "
                            "it has been discarded because at least one failed "
                            "validation (see the errors above). Nothing from this "
                            "pack can be selected until they are fixed."
                        ),
                        pack_id=pack_dir.name,
                    )
                )
                continue
            packs.append(pack)
            report.packs_loaded.append(pack.pack_id)
            for entry in pack.entries:
                report.entry_counts[entry.type] = (
                    report.entry_counts.get(entry.type, 0) + 1
                )

        packs = self._apply_dependencies(packs, report)

        # Deterministic order: highest priority first, then pack_id. Downstream
        # registration and duplicate resolution depend on this being stable.
        packs.sort(key=lambda p: (-p.priority, p.pack_id))

        report.duration_ms = (time.perf_counter() - started) * 1000.0
        return packs, report

    # -- discovery ---------------------------------------------------------

    def _discover(self, roots: list[Path], report: LoadReport) -> list[Path]:
        found: list[Path] = []
        for root in roots:
            if not root.is_dir():
                report.add(
                    Diagnostic(
                        code=ErrorCode.PACK_LOAD,
                        severity=Severity.INFO,
                        message=f"Pack root does not exist, skipping: {root}",
                    )
                )
                continue
            for child in sorted(root.iterdir()):
                if child.is_dir() and (child / MANIFEST_FILENAME).is_file():
                    found.append(child)
        return sorted(found, key=lambda p: p.name)

    # -- one pack ----------------------------------------------------------

    def _load_pack(self, pack_dir: Path, report: LoadReport) -> Pack | None:
        try:
            manifest = self._load_manifest(pack_dir, report)
            if manifest is None:
                return None
            entries = self._load_entries(pack_dir, manifest, report)
            if entries is None:
                return None
        except SchemaValidationError as exc:
            report.add(
                Diagnostic(
                    code=ErrorCode.PACK_LOAD,
                    severity=Severity.HIGH,
                    message=exc.message,
                    pack_id=pack_dir.name,
                    detail=exc.detail,
                )
            )
            return None
        except OSError as exc:
            report.add(
                Diagnostic(
                    code=ErrorCode.PACK_LOAD,
                    severity=Severity.HIGH,
                    message=f"Could not read pack: {exc}",
                    pack_id=pack_dir.name,
                )
            )
            return None

        return Pack(manifest=manifest, path=pack_dir, entries=tuple(entries))

    def _load_manifest(self, pack_dir: Path, report: LoadReport) -> Manifest | None:
        path = pack_dir / MANIFEST_FILENAME
        document = self.validator.read_json_file(path, pack_dir)

        issues = self.validator.validate_manifest(document)
        if issues:
            for diagnostic in issues_to_diagnostics(
                issues, pack_id=pack_dir.name, source_file=MANIFEST_FILENAME
            ):
                report.add(diagnostic)
            return None

        unknown = [
            ct
            for ct in document["content_types"]
            if not self.validator.content_type_is_known(ct)
        ]
        if unknown:
            report.add(
                Diagnostic(
                    code=ErrorCode.VALIDATION,
                    severity=Severity.HIGH,
                    message="Manifest declares content types the engine does not know: "
                    + ", ".join(unknown),
                    pack_id=document["pack_id"],
                    source_file=MANIFEST_FILENAME,
                    detail={"unknown_content_types": unknown},
                )
            )
            return None

        return Manifest(
            pack_id=document["pack_id"],
            name=document["name"],
            version=document["version"],
            schema_version=document["schema_version"],
            content_types=tuple(document["content_types"]),
            author=document.get("author", ""),
            description=document.get("description", ""),
            enabled_by_default=document.get("enabled_by_default", True),
            priority=document.get("priority", 100),
            dependencies=tuple(document.get("dependencies", ())),
            tags=tuple(document.get("tags", ())),
        )

    def _load_entries(
        self, pack_dir: Path, manifest: Manifest, report: LoadReport
    ) -> list[Entry] | None:
        files = sorted(
            p for p in pack_dir.glob("*.json") if p.name != MANIFEST_FILENAME
        )
        if len(files) > self.limits.max_files_per_pack:
            report.add(
                Diagnostic(
                    code=ErrorCode.VALIDATION,
                    severity=Severity.HIGH,
                    message=f"Pack contains {len(files)} content files, "
                    f"limit is {self.limits.max_files_per_pack}",
                    pack_id=manifest.pack_id,
                )
            )
            return None

        entries: list[Entry] = []
        seen_ids: set[str] = set()
        failed = False

        for path in files:
            file_entries = self._load_content_file(
                path, pack_dir, manifest, seen_ids, report
            )
            if file_entries is None:
                failed = True
                continue
            entries.extend(file_entries)

        if failed:
            return None

        if len(entries) > self.limits.max_entries_per_pack:
            report.add(
                Diagnostic(
                    code=ErrorCode.VALIDATION,
                    severity=Severity.HIGH,
                    message=f"Pack contains {len(entries)} entries, "
                    f"limit is {self.limits.max_entries_per_pack}",
                    pack_id=manifest.pack_id,
                )
            )
            return None

        if not entries:
            report.add(
                Diagnostic(
                    code=ErrorCode.PACK_LOAD,
                    severity=Severity.HIGH,
                    message="Pack contains no entries",
                    pack_id=manifest.pack_id,
                )
            )
            return None

        return entries

    def _load_content_file(
        self,
        path: Path,
        pack_dir: Path,
        manifest: Manifest,
        seen_ids: set[str],
        report: LoadReport,
    ) -> list[Entry] | None:
        document = self.validator.read_json_file(path, pack_dir)

        issues = self.validator.validate_content_file(document)
        if issues:
            for diagnostic in issues_to_diagnostics(
                issues, pack_id=manifest.pack_id, source_file=path.name
            ):
                report.add(diagnostic)
            return None

        content_type = document["content_type"]
        spec = TYPES_BY_CONTENT_TYPE.get(content_type)
        if spec is None:
            report.add(
                Diagnostic(
                    code=ErrorCode.VALIDATION,
                    severity=Severity.HIGH,
                    message=f"Unknown content type '{content_type}'",
                    pack_id=manifest.pack_id,
                    source_file=path.name,
                )
            )
            return None

        if content_type not in manifest.content_types:
            report.add(
                Diagnostic(
                    code=ErrorCode.VALIDATION,
                    severity=Severity.HIGH,
                    message=f"Content type '{content_type}' is not declared in the manifest",
                    pack_id=manifest.pack_id,
                    source_file=path.name,
                )
            )
            return None

        entries: list[Entry] = []
        failed = False

        for raw in document["entries"]:
            entry_issues = self.validator.validate_entry(raw, spec.type_name)
            if entry_issues:
                for diagnostic in issues_to_diagnostics(
                    entry_issues, pack_id=manifest.pack_id, source_file=path.name
                ):
                    report.add(diagnostic)
                failed = True
                continue

            entry_id: str = raw["id"]
            prefix = entry_id.split(ENTRY_ID_SEPARATOR)[0]
            if prefix != manifest.pack_id:
                report.add(
                    Diagnostic(
                        code=ErrorCode.VALIDATION,
                        severity=Severity.HIGH,
                        message=f"Entry id prefix '{prefix}' does not match "
                        f"pack_id '{manifest.pack_id}'",
                        pack_id=manifest.pack_id,
                        source_file=path.name,
                        entry_id=entry_id,
                    )
                )
                failed = True
                continue

            if entry_id in seen_ids:
                report.add(
                    Diagnostic(
                        code=ErrorCode.VALIDATION,
                        severity=Severity.HIGH,
                        message="Duplicate entry id within pack",
                        pack_id=manifest.pack_id,
                        source_file=path.name,
                        entry_id=entry_id,
                    )
                )
                failed = True
                continue

            seen_ids.add(entry_id)
            entries.append(_build_entry(raw, manifest.pack_id, path.name))

        return None if failed else entries

    # -- dependencies ------------------------------------------------------

    def _apply_dependencies(
        self, packs: list[Pack], report: LoadReport
    ) -> list[Pack]:
        """Unmet dependency disables the pack; other packs are unaffected (M13)."""
        available = {p.pack_id for p in packs}
        kept: list[Pack] = []

        for pack in packs:
            missing = [d for d in pack.manifest.dependencies if d not in available]
            if missing:
                report.add(
                    Diagnostic(
                        code=ErrorCode.PACK_LOAD,
                        severity=Severity.WARN,
                        message="Pack disabled, unmet dependencies: "
                        + ", ".join(missing),
                        pack_id=pack.pack_id,
                        detail={"missing_dependencies": missing},
                    )
                )
                report.packs_disabled.append(pack.pack_id)
                if pack.pack_id in report.packs_loaded:
                    report.packs_loaded.remove(pack.pack_id)
                for entry in pack.entries:
                    report.entry_counts[entry.type] -= 1
                continue
            kept.append(pack)

        report.entry_counts = {k: v for k, v in report.entry_counts.items() if v > 0}
        return kept


def _build_entry(raw: dict, pack_id: str, source_file: str) -> Entry:
    return Entry(
        id=raw["id"],
        type=raw["type"],
        label=raw["label"],
        pack_id=pack_id,
        source_file=source_file,
        enabled=raw.get("enabled", True),
        weight=float(raw.get("weight", 1.0)),
        prompt=raw.get("prompt", ""),
        negative_prompt=raw.get("negative_prompt", ""),
        is_fallback=raw.get("is_fallback", False),
        tags=tuple(raw.get("tags", ())),
        requires=raw.get("requires", {}),
        allows=raw.get("allows", {}),
        prefers=raw.get("prefers", {}),
        avoids=raw.get("avoids", {}),
        excludes=raw.get("excludes", {}),
        model_prompts=raw.get("model_prompts", {}),
        metadata=raw.get("metadata", {}),
    )
