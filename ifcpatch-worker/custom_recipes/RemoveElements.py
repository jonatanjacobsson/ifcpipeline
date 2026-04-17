"""
RemoveElements - Fast in-place removal of IFC elements

Unlike ExtractElements (which rebuilds a new file containing only selected
elements), this recipe modifies the file in-place by removing the targeted
elements.  This is dramatically faster when you want to *drop* a minority of
elements from a large model, because it only processes the elements being
removed — not the thousands you want to keep.

Supports the same ifcopenshell selector syntax as ExtractElements for the
query parameter, but the semantics are inverted: the query selects what to
*remove*, not what to keep.

Performance: uses a single-pass bulk relationship detachment (O(R + E))
instead of per-element remove_product calls (O(R × E)), where R = total
relationships and E = elements to remove.

**Policy:** instances of ``IfcSite``, ``IfcBuilding``, and ``IfcBuildingStorey``
are **never** removed, regardless of query. That keeps the core spatial
hierarchy and containment references intact for viewers (e.g. Solibri).
``IfcSpace`` and other spatial elements (e.g. ``IfcExternalSpatialElement``)
*can* be removed by query.

Recipe Name: RemoveElements
Description: Fast in-place removal of elements from an IFC model
Author: IFC Pipeline Team
"""

import logging
import re
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.selector
from typing import Union, List, Set, Optional
from logging import Logger

# Negated IFC class: "!IfcCovering" or "! IfcCovering" (same as ifcopenshell selector)
_NEGATED_IFC_TYPE = re.compile(r"^!\s*(Ifc[A-Za-z_][A-Za-z0-9_]*)$")


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Union[Logger, None] = None,
        query: str = "IfcSpace",
        clean_geometry: bool = True,
        clean_orphaned_types: bool = True,
    ):
        """Remove elements matching a selector query from the IFC model in-place

        This is the fast alternative to using ExtractElements to keep everything
        *except* certain elements.  Instead of copying the entire model minus
        the unwanted elements into a new file, this recipe removes the unwanted
        elements directly.

        :param query: Selector query for elements to REMOVE. Supports
            ifcopenshell selector syntax — e.g. ``"IfcSpace"``,
            ``"IfcSpace, IfcZone"``, ``"IfcDuctSegment, IfcPipeSegment"``,
            or any valid filter_elements expression.
            A comma-separated list of negated IFC types (``"!IfcCovering"`` or
            ``"! IfcCovering"``) removes every ``IfcProduct`` and ``IfcTypeProduct``
            except instances of those classes — i.e. the same universe as
            ``filter_elements`` uses, minus the listed types (union of exclusions).
            Add more ``!Ifc…`` terms to keep additional classes, e.g.
            ``"!IfcCovering, !IfcBuildingStorey"`` keeps coverings and building
            storeys while removing other products. Spatial structure elements
            are always kept automatically (see module docstring).
        :param clean_geometry: When True (default), also remove orphaned
            representations and placements that belonged exclusively to the
            removed elements.  Set to False for maximum speed if you don't
            care about leftover unreferenced geometry.
        :param clean_orphaned_types: When True, remove IfcTypeProduct
            instances that no longer have any associated elements after the
            removal.  Defaults to True.
        """
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)
        self.query = query.strip().rstrip(",").strip() if isinstance(query, str) else query

        if isinstance(clean_geometry, str):
            self.clean_geometry = clean_geometry.lower() in ("true", "1", "yes")
        else:
            self.clean_geometry = bool(clean_geometry)

        if isinstance(clean_orphaned_types, str):
            self.clean_orphaned_types = clean_orphaned_types.lower() in ("true", "1", "yes")
        else:
            self.clean_orphaned_types = bool(clean_orphaned_types)

        self.logger.info(
            f"RemoveElements: query={self.query!r}, "
            f"clean_geometry={self.clean_geometry}, "
            f"clean_orphaned_types={self.clean_orphaned_types}"
        )
        # Set by patch(); worker skips ifcpatch.write when True (no redundant copy to output)
        self.skip_output_write = False

    def patch(self):
        elements = self._select_elements()
        elements = self._exclude_spatial_structure_containers(elements)
        if not elements:
            self.skip_output_write = True
            self.logger.info("RemoveElements: no elements matched the query — nothing to do")
            return

        self.logger.info(f"RemoveElements: removing {len(elements)} element(s)")

        remove_ids = {e.id() for e in elements}

        orphaned_geometry: Set[int] = set()
        orphaned_placements: Set[int] = set()
        if self.clean_geometry:
            orphaned_geometry, orphaned_placements = self._collect_orphaned_geometry(
                elements, remove_ids
            )

        type_ids_before: Set[int] = set()
        if self.clean_orphaned_types:
            type_ids_before = self._collect_related_types(elements)

        self._bulk_detach_relationships(remove_ids)
        self._remove_entities(elements, "element")

        if orphaned_geometry:
            self._remove_geometry_tree(orphaned_geometry)
        if orphaned_placements:
            self._remove_entities_by_id(orphaned_placements, "placement")

        if self.clean_orphaned_types and type_ids_before:
            self._remove_orphaned_types(type_ids_before)

        self._remove_empty_relationships()

        self.logger.info("RemoveElements: done")

    # IFC classes that are always protected from removal regardless of query.
    # IfcSpace is intentionally NOT in this list — it can be removed by query.
    _PROTECTED_TYPES = ("IfcSite", "IfcBuilding", "IfcBuildingStorey")

    def _exclude_spatial_structure_containers(self, elements: list) -> list:
        """Drop core spatial container instances from the removal set (policy).

        Protects IfcSite, IfcBuilding, and IfcBuildingStorey because removing
        them breaks ``IfcRelContainedInSpatialStructure`` links and corrupts the
        storey/building hierarchy in viewers like Solibri.  IfcSpace is NOT
        protected and can be removed by query.
        """
        if not elements:
            return elements
        kept: list = []
        dropped = 0
        for e in elements:
            try:
                if any(e.is_a(t) for t in self._PROTECTED_TYPES):
                    dropped += 1
                    continue
            except Exception:
                pass
            kept.append(e)
        if dropped:
            self.logger.info(
                f"RemoveElements: policy — excluded {dropped} "
                f"protected spatial container(s) (IfcSite/IfcBuilding/IfcBuildingStorey); "
                f"{len(kept)} element(s) targeted for removal"
            )
        return kept

    def _parse_negated_ifc_types(self, query_terms: List[str]) -> Optional[List[str]]:
        """If every term is '!IfcClass', return class names; else None."""
        if not query_terms:
            return None
        names: List[str] = []
        for t in query_terms:
            m = _NEGATED_IFC_TYPE.match(t)
            if not m:
                return None
            names.append(m.group(1))
        return names

    def _elements_all_except_types(self, excluded_type_names: List[str]) -> list:
        """All IfcProduct ∪ IfcTypeProduct instances except the given IFC classes.

        Matches ifcopenshell ``filter_elements`` default scope. Each ``!Ifc…``
        term removes that class from the removal set (i.e. those instances are
        kept). Example: ``!IfcCovering, !IfcBuildingStorey`` keeps coverings and
        storeys; ``!IfcCovering, !IfcWall`` keeps coverings and walls.
        """
        universe: Set = set(self.file.by_type("IfcProduct")) | set(self.file.by_type("IfcTypeProduct"))
        to_remove = set(universe)
        for type_name in excluded_type_names:
            try:
                excluded = set(self.file.by_type(type_name))
            except Exception:
                self.logger.warning(f"Unknown IFC type: {type_name}")
                excluded = set()
            to_remove -= excluded
            self.logger.info(
                f"RemoveElements: negated {type_name!r} excluded {len(excluded)} instance(s); "
                f"{len(to_remove)} element(s) selected to remove so far"
            )
        self.logger.info(f"RemoveElements: negated-type query → {len(to_remove)} element(s) to remove")
        return list(to_remove)

    def _select_elements(self) -> list:
        """Resolve the query into a list of elements.

        Handles both ifcopenshell selector expressions and simple
        comma-separated IFC type names (e.g. "IfcSpace, IfcCovering").
        """
        query_terms = [t.strip() for t in self.query.split(",") if t.strip()]

        negated_names = self._parse_negated_ifc_types(query_terms)
        if negated_names is not None:
            return self._elements_all_except_types(negated_names)

        all_look_like_types = all(t.startswith("Ifc") and t.isidentifier() for t in query_terms)

        if all_look_like_types and len(query_terms) >= 1:
            elements = []
            for type_name in query_terms:
                try:
                    found = self.file.by_type(type_name)
                    self.logger.info(f"RemoveElements: by_type({type_name}) → {len(found)} element(s)")
                    elements.extend(found)
                except Exception:
                    self.logger.warning(f"Unknown IFC type: {type_name}")
            if elements:
                return elements

        try:
            selected = ifcopenshell.util.selector.filter_elements(self.file, self.query)
            result = list(selected) if selected else []
            self.logger.info(f"RemoveElements: filter_elements({self.query!r}) → {len(result)} element(s)")
            return result
        except Exception as e:
            self.logger.warning(f"Selector query also failed ({e})")
            return []

    def _collect_orphaned_geometry(
        self, elements: list, remove_ids: Set[int]
    ) -> tuple:
        """Find representations and placements used exclusively by target elements."""
        orphaned_geom_ids: Set[int] = set()
        orphaned_placement_ids: Set[int] = set()

        for element in elements:
            if hasattr(element, "Representation") and element.Representation:
                prod_repr = element.Representation
                inverse = self.file.get_inverse(prod_repr)
                referencing = {e.id() for e in inverse} - remove_ids
                if not referencing:
                    orphaned_geom_ids.add(prod_repr.id())

            if hasattr(element, "ObjectPlacement") and element.ObjectPlacement:
                placement = element.ObjectPlacement
                inverse = self.file.get_inverse(placement)
                referencing = {e.id() for e in inverse} - remove_ids
                if not referencing:
                    orphaned_placement_ids.add(placement.id())

        return orphaned_geom_ids, orphaned_placement_ids

    def _collect_related_types(self, elements: list) -> Set[int]:
        """Collect IfcTypeProduct IDs that are assigned to any of the target elements."""
        type_ids: Set[int] = set()
        for element in elements:
            try:
                element_type = ifcopenshell.util.element.get_type(element)
                if element_type:
                    type_ids.add(element_type.id())
            except Exception:
                pass
        return type_ids

    def _bulk_detach_relationships(self, remove_ids: Set[int]) -> None:
        """Single pass through all relationships, detaching target elements.

        This is the key performance optimisation: O(R) instead of O(R × E).
        """
        detached_count = 0
        for rel in list(self.file.by_type("IfcRelationship")):
            try:
                info = rel.get_info(recursive=False)
            except Exception:
                continue

            dirty = False
            for attr_name, val in info.items():
                if attr_name in ("id", "type"):
                    continue

                if isinstance(val, ifcopenshell.entity_instance) and val.id() in remove_ids:
                    try:
                        setattr(rel, attr_name, None)
                        dirty = True
                    except Exception:
                        pass
                elif isinstance(val, (tuple, list)):
                    filtered = [
                        v
                        for v in val
                        if not (isinstance(v, ifcopenshell.entity_instance) and v.id() in remove_ids)
                    ]
                    if len(filtered) != len(val):
                        try:
                            setattr(rel, attr_name, filtered)
                            dirty = True
                        except Exception:
                            pass

            if dirty:
                detached_count += 1

        self.logger.info(f"RemoveElements: detached targets from {detached_count} relationship(s)")

    def _remove_entities(self, entities: list, label: str) -> None:
        removed = 0
        for entity in entities:
            try:
                self.file.remove(entity)
                removed += 1
            except Exception as e:
                self.logger.debug(f"Could not remove {label} #{entity.id()}: {e}")
        self.logger.info(f"RemoveElements: removed {removed} {label}(s)")

    def _remove_entities_by_id(self, entity_ids: Set[int], label: str) -> None:
        removed = 0
        for eid in entity_ids:
            try:
                entity = self.file.by_id(eid)
                self.file.remove(entity)
                removed += 1
            except Exception:
                pass
        if removed:
            self.logger.info(f"RemoveElements: removed {removed} orphaned {label}(s)")

    def _remove_geometry_tree(self, prod_repr_ids: Set[int]) -> None:
        """Remove IfcProductDefinitionShape and its child IfcShapeRepresentations."""
        removed = 0
        for repr_id in prod_repr_ids:
            try:
                prod_repr = self.file.by_id(repr_id)
            except Exception:
                continue

            sub_reps = []
            if hasattr(prod_repr, "Representations") and prod_repr.Representations:
                sub_reps = list(prod_repr.Representations)

            try:
                self.file.remove(prod_repr)
                removed += 1
            except Exception:
                continue

            for sub in sub_reps:
                try:
                    inverse = self.file.get_inverse(sub)
                    if not inverse:
                        items = list(sub.Items) if hasattr(sub, "Items") and sub.Items else []
                        self.file.remove(sub)
                        removed += 1
                        for item in items:
                            try:
                                if not self.file.get_inverse(item):
                                    self.file.remove(item)
                                    removed += 1
                            except Exception:
                                pass
                except Exception:
                    pass

        if removed:
            self.logger.info(f"RemoveElements: removed {removed} orphaned geometry entit(ies)")

    def _remove_orphaned_types(self, type_ids: Set[int]) -> None:
        """Remove IfcTypeProduct instances that have no remaining typed elements."""
        removed = 0
        for tid in type_ids:
            try:
                type_entity = self.file.by_id(tid)
            except Exception:
                continue

            has_instances = False
            for rel in getattr(type_entity, "Types", []):
                if hasattr(rel, "RelatedObjects") and rel.RelatedObjects:
                    has_instances = True
                    break

            if has_instances:
                continue

            try:
                for rel in list(getattr(type_entity, "Types", [])):
                    self.file.remove(rel)
                self.file.remove(type_entity)
                removed += 1
            except Exception as e:
                self.logger.debug(f"Could not remove orphaned type #{tid}: {e}")

        if removed:
            self.logger.info(f"RemoveElements: removed {removed} orphaned type(s)")

    def _remove_empty_relationships(self) -> None:
        """Clean up relationships that have no related objects left."""
        removed = 0
        for rel in list(self.file.by_type("IfcRelationship")):
            try:
                info = rel.get_info(recursive=False)
                related_attrs = [k for k in info if k.startswith("Related")]
                if related_attrs and all(
                    not info[k] or info[k] == () for k in related_attrs
                ):
                    self.file.remove(rel)
                    removed += 1
            except Exception:
                pass

        if removed:
            self.logger.info(f"RemoveElements: cleaned up {removed} empty relationship(s)")

    def get_output(self) -> ifcopenshell.file:
        return self.file
