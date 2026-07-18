import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from aiohttp import web
    from server import PromptServer
except Exception:  # pragma: no cover - ComfyUI server is not present in unit tests.
    web = None
    PromptServer = None


SELECTION_SCHEMA_VERSION = "1.1"
SELECTION_SOURCE_NODE = "BRKN Pack Browser and Selector"

PROMPT_POOL_CATEGORIES: Dict[str, str] = {
    "base_pack": "Base Packs",
    "camera_lighting": "Camera Style and Lighting",
    "wardrobe_makeup": "Wardrobe and Makeup",
    "location": "Locations",
    "transportation": "Transportation",
    "actions": "Actions",
}

MODIFIER_CATEGORY_IDS: Tuple[str, ...] = (
    "camera_lighting",
    "wardrobe_makeup",
    "location",
    "transportation",
    "actions",
)

ENGINE_PACK_CATEGORY_ORDER: Tuple[str, ...] = (
    "wardrobe_makeup",
    "transportation",
    "location",
    "camera_lighting",
    "actions",
)

CATALOG_CATEGORIES: Tuple[str, ...] = (
    "All",
    "Camera Styles",
    "Lighting",
    "Camera Angles",
    "Framing",
    "Characters / Appearance",
    "Hair Styles",
    "Hair Colors",
    "Makeup",
    "Facial Features",
    "Wardrobe",
    "Characters",
    "Places",
    "Destinations",
    "Vehicles",
    "Actions",
    "Visual Styles",
    "Video Motion and Effects",
    "Production Utility",
)

CONTENT_TYPE_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "visual_styles": ("Visual Styles",),
    "camera_presets": ("Camera Styles", "Camera Angles", "Framing"),
    "lighting_presets": ("Lighting",),
    "characters": ("Characters", "Characters / Appearance"),
    "bodies": ("Characters", "Characters / Appearance"),
    "skins": ("Characters", "Characters / Appearance"),
    "eyes": ("Characters", "Characters / Appearance"),
    "face_shapes": ("Characters", "Characters / Appearance", "Facial Features"),
    "features": ("Characters", "Characters / Appearance", "Facial Features"),
    "brows": ("Characters", "Characters / Appearance", "Facial Features"),
    "lips": ("Characters", "Characters / Appearance", "Facial Features"),
    "hair_colors": ("Characters", "Characters / Appearance", "Hair Colors"),
    "hair_styles": ("Characters", "Characters / Appearance", "Hair Styles"),
    "head_positions": ("Characters",),
    "gazes": ("Characters",),
    "expressions": ("Characters",),
    "figures": ("Characters",),
    "body_arts": ("Characters", "Wardrobe"),
    "face_paints": ("Characters", "Characters / Appearance", "Makeup", "Wardrobe"),
    "outfits": ("Wardrobe",),
    "footwear": ("Wardrobe",),
    "outerwear": ("Wardrobe",),
    "accessories": ("Wardrobe",),
    "eyewear": ("Wardrobe",),
    "headwear": ("Wardrobe",),
    "bags": ("Wardrobe",),
    "bag": ("Wardrobe",),
    "environments": ("Places",),
    "locations": ("Places",),
    "atmospheres": ("Places",),
    "actions": ("Actions",),
    "poses": ("Actions",),
    "handhelds": ("Vehicles", "Actions"),
    "handheld": ("Vehicles", "Actions"),
}

TAG_CATEGORIES: Dict[str, str] = {
    "look": "Visual Styles",
    "visual": "Visual Styles",
    "cinematic": "Visual Styles",
    "place": "Places",
    "location": "Places",
    "destination": "Destinations",
    "vacation": "Destinations",
    "travel": "Destinations",
    "vehicle": "Vehicles",
    "car": "Vehicles",
    "bike": "Vehicles",
    "yacht": "Vehicles",
    "wardrobe": "Wardrobe",
    "wear": "Wardrobe",
    "fashion": "Wardrobe",
    "motion": "Video Motion and Effects",
    "capture": "Production Utility",
    "thumbnail": "Production Utility",
    "utility": "Production Utility",
}


@dataclass(frozen=True)
class PackEntry:
    id: str
    pack_id: str
    content_type: str
    type: str
    label: str
    prompt: str
    tags: Tuple[str, ...]
    metadata: Dict[str, object]


@dataclass(frozen=True)
class PackInfo:
    pack_id: str
    name: str
    version: str
    author: str
    description: str
    content_types: Tuple[str, ...]
    enabled_by_default: bool
    priority: int
    tags: Tuple[str, ...]
    categories: Tuple[str, ...]
    selection_role: str
    selection_category: str
    is_complete_pack: bool
    default_pinned_entry_ids: Tuple[str, ...]
    cover_image: str
    preview_images: Tuple[str, ...]
    thumbnail_warnings: Tuple[str, ...]
    path: Path
    entry_count: int


@dataclass(frozen=True)
class PackCollection:
    collection_id: str
    name: str
    description: str
    search_terms: Tuple[str, ...]
    referenced_pack_ids: Tuple[str, ...]
    recommended_weights: Dict[str, float]
    path: Path


@dataclass(frozen=True)
class PackCatalog:
    packs: Tuple[PackInfo, ...]
    entries: Tuple[PackEntry, ...]
    collections: Tuple[PackCollection, ...]
    warnings: Tuple[str, ...]


def default_packs_dir() -> Path:
    return Path(__file__).resolve().parent / "packs"


def default_collections_dir() -> Path:
    return Path(__file__).resolve().parent / "collections"


def load_pack_catalog(
    packs_dir: Optional[Path] = None,
    collections_dir: Optional[Path] = None,
) -> PackCatalog:
    root = Path(packs_dir) if packs_dir is not None else default_packs_dir()
    collections_root = Path(collections_dir) if collections_dir is not None else default_collections_dir()
    warnings: List[str] = []
    packs: List[PackInfo] = []
    entries: List[PackEntry] = []

    if not root.exists():
        return PackCatalog(tuple(), tuple(), tuple(), (f"Pack directory not found: {root}",))

    for pack_dir in _iter_pack_dirs(root):
        manifest_path = pack_dir / "manifest.json"
        if not manifest_path.exists():
            warnings.append(f"Skipping {pack_dir.name}: missing manifest.json")
            continue

        try:
            manifest = _read_json(manifest_path)
            pack_entries, entry_warnings = _load_pack_entries(pack_dir, manifest)
            browser_metadata, metadata_warnings = _load_browser_metadata(pack_dir, root)
            pack = _parse_pack_manifest(pack_dir, manifest, len(pack_entries), browser_metadata, metadata_warnings)
        except ValueError as exc:
            warnings.append(f"Skipping {pack_dir.name}: {exc}")
            continue

        warnings.extend(entry_warnings)
        warnings.extend(pack.thumbnail_warnings)
        packs.append(pack)
        entries.extend(pack_entries)

    packs.sort(key=lambda pack: (pack.priority, pack.pack_id))
    collections = _load_collections(collections_root, warnings)
    return PackCatalog(tuple(packs), tuple(entries), tuple(collections), tuple(warnings))


def build_selection(
    catalog: PackCatalog,
    mode: str = "Catalog",
    category_filter: str = "All",
    search_query: str = "",
    allow_pack_ids: str = "",
    base_pack_id: str = "",
    modifier_pack_ids: str = "",
    selected_packs_by_category: Optional[Dict[str, Sequence[str]]] = None,
    exclude_pack_ids: str = "",
    pin_entry_ids: str = "",
    selected_collection_ids: str = "",
    tag_filters: str = "",
) -> Dict[str, object]:
    pack_ids = {pack.pack_id for pack in catalog.packs}
    entry_ids = {entry.id for entry in catalog.entries}
    collection_ids = {collection.collection_id for collection in catalog.collections}

    base_pack_id = base_pack_id.strip()
    category_selection = _normalize_category_selection(selected_packs_by_category)
    modifiers = _dedupe(
        _parse_csv(modifier_pack_ids)
        + [
            pack_id
            for category_id in MODIFIER_CATEGORY_IDS
            for pack_id in category_selection.get(category_id, [])
        ]
    )
    if modifiers and not any(category_selection.values()):
        category_selection = group_modifiers_by_category(catalog, modifiers)
    allowed = _ordered_engine_pack_ids(base_pack_id, category_selection, modifiers, _parse_csv(allow_pack_ids))
    excluded = _parse_csv(exclude_pack_ids)
    pinned = _parse_csv(pin_entry_ids)
    selected_collections = _parse_csv(selected_collection_ids)
    tag_filter_values = _parse_csv(tag_filters)
    category_filters = [] if category_filter == "All" else [category_filter]
    warnings = list(catalog.warnings)

    for pack_id in allowed:
        if pack_id not in pack_ids:
            warnings.append(f"Allowed pack is not installed: {pack_id}")
    if base_pack_id and base_pack_id not in pack_ids:
        warnings.append(f"Base pack is not installed: {base_pack_id}")
    for pack_id in excluded:
        if pack_id not in pack_ids:
            warnings.append(f"Excluded pack is not installed: {pack_id}")
    for entry_id in pinned:
        if entry_id not in entry_ids:
            warnings.append(f"Pinned entry is not installed: {entry_id}")
    for collection_id in selected_collections:
        if collection_id not in collection_ids:
            warnings.append(f"Selected collection is not installed: {collection_id}")

    expanded_allowed = list(allowed)
    for collection in catalog.collections:
        if collection.collection_id not in selected_collections:
            continue
        for pack_id in collection.referenced_pack_ids:
            if pack_id not in pack_ids:
                warnings.append(f"{collection.collection_id} references missing pack: {pack_id}")
                continue
            if pack_id not in expanded_allowed:
                expanded_allowed.append(pack_id)

    final_enabled = [pack_id for pack_id in expanded_allowed if pack_id not in set(excluded)]
    default_pins = _default_pins_for_enabled_packs(catalog, final_enabled)
    pinned = _dedupe(default_pins + pinned)
    if category_filters and not final_enabled and mode == "Catalog":
        matching = [pack.pack_id for pack in catalog.packs if category_filter in pack.categories]
        final_enabled = [pack_id for pack_id in matching if pack_id not in set(excluded)]

    selection = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "base_pack_id": base_pack_id,
        "modifier_pack_ids": modifiers,
        "selected_packs_by_category": {
            category_id: [
                pack_id for pack_id in category_selection.get(category_id, []) if pack_id in modifiers
            ]
            for category_id in MODIFIER_CATEGORY_IDS
        },
        "allowed_pack_ids": allowed,
        "excluded_pack_ids": excluded,
        "pinned_entry_ids": pinned,
        "selected_collection_ids": selected_collections,
        "category_filters": category_filters,
        "tag_filters": tag_filter_values,
        "expanded_allowed_pack_ids": expanded_allowed,
        "enabled_pack_ids": final_enabled,
        "search_query": search_query.strip(),
        "mode": mode,
        "source_node": SELECTION_SOURCE_NODE,
        "warnings": _dedupe(warnings),
    }
    return selection


def selection_to_json(selection: Dict[str, object]) -> str:
    return json.dumps(selection, indent=2, ensure_ascii=False)


def selection_to_enabled_packs(selection: Dict[str, object]) -> str:
    return ", ".join(str(pack_id) for pack_id in selection.get("enabled_pack_ids", []))


def _normalize_category_selection(value: Optional[Dict[str, Sequence[str]]]) -> Dict[str, List[str]]:
    normalized = {category_id: [] for category_id in MODIFIER_CATEGORY_IDS}
    if not isinstance(value, dict):
        return normalized
    for category_id in MODIFIER_CATEGORY_IDS:
        raw = value.get(category_id, [])
        if isinstance(raw, str):
            normalized[category_id] = _parse_csv(raw)
        elif isinstance(raw, (list, tuple)):
            normalized[category_id] = _dedupe(str(item).strip() for item in raw if str(item).strip())
    return normalized


def _parse_category_json(value: str) -> Dict[str, List[str]]:
    if not str(value or "").strip():
        return {category_id: [] for category_id in MODIFIER_CATEGORY_IDS}
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {category_id: [] for category_id in MODIFIER_CATEGORY_IDS}
    return _normalize_category_selection(raw if isinstance(raw, dict) else {})


def group_modifiers_by_category(catalog: PackCatalog, modifier_pack_ids: Sequence[str]) -> Dict[str, List[str]]:
    packs_by_id = {pack.pack_id: pack for pack in catalog.packs}
    grouped = {category_id: [] for category_id in MODIFIER_CATEGORY_IDS}
    for pack_id in modifier_pack_ids:
        pack = packs_by_id.get(pack_id)
        category_id = pack.selection_category if pack else "camera_lighting"
        if category_id not in grouped:
            category_id = "camera_lighting"
        grouped[category_id].append(pack_id)
    return grouped


def _default_pins_for_enabled_packs(catalog: PackCatalog, enabled_pack_ids: Sequence[str]) -> List[str]:
    packs_by_id = {pack.pack_id: pack for pack in catalog.packs}
    pins: List[str] = []
    for pack_id in enabled_pack_ids:
        pack = packs_by_id.get(pack_id)
        if pack:
            pins.extend(pack.default_pinned_entry_ids)
    return _dedupe(pins)


def _ordered_engine_pack_ids(
    base_pack_id: str,
    category_selection: Dict[str, List[str]],
    modifier_pack_ids: Sequence[str],
    extra_allowed_pack_ids: Sequence[str],
) -> List[str]:
    ordered: List[str] = []
    if base_pack_id:
        ordered.append(base_pack_id)

    categorized = {
        category_id: list(category_selection.get(category_id, []))
        for category_id in MODIFIER_CATEGORY_IDS
    }
    categorized_known_ids = {
        pack_id
        for pack_ids in categorized.values()
        for pack_id in pack_ids
    }

    for category_id in ENGINE_PACK_CATEGORY_ORDER:
        ordered.extend(categorized.get(category_id, []))

    for pack_id in modifier_pack_ids:
        if pack_id not in categorized_known_ids:
            ordered.append(pack_id)

    ordered.extend(extra_allowed_pack_ids)
    return _dedupe(ordered)


def summarize_selection(selection: Dict[str, object], catalog: PackCatalog) -> str:
    packs_by_id = {pack.pack_id: pack for pack in catalog.packs}
    collection_names = {
        collection.collection_id: collection.name for collection in catalog.collections
    }
    enabled = [packs_by_id.get(pack_id).name if packs_by_id.get(pack_id) else pack_id for pack_id in selection.get("enabled_pack_ids", [])]
    base_pack_id = str(selection.get("base_pack_id", "") or "")
    base_name = packs_by_id.get(base_pack_id).name if packs_by_id.get(base_pack_id) else base_pack_id
    modifiers = [
        packs_by_id.get(pack_id).name if packs_by_id.get(pack_id) else pack_id
        for pack_id in selection.get("modifier_pack_ids", [])
    ]
    by_category = selection.get("selected_packs_by_category", {})
    category_counts = []
    if isinstance(by_category, dict):
        for category_id in MODIFIER_CATEGORY_IDS:
            count = len(by_category.get(category_id, []) or [])
            if count:
                category_counts.append(f"{PROMPT_POOL_CATEGORIES[category_id]}: {count}")
    allowed = selection.get("allowed_pack_ids", [])
    excluded = selection.get("excluded_pack_ids", [])
    pinned = selection.get("pinned_entry_ids", [])
    selected_collections = [
        collection_names.get(collection_id, collection_id)
        for collection_id in selection.get("selected_collection_ids", [])
    ]
    parts = [
        f"Mode: {selection.get('mode', 'Catalog')}",
        f"Base pack: {base_name or 'None'}",
        f"Modifier packs: {', '.join(modifiers) or 'None'}",
        f"Prompt pool categories: {', '.join(category_counts) or 'None'}",
        f"Category filters: {', '.join(selection.get('category_filters', [])) or 'None'}",
        f"Allowed packs: {', '.join(allowed) or 'None'}",
        f"Excluded packs: {', '.join(excluded) or 'None'}",
        f"Pinned entries: {', '.join(pinned) or 'None'}",
        f"Collections: {', '.join(selected_collections) or 'None'}",
        f"Enabled packs for existing randomizer: {', '.join(enabled) or 'None'}",
    ]
    return "\n".join(parts)


def render_browser_results(
    catalog: PackCatalog,
    mode: str,
    category_filter: str,
    search_query: str,
    limit: int = 50,
) -> str:
    if mode == "Collections":
        rows = [
            f"{collection.collection_id} | {collection.name} | packs: {', '.join(collection.referenced_pack_ids)}"
            for collection in catalog.collections
            if _matches_query(search_query, _collection_search_text(collection))
        ]
        return "\n".join(rows[:limit]) or "No collections found."

    if mode == "Search":
        scored = search_catalog(catalog, search_query, category_filter)
        rows = [
            f"{score:02d} | {item_id} | {label} | {description}"
            for score, item_id, label, description in scored[:limit]
        ]
        return "\n".join(rows) or "No search results found."

    packs = filter_packs(catalog.packs, category_filter)
    if search_query.strip():
        packs = [pack for pack in packs if _matches_query(search_query, _pack_search_text(pack))]
    rows = [
        f"{pack.pack_id} | {pack.name} | {', '.join(pack.categories)} | entries: {pack.entry_count} | cover: {_thumbnail_label(pack.cover_image)}"
        for pack in packs[:limit]
    ]
    return "\n".join(rows) or "No catalog packs found."


def filter_packs(packs: Sequence[PackInfo], category_filter: str) -> List[PackInfo]:
    if category_filter == "All":
        return list(packs)
    return [pack for pack in packs if category_filter in pack.categories]


def search_catalog(
    catalog: PackCatalog,
    query: str,
    category_filter: str = "All",
) -> List[Tuple[int, str, str, str]]:
    terms = _tokenize(query)
    if not terms and category_filter == "All":
        return []

    results: List[Tuple[int, str, str, str]] = []
    for pack in filter_packs(catalog.packs, category_filter):
        score = _score_terms(terms, _pack_search_text(pack), pack.name)
        if score or category_filter != "All":
            results.append((score or 1, pack.pack_id, pack.name, pack.description))

    allowed_pack_ids = {pack.pack_id for pack in filter_packs(catalog.packs, category_filter)}
    for entry in catalog.entries:
        if category_filter != "All" and entry.pack_id not in allowed_pack_ids:
            continue
        text = " ".join(
            [
                entry.id,
                entry.label,
                entry.prompt,
                " ".join(entry.tags),
                json.dumps(entry.metadata, ensure_ascii=False),
            ]
        )
        score = _score_terms(terms, text, entry.label)
        if score:
            results.append((score, entry.id, entry.label, entry.prompt))

    for collection in catalog.collections:
        if category_filter not in ("All", "Destinations"):
            continue
        score = _score_terms(terms, _collection_search_text(collection), collection.name)
        if score:
            results.append((score + 2, collection.collection_id, collection.name, collection.description))

    return sorted(results, key=lambda item: (-item[0], item[1]))


def thumbnail_ui_images(
    catalog: PackCatalog,
    mode: str,
    category_filter: str,
    search_query: str,
) -> List[Dict[str, str]]:
    packs = _matching_packs_for_display(catalog, mode, category_filter, search_query)
    for pack in packs:
        image_paths = [pack.cover_image] + list(pack.preview_images)
        image_paths = [path for path in image_paths if path]
        if image_paths:
            return _copy_images_for_comfyui(pack.pack_id, image_paths[:2])
    return []


def _matching_packs_for_display(
    catalog: PackCatalog,
    mode: str,
    category_filter: str,
    search_query: str,
) -> List[PackInfo]:
    if mode == "Search":
        result_ids = [result[1] for result in search_catalog(catalog, search_query, category_filter)]
        packs_by_id = {pack.pack_id: pack for pack in catalog.packs}
        ordered = [packs_by_id[item_id] for item_id in result_ids if item_id in packs_by_id]
        return ordered or filter_packs(catalog.packs, category_filter)
    packs = filter_packs(catalog.packs, category_filter)
    if search_query.strip():
        packs = [pack for pack in packs if _matches_query(search_query, _pack_search_text(pack))]
    return packs


class BRKNPackBrowserAndSelector:
    RETURN_TYPES = ("BRKN_PACK_SELECTION", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "pack_selection",
        "pack_selection_json",
        "selection_summary",
        "warnings",
        "enabled_packs",
    )
    FUNCTION = "select"
    CATEGORY = "BRKN/Prompt Randomizer"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "selected_base_pack_id": ("STRING", {"default": ""}),
                "modifier_pack_ids": ("STRING", {"default": "", "multiline": True}),
                "selected_pack_summary": ("STRING", {"default": ""}),
                "pack_browser": ("BRKN_PACK_BROWSER", {}),
            },
            "optional": {
                "selected_packs_by_category_json": ("STRING", {"default": "", "multiline": True}),
            },
        }

    def select(
        self,
        selected_base_pack_id="",
        modifier_pack_ids="",
        selected_packs_by_category_json="",
        selected_pack_summary="",
        pack_browser=None,
        **kwargs,
    ):
        catalog = load_pack_catalog()
        legacy_selected_pack_id = str(kwargs.get("selected_pack_id", "") or "").strip()
        base_pack_id = str(selected_base_pack_id or legacy_selected_pack_id).strip()
        modifier_ids = str(modifier_pack_ids or "").strip()
        selected_by_category = _parse_category_json(selected_packs_by_category_json)
        allow_pack_ids = str(kwargs.get("allow_pack_ids", "")).strip()
        category_filter = str(kwargs.get("category_filter", "All") or "All")
        search_query = str(kwargs.get("search_query", selected_pack_summary) or "")
        mode = str(kwargs.get("mode", "Catalog") or "Catalog")
        selection = build_selection(
            catalog=catalog,
            mode=mode,
            category_filter=category_filter,
            search_query=search_query,
            allow_pack_ids=allow_pack_ids,
            base_pack_id=base_pack_id,
            modifier_pack_ids=modifier_ids,
            selected_packs_by_category=selected_by_category,
            exclude_pack_ids=str(kwargs.get("exclude_pack_ids", "") or ""),
            pin_entry_ids=str(kwargs.get("pin_entry_ids", "") or ""),
            selected_collection_ids=str(kwargs.get("selected_collection_ids", "") or ""),
            tag_filters=str(kwargs.get("tag_filters", "") or ""),
        )
        selection_json = selection_to_json(selection)
        summary = summarize_selection(selection, catalog)
        warnings = "\n".join(selection["warnings"])
        enabled_packs = selection_to_enabled_packs(selection)
        browser_results = render_browser_results(catalog, mode, category_filter, search_query)
        ui_images = thumbnail_ui_images(catalog, mode, category_filter, search_query)

        return {
            "ui": {
                "text": [
                    f"BROWSER RESULTS:\n{browser_results}",
                    f"SELECTION SUMMARY:\n{summary}",
                    f"WARNINGS:\n{warnings or 'None'}",
                ],
                "images": ui_images,
            },
            "result": (selection, selection_json, summary, warnings, enabled_packs),
        }


def _load_pack_entries(pack_dir: Path, manifest: Dict[str, object]) -> Tuple[List[PackEntry], List[str]]:
    pack_id = str(manifest.get("pack_id", pack_dir.name))
    content_types = _as_tuple(manifest.get("content_types", []))
    entries: List[PackEntry] = []
    warnings: List[str] = []

    for content_type in content_types:
        content_path = pack_dir / f"{content_type}.json"
        if not content_path.exists() and content_type.endswith("s"):
            singular_fallback = pack_dir / f"{content_type[:-1]}.json"
            if singular_fallback.exists():
                content_path = singular_fallback
        if not content_path.exists():
            warnings.append(f"{pack_id}: missing {content_type}.json")
            continue

        try:
            content = _read_json(content_path)
        except ValueError as exc:
            warnings.append(f"{pack_id}/{content_path.name}: {exc}")
            continue

        for raw_entry in content.get("entries", []):
            if not isinstance(raw_entry, dict):
                warnings.append(f"{pack_id}/{content_path.name}: skipping non-object entry")
                continue
            entry_id = str(raw_entry.get("id", "")).strip()
            if not entry_id:
                warnings.append(f"{pack_id}/{content_path.name}: skipping entry without id")
                continue
            entries.append(
                PackEntry(
                    id=entry_id,
                    pack_id=pack_id,
                    content_type=content_type,
                    type=str(raw_entry.get("type", "")),
                    label=str(raw_entry.get("label", entry_id)),
                    prompt=str(raw_entry.get("prompt", "")),
                    tags=_as_tuple(raw_entry.get("tags", [])),
                    metadata=raw_entry.get("metadata", {}) if isinstance(raw_entry.get("metadata", {}), dict) else {},
                )
            )

    return entries, warnings


def _iter_pack_dirs(root: Path) -> List[Path]:
    pack_dirs: List[Path] = []
    seen = set()
    nested_roots = [root / child_name for child_name in ("bundled", "user") if (root / child_name).exists()]
    search_roots = nested_roots or [root]

    for search_root in search_roots:
        for item in sorted((path for path in search_root.iterdir() if path.is_dir()), key=lambda path: path.name):
            resolved = item.resolve()
            if resolved in seen or not (item / "manifest.json").exists():
                continue
            seen.add(resolved)
            pack_dirs.append(item)
    return pack_dirs


def _parse_pack_manifest(
    pack_dir: Path,
    manifest: Dict[str, object],
    entry_count: int,
    browser_metadata: Dict[str, object],
    metadata_warnings: Sequence[str],
) -> PackInfo:
    pack_id = str(manifest.get("pack_id", pack_dir.name)).strip()
    if not pack_id:
        raise ValueError("manifest missing pack_id")

    content_types = _as_tuple(manifest.get("content_types", []))
    recipes = _as_tuple(manifest.get("recipes", []))
    tags = _as_tuple(manifest.get("tags", []))
    categories = _infer_categories(content_types, tags, pack_id, str(manifest.get("name", "")))
    return PackInfo(
        pack_id=pack_id,
        name=str(manifest.get("name", pack_id)),
        version=str(manifest.get("version", "")),
        author=str(manifest.get("author", "")),
        description=str(manifest.get("description", "")),
        content_types=content_types or tuple(f"recipe:{recipe}" for recipe in recipes),
        enabled_by_default=bool(manifest.get("enabled_by_default", False)),
        priority=int(manifest.get("priority", 1000)),
        tags=tags,
        categories=categories,
        selection_role=_selection_role(pack_id, tags, browser_metadata),
        selection_category=_selection_category(categories, tags, browser_metadata),
        is_complete_pack=_is_complete_pack(pack_id, tags, browser_metadata),
        default_pinned_entry_ids=_as_tuple(browser_metadata.get("default_pinned_entry_ids", [])),
        cover_image=str(browser_metadata.get("cover_image", "")),
        preview_images=tuple(str(path) for path in browser_metadata.get("preview_images", []) if str(path).strip()),
        thumbnail_warnings=tuple(metadata_warnings),
        path=pack_dir,
        entry_count=entry_count,
    )


def _load_collections(collections_root: Path, warnings: List[str]) -> List[PackCollection]:
    collections: List[PackCollection] = []
    if not collections_root.exists():
        return collections

    for path in sorted(collections_root.glob("*.json"), key=lambda item: item.name):
        try:
            raw = _read_json(path)
            collection_id = str(raw.get("collection_id", path.stem)).strip()
            if not collection_id:
                raise ValueError("missing collection_id")
            collections.append(
                PackCollection(
                    collection_id=collection_id,
                    name=str(raw.get("name", collection_id)),
                    description=str(raw.get("description", "")),
                    search_terms=_as_tuple(raw.get("search_terms", [])),
                    referenced_pack_ids=_as_tuple(raw.get("referenced_pack_ids", [])),
                    recommended_weights={
                        str(key): float(value)
                        for key, value in raw.get("recommended_weights", {}).items()
                    }
                    if isinstance(raw.get("recommended_weights", {}), dict)
                    else {},
                    path=path,
                )
            )
        except ValueError as exc:
            warnings.append(f"Skipping collection {path.name}: {exc}")
    return collections


def _read_json(path: Path) -> Dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("root must be a JSON object")
    return data


def _infer_categories(content_types: Sequence[str], tags: Sequence[str], pack_id: str, name: str) -> Tuple[str, ...]:
    categories = set()
    for content_type in content_types:
        categories.update(CONTENT_TYPE_CATEGORIES.get(content_type, ()))
    searchable = " ".join([pack_id, name, " ".join(tags)]).lower()
    if "pack_type" in searchable:
        pass
    for token, category in TAG_CATEGORIES.items():
        if token in searchable:
            categories.add(category)
    if not categories:
        categories.add("Visual Styles")
    return tuple(category for category in CATALOG_CATEGORIES if category in categories)


def _selection_role(pack_id: str, tags: Sequence[str], browser_metadata: Dict[str, object]) -> str:
    explicit = str(browser_metadata.get("selection_role", "")).strip().lower()
    if explicit in {"base", "modifier"}:
        return explicit
    if pack_id == "brkn_chassis":
        return "base"
    lowered = {tag.lower() for tag in tags}
    if "layerable" in lowered or "capsule" in lowered or "utility" in lowered:
        return "modifier"
    if "base" in lowered or "closed_world" in lowered or pack_id in {"brkn_base", "brkn_instagram", "brkn_portraits"}:
        return "base"
    return "modifier"


def _selection_category(
    categories: Sequence[str],
    tags: Sequence[str],
    browser_metadata: Dict[str, object],
) -> str:
    explicit = str(browser_metadata.get("selection_category", "")).strip()
    if explicit:
        return _normalize_prompt_pool_category_id(explicit)
    lowered = {tag.lower() for tag in tags}
    if "chassis" in lowered:
        return "base_pack"
    if "base" in lowered or "closed_world" in lowered:
        return "base_pack"
    if {"vehicle", "car", "bike", "yacht"} & lowered:
        return "transportation"
    if {"wardrobe", "capsule"} & lowered:
        return "wardrobe_makeup"
    if {"place", "location", "home"} & lowered:
        return "location"
    if {"look", "visual", "cinematic"} & lowered:
        return "camera_lighting"
    for category in categories:
        category_id = _normalize_prompt_pool_category_id(category)
        if category_id in PROMPT_POOL_CATEGORIES:
            return category_id
    return "camera_lighting"


def _normalize_prompt_pool_category_id(value: str) -> str:
    key = value.strip().lower().replace("&", "and").replace(" ", "_").replace("-", "_")
    aliases = {
        "base": "base_pack",
        "base_packs": "base_pack",
        "camera": "camera_lighting",
        "camera_style": "camera_lighting",
        "camera_styles": "camera_lighting",
        "camera_angles": "camera_lighting",
        "framing": "camera_lighting",
        "lighting": "camera_lighting",
        "camera_style_and_lighting": "camera_lighting",
        "visual_styles": "camera_lighting",
        "wardrobe": "wardrobe_makeup",
        "makeup": "wardrobe_makeup",
        "hair_styles": "wardrobe_makeup",
        "hair_colors": "wardrobe_makeup",
        "characters_/_appearance": "wardrobe_makeup",
        "characters": "wardrobe_makeup",
        "places": "location",
        "locations": "location",
        "destinations": "location",
        "vehicles": "transportation",
        "vehicle": "transportation",
        "transport": "transportation",
        "actions": "actions",
    }
    return aliases.get(key, key if key in PROMPT_POOL_CATEGORIES else "camera_lighting")


def _is_complete_pack(pack_id: str, tags: Sequence[str], browser_metadata: Dict[str, object]) -> bool:
    explicit = browser_metadata.get("is_complete_pack")
    if isinstance(explicit, bool):
        return explicit
    if pack_id == "brkn_chassis":
        return True
    return _selection_role(pack_id, tags, browser_metadata) == "base"


def _parse_csv(value: str) -> List[str]:
    return _dedupe(part.strip() for part in value.replace("\n", ",").split(",") if part.strip())


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _as_tuple(value: object) -> Tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, tuple):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return tuple()


def _tokenize(value: str) -> Tuple[str, ...]:
    return tuple(part.strip().lower() for part in value.replace(",", " ").split() if part.strip())


def _score_terms(terms: Sequence[str], text: str, name: str) -> int:
    if not terms:
        return 0
    lowered_text = text.lower()
    lowered_name = name.lower()
    score = 0
    for term in terms:
        if term == lowered_name:
            score += 10
        elif term in lowered_name:
            score += 8
        elif term in lowered_text:
            score += 5
    return score


def _pack_search_text(pack: PackInfo) -> str:
    return " ".join(
        [
            pack.pack_id,
            pack.name,
            pack.description,
            " ".join(pack.content_types),
            " ".join(pack.tags),
            " ".join(pack.categories),
            pack.cover_image,
            " ".join(pack.preview_images),
        ]
    )


def _collection_search_text(collection: PackCollection) -> str:
    return " ".join(
        [
            collection.collection_id,
            collection.name,
            collection.description,
            " ".join(collection.search_terms),
            " ".join(collection.referenced_pack_ids),
        ]
    )


def _matches_query(query: str, text: str) -> bool:
    terms = _tokenize(query)
    if not terms:
        return True
    lowered = text.lower()
    return all(term in lowered for term in terms)


def _load_browser_metadata(pack_dir: Path, packs_root: Path) -> Tuple[Dict[str, object], List[str]]:
    package_root = packs_root.parent
    metadata_path = package_root / "browser_metadata" / f"{pack_dir.name}.json"
    if not metadata_path.exists():
        metadata_path = pack_dir / "browser.json"
    if not metadata_path.exists():
        return {}, []

    warnings: List[str] = []
    try:
        raw = _read_json(metadata_path)
    except ValueError as exc:
        return {}, [f"{pack_dir.name}/browser.json: {exc}"]

    thumbnail_base = str(raw.get("thumbnail_base", "")).strip()
    base_path = package_root / thumbnail_base if thumbnail_base else pack_dir

    cover = _resolve_image_record(raw.get("cover_image", {}), base_path, warnings, f"{pack_dir.name}: cover_image")
    previews = []
    raw_previews = raw.get("preview_images", [])
    if isinstance(raw_previews, list):
        for index, record in enumerate(raw_previews, start=1):
            resolved = _resolve_image_record(record, base_path, warnings, f"{pack_dir.name}: preview_images[{index}]")
            if resolved:
                previews.append(resolved)
    elif raw_previews:
        warnings.append(f"{pack_dir.name}/browser.json: preview_images must be a list")

    metadata: Dict[str, object] = {
        "cover_image": cover,
        "preview_images": previews,
    }
    for key in (
        "selection_role",
        "selection_category",
        "is_complete_pack",
        "default_pinned_entry_ids",
    ):
        if key in raw:
            metadata[key] = raw[key]
    return metadata, warnings


def _resolve_image_record(record: object, base_path: Path, warnings: List[str], label: str) -> str:
    if not isinstance(record, dict):
        if record:
            warnings.append(f"{label} must be an object")
        return ""
    raw_path = str(record.get("path", "")).strip()
    if not raw_path:
        return ""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = base_path / candidate
    candidate = candidate.resolve()
    if not candidate.exists():
        warnings.append(f"{label} image not found: {candidate}")
        return ""
    return str(candidate)


def _thumbnail_label(path: str) -> str:
    return Path(path).name if path else "fallback"


def _copy_images_for_comfyui(pack_id: str, image_paths: Sequence[str]) -> List[Dict[str, str]]:
    try:
        import folder_paths  # type: ignore
    except Exception:
        return []

    try:
        temp_dir = Path(folder_paths.get_temp_directory())
    except Exception:
        return []

    images: List[Dict[str, str]] = []
    for path in image_paths:
        source = Path(path)
        if not source.exists():
            continue
        target_name = f"brkn_browser_{pack_id}_{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
        target = temp_dir / target_name
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
        images.append({"filename": target_name, "subfolder": "", "type": "temp"})
    return images


def catalog_for_frontend(catalog: Optional[PackCatalog] = None) -> Dict[str, object]:
    catalog = catalog or load_pack_catalog()
    packs = []
    for pack in catalog.packs:
        packs.append(
            {
                "id": pack.pack_id,
                "name": _frontend_pack_name(pack),
                "full_name": pack.name,
                "category": pack.categories[0] if pack.categories else "Uncategorized",
                "categories": list(pack.categories),
                "selection_role": pack.selection_role,
                "selection_category": pack.selection_category,
                "selection_category_label": PROMPT_POOL_CATEGORIES.get(pack.selection_category, pack.selection_category),
                "is_complete_pack": pack.is_complete_pack,
                "description": pack.description,
                "tags": list(pack.tags),
                "entry_count": pack.entry_count,
                "thumbnail": f"/brkn/pack_browser/image?pack_id={pack.pack_id}&kind=cover",
                "preview": f"/brkn/pack_browser/image?pack_id={pack.pack_id}&kind=preview&index=0",
                "has_thumbnail": bool(pack.cover_image),
                "has_preview": bool(pack.preview_images),
                "warnings": list(pack.thumbnail_warnings),
            }
        )
    return {
        "packs": packs,
        "categories": list(CATALOG_CATEGORIES),
        "prompt_pool_categories": PROMPT_POOL_CATEGORIES,
        "warnings": list(catalog.warnings),
    }


def _frontend_pack_name(pack: PackInfo) -> str:
    name = pack.name
    if name.startswith("BRKN "):
        name = name[5:]
    if pack.pack_id == "wear_goth_classic2":
        return "Classic Goth 2"
    if name.startswith("Goth - Classic"):
        return "Classic Goth"
    return name


def _image_path_for_pack(pack_id: str, kind: str, index: int = 0) -> Optional[Path]:
    catalog = load_pack_catalog()
    for pack in catalog.packs:
        if pack.pack_id != pack_id:
            continue
        if kind == "cover" and pack.cover_image:
            return Path(pack.cover_image)
        if kind == "preview" and pack.preview_images:
            safe_index = max(0, min(index, len(pack.preview_images) - 1))
            return Path(pack.preview_images[safe_index])
    return None


def register_pack_browser_routes() -> None:
    if web is None or PromptServer is None:
        return
    routes = PromptServer.instance.routes

    @routes.get("/brkn/pack_browser/catalog")
    async def get_pack_browser_catalog(_request):  # type: ignore[no-untyped-def]
        return web.json_response(catalog_for_frontend())

    @routes.get("/brkn/pack_browser/image")
    async def get_pack_browser_image(request):  # type: ignore[no-untyped-def]
        pack_id = str(request.query.get("pack_id", "")).strip()
        kind = str(request.query.get("kind", "cover")).strip()
        try:
            index = int(request.query.get("index", "0"))
        except ValueError:
            index = 0
        path = _image_path_for_pack(pack_id, kind, index)
        if path is None or not path.exists() or not path.is_file():
            return web.Response(status=404, text="BRKN thumbnail not found")
        return web.FileResponse(path)


register_pack_browser_routes()
