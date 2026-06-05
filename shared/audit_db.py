"""
Audit-trail database layer for the object-storage variant.

Writes to the `object_versions` + `object_lineage` tables defined in
`postgres/init/03-audit.sql` and extended by `04-versioning.sql` (MinIO
VersionId pinning) and `05-guid-index.sql` (per-entity GUID index).

Public entry points:

- `record_upload(...)`      — root insertion (e.g. POST /upload)
- `record_derivative(...)`  — one derived version + N lineage edges
- `record_guids(...)`       — bulk upsert of extracted GUIDs for a version
- `record_tester_results(...)` / `record_clash_pairs(...)` — specialized sinks
- `fetch_history(key)`      — every version row for a given object key
- `fetch_lineage(key, ...)` — full ancestor + descendant tree, version-aware
- `fetch_job_lineage(job)`  — everything produced by a given RQ job
- `fetch_roots(limit, since)` — paginated list of first-time uploads
- `fetch_by_hash(sha256)`   — all keys currently mapped to a content hash
- `fetch_by_guid(guid, ...)` — every object_version a given IFC GUID appears in
- `fetch_guid_path(guid, ...)` — recursive lineage walk with presence flags

All functions degrade to `None` / `[]` if Postgres is unavailable so a broken
DB never breaks the upload pipeline — the object still makes it to S3.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .db_client import db_client

logger = logging.getLogger(__name__)


def _json():
    # Lazy import so modules that don't touch the DB stay lightweight.
    from psycopg2.extras import Json  # type: ignore
    return Json


def _execute_values():
    from psycopg2.extras import execute_values  # type: ignore
    return execute_values


# --------------------------------------------------------------------------- #
# Version row upsert                                                          #
# --------------------------------------------------------------------------- #


def _lookup_version_id(
    cursor,
    bucket: str,
    object_key: str,
    version_id: Optional[str] = None,
) -> Optional[int]:
    """Return the version row id for a (bucket, key, [version]) triple.

    When `version_id` is provided we look for the exact pinned row. Otherwise
    we return the newest version for that key."""
    if version_id:
        cursor.execute(
            """
            SELECT id FROM object_versions
            WHERE bucket = %s AND object_key = %s AND version_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (bucket, object_key, version_id),
        )
    else:
        cursor.execute(
            """
            SELECT id FROM object_versions
            WHERE bucket = %s AND object_key = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (bucket, object_key),
        )
    row = cursor.fetchone()
    return row[0] if row else None


def _upsert_version(
    cursor,
    *,
    bucket: str,
    object_key: str,
    sha256: Optional[str],
    size_bytes: Optional[int],
    version_id: Optional[str],
    content_type: Optional[str],
    kind: str,
    operation: str,
    worker: Optional[str],
    job_id: Optional[str],
    metadata: Dict[str, Any],
) -> int:
    """Insert a version row (or return the existing id when the tuple
    (bucket, key, COALESCE(version_id, sha256)) already exists). Matches the
    unique expression index created by `04-versioning.sql`."""
    Json = _json()
    # sha256/size may be None in corner cases (native-checksum fallback failed
    # mid-stream) — keep the row anyway so the audit chain isn't lost.
    cursor.execute(
        """
        INSERT INTO object_versions
            (bucket, object_key, sha256, size_bytes, version_id, content_type,
             kind, operation, worker, job_id, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bucket, object_key, COALESCE(version_id, sha256)) DO UPDATE
          SET metadata = object_versions.metadata || EXCLUDED.metadata,
              sha256 = COALESCE(object_versions.sha256, EXCLUDED.sha256),
              size_bytes = COALESCE(object_versions.size_bytes, EXCLUDED.size_bytes),
              content_type = COALESCE(object_versions.content_type, EXCLUDED.content_type)
        RETURNING id;
        """,
        (
            bucket,
            object_key,
            sha256,
            size_bytes,
            version_id,
            content_type,
            kind,
            operation,
            worker,
            job_id,
            Json(metadata or {}),
        ),
    )
    return cursor.fetchone()[0]


# --------------------------------------------------------------------------- #
# Public record_* entry points                                                #
# --------------------------------------------------------------------------- #


def record_upload(
    *,
    bucket: str,
    object_key: str,
    sha256: Optional[str],
    size_bytes: Optional[int],
    version_id: Optional[str] = None,
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Record a first-time (root) upload. Returns the new version id."""
    conn = db_client.get_connection()
    if not conn:
        logger.warning("Audit DB unavailable; skipping root record for %s", object_key)
        return None
    try:
        cursor = conn.cursor()
        vid = _upsert_version(
            cursor,
            bucket=bucket,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
            version_id=version_id,
            content_type=content_type,
            kind="root",
            operation="upload",
            worker=None,
            job_id=None,
            metadata=metadata or {},
        )
        conn.commit()
        logger.info(
            "audit: recorded root upload id=%s key=%s version_id=%s",
            vid, object_key, version_id,
        )
        return vid
    except Exception as e:
        logger.error("audit: record_upload failed: %s", e)
        logger.error(traceback.format_exc())
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def record_derivative(
    *,
    bucket: str,
    object_key: str,
    sha256: Optional[str],
    size_bytes: Optional[int],
    operation: str,
    worker: str,
    job_id: Optional[str],
    parents: Iterable[Tuple[str, str]],
    version_id: Optional[str] = None,
    parent_version_ids: Optional[Dict[str, str]] = None,
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Record a worker-produced derivative.

    `parents` is an iterable of (role, parent_key) pairs.
    `parent_version_ids` is an optional {parent_key: version_id} map — when
    present we resolve parents by (key, version_id) so ancestor walks stay
    exact even after the parent key gets overwritten.
    """
    parent_version_ids = parent_version_ids or {}
    conn = db_client.get_connection()
    if not conn:
        logger.warning("Audit DB unavailable; skipping derivative record for %s", object_key)
        return None
    try:
        cursor = conn.cursor()
        vid = _upsert_version(
            cursor,
            bucket=bucket,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
            version_id=version_id,
            content_type=content_type,
            kind="derived",
            operation=operation,
            worker=worker,
            job_id=job_id,
            metadata=metadata or {},
        )
        for role, parent_key in parents:
            pvid = parent_version_ids.get(parent_key)
            pid = _lookup_version_id(cursor, bucket, parent_key, pvid)
            if pid is None and pvid:
                # pinned version missing; fall back to latest
                pid = _lookup_version_id(cursor, bucket, parent_key)
            if pid is None:
                logger.warning(
                    "audit: parent %s not found for child %s (role=%s); edge skipped",
                    parent_key, object_key, role,
                )
                continue
            cursor.execute(
                """
                INSERT INTO object_lineage (parent_id, child_id, role, parent_version_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING;
                """,
                (pid, vid, role, pvid),
            )
        conn.commit()
        logger.info(
            "audit: recorded derivative id=%s key=%s version_id=%s op=%s job=%s",
            vid, object_key, version_id, operation, job_id,
        )
        return vid
    except Exception as e:
        logger.error("audit: record_derivative failed: %s", e)
        logger.error(traceback.format_exc())
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


# --------------------------------------------------------------------------- #
# GUID writers (used by guid-index-worker + upload hooks)                    #
# --------------------------------------------------------------------------- #


def record_guids(
    object_version_id: int,
    guids: Iterable[Tuple[str, Optional[str], str]],
    *,
    batch_size: int = 5000,
) -> int:
    """Bulk insert `(ifc_guid, entity_type, role)` triples for a given
    object_version. Returns the number of rows written (ignoring ON CONFLICT
    duplicates). Safe to re-run — the UNIQUE (object_version_id, ifc_guid,
    role) constraint keeps it idempotent.
    """
    conn = db_client.get_connection()
    if not conn:
        logger.warning("record_guids: DB unavailable, skipping vid=%s", object_version_id)
        return 0
    total = 0
    try:
        cursor = conn.cursor()
        execute_values = _execute_values()
        buf: List[Tuple[int, str, Optional[str], str]] = []

        def flush():
            nonlocal total
            if not buf:
                return
            execute_values(
                cursor,
                """
                INSERT INTO object_guids
                    (object_version_id, ifc_guid, entity_type, role)
                VALUES %s
                ON CONFLICT (object_version_id, ifc_guid, role) DO NOTHING;
                """,
                buf,
                page_size=1000,
            )
            total += len(buf)
            buf.clear()

        for g in guids:
            guid, entity, role = g
            if not guid:
                continue
            buf.append((object_version_id, guid, entity, role))
            if len(buf) >= batch_size:
                flush()
                conn.commit()
        flush()
        conn.commit()
        logger.info("record_guids: vid=%s inserted~=%d (pre-conflict)", object_version_id, total)
        return total
    except Exception as e:
        logger.error("record_guids failed vid=%s: %s", object_version_id, e)
        logger.error(traceback.format_exc())
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def record_tester_results(
    object_version_id: int,
    rows: Iterable[Tuple[str, str, bool, Optional[str]]],
    *,
    batch_size: int = 5000,
) -> int:
    """Bulk insert `(ifc_guid, ids_rule, passed, reason)` rows."""
    conn = db_client.get_connection()
    if not conn:
        return 0
    total = 0
    try:
        cursor = conn.cursor()
        execute_values = _execute_values()
        buf: List[Tuple[int, str, str, bool, Optional[str]]] = []

        def flush():
            nonlocal total
            if not buf:
                return
            execute_values(
                cursor,
                """
                INSERT INTO tester_results
                    (object_version_id, ifc_guid, ids_rule, passed, reason)
                VALUES %s
                ON CONFLICT (object_version_id, ifc_guid, ids_rule) DO NOTHING;
                """,
                buf,
                page_size=1000,
            )
            total += len(buf)
            buf.clear()

        for guid, rule, passed, reason in rows:
            if not guid or not rule:
                continue
            buf.append((object_version_id, guid, rule, bool(passed), reason))
            if len(buf) >= batch_size:
                flush()
                conn.commit()
        flush()
        conn.commit()
        logger.info("record_tester_results: vid=%s inserted~=%d", object_version_id, total)
        return total
    except Exception as e:
        logger.error("record_tester_results failed: %s", e)
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def record_clash_pairs(
    object_version_id: int,
    rows: Iterable[Tuple[str, str, Optional[float], Optional[str]]],
    *,
    batch_size: int = 5000,
) -> int:
    """Bulk insert `(guid_a, guid_b, distance, kind)` rows."""
    conn = db_client.get_connection()
    if not conn:
        return 0
    total = 0
    try:
        cursor = conn.cursor()
        execute_values = _execute_values()
        buf: List[Tuple[int, str, str, Optional[float], Optional[str]]] = []

        def flush():
            nonlocal total
            if not buf:
                return
            execute_values(
                cursor,
                """
                INSERT INTO clash_pairs
                    (object_version_id, guid_a, guid_b, distance, kind)
                VALUES %s
                """,
                buf,
                page_size=1000,
            )
            total += len(buf)
            buf.clear()

        for a, b, dist, kind in rows:
            if not a or not b:
                continue
            buf.append((object_version_id, a, b, dist, kind))
            if len(buf) >= batch_size:
                flush()
                conn.commit()
        flush()
        conn.commit()
        logger.info("record_clash_pairs: vid=%s inserted=%d", object_version_id, total)
        return total
    except Exception as e:
        logger.error("record_clash_pairs failed: %s", e)
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


# --------------------------------------------------------------------------- #
# Read helpers                                                                #
# --------------------------------------------------------------------------- #


_VERSION_COLUMNS = (
    "id, bucket, object_key, sha256, size_bytes, version_id, content_type, "
    "kind, operation, worker, job_id, metadata, created_at"
)


def _row_to_dict(row: Sequence[Any]) -> Dict[str, Any]:
    (
        vid, bucket, object_key, sha256, size_bytes, version_id, content_type,
        kind, operation, worker, job_id, metadata, created_at,
    ) = row[:13]
    return {
        "id": vid,
        "bucket": bucket,
        "object_key": object_key,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "version_id": version_id,
        "content_type": content_type,
        "kind": kind,
        "operation": operation,
        "worker": worker,
        "job_id": job_id,
        "metadata": metadata,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
    }


def fetch_version_pin_by_audit_id(audit_id: int) -> Optional[Dict[str, Any]]:
    """Return an `object_versions` row dict for primary key `id`, or None.

    Used by `object_storage.pin_for` when the client sends `input_audit_id`
    instead of a MinIO VersionId string. Degrades to None if the DB is down.
    """
    if not audit_id:
        return None
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        row = _select_version_row(cursor, audit_id=audit_id)
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.error("audit: fetch_version_pin_by_audit_id failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def resolve_original_filename(
    *,
    object_key: Optional[str] = None,
    bucket: Optional[str] = None,
    version_id: Optional[str] = None,
    audit_id: Optional[int] = None,
    depth: int = 10,
) -> Optional[str]:
    """Resolve the human-readable upload name (`metadata.original_filename`)
    for an audited object.

    Lookup order:
      1. Pinned row (audit_id, or object_key [+ version_id [+ bucket]]).
      2. Ancestors walked via `object_lineage`, nearest first, up to `depth`.

    Returns the first non-empty `original_filename` found, or `None` when the
    DB is unavailable, the row does not exist, or no ancestor carries the
    field. Never raises — worker job results must not fail on this lookup.
    """
    if not audit_id and not object_key:
        return None
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        row = _select_version_row(
            cursor,
            object_key=object_key,
            bucket=bucket,
            audit_id=audit_id,
            version_id=version_id,
        )
        if not row:
            return None
        self_dict = _row_to_dict(row)
        name = _original_filename_from_metadata(self_dict.get("metadata"))
        if name:
            return name

        cursor.execute(
            f"""
            WITH RECURSIVE up AS (
                SELECT {_prefix('v')}, 1 AS depth
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.parent_id
                WHERE l.child_id = %s
                UNION ALL
                SELECT {_prefix('v')}, up.depth + 1
                FROM up
                JOIN object_lineage l ON l.child_id = up.id
                JOIN object_versions v ON v.id = l.parent_id
                WHERE up.depth < %s
            )
            SELECT {_VERSION_COLUMNS} FROM up ORDER BY depth ASC, id ASC;
            """,
            (self_dict["id"], depth),
        )
        for r in cursor.fetchall():
            name = _original_filename_from_metadata(_row_to_dict(r).get("metadata"))
            if name:
                return name
        return None
    except Exception as e:
        logger.warning("audit: resolve_original_filename failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def _original_filename_from_metadata(metadata: Any) -> Optional[str]:
    """Pull a non-empty `original_filename` out of a JSONB metadata blob."""
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("original_filename")
    if isinstance(name, str):
        name = name.strip()
        if name:
            return name
    return None


def _prefix(alias: str) -> str:
    """Return `_VERSION_COLUMNS` with every column prefixed by `alias.`."""
    return ", ".join(f"{alias}.{c}" for c in _VERSION_COLUMNS.split(", "))


def _select_version_row(
    cursor,
    *,
    object_key: Optional[str] = None,
    bucket: Optional[str] = None,
    audit_id: Optional[int] = None,
    version_id: Optional[str] = None,
) -> Optional[Sequence[Any]]:
    """Look up a single row in object_versions. Precedence:
    1. audit_id (exact primary key)
    2. (object_key, version_id)
    3. newest row for object_key"""
    if audit_id:
        cursor.execute(
            f"SELECT {_VERSION_COLUMNS} FROM object_versions WHERE id = %s",
            (audit_id,),
        )
        return cursor.fetchone()
    if not object_key:
        return None
    params: List[Any] = []
    where = ["object_key = %s"]
    params.append(object_key)
    if bucket:
        where.append("bucket = %s")
        params.append(bucket)
    if version_id:
        where.append("version_id = %s")
        params.append(version_id)
    cursor.execute(
        f"SELECT {_VERSION_COLUMNS} FROM object_versions "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        tuple(params),
    )
    return cursor.fetchone()


def find_by_source_etag(
    *,
    bucket: str,
    object_key: str,
    source_etag: str,
) -> Optional[Dict[str, Any]]:
    """Return the newest row for `(bucket, object_key)` whose
    `metadata->>'source_etag'` matches `source_etag`, or `None`.

    Used by `/download-from-url` to short-circuit re-downloads when the
    caller can prove (via an upstream eTag / SharePoint version id / etc.)
    that the remote source hasn't changed since the last upload.
    """
    if not source_etag:
        return None
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_VERSION_COLUMNS} FROM object_versions "
            "WHERE bucket=%s AND object_key=%s "
            "AND metadata->>'source_etag' = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (bucket, object_key, source_etag),
        )
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.error("audit: find_by_source_etag failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def fetch_history(
    object_key: str,
    *,
    bucket: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Every object_versions row for a given key, newest first. Key result
    for the day-2 overwrite story: both versions show up."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        if bucket:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE bucket=%s AND object_key=%s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (bucket, object_key, limit),
            )
        else:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE object_key=%s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (object_key, limit),
            )
        return [_row_to_dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("audit: fetch_history failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_lineage(
    object_key: Optional[str] = None,
    bucket: Optional[str] = None,
    depth: int = 10,
    *,
    audit_id: Optional[int] = None,
    version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return `{self, ancestors, descendants}` starting from the row pinned by
    `audit_id` (best), `(object_key, version_id)`, or the newest version of
    `object_key` (legacy default).

    `depth` caps the recursion to avoid runaway trees.
    """
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        row = _select_version_row(
            cursor,
            object_key=object_key,
            bucket=bucket,
            audit_id=audit_id,
            version_id=version_id,
        )
        if not row:
            return None
        self_dict = _row_to_dict(row)
        self_id = self_dict["id"]

        ancestors: List[Dict[str, Any]] = []
        cursor.execute(
            f"""
            WITH RECURSIVE up AS (
                SELECT {_prefix('v')}, l.role, l.parent_version_id, 1 AS depth
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.parent_id
                WHERE l.child_id = %s
                UNION ALL
                SELECT {_prefix('v')}, l.role, l.parent_version_id, up.depth + 1
                FROM up
                JOIN object_lineage l ON l.child_id = up.id
                JOIN object_versions v ON v.id = l.parent_id
                WHERE up.depth < %s
            )
            SELECT {_VERSION_COLUMNS}, role, parent_version_id, depth
            FROM up ORDER BY depth, id;
            """,
            (self_id, depth),
        )
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["role"] = r[13]
            d["parent_version_id"] = r[14]
            d["depth"] = r[15]
            ancestors.append(d)

        descendants: List[Dict[str, Any]] = []
        cursor.execute(
            f"""
            WITH RECURSIVE down AS (
                SELECT {_prefix('v')}, l.role, l.parent_version_id, 1 AS depth
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.child_id
                WHERE l.parent_id = %s
                UNION ALL
                SELECT {_prefix('v')}, l.role, l.parent_version_id, down.depth + 1
                FROM down
                JOIN object_lineage l ON l.parent_id = down.id
                JOIN object_versions v ON v.id = l.child_id
                WHERE down.depth < %s
            )
            SELECT {_VERSION_COLUMNS}, role, parent_version_id, depth
            FROM down ORDER BY depth, id;
            """,
            (self_id, depth),
        )
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["role"] = r[13]
            d["parent_version_id"] = r[14]
            d["depth"] = r[15]
            descendants.append(d)

        return {"self": self_dict, "ancestors": ancestors, "descendants": descendants}
    except Exception as e:
        logger.error("audit: fetch_lineage failed: %s", e)
        logger.error(traceback.format_exc())
        return None
    finally:
        if conn:
            conn.close()


def fetch_job_lineage(job_id: str) -> List[Dict[str, Any]]:
    """Return every version produced by `job_id`, each with its parent list."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_VERSION_COLUMNS} FROM object_versions "
            "WHERE job_id=%s ORDER BY created_at ASC, id ASC",
            (job_id,),
        )
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
        for r in rows:
            cursor.execute(
                f"""
                SELECT {_VERSION_COLUMNS}, l.role, l.parent_version_id
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.parent_id
                WHERE l.child_id = %s
                """,
                (r["id"],),
            )
            parents: List[Dict[str, Any]] = []
            for p in cursor.fetchall():
                pd = _row_to_dict(p)
                pd["role"] = p[13]
                pd["parent_version_id"] = p[14]
                parents.append(pd)
            r["parents"] = parents
        return rows
    except Exception as e:
        logger.error("audit: fetch_job_lineage failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_roots(limit: int = 50, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """List recent root (first-time) uploads."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        if since:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE kind='root' AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT %s",
                (since, limit),
            )
        else:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE kind='root' ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        return [_row_to_dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("audit: fetch_roots failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_by_hash(sha256: str) -> List[Dict[str, Any]]:
    """All versions (root or derived) with the given content hash."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_VERSION_COLUMNS} FROM object_versions "
            "WHERE sha256=%s ORDER BY created_at DESC",
            (sha256,),
        )
        return [_row_to_dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("audit: fetch_by_hash failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


# --------------------------------------------------------------------------- #
# GUID read helpers                                                           #
# --------------------------------------------------------------------------- #


def fetch_by_guid(
    ifc_guid: str,
    *,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Every object_version row where `ifc_guid` appears, newest first.
    Paginate via `after_id` (the last row's `id` from the previous page)."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        params: List[Any] = [ifc_guid]
        where = ["g.ifc_guid = %s"]
        if after_id:
            where.append("v.id < %s")
            params.append(after_id)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT DISTINCT ON (v.id) {_prefix('v')},
                   g.entity_type, g.role
            FROM object_guids g
            JOIN object_versions v ON v.id = g.object_version_id
            WHERE {' AND '.join(where)}
            ORDER BY v.id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["entity_type"] = r[13]
            d["role"] = r[14]
            rows.append(d)
        return rows
    except Exception as e:
        logger.error("audit: fetch_by_guid failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_guid_path(ifc_guid: str, depth: int = 20) -> Optional[Dict[str, Any]]:
    """Walk the lineage graph anchored at every version that contains
    `ifc_guid`, annotating each node with `present: bool` and flagging the
    `object_lineage` edges where a parent contained the GUID but the child
    did not (`dropped_at`).

    Returns `{nodes, edges, dropped_at}` where:
      - nodes: list of distinct object_versions touched, each with `present`
      - edges: list of parent/child id pairs from object_lineage
      - dropped_at: edges where `parent.present && !child.present`, annotated
        with the operation that caused the drop
    """
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        # 1. Seed: versions where the GUID appears.
        cursor.execute(
            """
            SELECT object_version_id FROM object_guids WHERE ifc_guid = %s
            """,
            (ifc_guid,),
        )
        present_ids = {r[0] for r in cursor.fetchall()}
        if not present_ids:
            return {"nodes": [], "edges": [], "dropped_at": []}

        # 2. Expand up and down from each seed.
        cursor.execute(
            f"""
            WITH RECURSIVE seeds AS (
                SELECT id FROM object_versions WHERE id = ANY(%s)
            ),
            up AS (
                SELECT v.id, l.parent_id AS neighbor_id, 1 AS depth
                FROM seeds s
                JOIN object_lineage l ON l.child_id = s.id
                JOIN object_versions v ON v.id = l.child_id
                UNION ALL
                SELECT v.id, l.parent_id, up.depth + 1
                FROM up
                JOIN object_lineage l ON l.child_id = up.neighbor_id
                JOIN object_versions v ON v.id = l.child_id
                WHERE up.depth < %s
            ),
            down AS (
                SELECT v.id, l.child_id AS neighbor_id, 1 AS depth
                FROM seeds s
                JOIN object_lineage l ON l.parent_id = s.id
                JOIN object_versions v ON v.id = l.parent_id
                UNION ALL
                SELECT v.id, l.child_id, down.depth + 1
                FROM down
                JOIN object_lineage l ON l.parent_id = down.neighbor_id
                JOIN object_versions v ON v.id = l.parent_id
                WHERE down.depth < %s
            )
            SELECT DISTINCT id FROM (
                SELECT id FROM seeds
                UNION ALL SELECT neighbor_id FROM up
                UNION ALL SELECT neighbor_id FROM down
            ) q;
            """,
            (list(present_ids), depth, depth),
        )
        all_ids = sorted({r[0] for r in cursor.fetchall()})

        if not all_ids:
            return {"nodes": [], "edges": [], "dropped_at": []}

        cursor.execute(
            f"SELECT {_VERSION_COLUMNS} FROM object_versions WHERE id = ANY(%s) "
            "ORDER BY created_at ASC, id ASC",
            (all_ids,),
        )
        nodes: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["present"] = d["id"] in present_ids
            nodes.append(d)

        cursor.execute(
            """
            SELECT parent_id, child_id, role, parent_version_id
            FROM object_lineage
            WHERE parent_id = ANY(%s) AND child_id = ANY(%s)
            """,
            (all_ids, all_ids),
        )
        edges: List[Dict[str, Any]] = []
        dropped_at: List[Dict[str, Any]] = []
        node_ops = {n["id"]: n.get("operation") for n in nodes}
        node_workers = {n["id"]: n.get("worker") for n in nodes}
        for parent_id, child_id, role, parent_version_id in cursor.fetchall():
            edges.append({
                "parent_id": parent_id,
                "child_id": child_id,
                "role": role,
                "parent_version_id": parent_version_id,
            })
            if parent_id in present_ids and child_id not in present_ids:
                dropped_at.append({
                    "parent_id": parent_id,
                    "child_id": child_id,
                    "role": role,
                    "operation": node_ops.get(child_id),
                    "worker": node_workers.get(child_id),
                })
        return {"nodes": nodes, "edges": edges, "dropped_at": dropped_at}
    except Exception as e:
        logger.error("audit: fetch_guid_path failed: %s", e)
        logger.error(traceback.format_exc())
        return None
    finally:
        if conn:
            conn.close()


def fetch_guid_tester(
    ifc_guid: str,
    *,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        params: List[Any] = [ifc_guid]
        where = ["t.ifc_guid = %s"]
        if after_id:
            where.append("v.id < %s")
            params.append(after_id)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT {_prefix('v')}, t.ids_rule, t.passed, t.reason
            FROM tester_results t
            JOIN object_versions v ON v.id = t.object_version_id
            WHERE {' AND '.join(where)}
            ORDER BY v.id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["ids_rule"] = r[13]
            d["passed"] = r[14]
            d["reason"] = r[15]
            rows.append(d)
        return rows
    except Exception as e:
        logger.error("audit: fetch_guid_tester failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_guid_clashes(
    ifc_guid: str,
    *,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        params: List[Any] = [ifc_guid, ifc_guid]
        where = ["(c.guid_a = %s OR c.guid_b = %s)"]
        if after_id:
            where.append("v.id < %s")
            params.append(after_id)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT {_prefix('v')}, c.guid_a, c.guid_b, c.distance, c.kind
            FROM clash_pairs c
            JOIN object_versions v ON v.id = c.object_version_id
            WHERE {' AND '.join(where)}
            ORDER BY v.id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["guid_a"] = r[13]
            d["guid_b"] = r[14]
            d["distance"] = r[15]
            d["kind"] = r[16]
            rows.append(d)
        return rows
    except Exception as e:
        logger.error("audit: fetch_guid_clashes failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def fetch_guid_diffs(
    ifc_guid: str,
    *,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Versions where this GUID appears with a `diff_*` role."""
    conn = db_client.get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        params: List[Any] = [ifc_guid]
        where = ["g.ifc_guid = %s", "g.role LIKE 'diff_%%'"]
        if after_id:
            where.append("v.id < %s")
            params.append(after_id)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT {_prefix('v')}, g.entity_type, g.role
            FROM object_guids g
            JOIN object_versions v ON v.id = g.object_version_id
            WHERE {' AND '.join(where)}
            ORDER BY v.id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows: List[Dict[str, Any]] = []
        for r in cursor.fetchall():
            d = _row_to_dict(r)
            d["entity_type"] = r[13]
            d["role"] = r[14]
            rows.append(d)
        return rows
    except Exception as e:
        logger.error("audit: fetch_guid_diffs failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def lookup_by_source_etag(source_etag: str, object_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the most recent object_version row matching a source_etag.

    This is used by `/download-from-url` to short-circuit re-downloads when
    the upstream source version hasn't changed. The source_etag is an opaque
    token (e.g. "sp:<sharepoint_file_id>:v<version_id>") stored in the
    version metadata during the initial download.

    When `object_key` is provided, the lookup is scoped to that key only,
    preventing collisions where two different files share the same source
    identifier by coincidence.

    Returns the version row dict or None if no match.
    """
    if not source_etag:
        return None
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        Json = _json()
        cursor = conn.cursor()
        # Use the GIN index on metadata for fast lookup
        if object_key:
            cursor.execute(
                """
                SELECT id, bucket, object_key, version_id, sha256, size_bytes, metadata, created_at
                FROM object_versions
                WHERE metadata @> %s AND object_key = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (Json({"source_etag": source_etag}), object_key),
            )
        else:
            cursor.execute(
                """
                SELECT id, bucket, object_key, version_id, sha256, size_bytes, metadata, created_at
                FROM object_versions
                WHERE metadata @> %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (Json({"source_etag": source_etag}),),
            )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "bucket": row[1],
            "object_key": row[2],
            "version_id": row[3],
            "sha256": row[4],
            "size_bytes": row[5],
            "metadata": row[6],
            "created_at": row[7],
        }
    except Exception as e:
        logger.error("audit: lookup_by_source_etag failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()
