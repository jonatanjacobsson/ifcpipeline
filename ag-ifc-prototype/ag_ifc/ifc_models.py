"""Resolve and optionally download open IFC model paths."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelFile:
    model_set_id: str
    file_id: str
    filename: str
    discipline: str
    path: Path
    source: str


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_manifest() -> dict[str, Any]:
    path = _root() / "scenarios" / "ifc_models" / "manifest.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _download_dir() -> Path:
    return _root() / "scenarios" / "ifc_models" / "downloaded"


def _is_valid_ifc(path: Path, min_bytes: int = 10_000) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size < min_bytes:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(80).decode("utf-8", errors="ignore")
        return "ISO-10303-21" in head
    except OSError:
        return False


def _download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ag-ifc-prototype/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        dest.write_bytes(response.read())


def resolve_model_path(
    model_set: dict[str, Any],
    filename: str,
    *,
    fetch: bool = True,
) -> Path | None:
    """Return path to IFC, using download dir, local fallback, or HTTP fetch."""
    set_id = model_set["id"]
    downloaded = _download_dir() / set_id / filename
    if _is_valid_ifc(downloaded):
        return downloaded

    fallback = model_set.get("local_fallback")
    if fallback:
        candidate = (_root() / fallback / filename).resolve()
        if _is_valid_ifc(candidate):
            return candidate

    if not fetch:
        return None

    base = model_set.get("download_base") or load_manifest().get("download_base")
    if not base:
        return None

    url = f"{base.rstrip('/')}/{filename}"
    try:
        _download_url(url, downloaded)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    if _is_valid_ifc(downloaded):
        return downloaded
    # LFS pointer or HTML error page
    if downloaded.exists():
        downloaded.unlink(missing_ok=True)
    return None


def ensure_model_set(model_set_id: str, *, fetch: bool = True) -> dict[str, ModelFile]:
    manifest = load_manifest()
    model_set = next(
        (ms for ms in manifest["model_sets"] if ms["id"] == model_set_id),
        None,
    )
    if model_set is None:
        raise KeyError(f"unknown model set: {model_set_id}")

    resolved: dict[str, ModelFile] = {}
    for entry in model_set["files"]:
        path = resolve_model_path(model_set, entry["filename"], fetch=fetch)
        if path is None:
            if model_set.get("optional"):
                continue
            raise FileNotFoundError(
                f"Could not resolve {entry['filename']} for set {model_set_id}. "
                f"Run ./scripts/fetch_ifc_models.sh"
            )
        resolved[entry["id"]] = ModelFile(
            model_set_id=model_set_id,
            file_id=entry["id"],
            filename=entry["filename"],
            discipline=entry.get("discipline", ""),
            path=path,
            source=str(model_set.get("source", "")),
        )
    return resolved


def list_available_sets(*, fetch: bool = False) -> list[dict[str, Any]]:
    manifest = load_manifest()
    report = []
    for model_set in manifest["model_sets"]:
        files_status = []
        ok = True
        for entry in model_set["files"]:
            path = resolve_model_path(model_set, entry["filename"], fetch=fetch)
            valid = path is not None and _is_valid_ifc(path)
            files_status.append(
                {
                    "filename": entry["filename"],
                    "available": valid,
                    "path": str(path) if path else None,
                }
            )
            if not valid:
                ok = False
        report.append(
            {
                "id": model_set["id"],
                "name": model_set["name"],
                "ready": ok,
                "lfs_required": model_set.get("lfs_required", False),
                "files": files_status,
            }
        )
    return report
