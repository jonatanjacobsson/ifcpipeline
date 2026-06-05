"""Tests for `_validate_safe_path` in shared.classes.

Covers the S3 URI acceptance added to unblock gateway endpoints whose
callers (n8n workflows, gateway-to-worker flows) pass the canonical
`s3://<bucket>/<key>` URI that `/upload/*` and `/download-from-url`
now emit. Also pins the existing rejection behaviour so the security
hardening in `4fac594` doesn't silently regress.
"""

from __future__ import annotations

import pytest

from shared.classes import IfcDiffRequest, _validate_safe_path


# ---- happy paths -----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "foo.ifc",
        "uploads/foo.ifc",
        "uploads/sub/foo.ifc",
        "output/diff/report.json",
        "s3://ifcpipeline/uploads/foo.ifc",
        "s3://ifcpipeline/output/diff/report.json",
        "s3://bucket-with-hyphen/key.ifc",
        "s3://bucket_with_underscore/nested/key.ifc",
    ],
)
def test_accepts_safe_paths(path: str) -> None:
    assert _validate_safe_path(path) == path


# ---- legacy rejections (must still fail) -----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../etc/passwd",
        "uploads/../etc/passwd",
        "uploads/foo;rm -rf /.ifc",
        "uploads/$(whoami).ifc",
        "uploads/`id`.ifc",
        "uploads/a|b.ifc",
        "uploads/a&b.ifc",
        "uploads/space file.ifc",  # space not in SAFE_FILENAME_PATTERN
    ],
)
def test_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        _validate_safe_path(path)


# ---- s3 URI edge cases -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "s3://",                     # missing bucket + key
        "s3://bucket",               # missing separator + key
        "s3://bucket/",              # empty key
        "s3:///uploads/foo.ifc",     # empty bucket
        "s3://bad bucket/foo.ifc",   # space in bucket
        "s3://bu:ck/foo.ifc",        # colon in bucket
        "s3://bucket/../escape",     # ".." still rejected after scheme
        "s3://bucket/uploads/$(x)",  # shell meta still rejected
    ],
)
def test_rejects_bad_s3_uris(path: str) -> None:
    with pytest.raises(ValueError):
        _validate_safe_path(path)


# ---- end-to-end through the Pydantic model ---------------------------------


def test_ifcdiff_accepts_s3_uri_from_n8n() -> None:
    """The real-world payload n8n's IfcDiff node sent in execution 9155."""
    r = IfcDiffRequest(
        old_file="s3://ifcpipeline/uploads/E1-600-MM-Complete_model.ifc",
        new_file="s3://ifcpipeline/uploads/E1-600-MM-Complete_model.ifc",
        output_file="E1-600-MM-Complete_model.shallow_diff.json",
        is_shallow=True,
        old_version_id="bd39c0fb-d55d-4ac9-827e-11584755215e",
    )
    # Fields round-trip verbatim; stripping happens later in the worker
    # via shared.object_storage.normalize_input_key.
    assert r.old_file.startswith("s3://")
    assert r.new_file.startswith("s3://")
