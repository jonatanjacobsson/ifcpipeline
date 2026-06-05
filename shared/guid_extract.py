"""Streaming GUID extractors for ifcpipeline's GUID audit trail.

Every function here yields `(ifc_guid, entity_type, role)` tuples and is
constant-memory: we never materialize a full list of guids in RAM.
The worker that calls these feeds the stream into psycopg2's
`execute_values` in 5k-row batches, and every INSERT uses
`ON CONFLICT DO NOTHING` against the `(object_version_id, ifc_guid, role)`
unique index, which makes re-runs idempotent.

Files we extract from:
    - `.ifc` (STEP text)          → scan for IfcRoot-derived entities via regex.
    - `.ifczip`                   → open the inner .ifc with zipfile and scan.
    - `.json` (ifc2json output)   → use ijson for a streaming parse.
    - `.csv` / `.xlsx` (ifccsv)   → pandas read_csv(..., chunksize=...) over a
                                    "GlobalId" column if present.
    - diff report JSON            → classify into diff_added/diff_deleted/diff_changed.

`role` is the caller's responsibility — these extractors do not know whether
a file is a "root" upload or a "patched" derivative; the `guid-index-worker`
passes the right role per object.

Every extractor short-circuits on decode errors and logs a warning rather
than raising: a half-broken IFC file shouldn't fail the whole indexing job.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from typing import Dict, Generator, Iterable, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

GuidRow = Tuple[str, Optional[str], str]

# -----------------------------------------------------------------------------
# IFC STEP text
# -----------------------------------------------------------------------------

# IFC GUIDs are a 22-character base64-ish encoding.
# They're always the first parameter of an IfcRoot-derived entity, wrapped in
# single quotes. We match `#123=IfcFoo('22charguid',...` with a tolerant regex
# so we don't need a full parser — we just need guid+entity_type.
# Note: some tools emit multi-line entities. We work on one logical entity at
# a time by splitting on `;` but STEP happens to put every statement on its
# own line in practice, so a line-oriented scan is correct and fast.
_STEP_ENTITY_RE = re.compile(
    rb"""
    ^\#\d+\s*=\s*                     # line starts with `#nnn=`
    (?P<entity>Ifc[A-Za-z0-9]+)       # entity name
    \s*\(\s*                          # opening paren
    '(?P<guid>[0-9A-Za-z_$]{22})'     # 22-char GlobalId
    """,
    re.VERBOSE,
)


def extract_from_ifc_path(path: str, *, encoding: str = "utf-8") -> Iterator[GuidRow]:
    """Yield `(guid, entity_type, role="")` for every IFC element with a
    GlobalId. `role` is left blank — the caller stamps it.

    Works on both plain `.ifc` and `.ifczip` files.
    """
    if path.lower().endswith(".ifczip"):
        yield from _extract_from_ifczip(path, encoding=encoding)
        return
    with open(path, "rb") as fh:
        yield from _extract_from_step_stream(fh)


def _extract_from_ifczip(path: str, *, encoding: str = "utf-8") -> Iterator[GuidRow]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            inner = next(
                (n for n in zf.namelist() if n.lower().endswith(".ifc")),
                None,
            )
            if not inner:
                logger.warning("extract_from_ifc_path: no .ifc inside %s", path)
                return
            with zf.open(inner, "r") as fh:
                yield from _extract_from_step_stream(fh)
    except zipfile.BadZipFile:
        logger.warning("extract_from_ifc_path: %s is not a valid zip", path)


def _extract_from_step_stream(fh) -> Iterator[GuidRow]:
    """Scan a STEP text stream line-by-line for IfcRoot-derived entities.

    This is deliberately dumb — we don't try to honor backslash-continuation
    or embedded single quotes in other parameters, because the guid is always
    the first parameter (directly after `(` ) and IfcOpenShell / AEC tools
    don't fold those lines in practice. The worst case on pathological input
    is false negatives, not false positives.
    """
    for raw in fh:
        m = _STEP_ENTITY_RE.match(raw)
        if m is None:
            continue
        entity = m.group("entity").decode("ascii", errors="ignore")
        guid = m.group("guid").decode("ascii", errors="ignore")
        yield (guid, entity, "")


# -----------------------------------------------------------------------------
# ifc2json output
# -----------------------------------------------------------------------------

def extract_from_ifc_json_path(path: str) -> Iterator[GuidRow]:
    """Stream a (potentially huge) ifc2json document and yield GlobalIds.

    Uses ijson so we never load the whole document. We look for two common
    shapes:
      - `{"elements": [{"GlobalId": "...", "type": "IfcWall", ...}, ...]}`
      - flat top-level arrays of entities.
    """
    try:
        import ijson  # type: ignore
    except ImportError:
        logger.warning("extract_from_ifc_json_path: ijson not installed, skipping %s", path)
        return
    try:
        with open(path, "rb") as fh:
            # `item` path — walks the top-level array or any nested array of
            # dicts. We try both `elements.item` and `item` and stop at the
            # first that yields rows.
            found = False
            for prefix in ("elements.item", "item"):
                try:
                    fh.seek(0)
                    for obj in ijson.items(fh, prefix):
                        guid = _json_field(obj, "GlobalId", "globalId", "global_id")
                        if not guid:
                            continue
                        etype = _json_field(obj, "type", "IfcType", "@type")
                        yield (guid, etype, "")
                        found = True
                except (ValueError, KeyError):
                    continue
                if found:
                    return
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("extract_from_ifc_json_path(%s): %s", path, e)


def _json_field(obj, *names: str) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    for n in names:
        v = obj.get(n)
        if isinstance(v, str) and v:
            return v
    return None


# -----------------------------------------------------------------------------
# ifccsv output
# -----------------------------------------------------------------------------

def extract_from_csv_path(path: str) -> Iterator[GuidRow]:
    """Stream `GlobalId` values from a CSV / XLSX exported by ifccsv.

    Pandas gives us a `chunksize` iterator which caps memory regardless of
    file size. We don't know the IFC entity type from a CSV row unless the
    user exported a `type` column, so we emit the type when present and
    `None` otherwise.
    """
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        logger.warning("extract_from_csv_path: pandas not installed, skipping %s", path)
        return
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            df = pd.read_excel(path)
            yield from _iter_csv_chunk(df)
        else:
            for chunk in pd.read_csv(path, chunksize=5000, dtype=str, keep_default_na=False):
                yield from _iter_csv_chunk(chunk)
    except Exception as e:
        logger.warning("extract_from_csv_path(%s): %s", path, e)


def _iter_csv_chunk(chunk) -> Iterator[GuidRow]:
    cols = {c.lower(): c for c in chunk.columns}
    guid_col = cols.get("globalid") or cols.get("global_id") or cols.get("guid")
    if not guid_col:
        return
    type_col = cols.get("type") or cols.get("ifctype") or cols.get("entity")
    for _, row in chunk.iterrows():
        guid = row.get(guid_col)
        if not isinstance(guid, str) or not guid:
            continue
        etype = row.get(type_col) if type_col else None
        if not isinstance(etype, str) or not etype:
            etype = None
        yield (guid, etype, "")


# -----------------------------------------------------------------------------
# ifcdiff report
# -----------------------------------------------------------------------------

def extract_from_diff_report(path: str) -> Iterator[GuidRow]:
    """Yield classified rows from an ifcdiff JSON report.

    The role column is populated here so callers don't have to re-classify.
    IfcDiff's JSON shape is a flat object with lists under `added`,
    `deleted`, `changed` / `modified` (the worker emits `changed`; older
    runs used `modified`). Each entry is either a GlobalId string or a dict
    with a `global_id` / `GlobalId` field.
    """
    try:
        with open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("extract_from_diff_report(%s): %s", path, e)
        return
    if not isinstance(data, dict):
        return
    mapping = {
        "added": "diff_added",
        "deleted": "diff_deleted",
        "changed": "diff_changed",
        "modified": "diff_changed",
    }
    for key, role in mapping.items():
        bucket = data.get(key)
        if not bucket:
            continue
        for entry in _iter_diff_bucket(bucket):
            guid, etype = entry
            if not guid:
                continue
            yield (guid, etype, role)


def _iter_diff_bucket(bucket) -> Iterable[Tuple[Optional[str], Optional[str]]]:
    if isinstance(bucket, list):
        for item in bucket:
            yield from _extract_diff_item(item)
    elif isinstance(bucket, dict):
        for k, v in bucket.items():
            if isinstance(k, str) and len(k) == 22:
                yield (k, _json_field(v, "type", "IfcType") if isinstance(v, dict) else None)
            else:
                yield from _iter_diff_bucket(v)


def _extract_diff_item(item) -> Iterable[Tuple[Optional[str], Optional[str]]]:
    if isinstance(item, str):
        yield (item, None)
    elif isinstance(item, dict):
        guid = _json_field(item, "GlobalId", "global_id", "guid")
        etype = _json_field(item, "type", "IfcType")
        yield (guid, etype)


# -----------------------------------------------------------------------------
# Batching helper
# -----------------------------------------------------------------------------

def batched(iterable: Iterator[GuidRow], size: int = 5000) -> Iterator[list]:
    """Yield lists of up to `size` rows from a GUID-row iterator."""
    batch: list = []
    for row in iterable:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
