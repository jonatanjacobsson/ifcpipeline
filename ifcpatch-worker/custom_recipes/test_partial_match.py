#!/usr/bin/env python3
"""
Diagnostic script for V3.2 partial-match MappingSource fix.

Run inside the ifcpatch-worker container:
    docker exec -it ifcpipeline-ifcpatch-worker-1 python /app/custom_recipes/test_partial_match.py

Tests that SetColorBySelector only colors matched elements when a MappingSource
is shared between matched and unmatched elements.
"""

import logging
import sys
import json
import os
import tempfile
from collections import defaultdict

import ifcopenshell
import ifcopenshell.util.selector

sys.path.insert(0, '/app')
from custom_recipes.SetColorBySelector import Patcher

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger('test_partial_match')

IFC_FILE = os.environ.get('TEST_IFC_FILE', '/uploads/V--50_V02000.ifc')
SELECTOR = 'BIP.StatusConstruction=/.*KLADD.*/'
HEX_COLOR = '2b2b2b'


def build_mapping_source_index(ifc_file):
    """Build MappingSource -> set(all elements) index."""
    ms_to_all = defaultdict(set)
    for product in ifc_file.by_type('IfcProduct'):
        if not hasattr(product, 'Representation') or not product.Representation:
            continue
        if not product.Representation.is_a('IfcProductDefinitionShape'):
            continue
        for rep in product.Representation.Representations:
            if not rep.is_a('IfcShapeRepresentation'):
                continue
            if rep.Items:
                for item in rep.Items:
                    if item.is_a('IfcMappedItem'):
                        ms_to_all[item.MappingSource].add(product)
    return ms_to_all


def get_element_styles(element):
    """Get all surface styles applied to an element's representations."""
    styles = []
    if not hasattr(element, 'Representation') or not element.Representation:
        return styles
    if not element.Representation.is_a('IfcProductDefinitionShape'):
        return styles
    for rep in element.Representation.Representations:
        if not rep.is_a('IfcShapeRepresentation'):
            continue
        if rep.Items:
            for item in rep.Items:
                if item.is_a('IfcMappedItem'):
                    mapped_rep = item.MappingSource.MappedRepresentation
                    if mapped_rep and mapped_rep.Items:
                        for geo_item in mapped_rep.Items:
                            if hasattr(geo_item, 'StyledByItem') and geo_item.StyledByItem:
                                for styled in geo_item.StyledByItem:
                                    if styled.Styles:
                                        for s in styled.Styles:
                                            styles.append(s)
    return styles


def has_dark_grey_style(element, target_r=0.169, target_g=0.169, target_b=0.169, tol=0.02):
    """Check if element has been styled with dark grey (#2b2b2b)."""
    for style in get_element_styles(element):
        if style.is_a('IfcPresentationStyleAssignment'):
            for sub in style.Styles if style.Styles else []:
                if hasattr(sub, 'Styles') and sub.Styles:
                    for surface_style in sub.Styles:
                        if hasattr(surface_style, 'SurfaceColour') and surface_style.SurfaceColour:
                            c = surface_style.SurfaceColour
                            if (abs(c.Red - target_r) < tol and
                                abs(c.Green - target_g) < tol and
                                abs(c.Blue - target_b) < tol):
                                return True
        elif style.is_a('IfcSurfaceStyle'):
            if style.Styles:
                for surface_style in style.Styles:
                    if hasattr(surface_style, 'SurfaceColour') and surface_style.SurfaceColour:
                        c = surface_style.SurfaceColour
                        if (abs(c.Red - target_r) < tol and
                            abs(c.Green - target_g) < tol and
                            abs(c.Blue - target_b) < tol):
                            return True
    return False


def test_diagnose_partial_matches():
    """Phase 1: Diagnose how many MappingSources are partial matches."""
    logger.info(f"=== DIAGNOSTIC: Partial-match analysis on {IFC_FILE} ===")
    ifc_file = ifcopenshell.open(IFC_FILE)
    logger.info(f"Loaded {IFC_FILE}, schema={ifc_file.schema}")

    selector = f"IfcElement, {SELECTOR}"
    matched = ifcopenshell.util.selector.filter_elements(ifc_file, selector)
    matched_set = set(matched)
    logger.info(f"Selector '{SELECTOR}' matched {len(matched)} elements")

    if not matched:
        logger.warning("No elements matched - nothing to test")
        return False

    ms_index = build_mapping_source_index(ifc_file)
    logger.info(f"Total MappingSources in file: {len(ms_index)}")

    ms_to_matched = defaultdict(list)
    for elem in matched:
        if not hasattr(elem, 'Representation') or not elem.Representation:
            continue
        if not elem.Representation.is_a('IfcProductDefinitionShape'):
            continue
        for rep in elem.Representation.Representations:
            if not rep.is_a('IfcShapeRepresentation'):
                continue
            if rep.Items:
                for item in rep.Items:
                    if item.is_a('IfcMappedItem'):
                        ms_to_matched[item.MappingSource].append(elem)

    partial_count = 0
    full_count = 0
    total_over_colored = 0
    for ms, matched_elems in ms_to_matched.items():
        all_elems = ms_index.get(ms, set())
        matched_here = set(matched_elems)
        if matched_here < all_elems:
            partial_count += 1
            over = len(all_elems) - len(matched_here)
            total_over_colored += over
            logger.info(
                f"  PARTIAL: MS#{ms.id()} has {len(all_elems)} total, "
                f"{len(matched_here)} matched, {over} would be OVER-COLORED"
            )
        else:
            full_count += 1

    logger.info(f"\nSummary: {len(ms_to_matched)} MappingSources used by matched elements")
    logger.info(f"  Full matches (safe): {full_count}")
    logger.info(f"  Partial matches (need splitting): {partial_count}")
    logger.info(f"  Total elements that WOULD be over-colored without fix: {total_over_colored}")
    return partial_count > 0


def test_patched_coloring():
    """Phase 2: Run the patched SetColorBySelector and verify correctness."""
    logger.info(f"\n=== VALIDATION: Running patched SetColorBySelector ===")
    ifc_file = ifcopenshell.open(IFC_FILE)

    selector = f"IfcElement, {SELECTOR}"
    matched_before = ifcopenshell.util.selector.filter_elements(ifc_file, selector)
    matched_ids = {e.GlobalId for e in matched_before}
    logger.info(f"Matched {len(matched_ids)} elements with KLADD before patching")

    ms_index = build_mapping_source_index(ifc_file)
    ms_to_matched = defaultdict(list)
    for elem in matched_before:
        if not hasattr(elem, 'Representation') or not elem.Representation:
            continue
        if not elem.Representation.is_a('IfcProductDefinitionShape'):
            continue
        for rep in elem.Representation.Representations:
            if not rep.is_a('IfcShapeRepresentation'):
                continue
            if rep.Items:
                for item in rep.Items:
                    if item.is_a('IfcMappedItem'):
                        ms_to_matched[item.MappingSource].append(elem)

    at_risk_elements = set()
    for ms, mel in ms_to_matched.items():
        all_elems = ms_index.get(ms, set())
        unmatched = all_elems - set(mel)
        at_risk_elements.update(unmatched)

    at_risk_ids = {e.GlobalId for e in at_risk_elements}
    logger.info(f"Identified {len(at_risk_ids)} at-risk elements (share MS with matched, should NOT be colored)")

    op = json.dumps({"selectors": SELECTOR, "hex": HEX_COLOR})
    patcher = Patcher(ifc_file, logger, operation1=op)
    patcher.patch()
    patched_file = patcher.get_output()

    logger.info(f"\nStats: {patcher.stats}")
    logger.info(f"\n--- Checking coloring correctness ---")

    matched_colored = 0
    matched_not_colored = 0
    for gid in matched_ids:
        elem = patched_file.by_guid(gid)
        if has_dark_grey_style(elem):
            matched_colored += 1
        else:
            matched_not_colored += 1

    at_risk_colored = 0
    at_risk_clean = 0
    at_risk_colored_gids = []
    for gid in at_risk_ids:
        elem = patched_file.by_guid(gid)
        if has_dark_grey_style(elem):
            at_risk_colored += 1
            if len(at_risk_colored_gids) < 5:
                at_risk_colored_gids.append(gid)
        else:
            at_risk_clean += 1

    logger.info(f"\nMatched elements ({len(matched_ids)} total):")
    logger.info(f"  Colored (correct):     {matched_colored}")
    logger.info(f"  Not colored (missed):  {matched_not_colored}")

    logger.info(f"\nAt-risk elements ({len(at_risk_ids)} total):")
    logger.info(f"  Clean (correct):       {at_risk_clean}")
    logger.info(f"  Over-colored (BUG):    {at_risk_colored}")
    if at_risk_colored_gids:
        logger.info(f"  Sample over-colored:   {at_risk_colored_gids}")

    success = at_risk_colored == 0
    if success:
        logger.info("\n*** PASS: No over-coloring detected! V3.2 fix is working. ***")
    else:
        logger.error(f"\n*** FAIL: {at_risk_colored} at-risk elements were over-colored! ***")

    return success


if __name__ == '__main__':
    if not os.path.exists(IFC_FILE):
        logger.error(f"Test file not found: {IFC_FILE}")
        logger.info("Set TEST_IFC_FILE env var to point to an IFC file with partial-match scenario")
        sys.exit(1)

    has_partials = test_diagnose_partial_matches()
    if not has_partials:
        logger.info("No partial matches found in this file - fix not applicable here")
        sys.exit(0)

    success = test_patched_coloring()
    sys.exit(0 if success else 1)
