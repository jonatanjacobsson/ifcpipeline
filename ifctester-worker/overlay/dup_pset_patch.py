"""Duplicate-pset fix for IfcTester; extracted verbatim from tasks.py (_patch_ifctester_duplicate_psets)."""


def apply():
    """Let a Property requirement be satisfied by any same-named property set.

    Revit emits one IfcPropertySet per export-settings rule, so an element
    matching two rules for the same name gets two distinct pset entities (e.g.
    SPF holding spfFMGUID on one and ccName/ccNameUUID on the other). Stock
    ifctester resolves a string propertySet with get_pset(), which returns only
    the first match, so properties in the second are reported missing.

    Each candidate is checked separately rather than merged into one dict:
    ifctester re-reads the pset entity by its "id" to verify dataType, and a
    merged dict can only carry one id, which silently skips that check for
    properties sourced from the other pset.
    """
    import ifcopenshell.util.element
    import ifctester.facet as facet

    original_call = facet.Property.__call__
    original_get_pset = facet.get_pset

    def candidates(inst, name):
        found, seen = [], set()
        for rel in getattr(inst, "IsDefinedBy", None) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            pset = rel.RelatingPropertyDefinition
            if getattr(pset, "Name", None) != name or pset.id() in seen:
                continue
            seen.add(pset.id())
            found.append(ifcopenshell.util.element.get_property_definition(pset))
        return found

    def patched_call(self, inst, logger=None):
        if not isinstance(self.propertySet, str):
            return original_call(self, inst, logger)
        found = candidates(inst, self.propertySet)
        if len(found) < 2:
            return original_call(self, inst, logger)

        # "prohibited" must hold against every candidate; everything else is
        # satisfied as soon as one candidate meets it in full.
        require_all = self.cardinality == "prohibited"
        result = None
        for pset in found:
            facet.get_pset = lambda i, n, *a, _p=pset, **k: _p
            try:
                result = original_call(self, inst, logger)
            finally:
                facet.get_pset = original_get_pset
            if bool(result) != require_all:
                return result
        return result

    facet.Property.__call__ = patched_call
