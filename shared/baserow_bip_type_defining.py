"""Sync helpers for BIP001: Baserow «BIM - Egenskaper» type-defining BIP properties.

Used by ifc-gherkin-worker Behave steps. Reads optional env (no secrets in client bundles).
Each value falls back to the CDE-style name so the worker can reuse ``../cde/.env`` via compose:

- ``BASEROW_API_BASE`` or ``CDE_BASEROW_API_BASE`` — e.g. ``https://baserow.example.com/api``
- ``BASEROW_API_TOKEN`` or ``CDE_BASEROW_API_KEY`` — database token
- ``BASEROW_BIM_PROPERTIES_TABLE_ID`` or ``CDE_BASEROW_IDS_PROPERTIES_TABLE_ID`` — default ``1234``
- ``BASEROW_PROJECT_NAME`` — optional; when set, same project filter as CDE IDS (Projekt link values)
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# See baserow_drm_objects: Cloudflare blocks requests without a non-Python-urllib UA.
_BASEROW_REQUEST_HEADERS_BASE = {
    "User-Agent": "ifcpipeline-baserow-client/1.0",
    "Accept": "application/json",
}

_TYPE_DEFINING_KEYS: Tuple[str, ...] = (
    "Type Defining",
    "Type defining",
    "Typ definierande",
    "typ definierande",
    "Typedefining",
    "Type Definition",
    "Type definition",
)

_BIP_PSET_NORM = re.compile(r"[^A-Z0-9]", re.IGNORECASE)


def _truthy(v: Any) -> bool:
    if v is True:
        return True
    if v is False or v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def row_type_defining(row: dict[str, Any]) -> bool:
    for k in _TYPE_DEFINING_KEYS:
        if _truthy(row.get(k)):
            return True
    return False


def _norm_pset(val: Any) -> str:
    s = (str(val) if val is not None else "").strip().upper()
    return _BIP_PSET_NORM.sub("", s)


def _row_for_project_name(row: dict[str, Any], project_name: str) -> bool:
    name = (project_name or "").strip()
    if not name:
        return True
    projs = row.get("Projekt")
    if not projs:
        return True
    needle = name.lower()
    for p in projs:
        if isinstance(p, dict) and str(p.get("value", "")).strip().lower() == needle:
            return True
    return False


def baserow_credentials_from_env() -> Tuple[str, str, int] | None:
    base = (
        os.environ.get("BASEROW_API_BASE")
        or os.environ.get("CDE_BASEROW_API_BASE")
        or ""
    ).strip().rstrip("/")
    token = (
        os.environ.get("BASEROW_API_TOKEN")
        or os.environ.get("CDE_BASEROW_API_KEY")
        or ""
    ).strip()
    if not base or not token:
        return None
    raw_id = (
        os.environ.get("BASEROW_BIM_PROPERTIES_TABLE_ID")
        or os.environ.get("CDE_BASEROW_IDS_PROPERTIES_TABLE_ID")
        or "1234"
    ).strip()
    try:
        table_id = int(raw_id)
    except ValueError:
        table_id = 1234
    if not base.endswith("/api"):
        if base.endswith("/api/"):
            base = base.rstrip("/")
        elif "/api" not in base.split("://", 1)[-1]:
            base = f"{base}/api"
    return base, token, table_id


def baserow_project_name_from_env() -> str | None:
    raw = (os.environ.get("BASEROW_PROJECT_NAME") or "").strip()
    return raw or None


def _fetch_page(url: str, token: str, timeout: float = 120.0) -> dict[str, Any]:
    headers = {**_BASEROW_REQUEST_HEADERS_BASE, "Authorization": f"Token {token}"}
    req = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_bip_type_defining_property_names() -> List[str]:
    """Return sorted unique BIP property names flagged type-defining in Baserow.

    Raises ``ValueError`` when Baserow env is not configured (callers should check
    :func:`baserow_credentials_from_env` first). Configure ``BASEROW_*`` or ``CDE_BASEROW_*``.
    Raises ``RuntimeError`` when the filtered list is empty or on HTTP / URL errors.
    """
    cred = baserow_credentials_from_env()
    if cred is None:
        raise ValueError(
            "Baserow credentials not set (BASEROW_API_BASE + BASEROW_API_TOKEN, "
            "or CDE_BASEROW_API_BASE + CDE_BASEROW_API_KEY)"
        )
    base, token, table_id = cred
    project = baserow_project_name_from_env()

    params = urllib.parse.urlencode({"user_field_names": "true", "size": "200"})
    url: str | None = f"{base}/database/rows/table/{table_id}/?{params}"
    rows: List[dict[str, Any]] = []
    try:
        while url:
            data = _fetch_page(url, token)
            rows.extend(data.get("results") or [])
            nxt = data.get("next")
            url = str(nxt) if nxt else None
    except urllib.error.HTTPError as e:
        logger.warning("Baserow fetch failed HTTP %s: %s", e.code, e.reason)
        raise RuntimeError(f"Baserow HTTP {e.code}: cannot load BIP type-defining properties") from e
    except urllib.error.URLError as e:
        logger.warning("Baserow fetch failed: %s", e.reason)
        raise RuntimeError(f"Baserow unreachable: {e.reason!r}") from e

    names: List[str] = []
    for row in rows:
        if project and not _row_for_project_name(row, project):
            continue
        if _norm_pset(row.get("Property Set")) != "BIP":
            continue
        if not row_type_defining(row):
            continue
        prop = row.get("Property")
        if prop is None:
            continue
        s = str(prop).strip()
        if s:
            names.append(s)

    out = sorted(set(names))
    if not out:
        raise RuntimeError(
            "Baserow returned no BIP rows with type-defining=true "
            f"(table={table_id}, project filter={project!r}). "
            "Check Property Set, Typ definierande / Type Defining, and Projekt."
        )
    return out
