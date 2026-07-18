"""Constants, limits and the declarative content-type registry.

The content-type table below is the single source of truth for every content
type the engine knows about. The loader, registry, rule engine, resolver and
renderer all iterate this table rather than hard-coding type names, so adding a
new entry type (hairstyle, makeup, footwear...) means adding one row here plus
one JSON schema file -- no changes to the engine modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

SCHEMA_VERSION: Final[str] = "1.0"

# --------------------------------------------------------------------------
# Compatibility modes
# --------------------------------------------------------------------------


class CompatibilityMode(str, Enum):
    STRICT = "strict"
    BALANCED = "balanced"
    LOOSE = "loose"


DEFAULT_COMPATIBILITY_MODE: Final[CompatibilityMode] = CompatibilityMode.BALANCED

# Generic fallback entries are disabled in Strict, enabled elsewhere (blueprint 20).
FALLBACK_ENABLED_MODES: Final[frozenset[CompatibilityMode]] = frozenset(
    {CompatibilityMode.BALANCED, CompatibilityMode.LOOSE}
)


class PromptLength(str, Enum):
    COMPACT = "compact"
    STANDARD = "standard"
    DETAILED = "detailed"


class TargetModel(str, Enum):
    GENERIC = "generic"
    QWEN_IMAGE = "qwen_image"
    FLUX_KLEIN = "flux_klein"
    KREA = "krea"


# Decision 10: krea maps to the generic template during the POC.
MODEL_TEMPLATE_MAP: Final[dict[TargetModel, str]] = {
    TargetModel.GENERIC: "generic",
    TargetModel.QWEN_IMAGE: "qwen_image",
    TargetModel.FLUX_KLEIN: "flux_klein",
    TargetModel.KREA: "generic",
}

# --------------------------------------------------------------------------
# Rule vocabulary (blueprint 12)
# --------------------------------------------------------------------------

MATCH_SUFFIXES: Final[tuple[str, ...]] = ("_any", "_all", "_none")
DEFAULT_MATCH_SUFFIX: Final[str] = "_any"

RULE_VERBS: Final[tuple[str, ...]] = (
    "requires",
    "allows",
    "prefers",
    "avoids",
    "excludes",
)

HARD_RULE_VERBS: Final[frozenset[str]] = frozenset({"requires", "excludes", "allows"})
SOFT_RULE_VERBS: Final[frozenset[str]] = frozenset({"prefers", "avoids"})

ENTRY_ID_KEYS: Final[tuple[str, ...]] = (
    "character_ids",
    "outfit_ids",
    "garment_ids",
    "environment_ids",
    "parent_environment_ids",
    "location_ids",
    "action_ids",
    "pose_ids",
    "expression_ids",
    "prop_ids",
    "visual_style_ids",
    "camera_preset_ids",
    "lighting_preset_ids",
    # v2 character facets and wardrobe layers
    "body_ids",
    "skin_ids",
    "eyes_ids",
    "face_shape_ids",
    "features_ids",
    "brows_ids",
    "lips_ids",
    "hair_color_ids",
    "hair_style_ids",
    "footwear_ids",
    "outerwear_ids",
    "accessory_ids",
)

TAG_KEYS: Final[tuple[str, ...]] = (
    "character_tags",
    "outfit_tags",
    "garment_tags",
    "environment_tags",
    "location_tags",
    "action_tags",
    "pose_tags",
    "expression_tags",
    "prop_tags",
    "visual_style_tags",
    "camera_tags",
    "lighting_tags",
    "finish_tags",
    # v2 character facets and wardrobe layers
    "body_tags",
    "skin_tags",
    "eyes_tags",
    "face_shape_tags",
    "features_tags",
    "brows_tags",
    "lips_tags",
    "hair_color_tags",
    "hair_style_tags",
    "footwear_tags",
    "outerwear_tags",
    "accessory_tags",
)

CONTEXT_KEYS: Final[tuple[str, ...]] = (
    "time_tags",
    "season_tags",
    "weather_tags",
    "temperature_tags",
    "privacy_tags",
    "activity_tags",
    "mood_tags",
    "color_tags",
    "material_tags",
    "condition_tags",
    "orientation_tags",
)

GLOBAL_KEYS: Final[tuple[str, ...]] = (
    "visual_categories",
    "fashion_categories",
    "environment_categories",
    "action_families",
)

RULE_KEYS: Final[frozenset[str]] = frozenset(
    ENTRY_ID_KEYS + TAG_KEYS + CONTEXT_KEYS + GLOBAL_KEYS
)

# Default score deltas when a prefers/avoids block omits them.
DEFAULT_PREFERENCE_BONUS: Final[float] = 0.5
DEFAULT_AVOIDANCE_PENALTY: Final[float] = 0.5

# Loose mode demotes content exclusions to penalties instead of removals.
LOOSE_DEMOTED_EXCLUSION_PENALTY: Final[float] = 2.0

MINIMUM_SCORE: Final[float] = 0.001
MAX_FALLBACK_ATTEMPTS: Final[int] = 5

PACK_PRIORITY_BASELINE: Final[float] = 100.0
PACK_MULTIPLIER_MIN: Final[float] = 0.1
PACK_MULTIPLIER_MAX: Final[float] = 10.0

# --------------------------------------------------------------------------
# Security limits (blueprint 6 -- given qualitatively, quantified here)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PackLimits:
    max_file_bytes: int = 5 * 1024 * 1024
    max_files_per_pack: int = 64
    max_entries_per_pack: int = 5_000
    max_json_depth: int = 32
    follow_symlinks: bool = False


DEFAULT_LIMITS: Final[PackLimits] = PackLimits()

# --------------------------------------------------------------------------
# Content-type registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentTypeSpec:
    """Everything the engine needs to know about one content type.

    Adding a new type = add a row here + a matching JSON schema file.
    """

    type_name: str
    """Singular value of an entry's `type` field, e.g. "action"."""

    content_type: str
    """Plural value used in manifests and content-file wrappers, e.g. "actions"."""

    schema_file: str
    """Filename inside schemas/ that validates entries of this type."""

    section: str | None
    """Resolver section name. Drives sub-seeds, revision counters and locks.
    None means the type is loaded but not independently resolved."""

    template_var: str | None
    """Template variable this type populates, e.g. "action" -> {action}."""

    order: int
    """Position in the resolution order. Lower resolves first."""

    depends_on: tuple[str, ...] = ()
    """Sections that invalidate this one when they change (blueprint 18)."""

    multi: bool = False
    """True when the resolver may select more than one entry (props)."""

    id_key: str | None = None
    """Rule key holding IDs of this type, e.g. "action_ids"."""

    tag_key: str | None = None
    """Rule key holding tags of this type, e.g. "action_tags"."""


CONTENT_TYPES: Final[tuple[ContentTypeSpec, ...]] = (
    ContentTypeSpec(
        type_name="visual_style",
        content_type="visual_styles",
        schema_file="visual-style.schema.json",
        section="style",
        template_var="visual_style",
        order=10,
        id_key="visual_style_ids",
        tag_key="visual_style_tags",
    ),
    ContentTypeSpec(
        type_name="character",
        content_type="characters",
        schema_file="character.schema.json",
        section="character",
        template_var="character",
        order=20,
        id_key="character_ids",
        tag_key="character_tags",
    ),
    ContentTypeSpec(
        type_name="environment",
        content_type="environments",
        schema_file="environment.schema.json",
        section="environment",
        template_var="environment",
        order=30,
        id_key="environment_ids",
        tag_key="environment_tags",
    ),
    ContentTypeSpec(
        type_name="location",
        content_type="locations",
        schema_file="location.schema.json",
        section="location",
        template_var="location",
        order=40,
        depends_on=("environment",),
        id_key="location_ids",
        tag_key="location_tags",
    ),
    ContentTypeSpec(
        type_name="atmosphere", content_type="atmospheres",
        schema_file="atmosphere.schema.json",
        section="atmosphere", template_var="atmosphere", order=46,
        depends_on=("environment", "location", "conditions"),
        id_key="atmosphere_ids", tag_key="atmosphere_tags",
    ),
    ContentTypeSpec(
        type_name="action",
        content_type="actions",
        schema_file="action.schema.json",
        section="action",
        template_var="action",
        order=50,
        depends_on=("environment", "location"),
        id_key="action_ids",
        tag_key="action_tags",
    ),
    ContentTypeSpec(
        type_name="pose",
        content_type="poses",
        schema_file="pose.schema.json",
        section="pose",
        template_var="pose",
        order=60,
        depends_on=("action",),
        id_key="pose_ids",
        tag_key="pose_tags",
    ),
    ContentTypeSpec(
        type_name="outfit",
        content_type="outfits",
        schema_file="outfit.schema.json",
        section="fashion",
        template_var="fashion",
        order=70,
        depends_on=("character", "environment", "location", "action"),
        id_key="outfit_ids",
        tag_key="outfit_tags",
    ),
    ContentTypeSpec(
        type_name="prop",
        content_type="props",
        schema_file="prop.schema.json",
        section="props",
        template_var="props",
        order=80,
        depends_on=("location", "action"),
        multi=True,
        id_key="prop_ids",
        tag_key="prop_tags",
    ),
    ContentTypeSpec(
        type_name="expression",
        content_type="expressions",
        schema_file="expression.schema.json",
        section="expression",
        template_var="expression",
        order=90,
        depends_on=("action",),
        id_key="expression_ids",
        tag_key="expression_tags",
    ),
    ContentTypeSpec(
        type_name="camera_preset",
        content_type="camera_presets",
        schema_file="camera-preset.schema.json",
        section="camera",
        template_var=None,
        order=100,
        depends_on=("style", "action", "location"),
        id_key="camera_preset_ids",
        tag_key="camera_tags",
    ),
    # ---- Character facets (v2) -------------------------------------------
    # Split out of the character entry so a locked character -- or a character
    # LoRA -- can own the face while the pack still randomises everything else.
    # Each is single-select on purpose: metadata.py excludes `multi` sections
    # from the lock table, so folding the face into one multi-select would make
    # every roll unreproducible.
    #
    # 21-28 carry no rules, so sitting immediately after `character` costs them
    # nothing. `hair_style` is the exception and is placed at 55; see below.
    ContentTypeSpec(
        type_name="body", content_type="bodies", schema_file="body.schema.json",
        section="body", template_var="body", order=21,
        depends_on=("character",),
        id_key="body_ids", tag_key="body_tags",
    ),
    ContentTypeSpec(
        type_name="skin", content_type="skins", schema_file="skin.schema.json",
        section="skin", template_var="skin", order=22,
        depends_on=("character",),
        id_key="skin_ids", tag_key="skin_tags",
    ),
    ContentTypeSpec(
        type_name="eyes", content_type="eyes", schema_file="eyes.schema.json",
        section="eyes", template_var="eyes", order=23,
        depends_on=("character",),
        id_key="eyes_ids", tag_key="eyes_tags",
    ),
    ContentTypeSpec(
        type_name="face_shape", content_type="face_shapes",
        schema_file="face-shape.schema.json",
        section="face_shape", template_var="face_shape", order=24,
        depends_on=("character",),
        id_key="face_shape_ids", tag_key="face_shape_tags",
    ),
    ContentTypeSpec(
        type_name="features", content_type="features",
        schema_file="features.schema.json",
        section="features", template_var="features", order=25,
        depends_on=("character",),
        id_key="features_ids", tag_key="features_tags",
    ),
    ContentTypeSpec(
        type_name="brows", content_type="brows", schema_file="brows.schema.json",
        section="brows", template_var="brows", order=26,
        depends_on=("character",),
        id_key="brows_ids", tag_key="brows_tags",
    ),
    ContentTypeSpec(
        type_name="lips", content_type="lips", schema_file="lips.schema.json",
        section="lips", template_var="lips", order=27,
        depends_on=("character",),
        id_key="lips_ids", tag_key="lips_tags",
    ),
    ContentTypeSpec(
        type_name="hair_color", content_type="hair_colors",
        schema_file="hair-color.schema.json",
        section="hair_color", template_var="hair_color", order=28,
        depends_on=("character",),
        id_key="hair_color_ids", tag_key="hair_color_tags",
    ),
    # Hair *style*, unlike hair colour, is situational: bedhead belongs to a
    # bedroom, a sleek bun to an office, a high ponytail to exercise. Placed at
    # 21-29 with the other facets, every one of those preferences would point at
    # a section not yet resolved -- and would silently invert, biasing the room
    # towards the hair instead of the hair towards the room. It resolves after
    # `action` so its rules point upstream, where rules are meant to point.
    ContentTypeSpec(
        type_name="face_paint", content_type="face_paints",
        schema_file="face_paint.schema.json",
        section="face_paint", template_var="face_paint", order=29,
        depends_on=("character",),
        id_key="face_paint_ids", tag_key="face_paint_tags",
    ),
    ContentTypeSpec(
        type_name="hair_style", content_type="hair_styles",
        schema_file="hair-style.schema.json",
        section="hair_style", template_var="hair_style", order=55,
        depends_on=("character", "environment", "location", "action"),
        id_key="hair_style_ids", tag_key="hair_style_tags",
    ),
    # ---- Wardrobe layers (v2) --------------------------------------------
    # After `outfit` (70), so they can hard-rule on outfit_tags. That is what
    # stops stilettos rolling over pyjamas.
    ContentTypeSpec(
        type_name="footwear", content_type="footwear",
        schema_file="footwear.schema.json",
        section="footwear", template_var="footwear", order=71,
        depends_on=("fashion", "action", "location", "conditions"),
        id_key="footwear_ids", tag_key="footwear_tags",
    ),
    ContentTypeSpec(
        type_name="outerwear", content_type="outerwear",
        schema_file="outerwear.schema.json",
        section="outerwear", template_var="outerwear", order=72,
        depends_on=("fashion", "location", "conditions"),
        id_key="outerwear_ids", tag_key="outerwear_tags",
    ),
    ContentTypeSpec(
        type_name="accessory", content_type="accessories",
        schema_file="accessory.schema.json",
        section="accessory", template_var="accessory", order=73,
        depends_on=("fashion", "action", "location", "environment", "conditions"),
        id_key="accessory_ids", tag_key="accessory_tags",
    ),
    ContentTypeSpec(
        type_name="eyewear", content_type="eyewear",
        schema_file="eyewear.schema.json",
        section="eyewear", template_var="eyewear", order=74,
        depends_on=('fashion', 'location', 'conditions'),
        id_key="eyewear_ids", tag_key="eyewear_tags",
    ),
    ContentTypeSpec(
        type_name="headwear", content_type="headwear",
        schema_file="headwear.schema.json",
        section="headwear", template_var="headwear", order=75,
        depends_on=('fashion', 'location', 'conditions'),
        id_key="headwear_ids", tag_key="headwear_tags",
    ),
    ContentTypeSpec(
        type_name="bag", content_type="bags",
        schema_file="bag.schema.json",
        section="bag", template_var="bag", order=76,
        depends_on=('fashion', 'action', 'location', 'environment', 'conditions'),
        id_key="bag_ids", tag_key="bag_tags",
    ),
    ContentTypeSpec(
        type_name="handheld", content_type="handhelds",
        schema_file="handheld.schema.json",
        section="handheld", template_var="handheld", order=77,
        depends_on=('fashion', 'action', 'location', 'conditions'),
        id_key="handheld_ids", tag_key="handheld_tags",
    ),
    ContentTypeSpec(
        type_name="figure", content_type="figures",
        schema_file="figure.schema.json",
        section="figure", template_var="figure", order=31,
        depends_on=("character", "body"),
        id_key="figure_ids", tag_key="figure_tags",
    ),
    ContentTypeSpec(
        type_name="body_art", content_type="body_arts",
        schema_file="body_art.schema.json",
        section="body_art", template_var="body_art", order=32,
        depends_on=("character",),
        id_key="body_art_ids", tag_key="body_art_tags",
    ),
    ContentTypeSpec(
        type_name="head_position", content_type="head_positions",
        schema_file="head_position.schema.json",
        section="head_position", template_var="head_position", order=88,
        depends_on=("character", "action", "pose"),
        id_key="head_position_ids", tag_key="head_position_tags",
    ),
    ContentTypeSpec(
        type_name="gaze", content_type="gazes",
        schema_file="gaze.schema.json",
        section="gaze", template_var="gaze", order=89,
        depends_on=("action", "pose", "head_position"),
        id_key="gaze_ids", tag_key="gaze_tags",
    ),
    ContentTypeSpec(
        type_name="lighting_preset",
        content_type="lighting_presets",
        schema_file="lighting-preset.schema.json",
        section="lighting",
        template_var="lighting",
        order=110,
        depends_on=("style", "location"),
        id_key="lighting_preset_ids",
        tag_key="lighting_tags",
    ),
)

TYPES_BY_NAME: Final[dict[str, ContentTypeSpec]] = {
    spec.type_name: spec for spec in CONTENT_TYPES
}
TYPES_BY_CONTENT_TYPE: Final[dict[str, ContentTypeSpec]] = {
    spec.content_type: spec for spec in CONTENT_TYPES
}
TYPES_BY_SECTION: Final[dict[str, ContentTypeSpec]] = {
    spec.section: spec for spec in CONTENT_TYPES if spec.section is not None
}

# Every content type registers its own rule vocabulary. These used to be two
# hand-maintained tuples, and they had quietly drifted out of step with the type
# registry -- a rule naming a key that is not in here does not fail loudly, it
# simply never matches, and the gate you wrote is dead while looking alive.
#
# Adding a ContentTypeSpec now adds its keys. There is nothing left to forget.
RULE_KEYS = frozenset(
    RULE_KEYS
    | {spec.id_key for spec in CONTENT_TYPES if spec.id_key}
    | {spec.tag_key for spec in CONTENT_TYPES if spec.tag_key}
)

RESOLUTION_ORDER: Final[tuple[ContentTypeSpec, ...]] = tuple(
    sorted((s for s in CONTENT_TYPES if s.section is not None), key=lambda s: s.order)
)

# Sections that carry a sub-seed and a revision counter (blueprint 16).
# "conditions" resolves time/season/weather, which are settings rather than
# entries, so it has no content type but still needs a deterministic sub-seed.
SEEDED_SECTIONS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys([s.section for s in RESOLUTION_ORDER if s.section] + ["conditions"])
)

# Template variables sourced from an entry's metadata rather than its prompt text
# (decision B5: finish, colour treatment, camera behaviour, aspect ratio and gaze
# are fields of an existing entry, not entity types of their own).
# (variable, section, metadata field)
DERIVED_TEMPLATE_VARS: Final[tuple[tuple[str, str, str], ...]] = (
    ("framing", "camera", "framing"),
    ("camera_angle", "camera", "camera_angle"),
    ("camera_behavior", "style", "camera_behavior"),
    ("finish", "style", "finish"),
    ("color_treatment", "style", "color_treatment"),
    ("aspect_ratio", "style", "aspect_ratio"),
)

# Variables that may legitimately be empty. A template must guard these with an
# [[if]] block or a |default filter; templates.py rejects any that does not, so
# "no unresolved variables" is guaranteed at load time rather than hoped for at
# render time.
# Since v2, essentially everything can be empty: a section may be switched off,
# or simply absent from the enabled packs. `templates.py` refuses to load a
# template that uses one of these unguarded, so "no unresolved variables" stays a
# load-time guarantee rather than a run-time hope -- and a stranded preposition
# ("Cinematic photo of, wearing a coat") becomes impossible to ship.
#
# `action` is in here even though it is not user-skippable: a character-only pack
# ships no actions, and the template must survive that.
OPTIONAL_TEMPLATE_VARS: Final[frozenset[str]] = frozenset(
    {"prefix", "suffix", "required_terms", "props", "time", "season", "weather"}
    | {s.template_var for s in RESOLUTION_ORDER if s.template_var}
    | {var for var, _, _ in DERIVED_TEMPLATE_VARS}
)

# Always appended to the negative prompt. The pack format guarantees every
# character is an adult (schema: metadata.adult must be true); this makes the
# same guarantee to the downstream image model, which cannot read our schema.
MANDATORY_NEGATIVE_TERMS: Final[tuple[str, ...]] = (
    "child", "children", "minor", "teenager", "underage",
)

# Props select this many entries per prompt length (decision M5).
PROP_COUNT_BY_LENGTH: Final[dict[PromptLength, tuple[int, int]]] = {
    PromptLength.COMPACT: (0, 1),
    PromptLength.STANDARD: (1, 1),
    PromptLength.DETAILED: (1, 2),
}

# Time, season and weather are settings rather than entries: no fixture type owns
# them, but the templates need values and the rule vocabulary exposes them as
# context keys. Kept deliberately small.
CONDITIONS_SECTION: Final[str] = "conditions"
CONDITIONS_ORDER: Final[int] = 45  # blueprint 17: after location, before action

# Sections a user may switch off entirely (v2).
#
# `action` is absent on purpose. It is the spine: strip the verb and the prompt
# has nothing to depict, and pose, props, expression and camera all hang off it.
# Everything else may plausibly be carried by a LoRA instead of by text, and a
# rolled description then fights the weights rather than helping them -- which is
# the whole reason this exists.
SKIPPABLE_SECTIONS: Final[frozenset[str]] = frozenset(
    s.section for s in RESOLUTION_ORDER if s.section and s.section != "action"
)

# Attributes of the character, not sections in their own right.
#
# Switch the character off and these must go with her. Leaving them running
# yields a headless description of somebody who was never introduced -- and a
# character LoRA then has to fight nine adjectives that describe a stranger.
#
# `expression` is deliberately absent: it hangs off "the subject", which the
# template still supplies, so it survives a missing character noun intact.
CHARACTER_FACETS: Final[tuple[str, ...]] = (
    "body", "figure", "skin", "eyes", "face_shape", "features",
    "brows", "lips", "hair_color", "hair_style",
)


# Master switches. One click silences a whole block of the prompt.
#
# These mirror the four sub-prompt outputs, because that is the shape the prompt
# is assembled in: who she is, what she wears, where she is, how it is shot.
#
# `action` is deliberately in none of them. It is the spine -- strip the verb and
# the prompt has nothing to depict.
SECTION_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "character": (
        "character", "body", "figure", "skin", "eyes", "face_shape", "features",
        "brows", "lips", "hair_color", "hair_style", "face_paint",
        "figure", "body_art",
        "head_position", "gaze", "expression",
    ),
    "wardrobe": (
        "fashion", "footwear", "outerwear", "accessory",
        "eyewear", "headwear", "bag", "handheld",
    ),
    "scene": ("environment", "location", "atmosphere", "pose", "props"),
    "presentation": ("style", "camera", "lighting"),
}


RANDOM_OPTION: Final[str] = "random"
NONE_OPTION: Final[str] = "none"

TIME_OPTIONS: Final[tuple[str, ...]] = (
    "early morning", "mid morning", "midday", "afternoon",
    "late afternoon", "evening", "night",
)
SEASON_OPTIONS: Final[tuple[str, ...]] = ("spring", "summer", "autumn", "winter")
WEATHER_OPTIONS: Final[tuple[str, ...]] = (
    "clear", "overcast", "light rain", "heavy rain", "snow",
)

# Entry IDs are "<pack_id>.<type_name>.<entry_name>" (decision 1).
ENTRY_ID_SEPARATOR: Final[str] = "."
ENTRY_ID_PARTS: Final[int] = 3

MANIFEST_FILENAME: Final[str] = "manifest.json"


@dataclass(frozen=True)
class EngineSettings:
    """Engine-level knobs that are not content, kept out of the node layer."""

    compatibility_mode: CompatibilityMode = DEFAULT_COMPATIBILITY_MODE
    prompt_length: PromptLength = PromptLength.STANDARD
    target_model: TargetModel = TargetModel.GENERIC
    limits: PackLimits = field(default_factory=lambda: DEFAULT_LIMITS)
