import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { ComfyDialog, $el } from "../../../scripts/ui.js";

const DIALOG_ID = "brkn-pack-browser-dialog";
let catalogCache = null;

function loadStylesheet() {
  const id = "brkn-pack-browser-css";
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = new URL("./brkn_pack_browser.css?v=4", import.meta.url).href;
  document.head.appendChild(link);
}

function widgetByName(node, name) {
  return node.widgets?.find((widget) => widget.name === name);
}

function setWidgetValue(node, name, value) {
  const widget = widgetByName(node, name);
  if (!widget) return false;
  widget.value = value;
  widget.callback?.(value);
  return true;
}

function parseCsv(value) {
  return String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function parseCategoryJson(value) {
  try {
    const parsed = JSON.parse(String(value || "{}"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function normalize(value) {
  return String(value || "").toLowerCase();
}

async function fetchCatalog() {
  if (catalogCache) return catalogCache;
  const response = await api.fetchApi("/brkn/pack_browser/catalog");
  catalogCache = await response.json();
  return catalogCache;
}

function categoryEntries(catalog) {
  const labels = catalog.prompt_pool_categories || {};
  return [
    ["base_pack", labels.base_pack || "Base Packs"],
    ["camera_lighting", labels.camera_lighting || "Camera Style and Lighting"],
    ["wardrobe_makeup", labels.wardrobe_makeup || "Wardrobe and Makeup"],
    ["location", labels.location || "Locations"],
    ["transportation", labels.transportation || "Transportation"],
    ["actions", labels.actions || "Actions"],
  ];
}

function groupModifierPacks(modifierPacks) {
  const grouped = {
    camera_lighting: [],
    wardrobe_makeup: [],
    location: [],
    transportation: [],
    actions: [],
  };
  for (const pack of modifierPacks) {
    const category = grouped[pack.selection_category] ? pack.selection_category : "camera_lighting";
    grouped[category].push(pack);
  }
  return grouped;
}

function packSummary(basePack, modifierPacks, catalog) {
  const grouped = groupModifierPacks(modifierPacks);
  const lines = [];
  if (basePack) lines.push(`Base: ${basePack.name}`);
  for (const [categoryId, label] of categoryEntries(catalog)) {
    if (categoryId === "base_pack") continue;
    const count = grouped[categoryId]?.length || 0;
    if (count) lines.push(`${label}: ${count}`);
  }
  if (lines.length) return lines.join("\n");
  const modifierNames = modifierPacks.map((pack) => pack.name);
  if (!basePack && !modifierNames.length) return "";
  if (!basePack) return `Modifiers: ${modifierNames.join(", ")}`;
  if (!modifierNames.length) return `Base: ${basePack.name}`;
  return `Base: ${basePack.name}\nModifiers: ${modifierNames.join(", ")}`;
}

class BRKNPackBrowserDialog extends ComfyDialog {
  constructor() {
    super();
    this.selectedBaseId = "";
    this.modifierIds = new Set();
    this.hoveredPack = null;
    this.onApplySelection = null;
    this.catalog = { packs: [], categories: [], warnings: [] };

    this.element = $el("div", {
      id: DIALOG_ID,
      parent: document.body,
      style: {
        alignItems: "center",
        background: "rgba(0, 0, 0, 0.68)",
        bottom: "0",
        display: "none",
        justifyContent: "center",
        left: "0",
        position: "fixed",
        right: "0",
        top: "0",
        zIndex: "10000",
      },
    }, [
      $el("div.brkn-browser-panel", [
        $el("div.brkn-browser-header", [
          $el("div", [
            $el("h2", "Select Packs"),
            $el("p", "Choose one base pack and any number of modifier packs"),
          ]),
          $el("button.brkn-browser-close", {
            textContent: "Close",
            onclick: () => this.close(),
          }),
        ]),
        $el("div.brkn-browser-toolbar", [
          this.searchInput = $el("input.brkn-browser-search", {
            type: "search",
            placeholder: "Search packs",
            oninput: () => this.renderGrid(),
          }),
          this.categorySelect = $el("select.brkn-browser-category", {
            onchange: () => this.renderGrid(),
          }),
        ]),
        $el("div.brkn-browser-body", [
          this.gridEl = $el("div.brkn-browser-grid"),
          $el("aside.brkn-browser-preview", [
            this.previewImageEl = $el("div.brkn-browser-preview-image"),
            this.previewNameEl = $el("h3", "No pack selected"),
            this.previewMetaEl = $el("p", ""),
            this.warningEl = $el("div.brkn-browser-warning", ""),
            $el("div.brkn-browser-selected", [
              $el("h4", "Selected"),
              this.selectedSummaryEl = $el("pre", "None"),
            ]),
            this.confirmButton = $el("button.brkn-browser-confirm", {
              textContent: "Use Selected Packs",
              onclick: () => this.confirmSelection(),
            }),
          ]),
        ]),
      ]),
    ]);
  }

  static launch(currentBaseId, currentModifierIds, currentByCategory, onApplySelection) {
    if (!BRKNPackBrowserDialog.instance) {
      BRKNPackBrowserDialog.instance = new BRKNPackBrowserDialog();
    }
    const dialog = BRKNPackBrowserDialog.instance;
    dialog.selectedBaseId = currentBaseId || "";
    const categorizedIds = Object.values(currentByCategory || {}).flat();
    dialog.modifierIds = new Set([...(currentModifierIds || []), ...categorizedIds]);
    dialog.onApplySelection = onApplySelection;
    dialog.open();
  }

  async open() {
    this.element.style.display = "flex";
    this.catalog = await fetchCatalog();
    this.renderCategories();
    this.renderGrid();
    const initial =
      this.catalog.packs.find((pack) => pack.id === this.selectedBaseId) ||
      this.catalog.packs.find((pack) => this.modifierIds.has(pack.id)) ||
      this.catalog.packs[0];
    this.setPreview(initial || null);
    this.updateSelectedSummary();
  }

  close() {
    this.element.style.display = "none";
  }

  renderCategories() {
    const selected = this.categorySelect.value || "all";
    const categories = [["all", "All Prompt Pools"], ...categoryEntries(this.catalog)];
    this.categorySelect.innerHTML = "";
    for (const [categoryId, label] of categories) {
      this.categorySelect.appendChild($el("option", {
        value: categoryId,
        textContent: label,
        selected: categoryId === selected,
      }));
    }
  }

  filteredPacks() {
    const query = normalize(this.searchInput.value);
    const category = this.categorySelect.value || "all";
    return (this.catalog.packs || []).filter((pack) => {
      const text = normalize([
        pack.id,
        pack.name,
        pack.full_name,
        pack.description,
        pack.selection_role,
        pack.selection_category,
        (pack.tags || []).join(" "),
        (pack.categories || []).join(" "),
      ].join(" "));
      const matchesQuery = !query || text.includes(query);
      const matchesCategory =
        category === "all" ||
        (category === "base_pack" && pack.selection_role === "base") ||
        pack.selection_category === category ||
        (pack.categories || []).includes(category);
      return matchesQuery && matchesCategory;
    });
  }

  isSelected(pack) {
    return pack.selection_role === "base"
      ? pack.id === this.selectedBaseId
      : this.modifierIds.has(pack.id);
  }

  togglePack(pack) {
    if (pack.selection_role === "base") {
      this.selectedBaseId = pack.id;
    } else if (this.modifierIds.has(pack.id)) {
      this.modifierIds.delete(pack.id);
    } else {
      this.modifierIds.add(pack.id);
    }
    this.setPreview(pack);
    this.renderGrid();
    this.updateSelectedSummary();
  }

  renderGrid() {
    const packs = this.filteredPacks();
    this.gridEl.innerHTML = "";
    if (!packs.length) {
      this.gridEl.appendChild($el("div.brkn-browser-empty", "No packs found."));
      return;
    }
    for (const pack of packs) {
      const isBase = pack.selection_role === "base";
      const categoryLabel = pack.selection_category_label || pack.selection_category || pack.category || "";
      const card = $el("button.brkn-browser-card", {
        dataset: { packId: pack.id },
        onclick: () => this.togglePack(pack),
        onmouseenter: () => this.setPreview(pack),
      }, [
        pack.has_thumbnail
          ? $el("img", { src: pack.thumbnail, alt: pack.name, loading: "lazy" })
          : $el("div.brkn-browser-fallback", "No Image"),
        $el("span.brkn-browser-card-name", pack.name),
        $el("small", `${isBase ? "Base" : "Modifier"} - ${categoryLabel}`),
      ]);
      if (this.isSelected(pack)) card.classList.add("selected");
      if (isBase) card.classList.add("base-pack");
      this.gridEl.appendChild(card);
    }
  }

  setPreview(pack) {
    this.hoveredPack = pack;
    this.previewImageEl.innerHTML = "";
    if (!pack) {
      this.previewNameEl.textContent = "No pack selected";
      this.previewMetaEl.textContent = "";
      this.warningEl.textContent = "";
      return;
    }
    if (pack.has_preview || pack.has_thumbnail) {
      this.previewImageEl.appendChild($el("img", {
        src: pack.has_preview ? pack.preview : pack.thumbnail,
        alt: pack.name,
      }));
    } else {
      this.previewImageEl.appendChild($el("div.brkn-browser-preview-fallback", "Missing thumbnail"));
    }
    const role = pack.selection_role === "base" ? "Base pack" : "Modifier pack";
    const categoryLabel = pack.selection_category_label || pack.selection_category || pack.category || "Uncategorized";
    this.previewNameEl.textContent = pack.name;
    this.previewMetaEl.textContent = `${role} - ${categoryLabel} - ${pack.entry_count || 0} entries`;
    this.warningEl.textContent = (pack.warnings || []).join("\n");
  }

  selectedPacks() {
    const packs = this.catalog.packs || [];
    const basePack = packs.find((pack) => pack.id === this.selectedBaseId) || null;
    const modifierPacks = [...this.modifierIds]
      .map((id) => packs.find((pack) => pack.id === id))
      .filter(Boolean);
    return { basePack, modifierPacks };
  }

  updateSelectedSummary() {
    const { basePack, modifierPacks } = this.selectedPacks();
    this.selectedSummaryEl.textContent = packSummary(basePack, modifierPacks, this.catalog) || "None";
  }

  confirmSelection() {
    const { basePack, modifierPacks } = this.selectedPacks();
    const grouped = groupModifierPacks(modifierPacks);
    const groupedIds = Object.fromEntries(
      Object.entries(grouped).map(([categoryId, packs]) => [categoryId, packs.map((pack) => pack.id)])
    );
    this.onApplySelection?.({
      basePack,
      modifierPacks,
      modifierIds: modifierPacks.map((pack) => pack.id),
      selectedPacksByCategory: groupedIds,
      summary: packSummary(basePack, modifierPacks, this.catalog),
    });
    this.close();
  }
}

function createPackBrowserButton(node, inputName) {
  const button = node.addCustomWidget({
    type: "button",
    name: inputName,
    label: "Select Packs...",
    serialize: true,
  });
  button.serializeValue = () => null;
  button.callback = () => {
    const baseWidget = widgetByName(node, "selected_base_pack_id") || widgetByName(node, "selected_pack_id");
    const modifiersWidget = widgetByName(node, "modifier_pack_ids");
    const byCategoryWidget = widgetByName(node, "selected_packs_by_category_json");
    const summaryWidget = widgetByName(node, "selected_pack_summary") || widgetByName(node, "selected_pack_name");
    BRKNPackBrowserDialog.launch(
      baseWidget?.value || "",
      parseCsv(modifiersWidget?.value),
      parseCategoryJson(byCategoryWidget?.value),
      (selection) => {
      if (baseWidget) {
        baseWidget.value = selection.basePack?.id || "";
        baseWidget.callback?.(baseWidget.value);
      }
      if (modifiersWidget) {
        modifiersWidget.value = selection.modifierIds.join(", ");
        modifiersWidget.callback?.(modifiersWidget.value);
      }
      if (byCategoryWidget) {
        byCategoryWidget.value = JSON.stringify(selection.selectedPacksByCategory, null, 2);
        byCategoryWidget.callback?.(byCategoryWidget.value);
      }
      if (summaryWidget) {
        summaryWidget.value = selection.summary;
        summaryWidget.callback?.(selection.summary);
      }
      node.setDirtyCanvas?.(true, true);
    });
  };
  return { widget: button };
}

function addResetButtonOnce(node, name, label, callback) {
  if (!node || node.widgets?.some((widget) => widget.name === name)) return;
  node.addWidget("button", label, null, () => {
    callback(node);
    node.setDirtyCanvas?.(true, true);
  });
}

function resetBrowserSelection(node) {
  setWidgetValue(node, "selected_base_pack_id", "");
  setWidgetValue(node, "selected_pack_id", "");
  setWidgetValue(node, "modifier_pack_ids", "");
  setWidgetValue(node, "selected_packs_by_category_json", "");
  setWidgetValue(node, "selected_pack_summary", "");
  setWidgetValue(node, "selected_pack_name", "");
}

function resetComposerOverrides(node) {
  const neutralValues = {
    visual_category: "any",
    use_character: true,
    use_wardrobe: true,
    use_scene: true,
    use_presentation: true,
    descent: "any",
    action_family: "any",
    time: "random",
    season: "none",
    weather: "none",
    prefix: "",
    suffix: "",
    required_terms: "",
    excluded_terms: "",
    additional_negative_prompt: "",
    enabled_packs: "",
    metadata_json: "",
    reuse_from_metadata: "off",
  };
  const untouched = new Set(["seed", "compatibility_mode", "target_model", "prompt_length"]);
  for (const widget of node.widgets || []) {
    if (!widget?.name || untouched.has(widget.name)) continue;
    if (Object.prototype.hasOwnProperty.call(neutralValues, widget.name)) {
      setWidgetValue(node, widget.name, neutralValues[widget.name]);
      continue;
    }
    if (widget.name.endsWith("_reroll")) {
      setWidgetValue(node, widget.name, 0);
      continue;
    }
    const values = widget.options?.values || widget.options?.items || [];
    if (Array.isArray(values) && values.includes("random")) {
      setWidgetValue(node, widget.name, "random");
    }
  }
}

app.registerExtension({
  name: "BRKN.PackBrowser",
  init() {
    loadStylesheet();
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      originalOnNodeCreated?.apply(this, args);
      if (nodeData.name === "BRKNPackBrowserAndSelector") {
        addResetButtonOnce(this, "brkn_reset_pack_selection", "Reset BRKN Selection", resetBrowserSelection);
      }
      if (nodeData.name === "LifestylePromptComposer") {
        addResetButtonOnce(this, "brkn_reset_prompt_overrides", "Reset BRKN Overrides", resetComposerOverrides);
      }
    };
  },
  getCustomWidgets() {
    return {
      BRKN_PACK_BROWSER: createPackBrowserButton,
    };
  },
});
