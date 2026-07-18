"""Prompt templates and their mini-language (blueprint 21).

Deliberately not a template engine. No expressions, no arbitrary code, no
attribute access -- blueprint 6 forbids it, and a template is data that may
arrive from a pack. The whole grammar is:

    {var}                      substitute
    {var|join:", "}            join a list
    {var|default:"text"}       substitute, or this text when empty
    [[if var]]...[[endif]]     include only when var is non-empty

One filter per variable, no nested conditionals. Anything else is PCT500.

Templates are validated when loaded, not when rendered: a template that uses an
optional variable without guarding it is rejected up front. That is what makes
the "no unresolved variables" guarantee hold by construction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .constants import OPTIONAL_TEMPLATE_VARS, PromptLength
from .errors import TemplateError

# {name} or {name|filter:"arg"} / {name|filter:', '}
VARIABLE_RE = re.compile(
    r"\{(?P<name>[a-z_][a-z0-9_]*)"
    r"(?:\|(?P<filter>join|default):(?P<arg>\"[^\"]*\"|'[^']*'))?\}"
)
CONDITIONAL_RE = re.compile(
    r"\[\[if\s+(?P<name>[a-z_][a-z0-9_]*)\s*\]\](?P<body>.*?)\[\[endif\]\]",
    re.DOTALL,
)
# Anything that looks like our syntax but did not match the strict forms above.
SUSPECT_RE = re.compile(r"\{[^}]*\}|\[\[(?!if\s|endif\]).*?\]\]")
# Anything brace-shaped left after substitution: malformed, unknown, or unbalanced.
RESIDUE_RE = re.compile(r"\{[^}]*\}|\[\[[^\]]*\]\]")

_LENGTH_ORDER: tuple[PromptLength, ...] = (
    PromptLength.COMPACT,
    PromptLength.STANDARD,
    PromptLength.DETAILED,
)


@dataclass(frozen=True)
class Template:
    template_id: str
    name: str
    supported_lengths: tuple[PromptLength, ...]
    positive_templates: Mapping[PromptLength, str]
    negative_template: str
    separator: str = " "
    omit_empty_sections: bool = True
    sentence_case: bool = False

    def body_for(self, length: PromptLength) -> tuple[str, PromptLength]:
        """Return the body, falling back to the nearest supported length (M12)."""
        if length in self.positive_templates:
            return self.positive_templates[length], length

        index = _LENGTH_ORDER.index(length)
        ranked = sorted(
            self.positive_templates,
            key=lambda candidate: abs(_LENGTH_ORDER.index(candidate) - index),
        )
        nearest = ranked[0]
        return self.positive_templates[nearest], nearest


def load_template(path: Path) -> Template:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateError(f"Could not read template {path.name}: {exc}") from exc

    try:
        bodies = {
            PromptLength(key): value
            for key, value in document["positive_templates"].items()
        }
        template = Template(
            template_id=document["template_id"],
            name=document["name"],
            supported_lengths=tuple(
                PromptLength(v) for v in document["supported_lengths"]
            ),
            positive_templates=bodies,
            negative_template=document["negative_template"],
            separator=document.get("separator", " "),
            omit_empty_sections=document.get("omit_empty_sections", True),
            sentence_case=document.get("sentence_case", False),
        )
    except (KeyError, ValueError) as exc:
        raise TemplateError(f"Malformed template {path.name}: {exc}") from exc

    for length, body in template.positive_templates.items():
        _validate_body(template.template_id, str(length.value), body)
    _validate_body(template.template_id, "negative", template.negative_template)
    return template


def load_templates(directory: Path) -> dict[str, Template]:
    if not directory.is_dir():
        raise TemplateError(f"Template directory not found: {directory}")
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.json")):
        template = load_template(path)
        templates[template.template_id] = template
    if not templates:
        raise TemplateError(f"No templates found in {directory}")
    return templates


def _validate_body(template_id: str, label: str, body: str) -> None:
    """Reject unsupported syntax and unguarded optional variables."""
    guarded: set[str] = set()
    for match in CONDITIONAL_RE.finditer(body):
        guarded.add(match.group("name"))
        if "[[if" in match.group("body"):
            raise TemplateError(
                f"{template_id}/{label}: nested [[if]] blocks are not supported"
            )

    stripped = CONDITIONAL_RE.sub("", body)
    if "[[if" in stripped or "[[endif]]" in stripped:
        raise TemplateError(f"{template_id}/{label}: unbalanced [[if]] / [[endif]]")

    for match in VARIABLE_RE.finditer(body):
        name = match.group("name")
        has_default = match.group("filter") == "default"
        if name in OPTIONAL_TEMPLATE_VARS and name not in guarded and not has_default:
            raise TemplateError(
                f"{template_id}/{label}: '{{{name}}}' can be empty, so it must sit "
                "inside an [[if]] block or carry a |default filter"
            )

    residue = VARIABLE_RE.sub("", CONDITIONAL_RE.sub("", body))
    bad = SUSPECT_RE.findall(residue)
    if bad:
        raise TemplateError(
            f"{template_id}/{label}: unsupported template syntax {bad[0]!r}"
        )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

Values = Mapping[str, str | Sequence[str]]


def render_body(
    body: str,
    values: Values,
    separator: str = ", ",
    sentence_case: bool = False,
) -> str:
    """Substitute, then clean up. Unknown variables render as empty, never as
    literal braces -- a template referencing a variable that no content type
    supplies should quietly disappear, not leak `{hairstyle}` into a prompt."""

    def conditional(match: re.Match[str]) -> str:
        return match.group("body") if _truthy(values.get(match.group("name"))) else ""

    body = CONDITIONAL_RE.sub(conditional, body)

    def variable(match: re.Match[str]) -> str:
        name = match.group("name")
        filter_name = match.group("filter")
        arg = _unquote(match.group("arg"))
        value = values.get(name)

        if filter_name == "join":
            items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
            return (arg or separator).join(str(v) for v in items if v)

        text = _flatten(value, separator)
        if filter_name == "default" and not text:
            return arg or ""
        return text

    text = VARIABLE_RE.sub(variable, body)
    text = RESIDUE_RE.sub("", text)  # malformed syntax is dropped, never printed
    text = cleanup(text)
    return capitalize_sentences(text) if sentence_case else text


_CLEANUP_STEPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[ \t]+"), " "),
    (re.compile(r"\s+([,.;:!?])"), r"\1"),      # " ," -> ","
    (re.compile(r"([,;:])\s*(?=[.!?])"), ""),   # ", ." -> "."
    (re.compile(r"([,;:])\1+"), r"\1"),         # ",," -> ","
    (re.compile(r"([.!?])\1+"), r"\1"),         # ".." -> "."
    (re.compile(r"([.!?])\s*([,;:])"), r"\1"),  # ". ," -> "."
    (re.compile(r"\s+"), " "),
)


def cleanup(text: str) -> str:
    """Remove the punctuation debris that empty variables leave behind (M8)."""
    for pattern, replacement in _CLEANUP_STEPS:
        text = pattern.sub(replacement, text)
    text = text.strip()
    text = re.sub(r"^[,;:.\s]+", "", text)
    text = re.sub(r"[,;:\s]+$", "", text)
    return text.strip()


_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)([a-z])")


def capitalize_sentences(text: str) -> str:
    """Entry prompts are authored as lower-case fragments so they can be dropped
    into any position in any template. Natural-language templates therefore have
    to restore sentence case themselves; fragment templates leave it alone."""
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), text)


def _truthy(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(str(v).strip() for v in value)
    return bool(str(value).strip())


def _flatten(value: Any, separator: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return separator.join(str(v) for v in value if str(v).strip())
    return str(value).strip()


def _unquote(arg: str | None) -> str:
    if not arg:
        return ""
    return arg[1:-1]
