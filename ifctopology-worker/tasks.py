import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.selector
from shared import object_storage as s3
from shared.classes import IfcTopologyRequest, TopologyEngine, TopologySampleStrategy

try:
    import ifcopenshell.util.placement
except Exception:  # pragma: no cover - depends on IfcOpenShell wheel contents
    ifcopenshell.util.placement = None


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifctopology-worker"


Point = Tuple[float, float, float]


@dataclass
class BBox:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def centroid(self) -> Point:
        return (
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
            (self.min_z + self.max_z) / 2.0,
        )

    @property
    def volume(self) -> float:
        return (
            max(0.0, self.max_x - self.min_x)
            * max(0.0, self.max_y - self.min_y)
            * max(0.0, self.max_z - self.min_z)
        )

    def contains_point(self, point: Point, tolerance: float) -> bool:
        x, y, z = point
        return (
            self.min_x - tolerance <= x <= self.max_x + tolerance
            and self.min_y - tolerance <= y <= self.max_y + tolerance
            and self.min_z - tolerance <= z <= self.max_z + tolerance
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min": [self.min_x, self.min_y, self.min_z],
            "max": [self.max_x, self.max_y, self.max_z],
            "centroid": list(self.centroid),
            "volume": self.volume,
        }


@dataclass
class SpaceCandidate:
    source_file: str
    global_id: str
    name: Optional[str]
    long_name: Optional[str]
    storey: Optional[str]
    zones: List[Dict[str, Optional[str]]]
    bbox: BBox
    topology_cell: Any = None


@dataclass
class ElementCandidate:
    source_file: str
    element: Any
    global_id: str
    ifc_class: str
    name: Optional[str]
    sample_point: Point
    bbox: Optional[BBox]


def _current_job_id() -> Optional[str]:
    try:
        from rq import get_current_job

        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _topologicpy_status() -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        import topologicpy  # type: ignore

        return {
            "available": True,
            "version": getattr(topologicpy, "__version__", "unknown"),
            "import_seconds": round(time.perf_counter() - start, 6),
        }
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "import_seconds": round(time.perf_counter() - start, 6),
        }


def _selected_engine(request: IfcTopologyRequest, status: Dict[str, Any]) -> str:
    if request.engine == TopologyEngine.BBOX:
        return TopologyEngine.BBOX.value
    if request.engine == TopologyEngine.TOPOLOGICPY:
        if not status["available"]:
            raise RuntimeError(f"TopologicPy engine requested but unavailable: {status.get('error')}")
        # First pass keeps the containment implementation comparable with the
        # bbox baseline while measuring TopologicPy import/runtime overhead.
        return TopologyEngine.TOPOLOGICPY.value
    return TopologyEngine.TOPOLOGICPY.value if status["available"] else TopologyEngine.BBOX.value


def _geometry_settings():
    settings = ifcopenshell.geom.settings()
    try:
        settings.set(settings.USE_WORLD_COORDS, True)
    except Exception:
        try:
            settings.set("USE_WORLD_COORDS", True)
        except Exception:
            logger.warning("IfcOpenShell wheel does not support USE_WORLD_COORDS setting")
    return settings


def _bbox_from_shape(model: Any, product: Any, settings: Any) -> Optional[BBox]:
    try:
        shape = ifcopenshell.geom.create_shape(settings, product)
        verts = list(shape.geometry.verts)
    except Exception as exc:
        logger.debug("Could not create shape for %s: %s", getattr(product, "GlobalId", None), exc)
        return None

    if len(verts) < 3:
        return None

    xs = verts[0::3]
    ys = verts[1::3]
    zs = verts[2::3]
    return BBox(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _placement_point(product: Any) -> Optional[Point]:
    placement_module = getattr(ifcopenshell.util, "placement", None)
    if placement_module is None or not getattr(product, "ObjectPlacement", None):
        return None
    try:
        matrix = placement_module.get_local_placement(product.ObjectPlacement)
        return (float(matrix[0][3]), float(matrix[1][3]), float(matrix[2][3]))
    except Exception:
        return None


def _sample_point(
    product: Any,
    bbox: Optional[BBox],
    sample_strategy: TopologySampleStrategy,
) -> Optional[Point]:
    if sample_strategy == TopologySampleStrategy.PLACEMENT:
        return _placement_point(product) or (bbox.centroid if bbox else None)
    return bbox.centroid if bbox else _placement_point(product)


def _filter_elements(model: Any, query: str) -> List[Any]:
    try:
        return list(ifcopenshell.util.selector.filter_elements(model, query))
    except Exception:
        logger.debug("Selector query failed; falling back to by_type(%s)", query, exc_info=True)
        return list(model.by_type(query))


def _space_storey(space: Any) -> Optional[str]:
    for rel in getattr(space, "ContainedInStructure", []) or []:
        structure = getattr(rel, "RelatingStructure", None)
        if structure:
            return getattr(structure, "Name", None)
    return None


def _zone_assignments(model: Any) -> Dict[str, List[Dict[str, Optional[str]]]]:
    assignments: Dict[str, List[Dict[str, Optional[str]]]] = {}
    for rel in model.by_type("IfcRelAssignsToGroup"):
        group = getattr(rel, "RelatingGroup", None)
        if not group or not group.is_a("IfcZone"):
            continue
        zone = {
            "global_id": getattr(group, "GlobalId", None),
            "name": getattr(group, "Name", None),
            "long_name": getattr(group, "LongName", None),
        }
        for obj in getattr(rel, "RelatedObjects", []) or []:
            guid = getattr(obj, "GlobalId", None)
            if guid:
                assignments.setdefault(guid, []).append(zone)
    return assignments


def _collect_spaces(
    model: Any,
    source_file: str,
    query: str,
    include_zones: bool,
    settings: Any,
) -> Tuple[List[SpaceCandidate], int]:
    zones = _zone_assignments(model) if include_zones else {}
    spaces: List[SpaceCandidate] = []
    geometry_failures = 0

    for space in _filter_elements(model, query):
        bbox = _bbox_from_shape(model, space, settings)
        if bbox is None:
            geometry_failures += 1
            continue
        spaces.append(
            SpaceCandidate(
                source_file=source_file,
                global_id=getattr(space, "GlobalId", ""),
                name=getattr(space, "Name", None),
                long_name=getattr(space, "LongName", None),
                storey=_space_storey(space),
                zones=zones.get(getattr(space, "GlobalId", ""), []),
                bbox=bbox,
            )
        )

    spaces.sort(key=lambda item: item.bbox.volume)
    return spaces, geometry_failures


def _collect_elements(
    model: Any,
    source_file: str,
    query: str,
    settings: Any,
    sample_strategy: TopologySampleStrategy,
    max_elements: Optional[int],
) -> Tuple[List[ElementCandidate], int]:
    elements: List[ElementCandidate] = []
    geometry_failures = 0

    for product in _filter_elements(model, query):
        if max_elements is not None and len(elements) >= max_elements:
            break
        bbox = _bbox_from_shape(model, product, settings)
        point = _sample_point(product, bbox, sample_strategy)
        if point is None:
            geometry_failures += 1
            continue
        elements.append(
            ElementCandidate(
                source_file=source_file,
                element=product,
                global_id=getattr(product, "GlobalId", ""),
                ifc_class=product.is_a(),
                name=getattr(product, "Name", None),
                sample_point=point,
                bbox=bbox,
            )
        )

    return elements, geometry_failures


def _build_topologic_bbox_cells(spaces: Iterable[SpaceCandidate], tolerance: float) -> int:
    from topologicpy.Cell import Cell
    from topologicpy.Vertex import Vertex

    built = 0
    for space in spaces:
        bbox = space.bbox
        width = max(tolerance, bbox.max_x - bbox.min_x)
        length = max(tolerance, bbox.max_y - bbox.min_y)
        height = max(tolerance, bbox.max_z - bbox.min_z)
        origin = Vertex.ByCoordinates(*bbox.centroid)
        space.topology_cell = Cell.Prism(
            origin=origin,
            width=width,
            length=length,
            height=height,
            placement="center",
            tolerance=max(tolerance, 0.0001),
        )
        built += 1
    return built


def _topologic_contains(space: SpaceCandidate, point: Point, tolerance: float) -> bool:
    from topologicpy.Cell import Cell
    from topologicpy.Vertex import Vertex

    if space.topology_cell is None:
        return space.bbox.contains_point(point, tolerance)
    vertex = Vertex.ByCoordinates(*point)
    status = Cell.ContainmentStatus(space.topology_cell, vertex, tolerance=max(tolerance, 0.0001))
    return status in (0, 1)


def _match_element_to_spaces(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
) -> List[SpaceCandidate]:
    if selected_engine == TopologyEngine.TOPOLOGICPY.value:
        return [space for space in spaces if _topologic_contains(space, element.sample_point, tolerance)]
    return [space for space in spaces if space.bbox.contains_point(element.sample_point, tolerance)]


def _space_payload(space: Optional[SpaceCandidate]) -> Optional[Dict[str, Any]]:
    if space is None:
        return None
    return {
        "source_file": space.source_file,
        "global_id": space.global_id,
        "name": space.name,
        "long_name": space.long_name,
        "storey": space.storey,
        "zones": space.zones,
        "bbox": space.bbox.to_dict(),
    }


def _element_payload(element: ElementCandidate, matches: List[SpaceCandidate]) -> Dict[str, Any]:
    chosen = matches[0] if matches else None
    return {
        "source_file": element.source_file,
        "global_id": element.global_id,
        "ifc_class": element.ifc_class,
        "name": element.name,
        "sample_point": list(element.sample_point),
        "bbox": element.bbox.to_dict() if element.bbox else None,
        "matched_space": _space_payload(chosen),
        "match_count": len(matches),
    }


def _get_or_create_pset(model: Any, element: Any, pset_name: str) -> Any:
    for rel in getattr(element, "IsDefinedBy", []) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = getattr(rel, "RelatingPropertyDefinition", None)
        if pset and pset.is_a("IfcPropertySet") and getattr(pset, "Name", None) == pset_name:
            return pset

    import ifcopenshell.api

    return ifcopenshell.api.run("pset.add_pset", model, product=element, name=pset_name)


def _stamp_element(model: Any, element: Any, space: SpaceCandidate, pset_name: str) -> None:
    import ifcopenshell.api

    zone_names = [zone.get("name") for zone in space.zones if zone.get("name")]
    zone_ids = [zone.get("global_id") for zone in space.zones if zone.get("global_id")]
    properties = {
        "RoomGlobalId": space.global_id,
        "RoomName": space.name or "",
        "RoomLongName": space.long_name or "",
        "BuildingStoreyName": space.storey or "",
        "ZoneNames": ", ".join(zone_names),
        "ZoneGlobalIds": ", ".join(zone_ids),
        "StampedBy": "ifctopology-worker",
    }
    pset = _get_or_create_pset(model, element, pset_name)
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=properties)


def _stage_inputs(files: List[str], tmpdir: str) -> Tuple[Dict[str, str], List[str]]:
    paths: Dict[str, str] = {}
    keys: List[str] = []
    for index, filename in enumerate(files):
        if s3.is_enabled():
            key = s3.normalize_input_key(filename)
            keys.append(key)
            local_name = os.path.basename(key) or "input.ifc"
            local_path = os.path.join(tmpdir, f"{index}-{local_name}")
            s3.get_client().download_file(Bucket=s3.bucket_name(), Key=key, Filename=local_path)
        else:
            normalized = filename.lstrip("/")
            if normalized.startswith("uploads/"):
                normalized = normalized[len("uploads/"):]
            local_path = os.path.join("/uploads", normalized)
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"Input IFC file not found: {filename}")
        paths[filename] = local_path
    return paths, keys


def _default_stamped_name(source_name: str, index: int) -> str:
    base, ext = os.path.splitext(os.path.basename(source_name))
    return f"{base or 'model'}_roomstamped_{index}{ext or '.ifc'}"


def _output_ifc_path(request: IfcTopologyRequest, output_dir: str, source_name: str, index: int) -> str:
    if not request.output_ifc_prefix:
        return os.path.join(output_dir, _default_stamped_name(source_name, index))

    prefix = request.output_ifc_prefix.strip("/")
    if not prefix:
        return os.path.join(output_dir, _default_stamped_name(source_name, index))
    if prefix.lower().endswith(".ifc") and len(request.element_files) == 1:
        return os.path.join(output_dir, os.path.basename(prefix))
    return os.path.join(output_dir, prefix, _default_stamped_name(source_name, index))


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def run_roomstamp_benchmark(job_data: dict) -> dict:
    """Benchmark room/zone containment and optionally stamp target IFC models."""
    request = IfcTopologyRequest(**job_data)
    start = time.perf_counter()
    topologicpy = _topologicpy_status()
    selected_engine = _selected_engine(request, topologicpy)
    tmpdir = tempfile.mkdtemp(prefix="ifctopology-")

    try:
        all_inputs = list(dict.fromkeys(request.spatial_files + request.element_files))
        local_paths, input_keys = _stage_inputs(all_inputs, tmpdir)
        output_dir = os.path.join(tmpdir, "output") if s3.is_enabled() else "/output/topology"
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, os.path.basename(request.output_file))
        settings = _geometry_settings()

        load_start = time.perf_counter()
        spatial_models = {
            filename: ifcopenshell.open(local_paths[filename])
            for filename in request.spatial_files
        }
        element_models = {
            filename: ifcopenshell.open(local_paths[filename])
            for filename in request.element_files
        }
        load_seconds = time.perf_counter() - load_start

        space_start = time.perf_counter()
        spaces: List[SpaceCandidate] = []
        space_geometry_failures = 0
        for filename, model in spatial_models.items():
            collected, failures = _collect_spaces(
                model,
                filename,
                request.space_query,
                request.include_zones,
                settings,
            )
            spaces.extend(collected)
            space_geometry_failures += failures
        spaces.sort(key=lambda item: item.bbox.volume)
        space_seconds = time.perf_counter() - space_start

        topologic_cell_count = 0
        topologic_cell_seconds = 0.0
        if selected_engine == TopologyEngine.TOPOLOGICPY.value:
            topologic_cell_start = time.perf_counter()
            topologic_cell_count = _build_topologic_bbox_cells(spaces, request.tolerance)
            topologic_cell_seconds = time.perf_counter() - topologic_cell_start

        element_start = time.perf_counter()
        elements_by_file: Dict[str, List[ElementCandidate]] = {}
        element_geometry_failures = 0
        for filename, model in element_models.items():
            collected, failures = _collect_elements(
                model,
                filename,
                request.element_query,
                settings,
                request.sample_strategy,
                request.max_elements,
            )
            elements_by_file[filename] = collected
            element_geometry_failures += failures
        element_seconds = time.perf_counter() - element_start

        match_start = time.perf_counter()
        matches_by_file: Dict[str, List[Tuple[ElementCandidate, List[SpaceCandidate]]]] = {}
        element_results: List[Dict[str, Any]] = []
        matched_count = 0
        ambiguous_count = 0
        total_elements = 0
        candidate_tests = 0

        for filename, elements in elements_by_file.items():
            matches_by_file[filename] = []
            for element in elements:
                matches = _match_element_to_spaces(
                    element,
                    spaces,
                    request.tolerance,
                    selected_engine,
                )
                candidate_tests += len(spaces)
                total_elements += 1
                if matches:
                    matched_count += 1
                if len(matches) > 1:
                    ambiguous_count += 1
                matches_by_file[filename].append((element, matches))
                element_results.append(_element_payload(element, matches))
        match_seconds = time.perf_counter() - match_start

        stamped_outputs: List[Dict[str, Any]] = []
        if request.stamp:
            stamp_start = time.perf_counter()
            for index, filename in enumerate(request.element_files, start=1):
                model = element_models[filename]
                stamped = 0
                for element, matches in matches_by_file[filename]:
                    if matches:
                        _stamp_element(model, element.element, matches[0], request.pset_name)
                        stamped += 1

                out_path = _output_ifc_path(request, output_dir, filename, index)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                model.write(out_path)
                stamped_outputs.append({
                    "source_file": filename,
                    "stamped_element_count": stamped,
                    "output_path": out_path,
                })
            stamp_seconds = time.perf_counter() - stamp_start
        else:
            stamp_seconds = 0.0

        elapsed_seconds = time.perf_counter() - start
        summary = {
            "engine_requested": request.engine.value,
            "engine_selected": selected_engine,
            "containment_method": (
                "topologicpy_bbox_cell"
                if selected_engine == TopologyEngine.TOPOLOGICPY.value
                else "bbox"
            ),
            "topologicpy_used_for_containment": selected_engine == TopologyEngine.TOPOLOGICPY.value,
            "topologic_cell_count": topologic_cell_count,
            "sample_strategy": request.sample_strategy.value,
            "spatial_file_count": len(request.spatial_files),
            "element_file_count": len(request.element_files),
            "space_count": len(spaces),
            "element_count": total_elements,
            "matched_count": matched_count,
            "unmatched_count": total_elements - matched_count,
            "ambiguous_match_count": ambiguous_count,
            "candidate_tests": candidate_tests,
            "matches_per_second": round(total_elements / match_seconds, 2) if match_seconds else total_elements,
            "stamp": request.stamp,
            "space_geometry_failures": space_geometry_failures,
            "element_geometry_failures": element_geometry_failures,
        }
        benchmark = {
            "load_seconds": round(load_seconds, 6),
            "space_index_seconds": round(space_seconds, 6),
            "topologic_cell_seconds": round(topologic_cell_seconds, 6),
            "element_geometry_seconds": round(element_seconds, 6),
            "match_seconds": round(match_seconds, 6),
            "stamp_seconds": round(stamp_seconds, 6),
            "total_seconds": round(elapsed_seconds, 6),
        }
        report = {
            "success": True,
            "message": "Topology roomstamp benchmark completed",
            "summary": summary,
            "benchmark": benchmark,
            "topologicpy": topologicpy,
            "request": request.model_dump(mode="json"),
            "spaces": [_space_payload(space) for space in spaces],
            "elements": element_results,
            "stamped_outputs": stamped_outputs,
        }
        _write_json(report_path, report)

        result = {
            "success": True,
            "message": "Topology roomstamp benchmark completed",
            "summary": summary,
            "benchmark": benchmark,
            "report_path": report_path,
            "stamped_outputs": stamped_outputs,
            "storage": "filesystem",
        }

        if s3.is_enabled():
            report_key = s3.normalize_output_key(request.output_file, "topology")
            audit = s3.upload_and_audit(
                report_path,
                key=report_key,
                operation="ifctopology_roomstamp",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", key) for key in input_keys],
                metadata=summary,
                content_type="application/json",
            )
            result.update({
                "storage": "s3",
                "bucket": s3.bucket_name(),
                "output_key": report_key,
                "report_path": f"s3://{s3.bucket_name()}/{report_key}",
                "sha256": audit["sha256"],
                "size_bytes": audit["size_bytes"],
                "audit_id": audit["audit_id"],
            })

            uploaded_ifcs = []
            for item in stamped_outputs:
                key = s3.normalize_output_key(
                    os.path.basename(item["output_path"]),
                    "topology",
                )
                audit_ifc = s3.upload_and_audit(
                    item["output_path"],
                    key=key,
                    operation="ifctopology_roomstamp_ifc",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", s3.normalize_input_key(item["source_file"]))],
                    metadata={
                        "stamped_element_count": item["stamped_element_count"],
                        "pset_name": request.pset_name,
                    },
                    content_type="application/x-step",
                )
                uploaded_ifcs.append({
                    **item,
                    "output_key": key,
                    "output_path": f"s3://{s3.bucket_name()}/{key}",
                    "sha256": audit_ifc["sha256"],
                    "size_bytes": audit_ifc["size_bytes"],
                    "audit_id": audit_ifc["audit_id"],
                })
            result["stamped_outputs"] = uploaded_ifcs

        return result
    except Exception:
        logger.exception("Error during topology roomstamp benchmark")
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
