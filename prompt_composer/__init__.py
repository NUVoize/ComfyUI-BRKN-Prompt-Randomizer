"""ComfyUI Modular Prompt Composer -- core engine.

This package must never import ComfyUI. It is a plain Python library so that
every phase can be exercised with ordinary unit tests.
"""

from __future__ import annotations

from pathlib import Path

from .constants import (
    CONTENT_TYPES,
    CompatibilityMode,
    PromptLength,
    TargetModel,
)
from .errors import Diagnostic, ErrorCode, Severity
from .loader import PackLoader
from .models import Entry, LoadReport, Manifest, Pack
from .registry import ContentRegistry, build_registry
from .validator import SchemaValidator

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = PACKAGE_ROOT / "schemas"
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
BUNDLED_PACKS_DIR = PACKAGE_ROOT / "packs" / "bundled"
USER_PACKS_DIR = PACKAGE_ROOT / "packs" / "user"

__all__ = [
    "CONTENT_TYPES",
    "BUNDLED_PACKS_DIR",
    "CompatibilityMode",
    "ContentRegistry",
    "Diagnostic",
    "Entry",
    "ErrorCode",
    "LoadReport",
    "Manifest",
    "Pack",
    "PackLoader",
    "PromptLength",
    "SCHEMAS_DIR",
    "SchemaValidator",
    "Severity",
    "TEMPLATES_DIR",
    "TargetModel",
    "USER_PACKS_DIR",
    "build_registry",
    "load_default_registry",
]


def load_default_registry() -> tuple[ContentRegistry, LoadReport]:
    """Load bundled + user packs from the standard locations."""
    validator = SchemaValidator(SCHEMAS_DIR)
    loader = PackLoader(validator)
    packs, report = loader.load_all([BUNDLED_PACKS_DIR, USER_PACKS_DIR])
    registry = build_registry(packs, report)
    return registry, report
