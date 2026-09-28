"""
Shared engine for the Lubekalk recipes: turns ventilation model elements into
Lubekalk calculation rows (Kod, Dim 1, Dim 2, Montageläge, Kanalmaterial,
Antal, Längd).

Not an IfcPatch recipe (leading underscore — skipped by recipe discovery).

What a CAD export does not carry, the model graph does:

* **Sizes of fittings.** MagiCAD/Revit exports put ``BIP.Diameter`` on duct
  segments only. A fitting's size is read from the ports it connects to and
  propagated through the port graph (fitting-to-fitting joints included).
* **Bend angles.** Taken from the directions of a bend's two ports. The exported
  port axes point inward on some ports and outward on others, so each axis is
  first oriented away from the fitting.
* **Rectangular dimensions.** Taken from the ``IfcRectangleProfileDef`` of the
  duct's extrusion. Revit sometimes extrudes along the width and puts the length
  in the profile; the axis that equals the segment length is dropped.
* **Montageläge.** Installation height above the floor beneath the element
  (centre of its ports), banded as Normtid VVS / SBUF 13833 do for ventilation,
  unless the element's space says fan room, shaft or roof.

Every assignment records where each value came from, and an element that cannot
be resolved says why instead of being given a guess.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.system
import ifcopenshell.util.unit

PSET = "Lubekalk"

#: Column order of Lubekalk's Excel quantity import.
COLUMNS = ("Kod", "Rev", "Handling", "P-del", "Beteckning", "Dim 1", "Dim 2",
           "Montageläge", "Kanalmaterial", "Antal", "Längd", "Höjd")

STATUS_READY = "Klar"          # primary row complete
STATUS_INCLUDED = "Ingår"      # priced through another element (e.g. insulation parts)
STATUS_OUT_OF_SCOPE = "Utanför"  # not ventilation calculation scope
STATUS_MISSING = "Saknas"      # in scope but a required value is unresolved

ROUND = "round"
RECT = "rect"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _psets(element) -> dict:
    try:
        return ifcopenshell.util.element.get_psets(element)
    except Exception:
        return {}


def prop(psets: dict, path: str) -> Any:
    """Read ``Pset.Property`` from a get_psets() dict; blank/undefined → None."""
    pset, _, name = path.partition(".")
    value = (psets.get(pset) or {}).get(name)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return None if not text or text.lower() == "undefined" else text
    return value


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _unit(vector) -> tuple[float, float, float] | None:
    x, y, z = (float(v) for v in vector[:3])
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-9:
        return None
    return (x / norm, y / norm, z / norm)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _angle_deg(a, b) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(a, b)))))


# ---------------------------------------------------------------------------
# Rows and assignments
# ---------------------------------------------------------------------------


def whole_mm(value: float | None) -> int | None:
    """Lubekalk reads Dim 1 / Dim 2 as whole millimetres and rejects the row on "177,8".

    Half rounds up (52.5 -> 53), not to even: a hole or a section is never smaller than modelled.
    """
    if value is None:
        return None
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class Row:
    kod: str
    dim1: int | None = None
    dim2: int | None = None
    antal: int = 0
    langd: float = 0.0
    beteckning: str = ""
    role: str = "primär"

    def __post_init__(self):
        self.dim1, self.dim2 = whole_mm(self.dim1), whole_mm(self.dim2)


@dataclass
class Assignment:
    status: str
    reason: str = ""
    kind: str = ""
    source: str = ""
    rows: list[Row] = field(default_factory=list)
    montage: str | None = None
    height_m: float | None = None
    material: str | None = None
    handling: str | None = None
    pdel: str | None = None
    insulation: str | None = None
    insulation_code: str | None = None
    hanger_code: str | None = None
    confidence: str = "hög"

    @property
    def primary(self) -> Row | None:
        return next((row for row in self.rows if row.role == "primär"), None)


# ---------------------------------------------------------------------------
# Mapping tables → decisions
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    status: str
    kind: str = ""
    code: str | None = None
    source: str = ""
    reason: str = ""
    confidence: str = "hög"


class Mapping:
    """Decisions driven by a data-only mapping module (``mappings/<name>.py``).

    The module holds the Lubekalk code catalogue and the project's model
    vocabulary; this class holds the logic, so a kalkylator reviews one table.
    """

    def __init__(self, module):
        self.t = module
        self.PRODUCT_CODE = module.PRODUCT_CODE
        self.DUCT_CODE = module.DUCT_CODE
        self.TRANSITION_CODE = module.TRANSITION_CODE
        self.VOID_CODES = getattr(module, "VOID_CODES", {"round": "CH", "rect": "RH"})
        self.DETAIL_INSULATION_CODE = getattr(module, "DETAIL_INSULATION_CODE", None)
        self.MONTAGE_DEFAULT = module.MONTAGE_BANDS[0][1]
        self.NO_HANGER_MONTAGE = set(getattr(module, "NO_HANGER_MONTAGE", ()))
        self._product = [re.compile(p, re.IGNORECASE) for p in module.PRODUCT_PATTERNS]
        self._sizes = [re.compile(p, re.IGNORECASE) for p in module.CONNECTION_SIZE_PATTERNS]
        self._space = [(re.compile(p, re.IGNORECASE), text) for p, text in module.MONTAGE_BY_SPACE]
        self._material = [(re.compile(p, re.IGNORECASE), text) for p, text in module.MATERIAL_RULES]
        self._included = [re.compile(p, re.IGNORECASE) for p in module.INCLUDED_PART_PATTERNS]

    # -- what an element is ------------------------------------------------

    def classify(self, element, reference: str, ps: dict) -> Rule:
        cls = element.is_a()
        name = element.Name or ""
        # Duct insulation is priced through its host, whatever class the export gives it:
        # IfcBuildingElementPart in one Revit export, IfcCovering in the next (M1 v42 → v43).
        if (element.is_a("IfcBuildingElementPart") or element.is_a("IfcCovering")) and any(
                p.search(name) or p.search(reference) for p in self._included):
            return Rule(STATUS_INCLUDED, kind="isolering",
                        reason="Isoleringen prissätts via värdkanalens/fittingens isoleringsrad",
                        source=f"isolering som {cls} (BIP.InsulationType på värd)")
        for out_cls, reason in self.t.OUT_OF_SCOPE_CLASSES.items():
            if element.is_a(out_cls):
                return Rule(STATUS_OUT_OF_SCOPE, kind="utanför", reason=reason, source=f"klass {cls}")
        if element.is_a("IfcBuildingElementPart"):
            return Rule(STATUS_READY, kind="product", source="byggdelsdel som sakvara", confidence="medel")
        void = ps.get("Pset_ProvisionForVoid") or {}
        # Revit writes a lone Pset_ProvisionForVoid.Volume on ordinary elements; only an
        # opening object, or one with opening dimensions, is a provision for a void.
        if (element.ObjectType or "").upper() == "PROVISIONFORVOID" or (
                element.is_a("IfcBuildingElementProxy") and (void.get("Width") or void.get("Diameter"))):
            return Rule(STATUS_READY, kind="void", confidence="medel",
                        source="Pset_ProvisionForVoid (håltagning — ingår den i ventilationsentreprenaden?)")
        if any(p.search(reference) for p in self._product):
            return Rule(STATUS_READY, kind="product", source=f"produktbeteckning {reference!r}")
        known = self.t.REFERENCE_KINDS.get(reference)
        if known:
            kind, code, confidence = known
            return Rule(STATUS_READY, kind=kind, code=code, confidence=confidence,
                        source=f"Pset_QuantityTakeOff.Reference {reference!r}")
        if element.is_a("IfcFlowSegment"):
            return Rule(STATUS_READY, kind="duct", source="IfcFlowSegment", confidence="medel")
        if element.is_a("IfcFlowFitting"):
            return Rule(STATUS_READY, kind="auto", source="portgeometri (okänd fittingtyp)", confidence="låg")
        for product_cls in self.t.PRODUCT_CLASSES:
            if element.is_a(product_cls):
                return Rule(STATUS_READY, kind="product", source=f"klass {cls}")
        return Rule(STATUS_OUT_OF_SCOPE, kind="utanför", source=f"klass {cls}",
                    reason="Ingen ventilationskalkylpost för klassen")

    def fitting_kind(self, reference, ps, ifc_class) -> str | None:
        if reference and any(p.search(reference) for p in self._product):
            return "product"
        known = self.t.REFERENCE_KINDS.get(reference or "")
        return known[0] if known else None

    # -- values ------------------------------------------------------------

    def nominal_diameter(self, *extents: float) -> float | None:
        """Snap a fitting end's measured extents to the project's nominal duct series.

        ``extents`` are the ring's width and height in the port plane. A saddle
        or partial ring can distort one of them, so an extent that matches a
        nominal size within 4 % wins first. Otherwise the smaller extent is read
        as a male spigot, which the fitting families model at 0.85-0.92 x
        nominal (MagiCAD tee branches: exactly 0.875 x). Anything else is
        refused (None) rather than guessed.
        """
        values = sorted((e for e in extents if 40 <= e <= 2600), reverse=True)
        if not values:
            return None
        series = getattr(self.t, "NOMINAL_DIAMETERS", ())
        if not series:
            return float(round(values[0]))
        for value in values:
            exact = [d for d in series if abs(value / d - 1.0) <= 0.04]
            if exact:
                return float(min(exact, key=lambda d: abs(value / d - 1.0)))
        for value in reversed(values):
            spigot = [d for d in series if 0.85 <= value / d <= 0.92]
            if spigot:
                return float(min(spigot, key=lambda d: abs(value / d - 0.88)))
        return None

    def connection_size_from_name(self, name: str) -> float | None:
        for pattern in self._sizes:
            match = pattern.search(name or "")
            if match:
                return float(match.group(1))
        return None

    def product_designation(self, element, reference, ps) -> str:
        for path in self.t.PRODUCT_NAME_PROPERTIES:
            value = prop(ps, path)
            if value:
                return str(value)
        return reference or element.Name or element.is_a()

    def montage(self, ps: dict, height_m: float | None) -> str | None:
        space = " ".join(str(prop(ps, path) or "") for path in self.t.SPACE_PROPERTIES)
        for pattern, text in self._space:
            if pattern.search(space):
                return text
        if height_m is None:
            return None
        for limit, text in self.t.MONTAGE_BANDS:
            if height_m < limit:
                return text
        return self.t.MONTAGE_BANDS[-1][1]

    def material(self, ps: dict, reference: str) -> str:
        text = " ".join(str(prop(ps, path) or "") for path in self.t.MATERIAL_PROPERTIES) + " " + (reference or "")
        for pattern, value in self._material:
            if pattern.search(text):
                return value
        return self.t.MATERIAL_DEFAULT

    def _insulation(self, insulation: str | None) -> dict:
        return self.t.INSULATION.get(insulation or "", {}) if insulation else {}

    def knows_insulation(self, insulation: str) -> bool:
        return insulation in self.t.INSULATION

    def insulation_fire_class(self, insulation: str | None) -> str | None:
        return self._insulation(insulation).get("fire_class")

    def insulation_code(self, insulation: str | None, shape: str) -> str | None:
        return self._insulation(insulation).get(shape)

    def hanger_code(self, shape: str, fire_class: str | None) -> str:
        table = self.t.HANGER_CODES[shape]
        return table.get(fire_class) or table[None]

    def bend_code(self, shape: str, angle: float) -> str:
        for limit, code in self.t.BEND_CODES[shape]:
            if angle <= limit:
                return code
        return self.t.BEND_CODES[shape][-1][1]

    def branch_code(self, kind: str, run: tuple, branch: tuple | None) -> str | None:
        """Code for a branching fitting; a cross falls back to its tee code."""
        branch_shape = branch[0] if branch else run[0]
        for key in ((kind, run[0], branch_shape), ("tee", run[0], branch_shape)):
            if key in self.t.BRANCH_CODES:
                return self.t.BRANCH_CODES[key]
        return None

    def reduction_code(self, big: tuple, small: tuple, on_fitting: bool) -> str:
        if big[0] == RECT and small[0] == RECT:
            changed = sum(1 for a, b in zip(big[1:], small[1:]) if abs(a - b) > 0.5)
            return self.t.RECT_REDUCTION_CODES[max(1, changed)]
        return self.t.ROUND_REDUCTION_CODES[on_fitting]

    def simple_code(self, kind: str, shape: str, on_fitting: bool, insulation: str | None) -> str:
        if kind == "cleanout":
            return self.t.CLEANOUT_CODES[bool(self.insulation_fire_class(insulation))]
        return self.t.SIMPLE_CODES[(kind, shape, on_fitting)]


# ---------------------------------------------------------------------------
# Model graph: ports, sizes, heights
# ---------------------------------------------------------------------------


class VentilationModel:
    """Port graph + geometry facts for one IFC file, computed once."""

    def __init__(self, file: ifcopenshell.file, mapping):
        self.file = file
        self.mapping = mapping
        scale = ifcopenshell.util.unit.calculate_unit_scale(file)  # metres per file length unit
        self.to_mm = scale * 1000.0
        self.to_m = scale
        self._psets: dict[int, dict] = {}
        self._ports: dict[int, list] = {}
        self.port_owner: dict[int, Any] = {}
        self.port_size: dict[int, tuple] = {}
        self.port_origin: dict[int, tuple[float, float, float]] = {}
        self.port_axis: dict[int, tuple[float, float, float] | None] = {}
        self.storey_levels = sorted(
            float(ifcopenshell.util.placement.get_local_placement(s.ObjectPlacement)[2, 3])
            for s in file.by_type("IfcBuildingStorey") if s.ObjectPlacement
        )
        self.size_source: dict[int, str] = {}
        self._index_ports()
        # Tiers, most trustworthy first; each only fills ports the previous left empty.
        self._seed_segment_sizes()
        self._propagate_sizes()
        self._seed_name_sizes()
        self._propagate_sizes()
        self._seed_geometry_sizes()
        self._propagate_sizes()

    # -- cached accessors --------------------------------------------------

    def psets(self, element) -> dict:
        key = element.id()
        if key not in self._psets:
            self._psets[key] = _psets(element)
        return self._psets[key]

    def ports(self, element) -> list:
        return self._ports.get(element.id(), [])

    def reference(self, element) -> str | None:
        ps = self.psets(element)
        for path in ("Pset_QuantityTakeOff.Reference", "Pset_DistributionFlowElementCommon.Reference"):
            value = prop(ps, path)
            if value:
                return str(value)
        return element.ObjectType or None

    def connected_port(self, port):
        try:
            return ifcopenshell.util.system.get_connected_port(port)
        except Exception:
            return None

    def neighbour(self, port):
        other = self.connected_port(port)
        return self.port_owner.get(other.id()) if other is not None else None

    # -- indexing ------------------------------------------------------------

    def _index_ports(self) -> None:
        for element in self.file.by_type("IfcElement"):
            try:
                ports = list(ifcopenshell.util.system.get_ports(element))
            except Exception:
                ports = []
            if not ports:
                continue
            self._ports[element.id()] = ports
            for port in ports:
                self.port_owner[port.id()] = element
                try:
                    matrix = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
                    self.port_origin[port.id()] = tuple(float(v) for v in matrix[:3, 3])
                    self.port_axis[port.id()] = _unit(matrix[:3, 2])
                except Exception:
                    self.port_axis[port.id()] = None

    def outward_axes(self, element) -> list[tuple[int, tuple[float, float, float]]]:
        """Each port's axis oriented away from the fitting's port midpoint."""
        ports = self.ports(element)
        origins = [self.port_origin.get(p.id()) for p in ports]
        if not ports or any(o is None for o in origins):
            return []
        centre = tuple(sum(o[i] for o in origins) / len(origins) for i in range(3))
        result = []
        for port, origin in zip(ports, origins):
            axis = self.port_axis.get(port.id())
            if axis is None:
                continue
            to_centre = tuple(c - o for c, o in zip(centre, origin))
            if _dot(to_centre, axis) > 0:  # axis points into the fitting: flip it
                axis = tuple(-v for v in axis)
            result.append((port.id(), axis))
        return result

    # -- sizes ---------------------------------------------------------------

    def segment_size(self, element) -> tuple | None:
        """Cross-section of a duct segment in mm: (ROUND, d) or (RECT, a, b)."""
        ps = self.psets(element)
        shape = prop(ps, "Pset_DuctSegmentTypeCommon.Shape")
        shape_text = str(shape).upper() if shape is not None else ""
        diameter = _number(prop(ps, "BIP.Diameter")) or (
            _number(prop(ps, "BIP.ProductSize")) if "RECT" not in shape_text else None)
        if diameter and "RECT" not in shape_text:
            return (ROUND, round(diameter * self.to_mm, 1))  # BIP values are in model length units
        rect = self._profile_dims(element, _number(prop(ps, "BIP.Length"))
                                  or _number(prop(ps, "Pset_DuctSegmentTypeCommon.Length")))
        if rect:
            return rect
        return None

    def _profile_dims(self, element, length: float | None) -> tuple | None:
        representation = getattr(element, "Representation", None)
        if representation is None:
            return None
        for rep in representation.Representations or []:
            for item in rep.Items or []:
                if not item.is_a("IfcExtrudedAreaSolid"):
                    continue
                area = item.SweptArea
                depth = float(item.Depth)
                if area.is_a("IfcCircleProfileDef"):
                    return (ROUND, round(2 * float(area.Radius) * self.to_mm, 1))
                if area.is_a("IfcRectangleProfileDef"):
                    dims = [float(area.XDim), float(area.YDim), depth]
                    if length:
                        # Drop the axis that is the segment's length (extrusion or profile).
                        idx = min(range(3), key=lambda i: abs(dims[i] - length))
                        if abs(dims[idx] - length) <= max(1.0, 0.01 * length):
                            dims.pop(idx)
                        else:
                            dims = dims[:2]
                    else:
                        dims = dims[:2]
                    a, b = sorted((round(d * self.to_mm, 1) for d in dims), reverse=True)
                    return (RECT, a, b)
        return None

    def _set(self, port_id: int, size: tuple, source: str) -> bool:
        if port_id in self.port_size:
            return False
        self.port_size[port_id] = size
        self.size_source[port_id] = source
        return True

    def _seed_segment_sizes(self) -> None:
        for element in self.file.by_type("IfcFlowSegment"):
            size = self.segment_size(element)
            if size:
                for port in self.ports(element):
                    self._set(port.id(), size, "kanal")

    def _seed_name_sizes(self) -> None:
        """Products whose designation states the connection size (FTCU-400, BSKC6-200…).

        Only ports the duct network left unsized: a designation number is a
        weaker claim than the duct it is bolted to.
        """
        for ports in self._ports.values():
            element = self.port_owner[ports[0].id()]
            if len(ports) > 2:
                continue  # an air handler's designation does not size all of its connections
            size = self.mapping.connection_size_from_name(self.reference(element) or "")
            if size:
                for port in ports:
                    self._set(port.id(), (ROUND, size), "produktbeteckning")

    def _seed_geometry_sizes(self) -> None:
        """Measure the fitting's own end ring at every port still unsized."""
        pending: dict[int, list] = defaultdict(list)
        for port_id, element in self.port_owner.items():
            if port_id not in self.port_size and not element.is_a("IfcFlowSegment"):
                pending[element.id()].append(port_id)
        if not pending:
            return
        try:
            import ifcopenshell.geom
            import numpy as np
        except Exception:
            return
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        per_metre = 1.0 / self.to_m  # geometry comes back in metres; ports are in file units
        for element_id, port_ids in pending.items():
            element = self.file.by_id(element_id)
            try:
                shape = ifcopenshell.geom.create_shape(settings, element)
            except Exception:
                continue
            verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) * per_metre
            known_shapes = {self.port_size[p.id()][0] for p in self.ports(element) if p.id() in self.port_size}
            for port_id in port_ids:
                port = self.file.by_id(port_id)
                try:
                    matrix = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
                except Exception:
                    continue
                origin, x_axis, y_axis, z_axis = matrix[:3, 3], matrix[:3, 0], matrix[:3, 1], matrix[:3, 2]
                offset = verts - origin
                ring = offset[np.abs(offset @ z_axis) < 2.0 / self.to_mm]  # 2 mm either side of the port plane
                if len(ring) < 4:
                    continue
                width = float((ring @ x_axis).max() - (ring @ x_axis).min()) * self.to_mm
                height = float((ring @ y_axis).max() - (ring @ y_axis).min()) * self.to_mm
                if width < 20 or height < 20:
                    continue
                # The fitting's own known ends decide its shape; only a fitting with
                # no known end falls back to the ring's aspect ratio.
                shape_kind = next(iter(known_shapes)) if len(known_shapes) == 1 else (
                    ROUND if abs(width - height) <= 0.05 * max(width, height) else RECT)
                if shape_kind == ROUND:
                    diameter = self.mapping.nominal_diameter(width, height)
                    if diameter is None:
                        continue
                    size = (ROUND, diameter)
                else:
                    a, b = sorted((round(width), round(height)), reverse=True)
                    if a > 4000:
                        continue
                    size = (RECT, float(a), float(b))
                self._set(port_id, size, "geometri")

    def _same_size_ports(self, element) -> list[list[int]]:
        """Groups of this element's ports that must share one cross-section."""
        ports = self.ports(element)
        if not ports:
            return []
        kind = self.mapping.fitting_kind(self.reference(element), self.psets(element), element.is_a())
        ids = [p.id() for p in ports]
        if kind in ("bend", "joint", "cap", "cleanout"):
            return [ids]
        if kind == "product":
            return [ids] if len(ids) == 2 else []
        if kind in ("tee", "cross"):
            main = self.main_run(element)
            return [main] if main else []
        return []

    def main_run(self, element) -> list[int]:
        """The two ports of a tee/cross that face each other (the through run)."""
        axes = self.outward_axes(element)
        best, pair = 0.0, []
        for i in range(len(axes)):
            for j in range(i + 1, len(axes)):
                opposition = -_dot(axes[i][1], axes[j][1])
                if opposition > best:
                    best, pair = opposition, [axes[i][0], axes[j][0]]
        return pair if best > 0.9 else []

    def _propagate_sizes(self) -> None:
        groups = []
        for element_id, ports in self._ports.items():
            element = self.port_owner[ports[0].id()]
            if element.is_a("IfcFlowSegment"):
                continue
            groups.extend(self._same_size_ports(element))
        for _ in range(50):
            changed = False
            for port_id, element in list(self.port_owner.items()):
                size = self.port_size.get(port_id)
                if size is None:
                    continue
                other = self.connected_port(self.file.by_id(port_id))
                if other is not None and self._set(other.id(), size, self.size_source.get(port_id, "graf")):
                    changed = True
            for group in groups:
                known = [p for p in group if p in self.port_size]
                if known:
                    for p in group:
                        if self._set(p, self.port_size[known[0]], self.size_source.get(known[0], "graf")):
                            changed = True
            if not changed:
                break

    def far_end_size(self, element) -> tuple | None:
        """Size of the end opposite a one-port fitting's only port, from its mesh.

        Some reduction families export a single connector; the other end still
        exists in the geometry, at the far extreme along the port's axis.
        """
        ports = self.ports(element)
        if len(ports) != 1:
            return None
        port = ports[0]
        try:
            import ifcopenshell.geom
            import numpy as np
            settings = ifcopenshell.geom.settings()
            settings.set("use-world-coords", True)
            shape = ifcopenshell.geom.create_shape(settings, element)
            matrix = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
        except Exception:
            return None
        verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) / self.to_m
        origin, x_axis, y_axis, z_axis = matrix[:3, 3], matrix[:3, 0], matrix[:3, 1], matrix[:3, 2]
        along = (verts - origin) @ z_axis
        far = along[np.argmax(np.abs(along))]
        ring = verts[np.abs(along - far) < 2.0 / self.to_mm] - origin
        if len(ring) < 4:
            return None
        width = float((ring @ x_axis).max() - (ring @ x_axis).min()) * self.to_mm
        height = float((ring @ y_axis).max() - (ring @ y_axis).min()) * self.to_mm
        known = self.port_size.get(port.id())
        if known and known[0] == RECT:
            a, b = sorted((round(width), round(height)), reverse=True)
            return (RECT, float(a), float(b)) if a <= 4000 else None
        diameter = self.mapping.nominal_diameter(width, height)
        return (ROUND, diameter) if diameter else None

    # -- position ------------------------------------------------------------

    def centre_z(self, element) -> float | None:
        origins = [self.port_origin[p.id()] for p in self.ports(element) if p.id() in self.port_origin]
        if origins:
            return sum(o[2] for o in origins) / len(origins)
        try:
            return float(ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)[2, 3])
        except Exception:
            return None

    def height_above_floor_m(self, element) -> float | None:
        z = self.centre_z(element)
        if z is None or not self.storey_levels:
            return None
        below = [level for level in self.storey_levels if level <= z + 1.0]
        floor = below[-1] if below else self.storey_levels[0]
        return round((z - floor) * self.to_m, 3)

    def is_vertical(self, element) -> bool:
        origins = [self.port_origin[p.id()] for p in self.ports(element) if p.id() in self.port_origin]
        if len(origins) != 2:
            return False
        direction = _unit([b - a for a, b in zip(*origins)])
        return bool(direction) and abs(direction[2]) > 0.9


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def _size_text(size: tuple | None) -> str:
    if not size:
        return "?"
    return f"Ø{size[1]:g}" if size[0] == ROUND else f"{size[1]:g}x{size[2]:g}"


def _dims(size: tuple | None) -> tuple[float | None, float | None]:
    if not size:
        return None, None
    return (size[1], None) if size[0] == ROUND else (size[1], size[2])


class Assigner:
    def __init__(self, model: VentilationModel, mapping, *, handling_source: str, pdel_source: str,
                 include_hangers: bool = False, include_voids: bool = False):
        self.model = model
        self.mapping = mapping
        self.handling_source = handling_source
        self.pdel_source = pdel_source
        # Off by default: the kalkylator prices upphängning and håltagning outside Lubekalk.
        self.include_hangers = include_hangers
        self.include_voids = include_voids

    def assign(self, element) -> Assignment:
        m, mp = self.model, self.mapping
        ps = m.psets(element)
        reference = m.reference(element) or ""
        rule = mp.classify(element, reference, ps)
        if rule.status in (STATUS_OUT_OF_SCOPE, STATUS_INCLUDED):
            return Assignment(status=rule.status, reason=rule.reason, kind=rule.kind, source=rule.source)
        if rule.kind == "void" and not self.include_voids:
            return Assignment(status=STATUS_OUT_OF_SCOPE, kind=rule.kind, source=rule.source,
                              reason="Håltagning tas inte med i kalkylen (include_voids=false)")

        a = Assignment(status=STATUS_READY, kind=rule.kind, source=rule.source, confidence=rule.confidence)
        a.handling = self._text(ps, self.handling_source)
        a.pdel = self._text(ps, self.pdel_source)
        a.height_m = m.height_above_floor_m(element)
        a.montage = mp.montage(ps, a.height_m)
        a.material = mp.material(ps, reference)
        a.insulation = prop(ps, "BIP.InsulationType")
        if a.insulation and not mp.knows_insulation(a.insulation):
            a.source += f"; okänd isoleringstyp {a.insulation!r} (ej i mappningen)"
            a.confidence = "låg"
        ports = m.ports(element)
        sizes = [m.port_size.get(p.id()) for p in ports]

        if rule.kind == "product":
            name = mp.product_designation(element, reference, ps)
            dim = mp.connection_size_from_name(reference or "")
            a.rows.append(Row(kod=mp.PRODUCT_CODE, beteckning=name, dim1=dim, antal=1))
            a.montage = a.montage or mp.MONTAGE_DEFAULT
            return self._finish(a)

        if rule.kind == "void":
            width = _number(prop(ps, "Pset_ProvisionForVoid.Width"))
            height = _number(prop(ps, "Pset_ProvisionForVoid.Height"))
            diameter = _number(prop(ps, "Pset_ProvisionForVoid.Diameter"))
            round_void = bool(diameter) or bool(re.search(r"round|circ|rund", reference, re.IGNORECASE))
            if round_void and (diameter or width):
                a.rows.append(Row(kod=mp.VOID_CODES[ROUND], dim1=round((diameter or width) * m.to_mm, 1), antal=1))
            elif width and height:
                d1, d2 = sorted((round(width * m.to_mm, 1), round(height * m.to_mm, 1)), reverse=True)
                a.rows.append(Row(kod=mp.VOID_CODES[RECT], dim1=d1, dim2=d2, antal=1))
            else:
                return self._missing(a, "Håltagningen saknar mått i Pset_ProvisionForVoid")
            return self._finish(a)

        if rule.kind == "duct":
            size = m.segment_size(element) or next((s for s in sizes if s), None)
            length_mm = (_number(prop(ps, "BIP.Length"))
                         or _number(prop(ps, "Pset_DuctSegmentTypeCommon.Length"))
                         or _number(prop(ps, "Pset_FlowSegmentDuctSegment.Length")))
            if size is None:
                return self._missing(a, "Dimension saknas på kanalen (varken BIP.Diameter eller profil)")
            if not length_mm:
                return self._missing(a, "Längd saknas på kanalen")
            kod = mp.DUCT_CODE[size[0]]
            d1, d2 = _dims(size)
            length_m = round(length_mm * m.to_m, 3)
            a.rows.append(Row(kod=kod, dim1=d1, dim2=d2, langd=length_m))
            fire = mp.insulation_fire_class(a.insulation)
            a.insulation_code = mp.insulation_code(a.insulation, size[0])
            if a.insulation_code:
                a.rows.append(Row(kod=a.insulation_code, dim1=d1, dim2=d2, langd=length_m, role="isolering"))
            if self.include_hangers and not m.is_vertical(element) and a.montage not in mp.NO_HANGER_MONTAGE:
                a.hanger_code = mp.hanger_code(size[0], fire)
                a.rows.append(Row(kod=a.hanger_code, dim1=d1, dim2=d2, langd=length_m, role="upphängning"))
            return self._finish(a)

        # Fittings: the size comes from the port graph.
        known = [s for s in sizes if s]
        if rule.kind == "auto":
            rule.kind = self._infer_kind(element, known)
            a.kind = rule.kind
            if rule.kind is None:
                return self._missing(a, f"Okänd fittingtyp {reference!r} med {len(ports)} portar")
        if not known:
            return self._missing(a, "Ingen anslutande kanal ger fittingens dimension")
        shapes = {s[0] for s in known}

        if rule.kind == "bend":
            angle = self.bend_angle(element)
            if angle is None:
                return self._missing(a, "Böjvinkel kan inte bestämmas från portarna")
            size = known[0]
            kod = mp.bend_code(size[0], angle)
            d1, d2 = _dims(size)
            a.rows.append(Row(kod=kod, dim1=d1, dim2=d2, antal=1))
            a.source += f"; vinkel {angle:.0f}°"
        elif rule.kind in ("tee", "cross", "tap"):
            if len(shapes) > 1:
                # Mixed round/rect (e.g. a rectangular take-off from a circular duct):
                # the code family says which side is the run. C* codes are sized by
                # the circular duct, R* codes by the rectangular one.
                code_hint = rule.code or ""
                run_shape = ROUND if code_hint.startswith("C") or not code_hint else RECT
                run = max((s for s in known if s[0] == run_shape), key=lambda s: s[1])
                branch_size = max((s for s in known if s[0] != run_shape), key=lambda s: s[1])
            else:
                main = m.main_run(element)
                main_sizes = [m.port_size.get(p) for p in main if m.port_size.get(p)]
                branch = [m.port_size.get(p.id()) for p in ports
                          if p.id() not in main and m.port_size.get(p.id())]
                run = max(main_sizes or known, key=lambda s: s[1])
                branch_size = max(branch, key=lambda s: s[1]) if branch else None
            kod = rule.code
            if kod and (kod.startswith("C") != (run[0] == ROUND)):
                # The family name says one shape, the connected ducts another: trust the ducts.
                a.source += f"; familjens kod {kod} stämmer inte med anslutningarna ({run[0]})"
                a.confidence = "låg"
                kod = None
            kod = kod or mp.branch_code(rule.kind, run, branch_size)
            if kod is None:
                return self._missing(a, f"Ingen kod för {rule.kind} {run[0]}→{branch_size[0] if branch_size else '?'}")
            d1, _ = _dims(run)
            d2 = _dims(branch_size)[0] if branch_size else None
            a.rows.append(Row(kod=kod, dim1=d1, dim2=d2, antal=1,
                              beteckning=_size_text(branch_size) if branch_size and branch_size[0] != run[0] else ""))
        elif rule.kind == "reduction":
            if len(known) < 2 and len(ports) == 1:
                far = m.far_end_size(element)
                if far:
                    known = known + [far]
                    a.source += "; bortre änden mätt i geometrin (en port)"
            if len(known) < 2:
                return self._missing(a, "Bara ena sidan av dimensionsändringen har känd dimension")
            big, small = sorted(known[:2], key=lambda s: (s[1], s[2] if len(s) > 2 else 0), reverse=True)
            if big == small:
                # A dimension change with equal ends: one end was read from geometry
                # (a spigot can look like the next size down). Keep it, flag it.
                a.confidence = "låg"
                a.source += "; lika dimension på båda sidor — kontrollera"
            on_fitting = any(
                (n := m.neighbour(p)) is not None and not n.is_a("IfcFlowSegment") for p in ports)
            kod = mp.reduction_code(big, small, on_fitting)
            if big[0] == RECT and small[0] == RECT:
                # Dim 1 x Dim 2 is the big end; the Beteckning names the other (Lubekalk: <= 15 chars).
                a.rows.append(Row(kod=kod, dim1=big[1], dim2=big[2], antal=1,
                                  beteckning=f"→{small[1]:g}x{small[2]:g}"))
            else:
                a.rows.append(Row(kod=kod, dim1=_dims(big)[0], dim2=_dims(small)[0], antal=1))
        elif rule.kind == "transition":
            rect = next((s for s in known if s[0] == RECT), None)
            circ = next((s for s in known if s[0] == ROUND), None)
            if rect is None:
                return self._missing(a, "Rektangulär sida av övergången saknar dimension")
            a.rows.append(Row(kod=rule.code or mp.TRANSITION_CODE, dim1=rect[1], dim2=rect[2], antal=1,
                              beteckning=_size_text(circ) if circ else ""))
        elif rule.kind in ("cap", "joint", "cleanout"):
            size = known[0]
            on_fitting = any(
                (n := m.neighbour(p)) is not None and not n.is_a("IfcFlowSegment") for p in ports)
            kod = rule.code or mp.simple_code(rule.kind, size[0], on_fitting, a.insulation)
            d1, d2 = _dims(size)
            a.rows.append(Row(kod=kod, dim1=d1, dim2=d2, antal=1))
        else:
            return self._missing(a, f"Okänd fittingtyp {reference!r}")

        primary = a.primary
        if (a.insulation and self.mapping.DETAIL_INSULATION_CODE
                and self.mapping.insulation_code(a.insulation, RECT if primary.dim2 else ROUND)):
            a.rows.append(Row(kod=self.mapping.DETAIL_INSULATION_CODE, dim1=primary.dim1, dim2=primary.dim2,
                              antal=1, role="detaljisolering"))
        return self._finish(a)

    def _infer_kind(self, element, known: list) -> str | None:
        count = len(self.model.ports(element))
        if count == 1:
            return "cap"
        if count == 3:
            return "tee"
        if count == 4:
            return "cross"
        if count == 2:
            angle = self.bend_angle(element)
            if angle is not None and angle > 10:
                return "bend"
            if len({s for s in known}) > 1:
                return "reduction" if len({s[0] for s in known}) == 1 else "transition"
            return "joint"
        return None

    def bend_angle(self, element) -> float | None:
        axes = self.model.outward_axes(element)
        if len(axes) != 2:
            return None
        return round(180.0 - _angle_deg(axes[0][1], axes[1][1]), 1)

    def _text(self, ps: dict, path: str) -> str | None:
        if not path:
            return None
        value = prop(ps, path)
        return str(value) if value is not None else None

    def _missing(self, a: Assignment, reason: str) -> Assignment:
        a.status, a.reason = STATUS_MISSING, reason
        a.rows = []
        return a

    def _finish(self, a: Assignment) -> Assignment:
        if a.montage is None:
            return self._missing(a, "Montagehöjd kan inte bestämmas")
        primary = a.primary
        if primary is None or not primary.kod:
            return self._missing(a, "Ingen Lubekalk-kod")
        return a


# ---------------------------------------------------------------------------
# Pset round trip and export rows
# ---------------------------------------------------------------------------


def assignment_properties(a: Assignment) -> dict[str, Any]:
    """The flat ``Lubekalk`` pset written onto the element."""
    props: dict[str, Any] = {"Status": a.status, "Orsak": a.reason or "", "Källa": a.source or "",
                             "Säkerhet": a.confidence}
    primary = a.primary
    if primary is not None:
        props.update({
            "Kod": primary.kod, "Beteckning": primary.beteckning or "",
            "Dim 1": primary.dim1 if primary.dim1 is not None else 0,
            "Dim 2": primary.dim2 if primary.dim2 is not None else 0,
            "Montageläge": a.montage or "", "Kanalmaterial": a.material or "",
            "Antal": int(primary.antal), "Längd": float(primary.langd),
            "Handling": a.handling or "", "P-del": a.pdel or "",
        })
    if a.height_m is not None:
        props["Montagehöjd"] = float(a.height_m)
    if a.insulation:
        props["Isolering"] = a.insulation
    for row in a.rows:
        if row.role == "isolering":
            props["IsoleringKod"] = row.kod
        elif row.role == "upphängning":
            props["UpphängningKod"] = row.kod
        elif row.role == "detaljisolering":
            props["DetaljisoleringKod"] = row.kod
    return props


def rows_from_pset(pset: dict) -> list[dict]:
    """Rebuild import rows (primary + secondary) from a written ``Lubekalk`` pset."""
    if (pset or {}).get("Status") != STATUS_READY or not pset.get("Kod"):
        return []
    base = {"Rev": "", "Handling": pset.get("Handling") or "", "P-del": pset.get("P-del") or "",
            "Montageläge": pset.get("Montageläge") or "", "Kanalmaterial": pset.get("Kanalmaterial") or "",
            "Höjd": 0}
    # Whole millimetres even from a model patched before dimensions were rounded.
    dim1, dim2 = whole_mm(pset.get("Dim 1") or 0), whole_mm(pset.get("Dim 2") or 0)
    rows = [{**base, "Kod": pset["Kod"], "Beteckning": pset.get("Beteckning") or "", "Dim 1": dim1,
             "Dim 2": dim2, "Antal": pset.get("Antal") or 0, "Längd": pset.get("Längd") or 0}]
    length = pset.get("Längd") or 0
    if pset.get("IsoleringKod"):
        rows.append({**base, "Kod": pset["IsoleringKod"], "Beteckning": pset.get("Isolering") or "",
                     "Dim 1": dim1, "Dim 2": dim2, "Antal": 0, "Längd": length})
    if pset.get("UpphängningKod"):
        rows.append({**base, "Kod": pset["UpphängningKod"], "Beteckning": "", "Dim 1": dim1, "Dim 2": dim2,
                     "Antal": 0, "Längd": length})
    if pset.get("DetaljisoleringKod"):
        rows.append({**base, "Kod": pset["DetaljisoleringKod"], "Beteckning": pset.get("Isolering") or "",
                     "Dim 1": dim1, "Dim 2": dim2, "Antal": 1, "Längd": 0})
    return rows


def aggregate(rows: Iterable[dict]) -> list[dict]:
    """Sum Antal and Längd over identical Lubekalk keys, as the import expects."""
    key_cols = ("Kod", "Rev", "Handling", "P-del", "Beteckning", "Dim 1", "Dim 2", "Montageläge",
                "Kanalmaterial", "Höjd")
    totals: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(col) for col in key_cols)
        slot = totals.setdefault(key, {**{col: row.get(col) for col in key_cols}, "Antal": 0, "Längd": 0.0})
        slot["Antal"] += int(row.get("Antal") or 0)
        slot["Längd"] = round(slot["Längd"] + float(row.get("Längd") or 0), 3)
    return [{col: slot[col] for col in COLUMNS} for _, slot in sorted(
        totals.items(), key=lambda kv: tuple(str(v) for v in kv[0]))]


def readiness(assignments: dict[str, Assignment], classes: dict[str, str]) -> dict:
    by_status = Counter(a.status for a in assignments.values())
    by_class = defaultdict(Counter)
    reasons = Counter()
    kinds = Counter()
    for gid, a in assignments.items():
        by_class[classes[gid]][a.status] += 1
        if a.status == STATUS_MISSING:
            reasons[a.reason] += 1
        kinds[(a.kind, a.primary.kod if a.primary else None)] += 1
    total = len(assignments)
    resolved = by_status[STATUS_READY] + by_status[STATUS_INCLUDED] + by_status[STATUS_OUT_OF_SCOPE]
    return {
        "elements": total,
        "by_status": dict(by_status),
        "resolved_share": round(resolved / total, 4) if total else None,
        "by_class": {k: dict(v) for k, v in sorted(by_class.items())},
        "missing_reasons": dict(reasons.most_common()),
        "codes": {f"{kind}:{kod}": n for (kind, kod), n in kinds.most_common()},
    }
