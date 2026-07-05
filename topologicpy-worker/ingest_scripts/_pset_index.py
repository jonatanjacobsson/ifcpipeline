"""Forward-pass property-set index — name-filtered replacement for per-element
``ifcopenshell.util.element.get_psets()`` inverse walks.

``get_psets()`` walks each element's ``IsDefinedBy`` inverse and extracts *every*
pset on the element; calling it per element is O(elements × psets). When a script
only reads a few known pset names, one forward pass over
``IfcRelDefinesByProperties`` (plus ``IfcRelDefinesByType`` for type inheritance)
builds the same name→props dicts for the whole file at once, extracting values
only for the wanted names. Values come from ifcopenshell's own
``get_property_definition``, so property and IfcElementQuantity values (including
the ``"id"`` key) match a ``get_psets()`` call exactly — only discovery differs.

Underscore-prefixed on purpose: not an ingest script (skipped by discovery).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

import ifcopenshell.util.element

from ingest_scripts import safe_by_type


def build_pset_index(
    ifc: Any,
    pset_names: Iterable[str],
    psets_only: bool = False,
    only_ids: Optional[Set[int]] = None,
) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """Map element ``id()`` → ``{pset_name: props}`` for ONLY the given pset names.

    Mirrors ``get_psets(element, psets_only=...)`` restricted to ``pset_names``:
    type psets are inherited first (first ``IfcRelDefinesByType`` per object wins,
    as in ``get_type``) and occurrence ``IfcRelDefinesByProperties`` definitions
    update on top, merging same-named sets in relation order. Elements without any
    wanted pset have no entry — callers use ``index.get(element.id(), {})``.

    ``only_ids`` restricts the index to those element ids, skipping value
    extraction for psets attached to anything else (e.g. a broadly-used pset name
    like ``BIP`` when only spaces are read).
    """
    wanted = frozenset(pset_names)
    prop_cache: Dict[int, Dict[str, Any]] = {}

    def _wanted(definition: Any) -> bool:
        # getattr default: IFC4 allows RelatingPropertyDefinition to be an
        # IfcPropertySetDefinitionSet (an aggregate SELECT, not an entity), which
        # has no Name and raises AttributeError on access — get_psets() skips
        # such definitions, so the index must too instead of killing the job.
        if definition is None or getattr(definition, "Name", None) not in wanted:
            return False
        if (
            psets_only
            and not definition.is_a("IfcPropertySet")
            and not definition.is_a("IfcPreDefinedPropertySet")
        ):
            return False
        return True

    def _props(definition: Any) -> Dict[str, Any]:
        did = definition.id()
        props = prop_cache.get(did)
        if props is None:
            props = prop_cache[did] = ifcopenshell.util.element.get_property_definition(definition)
        return props

    index: Dict[int, Dict[str, Dict[str, Any]]] = {}

    # Type-pset inheritance: get_psets() seeds from get_type(element) first.
    type_of: Dict[int, Any] = {}
    for rel in safe_by_type(ifc, "IfcRelDefinesByType"):
        relating_type = rel.RelatingType
        if relating_type is None:
            continue
        for obj in rel.RelatedObjects or []:
            oid = obj.id()
            if only_ids is not None and oid not in only_ids:
                continue
            type_of.setdefault(oid, relating_type)
    type_psets_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for oid, type_obj in type_of.items():
        tid = type_obj.id()
        tpsets = type_psets_cache.get(tid)
        if tpsets is None:
            tpsets = type_psets_cache[tid] = {}
            for definition in getattr(type_obj, "HasPropertySets", None) or []:
                if _wanted(definition):
                    tpsets.setdefault(definition.Name, {}).update(_props(definition))
        if tpsets:
            # Copy per element: occurrence psets update on top of the type's.
            index[oid] = {name: dict(props) for name, props in tpsets.items()}

    for rel in safe_by_type(ifc, "IfcRelDefinesByProperties"):
        definition = rel.RelatingPropertyDefinition
        if not _wanted(definition):
            continue
        props = None
        for obj in rel.RelatedObjects or []:
            oid = obj.id()
            if only_ids is not None and oid not in only_ids:
                continue
            if props is None:
                props = _props(definition)
            index.setdefault(oid, {}).setdefault(definition.Name, {}).update(props)

    return index
