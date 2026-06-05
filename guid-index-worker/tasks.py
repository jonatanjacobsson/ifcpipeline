"""GUID indexing worker.

Consumes jobs from the `guid_index` queue. Each job is:
    index_object(audit_id, object_key, version_id, role)

Flow:
    1. Download the pinned version (MinIO VersionId) to a temp path.
    2. Pick the right extractor from shared.guid_extract based on extension.
    3. Stream `(guid, entity_type, role)` rows through audit_db.record_guids
       in 5k batches with ON CONFLICT DO NOTHING.

Idempotent by design — re-running an already-indexed version is a no-op.

The `role` passed in overrides any role the extractor would have produced
(e.g. extract_from_diff_report stamps its own diff_* roles; we let it win).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Iterator, Optional, Tuple

from shared import audit_db
from shared import object_storage as s3
from shared import guid_extract

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "guid-index-worker"

GuidRow = Tuple[str, Optional[str], str]


def _is_diff_report(role: str, key: str) -> bool:
    return role.startswith("diff_") or "diff" in key.lower() and key.lower().endswith(".json")


def _pick_extractor(path: str, role: str) -> Iterator[GuidRow]:
    ext = os.path.splitext(path)[1].lower()
    if _is_diff_report(role, path):
        # Diff reports classify their own roles; we pass them through.
        yield from guid_extract.extract_from_diff_report(path)
        return
    if ext in (".ifc", ".ifczip"):
        base = guid_extract.extract_from_ifc_path(path)
    elif ext == ".json":
        base = guid_extract.extract_from_ifc_json_path(path)
    elif ext in (".csv", ".xlsx"):
        base = guid_extract.extract_from_csv_path(path)
    else:
        logger.info("guid-index: skipping unsupported extension %s (role=%s)", ext, role)
        return
    # Stamp the caller-provided role on every row the base extractor yielded
    # with an empty role.
    for guid, entity, inner_role in base:
        yield (guid, entity, inner_role or role)


def index_object(
    audit_id: int,
    object_key: str,
    version_id: Optional[str] = None,
    role: str = "root",
) -> dict:
    """Extract IFC GUIDs from the given object_version and record them.

    Returns a small summary dict for the job result in rq-dashboard. The
    actual rows go through audit_db.record_guids, which handles batching
    and ON CONFLICT.
    """
    if not audit_id:
        return {"success": False, "error": "missing audit_id"}

    logger.info(
        "guid-index start: audit_id=%s key=%s version_id=%s role=%s",
        audit_id, object_key, version_id, role,
    )

    suffix = os.path.splitext(object_key)[1] or ".bin"
    inserted = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            local_path = tmp.name
            s3.download_to_path(object_key, local_path, version_id=version_id)
            rows = _pick_extractor(local_path, role)
            inserted = audit_db.record_guids(audit_id, rows)
        logger.info(
            "guid-index done: audit_id=%s key=%s inserted~=%d",
            audit_id, object_key, inserted,
        )
        return {
            "success": True,
            "audit_id": audit_id,
            "object_key": object_key,
            "version_id": version_id,
            "role": role,
            "rows_inserted_pre_conflict": inserted,
        }
    except Exception as e:
        logger.exception("guid-index failed: audit_id=%s key=%s", audit_id, object_key)
        return {
            "success": False,
            "audit_id": audit_id,
            "object_key": object_key,
            "error": str(e),
        }
