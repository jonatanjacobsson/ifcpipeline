import logging
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from config import settings

logger = logging.getLogger(__name__)


@contextmanager
def _get_conn():
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
    )
    try:
        yield conn
    finally:
        conn.close()


def check_health() -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return {"connected": True, "version": conn.server_version}
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
        return {"connected": False, "error": str(e)}


def get_stats() -> dict:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                tables = ["clash_results", "conversion_results", "tester_results", "diff_results"]
                counts = {}
                for table in tables:
                    cur.execute(f"SELECT COUNT(*) as count FROM {table}")
                    row = cur.fetchone()
                    counts[table] = row["count"] if row else 0

                cur.execute("""
                    SELECT 'tester' as type, id, ifc_filename as label,
                           ifc_filename, ids_filename,
                           pass_count, fail_count, created_at
                    FROM tester_results
                    ORDER BY created_at DESC LIMIT 10
                """)
                recent_tests = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT 'clash' as type, id, clash_set_name as label,
                           clash_count, created_at
                    FROM clash_results
                    ORDER BY created_at DESC LIMIT 10
                """)
                recent_clashes = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT 'diff' as type, id, old_file, new_file,
                           diff_count, created_at
                    FROM diff_results
                    ORDER BY created_at DESC LIMIT 10
                """)
                recent_diffs = [dict(r) for r in cur.fetchall()]

                for item in recent_tests + recent_clashes + recent_diffs:
                    if item.get("created_at"):
                        item["created_at"] = item["created_at"].isoformat()

                return {
                    "connected": True,
                    "counts": counts,
                    "recent_tests": recent_tests,
                    "recent_clashes": recent_clashes,
                    "recent_diffs": recent_diffs,
                }
    except Exception as e:
        logger.error(f"Failed to get DB stats: {e}")
        return {"connected": False, "error": str(e), "counts": {}, "recent_tests": [], "recent_clashes": [], "recent_diffs": []}


def get_tester_results(limit: int = 50, offset: int = 0) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, ifc_filename, ids_filename, pass_count, fail_count, created_at
                    FROM tester_results ORDER BY created_at DESC LIMIT %s OFFSET %s
                """, (limit, offset))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].isoformat()
                return rows
    except Exception as e:
        logger.error(f"Failed to get tester results: {e}")
        return []


def get_ids_run_history(ifc_filename: str, limit: int = 2) -> list:
    """Return the `limit` most-recent tester_results rows for *ifc_filename*.

    Each row includes the full ``test_results`` JSONB so callers can extract
    per-spec metrics.  Rows are ordered newest-first, so index 0 is the latest
    run and index 1 (when present) is the immediately preceding run.
    """
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, ifc_filename, ids_filename, pass_count, fail_count,
                           created_at, test_results
                    FROM tester_results
                    WHERE ifc_filename = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (ifc_filename, limit),
                )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].isoformat()
                return rows
    except Exception as e:
        logger.error(f"Failed to get IDS run history for {ifc_filename}: {e}")
        return []


def get_clash_results(limit: int = 50, offset: int = 0) -> list:
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, clash_set_name, output_filename, clash_count, created_at
                    FROM clash_results ORDER BY created_at DESC LIMIT %s OFFSET %s
                """, (limit, offset))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].isoformat()
                return rows
    except Exception as e:
        logger.error(f"Failed to get clash results: {e}")
        return []
