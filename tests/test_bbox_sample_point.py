"""Unit tests for vertically biased bbox sampling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

import tasks


def test_flat_element_keeps_centroid_z():
    bbox = tasks.BBox(10, 20, 8.5, 12, 22, 8.8)
    sample = tasks._bbox_sample_point(bbox, vertical_offset_m=1.2)
    assert sample[2] == bbox.centroid[2]


def test_tall_element_uses_floor_plus_offset():
    bbox = tasks.BBox(0, 0, 0, 1, 1, 10)
    sample = tasks._bbox_sample_point(bbox, vertical_offset_m=1.2)
    assert sample[0] == bbox.centroid[0]
    assert sample[1] == bbox.centroid[1]
    assert sample[2] == 1.2


def test_short_tallish_element_keeps_centroid():
    bbox = tasks.BBox(0, 0, 0, 1, 1, 1.0)
    sample = tasks._bbox_sample_point(bbox, vertical_offset_m=1.2)
    assert sample[2] == bbox.centroid[2]
