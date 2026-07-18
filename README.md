# BRKN Prompt Randomizer

BRKN Prompt Randomizer is a ComfyUI custom-node package for building prompt pools from modular BRKN packs.

The current V1 flow is:

```text
BRKN Pack Browser and Selector
-> BRKN_PACK_SELECTION
-> BRKN Lifestyle Prompt Composer
-> final prompt
```

The browser is the visual selector. It discovers packs, loads thumbnail metadata before generation, lets the user choose one base pack plus modifier packs, and outputs structured selection metadata. The composer remains the prompt engine: it applies pack filtering, exclusions, pinned entries, compatibility rules, seeded randomization, locks, rerolls, and final prompt composition.

## Install

Copy or clone this folder into:

```text
ComfyUI/custom_nodes/ComfyUI-Modular-Prompt-Composer
```

Then restart ComfyUI and hard-refresh the browser.

## Nodes

- `BRKN Pack Browser and Selector`: compact node with a visual pack-selection modal.
- `BRKN Lifestyle Prompt Composer`: prompt generation engine that consumes the browser output.

Saved workflows keep the internal node identifiers for backward compatibility. User-facing labels use BRKN naming.

## Pack Browser

The selector supports:

- one active base pack
- multiple modifier packs
- category grouping
- search
- pre-execution thumbnails
- larger preview images when provided
- persisted selections in workflow JSON
- structured `BRKN_PACK_SELECTION` output
- flattened `enabled_pack_ids` bridge for current composer compatibility

Current browser categories include:

- Base Packs
- Camera Style and Lighting
- Wardrobe and Makeup
- Locations
- Transportation
- Actions

Thumbnail paths are metadata-driven. Pack browser metadata lives in `browser_metadata/*.json` and points to assets under `thumbnails/`.

Example metadata:

```json
{
  "pack_id": "brkn_instagram",
  "selection_role": "base",
  "selection_category": "base_pack",
  "thumbnail_base": "thumbnails/style",
  "cover_image": {
    "path": "instagram__thumbnail_512.webp"
  }
}
```

## Thumbnails

Browser-ready thumbnails are square 512 x 512 WebP files. Larger PNG files can remain as masters when useful, but the browser should point at the WebP thumbnail.

For wardrobe/style packs with two images:

- three-quarter image: cover thumbnail
- close-up image: preview image

For transportation packs:

- vehicle image: cover thumbnail

Missing image paths produce browser warnings and a clean fallback tile instead of blocking the workflow.

## LoRAs

LoRAs are not hard-coded in BRKN packs. The packs provide prompt text and trigger-token pools only. LoRA usage remains a workflow/model choice outside the pack browser.

## Release Notes

Development-only thumbnail authoring utilities should not be included in a public release. The browser itself only requires the runtime pack data, browser metadata, thumbnails, frontend files, and node code.
