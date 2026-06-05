"""
PropagatePropertyFromClashPairs Recipe

Reads a property from elements in a *source* IFC model (typically the
``IfcSpace`` elements of an "spaces" model A) and writes it onto the
*target* IFC model B's elements that the ifcpipeline ``ifcclash`` worker
identified as colliding/intersecting/clearing each space.

The recipe is designed so n8n only orchestrates: the entire join
(``a_global_id`` → source value → write on ``b_global_id``) happens here,
in the worker, never in n8n. The clash report itself is read straight from
S3 (or the local filesystem) so n8n never has to forward a large JSON
through its execution context.

Recipe Name: PropagatePropertyFromClashPairs
Author: jonatan.jacobsson + cursor agent (2026-05)

Positional arguments to ``Patcher.__init__`` (n8n IfcPatch node order):
    1. source_file:   S3 key, ``s3://bucket/key`` URI, or legacy
                      ``/uploads/<name>`` path of model A (the spaces model
                      with the source property).
    2. pairs_source:  Either an inline JSON document (``[{"a": guid_a,
                      "b": guid_b}, ...]`` or the raw ifcclash report) OR
                      an S3 key / ``s3://bucket/key`` URI / local path
                      pointing at an ifcclash report JSON file. Inline
                      JSON is detected by a leading ``[`` or ``{``.
    3. property_from: **DEPRECATED** single-property API. Pass a
                      ``mappings`` array (position 9) instead — it
                      supersedes this slot for all new callers and is
                      strictly more capable (multi-target, per-mapping
                      data_type/default_value/aggregate). Kept so legacy
                      callers continue to work: when ``mappings`` is empty
                      the recipe builds a one-element mappings list from
                      ``property_from`` + ``property_to`` and logs a
                      deprecation warning. Empty/whitespace ⇒ caller is
                      expected to use ``mappings``. Examples (legacy):
                      ``Name``, ``LongName``, ``Pset_SpaceCommon.Reference``,
                      ``material.Name``.
    4. property_to:   **DEPRECATED** companion to ``property_from``. Same
                      semantics: ignored whenever ``mappings`` is non-empty,
                      consumed only as the fallback legacy single mapping
                      (with a deprecation warning). New callers should
                      use the ``mappings`` array's ``to`` field instead.
                      Format: ``Pset.PropertyName`` for property sets, or
                      a bare attribute like ``Name``.
    5. data_type:     Optional IFC data type (``IfcLabel``, ``IfcText``,
                      ``IfcInteger``, ``IfcReal``, ``IfcBoolean``,
                      ``IfcIdentifier``). Empty → infer from the source
                      value; defaults to ``IfcLabel`` if all candidates
                      look like text.
    6. default_value: Optional fallback used when the source space has
                      no value for ``property_from``. Empty → element is
                      skipped silently.
    7. aggregate:     ``first`` (default, keep the first value seen for a
                      given target GUID), ``join`` (concat distinct
                      values with ", "), or ``skip_conflicts`` (drop the
                      target if A→B mappings disagree).
    8. pairs_source_side: Which side of each ``(a_global_id, b_global_id)``
                      tuple is the *source* (model A) GUID. Accepted
                      values: ``auto`` (default), ``a``, or ``b``.
                      ``auto`` classifies **every pair independently**:
                      for each ``(a, b)`` the recipe tries to resolve
                      ``a`` in the source IFC first; if that succeeds
                      the orientation is ``a → b``, otherwise it tries
                      ``b`` (orientation ``b → a``); pairs where
                      neither side resolves are counted in
                      ``pairs_unresolved`` and skipped. This handles
                      ifcclash reports that mix orientations within a
                      single output (which is the common case).
                      ``a`` / ``b`` force the legacy single-direction
                      behaviour for callers that have a pre-normalised
                      report and want to skip the per-pair lookup.
    9. mappings:      Optional JSON array of ``{"from": ..., "to": ...,
                      "data_type": ..., "default_value": ...,
                      "aggregate": ...}`` objects, OR a JSON object with a
                      single ``mappings`` key wrapping that array. When
                      present this takes precedence over ``property_from``
                      / ``property_to`` so a single recipe call can write
                      multiple properties in **one** pass over the IFC
                      (one ``ifcopenshell.open`` of source A, one
                      iteration of the target file). Per-mapping
                      ``data_type``/``default_value``/``aggregate`` fall
                      back to the recipe-level defaults at positions 5,
                      6, 7 when omitted. ``from`` and ``to`` are
                      required for each mapping; ``to`` follows the
                      same ``Pset.Property`` / bare-attribute syntax as
                      ``property_to``.
   10. sort_by:       Optional property/attribute path on the **source**
                      (model A) entity used to pick a single canonical
                      candidate per target when one element clashes
                      multiple spaces. Same syntax as ``property_from``
                      (bare attribute, ``Pset.Property``, ``material.X``,
                      ``type.X``). Empty / whitespace → no dedup; the
                      legacy ``aggregate`` strategy runs unchanged. Each
                      candidate value is coerced to a sort key with a
                      **numeric-if-parseable, lexicographic-otherwise**
                      rule per value — so ``'010-217'``-style storey
                      prefixes sort lexicographically (``'010-217' <
                      '050-199'``) while pure numbers sort numerically.
                      Candidates without a value are tied to the end of
                      the order regardless of direction; a candidate
                      with any value beats one without. Groups where
                      *every* candidate lacks a sort value fall through
                      to the legacy ``aggregate`` strategy.
   11. sort_order:    ``asc`` (default) or ``desc``. Aliases
                      ``ascending``/``descending`` tolerated. Ignored
                      when ``sort_by`` is empty.

Empty positional arguments are treated as "use default" so the n8n
``argumentValues`` collection can leave optional slots blank. When
``mappings`` is supplied (the recommended path), ``property_from`` /
``property_to`` are ignored entirely. The legacy single-property API
is preserved purely for backward compatibility and emits a deprecation
warning on every use; please migrate by sending a one-element
``mappings`` array instead (``[{"from": "...", "to": "..."}]``).

Side effect: writes to ``self.file`` in place. ``get_output()`` returns
the patched ``ifcopenshell.file`` for ``ifcpatch.write``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from logging import Logger
from typing import Dict, Iterable, List, Optional, Tuple, Union

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.selector

# `shared` is on PYTHONPATH inside the ifcpatch-worker container
# (see ifcpatch-worker/Dockerfile). The recipe degrades gracefully if S3 is
# disabled — `is_enabled()` is False and we treat every "key" as a local path.
try:  # pragma: no cover - exercised via container
    from shared import object_storage as s3
except Exception:  # pragma: no cover - degraded local-only mode
    s3 = None  # type: ignore[assignment]


_EI = ifcopenshell.entity_instance


def _safe_is_a(inst, class_name: str) -> bool:
    if inst is None or not isinstance(inst, _EI):
        return False
    try:
        return inst.is_a(class_name)
    except (AttributeError, TypeError, RuntimeError, SystemError):
        return False


SUPPORTED_DATA_TYPES: Dict[str, type] = {
    "IfcText": str,
    "IfcLabel": str,
    "IfcIdentifier": str,
    "IfcInteger": int,
    "IfcReal": float,
    "IfcBoolean": bool,
}

# Friendly aliases for the ``data_type`` argument so callers don't have to
# remember IFC type names. Anything not listed here must be a literal
# ``Ifc*`` type. Case-insensitive lookup is performed in
# ``_resolve_data_type``.
DATA_TYPE_ALIASES: Dict[str, str] = {
    "string": "IfcLabel",
    "str": "IfcLabel",
    "text": "IfcText",
    "label": "IfcLabel",
    "identifier": "IfcIdentifier",
    "id": "IfcIdentifier",
    "int": "IfcInteger",
    "integer": "IfcInteger",
    "float": "IfcReal",
    "real": "IfcReal",
    "number": "IfcReal",
    "bool": "IfcBoolean",
    "boolean": "IfcBoolean",
}


def _resolve_data_type(raw: Optional[str]) -> Optional[str]:
    """Normalise a user-supplied data_type to a SUPPORTED_DATA_TYPES key.

    - Empty / None → None (caller infers from the value).
    - Recognised alias (case-insensitive) → mapped IFC type.
    - Already an IFC type → returned unchanged.
    - Anything else → ValueError with the canonical supported set.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text in SUPPORTED_DATA_TYPES:
        return text
    alias = DATA_TYPE_ALIASES.get(text.lower())
    if alias is not None:
        return alias
    raise ValueError(
        f"Unsupported data_type {raw!r}. Supported: "
        f"{sorted(SUPPORTED_DATA_TYPES)} or aliases "
        f"{sorted(set(DATA_TYPE_ALIASES))}"
    )


class Patcher:
    """Propagate a property from clash partner A onto clash partner B.

    The expensive parts (loading model A, joining ``a_global_id`` →
    source value → ``b_global_id`` → target element, writing the property)
    all happen here so the n8n side stays tiny. See module docstring for
    the positional argument contract.
    """

    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Union[Logger, None] = None,
        source_file: str = "",
        pairs_source: str = "",
        property_from: str = "",
        property_to: str = "",
        data_type: str = "",
        default_value: str = "",
        aggregate: str = "first",
        pairs_source_side: str = "auto",
        mappings: str = "",
        sort_by: str = "",
        sort_order: str = "asc",
    ):
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)

        self.source_file_arg = (source_file or "").strip()
        self.pairs_source_arg = (pairs_source or "").strip()
        self.property_from = (property_from or "").strip()
        self.property_to = (property_to or "").strip()
        # data_type accepts the IFC type names AND friendly aliases like
        # "string" / "int" / "bool" — see DATA_TYPE_ALIASES. The resolver
        # raises ValueError early so a misspelled type fails before the
        # IFC is even opened.
        self.data_type_override: Optional[str] = _resolve_data_type(data_type)
        self.default_value: Optional[str] = (
            (default_value or "").strip() or None
        )
        self.aggregate_strategy = ((aggregate or "first").strip().lower()
                                   or "first")
        self.pairs_source_side = ((pairs_source_side or "auto").strip().lower()
                                  or "auto")
        self.mappings_arg = self._normalize_mapping_arg(mappings)

        if self.aggregate_strategy not in {"first", "join", "skip_conflicts"}:
            self.logger.warning(
                "Unknown aggregate strategy %r; falling back to 'first'",
                self.aggregate_strategy,
            )
            self.aggregate_strategy = "first"

        if self.pairs_source_side not in {"a", "b", "auto"}:
            self.logger.warning(
                "Unknown pairs_source_side %r; falling back to 'auto'",
                self.pairs_source_side,
            )
            self.pairs_source_side = "auto"

        # sort_by / sort_order — optional dedup. Empty sort_by ⇒ no-op
        # (the recipe stays byte-identical for callers that don't set it).
        self.sort_by: str = (sort_by or "").strip()
        order_raw = (sort_order or "asc").strip().lower() or "asc"
        if order_raw in {"asc", "ascending"}:
            self.sort_order = "asc"
        elif order_raw in {"desc", "descending"}:
            self.sort_order = "desc"
        else:
            self.logger.warning(
                "Unknown sort_order %r; falling back to 'asc'", order_raw,
            )
            self.sort_order = "asc"

        # Resolve the working mappings list: explicit ``mappings`` wins;
        # otherwise fall back to a single legacy mapping built from
        # property_from + property_to.
        self.mappings: List[Dict[str, Optional[str]]] = self._build_mappings()

        if not self.source_file_arg:
            raise ValueError("source_file argument is required")
        if not self.pairs_source_arg:
            raise ValueError("pairs_source argument is required")
        if not self.mappings:
            # Recommend the new array path first; the legacy single-property
            # API is preserved but no longer the documented surface.
            raise ValueError(
                "PropagatePropertyFromClashPairs requires a non-empty "
                "`mappings` array (preferred), e.g. "
                "[{\"from\": \"Name\", \"to\": \"Pset_Foo.Bar\"}]. The legacy "
                "single-property API (property_from + property_to) is also "
                "accepted but deprecated; neither path supplied a usable "
                "mapping."
            )

        # Per-mapping derived fields (pset, property name, is_attribute).
        # Cached here so the per-element write loop is a tight inner loop
        # without re-parsing the same target path 1×N times.
        self._mapping_targets: List[Dict[str, object]] = []
        for m in self.mappings:
            pset, prop, is_attr = self._split_property_path(m["to"])
            self._mapping_targets.append({
                "pset": pset,
                "property": prop,
                "is_attribute": is_attr,
            })

        # Stats surfaced to the worker job result. ``mappings_stats`` is
        # a flat array (computed in patch()) suitable for direct
        # consumption by callers that want one row per mapping.
        # ``per_mapping`` keeps the original keyed dict for backward
        # compatibility.
        self.stats: Dict[str, object] = {
            "pairs_total": 0,
            "pairs_oriented_a_to_b": 0,
            "pairs_oriented_b_to_a": 0,
            "pairs_unresolved": 0,
            "pairs_unique_targets": 0,
            "pairs_source_side_resolved": self.pairs_source_side,
            "sort_by_resolved": self.sort_by,
            "sort_order_resolved": self.sort_order if self.sort_by else "",
            "pairs_dropped_by_sort": 0,
            "targets_with_multiple_candidates": 0,
            "targets_dedup_winners": 0,
            "targets_no_sort_value_fellthrough": 0,
            "mappings_count": len(self.mappings),
            "mappings_stats": [],
            "per_mapping": {},
        }
        for m in self.mappings:
            self.stats["per_mapping"][m["to"]] = {
                "from": m["from"],
                "data_type_override": m.get("data_type"),
                "default_value": m.get("default_value"),
                "aggregate": m.get("aggregate", self.aggregate_strategy),
                "source_lookups": 0,
                "source_hits": 0,
                "source_misses": 0,
                "targets_resolved": 0,
                "targets_modified": 0,
                "targets_skipped": 0,
                "targets_skipped_no_source_value": 0,
                "conflicts_dropped": 0,
                "data_type_used": None,
            }

        # Lazily allocated. Cleaned up in patch() so a SIGSEGV during
        # patch() doesn't leak a tempfile.
        self._tempfiles: List[str] = []

        # Resolved once at the top of patch() to avoid 1×N ifcopenshell
        # C-extension calls from inside the per-element write loop. Repeated
        # ``self.file.by_type(...)`` invocations across thousands of elements
        # have been observed to crash with
        # ``SystemError: entity_instance.__init__ returned NULL`` on large
        # files; caching the owner history removes that hot path entirely.
        self._owner_history_cache = None

    # ------------------------------------------------------------------
    # Argument parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_mapping_arg(raw) -> Optional[List[Dict[str, str]]]:
        """Accept the ``mappings`` argument as a Python list/dict already,
        a JSON string, or empty. Returns a list of dicts or None.

        n8n's IfcPatch node serialises everything to strings before posting
        to the worker, so a JSON-encoded array is the common path. Inline
        Python objects are still accepted for unit testing / direct API
        callers.
        """
        if raw is None:
            return None
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            inner = raw.get("mappings")
            if isinstance(inner, list):
                return inner
            return [raw]
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"mappings argument is not valid JSON: {e}"
                ) from e
            return Patcher._normalize_mapping_arg(parsed)
        raise ValueError(
            f"mappings must be a list, dict or JSON string; got "
            f"{type(raw).__name__}"
        )

    def _build_mappings(self) -> List[Dict[str, Optional[str]]]:
        """Return the canonical list of mappings to apply.

        Precedence:
          1. Non-empty ``mappings`` argument (new path — recommended).
          2. Legacy ``property_from`` + ``property_to`` (single mapping,
             **deprecated**; emits a warning on every use and is preserved
             solely for backward compatibility).

        Each returned dict has the keys ``from``, ``to``, ``data_type``
        (or None to fall back to the recipe-level override / inference),
        ``default_value`` (or None to fall back to the recipe-level
        default), and ``aggregate`` (or recipe-level default).
        """
        out: List[Dict[str, Optional[str]]] = []
        if self.mappings_arg:
            # Surface a one-shot info note when callers send BOTH the
            # new array AND the legacy single fields — those legacy
            # fields are dropped on the floor and the user might think
            # they still apply.
            if self.property_from or self.property_to:
                self.logger.info(
                    "PropagatePropertyFromClashPairs: `mappings` is set "
                    "(%d entr%s), so the legacy `property_from`/`property_to` "
                    "values (%r → %r) are ignored. Drop them from the call "
                    "to silence this notice.",
                    len(self.mappings_arg),
                    "y" if len(self.mappings_arg) == 1 else "ies",
                    self.property_from, self.property_to,
                )
            for i, raw in enumerate(self.mappings_arg):
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"mappings[{i}] must be an object, got "
                        f"{type(raw).__name__}"
                    )
                src = (raw.get("from") or raw.get("property_from") or "").strip()
                dst = (raw.get("to") or raw.get("property_to") or "").strip()
                if not src or not dst:
                    raise ValueError(
                        f"mappings[{i}] requires both 'from' and 'to'"
                    )
                try:
                    dt = _resolve_data_type(raw.get("data_type"))
                except ValueError as e:
                    raise ValueError(f"mappings[{i}].data_type: {e}") from e
                dv_raw = raw.get("default_value")
                dv = None if dv_raw in (None, "") else str(dv_raw)
                agg = (raw.get("aggregate") or "").strip().lower() or self.aggregate_strategy
                if agg not in {"first", "join", "skip_conflicts"}:
                    self.logger.warning(
                        "mappings[%d].aggregate %r unknown; using %r",
                        i, raw.get("aggregate"), self.aggregate_strategy,
                    )
                    agg = self.aggregate_strategy
                out.append({
                    "from": src,
                    "to": dst,
                    "data_type": dt,
                    "default_value": dv,
                    "aggregate": agg,
                })
            return out
        # Legacy single-mapping fallback. Kept working for backward
        # compatibility but loudly deprecated — every legacy call should
        # land in the operator's eyes via the worker logs.
        if self.property_from and self.property_to:
            self.logger.warning(
                "PropagatePropertyFromClashPairs: legacy single-property "
                "API in use (property_from=%r, property_to=%r). This shim "
                "still works but is deprecated — please migrate by sending "
                "`mappings=[{\"from\": %r, \"to\": %r}]` (positional arg #9). "
                "Future releases may drop the single-property positional "
                "slots.",
                self.property_from, self.property_to,
                self.property_from, self.property_to,
            )
            out.append({
                "from": self.property_from,
                "to": self.property_to,
                "data_type": self.data_type_override,
                "default_value": self.default_value,
                "aggregate": self.aggregate_strategy,
            })
        return out

    @staticmethod
    def _split_property_path(path: str) -> Tuple[Optional[str], str, bool]:
        """Return (pset_name, property_name, is_attribute).

        Bare names (no dot) are treated as direct entity attributes and
        are written via setattr — useful for ``Name``, ``Description``,
        ``Tag``.
        """
        if "." in path:
            pset, prop = path.split(".", 1)
            pset = pset.strip()
            prop = prop.strip()
            if not pset or not prop:
                raise ValueError(
                    f"property_to {path!r} must be either 'Pset.Property' "
                    f"or a bare attribute name"
                )
            return pset, prop, False
        return None, path, True

    def _looks_like_inline_json(self, blob: str) -> bool:
        if not blob:
            return False
        head = blob.lstrip()[:1]
        return head in ("[", "{")

    def _materialize_pairs_blob(self) -> str:
        """Return the JSON text for ``pairs_source``.

        Inline JSON is returned verbatim; an S3 key / s3:// URI / local
        path is fetched and read into memory.
        """
        if self._looks_like_inline_json(self.pairs_source_arg):
            return self.pairs_source_arg

        # Treat as a path / S3 key
        local_path = self._resolve_to_local_file(
            self.pairs_source_arg, what="pairs_source"
        )
        with open(local_path, "r", encoding="utf-8") as fh:
            return fh.read()

    def _resolve_to_local_file(self, ref: str, *, what: str) -> str:
        """Download ``ref`` from S3 if needed, otherwise return a local path.

        Resolution order:
          1. Existing local path (absolute or relative to ``/uploads`` /
             ``/output``).
          2. ``s3://bucket/key`` URI (always treated as S3).
          3. Bucket-relative key (e.g. ``output/clash/foo.json``).
          4. Anything that ``shared.object_storage.normalize_input_key``
             can collapse onto an existing object.
        """
        if not ref:
            raise FileNotFoundError(f"{what}: empty reference")

        # 1. Direct local existence — covers /uploads/foo.ifc style refs
        for candidate in self._local_candidates(ref):
            if os.path.exists(candidate):
                self.logger.info("%s: using local file %s", what, candidate)
                return candidate

        if s3 is None or not s3.is_enabled():
            raise FileNotFoundError(
                f"{what}: cannot find {ref!r} on disk and object storage is disabled"
            )

        # 2-4. Resolve through the bucket
        candidates: List[str] = []
        if ref.startswith("s3://"):
            # Strip scheme, keep key
            _, _, rest = ref.partition("s3://")
            _, _, key = rest.partition("/")
            candidates.append(key)
        else:
            stripped = ref.lstrip("/")
            candidates.append(stripped)
            # Also try uploads/<basename> and output/clash/<basename> as
            # convenience defaults — this matches how the n8n IfcPatch node
            # passes legacy "/uploads/foo.ifc" style values.
            base = os.path.basename(stripped) or stripped
            candidates.append(s3.normalize_input_key(stripped))
            candidates.append(f"uploads/{base}")
            candidates.append(f"output/clash/{base}")

        seen: List[str] = []
        for key in candidates:
            if not key or key in seen:
                continue
            seen.append(key)
            if s3.object_exists(key):
                tmp = tempfile.NamedTemporaryFile(
                    delete=False,
                    prefix=f"propagate-{what}-",
                    suffix=os.path.splitext(key)[1] or "",
                )
                tmp.close()
                self._tempfiles.append(tmp.name)
                self.logger.info(
                    "%s: downloading s3://%s/%s → %s",
                    what, s3.bucket_name(), key, tmp.name,
                )
                s3.download_to_path(key, tmp.name)
                return tmp.name

        raise FileNotFoundError(
            f"{what}: {ref!r} not found locally or in s3://{s3.bucket_name()} "
            f"(tried keys: {seen})"
        )

    @staticmethod
    def _local_candidates(ref: str) -> List[str]:
        if not ref or ref.startswith("s3://"):
            return []
        candidates = [ref]
        if not ref.startswith("/"):
            candidates.append(os.path.join("/uploads", ref))
            candidates.append(os.path.join("/output", ref))
        # If ref is a bucket key (uploads/foo.ifc) the worker's bind mounts
        # also let us hit the same file through the legacy filesystem layout.
        if ref.startswith("uploads/"):
            candidates.append("/" + ref)
        if ref.startswith("output/"):
            candidates.append("/" + ref)
        return candidates

    # ------------------------------------------------------------------
    # Pair extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _yield_pairs_from_node(node) -> Iterable[Tuple[str, str]]:
        """Walk an ifcclash report (or already-flat list) and yield
        ``(a_global_id, b_global_id)`` tuples. Defensive against schema
        drift between ifcclash versions.
        """
        if isinstance(node, dict):
            a = node.get("a_global_id")
            b = node.get("b_global_id")
            if not (a and b):
                a_obj = node.get("a")
                b_obj = node.get("b")
                if isinstance(a_obj, dict):
                    a = a_obj.get("GlobalId") or a_obj.get("global_id") or a
                if isinstance(b_obj, dict):
                    b = b_obj.get("GlobalId") or b_obj.get("global_id") or b
            if isinstance(a, str) and isinstance(b, str) and len(a) == 22 and len(b) == 22:
                yield a, b
                return
            for v in node.values():
                yield from Patcher._yield_pairs_from_node(v)
        elif isinstance(node, list):
            for v in node:
                yield from Patcher._yield_pairs_from_node(v)

    def _parse_pairs(self, blob: str) -> List[Tuple[str, str]]:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as e:
            raise ValueError(f"pairs_source is not valid JSON: {e}") from e

        pairs: List[Tuple[str, str]] = list(self._yield_pairs_from_node(data))
        if not pairs:
            self.logger.warning(
                "pairs_source parsed successfully but contained zero "
                "(a_global_id, b_global_id) pairs"
            )
        return pairs

    # ------------------------------------------------------------------
    # Source-side value extraction (model A)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_from_field(from_string: str) -> Tuple[Optional[str], str, Optional[str]]:
        """Mirror the SetPropertyBySelector parser so callers get the
        same syntax: ``source.attribute`` plus optional ``=/regex/``.
        """
        regex_pattern: Optional[str] = None
        source_path = from_string
        if "=/" in from_string and from_string.endswith("/"):
            parts = from_string.split("=/", 1)
            source_path = parts[0]
            regex_pattern = parts[1][:-1]
        if "." in source_path:
            source, attribute = source_path.split(".", 1)
        else:
            source, attribute = None, source_path
        return source, attribute, regex_pattern

    def _extract_value(self, element, from_string: str) -> Optional[str]:
        """Return the source value as a string, or None if not found."""
        try:
            source, attribute, regex_pattern = self._parse_from_field(from_string)
            value = None
            if source is None:
                value = getattr(element, attribute, None)
            elif source == "material":
                materials = ifcopenshell.util.element.get_materials(element)
                if materials:
                    material = materials[0] if isinstance(materials, list) else materials
                    value = getattr(material, attribute, None)
            elif source == "type":
                el_type = ifcopenshell.util.element.get_type(element)
                if el_type is not None:
                    value = getattr(el_type, attribute, None)
            else:
                psets = ifcopenshell.util.element.get_psets(element)
                if source in psets and attribute in psets[source]:
                    value = psets[source][attribute]
            if value is None:
                return None
            value_str = str(value)
            if regex_pattern:
                match = re.search(regex_pattern, value_str)
                if not match:
                    return None
                value_str = match.group(0)
            return value_str
        except Exception as e:  # pragma: no cover - belt-and-braces
            self.logger.debug(
                "Failed to extract %r from element %s: %s",
                from_string, getattr(element, "GlobalId", "?"), e,
            )
            return None

    def _build_source_value_maps(
        self,
        source_ifc: ifcopenshell.file,
        a_guids_needed: Iterable[str],
    ) -> List[Dict[str, str]]:
        """Walk the (already open) source IFC and return ``[per_mapping_dict, ...]``.

        Each entry maps ``source_guid → value_str`` for the corresponding
        mapping at the same index in ``self.mappings``. A GUID that has
        no value (and no per-mapping ``default_value``) is omitted from
        that mapping's dict so the writer knows to skip it.

        Single-pass design: we resolve each source element once and call
        ``_extract_value`` for every mapping while we hold the element.
        """
        wanted = set(a_guids_needed)
        per_mapping: List[Dict[str, str]] = [dict() for _ in self.mappings]
        misses = 0
        for guid in wanted:
            try:
                element = source_ifc.by_guid(guid)
            except Exception:
                element = None
            if element is None:
                misses += 1
                continue
            for idx, mapping in enumerate(self.mappings):
                stat = self.stats["per_mapping"][mapping["to"]]
                value = self._extract_value(element, mapping["from"])
                stat["source_lookups"] += 1
                if value is None:
                    fallback = mapping.get("default_value")
                    if fallback is not None:
                        per_mapping[idx][guid] = fallback
                    else:
                        stat["source_misses"] += 1
                    continue
                per_mapping[idx][guid] = value
                stat["source_hits"] += 1
        for idx, mapping in enumerate(self.mappings):
            stat = self.stats["per_mapping"][mapping["to"]]
            self.logger.info(
                "Mapping[%d] %r → %r: %d/%d source GUIDs had a value "
                "(plus %d GUIDs not present in source model)",
                idx, mapping["from"], mapping["to"],
                len(per_mapping[idx]), len(wanted), misses,
            )
        return per_mapping

    # ------------------------------------------------------------------
    # Target-side writers (model B == self.file)
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_data_type(value: str) -> str:
        if value is None:
            return "IfcLabel"
        text = str(value).strip()
        low = text.lower()
        if low in ("true", "false", "yes", "no"):
            return "IfcBoolean"
        try:
            int(text)
            return "IfcInteger"
        except ValueError:
            pass
        try:
            float(text.replace(",", "."))
            return "IfcReal"
        except ValueError:
            pass
        return "IfcLabel"

    def _convert_value(self, raw, data_type: str):
        converter = SUPPORTED_DATA_TYPES[data_type]
        if data_type == "IfcBoolean":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes")
        return converter(raw)

    def _get_or_create_owner_history(self):
        if self._owner_history_cache is not None:
            return self._owner_history_cache
        existing = self.file.by_type("IfcOwnerHistory")
        if existing:
            self._owner_history_cache = existing[0]
            return self._owner_history_cache
        self.logger.warning("No IfcOwnerHistory found in target model; creating minimal one")
        person = self.file.create_entity("IfcPerson", None, None, None)
        org = self.file.create_entity("IfcOrganization", None, "Unknown")
        person_org = self.file.create_entity("IfcPersonAndOrganization", person, org)
        app = self.file.create_entity(
            "IfcApplication", org, "Unknown", "Unknown", "Unknown"
        )
        self._owner_history_cache = self.file.create_entity(
            "IfcOwnerHistory", person_org, app, None, None, None, None, None, 0,
        )
        return self._owner_history_cache

    @staticmethod
    def _relating_property_definitions(related_props):
        if related_props is None:
            return []
        if isinstance(related_props, _EI):
            return [related_props]
        if isinstance(related_props, (list, tuple)):
            return [x for x in related_props if isinstance(x, _EI)]
        return []

    def _find_property_set(self, element, pset_name: str):
        try:
            raw = getattr(element, "IsDefinedBy", None) or []
            rels = list(raw)
        except (TypeError, AttributeError):
            rels = []
        for rel in rels:
            if not _safe_is_a(rel, "IfcRelDefinesByProperties"):
                continue
            try:
                related = getattr(rel, "RelatingPropertyDefinition", None)
            except Exception:
                continue
            for cand in self._relating_property_definitions(related):
                if _safe_is_a(cand, "IfcPropertySet") and cand.Name == pset_name:
                    return cand, rel
        return None, None

    def _find_property_in_set(self, property_set, property_name: str):
        try:
            props = list(getattr(property_set, "HasProperties", None) or [])
        except (TypeError, AttributeError):
            return None
        for prop in props:
            if not isinstance(prop, _EI):
                continue
            if _safe_is_a(prop, "IfcPropertySingleValue") and prop.Name == property_name:
                return prop
        return None

    def _create_property_set(
        self,
        element,
        pset_name: str,
        property_name: str,
        data_type: str,
        value,
    ) -> None:
        owner_history = self._get_or_create_owner_history()
        typed_value = self.file.create_entity(data_type, value)
        prop = self.file.create_entity(
            "IfcPropertySingleValue", property_name, None, typed_value, None,
        )
        pset = self.file.create_entity(
            "IfcPropertySet",
            ifcopenshell.guid.new(),
            owner_history,
            pset_name,
            None,
            [prop],
        )
        self.file.create_entity(
            "IfcRelDefinesByProperties",
            ifcopenshell.guid.new(),
            owner_history,
            None,
            None,
            [element],
            pset,
        )

    def _add_property_to_existing_set(
        self,
        property_set,
        property_name: str,
        data_type: str,
        value,
    ) -> None:
        typed_value = self.file.create_entity(data_type, value)
        prop = self.file.create_entity(
            "IfcPropertySingleValue", property_name, None, typed_value, None,
        )
        if property_set.HasProperties:
            existing = list(property_set.HasProperties)
            existing.append(prop)
            property_set.HasProperties = existing
        else:
            property_set.HasProperties = [prop]

    def _update_property_value(self, property_entity, data_type: str, value):
        property_entity.NominalValue = self.file.create_entity(data_type, value)

    def _write_property_for_target(
        self,
        element,
        value: str,
        data_type: str,
        target_pset: Optional[str],
        target_property: str,
        is_attribute: bool,
    ) -> bool:
        try:
            converted = self._convert_value(value, data_type)
        except Exception as e:
            self.logger.warning(
                "Cannot convert value %r to %s on %s: %s",
                value, data_type, getattr(element, "GlobalId", "?"), e,
            )
            return False

        if is_attribute:
            try:
                setattr(element, target_property, converted)
                return True
            except Exception as e:
                self.logger.warning(
                    "Failed to set attribute %s on %s: %s",
                    target_property, getattr(element, "GlobalId", "?"), e,
                )
                return False

        property_set, _ = self._find_property_set(element, target_pset)
        if property_set is None:
            self._create_property_set(
                element,
                target_pset,
                target_property,
                data_type,
                converted,
            )
            return True
        existing = self._find_property_in_set(property_set, target_property)
        if existing is not None:
            self._update_property_value(existing, data_type, converted)
        else:
            self._add_property_to_existing_set(
                property_set, target_property, data_type, converted,
            )
        return True

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------

    def _aggregate_target_values_per_mapping(
        self,
        pairs: List[Tuple[str, str]],
        per_mapping_source: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """Run the aggregation policy independently for each mapping.

        Returns ``[{b_guid: final_value}, ...]`` aligned with
        ``self.mappings``. ``__CONFLICT__`` sentinels are stripped at
        the end so the writer only sees real values.

        Walking pairs once but updating N parallel dicts costs the same
        as running ``_aggregate_target_values`` once per mapping while
        avoiding N passes over a 10k+ element pair list.
        """
        seen_pairs = 0
        unique_targets: set = set()
        outs: List[Dict[str, str]] = [dict() for _ in self.mappings]
        for a_guid, b_guid in pairs:
            seen_pairs += 1
            unique_targets.add(b_guid)
            for idx, mapping in enumerate(self.mappings):
                value = per_mapping_source[idx].get(a_guid)
                if value is None:
                    continue
                strategy = mapping.get("aggregate", self.aggregate_strategy)
                bucket = outs[idx]
                existing = bucket.get(b_guid)
                if existing is None:
                    bucket[b_guid] = value
                    continue
                if existing == value:
                    continue
                if strategy == "first":
                    continue
                if strategy == "skip_conflicts":
                    bucket[b_guid] = "__CONFLICT__"
                    continue
                if strategy == "join":
                    pieces = [p.strip() for p in existing.split(", ") if p.strip()]
                    if value not in pieces:
                        pieces.append(value)
                    bucket[b_guid] = ", ".join(pieces)

        self.stats["pairs_total"] = seen_pairs
        self.stats["pairs_unique_targets"] = len(unique_targets)

        for idx, mapping in enumerate(self.mappings):
            bucket = outs[idx]
            stat = self.stats["per_mapping"][mapping["to"]]
            if mapping.get("aggregate", self.aggregate_strategy) == "skip_conflicts":
                conflicts = [g for g, v in bucket.items() if v == "__CONFLICT__"]
                for g in conflicts:
                    bucket.pop(g, None)
                stat["conflicts_dropped"] = len(conflicts)
                if conflicts:
                    self.logger.info(
                        "Mapping %r: skip_conflicts dropped %d target GUID(s)",
                        mapping["to"], len(conflicts),
                    )
        return outs

    def _orient_pairs(
        self,
        pairs: List[Tuple[str, str]],
        source_ifc: ifcopenshell.file,
    ) -> List[Tuple[str, str]]:
        """Return pairs normalised so that the *first* element of every
        tuple is the source (model A) GUID.

        Three strategies, selected by ``self.pairs_source_side``:

        * ``a`` — assume every tuple is already source-first; pass through.
        * ``b`` — assume every tuple is reversed; flip globally.
        * ``auto`` (default) — classify each pair independently against
          ``source_ifc`` so reports that mix orientations within a
          single output are handled correctly. ifcclash's
          ``check_all=false`` mode emits one pair per collision in
          whatever order it discovered the entities, so a single report
          can contain both ``(space, beam)`` and ``(beam, space)``
          tuples; the per-pair classifier catches both.

        Unresolved pairs (neither side is in the source IFC) are
        dropped and counted in ``stats['pairs_unresolved']``.
        """
        chosen = self.pairs_source_side
        self.stats["pairs_source_side_resolved"] = chosen

        if chosen == "a":
            self.logger.info(
                "pairs_source_side='a': trusting all pairs as (source, target)"
            )
            self.stats["pairs_oriented_a_to_b"] = len(pairs)
            return pairs

        if chosen == "b":
            self.logger.info(
                "pairs_source_side='b': flipping every (a, b) to (b, a)"
            )
            self.stats["pairs_oriented_b_to_a"] = len(pairs)
            return [(b, a) for a, b in pairs]

        # auto — classify each pair independently. ``by_guid`` is a hash
        # lookup inside ifcopenshell, but we still memoise so any pair
        # involving the same GUID twice (very common — one space clashes
        # many beams) costs one lookup, not many.
        guid_in_source: Dict[str, bool] = {}

        def _has(guid: str) -> bool:
            cached = guid_in_source.get(guid)
            if cached is not None:
                return cached
            try:
                found = source_ifc.by_guid(guid) is not None
            except Exception:
                found = False
            guid_in_source[guid] = found
            return found

        oriented: List[Tuple[str, str]] = []
        a_to_b = 0
        b_to_a = 0
        unresolved = 0
        for a, b in pairs:
            if _has(a):
                oriented.append((a, b))
                a_to_b += 1
            elif _has(b):
                oriented.append((b, a))
                b_to_a += 1
            else:
                unresolved += 1
        self.stats["pairs_oriented_a_to_b"] = a_to_b
        self.stats["pairs_oriented_b_to_a"] = b_to_a
        self.stats["pairs_unresolved"] = unresolved
        self.logger.info(
            "auto pairs_source_side: %d pair(s) classified — "
            "a→b=%d, b→a=%d, unresolved=%d",
            len(pairs), a_to_b, b_to_a, unresolved,
        )
        if unresolved:
            self.logger.warning(
                "%d clash pair(s) had neither side present in the source "
                "IFC; they have been skipped",
                unresolved,
            )
        return oriented

    @staticmethod
    def _sort_key_for_value(raw: Optional[str]) -> Tuple[int, int, object]:
        """Compute a tuple sort key for a single ``sort_by`` value.

        The returned tuple is ``(has_value, kind, key)`` so that:

        * ``has_value=0`` (real value present) ranks before
          ``has_value=1`` (missing) under ascending order — i.e. real
          values always beat missing ones, regardless of direction.
        * ``kind=0`` for numerically-parseable values (``float()``
          succeeds on the trimmed string), ``kind=1`` for everything
          else. This makes the comparison total even when the source
          property mixes numeric and string values within one clash
          group, while still preserving the requested rule for
          ``'010-217'``-style strings (parse fails → lexicographic).
        * ``key`` is a ``float`` when ``kind=0`` and the original
          string otherwise.

        Returning a real Python tuple keeps sorting stable across Python
        versions; we never need to invert the tuple itself for desc —
        callers reverse the result list, which preserves the
        "missing ⇒ last" invariant.
        """
        if raw is None:
            return (1, 0, "")
        text = str(raw).strip()
        if not text:
            return (1, 0, "")
        try:
            return (0, 0, float(text))
        except (TypeError, ValueError):
            return (0, 1, str(raw))

    def _dedupe_pairs_by_sort(
        self,
        pairs: List[Tuple[str, str]],
        source_ifc: ifcopenshell.file,
    ) -> List[Tuple[str, str]]:
        """Collapse multi-candidate target GUIDs down to a single winner
        picked by the recipe-level ``sort_by`` rule.

        Pre-condition: pairs are oriented (first element of every tuple
        is the source GUID — same contract as ``_orient_pairs``'s
        output). Multi-candidate groups with **no** source carrying a
        sort value pass through untouched so the legacy ``aggregate``
        strategy can still process them.
        """
        if not self.sort_by:
            return pairs

        # 1. Group oriented pairs by target.
        by_target: Dict[str, List[str]] = {}
        for a, b in pairs:
            by_target.setdefault(b, []).append(a)

        # 2. Pre-compute the sort value for every unique source GUID
        #    that actually has competition. Singletons need no lookup
        #    (they're the only candidate, so they win by default).
        candidate_sources: set = set()
        for sources in by_target.values():
            if len(sources) > 1:
                candidate_sources.update(sources)
        sort_values: Dict[str, Optional[str]] = {}
        for guid in candidate_sources:
            try:
                element = source_ifc.by_guid(guid)
            except Exception:
                element = None
            if element is None:
                sort_values[guid] = None
                continue
            sort_values[guid] = self._extract_value(element, self.sort_by)

        # 3. Walk each target group and pick the canonical winner. We
        #    rebuild ``pairs`` from scratch so the output order matches
        #    the input order of *winning* pairs (helps downstream stats
        #    stay deterministic).
        retained_sources_per_target: Dict[str, set] = {}
        multi = 0
        winners = 0
        fellthrough = 0
        dropped = 0
        for target, sources in by_target.items():
            if len(sources) <= 1:
                retained_sources_per_target[target] = set(sources)
                continue
            multi += 1
            # Distinguish candidates that actually have a sort value vs
            # those that don't, so we can fall through when **every**
            # candidate is value-less.
            valued: List[Tuple[Tuple[int, int, object], str]] = []
            unvalued: List[str] = []
            for src in sources:
                key = self._sort_key_for_value(sort_values.get(src))
                if key[0] == 0:  # has_value
                    valued.append((key, src))
                else:
                    unvalued.append(src)
            if not valued:
                # Nobody had a sort value → fall back to the legacy
                # aggregate by retaining the whole group.
                retained_sources_per_target[target] = set(sources)
                fellthrough += 1
                continue
            # Sort by (kind, key) within the valued bucket; desc just
            # reverses that ordering. Missing-value candidates are
            # discarded outright once any candidate has a real value
            # (per spec: "always beats one without").
            valued.sort(key=lambda kv: kv[0])
            if self.sort_order == "desc":
                valued.reverse()
            winner_src = valued[0][1]
            retained_sources_per_target[target] = {winner_src}
            winners += 1
            dropped += len(sources) - 1

        # 4. Filter the input pair list down to (winner_src, target)
        #    tuples, preserving original order so deterministic stats /
        #    debugging are easy.
        kept: List[Tuple[str, str]] = []
        seen_pair: set = set()
        for a, b in pairs:
            allowed = retained_sources_per_target.get(b)
            if not allowed or a not in allowed:
                continue
            # Avoid emitting (a,b) twice if it appeared twice in the
            # incoming list — dedup is per pair-tuple, not per
            # (target, source) winner choice.
            key = (a, b)
            if key in seen_pair:
                continue
            seen_pair.add(key)
            kept.append(key)

        self.stats["pairs_dropped_by_sort"] = dropped
        self.stats["targets_with_multiple_candidates"] = multi
        self.stats["targets_dedup_winners"] = winners
        self.stats["targets_no_sort_value_fellthrough"] = fellthrough
        self.logger.info(
            "sort_by=%r sort_order=%s: %d target(s) had >1 candidate; "
            "%d picked a winner (dropped %d pair(s)); %d fell through "
            "(no candidate had a sort value)",
            self.sort_by, self.sort_order, multi, winners, dropped, fellthrough,
        )
        return kept

    def patch(self) -> None:
        try:
            # 1. Materialise the clash report (inline JSON or download).
            blob = self._materialize_pairs_blob()

            # 2. Walk it into (a, b) tuples.
            pairs = self._parse_pairs(blob)
            if not pairs:
                self.stats["mappings_stats"] = self._build_mappings_stats()
                self.logger.info(
                    "No clash pairs to process; the patched output IFC will "
                    "be byte-identical to the input"
                )
                return
            self.stats["pairs_total"] = len(pairs)
            self.logger.info(
                "Loaded %d clash pair(s) from pairs_source; %d mapping(s) to apply",
                len(pairs), len(self.mappings),
            )

            # 3. Open the source file (model A) once and reuse it for both
            #    per-pair orientation classification and source-value
            #    extraction. ifcclash labels clash sides by *file
            #    ordering*, not by which one is the spaces model — and
            #    inside a single report a given collision can be reported
            #    as either (space, beam) or (beam, space), so we classify
            #    each pair against the open source IFC and flip when
            #    needed. ``pairs_source_side='a'``/``'b'`` skip the
            #    classifier and trust the caller globally.
            source_path = self._resolve_to_local_file(
                self.source_file_arg, what="source_file"
            )
            self.logger.info(
                "Loading source IFC for property propagation: %s", source_path,
            )
            source_ifc = ifcopenshell.open(source_path)
            try:
                self.logger.info(
                    "Source IFC loaded: schema=%s, total elements=%s",
                    source_ifc.schema,
                    len(source_ifc.by_type("IfcRoot")),
                )
            except Exception:
                self.logger.info(
                    "Source IFC loaded: schema=%s", source_ifc.schema,
                )

            pairs = self._orient_pairs(pairs, source_ifc)
            if not pairs:
                self.stats["mappings_stats"] = self._build_mappings_stats()
                self.logger.info(
                    "No pairs left after orientation classification; "
                    "the patched output IFC will be byte-identical to the input"
                )
                return

            # Optional pre-aggregation dedup: when sort_by is set, every
            # target with multiple candidate sources is reduced to a
            # single winner picked by the sort rule. Empty sort_by is
            # the zero-effect path (no source IFC re-traversal, no
            # extra stats).
            pairs = self._dedupe_pairs_by_sort(pairs, source_ifc)
            if not pairs:
                self.stats["mappings_stats"] = self._build_mappings_stats()
                self.logger.info(
                    "No pairs left after sort-by dedup; the patched "
                    "output IFC will be byte-identical to the input"
                )
                return

            unique_source_guids = {a for a, _ in pairs}
            per_mapping_source = self._build_source_value_maps(
                source_ifc, unique_source_guids,
            )

            # 4. Collapse to one value per (mapping, target b_guid) via each
            #    mapping's aggregation strategy.
            per_mapping_target = self._aggregate_target_values_per_mapping(
                pairs, per_mapping_source
            )

            # 5. Decide on the data_type for each mapping. The writer needs
            #    a concrete type for IfcPropertySingleValue.NominalValue.
            data_types: List[str] = []
            for idx, mapping in enumerate(self.mappings):
                override = mapping.get("data_type")
                if override:
                    dt = override
                else:
                    sample = next(iter(per_mapping_target[idx].values()), None)
                    dt = "IfcLabel" if sample is None else self._infer_data_type(sample)
                data_types.append(dt)
                self.stats["per_mapping"][mapping["to"]]["data_type_used"] = dt
                self.logger.info(
                    "Mapping[%d] %r → %r will write %d target element(s) "
                    "(data_type=%s, aggregate=%s)",
                    idx, mapping["from"], mapping["to"],
                    len(per_mapping_target[idx]), dt,
                    mapping.get("aggregate", self.aggregate_strategy),
                )

            # 6. Build the union of all target GUIDs. Each element is
            #    resolved once and all applicable mappings are written
            #    while we still hold the entity_instance — single pass
            #    over the target IFC, no matter how many mappings.
            #    Pre-warm the owner-history cache so the per-element loop
            #    never has to call self.file.by_type() (which has been
            #    observed to intermittently SIGSEGV on large files).
            self._get_or_create_owner_history()
            all_target_guids: set = set()
            for bucket in per_mapping_target:
                all_target_guids.update(bucket.keys())
            self.logger.info(
                "Resolving %d unique target element(s) for write phase",
                len(all_target_guids),
            )
            for b_guid in all_target_guids:
                try:
                    element = self.file.by_guid(b_guid)
                except Exception:
                    element = None
                if element is None:
                    # Target b_guid clashed but the element isn't present
                    # in the target IFC (selector mismatch, stale clash
                    # report, etc). Count it as a hard skip per mapping
                    # that had a candidate value for this GUID.
                    for idx, mapping in enumerate(self.mappings):
                        if b_guid in per_mapping_target[idx]:
                            self.stats["per_mapping"][mapping["to"]]["targets_skipped"] += 1
                    continue
                for idx, mapping in enumerate(self.mappings):
                    value = per_mapping_target[idx].get(b_guid)
                    if value is None:
                        # Some *other* mapping wrote to this element but
                        # we have no source value for this one. Worth
                        # counting so the per-mapping coverage is honest.
                        self.stats["per_mapping"][mapping["to"]]["targets_skipped_no_source_value"] += 1
                        continue
                    stat = self.stats["per_mapping"][mapping["to"]]
                    stat["targets_resolved"] += 1
                    spec = self._mapping_targets[idx]
                    ok = self._write_property_for_target(
                        element,
                        value,
                        data_types[idx],
                        spec["pset"],
                        spec["property"],
                        spec["is_attribute"],
                    )
                    if ok:
                        stat["targets_modified"] += 1
                    else:
                        stat["targets_skipped"] += 1

            # Flat mappings_stats summary array — easier for callers
            # (n8n, dashboards) than the keyed per_mapping dict.
            self.stats["mappings_stats"] = self._build_mappings_stats()

            self.logger.info(
                "PropagatePropertyFromClashPairs done: %s",
                json.dumps(self.stats, sort_keys=True),
            )
        finally:
            for path in self._tempfiles:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _build_mappings_stats(self) -> List[Dict[str, object]]:
        """Project ``self.stats['per_mapping']`` into the flat array
        format requested by the workflow / dashboard side.
        """
        out: List[Dict[str, object]] = []
        for mapping in self.mappings:
            pm = self.stats["per_mapping"][mapping["to"]]
            out.append({
                "from": mapping["from"],
                "to": mapping["to"],
                "sources_read": pm["source_hits"],
                "sources_missing": pm["source_misses"],
                "targets_written": pm["targets_modified"],
                "targets_skipped_no_source_value":
                    pm["targets_skipped_no_source_value"],
                "targets_skipped_write_failed": pm["targets_skipped"],
                "conflicts_dropped": pm["conflicts_dropped"],
                "data_type_used": pm["data_type_used"],
                "aggregate": pm["aggregate"],
            })
        return out

    def get_output(self) -> ifcopenshell.file:
        return self.file
