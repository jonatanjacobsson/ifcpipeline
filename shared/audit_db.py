"""
Audit-trail database layer for the object-storage variant.

Writes to the `object_versions` + `object_lineage` tables defined in
`postgres/init/02-audit.sql`. Reuses `DBClient.get_connection()` so we pick up
the same env vars as the existing result writers.

Public entry points:

- `record_upload(...)`      — root insertion (e.g. POST /upload)
- `record_derivative(...)`  — one derived version + N lineage edges
- `fetch_lineage(key)`      — full ancestor + descendant tree for a key
- `fetch_job_lineage(job)`  — everything produced by a given RQ job
- `fetch_roots(limit, since)` — paginated list of first-time uploads
- `fetch_by_hash(sha256)`   — all keys currently mapped to a content hash

All functions degrade to `None` / `[]` if Postgres is unavailable so a broken
DB never breaks the upload pipeline — the object still makes it to S3.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .db_client import db_client

logger = logging.getLogger(__name__)


def _json():
    # Lazy import so modules that don't touch the DB stay lightweight.
    from psycopg2.extras import Json  # type: ignore
    return Json


def _lookup_version_id(cursor, bucket: str, object_key: str) -> Optional[int]:
    """Return the newest version id for (bucket, key), or None."""
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
    sha256: str,
    size_bytes: int,
    content_type: Optional[str],
    kind: str,
    operation: str,
    worker: Optional[str],
    job_id: Optional[str],
    metadata: Dict[str, Any],
) -> int:
    """Insert a version row (or return the existing id if the exact triple
    (bucket, key, sha256) was already recorded)."""
    Json = _json()
    cursor.execute(
        """
        INSERT INTO object_versions
            (bucket, object_key, sha256, size_bytes, content_type,
             kind, operation, worker, job_id, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bucket, object_key, sha256) DO UPDATE
          SET metadata = object_versions.metadata || EXCLUDED.metadata
        RETURNING id;
        """,
        (
            bucket,
            object_key,
            sha256,
            size_bytes,
            content_type,
            kind,
            operation,
            worker,
            job_id,
            Json(metadata or {}),
        ),
    )
    return cursor.fetchone()[0]


def record_upload(
    *,
    bucket: str,
    object_key: str,
    sha256: str,
    size_bytes: int,
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
            content_type=content_type,
            kind="root",
            operation="upload",
            worker=None,
            job_id=None,
            metadata=metadata or {},
        )
        conn.commit()
        logger.info("audit: recorded root upload id=%s key=%s", vid, object_key)
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
    sha256: str,
    size_bytes: int,
    operation: str,
    worker: str,
    job_id: Optional[str],
    parents: Iterable[Tuple[str, str]],
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Record a worker-produced derivative.

    `parents` is an iterable of (role, parent_key) pairs. Missing parents (not
    previously audited) are tolerated — we log a warning and skip that edge
    rather than blocking the job.
    """
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
            content_type=content_type,
            kind="derived",
            operation=operation,
            worker=worker,
            job_id=job_id,
            metadata=metadata or {},
        )
        for role, parent_key in parents:
            pid = _lookup_version_id(cursor, bucket, parent_key)
            if pid is None:
                logger.warning(
                    "audit: parent %s not found for child %s (role=%s); edge skipped",
                    parent_key, object_key, role,
                )
                continue
            cursor.execute(
                """
                INSERT INTO object_lineage (parent_id, child_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING;
                """,
                (pid, vid, role),
            )
        conn.commit()
        logger.info(
            "audit: recorded derivative id=%s key=%s op=%s job=%s",
            vid, object_key, operation, job_id,
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


def _row_to_dict(row: Tuple) -> Dict[str, Any]:
    (
        vid, bucket, object_key, sha256, size_bytes, content_type,
        kind, operation, worker, job_id, metadata, created_at,
    ) = row
    return {
        "id": vid,
        "bucket": bucket,
        "object_key": object_key,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "content_type": content_type,
        "kind": kind,
        "operation": operation,
        "worker": worker,
        "job_id": job_id,
        "metadata": metadata,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
    }


_VERSION_COLUMNS = (
    "id, bucket, object_key, sha256, size_bytes, content_type, "
    "kind, operation, worker, job_id, metadata, created_at"
)


def fetch_lineage(object_key: str, bucket: Optional[str] = None, depth: int = 10) -> Optional[Dict[str, Any]]:
    """Return `{self, ancestors, descendants}` for the most-recent version of
    `object_key`. `depth` caps the recursion to avoid runaway trees."""
    conn = db_client.get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        # find the self row
        if bucket:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE bucket=%s AND object_key=%s ORDER BY created_at DESC LIMIT 1",
                (bucket, object_key),
            )
        else:
            cursor.execute(
                f"SELECT {_VERSION_COLUMNS} FROM object_versions "
                "WHERE object_key=%s ORDER BY created_at DESC LIMIT 1",
                (object_key,),
            )
        row = cursor.fetchone()
        if not row:
            return None
        self_dict = _row_to_dict(row)
        self_id = self_dict["id"]

        ancestors = []
        cursor.execute(
            f"""
            WITH RECURSIVE up AS (
                SELECT v.{_VERSION_COLUMNS.replace(", ", ", v.")}, l.role, 1 AS depth
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.parent_id
                WHERE l.child_id = %s
                UNION ALL
                SELECT v.{_VERSION_COLUMNS.replace(", ", ", v.")}, l.role, up.depth + 1
                FROM up
                JOIN object_lineage l ON l.child_id = up.id
                JOIN object_versions v ON v.id = l.parent_id
                WHERE up.depth < %s
            )
            SELECT {_VERSION_COLUMNS}, role, depth FROM up ORDER BY depth, id;
            """,
            (self_id, depth),
        )
        for r in cursor.fetchall():
            d = _row_to_dict(r[:12])
            d["role"] = r[12]
            d["depth"] = r[13]
            ancestors.append(d)

        descendants = []
        cursor.execute(
            f"""
            WITH RECURSIVE down AS (
                SELECT v.{_VERSION_COLUMNS.replace(", ", ", v.")}, l.role, 1 AS depth
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.child_id
                WHERE l.parent_id = %s
                UNION ALL
                SELECT v.{_VERSION_COLUMNS.replace(", ", ", v.")}, l.role, down.depth + 1
                FROM down
                JOIN object_lineage l ON l.parent_id = down.id
                JOIN object_versions v ON v.id = l.child_id
                WHERE down.depth < %s
            )
            SELECT {_VERSION_COLUMNS}, role, depth FROM down ORDER BY depth, id;
            """,
            (self_id, depth),
        )
        for r in cursor.fetchall():
            d = _row_to_dict(r[:12])
            d["role"] = r[12]
            d["depth"] = r[13]
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
                SELECT {_VERSION_COLUMNS}, l.role
                FROM object_lineage l
                JOIN object_versions v ON v.id = l.parent_id
                WHERE l.child_id = %s
                """,
                (r["id"],),
            )
            parents = []
            for p in cursor.fetchall():
                pd = _row_to_dict(p[:12])
                pd["role"] = p[12]
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
