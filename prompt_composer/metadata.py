"""Structured metadata (blueprint 8, 25).

Metadata is the workflow's memory. It carries the selected ids, their pack and
version provenance, the section seeds and revisions, and every diagnostic -- so a
prompt can be reproduced, audited, or partially re-rendered later.

The presentation-swap flow (core flow 4) is exactly this: read the content
sections back out of a previous run's metadata, lock them, change the style.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .constants import RESOLUTION_ORDER, TYPES_BY_SECTION
from .errors import sort_diagnostics
from .registry import ContentRegistry
from .renderer import RenderedPrompt
from .resolver import Resolution

METADATA_VERSION = "1.0"

# Sections that describe *what* is in the image, as opposed to how it looks.
# Locking these and changing the style is the presentation swap.
CONTENT_SECTIONS: tuple[str, ...] = (
    "character",
    # the face, split out of `character` so a LoRA can own it
    "body",
    "figure",
    "skin",
    "eyes",
    "face_shape",
    "features",
    "brows",
    "lips",
    "hair_color",
    "hair_style",
    "face_paint",
    "figure",
    "body_art",
    "environment",
    "location",
    "atmosphere",
    "action",
    "pose",
    "fashion",
    "footwear",
    "outerwear",
    "accessory",
    "eyewear",
    "headwear",
    "bag",
    "handheld",
    "props",
    "head_position",
    "gaze",
    "expression",
)
PRESENTATION_SECTIONS: tuple[str, ...] = ("style", "camera", "lighting")


def build_metadata(
    resolution: Resolution,
    rendered: RenderedPrompt | None = None,
    registry: ContentRegistry | None = None,
) -> dict[str, Any]:
    settings = resolution.settings

    sections: dict[str, Any] = {}
    for spec in RESOLUTION_ORDER:
        section = spec.section or spec.type_name
        result = resolution.context.results.get(section)
        entries = resolution.entries(section)
        sections[section] = {
            "ids": [e.id for e in entries],
            "labels": [e.label for e in entries],
            "locked": bool(result and result.locked),
            "skipped": bool(result and result.skipped),
            "fallback_step": result.fallback_step if result else 0,
            "candidate_count": result.candidate_count if result else 0,
            "seed": result.seed if result else 0,
            "revision": result.revision if result else 0,
            "provenance": [_provenance(e.pack_id, registry) for e in entries],
        }

    metadata: dict[str, Any] = {
        "metadata_version": METADATA_VERSION,
        "seed": settings.seed,
        "compatibility_mode": settings.compatibility_mode.value,
        "prompt_length": settings.prompt_length.value,
        "target_model": settings.target_model.value,
        "sections": sections,
        "conditions": dict(resolution.conditions),
        "warnings": [
            {
                "code": d.code.value,
                "severity": d.severity.value,
                "message": d.message,
                "section": d.section,
                "entry_id": d.entry_id,
            }
            for d in sort_diagnostics(resolution.diagnostics)
        ],
        "timings_ms": {"resolve": round(resolution.duration_ms, 3)},
    }

    if rendered is not None:
        metadata["template_id"] = rendered.template_id
        metadata["rendered_length"] = rendered.prompt_length
        metadata["positive_prompt"] = rendered.positive
        metadata["negative_prompt"] = rendered.negative

    return metadata


def _provenance(pack_id: str, registry: ContentRegistry | None) -> dict[str, str]:
    pack = registry.pack(pack_id) if registry else None
    return {
        "pack_id": pack_id,
        "version": pack.manifest.version if pack else "",
    }


def metadata_to_json(metadata: Mapping[str, Any]) -> str:
    return json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False)


def metadata_from_json(text: str) -> dict[str, Any]:
    """Parse metadata handed back in by the user. Never raises on junk input --
    an unusable string simply yields nothing to restore."""
    if not text or not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def selections_from_metadata(metadata: Mapping[str, Any]) -> dict[str, list[str]]:
    """Every section's selected ids, for the resolver's `previous` argument."""
    sections = metadata.get("sections")
    if not isinstance(sections, Mapping):
        return {}
    out: dict[str, list[str]] = {}
    for section, payload in sections.items():
        if section not in TYPES_BY_SECTION or not isinstance(payload, Mapping):
            continue
        ids = payload.get("ids")
        if isinstance(ids, list):
            out[section] = [str(i) for i in ids if isinstance(i, str)]
    return out


def skips_from_metadata(
    metadata: Mapping[str, Any],
    sections: tuple[str, ...] = CONTENT_SECTIONS,
) -> set[str]:
    """Sections that were switched off in the run this metadata came from.

    Without this, reusing a scene would quietly switch its skipped sections back
    on -- you would reproduce the shot and find a hairstyle in it that you had
    deliberately turned off, fighting the LoRA all over again.
    """
    out: set[str] = set()
    for section, block in (metadata.get("sections") or {}).items():
        if section in sections and isinstance(block, Mapping) and block.get("skipped"):
            out.add(section)
    return out


def locks_from_metadata(
    metadata: Mapping[str, Any],
    sections: tuple[str, ...] = CONTENT_SECTIONS,
) -> dict[str, str]:
    """Turn a previous run's selections into locks.

    Multi-select sections (props) are skipped: a lock is a single id, and props
    follow the action anyway.
    """
    locks: dict[str, str] = {}
    for section, ids in selections_from_metadata(metadata).items():
        if section not in sections or not ids:
            continue
        spec = TYPES_BY_SECTION.get(section)
        if spec is None or spec.multi:
            continue
        locks[section] = ids[0]
    return locks
