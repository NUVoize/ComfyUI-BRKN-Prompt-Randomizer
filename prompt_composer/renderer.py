"""Prompt rendering (blueprint 21-23, 25).

The renderer never invents text. Every fragment comes from an entry's `prompt`
(or its `model_prompts` override), from an entry's metadata, or from the user's
own prefix/suffix/required terms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .constants import (
    DERIVED_TEMPLATE_VARS,
    MANDATORY_NEGATIVE_TERMS,
    MODEL_TEMPLATE_MAP,
    RESOLUTION_ORDER,
    TYPES_BY_SECTION,
)
from .errors import ErrorCode, Severity, TemplateError
from .resolver import Resolution
from .templates import Template, load_templates, render_body

# Which sections feed each of the four sub-prompt outputs (blueprint 25).
SUB_PROMPTS: dict[str, tuple[str, ...]] = {
    "character": (
        "character", "body", "figure", "skin", "eyes", "face_shape", "features",
        "brows", "lips", "hair_color", "hair_style", "face_paint", "figure", "body_art",
        "head_position", "gaze", "expression",
    ),
    "fashion": (
        "fashion", "footwear", "outerwear", "accessory",
        "eyewear", "headwear", "bag", "handheld",
    ),
    "scene": ("environment", "location", "atmosphere", "action", "pose", "props"),
    "presentation": ("style", "camera", "lighting"),
}


@dataclass
class RenderedPrompt:
    positive: str = ""
    negative: str = ""
    character_prompt: str = ""
    fashion_prompt: str = ""
    scene_prompt: str = ""
    presentation_prompt: str = ""
    summary: str = ""
    template_id: str = ""
    prompt_length: str = ""
    variables: dict[str, object] = field(default_factory=dict)


class PromptRenderer:
    def __init__(self, templates_dir: Path) -> None:
        self.templates: dict[str, Template] = load_templates(templates_dir)

    def template_for(self, resolution: Resolution) -> Template:
        model = resolution.settings.target_model
        template_id = MODEL_TEMPLATE_MAP[model]
        template = self.templates.get(template_id)
        if template is None:
            raise TemplateError(
                f"Target model '{model.value}' maps to template '{template_id}', "
                "which is not installed."
            )
        return template

    def render(self, resolution: Resolution) -> RenderedPrompt:
        template = self.template_for(resolution)
        values = build_variables(resolution, template.template_id)

        requested = resolution.settings.prompt_length
        body, used = template.body_for(requested)
        if used is not requested:
            resolution.context.warn(
                ErrorCode.TEMPLATE,
                Severity.WARN,
                f"Template '{template.name}' does not support the '{requested.value}' "
                f"length; '{used.value}' was used instead.",
            )

        positive = render_body(
            body, values, template.separator, template.sentence_case
        )
        negative = render_negative(resolution, template, values)

        return RenderedPrompt(
            positive=positive,
            negative=negative,
            character_prompt=_sub_prompt(resolution, "character", template.template_id),
            fashion_prompt=_sub_prompt(resolution, "fashion", template.template_id),
            scene_prompt=_sub_prompt(resolution, "scene", template.template_id),
            presentation_prompt=_sub_prompt(resolution, "presentation", template.template_id),
            summary=build_summary(resolution),
            template_id=template.template_id,
            prompt_length=used.value,
            variables=dict(values),
        )


# --------------------------------------------------------------------------
# Variables
# --------------------------------------------------------------------------


def build_variables(resolution: Resolution, template_id: str) -> dict[str, object]:
    """Assemble every template variable from the resolved selections.

    Driven by the content-type registry: a new type with a `template_var` starts
    populating its variable with no change here.
    """
    settings = resolution.settings
    values: dict[str, object] = {}

    for spec in RESOLUTION_ORDER:
        if not spec.template_var:
            continue
        entries = resolution.entries(spec.section or "")
        if spec.multi:
            values[spec.template_var] = [
                e.prompt_for_model(template_id) for e in entries if e.prompt
            ]
        else:
            entry = entries[0] if entries else None
            values[spec.template_var] = entry.prompt_for_model(template_id) if entry else ""

    for variable, section, field_name in DERIVED_TEMPLATE_VARS:
        entry = resolution.entry(section)
        values[variable] = str(entry.metadata.get(field_name, "")) if entry else ""

    for key, value in resolution.conditions.items():
        values[key] = value

    values["prefix"] = settings.prefix.strip()
    values["suffix"] = settings.suffix.strip()
    values["required_terms"] = settings.required_terms.strip()

    return values


# --------------------------------------------------------------------------
# Negative prompt
# --------------------------------------------------------------------------


def collect_negatives(resolution: Resolution) -> list[str]:
    """Every selected entry's negative_prompt, in resolution order."""
    terms: list[str] = []
    for spec in RESOLUTION_ORDER:
        for entry in resolution.entries(spec.section or ""):
            if entry.negative_prompt:
                terms.append(entry.negative_prompt)
    return terms


def render_negative(
    resolution: Resolution,
    template: Template,
    values: Mapping[str, object],
) -> str:
    entry_negatives = ", ".join(collect_negatives(resolution))
    negative_values = dict(values)
    negative_values["entry_negative_prompts"] = entry_negatives
    negative_values["user_negative_prompt"] = resolution.settings.negative_prompt.strip()

    rendered = render_body(template.negative_template, negative_values, ", ")
    merged = ", ".join(filter(None, [rendered, ", ".join(MANDATORY_NEGATIVE_TERMS)]))
    return dedupe_terms(merged)


def dedupe_terms(text: str) -> str:
    """Comma-separated dedup: case-insensitive comparison, first-seen order and
    original casing preserved (decision M6)."""
    seen: set[str] = set()
    kept: list[str] = []
    for raw in text.split(","):
        term = " ".join(raw.split())
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(term)
    return ", ".join(kept)


# --------------------------------------------------------------------------
# Sub-prompts and summary
# --------------------------------------------------------------------------


def _sub_prompt(resolution: Resolution, group: str, template_id: str) -> str:
    fragments: list[str] = []
    for section in SUB_PROMPTS[group]:
        for entry in resolution.entries(section):
            text = entry.prompt_for_model(template_id)
            if text:
                fragments.append(text)

    if group == "presentation":
        style = resolution.entry("style")
        if style:
            for key in ("finish", "color_treatment"):
                value = str(style.metadata.get(key, "")).strip()
                if value:
                    fragments.append(value)
    if group == "scene":
        for key in ("time", "season", "weather"):
            value = resolution.conditions.get(key, "")
            if value:
                fragments.append(value)

    from .templates import cleanup

    return cleanup(", ".join(fragments))


def build_summary(resolution: Resolution) -> str:
    """Human-readable summary output (blueprint 25)."""
    lines = [f"seed: {resolution.settings.seed}"]
    for spec in RESOLUTION_ORDER:
        section = spec.section or spec.type_name
        entries = resolution.entries(section)
        labels = ", ".join(e.label for e in entries) if entries else "-"
        result = resolution.context.results.get(section)
        flags = []
        if result and result.skipped:
            flags.append("off")
        elif result and result.locked:
            # A lock finds its entry by id whatever the enabled packs are, so a
            # locked section can resolve while its pool is empty. Saying
            # "not in packs" next to the thing it just selected is a lie.
            flags.append("locked")
        elif result and result.pool_total == 0:
            flags.append("not in packs")
        elif result and result.pool_total:
            # How much of this section was actually reachable. A gate that can
            # never match shows up here as 3/21 and nowhere else.
            flags.append(f"{result.candidate_count}/{result.pool_total}")
        if result and result.fallback_step >= 4 and result.pool_total:
            flags.append(f"fallback step {result.fallback_step}")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(f"{section:12} {labels}{suffix}")

    conditions = ", ".join(f"{k}={v}" for k, v in resolution.conditions.items() if v)
    if conditions:
        lines.append(f"{'conditions':12} {conditions}")

    from .errors import Severity

    notes = [d for d in resolution.diagnostics if d.severity is Severity.INFO]
    if notes:
        lines.append("")
        lines.extend(f"note: {d.message}" for d in notes)
    return "\n".join(lines)


def format_warnings(resolution: Resolution) -> str:
    """Warnings output: one line per diagnostic, severity spelled out in text so
    it never depends on colour alone (blueprint 6, accessibility).

    INFO is deliberately excluded. A warnings box that says something on every
    single generation is a warnings box nobody reads, and then the one run that
    genuinely needed attention scrolls past unnoticed. Informational diagnostics
    surface in `summary`, which is where you look when you are curious rather
    than when something is wrong.
    """
    from .errors import Severity, sort_diagnostics

    notable = [d for d in resolution.diagnostics if d.severity is not Severity.INFO]
    if not notable:
        return ""
    return "\n".join(d.format() for d in sort_diagnostics(notable))
