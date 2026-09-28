"""AssignLubekalkPset: model graph → Lubekalk rows, on a small synthetic duct network."""

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
sys.path.insert(0, str(CUSTOM))
sys.path.insert(0, str(WORKER_ROOT / "scripts"))

import _lubekalk  # noqa: E402

MAPPING = importlib.import_module("mappings.lubekalk_magicad")  # the recipe's tracked default
LOG = logging.getLogger("test_lubekalk")


# --- a tiny MagiCAD-shaped model -------------------------------------------------


class Builder:
    def __init__(self):
        self.f = f = ifcopenshell.file(schema="IFC4")
        ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject")
        ifcopenshell.api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS"})
        ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
        self.body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                                         target_view="MODEL_VIEW", parent=ctx)
        site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite")
        building = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding")
        ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=f.by_type("IfcProject")[0])
        ifcopenshell.api.run("aggregate.assign_object", f, products=[building], relating_object=site)
        self.storeys = []
        for name, z in (("PLAN 01", 0.0), ("PLAN 02", 8000.0)):
            storey = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=name)
            ifcopenshell.api.run("aggregate.assign_object", f, products=[storey], relating_object=building)
            ifcopenshell.api.run("geometry.edit_object_placement", f, product=storey,
                                 matrix=self._matrix((0, 0, z)), is_si=False)
            self.storeys.append(storey)

    @staticmethod
    def _matrix(origin, z_axis=(0, 0, 1), x_axis=None):
        z = np.array(z_axis, dtype=float)
        z /= np.linalg.norm(z)
        if x_axis is None:
            helper = np.array([0, 0, 1.0]) if abs(z[2]) < 0.9 else np.array([1.0, 0, 0])
            x = np.cross(helper, z)
        else:
            x = np.array(x_axis, dtype=float)
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
        return m

    def element(self, ifc_class, reference, bip=None, name=None, psets=None):
        f = self.f
        e = ifcopenshell.api.run("root.create_entity", f, ifc_class=ifc_class, name=name or reference)
        ifcopenshell.api.run("spatial.assign_container", f, products=[e], relating_structure=self.storeys[0])
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=e, matrix=self._matrix((0, 0, 0)),
                             is_si=False)
        qto = ifcopenshell.api.run("pset.add_pset", f, product=e, name="Pset_QuantityTakeOff")
        ifcopenshell.api.run("pset.edit_pset", f, pset=qto, properties={"Reference": reference})
        values = {"StoreyName": "PLAN 01", "SystemName": "T1", "SpaceLongName": "Office area", **(bip or {})}
        pset = ifcopenshell.api.run("pset.add_pset", f, product=e, name="BIP")
        ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties=values)
        for pset_name, props in (psets or {}).items():
            extra = ifcopenshell.api.run("pset.add_pset", f, product=e, name=pset_name)
            ifcopenshell.api.run("pset.edit_pset", f, pset=extra, properties=props)
        return e

    def port(self, element, origin, axis):
        port = ifcopenshell.api.run("system.add_port", self.f, element=element)
        ifcopenshell.api.run("geometry.edit_object_placement", self.f, product=port,
                             matrix=self._matrix(origin, axis), is_si=False)
        return port

    def connect(self, a, b):
        ifcopenshell.api.run("system.connect_port", self.f, port1=a, port2=b)

    def rect_body(self, element, x, y, depth):
        f = self.f
        profile = f.createIfcRectangleProfileDef("AREA", None, None, float(x), float(y))
        solid = f.createIfcExtrudedAreaSolid(profile, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0., 0., 0.))),
                                             f.createIfcDirection((0., 0., 1.)), float(depth))
        rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [solid])
        element.Representation = f.createIfcProductDefinitionShape(None, None, [rep])


def build_network():
    """
    A ─ bend(90°) ─ B ─ reduction ─ C ─ tee ─ plug
                                         └── D (branch 125)
    R (rect 600x300, B80) ─ rect bend
    plus: a flow controller on D, an insulation part, a ceiling, a fan-room duct.
    """
    b = Builder()
    z = 3000.0
    duct = {"TypeDescription": "Spirokanal varmförzinkad stål"}
    A = b.element("IfcDuctSegment", "Cirkulär kanal", {**duct, "Diameter": 250.0, "Length": 3000.0})
    a1, a2 = b.port(A, (0, 0, z), (-1, 0, 0)), b.port(A, (3000, 0, z), (1, 0, 0))
    bend = b.element("IfcDuctFitting", "magicirc_elbow_full_radius_001", {"InsulationType": "V20"})
    # First port axis points INTO the fitting, as some exports do; the engine must flip it.
    e1, e2 = b.port(bend, (3000, 0, z), (1, 0, 0)), b.port(bend, (3300, 300, z), (0, 1, 0))
    B = b.element("IfcDuctSegment", "Cirkulär kanal", {**duct, "Diameter": 250.0, "Length": 2000.0})
    b1, b2 = b.port(B, (3300, 300, z), (0, -1, 0)), b.port(B, (3300, 2300, z), (0, 1, 0))
    red = b.element("IfcDuctFitting", "RCU")
    r1, r2 = b.port(red, (3300, 2300, z), (0, -1, 0)), b.port(red, (3300, 2400, z), (0, 1, 0))
    C = b.element("IfcDuctSegment", "Cirkulär kanal", {**duct, "Diameter": 160.0, "Length": 1000.0})
    c1, c2 = b.port(C, (3300, 2400, z), (0, -1, 0)), b.port(C, (3300, 3400, z), (0, 1, 0))
    tee = b.element("IfcDuctFitting", "magicirc_tee_centric_90_001")
    t1, t2, t3 = (b.port(tee, (3300, 3400, z), (0, -1, 0)), b.port(tee, (3300, 3600, z), (0, 1, 0)),
                  b.port(tee, (3400, 3500, z), (1, 0, 0)))
    plug = b.element("IfcDuctFitting", "magicirc_plug_001")
    p1 = b.port(plug, (3300, 3600, z), (0, -1, 0))
    D = b.element("IfcDuctSegment", "Cirkulär kanal", {**duct, "Diameter": 125.0, "Length": 500.0})
    d1, d2 = b.port(D, (3400, 3500, z), (-1, 0, 0)), b.port(D, (3900, 3500, z), (1, 0, 0))
    ftcu = b.element("IfcFlowController", "FTCU-125", {"ProductType": "FTCU-125"})
    f1 = b.port(ftcu, (3900, 3500, z), (-1, 0, 0))
    for x, y in ((a2, e1), (e2, b1), (b2, r1), (r2, c1), (c2, t1), (t2, p1), (t3, d1), (d2, f1)):
        b.connect(x, y)

    R = b.element("IfcDuctSegment", "Rektangulär kanal",
                  {"TypeDescription": "Rektangulär kanal varmförzinkad stål", "Length": 2500.0,
                   "InsulationType": "B80"},
                  psets={"Pset_DuctSegmentTypeCommon": {"Shape": "RECTANGULAR"}})
    b.rect_body(R, 600, 300, 2500)
    q1, q2 = b.port(R, (0, 5000, 5000), (-1, 0, 0)), b.port(R, (2500, 5000, 5000), (1, 0, 0))
    rbend = b.element("IfcDuctFitting", "LBXR")
    s1, s2 = b.port(rbend, (2500, 5000, 5000), (-1, 0, 0)), b.port(rbend, (2800, 5300, 5000), (0, 1, 0))
    b.connect(q2, s1)

    # A "rectangular take-off from a circular duct" family, modelled between two
    # rectangular ducts: the connected ducts decide the code, not the family name.
    R2 = b.element("IfcDuctSegment", "Rektangulär kanal",
                   {"TypeDescription": "Rektangulär kanal varmförzinkad stål", "Length": 1000.0},
                   psets={"Pset_DuctSegmentTypeCommon": {"Shape": "RECTANGULAR"}})
    b.rect_body(R2, 800, 400, 1000)
    g1 = b.port(R2, (6000, 0, 3000), (1, 0, 0))
    lpsr = b.element("IfcDuctFitting", "magirect_outlet1_001 from cirk lika Lindab LPSR")
    h1, h2 = b.port(lpsr, (6000, 0, 3000), (-1, 0, 0)), b.port(lpsr, (6100, 0, 3000), (1, 0, 0))
    R3 = b.element("IfcDuctSegment", "Rektangulär kanal",
                   {"TypeDescription": "Rektangulär kanal varmförzinkad stål", "Length": 1000.0},
                   psets={"Pset_DuctSegmentTypeCommon": {"Shape": "RECTANGULAR"}})
    b.rect_body(R3, 400, 300, 1000)
    k1 = b.port(R3, (6100, 0, 3000), (-1, 0, 0))
    b.connect(g1, h1), b.connect(h2, k1)

    fan = b.element("IfcDuctSegment", "Cirkulär kanal",
                    {**duct, "Diameter": 400.0, "Length": 1000.0, "SpaceLongName": "AHU plant room"})
    b.port(fan, (0, 9000, 2500), (-1, 0, 0)), b.port(fan, (1000, 9000, 2500), (1, 0, 0))
    b.element("IfcBuildingElementPart", "V20", name="Duct Insulation:V20:123")
    # The same insulation exported as a covering (Revit, M1 v43) is still the host's insulation.
    b.element("IfcCovering", "V50", name="Duct Insulation:V50:456")
    # A provision for a void is håltagning; a lone ProvisionForVoid.Volume (Revit writes it
    # on ordinary elements) is not.
    hole = b.element("IfcBuildingElementProxy", "Opening - Rectangle",
                     psets={"Pset_ProvisionForVoid": {"Width": 400.0, "Height": 200.0, "Depth": 300.0}})
    hole.ObjectType = "PROVISIONFORVOID"
    extra = ifcopenshell.api.run("pset.add_pset", b.f, product=ftcu, name="Pset_ProvisionForVoid")
    ifcopenshell.api.run("pset.edit_pset", b.f, pset=extra, properties={"Volume": 0.01})
    b.element("IfcCovering", "FLÄKTRUMSTAK 80MM")
    names = {"A": A, "bend": bend, "B": B, "red": red, "C": C, "tee": tee, "plug": plug, "D": D, "ftcu": ftcu,
             "R": R, "rbend": rbend, "fan": fan, "R2": R2, "lpsr": lpsr, "R3": R3, "hole": hole}
    return b.f, names


@pytest.fixture(scope="module")
def assigned():
    f, names = build_network()
    mapping = _lubekalk.Mapping(MAPPING)
    model = _lubekalk.VentilationModel(f, mapping)
    assigner = _lubekalk.Assigner(model, mapping, handling_source="BIP.StoreyName", pdel_source="BIP.SystemName",
                                  include_hangers=True, include_voids=True)
    return f, names, {key: assigner.assign(element) for key, element in names.items()}, assigner


def test_hangers_and_voids_stay_out_unless_asked_for():
    # The kalkylator never prices upphängning or håltagning in Lubekalk: both are opt-in.
    f, names = build_network()
    mapping = _lubekalk.Mapping(MAPPING)
    assigner = _lubekalk.Assigner(_lubekalk.VentilationModel(f, mapping), mapping,
                                  handling_source="BIP.StoreyName", pdel_source="BIP.SystemName")
    hole, duct = assigner.assign(names["hole"]), assigner.assign(names["A"])
    assert (hole.status, hole.rows) == ("Utanför", [])
    assert "include_voids" in hole.reason
    assert duct.status == "Klar" and not [row for row in duct.rows if row.role == "upphängning"]


def test_ducts_carry_their_own_size_length_and_hangers(assigned):
    _, _, a, _ = assigned
    duct = a["A"]
    assert duct.status == "Klar"
    assert (duct.primary.kod, duct.primary.dim1, duct.primary.langd) == ("CK", 250.0, 3.0)
    assert duct.montage == "Normalmontage      th. <4.0 m" and duct.height_m == 3.0
    assert duct.material == "Galvplåt" and duct.handling == "PLAN 01" and duct.pdel == "T1"
    assert [(r.role, r.kod) for r in duct.rows] == [("primär", "CK"), ("upphängning", "CU1")]


def test_a_bend_takes_its_size_from_the_duct_and_its_angle_from_the_ports(assigned):
    _, _, a, assigner = assigned
    bend = a["bend"]
    # One exported axis pointed inward; oriented outward the bend is 90°, not 0° or 180°.
    assert assigner.bend_angle(assigned[1]["bend"]) == 90.0
    assert (bend.primary.kod, bend.primary.dim1, bend.primary.antal) == ("CB9", 250.0, 1)
    # V20 thermal insulation has no Lubekalk code in the template: no guessed detail row.
    assert [r.role for r in bend.rows] == ["primär"]


def test_a_reduction_is_sized_on_both_sides_and_coded_for_a_pipe_run(assigned):
    red = assigned[2]["red"]
    assert (red.primary.kod, red.primary.dim1, red.primary.dim2) == ("CDR", 250.0, 160.0)


def test_a_tee_is_run_and_branch_and_a_plug_inherits_through_the_run(assigned):
    a = assigned[2]
    assert (a["tee"].primary.kod, a["tee"].primary.dim1, a["tee"].primary.dim2) == ("CT", 160.0, 125.0)
    # The plug touches only the tee; its size came through the tee's main run.
    assert (a["plug"].primary.kod, a["plug"].primary.dim1) == ("CGD", 160.0)


def test_products_are_sakvaror_with_their_designation(assigned):
    ftcu = assigned[2]["ftcu"]
    assert (ftcu.primary.kod, ftcu.primary.beteckning, ftcu.primary.antal) == ("SAK", "FTCU-125", 1)


def test_a_product_exported_as_a_fitting_is_still_a_sakvara():
    # BRNF roof hoods: a terminal in one Revit export, a one-port unconnected fitting in the next.
    b = Builder()
    hood = b.element("IfcDuctFitting", "BRNF-1300", {"ProductType": "BRNF-1300"}, name="AD1")
    b.port(hood, (0, 0, 3000), (0, 0, -1))
    mapping = _lubekalk.Mapping(MAPPING)
    a = _lubekalk.Assigner(_lubekalk.VentilationModel(b.f, mapping), mapping, handling_source="BIP.StoreyName",
                           pdel_source="BIP.SystemName").assign(hood)
    assert (a.status, a.primary.kod, a.primary.beteckning) == ("Klar", "SAK", "BRNF-1300")


def test_rectangular_ducts_read_their_section_and_fire_insulation(assigned):
    a = assigned[2]
    duct = a["R"]
    assert (duct.primary.kod, duct.primary.dim1, duct.primary.dim2, duct.primary.langd) == ("RK", 600.0, 300.0, 2.5)
    assert duct.montage == "Höjdmontage 6 m   th. <6.0 m"  # 5.0 m above the floor below
    assert [(r.role, r.kod) for r in duct.rows] == [("primär", "RK"), ("isolering", "RI7"), ("upphängning", "RU3")]
    assert (a["rbend"].primary.kod, a["rbend"].primary.dim1, a["rbend"].primary.dim2) == ("RB", 600.0, 300.0)


def test_a_family_name_that_contradicts_its_ducts_yields_to_the_ducts(assigned):
    lpsr = assigned[2]["lpsr"]
    assert (lpsr.primary.kod, lpsr.primary.dim1) == ("RA1", 800.0)
    assert lpsr.confidence == "låg" and "stämmer inte med anslutningarna" in lpsr.source


def test_provisions_for_voids_are_haltagning(assigned):
    a = assigned[2]
    assert (a["hole"].primary.kod, a["hole"].primary.dim1, a["hole"].primary.dim2) == ("RH", 400.0, 200.0)
    assert a["ftcu"].primary.kod == "SAK"  # a stray ProvisionForVoid.Volume does not make it a hole


def test_space_decides_montage_before_height(assigned):
    assert assigned[2]["fan"].montage == "Fläktrum"


def test_every_element_gets_a_status_and_the_export_aggregates(assigned, tmp_path):
    from AssignLubekalkPset import Patcher
    import _lubekalk_export as export

    f, _, _, _ = assigned
    patcher = Patcher(f, LOG, include_hangers="true",
                      include_voids="true")
    patcher.patch()
    statuses = {e.GlobalId: ifcopenshell.util.element.get_psets(e)["Lubekalk"]["Status"]
                for e in f.by_type("IfcElement")}
    assert set(statuses.values()) <= {"Klar", "Ingår", "Utanför"}
    assert patcher.report["by_status"] == {"Klar": 16, "Ingår": 2, "Utanför": 1}
    duct = next(e for e in f.by_type("IfcDuctSegment")
                if ifcopenshell.util.element.get_psets(e)["BIP"].get("Diameter") == 250.0)
    assert "UpphängningKod" in ifcopenshell.util.element.get_psets(duct)["Lubekalk"]

    # A rerun with the defaults replaces the pset instead of leaving stale rows behind.
    rerun = Patcher(f, LOG)
    rerun.patch()
    assert rerun.report["by_status"] == {"Klar": 15, "Ingår": 2, "Utanför": 2}  # the hole is out
    assert "UpphängningKod" not in ifcopenshell.util.element.get_psets(duct)["Lubekalk"]

    result = export.collect(f)
    ck = [row for row in result["import"] if row["Kod"] == "CK" and row["Dim 1"] == 250.0]
    # A (3.0 m) and B (2.0 m): same code, size, montage, material, handling, part → one row.
    assert len(ck) == 1 and ck[0]["Längd"] == 5.0 and ck[0]["Antal"] == 0
    assert list(result["import"][0]) == list(_lubekalk.COLUMNS)
    assert {g["Isolering"] for g in result["insulation"]} == {"V20"}  # thermal insulation left to code
    written = export.write(result, tmp_path / "import.csv")
    assert written and written[0].read_text(encoding="utf-8-sig").startswith("Kod;Rev;Handling;P-del")


@pytest.mark.parametrize("value, expected", [
    (177.8, 178), (96.0685546875, 96), (7.50018004996617, 8), (52.5, 53), (27.5, 28),
    (1200.001171875, 1200), (250.0, 250), (None, None),
])
def test_dimensions_are_whole_millimetres(value, expected):
    # Lubekalk rejects "177,8" ("is not a valid integer value"); half rounds up, not to even.
    assert _lubekalk.whole_mm(value) == expected
    assert _lubekalk.Row("RH", dim1=value).dim1 == expected


def test_a_pset_written_before_rounding_still_exports_whole_millimetres():
    pset = {"Status": "Klar", "Kod": "RH", "Dim 1": 177.849609375, "Dim 2": 27.500065612793, "Antal": 1}
    (row,) = _lubekalk.rows_from_pset(pset)
    assert (row["Dim 1"], row["Dim 2"]) == (178, 28)


def test_unknown_codes_are_set_aside_and_the_csv_is_what_lubekalk_reads(tmp_path):
    from AssignLubekalkPset import Patcher
    import _lubekalk_export as export

    f, _ = build_network()
    Patcher(f, LOG, include_hangers="true",
            include_voids="true").patch()
    full = export.collect(f)
    hangers = {row["Kod"] for row in full["import"]} & {"CU1", "CU3", "RU1", "RU3"}
    assert hangers

    result = export.collect(f, omit_codes=sorted(hangers))
    assert not {row["Kod"] for row in result["import"]} & hangers
    assert {row["Kod"] for row in result["omitted"]} == hangers
    assert len(result["import"]) + len(result["omitted"]) == len(full["import"])
    ck = sum(row["Längd"] for row in full["import"] if row["Kod"] == "CK" and not row["Dim 2"])
    cu = result["summary"]["omitted_codes"].get("CU1") or result["summary"]["omitted_codes"].get("CU3")
    assert cu["Längd"] <= ck  # hangers are per metre of the duct they hang

    path = tmp_path / "lubekalk.csv"
    assert export.write_lubekalk_csv(result["import"], path) == 0
    raw = path.read_bytes()
    assert raw.startswith("Kod;Rev;Handling;P-del;Beteckning;Dim 1;Dim 2;Montageläge;".encode("cp1252"))
    assert b"\r\n" in raw
    lines = raw.decode("cp1252").splitlines()[1:]
    rh = next(line.split(";") for line in lines if line.startswith("RH;"))
    assert (rh[5], rh[6]) == ("400", "200")
    for cells in (line.split(";") for line in lines):
        assert cells[5].lstrip("-").isdigit() and cells[6].lstrip("-").isdigit(), cells
        assert cells[9].isdigit(), cells  # Antal
    written = export.write(result, tmp_path / "import.xlsx")
    assert written

    # Details can drop their notes so Lubekalk attaches them to the duct row; products keep names.
    bare = export.collect(f, keep_beteckning=["SAK"])
    assert all(not row["Beteckning"] for row in bare["import"] if row["Kod"] != "SAK")
    assert any(row["Beteckning"] for row in bare["import"] if row["Kod"] == "SAK")


def test_the_recipe_returns_the_import_csv_and_a_report(tmp_path):
    from AssignLubekalkPset import Patcher

    f, _ = build_network()
    f._input_file_path = str(tmp_path / "input.ifc")  # the worker stages the input here
    patcher = Patcher(f, LOG, keep_beteckning_only="SAK")
    patcher.patch()
    artifacts = patcher.get_artifacts()
    assert set(artifacts) == {"lubekalk_import", "lubekalk_report"}
    assert all(Path(path).parent.parent == tmp_path for path in artifacts.values())
    lines = Path(artifacts["lubekalk_import"]).read_bytes().decode("cp1252").splitlines()
    assert lines[0] == ";".join(_lubekalk.COLUMNS)
    assert all(not line.split(";")[4] for line in lines[1:] if not line.startswith("SAK;"))
    report = json.loads(Path(artifacts["lubekalk_report"]).read_text(encoding="utf-8"))
    assert report["readiness"]["by_status"] == {"Klar": 15, "Ingår": 2, "Utanför": 2}
    summary = patcher.get_summary()
    assert summary["by_status"] == {"Klar": 15, "Ingår": 2, "Utanför": 2}
    assert summary["import_rows"] == len(lines) - 1

    dry = Patcher(build_network()[0], LOG, dry_run="true")
    dry.patch()
    assert dry.get_artifacts() == {}


def test_the_command_line_export_writes_the_same_import(tmp_path):
    import subprocess
    from AssignLubekalkPset import Patcher

    f, _ = build_network()
    Patcher(f, LOG, export_csv="false").patch()
    model = tmp_path / "patched.ifc"
    f.write(str(model))
    run = subprocess.run([sys.executable, str(WORKER_ROOT / "scripts" / "export_lubekalk_import.py"), str(model),
                          "--out", str(tmp_path / "import.xlsx"), "--lubekalk-csv", str(tmp_path / "import.csv")],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "import.csv").read_bytes().startswith(b"Kod;Rev;Handling;P-del;Beteckning;")


def test_every_parameter_is_documented_for_the_recipe_listing():
    # /patch/recipes/list reads "<name>: <description>" lines from Patcher.__init__'s docstring.
    import inspect
    from AssignLubekalkPset import Patcher

    doc = inspect.getdoc(Patcher.__init__)
    assert doc.split("\n\n")[0].startswith("Write a Lubekalk pset")
    params = [name for name in inspect.signature(Patcher.__init__).parameters
              if name not in ("self", "file", "logger")]
    for name in params:
        line = next(line.strip() for line in doc.splitlines() if line.strip().startswith(f"{name}:"))
        # The listing keeps what follows the LAST of the first two colons: one colon per line only.
        assert line.count(":") == 1, line


def test_long_designations_fit_lubekalks_field_and_stay_distinct(tmp_path):
    import _lubekalk_export as export

    names = ["BRNF-1300", "SLGPU 400-1200-100",
             "SAL_35 inverted 2 slot_shallow_JR_1500 dummy box opposite",
             "SAL_35 inverted 2 slot_shallow_JR_1900 dummy box opposite",
             "valkpro-plus-p10-east-west-3500mm"]
    short = export.short_designations(names, limit=15)
    assert set(short) == set(names[2:]) | {"SLGPU 400-1200-100"}  # only the long ones change
    assert all(len(s) <= 15 for s in short.values())
    assert len(set(short.values())) == len(short) and not set(short.values()) & {"BRNF-1300"}
    sal = short["SAL_35 inverted 2 slot_shallow_JR_1500 dummy box opposite"]
    assert sal.startswith("SAL_35 inver~") and len(sal) == 15
    # Stable across exports: another set of names does not move a product's short name.
    assert export.short_designations(names[2:3], limit=15)[names[2]] == sal

    records = [dict.fromkeys(_lubekalk.COLUMNS, "") | {"Kod": "RD2", "Beteckning": "→1050x800",
                                                       "Dim 1": 1200, "Dim 2": 800, "Antal": 1}]
    out = tmp_path / "l.csv"
    assert export.write_lubekalk_csv(records, out) == 0
    assert ";>1050x800;" in out.read_bytes().decode("cp1252")


@pytest.mark.parametrize("extents, expected", [
    ((250.0, 250.0), 250.0),          # a clean ring
    ((219.0, 217.0), 250.0),          # tee branch spigot, 0.875 x nominal
    ((295.0, 273.0), 315.0),          # saddle widens one axis; the spigot decides
    ((400.0, 329.0), 400.0),          # one extent is exact
    ((194.0, 197.0), 200.0),          # within 4 %
    ((743585.0, 353763.0), None),     # world-scale garbage is refused, not guessed
])
def test_measured_ends_snap_to_the_project_series(extents, expected):
    assert _lubekalk.Mapping(MAPPING).nominal_diameter(*extents) == expected
