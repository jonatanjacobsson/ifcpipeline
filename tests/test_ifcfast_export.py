"""Unit tests for shared.ifcfast_export (native ifcfast package)."""

from __future__ import annotations

import csv
import os
import tempfile

import pytest

pytest.importorskip("ifcfast")

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from ifcfast_export import (  # noqa: E402
    export_products_csv,
    filter_products_df,
    resolve_export_columns,
)

EXAMPLE_IFC = os.path.join(
    os.path.dirname(__file__),
    "..",
    "shared",
    "examples",
    "Building-Architecture.ifc",
)


@pytest.mark.skipif(not os.path.isfile(EXAMPLE_IFC), reason="sample IFC missing")
def test_export_products_csv_walls() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "walls.csv")
        rows = export_products_csv(
            EXAMPLE_IFC,
            out,
            query="IfcWall",
            attributes=["Name", "Description"],
            include_global_id=True,
        )
        assert rows == 4
        with open(out, encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            assert header[0] == "GlobalId"
            assert len(list(reader)) == 4


def test_resolve_export_columns_maps_global_id() -> None:
    cols = resolve_export_columns(
        ["Name"],
        include_global_id=True,
        available={"guid", "name", "entity"},
    )
    assert cols[0] == ("GlobalId", "guid")


def test_filter_products_df_ifc_element_excludes_spatial() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "entity": ["IfcWall", "IfcBuildingStorey", "IfcDoor"],
            "guid": ["a", "b", "c"],
        }
    )
    out = filter_products_df(df, "IfcElement")
    assert len(out) == 2
    assert "IfcBuildingStorey" not in out["entity"].values
