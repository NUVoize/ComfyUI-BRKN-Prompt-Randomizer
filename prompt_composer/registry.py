"""The ID registry: the single lookup surface the resolver talks to.

Cross-pack duplicate IDs are resolved deterministically: packs are registered in
(-priority, pack_id) order, so the first pack to claim an ID keeps it and the
later pack is rejected whole. That keeps registration order-independent of the
filesystem.
"""

from __future__ import annotations

from collections import defaultdict

from .constants import CONTENT_TYPES, ENTRY_ID_KEYS, RULE_KEYS, RULE_VERBS
from .errors import Diagnostic, ErrorCode, Severity
from .models import Entry, LoadReport, Pack
from .rules import rule_values, split_rule_key


class ContentRegistry:
    """Indexed, validated view over every entry the engine may select from."""

    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._by_type: dict[str, list[Entry]] = defaultdict(list)
        self._packs: dict[str, Pack] = {}
        self._enabled_packs: set[str] = set()

    # -- registration ------------------------------------------------------

    def register_packs(self, packs: list[Pack], report: LoadReport) -> None:
        """Register packs in the order given. First claim on an ID wins."""
        for pack in packs:
            if not self._register_pack(pack, report):
                report.packs_failed.append(pack.pack_id)
                if pack.pack_id in report.packs_loaded:
                    report.packs_loaded.remove(pack.pack_id)

        self._check_references(report)
        self._check_rule_keys(report)

    def _register_pack(self, pack: Pack, report: LoadReport) -> bool:
        collisions = [e.id for e in pack.entries if e.id in self._entries]
        if collisions:
            for entry_id in collisions:
                report.add(
                    Diagnostic(
                        code=ErrorCode.VALIDATION,
                        severity=Severity.HIGH,
                        message="Duplicate entry id already claimed by pack "
                        f"'{self._entries[entry_id].pack_id}'; this pack is rejected",
                        pack_id=pack.pack_id,
                        entry_id=entry_id,
                    )
                )
            return False

        for entry in pack.entries:
            self._entries[entry.id] = entry
            self._by_type[entry.type].append(entry)

        self._packs[pack.pack_id] = pack
        if pack.manifest.enabled_by_default:
            self._enabled_packs.add(pack.pack_id)

        for entries in self._by_type.values():
            entries.sort(key=lambda e: e.id)  # determinism contract (decision 8)
        return True

    # -- integrity ---------------------------------------------------------

    def _check_references(self, report: LoadReport) -> None:
        """Dangling *_ids references are reported, not fatal (decision M-refs).

        A pack may legitimately reference an optional companion pack. The
        resolver drops unresolvable references at selection time.
        """
        for entry in sorted(self._entries.values(), key=lambda e: e.id):
            for verb in RULE_VERBS:
                block = getattr(entry, verb)
                for raw_key, raw_value in block.items():
                    key, _ = split_rule_key(raw_key)
                    if key not in ENTRY_ID_KEYS:
                        continue
                    for value in rule_values(raw_value):
                        if value not in self._entries:
                            report.add(
                                Diagnostic(
                                    code=ErrorCode.MISSING_REFERENCE,
                                    severity=Severity.WARN,
                                    message=f"{verb}.{raw_key} references unknown "
                                    f"entry id '{value}'; the reference is ignored",
                                    pack_id=entry.pack_id,
                                    source_file=entry.source_file,
                                    entry_id=entry.id,
                                )
                            )

    def _check_rule_keys(self, report: LoadReport) -> None:
        """Unknown rule keys are almost always typos, so surface them loudly."""
        for entry in sorted(self._entries.values(), key=lambda e: e.id):
            for verb in RULE_VERBS:
                for raw_key in getattr(entry, verb):
                    if split_rule_key(raw_key)[0] not in RULE_KEYS:
                        report.add(
                            Diagnostic(
                                code=ErrorCode.VALIDATION,
                                severity=Severity.WARN,
                                message=f"{verb}.{raw_key} is not a known rule key "
                                "and will never match",
                                pack_id=entry.pack_id,
                                source_file=entry.source_file,
                                entry_id=entry.id,
                            )
                        )

    # -- lookup ------------------------------------------------------------

    def get(self, entry_id: str) -> Entry | None:
        return self._entries.get(entry_id)

    def has(self, entry_id: str) -> bool:
        return entry_id in self._entries

    def pack(self, pack_id: str) -> Pack | None:
        return self._packs.get(pack_id)

    def pack_multiplier(self, entry: Entry) -> float:
        pack = self._packs.get(entry.pack_id)
        return pack.manifest.multiplier if pack else 1.0

    @property
    def enabled_pack_ids(self) -> frozenset[str]:
        return frozenset(self._enabled_packs)

    def set_enabled_packs(self, pack_ids: set[str]) -> None:
        self._enabled_packs = {p for p in pack_ids if p in self._packs}

    def get_enabled_entries(
        self,
        type_name: str,
        *,
        enabled_packs: frozenset[str] | None = None,
        include_fallbacks: bool = False,
    ) -> list[Entry]:
        """Selectable entries of one type, sorted by id for deterministic choice."""
        packs = self.enabled_pack_ids if enabled_packs is None else enabled_packs
        return [
            entry
            for entry in self._by_type.get(type_name, ())
            if entry.enabled
            and entry.pack_id in packs
            and (include_fallbacks or not entry.is_fallback)
        ]

    def get_fallback_entries(
        self, type_name: str, *, enabled_packs: frozenset[str] | None = None
    ) -> list[Entry]:
        packs = self.enabled_pack_ids if enabled_packs is None else enabled_packs
        return [
            entry
            for entry in self._by_type.get(type_name, ())
            if entry.enabled and entry.is_fallback and entry.pack_id in packs
        ]

    def counts_by_type(self) -> dict[str, int]:
        return {
            spec.type_name: len(self._by_type.get(spec.type_name, ()))
            for spec in CONTENT_TYPES
        }

    def pack_ids(self) -> frozenset[str]:
        """Every pack that loaded, enabled or not."""
        return frozenset(self._packs)

    def missing_types(self) -> list[str]:
        """Content types a pack promised in its manifest but did not deliver.

        Not "types nobody shipped". Since v2 there are twenty-three content types
        and no pack is expected to carry them all -- a character pack ships a
        character, a world pack ships places, and the base pack ships neither
        camera presets nor lighting. An absent type is a shape of pack, not a
        fault. A *declared* type with no entries behind it is a broken pack, and
        that is what this reports.
        """
        promised: set[str] = set()
        for pack_id in self._enabled_packs:
            pack = self._packs.get(pack_id)
            if pack is not None:
                promised.update(pack.manifest.content_types)
        return sorted(
            spec.type_name
            for spec in CONTENT_TYPES
            if spec.content_type in promised
            and not self.get_enabled_entries(spec.type_name)
        )

    def __len__(self) -> int:
        return len(self._entries)


def build_registry(packs: list[Pack], report: LoadReport) -> ContentRegistry:
    registry = ContentRegistry()
    registry.register_packs(packs, report)
    return registry
