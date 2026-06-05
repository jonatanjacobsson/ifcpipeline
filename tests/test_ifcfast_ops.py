"""Tests for native ifcfast operations."""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

pytest.importorskip("ifcfast")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from ifcfast_ops import DATA_LAYERS, layer_dataframe, open_model, run_operation  # noqa: E402

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "shared", "examples", "Building-Architecture.ifc")


@pytest.mark.skipif(not os.path.isfile(EXAMPLE), reason="sample IFC missing")
def test_summary_operation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = run_operation(EXAMPLE, tmp, operation="summary")
        assert result["inline"]["success"]
        assert "schema" in result["inline"]["summary"]
        path = result["artifacts"][0]["local_path"]
        payload = json.load(open(path, encoding="utf-8"))
        assert payload["schema"]


@pytest.mark.skipif(not os.path.isfile(EXAMPLE), reason="sample IFC missing")
def test_export_layer_products() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = run_operation(
            EXAMPLE,
            tmp,
            operation="export_layer",
            layer="products",
            output_format="csv",
        )
        assert result["artifacts"][0]["rows"] >= 1


@pytest.mark.skipif(not os.path.isfile(EXAMPLE), reason="sample IFC missing")
def test_all_layers_known() -> None:
    model = open_model(EXAMPLE)
    for layer in DATA_LAYERS:
        df = layer_dataframe(model, layer)
        assert df is not None
