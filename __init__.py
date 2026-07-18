"""ComfyUI entry point.

Registration must never take ComfyUI down with it. If the engine cannot start --
jsonschema missing, schemas unreadable, templates malformed -- we register a
placeholder node that explains the problem instead of raising during ComfyUI's
node scan, which would make the whole custom-node folder disappear silently.
"""

from __future__ import annotations

import traceback

try:
    from .brkn_pack_browser import BRKNPackBrowserAndSelector
except Exception:
    try:
        from brkn_pack_browser import BRKNPackBrowserAndSelector
    except Exception:
        BRKNPackBrowserAndSelector = None

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

try:
    try:
        from .nodes.lifestyle_composer import (
            NODE_CLASS_MAPPINGS as _NODES,
            NODE_DISPLAY_NAME_MAPPINGS as _NAMES,
        )
    except Exception:
        from nodes.lifestyle_composer import (
            NODE_CLASS_MAPPINGS as _NODES,
            NODE_DISPLAY_NAME_MAPPINGS as _NAMES,
        )

    NODE_CLASS_MAPPINGS.update(_NODES)
    NODE_DISPLAY_NAME_MAPPINGS.update(_NAMES)

except Exception:  # noqa: BLE001 - a failed import must not break ComfyUI
    _ERROR = traceback.format_exc()
    print(
        "[Modular Prompt Composer] Failed to load. The node will appear in a "
        "degraded state.\n" + _ERROR
    )

    class PromptComposerLoadError:
        CATEGORY = "prompt_composer"
        FUNCTION = "report"
        RETURN_TYPES = ("STRING",)
        RETURN_NAMES = ("error",)

        @classmethod
        def INPUT_TYPES(cls) -> dict:
            return {"required": {}}

        def report(self) -> tuple[str]:
            return (
                "Modular Prompt Composer failed to load. Install this node's "
                "requirements (pip install -r requirements.txt) and restart "
                "ComfyUI.\n\n" + _ERROR,
            )

    NODE_CLASS_MAPPINGS["PromptComposerLoadError"] = PromptComposerLoadError
    NODE_DISPLAY_NAME_MAPPINGS["PromptComposerLoadError"] = (
        "Prompt Composer (failed to load)"
    )

if BRKNPackBrowserAndSelector is not None:
    NODE_CLASS_MAPPINGS["BRKNPackBrowserAndSelector"] = BRKNPackBrowserAndSelector
    NODE_DISPLAY_NAME_MAPPINGS["BRKNPackBrowserAndSelector"] = "BRKN Pack Browser and Selector"
WEB_DIRECTORY = "web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]


