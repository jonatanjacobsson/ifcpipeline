"""Tests for DownloadUrlRequest and upload filename resolution."""

from __future__ import annotations

import pytest

from shared.classes import DownloadUrlRequest, _validate_original_upload_basename
from shared.object_storage import build_upload_key_from_original, resolve_upload_filename


def test_download_url_request_accepts_spaces_and_unicode() -> None:
    req = DownloadUrlRequest(
        url="https://example.com/file",
        output_filename="M1-570-MM-Complete model.ifc",
    )
    assert req.output_filename == "M1-570-MM-Complete model.ifc"


def test_download_url_request_strips_directories() -> None:
    req = DownloadUrlRequest(
        url="https://example.com/file",
        output_filename="/evil/path/My file.ifc",
    )
    assert req.output_filename == "My file.ifc"


def test_download_url_request_rejects_path_traversal_in_basename() -> None:
    with pytest.raises(ValueError):
        DownloadUrlRequest(
            url="https://example.com/file",
            output_filename="..",
        )


def test_resolve_upload_filename_spaces_to_underscores() -> None:
    original, storage = resolve_upload_filename("M1-570-MM-Complete model.ifc")
    assert original == "M1-570-MM-Complete model.ifc"
    assert storage == "M1-570-MM-Complete_model.ifc"


def test_resolve_upload_filename_master_model() -> None:
    original, storage = resolve_upload_filename("S2-200-MM-MASTER MODEL.ifc")
    assert original == "S2-200-MM-MASTER MODEL.ifc"
    assert storage == "S2-200-MM-MASTER_MODEL.ifc"


def test_build_upload_key_from_original() -> None:
    original, storage, key = build_upload_key_from_original("M1-570-MM-Complete model.ifc")
    assert original == "M1-570-MM-Complete model.ifc"
    assert storage == "M1-570-MM-Complete_model.ifc"
    assert key == "uploads/M1-570-MM-Complete_model.ifc"


@pytest.mark.parametrize(
    "name",
    [
        "S2-2b-200-1-020-S02 - SAMORDNINGSPLAN INGJUTNINGSGODS.ifc",
        "SP1-540-MM-MASTER MODEL.ifc",
    ],
)
def test_original_upload_basename_accepts_sharepoint_names(name: str) -> None:
    assert _validate_original_upload_basename(name) == name
