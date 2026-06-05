"""Tests for PropagatePropertyFromClashPairs ``sort_by`` dedup.

Builds a minimal in-memory IFC4 file with three IfcSpaces and one
IfcBeam that clashes all three. Then exercises:

* the no-op path (``sort_by=""``) — confirms behaviour is byte-identical
  to the legacy ``aggregate`` strategy;
* ``sort_by='Name'``, ``sort_order='asc'`` — confirms the lowest-named
  space wins and the dedup stats are populated;
* ``sort_by='Name'``, ``sort_order='desc'`` — confirms the highest-named
  space wins;
* ``sort_by='BIP Room function programme.Room number'`` — confirms the
  spaced pset name path is parsed correctly and that the pset-stored
  values drive the dedup just like attributes do;
* a "fall-through" case where the sort_by property is **missing** on
  every candidate — confirms the legacy aggregate path still runs and
  the new ``targets_no_sort_value_fellthrough`` stat fires.

The fixture writes the clash pairs via a temp JSON file so the recipe
exercises its real ``_materialize_pairs_blob`` path. Source values are
read straight off the same ``ifcopenshell.file`` to avoid having to
spin up two IFC files for a 3-space test.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))


def _make_fixture(room_numbers_in_pset: bool = False):
    """Build a tiny IFC with 3 IfcSpaces + 1 IfcBeam clashing all three.

    When ``room_numbers_in_pset`` is True the 3 spaces also carry a
    ``BIP Room function programme`` pset with a ``Room number`` value
    matching the Name — used to exercise the pset-name-with-spaces
    sort_by path.
    """
    f = ifcopenshell.file(schema="IFC4")
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    units = f.create_entity("IfcUnitAssignment", (length,))
    origin = f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0))
    z_ax = f.create_entity("IfcDirection", (0.0, 0.0, 1.0))
    x_ax = f.create_entity("IfcDirection", (1.0, 0.0, 0.0))
    axes = f.create_entity("IfcAxis2Placement3D", origin, z_ax, x_ax)
    ctx = f.create_entity(
        "IfcGeometricRepresentationContext",
        None, "Model", 3, 1.0e-5, axes, None,
    )
    f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="SortDedupTest",
        RepresentationContexts=(ctx,),
        UnitsInContext=units,
    )
    owner_history = None  # minimal — recipe will create one on demand

    space_names = ["010-100", "010-200", "050-100"]
    spaces = []
    for name in space_names:
        sp = f.create_entity(
            "IfcSpace",
            GlobalId=ifcopenshell.guid.new(),
            Name=name,
            LongName=f"Space {name}",
            ObjectPlacement=None,
        )
        spaces.append(sp)
        if room_numbers_in_pset:
            prop = f.create_entity(
                "IfcPropertySingleValue",
                Name="Room number",
                NominalValue=f.create_entity("IfcLabel", name),
            )
            pset = f.create_entity(
                "IfcPropertySet",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=owner_history,
                Name="BIP Room function programme",
                HasProperties=(prop,),
            )
            f.create_entity(
                "IfcRelDefinesByProperties",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=owner_history,
                RelatedObjects=(sp,),
                RelatingPropertyDefinition=pset,
            )

    beam = f.create_entity(
        "IfcBeam",
        GlobalId=ifcopenshell.guid.new(),
        Name="Beam1",
        ObjectPlacement=None,
    )
    return f, spaces, beam


def _pairs_blob(spaces, beam):
    """Return a temp file path containing the ifcclash pair JSON for
    every (space, beam) clash."""
    payload = [
        {"a_global_id": sp.GlobalId, "b_global_id": beam.GlobalId}
        for sp in spaces
    ]
    fh = tempfile.NamedTemporaryFile(
        delete=False, mode="w", suffix=".json", prefix="propagate-sort-test-",
    )
    json.dump(payload, fh)
    fh.close()
    return fh.name


def _write_source_to_temp(f) -> str:
    fh = tempfile.NamedTemporaryFile(
        delete=False, suffix=".ifc", prefix="propagate-sort-src-",
    )
    fh.close()
    f.write(fh.name)
    return fh.name


def _run(
    *,
    sort_by: str,
    sort_order: str,
    use_pset_source: bool = False,
    sort_by_value_source: str = "Name",
    aggregate: str = "first",
    fixture_room_numbers_in_pset: bool = False,
):
    """One end-to-end recipe invocation; returns (Patcher, beam_after, props_on_beam)."""
    from PropagatePropertyFromClashPairs import Patcher  # noqa: E402

    src, spaces, beam = _make_fixture(
        room_numbers_in_pset=fixture_room_numbers_in_pset
    )
    src_path = _write_source_to_temp(src)
    pairs_path = _pairs_blob(spaces, beam)

    # The target IFC ("model B") is the *same* file in this test (it
    # holds the same beam GlobalId). The recipe opens ``source_file``
    # separately for source-value extraction, so reusing one in-memory
    # file is fine — the recipe writes onto the beam in ``src`` /
    # ``target`` interchangeably here.
    target = ifcopenshell.open(src_path)
    target_beam_guid = beam.GlobalId
    target_beam = target.by_guid(target_beam_guid)

    p = Patcher(
        target,
        logging.getLogger("sort_dedup_test"),
        source_file=src_path,
        pairs_source=pairs_path,
        property_from=sort_by_value_source if not use_pset_source else "BIP Room function programme.Room number",
        property_to="BIP.SpaceName",
        aggregate=aggregate,
        pairs_source_side="auto",
        sort_by=sort_by,
        sort_order=sort_order,
    )
    p.patch()
    return p, target_beam, target


def _read_bip(beam):
    import ifcopenshell.util.element as ue
    return (ue.get_psets(beam).get("BIP") or {})


def test_empty_sort_by_is_no_op():
    """With sort_by="", the recipe must behave exactly like before:
    aggregate=first wins the first oriented pair seen, all 3 pairs are
    counted in pairs_total, no dedup stats fire.
    """
    p, beam, _ = _run(sort_by="", sort_order="asc", aggregate="first")
    assert p.stats["pairs_total"] == 3
    assert p.stats["sort_by_resolved"] == ""
    assert p.stats["sort_order_resolved"] == ""
    assert p.stats["pairs_dropped_by_sort"] == 0
    assert p.stats["targets_with_multiple_candidates"] == 0
    assert p.stats["targets_dedup_winners"] == 0
    bip = _read_bip(beam)
    # aggregate=first → whichever space hit first wins; we don't pin to
    # a specific name since pair iteration order is not part of the
    # legacy contract. But the value MUST be one of the three.
    assert bip.get("SpaceName") in {"010-100", "010-200", "050-100"}


def test_sort_by_name_asc_picks_lowest_lex_name():
    p, beam, _ = _run(sort_by="Name", sort_order="asc")
    bip = _read_bip(beam)
    assert bip.get("SpaceName") == "010-100", bip
    assert p.stats["sort_by_resolved"] == "Name"
    assert p.stats["sort_order_resolved"] == "asc"
    assert p.stats["pairs_dropped_by_sort"] == 2
    assert p.stats["targets_with_multiple_candidates"] == 1
    assert p.stats["targets_dedup_winners"] == 1
    assert p.stats["targets_no_sort_value_fellthrough"] == 0


def test_sort_by_name_desc_picks_highest_lex_name():
    p, beam, _ = _run(sort_by="Name", sort_order="desc")
    bip = _read_bip(beam)
    assert bip.get("SpaceName") == "050-100", bip
    assert p.stats["pairs_dropped_by_sort"] == 2
    assert p.stats["targets_dedup_winners"] == 1


def test_sort_by_alias_ascending_normalises_to_asc():
    """``sort_order='ascending'`` must be accepted and produce the
    same winner as ``asc``."""
    p, beam, _ = _run(sort_by="Name", sort_order="ascending")
    assert p.stats["sort_order_resolved"] == "asc"
    assert _read_bip(beam).get("SpaceName") == "010-100"


def test_sort_by_pset_with_spaces_in_name():
    """Confirms the dedup path correctly looks up a property whose
    pset name contains literal spaces (``BIP Room function programme``)
    — the same shape the Nobel parent now ships."""
    p, beam, _ = _run(
        sort_by="BIP Room function programme.Room number",
        sort_order="asc",
        fixture_room_numbers_in_pset=True,
    )
    assert p.stats["pairs_dropped_by_sort"] == 2
    assert p.stats["targets_dedup_winners"] == 1
    # All three spaces hold their Name in the pset's Room number too,
    # so the asc winner is still "010-100".
    assert _read_bip(beam).get("SpaceName") == "010-100"


def test_fellthrough_when_no_candidate_has_sort_value():
    """When ``sort_by`` resolves to None on every candidate (here:
    ``Description`` is unset on all three spaces), the recipe must keep
    every candidate and let the legacy aggregate strategy run, while
    counting the group in ``targets_no_sort_value_fellthrough``.
    """
    p, beam, _ = _run(
        sort_by="Description",
        sort_order="asc",
        aggregate="first",
    )
    assert p.stats["targets_with_multiple_candidates"] == 1
    assert p.stats["targets_dedup_winners"] == 0
    assert p.stats["targets_no_sort_value_fellthrough"] == 1
    assert p.stats["pairs_dropped_by_sort"] == 0
    # aggregate=first still wrote *something* — one of the three names.
    assert _read_bip(beam).get("SpaceName") in {"010-100", "010-200", "050-100"}


def test_unknown_sort_order_falls_back_to_asc():
    p, beam, _ = _run(sort_by="Name", sort_order="garbage")
    assert p.stats["sort_order_resolved"] == "asc"
    assert _read_bip(beam).get("SpaceName") == "010-100"


def test_sort_key_numeric_when_parseable():
    """Sort key helper: pure numeric strings sort numerically, mixed
    strings sort lexicographically, missing values rank last regardless
    of direction.
    """
    from PropagatePropertyFromClashPairs import Patcher

    sk = Patcher._sort_key_for_value
    # Numeric kind, ordered by float value
    assert sk("10") < sk("100"), (sk("10"), sk("100"))
    assert sk("2") < sk("10"), "'2' < '10' numerically, must not be lexicographic"
    # Lexicographic kind for storey-prefixed strings
    assert sk("010-217") < sk("010-218"), "lex sort within same prefix"
    assert sk("010-999") < sk("050-100"), "lex sort across prefixes"
    # Missing value always ranks LAST under asc ordering
    assert sk("010-100") < sk(None), "real value beats missing under asc"
    assert sk("010-100") < sk(""), "empty string is missing"
    # Numeric and string sort into separate kinds so comparison is total
    assert sk("42") < sk("010-100"), "numeric kind (0) before string kind (1)"
