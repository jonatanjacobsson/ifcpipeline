"""Unit tests for ExtractSpaceAttributes classification and helpers."""

from __future__ import annotations

from ingest_scripts.ExtractSpaceAttributes import classify_space_function


def test_classify_centralort_from_long_name():
    assert classify_space_function(long_name="ELCENTRAL") == "centralort"
    assert classify_space_function(long_name="Fläktrum 01") == "centralort"


def test_classify_circulation_types():
    assert classify_space_function(long_name="Korridor") == "korridor"
    assert classify_space_function(long_name="Trapphus A") == "trapphus"
    assert classify_space_function(long_name="Hisshall") == "hisshall"


def test_classify_from_bip_reference():
    assert classify_space_function(reference="ELCENTRAL 2-1108") == "centralort"


def test_no_classification_for_regular_room():
    assert classify_space_function(long_name="Kök", space_name="2-1103-2") is None
