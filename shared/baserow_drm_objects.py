"""Baserow DRM (table 1282) — Object-type rows for ifc-gherkin-worker DRM001.

Env (same base/token pattern as :mod:`shared.baserow_bip_type_defining`):

- ``BASEROW_API_BASE`` or ``CDE_BASEROW_API_BASE``
- ``BASEROW_API_TOKEN`` or ``CDE_BASEROW_API_KEY``
- ``BASEROW_DRM_TABLE_ID`` or ``CDE_BASEROW_DRM_TABLE_ID`` — default ``1282``

Pipeline / worker (set by ifc-gherkin-worker ``tasks.py`` when present):

- ``GHERKIN_DISCIPLINE_CODE`` — optional explicit discipline (e.g. from CDE slot)
- ``GHERKIN_IFC_SOURCE_BASENAME`` — original pipeline IFC basename (before local copy to input.ifc)
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# Cloudflare in front of baserow.byggstyrning.se returns 403 (error 1010) when no
# User-Agent is sent or when the default Python-urllib UA is used.
_BASEROW_REQUEST_HEADERS_BASE: Dict[str, str] = {
    "User-Agent": "ifcpipeline-baserow-client/1.0",
    "Accept": "application/json",
}

_DEFAULT_DRM_TABLE_ID = 1282
_IFC_CLASS_TOKEN = re.compile(r"\bIfc[A-Za-z0-9]+\b")
_DD_PREFIX = re.compile(r"^dd[0-9a-fA-F]{8}-", re.IGNORECASE)


def baserow_drm_credentials_from_env() -> Tuple[str, str, int] | None:
    """Return (api_base, token, drm_table_id) or None if not configured."""
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
        os.environ.get("BASEROW_DRM_TABLE_ID")
        or os.environ.get("CDE_BASEROW_DRM_TABLE_ID")
        or str(_DEFAULT_DRM_TABLE_ID)
    ).strip()
    try:
        table_id = int(raw_id)
    except ValueError:
        table_id = _DEFAULT_DRM_TABLE_ID
    if not base.endswith("/api"):
        if base.endswith("/api/"):
            base = base.rstrip("/")
        elif "/api" not in base.split("://", 1)[-1]:
            base = f"{base}/api"
    return base, token, table_id


def _fetch_page(url: str, token: str, timeout: float = 120.0) -> dict[str, Any]:
    headers = {**_BASEROW_REQUEST_HEADERS_BASE, "Authorization": f"Token {token}"}
    req = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ifc_class_tokens(cell: Any) -> List[str]:
    """Extract unique Ifc* class names from a DRM cell (order preserved)."""
    if cell is None:
        return []
    text = str(cell).strip()
    if not text:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _IFC_CLASS_TOKEN.finditer(text):
        t = m.group(0)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _who_value(row: dict[str, Any]) -> str:
    raw = row.get("Responsible (WHO)")
    if isinstance(raw, dict):
        return str(raw.get("value") or "").strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _requirement_type_value(row: dict[str, Any]) -> str:
    raw = row.get("Requirement Type")
    if isinstance(raw, dict):
        return str(raw.get("value") or "").strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _what_text(row: dict[str, Any]) -> str:
    raw = row.get("Information Requirement (WHAT)")
    if raw is None:
        return ""
    # Rich text may be string or object depending on Baserow version
    if isinstance(raw, str):
        return raw.strip()
    return str(raw).strip()


def fetch_all_drm_rows() -> List[dict[str, Any]]:
    """Fetch all rows from the DRM table (paginated). Raises on HTTP errors."""
    cred = baserow_drm_credentials_from_env()
    if cred is None:
        raise ValueError(
            "Baserow credentials not set (BASEROW_API_BASE + BASEROW_API_TOKEN, "
            "or CDE_BASEROW_API_BASE + CDE_BASEROW_API_KEY)"
        )
    base, token, table_id = cred
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
        logger.warning("Baserow DRM fetch HTTP %s: %s", e.code, e.reason)
        raise RuntimeError(f"Baserow HTTP {e.code}: cannot load DRM table {table_id}") from e
    except urllib.error.URLError as e:
        logger.warning("Baserow DRM fetch failed: %s", e.reason)
        raise RuntimeError(f"Baserow unreachable: {e.reason!r}") from e
    return rows


def fetch_drm_object_rows() -> List[dict[str, Any]]:
    """Return DRM rows where Requirement Type is Object."""
    out: List[dict[str, Any]] = []
    for row in fetch_all_drm_rows():
        if _requirement_type_value(row) != "Object":
            continue
        out.append(row)
    return out


def collect_who_candidates(rows: Sequence[dict[str, Any]]) -> List[str]:
    """Distinct Responsible (WHO) values for prefix matching, longest-first sort."""
    seen: Set[str] = set()
    ordered: List[str] = []
    for row in rows:
        w = _who_value(row)
        if w and w not in seen:
            seen.add(w)
            ordered.append(w)
    ordered.sort(key=len, reverse=True)
    return ordered


def discipline_from_pipeline_basename(
    basename: str, who_candidates: Iterable[str]
) -> Optional[str]:
    """Infer discipline code from CDE-style pipeline basename using WHO options."""
    stem = Path(basename).stem
    rest = _DD_PREFIX.sub("", stem)
    if not rest:
        return None
    cand_list = list(who_candidates)
    cand_list.sort(key=len, reverse=True)
    cand_set = frozenset(cand_list)
    for w in cand_list:
        if rest.startswith(w):
            tail = rest[len(w) :]
            if not tail or tail[0] in "_- \t":
                return w
    for sep in ("_", "-"):
        if sep in rest:
            tok = rest.split(sep, 1)[0].strip()
            if tok in cand_set:
                return tok
    return None


def resolve_discipline_code(
    *,
    explicit: Optional[str],
    pipeline_basename: str,
    who_candidates: Sequence[str],
) -> Optional[str]:
    """Prefer explicit code (e.g. env / API); else parse basename with WHO list."""
    e = (explicit or "").strip()
    if e:
        return e
    return discipline_from_pipeline_basename(pipeline_basename, who_candidates)


def gherkin_discipline_env() -> Optional[str]:
    return (os.environ.get("GHERKIN_DISCIPLINE_CODE") or "").strip() or None


def gherkin_ifc_source_basename() -> Optional[str]:
    return (os.environ.get("GHERKIN_IFC_SOURCE_BASENAME") or "").strip() or None
