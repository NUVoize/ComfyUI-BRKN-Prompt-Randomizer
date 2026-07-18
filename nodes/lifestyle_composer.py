"""The Lifestyle Prompt Composer node.

A thin adapter. Everything below is translation between ComfyUI's widget types
and `prompt_composer`, which knows nothing about ComfyUI and is tested without it.

Written against the classic INPUT_TYPES / RETURN_TYPES API rather than the V3
schema API (decision 9): classic is stable and still fully supported, so the
minimum ComfyUI version can be stated today instead of deferred.

Known POC limitation: INPUT_TYPES is evaluated once at import, so ComfyUI caches
the dropdown option lists. Adding a pack to packs/user/ therefore needs a ComfyUI
restart. Refreshing dropdowns live is the job of the JS layer, which is out of
POC scope.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from prompt_composer import TEMPLATES_DIR, load_default_registry
from prompt_composer.constants import (
    CONDITIONS_SECTION,
    NONE_OPTION,
    RANDOM_OPTION,
    RESOLUTION_ORDER,
    SEASON_OPTIONS,
    SECTION_GROUPS,
    SEEDED_SECTIONS,
    SKIPPABLE_SECTIONS,
    TIME_OPTIONS,
    WEATHER_OPTIONS,
    CompatibilityMode,
    PromptLength,
    TargetModel,
)
from prompt_composer.context import Settings
from prompt_composer.errors import Severity
from prompt_composer.metadata import (
    build_metadata,
    locks_from_metadata,
    metadata_from_json,
    metadata_to_json,
    selections_from_metadata,
)
from prompt_composer.renderer import PromptRenderer, format_warnings
from prompt_composer.resolver import resolve_prompt

METADATA_TYPE = "PROMPT_COMPOSER_METADATA"
PACK_SELECTION_TYPE = "BRKN_PACK_SELECTION"
MAX_SEED = 2**63 - 1

# Loaded once at import. A pack that fails validation is reported and skipped;
# it must not stop the node from registering.
REGISTRY, LOAD_REPORT = load_default_registry()
RENDERER = PromptRenderer(TEMPLATES_DIR)

# Sections the user can lock from a dropdown. Multi-select sections are excluded:
# a lock is a single id, and props follow the action.
LOCKABLE = tuple(
    spec for spec in RESOLUTION_ORDER if spec.section and not spec.multi
)


def _entry_options(type_name: str) -> list[str]:
    """Dropdown values are entry ids: namespaced, unambiguous, and stable."""
    entries = REGISTRY.get_enabled_entries(type_name)
    return [RANDOM_OPTION] + [e.id for e in entries]


def _metadata_options(type_name: str, field: str) -> list[str]:
    values = {
        str(e.metadata[field])
        for e in REGISTRY.get_enabled_entries(type_name)
        if e.metadata.get(field)
    }
    return ["any"] + sorted(values)


def _csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _tags(raw: str) -> frozenset[str]:
    return frozenset(t.lower().replace(" ", "_") for t in _csv(raw))


class LifestylePromptComposer:
    CATEGORY = "prompt_composer"
    FUNCTION = "compose"

    RETURN_TYPES = (
        "STRING", "STRING", METADATA_TYPE, "STRING", "STRING",
        "STRING", "STRING", "STRING", "STRING", "STRING", "INT",
    )
    RETURN_NAMES = (
        "positive_prompt", "negative_prompt", "metadata", "metadata_json",
        "summary", "character_prompt", "fashion_prompt", "scene_prompt",
        "presentation_prompt", "warnings", "used_seed",
    )
    OUTPUT_TOOLTIPS = (
        "Rendered positive prompt.",
        "Merged, de-duplicated negative prompt.",
        "Structured metadata for another Prompt Composer node.",
        "The same metadata as JSON. Paste this back into metadata_json to reuse a scene.",
        "Human-readable summary of what was selected.",
        "Character and expression fragment.",
        "Outfit fragment.",
        "Environment, location, action, pose and props fragment.",
        "Style, camera and lighting fragment.",
        "Warnings and conflicts. Empty when everything resolved cleanly.",
        "The seed actually used.",
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        required: dict[str, Any] = {
            "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0x7FFFFFFFFFFFFFFF,
                        # Without this ComfyUI never draws the
                        # fixed/increment/randomize control -- and the seed
                        # then never moves. Thirty queued images, one woman,
                        # thirty times.
                        "control_after_generate": True,
                        "tooltip": "Every section derives its own sub-seed "
                                   "from this. Set control_after_generate to "
                                   "randomize for a fresh scene each run.",
                    },
                ),
            "compatibility_mode": (
                [m.value for m in CompatibilityMode],
                {"default": CompatibilityMode.STRICT.value},
            ),
            "target_model": (
                [m.value for m in TargetModel], {"default": TargetModel.GENERIC.value}
            ),
            "prompt_length": (
                [p.value for p in PromptLength], {"default": PromptLength.STANDARD.value}
            ),
            "visual_category": (
                _metadata_options("visual_style", "visual_category"), {"default": "any"}
            ),
        }

        optional: dict[str, Any] = {}

        # Master switches, first so they sit at the top of the node. Turning one
        # off silences its entire block -- and overrides every dropdown inside it.
        #
        # This is the switch a character LoRA needs. Eleven separate `none`s to
        # stop the prompt describing a face is not something anyone will do twice.
        _BLURB = {
            "character": "Face, body, hair, expression. Turn OFF when a character "
                         "LoRA owns the person.",
            "wardrobe": "Outfit, footwear, outerwear, accessories.",
            "scene": "Environment, location, pose, props. (The action always stays.)",
            "presentation": "Visual style, camera, lighting. Turn OFF when a style "
                            "LoRA owns the look.",
        }
        for group in SECTION_GROUPS:
            optional[f"use_{group}"] = (
                "BOOLEAN",
                {"default": True, "label_on": "roll", "label_off": "off",
                 "tooltip": _BLURB[group]},
            )

        # Who she is, filtered rather than reweighted.
        optional["descent"] = (
            _metadata_options("character", "descent"),
            {"default": "any",
             "tooltip": "Filter the character pool by descent. 'any' keeps the "
                        "full spread; 'unspecified' names no ethnicity at all and "
                        "lets skin, hair and eyes carry the look."},
        )

        # One dropdown per lockable section:
        #   random -> roll it
        #   none   -> switch the section off entirely (nothing rolled, nothing written)
        #   an id  -> lock it
        #
        # `none` exists because a character LoRA already carries the face. A rolled
        # hairstyle then fights the weights rather than helping them -- the pixie
        # cut that ruins the render was put there by the prompt, not the model.
        for spec in LOCKABLE:
            options = _entry_options(spec.type_name)
            if spec.section in SKIPPABLE_SECTIONS:
                options = [RANDOM_OPTION, NONE_OPTION, *options[1:]]
            optional[spec.section] = (options, {"default": RANDOM_OPTION})

        optional["action_family"] = (
            _metadata_options("action", "action_family"), {"default": "any"}
        )
        optional["time"] = (
            [RANDOM_OPTION, NONE_OPTION, *TIME_OPTIONS], {"default": RANDOM_OPTION}
        )
        optional["season"] = (
            [NONE_OPTION, RANDOM_OPTION, *SEASON_OPTIONS], {"default": NONE_OPTION}
        )
        optional["weather"] = (
            [NONE_OPTION, RANDOM_OPTION, *WEATHER_OPTIONS], {"default": NONE_OPTION}
        )

        optional["prefix"] = ("STRING", {"default": "", "multiline": True})
        optional["suffix"] = ("STRING", {"default": "", "multiline": True})
        optional["required_terms"] = ("STRING", {"default": "", "multiline": False})
        optional["excluded_terms"] = (
            "STRING",
            {"default": "", "multiline": False,
             "tooltip": "Comma-separated tags to exclude, e.g. garage, sleepwear"},
        )
        optional["additional_negative_prompt"] = (
            "STRING", {"default": "", "multiline": True}
        )
        optional["enabled_packs"] = (
            "STRING",
            {"default": "", "multiline": False,
             "tooltip": "Comma-separated pack ids. Empty means every loaded pack."},
        )

        # Reroll one section without disturbing the rest (decision B7). Bumping a
        # parent's counter cascades to its children, per blueprint 18.
        for section in SEEDED_SECTIONS:
            optional[f"{section}_reroll"] = (
                "INT", {"default": 0, "min": 0, "max": 10_000}
            )

        optional["metadata_json"] = (
            "STRING",
            {"default": "", "multiline": True,
             "tooltip": "Paste metadata_json from a previous run to reuse its scene."},
        )
        optional["reuse_from_metadata"] = (
            ["off", "lock_content", "lock_everything"], {"default": "off"}
        )
        optional["metadata_in"] = (METADATA_TYPE,)
        optional["pack_selection"] = (PACK_SELECTION_TYPE,)

        return {"required": required, "optional": optional}

    # ----------------------------------------------------------------------

    def compose(
        self,
        seed: int,
        compatibility_mode: str,
        target_model: str,
        prompt_length: str,
        visual_category: str,
        metadata_in: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple:
        previous = self._previous_metadata(kwargs.get("metadata_json", ""), metadata_in)
        settings = self._settings(
            seed, compatibility_mode, target_model, prompt_length,
            visual_category, previous, kwargs,
        )

        resolution = resolve_prompt(
            REGISTRY, settings, previous=selections_from_metadata(previous)
        )
        rendered = RENDERER.render(resolution)
        metadata = build_metadata(resolution, rendered, REGISTRY)

        warnings = format_warnings(resolution)
        if LOAD_REPORT.diagnostics:
            pack_issues = "\n".join(
                d.format()
                for d in LOAD_REPORT.diagnostics
                if d.severity is not Severity.INFO
            )
            warnings = "\n".join(filter(None, [pack_issues, warnings]))

        return (
            rendered.positive,
            rendered.negative,
            metadata,
            metadata_to_json(metadata),
            rendered.summary,
            rendered.character_prompt,
            rendered.fashion_prompt,
            rendered.scene_prompt,
            rendered.presentation_prompt,
            warnings,
            seed,
        )

    # -- input translation -------------------------------------------------

    @staticmethod
    def _previous_metadata(
        metadata_json: str, metadata_in: dict[str, Any] | None
    ) -> dict[str, Any]:
        # The socket wins when connected; the pasted JSON is the fallback, and is
        # what survives a workflow save (a custom-type socket does not).
        if isinstance(metadata_in, dict) and metadata_in:
            return metadata_in
        return metadata_from_json(metadata_json)

    @staticmethod
    def _pack_selection(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                import json
                parsed = json.loads(raw)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _selection_pack_ids(selection: dict[str, Any]) -> list[str]:
        if not selection:
            return []
        for key in ("enabled_pack_ids", "expanded_allowed_pack_ids", "allowed_pack_ids"):
            value = selection.get(key)
            if isinstance(value, list):
                excluded = set(selection.get("excluded_pack_ids", []))
                return [str(pack_id) for pack_id in value if str(pack_id) and str(pack_id) not in excluded]
        return []
    def _settings(
        self,
        seed: int,
        compatibility_mode: str,
        target_model: str,
        prompt_length: str,
        visual_category: str,
        previous: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> Settings:
        locks: dict[str, str] = {}
        skipped: set[str] = set()
        pack_selection = self._pack_selection(kwargs.get("pack_selection"))

        conditions = {
            "time": kwargs.get("time", RANDOM_OPTION),
            "season": kwargs.get("season", NONE_OPTION),
            "weather": kwargs.get("weather", NONE_OPTION),
        }

        reuse = kwargs.get("reuse_from_metadata", "off")
        if reuse != "off" and previous:
            from prompt_composer.metadata import (
                CONTENT_SECTIONS,
                PRESENTATION_SECTIONS,
                skips_from_metadata,
            )

            sections = (
                CONTENT_SECTIONS
                if reuse == "lock_content"
                else CONTENT_SECTIONS + PRESENTATION_SECTIONS
            )
            locks.update(locks_from_metadata(previous, sections))
            skipped.update(skips_from_metadata(previous, sections))

            restored = previous.get("conditions", {})
            defaults = {"time": RANDOM_OPTION, "season": NONE_OPTION,
                        "weather": NONE_OPTION}
            for key, default in defaults.items():
                if conditions[key] == default and key in restored:
                    conditions[key] = restored[key] or NONE_OPTION

        # A block switched off beats everything inside it: the dropdowns under it
        # are moot, and so is any lock restored from metadata.
        for group, sections in SECTION_GROUPS.items():
            if kwargs.get(f"use_{group}", True):
                continue
            for section in sections:
                if section in SKIPPABLE_SECTIONS:
                    skipped.add(section)
                    locks.pop(section, None)

        # Browser-pinned entries are treated as locks, then explicit dropdowns
        # below may still override them for workflow-level control.
        if pack_selection:
            type_to_section = {spec.type_name: spec.section for spec in LOCKABLE}
            for entry_id in pack_selection.get("pinned_entry_ids", []):
                parts = str(entry_id).split(".", 2)
                if len(parts) != 3:
                    continue
                section = type_to_section.get(parts[1])
                if section and section not in skipped:
                    locks[section] = str(entry_id)
        # An explicit dropdown choice always beats a restored lock.
        for spec in LOCKABLE:
            chosen = kwargs.get(spec.section, RANDOM_OPTION)
            if not chosen or chosen == RANDOM_OPTION:
                continue
            if chosen == NONE_OPTION and spec.section in SKIPPABLE_SECTIONS:
                skipped.add(spec.section)
                locks.pop(spec.section, None)  # `none` also overrides a restored lock
                continue
            if spec.section in skipped:
                continue  # its block is off; a lock underneath it means nothing
            locks[spec.section] = chosen

        revisions = {
            section: int(kwargs.get(f"{section}_reroll", 0) or 0)
            for section in SEEDED_SECTIONS
        }

        metadata_filters: dict[str, dict[str, str]] = {}

        descent = kwargs.get("descent", "any")
        if descent != "any":
            metadata_filters["character"] = {"descent": descent}

        if visual_category != "any":
            metadata_filters["style"] = {"visual_category": visual_category}
        action_family = kwargs.get("action_family", "any")
        if action_family != "any":
            metadata_filters["action"] = {"action_family": action_family}

        selection_packs = self._selection_pack_ids(pack_selection)
        packs = selection_packs or _csv(kwargs.get("enabled_packs", ""))

        return Settings(
            seed=int(seed),
            compatibility_mode=CompatibilityMode(compatibility_mode),
            prompt_length=PromptLength(prompt_length),
            target_model=TargetModel(target_model),
            locks=locks,
            skipped=frozenset(skipped),
            revisions=revisions,
            enabled_packs=frozenset(packs) if packs else None,
            excluded_tags=_tags(kwargs.get("excluded_terms", "")),
            metadata_filters=metadata_filters,
            time=conditions["time"],
            season=conditions["season"],
            weather=conditions["weather"],
            prefix=kwargs.get("prefix", ""),
            suffix=kwargs.get("suffix", ""),
            required_terms=kwargs.get("required_terms", ""),
            negative_prompt=kwargs.get("additional_negative_prompt", ""),
        )


NODE_CLASS_MAPPINGS = {"LifestylePromptComposer": LifestylePromptComposer}
NODE_DISPLAY_NAME_MAPPINGS = {"LifestylePromptComposer": "BRKN Lifestyle Prompt Composer"}

__all__ = [
    "CONDITIONS_SECTION",
    "LifestylePromptComposer",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
]



