"""Tests for ``object_storage.safe_upload_basename`` (API gateway upload keys).

The storage basename is a deterministic, ASCII-safe slug of the client
filename with no per-upload or per-name suffix: the same filename always maps
to the same ``uploads/`` key, so a re-upload replaces the object in place. The
bucket is versioned and each upload's VersionId is recorded in the audit
table, so the earlier bytes stay addressable. ``/download-from-url``'s
``source_etag`` cache and callers that reuse the returned key rely on this
stability (see also ``tests/test_download_url_request.py``).
"""

from __future__ import annotations

import pytest

from shared.object_storage import safe_upload_basename


def test_spaces_replaced_and_ascii_safe() -> None:
    out = safe_upload_basename("My Project Name.ifc")
    assert out == "My_Project_Name.ifc"


def test_swedish_letters_fold_and_safe() -> None:
    out = safe_upload_basename("ÅÄÖ Byggnad.ifc")
    assert out == "AAO_Byggnad.ifc"
    assert all(ord(c) < 128 for c in out)


def test_stem_only_non_ascii_becomes_file_prefix() -> None:
    # Currency symbols strip to empty under ASCII folding → fallback stem ``file``.
    assert safe_upload_basename("£¥€.ifc") == "file.ifc"


def test_same_name_always_maps_to_same_key() -> None:
    # Re-uploading a file must replace it in place, not create a sibling key.
    assert safe_upload_basename("My file.ifc") == safe_upload_basename("My file.ifc")
    assert safe_upload_basename("dir/My file.ifc") == safe_upload_basename("My file.ifc")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known limitation: names that differ only in characters the slug drops or "
        "folds (ä/a, space/underscore, 'a (1)'/'a 1') share one key and overwrite each "
        "other. Fixing it changes the key of every such upload, so it is a deliberate "
        "key-scheme change, not a test fix."
    ),
)
def test_distinct_originals_same_ascii_slug_get_distinct_keys() -> None:
    assert safe_upload_basename("xä.ifc") != safe_upload_basename("xa.ifc")


def test_multipart_extension_lowercased() -> None:
    out = safe_upload_basename("model.BCFZIP")
    assert out.endswith(".bcfzip")


def test_long_stem_truncated() -> None:
    long_stem = "a" * 300
    original = f"{long_stem}.ifc"
    out = safe_upload_basename(original)
    assert out.endswith(".ifc")
    # uploads/ prefix is added by gateway; basename alone must stay bounded
    assert len(out) < 250


def test_basename_only_strips_directories() -> None:
    out = safe_upload_basename("/evil/path/My file.ifc")
    assert out == "My_file.ifc"


@pytest.mark.parametrize(
    "original,expected",
    [
        ("model.ids", "model.ids"),
        ("report.bcf", "report.bcf"),
        ("Report.BCF", "Report.bcf"),
    ],
)
def test_extensions_preserved(original: str, expected: str) -> None:
    assert safe_upload_basename(original) == expected
