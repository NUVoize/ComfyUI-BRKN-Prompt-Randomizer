import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brkn_pack_browser import (
    BRKNPackBrowserAndSelector,
    build_selection,
    catalog_for_frontend,
    load_pack_catalog,
    render_browser_results,
    search_catalog,
    selection_to_enabled_packs,
    selection_to_json,
    thumbnail_ui_images,
)


class BRKNPackBrowserTests(unittest.TestCase):
    def test_node_registration_shape(self):
        spec = importlib.util.spec_from_file_location("brkn_extension_init", ROOT / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertIn("BRKNPackBrowserAndSelector", module.NODE_CLASS_MAPPINGS)
        self.assertEqual(
            module.NODE_DISPLAY_NAME_MAPPINGS["BRKNPackBrowserAndSelector"],
            "BRKN Pack Browser and Selector",
        )
        self.assertEqual(module.WEB_DIRECTORY, "web")

    def test_node_return_types_include_custom_selection(self):
        self.assertEqual(BRKNPackBrowserAndSelector.RETURN_TYPES[0], "BRKN_PACK_SELECTION")
        self.assertIn("pack_selection_json", BRKNPackBrowserAndSelector.RETURN_NAMES)
        self.assertIn("enabled_packs", BRKNPackBrowserAndSelector.RETURN_NAMES)

    def test_catalog_loads_installed_packs_entries_and_collections(self):
        catalog = load_pack_catalog()

        pack_ids = {pack.pack_id for pack in catalog.packs}
        collection_ids = {collection.collection_id for collection in catalog.collections}

        self.assertIn("brkn_base", pack_ids)
        self.assertIn("look_cinematic", pack_ids)
        self.assertIn("brkn_thumbnail_capture", pack_ids)
        self.assertIn("greece_vacation", collection_ids)
        self.assertGreater(len(catalog.entries), 0)

    def test_classic_goth_thumbnail_metadata_resolves_from_browser_json(self):
        catalog = load_pack_catalog()
        pack = next(pack for pack in catalog.packs if pack.pack_id == "wear_goth_classic")

        self.assertTrue(pack.cover_image.endswith("wear_classic_goth_cover_thumbnail_512.webp"))
        self.assertTrue(Path(pack.cover_image).exists())
        self.assertEqual(len(pack.preview_images), 1)
        self.assertTrue(pack.preview_images[0].endswith("wear_classic_goth_close-up_thumbnail_512.webp"))
        self.assertFalse(pack.thumbnail_warnings)

    def test_catalog_search_filters_to_classic_goth_and_shows_cover_path(self):
        catalog = load_pack_catalog()
        results = render_browser_results(catalog, "Catalog", "Wardrobe", "classic goth")

        self.assertIn("wear_goth_classic", results)
        self.assertIn("wear_classic_goth_cover_thumbnail_512.webp", results)

    def test_catalog_category_filter_finds_visual_style_pack(self):
        catalog = load_pack_catalog()
        results = render_browser_results(catalog, "Catalog", "Visual Styles", "")

        self.assertIn("look_cinematic", results)

    def test_catalog_category_filter_finds_capture_utility_pack(self):
        catalog = load_pack_catalog()
        results = render_browser_results(catalog, "Catalog", "Production Utility", "")

        self.assertIn("brkn_thumbnail_capture", results)

    def test_hair_facets_are_browser_categories(self):
        catalog = load_pack_catalog()
        hair_style_results = render_browser_results(catalog, "Catalog", "Hair Styles", "")
        hair_color_results = render_browser_results(catalog, "Catalog", "Hair Colors", "")

        self.assertIn("brkn_base", hair_style_results)
        self.assertIn("brkn_base", hair_color_results)

    def test_thumbnail_ui_images_is_empty_without_comfyui_folder_paths(self):
        catalog = load_pack_catalog()

        self.assertEqual(thumbnail_ui_images(catalog, "Catalog", "Wardrobe", "classic goth"), [])

    def test_missing_thumbnail_image_warns_without_blocking_pack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packs = root / "packs"
            thumbnails = root / "thumbnails"
            pack_dir = packs / "wear_test"
            pack_dir.mkdir(parents=True)
            thumbnails.mkdir()
            (pack_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "pack_id": "wear_test",
                        "name": "Wear Test",
                        "version": "1.0.0",
                        "content_types": ["outfits"],
                        "tags": ["wardrobe"],
                    }
                ),
                encoding="utf-8",
            )
            (pack_dir / "outfits.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "content_type": "outfits",
                        "entries": [
                            {
                                "id": "wear_test.outfit.one",
                                "type": "outfit",
                                "label": "One",
                                "prompt": "one outfit",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (pack_dir / "browser.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "thumbnail_base": "thumbnails",
                        "cover_image": {"path": "missing.webp"},
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_pack_catalog(packs_dir=packs, collections_dir=root / "collections")

        self.assertEqual(len(catalog.packs), 1)
        self.assertEqual(catalog.packs[0].cover_image, "")
        self.assertTrue(any("image not found" in warning for warning in catalog.warnings))

    def test_browser_widget_values_persist_selection_state(self):
        node = BRKNPackBrowserAndSelector()
        result = node.select(
            selected_base_pack_id="brkn_instagram",
            modifier_pack_ids="wear_goth_classic, look_cinematic",
            selected_packs_by_category_json=json.dumps(
                {
                    "camera_lighting": ["look_cinematic"],
                    "wardrobe_makeup": ["wear_goth_classic"],
                }
            ),
            selected_pack_summary="Base: Instagram\nModifiers: Classic Goth, Cinematic",
        )
        payload = json.loads(result["result"][1])

        self.assertEqual(payload["base_pack_id"], "brkn_instagram")
        self.assertEqual(payload["modifier_pack_ids"], ["wear_goth_classic", "look_cinematic"])
        self.assertEqual(payload["selected_packs_by_category"]["camera_lighting"], ["look_cinematic"])
        self.assertEqual(payload["selected_packs_by_category"]["wardrobe_makeup"], ["wear_goth_classic"])
        self.assertEqual(payload["enabled_pack_ids"], ["brkn_instagram", "wear_goth_classic", "look_cinematic"])

    def test_frontend_catalog_exposes_classic_goth_thumbnail_and_preview_urls(self):
        catalog = load_pack_catalog()
        payload = catalog_for_frontend(catalog)
        pack = next(pack for pack in payload["packs"] if pack["id"] == "wear_goth_classic")

        self.assertEqual(pack["name"], "Classic Goth")
        self.assertEqual(pack["thumbnail"], "/brkn/pack_browser/image?pack_id=wear_goth_classic&kind=cover")
        self.assertEqual(pack["preview"], "/brkn/pack_browser/image?pack_id=wear_goth_classic&kind=preview&index=0")
        self.assertEqual(pack["selection_category"], "wardrobe_makeup")
        self.assertEqual(pack["selection_category_label"], "Wardrobe and Makeup")
        self.assertTrue(pack["has_thumbnail"])
        self.assertTrue(pack["has_preview"])

    def test_thumbnail_metadata_covers_supplied_wardrobe_and_transportation_assets(self):
        catalog = load_pack_catalog()
        packs = {pack.pack_id: pack for pack in catalog.packs}
        wardrobe_ids = {
            "wear_egirl",
            "wear_egirl_classic",
            "wear_emo",
            "wear_goth",
            "wear_goth_classic",
            "wear_goth_classic2",
            "wear_goth_mall",
            "wear_goth_romantic",
            "wear_goth_trad",
            "wear_riviera",
        }
        transportation_ids = {
            "place_bike",
            "place_car_911",
            "place_car_bmw2000",
            "place_car_gto",
            "place_car_gwagon",
            "place_yacht",
        }
        base_and_style_ids = {
            "brkn_base",
            "brkn_chassis",
            "brkn_instagram",
            "brkn_portraits",
            "brkn_thumbnail_capture",
            "look_cinematic",
            "look_y2k",
            "place_home",
        }

        for pack_id in wardrobe_ids:
            with self.subTest(pack_id=pack_id):
                self.assertTrue(packs[pack_id].cover_image.endswith(".webp"))
                self.assertTrue(Path(packs[pack_id].cover_image).exists())
                self.assertEqual(len(packs[pack_id].preview_images), 1)
                self.assertTrue(Path(packs[pack_id].preview_images[0]).exists())
                self.assertFalse(packs[pack_id].thumbnail_warnings)
        for pack_id in transportation_ids:
            with self.subTest(pack_id=pack_id):
                self.assertTrue(packs[pack_id].cover_image.endswith(".webp"))
                self.assertTrue(Path(packs[pack_id].cover_image).exists())
                self.assertFalse(packs[pack_id].thumbnail_warnings)
        for pack_id in base_and_style_ids:
            with self.subTest(pack_id=pack_id):
                self.assertTrue(packs[pack_id].cover_image.endswith(".webp"))
                self.assertTrue(Path(packs[pack_id].cover_image).exists())
                self.assertFalse(packs[pack_id].thumbnail_warnings)

    def test_frontend_catalog_marks_base_and_modifier_roles(self):
        payload = catalog_for_frontend(load_pack_catalog())
        instagram = next(pack for pack in payload["packs"] if pack["id"] == "brkn_instagram")
        goth = next(pack for pack in payload["packs"] if pack["id"] == "wear_goth_classic")
        car = next(pack for pack in payload["packs"] if pack["id"] == "place_car_911")
        chassis = next(pack for pack in payload["packs"] if pack["id"] == "brkn_chassis")
        thumbnail = next(pack for pack in payload["packs"] if pack["id"] == "brkn_thumbnail_capture")

        self.assertEqual(instagram["selection_role"], "base")
        self.assertEqual(instagram["selection_category"], "base_pack")
        self.assertTrue(instagram["is_complete_pack"])
        self.assertEqual(goth["selection_role"], "modifier")
        self.assertEqual(goth["selection_category"], "wardrobe_makeup")
        self.assertFalse(goth["is_complete_pack"])
        self.assertEqual(car["selection_category"], "transportation")
        self.assertEqual(chassis["selection_role"], "base")
        self.assertEqual(chassis["selection_category"], "base_pack")
        self.assertEqual(thumbnail["selection_role"], "base")
        self.assertEqual(thumbnail["selection_category"], "base_pack")

    def test_base_replacement_preserves_modifiers_in_selection_contract(self):
        catalog = load_pack_catalog()
        first = build_selection(
            catalog,
            base_pack_id="brkn_instagram",
            modifier_pack_ids="wear_goth_classic, look_cinematic",
        )
        second = build_selection(
            catalog,
            base_pack_id="brkn_portraits",
            modifier_pack_ids=", ".join(first["modifier_pack_ids"]),
        )

        self.assertEqual(second["base_pack_id"], "brkn_portraits")
        self.assertEqual(second["modifier_pack_ids"], ["wear_goth_classic", "look_cinematic"])
        self.assertEqual(second["enabled_pack_ids"], ["brkn_portraits", "wear_goth_classic", "look_cinematic"])
        self.assertEqual(second["selected_packs_by_category"]["wardrobe_makeup"], ["wear_goth_classic"])
        self.assertEqual(second["selected_packs_by_category"]["camera_lighting"], ["look_cinematic"])

    def test_structured_prompt_pool_selection_preserves_categories_and_flat_bridge(self):
        catalog = load_pack_catalog()
        selection = build_selection(
            catalog,
            base_pack_id="brkn_instagram",
            selected_packs_by_category={
                "camera_lighting": ["look_cinematic"],
                "wardrobe_makeup": ["wear_goth_classic"],
                "location": ["place_home"],
                "transportation": ["place_car_911"],
                "actions": ["brkn_chassis"],
            },
        )

        self.assertEqual(selection["schema_version"], "1.1")
        self.assertEqual(selection["base_pack_id"], "brkn_instagram")
        self.assertEqual(selection["selected_packs_by_category"]["camera_lighting"], ["look_cinematic"])
        self.assertEqual(selection["selected_packs_by_category"]["wardrobe_makeup"], ["wear_goth_classic"])
        self.assertEqual(selection["selected_packs_by_category"]["location"], ["place_home"])
        self.assertEqual(selection["selected_packs_by_category"]["transportation"], ["place_car_911"])
        self.assertEqual(selection["selected_packs_by_category"]["actions"], ["brkn_chassis"])
        self.assertEqual(
            selection["enabled_pack_ids"],
            ["brkn_instagram", "wear_goth_classic", "place_car_911", "place_home", "look_cinematic", "brkn_chassis"],
        )

    def test_layerable_recipe_outputs_expected_engine_pack_order(self):
        catalog = load_pack_catalog()
        examples = [
            ("wear_goth_classic", "place_car_911", "look_cinematic"),
            ("wear_egirl_classic", "place_car_gwagon", "look_y2k"),
            ("wear_riviera", "place_yacht", "look_cinematic"),
            ("wear_goth_classic2", "place_bike", "look_cinematic"),
            ("wear_goth_mall", "place_home", "look_y2k"),
        ]

        for wardrobe_pack, place_pack, style_pack in examples:
            category = "transportation" if place_pack.startswith(("place_car", "place_yacht", "place_bike")) else "location"
            selected = {
                "camera_lighting": [style_pack],
                "wardrobe_makeup": [wardrobe_pack],
                "location": [],
                "transportation": [],
                "actions": [],
            }
            selected[category] = [place_pack]
            selection = build_selection(
                catalog,
                base_pack_id="brkn_chassis",
                selected_packs_by_category=selected,
            )

            self.assertEqual(
                selection["enabled_pack_ids"],
                ["brkn_chassis", wardrobe_pack, place_pack, style_pack],
            )

    def test_thumbnail_capture_base_adds_default_override_pins(self):
        catalog = load_pack_catalog()
        selection = build_selection(
            catalog,
            base_pack_id="brkn_thumbnail_capture",
            selected_packs_by_category={
                "wardrobe_makeup": ["wear_goth_classic"],
            },
        )

        self.assertEqual(selection["enabled_pack_ids"], ["brkn_thumbnail_capture", "wear_goth_classic"])
        self.assertIn("brkn_thumbnail_capture.visual_style.catalog_studio", selection["pinned_entry_ids"])
        self.assertIn("brkn_thumbnail_capture.camera_preset.thumbnail_threequarter_fashion", selection["pinned_entry_ids"])
        self.assertIn("brkn_thumbnail_capture.lighting_preset.clean_studio", selection["pinned_entry_ids"])
        self.assertIn("brkn_thumbnail_capture.action.holding_still_catalog", selection["pinned_entry_ids"])
        self.assertIn("brkn_thumbnail_capture.pose.neutral_standing_catalog", selection["pinned_entry_ids"])

    def test_search_finds_cinematic_entries_and_packs(self):
        catalog = load_pack_catalog()
        results = search_catalog(catalog, "warm cinematic interior")
        result_ids = [result[1] for result in results]

        self.assertTrue(any("look_cinematic" in result_id for result_id in result_ids))

    def test_collection_expands_to_installed_pack_ids_and_warns_missing_pack(self):
        catalog = load_pack_catalog()
        selection = build_selection(catalog, selected_collection_ids="greece_vacation")

        self.assertIn("wear_riviera", selection["enabled_pack_ids"])
        self.assertIn("place_yacht", selection["enabled_pack_ids"])
        self.assertNotIn("greece_locations", selection["enabled_pack_ids"])
        self.assertTrue(any("greece_locations" in warning for warning in selection["warnings"]))

    def test_excluded_pack_wins_over_allowed_and_collection_expansion(self):
        catalog = load_pack_catalog()
        selection = build_selection(
            catalog,
            allow_pack_ids="look_cinematic",
            exclude_pack_ids="look_cinematic",
            selected_collection_ids="urban_night_cinema",
        )

        self.assertIn("look_cinematic", selection["expanded_allowed_pack_ids"])
        self.assertNotIn("look_cinematic", selection["enabled_pack_ids"])

    def test_pinned_entry_is_preserved_in_selection_json(self):
        catalog = load_pack_catalog()
        selection = build_selection(
            catalog,
            pin_entry_ids="look_cinematic.visual_style.anamorphic",
        )
        payload = json.loads(selection_to_json(selection))

        self.assertEqual(payload["pinned_entry_ids"], ["look_cinematic.visual_style.anamorphic"])
        self.assertEqual(payload["source_node"], "BRKN Pack Browser and Selector")

    def test_enabled_packs_bridge_string_supports_existing_randomizer_field(self):
        catalog = load_pack_catalog()
        selection = build_selection(catalog, allow_pack_ids="brkn_chassis, wear_goth")

        self.assertEqual(selection_to_enabled_packs(selection), "brkn_chassis, wear_goth")

    def test_node_outputs_json_summary_warnings_and_enabled_packs(self):
        node = BRKNPackBrowserAndSelector()
        result = node.select(
            selected_pack_id="",
            selected_pack_name="greece",
            mode="Collections",
            category_filter="All",
            selected_collection_ids="greece_vacation",
        )

        selection, selection_json, summary, warnings, enabled_packs = result["result"]
        self.assertEqual(selection["schema_version"], "1.1")
        self.assertIn("greece_vacation", selection_json)
        self.assertIn("Collections", summary)
        self.assertIn("greece_locations", warnings)
        self.assertIn("wear_riviera", enabled_packs)


if __name__ == "__main__":
    unittest.main()
