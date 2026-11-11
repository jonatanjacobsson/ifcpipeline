"""``redact_arguments`` masks secret-named recipe parameters before logging/auditing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_recipes"))
sys.path.insert(0, str(ROOT.parent))

import tasks  # noqa: E402


def test_secret_named_argument_is_masked():
    args = ["proj", "area", "folder", "super-secret-key", "IFC"]
    redacted = tasks.redact_arguments("DaluxUpload", args, use_custom=True)
    assert redacted[3] == "***"
    assert redacted[:3] == args[:3]
    assert "super-secret-key" not in repr(redacted)
    assert args[3] == "super-secret-key"  # input left untouched


def test_non_secret_recipe_is_unchanged():
    args = [".IfcWall"]
    assert tasks.redact_arguments("ExtractElements", args) == args


def test_unknown_recipe_masks_everything():
    assert tasks.redact_arguments("NoSuchRecipe", ["a", "b"], use_custom=True) == ["***", "***"]


def test_empty_arguments():
    assert tasks.redact_arguments("DaluxUpload", [], use_custom=True) == []
    assert tasks.redact_arguments("DaluxUpload", None, use_custom=True) is None
