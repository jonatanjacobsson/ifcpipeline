"""Tests for ``object_storage.safe_upload_basename`` (API gateway upload keys)."""

from __future__ import annotations

import hashlib

import pytest

from shared.object_storage import safe_upload_basename


def _digest_fragment(original_basename: str) -> str:
    base = original_basename.rsplit("/", 1)[-1]
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]


def test_spaces_replaced_and_ascii_safe() -> None:
    out = safe_upload_basename("My Project Name.ifc")
    assert out.endswith(".ifc")
    assert " " not in out
    assert out == f"My_Project_Name_{_digest_fragment('My Project Name.ifc')}.ifc"


def test_swedish_letters_fold_and_safe() -> None:
    out = safe_upload_basename("ÅÄÖ Byggnad.ifc")
    assert out.endswith(".ifc")
    assert all(ord(c) < 128 for c in out)
    assert "Å" not in out and "Ä" not in out and "Ö" not in out


def test_stem_only_non_ascii_becomes_file_prefix() -> None:
    # Currency symbols strip to empty under ASCII folding → fallback stem ``file``.
    out = safe_upload_basename("£¥€.ifc")
    assert out.startswith("file_")
    assert out.endswith(".ifc")


def test_distinct_originals_same_ascii_slug_differ_by_hash() -> None:
    # Both stems fold to "xa" under NFKD + ASCII; full basenames differ → hashes differ.
    a = safe_upload_basename("xä.ifc")
    b = safe_upload_basename("xa.ifc")
    assert a != b
    assert a.startswith("xa_") and a.endswith(".ifc")
    assert b.startswith("xa_") and b.endswith(".ifc")


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
    assert "evil" not in out and "path" not in out
    assert out == f"My_file_{_digest_fragment('My file.ifc')}.ifc"


@pytest.mark.parametrize(
    "original,expected_stem_prefix",
    [
        ("model.ids", "model"),
        ("report.bcf", "report"),
    ],
)
def test_extensions_preserved(original: str, expected_stem_prefix: str) -> None:
    out = safe_upload_basename(original)
    assert out.startswith(f"{expected_stem_prefix}_")
    assert out.endswith(original[original.rindex(".") :].lower())
