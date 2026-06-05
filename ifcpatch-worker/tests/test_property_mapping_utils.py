"""Tests for shared property-mapping argument normalization."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from _property_mapping_utils import (  # noqa: E402
    is_blank_argument,
    normalize_bool_argument,
    normalize_mapping_module,
)


def test_is_blank_argument():
    assert is_blank_argument(None)
    assert is_blank_argument("")
    assert is_blank_argument("   ")
    assert is_blank_argument("undefined")
    assert not is_blank_argument("nobel_a1_contract_id")


def test_normalize_mapping_module_blank_uses_default():
    assert (
        normalize_mapping_module("", "nobel_a1_kostengruppe_bsabe")
        == "nobel_a1_kostengruppe_bsabe"
    )
    assert (
        normalize_mapping_module("  ", "nobel_a1_kostengruppe_bsabe")
        == "nobel_a1_kostengruppe_bsabe"
    )


def test_normalize_mapping_module_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid mapping_module"):
        normalize_mapping_module("../evil", "nobel_a1_kostengruppe_bsabe")


def test_normalize_bool_argument_blank_keeps_default():
    assert normalize_bool_argument("", True) is True
    assert normalize_bool_argument("  ", False) is False
    assert normalize_bool_argument("true", False) is True
