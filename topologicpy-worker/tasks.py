import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from multiprocessing import get_context
from queue import Empty
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import numpy as np
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.selector
from shared import audit_db, object_storage as s3
from shared.classes import TopologicpyRequest, TopologyEngine, TopologySampleStrategy

import space_cache

try:
    import ifcopenshell.util.placement
except Exception:  # pragma: no cover - depends on IfcOpenShell wheel contents
    ifcopenshell.util.placement = None


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "topologicpy-worker"
UPLOADS_DIR = os.environ.get("IFCPIPELINE_UPLOADS_DIR", "/uploads")
OUTPUT_DIR = os.environ.get("IFCPIPELINE_OUTPUT_DIR", "/output")
EXAMPLES_DIR = os.environ.get("IFCPIPELINE_EXAMPLES_DIR", "/examples")

# Production tuning (env overrides). Default: fast bbox-prism cells, bbox distance resolution.
_CELL_MODE = os.environ.get("IFCTOPOLOGY_CELL_MODE", "prism").strip().lower()  # prism | mesh
_DISTANCE_MODE = os.environ.get("IFCTOPOLOGY_DISTANCE_MODE", "bbox").strip().lower()  # bbox | topologic
_MAX_PROXIMATE_SPACES = max(1, int(os.environ.get("IFCTOPOLOGY_MAX_PROXIMATE_SPACES", "32")))
_PROXIMITY_THRESHOLD_M = float(os.environ.get("IFCTOPOLOGY_PROXIMITY_THRESHOLD_M", "10.0"))
_PROGRESS_LOG_EVERY = max(1, int(os.environ.get("IFCTOPOLOGY_PROGRESS_LOG_EVERY", "250")))
# When bbox sampling places the point more than this far above min_z, snap Z to min_z + offset
# so columns / vertical MEP map to the lowest storey zone, not mid-height.
_VERTICAL_BIAS_OFFSET_M = float(os.environ.get("IFCTOPOLOGY_VERTICAL_BIAS_OFFSET_M", "1.2"))
_OVERLAP_RESOLUTION = os.environ.get("IFCTOPOLOGY_OVERLAP_RESOLUTION", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
_OVERLAP_SAMPLES = max(4, min(128, int(os.environ.get("IFCTOPOLOGY_OVERLAP_SAMPLES", "24"))))
_OVERLAP_CONFIDENCE_MARGIN = float(os.environ.get("IFCTOPOLOGY_OVERLAP_CONFIDENCE_MARGIN", "0.55"))
_OVERLAP_COVERAGE_MIN = float(os.environ.get("IFCTOPOLOGY_OVERLAP_COVERAGE_MIN", "0.35"))
_HYBRID_GEOMETRIC_FALLBACK = os.environ.get("IFCTOPOLOGY_HYBRID_GEOMETRIC_FALLBACK", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)


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
    verts: Optional[List[float]] = None
    faces: Optional[List[int]] = None
    cell_kind: Optional[str] = None


@dataclass
class ElementCandidate:
    source_file: str
    element: Any
    global_id: str
    ifc_class: str
    name: Optional[str]
    sample_point: Point
    bbox: Optional[BBox]
    sample_points: Optional[List[Point]] = None
    verts: Optional[List[float]] = None
    faces: Optional[List[int]] = None


@dataclass
class MatchResolution:
    """Result of matching one element to a single dominant room."""

    space: Optional[SpaceCandidate]
    method: str  # overlap_majority | overlap_geometric | proximity | legacy | unmatched
    status: str  # Contained | Resolved | Proximity | Unmatched
    confidence: float
    candidate_count: int = 0
    raw_matches: Optional[List[SpaceCandidate]] = None


class SpaceIndex:
    def __init__(self, spaces: List[SpaceCandidate], grid_size: float = 10.0):
        """
        Builds a spatial grid index over spaces.
        grid_size: size of each grid cell in the XY plane.
        """
        self.spaces = spaces
        self.grid_size = grid_size
        self.grid: Dict[Tuple[int, int], List[SpaceCandidate]] = {}

        for space in spaces:
            bbox = space.bbox
            min_gx = int(np.floor(bbox.min_x / grid_size))
            max_gx = int(np.floor(bbox.max_x / grid_size))
            min_gy = int(np.floor(bbox.min_y / grid_size))
            max_gy = int(np.floor(bbox.max_y / grid_size))

            for gx in range(min_gx, max_gx + 1):
                for gy in range(min_gy, max_gy + 1):
                    self._append_to_cell((gx, gy), space)

    @staticmethod
    def _normalize_cell_spaces(cell_spaces: Any) -> Optional[List[SpaceCandidate]]:
        """Coerce a grid bucket to a list (guards native-heap type confusion)."""
        if cell_spaces is None:
            return None
        if isinstance(cell_spaces, SpaceCandidate):
            return [cell_spaces]
        if isinstance(cell_spaces, list):
            return cell_spaces
        return None

    def _append_to_cell(self, cell_key: Tuple[int, int], space: SpaceCandidate) -> None:
        bucket = self._normalize_cell_spaces(self.grid.get(cell_key))
        if bucket is None:
            bucket = []
        bucket.append(space)
        self.grid[cell_key] = bucket

    def query(self, point: Point, tolerance: float = 0.0) -> List[SpaceCandidate]:
        """
        Returns space candidates that overlap the query point.
        """
        coerced = _coerce_point(point)
        if coerced is None:
            logger.warning(
                "SpaceIndex.query skipped invalid point type=%s value=%r",
                type(point).__name__,
                point,
            )
            return []
        x, y, z = coerced
        gx = int(np.floor(x / self.grid_size))
        gy = int(np.floor(y / self.grid_size))

        candidates = []
        seen = set()

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                raw_cell = self.grid.get((gx + dx, gy + dy))
                cell_spaces = self._normalize_cell_spaces(raw_cell)
                if cell_spaces is None:
                    if raw_cell is not None:
                        logger.warning(
                            "SpaceIndex.query skipping corrupt grid cell %s type=%s",
                            (gx + dx, gy + dy),
                            type(raw_cell).__name__,
                        )
                    continue
                if raw_cell is not None and not isinstance(raw_cell, list):
                    self.grid[(gx + dx, gy + dy)] = cell_spaces
                for space in cell_spaces:
                    if space.global_id not in seen:
                        if space.bbox.min_z - tolerance <= z <= space.bbox.max_z + tolerance:
                            candidates.append(space)
                            seen.add(space.global_id)

        return candidates


# When the heavy work runs in a spawn-isolated child (see _run_in_spawn_isolation),
# the RQ job context is NOT inherited (spawn = fresh interpreter). These globals
# let the child resolve its job id / Redis connection so progress meta updates and
# job_id-tagged phase logs keep working from inside the subprocess.
_ISOLATED_JOB_ID: Optional[str] = None
_ISOLATED_REDIS_URL: Optional[str] = None
_ISOLATED_JOB: Any = None


def _current_job_id() -> Optional[str]:
    if _ISOLATED_JOB_ID is not None:
        return _ISOLATED_JOB_ID
    try:
        from rq import get_current_job

        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _resolve_job() -> Any:
    """Return the active RQ job for progress-meta updates.

    In-process (SimpleWorker parent) this is ``rq.get_current_job()``. Inside a
    spawn-isolated child there is no job context, so the job is fetched from
    Redis by the id injected into the child payload.
    """
    global _ISOLATED_JOB
    if _ISOLATED_JOB_ID is not None:
        if _ISOLATED_JOB is None and _ISOLATED_REDIS_URL:
            try:
                from redis import Redis
                from rq.job import Job

                conn = Redis.from_url(_ISOLATED_REDIS_URL)
                _ISOLATED_JOB = Job.fetch(_ISOLATED_JOB_ID, connection=conn)
            except Exception:
                _ISOLATED_JOB = None
        return _ISOLATED_JOB
    try:
        from rq import get_current_job

        return get_current_job()
    except Exception:
        return None


@dataclass
class _JobTuning:
    cell_mode: str
    distance_mode: str
    max_proximate_spaces: int
    proximity_threshold_m: float
    progress_log_every: int
    overlap_resolution: bool
    overlap_samples: int
    overlap_confidence_margin: float
    overlap_coverage_min: float
    hybrid_geometric_fallback: bool


@dataclass
class _RunStats:
    topologic_distance_calls: int = 0
    topologic_distance_failures: int = 0
    topologic_containment_calls: int = 0
    bbox_distance_resolutions: int = 0
    prism_cells: int = 0
    mesh_cells: int = 0
    mesh_repaired_cells: int = 0
    hull_cells: int = 0
    mesh_faces_skipped: int = 0
    ambiguous_resolved: int = 0
    unmatched_resolved: int = 0
    overlap_majority: int = 0
    overlap_geometric: int = 0
    proximity_forced: int = 0
    legacy_matched: int = 0
    confidence_histogram: Optional[Dict[str, int]] = None

    def __post_init__(self) -> None:
        if self.confidence_histogram is None:
            self.confidence_histogram = {
                "0.0-0.25": 0,
                "0.25-0.5": 0,
                "0.5-0.75": 0,
                "0.75-1.0": 0,
            }

    def record_confidence(self, confidence: float) -> None:
        if self.confidence_histogram is None:
            self.confidence_histogram = {}
        if confidence < 0.25:
            bucket = "0.0-0.25"
        elif confidence < 0.5:
            bucket = "0.25-0.5"
        elif confidence < 0.75:
            bucket = "0.5-0.75"
        else:
            bucket = "0.75-1.0"
        self.confidence_histogram[bucket] = self.confidence_histogram.get(bucket, 0) + 1


def _tuning_from_request(request: TopologicpyRequest) -> _JobTuning:
    return _JobTuning(
        cell_mode=(request.cell_mode or _CELL_MODE).strip().lower(),
        distance_mode=(request.distance_mode or _DISTANCE_MODE).strip().lower(),
        max_proximate_spaces=max(
            1,
            int(request.max_proximate_spaces or _MAX_PROXIMATE_SPACES),
        ),
        proximity_threshold_m=_PROXIMITY_THRESHOLD_M,
        progress_log_every=_PROGRESS_LOG_EVERY,
        overlap_resolution=request.overlap_resolution if request.overlap_resolution is not None else _OVERLAP_RESOLUTION,
        overlap_samples=max(4, min(128, int(request.overlap_samples or _OVERLAP_SAMPLES))),
        overlap_confidence_margin=float(
            request.overlap_confidence_margin if request.overlap_confidence_margin is not None else _OVERLAP_CONFIDENCE_MARGIN
        ),
        overlap_coverage_min=float(
            request.overlap_coverage_min if request.overlap_coverage_min is not None else _OVERLAP_COVERAGE_MIN
        ),
        hybrid_geometric_fallback=(
            request.hybrid_geometric_fallback
            if request.hybrid_geometric_fallback is not None
            else _HYBRID_GEOMETRIC_FALLBACK
        ),
    )


class _PsetCache:
    """Avoid repeated IsDefinedBy scans when stamping thousands of elements."""

    def __init__(self, model: Any, pset_name: str) -> None:
        self._model = model
        self._pset_name = pset_name
        self._by_element_id: Dict[int, Any] = {}

    def get_or_create(self, element: Any) -> Any:
        key = element.id()
        cached = self._by_element_id.get(key)
        if cached is not None:
            return cached
        pset = _get_or_create_pset(self._model, element, self._pset_name)
        self._by_element_id[key] = pset
        return pset


def _log_phase(phase: str, **fields: Any) -> None:
    """Structured phase log for production tracking (grep-friendly key=value)."""
    job_id = _current_job_id()
    parts = [f"phase={phase}"]
    if job_id:
        parts.append(f"job_id={job_id}")
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    logger.info("[roomstamp] %s", " ".join(parts))


def _use_topologic_distance(selected_engine: str, tuning: _JobTuning) -> bool:
    return (
        selected_engine == TopologyEngine.TOPOLOGICPY.value
        and tuning.distance_mode == "topologic"
    )


def _use_topologic_containment(selected_engine: str) -> bool:
    return selected_engine == TopologyEngine.TOPOLOGICPY.value


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


def _selected_engine(request: TopologicpyRequest, status: Dict[str, Any]) -> str:
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


def _placement_point(product: Any, scale_factor: float = 1.0) -> Optional[Point]:
    placement_module = getattr(ifcopenshell.util, "placement", None)
    if placement_module is None or not getattr(product, "ObjectPlacement", None):
        return None
    try:
        matrix = placement_module.get_local_placement(product.ObjectPlacement)
        return (
            float(matrix[0][3]) * scale_factor,
            float(matrix[1][3]) * scale_factor,
            float(matrix[2][3]) * scale_factor,
        )
    except Exception:
        return None


def _bbox_sample_point(bbox: BBox, vertical_offset_m: Optional[float] = None) -> Point:
    """Sample point from element bbox for room matching.

    Uses horizontal bbox center. For vertically tall elements (centroid more than
    ``vertical_offset_m`` above the bbox floor), Z is taken at ``min_z + offset``
    so columns, vertical risers, and tall proxies stamp against the lowest
    storey zone instead of a level several metres higher.
    """
    offset_m = _VERTICAL_BIAS_OFFSET_M if vertical_offset_m is None else vertical_offset_m
    cx, cy, cz = bbox.centroid
    rise_above_floor = cz - bbox.min_z
    if rise_above_floor > offset_m:
        z = min(bbox.max_z, bbox.min_z + offset_m)
    else:
        z = cz
    return (cx, cy, z)


def _placement_is_reliable(point: Point, bbox: BBox, tolerance: float = 0.01) -> bool:
    """True when ObjectPlacement is near the element geometry.

    Revit MEP exports often leave IfcFlowSegment ObjectPlacement at the model
    origin while the swept solid is offset in representation geometry. Using
    those placements for room matching assigns elements to the wrong room.
    """
    max_gap = max(tolerance, 2.0)
    return _distance_point_to_bbox(point, bbox) <= max_gap


def _sample_point(
    product: Any,
    bbox: Optional[BBox],
    sample_strategy: TopologySampleStrategy,
    scale_factor: float = 1.0,
) -> Optional[Point]:
    if sample_strategy == TopologySampleStrategy.PLACEMENT:
        placement = _placement_point(product, scale_factor)
        if placement is not None and bbox is not None:
            if _placement_is_reliable(placement, bbox):
                return placement
            return _bbox_sample_point(bbox)
        return placement or (_bbox_sample_point(bbox) if bbox else None)
    return _bbox_sample_point(bbox) if bbox else _placement_point(product, scale_factor)


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
        try:
            shape = ifcopenshell.geom.create_shape(settings, space)
            verts = list(shape.geometry.verts)
            faces = list(shape.geometry.faces)
        except Exception as exc:
            logger.debug("Could not create shape for space %s: %s", getattr(space, "GlobalId", None), exc)
            geometry_failures += 1
            continue

        if len(verts) < 3:
            geometry_failures += 1
            continue

        xs = verts[0::3]
        ys = verts[1::3]
        zs = verts[2::3]
        bbox = BBox(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

        spaces.append(
            SpaceCandidate(
                source_file=source_file,
                global_id=getattr(space, "GlobalId", ""),
                name=getattr(space, "Name", None),
                long_name=getattr(space, "LongName", None),
                storey=_space_storey(space),
                zones=zones.get(getattr(space, "GlobalId", ""), []),
                bbox=bbox,
                verts=verts,
                faces=faces,
            )
        )

    spaces.sort(key=lambda item: item.bbox.volume)
    return spaces, geometry_failures


def _space_to_cache_dict(space: SpaceCandidate) -> Dict[str, Any]:
    """Serialize a collected space (without the C++ topology_cell)."""
    b = space.bbox
    return {
        "source_file": space.source_file,
        "global_id": space.global_id,
        "name": space.name,
        "long_name": space.long_name,
        "storey": space.storey,
        "zones": space.zones,
        "bbox": [b.min_x, b.min_y, b.min_z, b.max_x, b.max_y, b.max_z],
        "verts": space.verts,
        "faces": space.faces,
    }


def _space_from_cache_dict(data: Dict[str, Any]) -> SpaceCandidate:
    bb = data["bbox"]
    return SpaceCandidate(
        source_file=data.get("source_file", ""),
        global_id=data.get("global_id", ""),
        name=data.get("name"),
        long_name=data.get("long_name"),
        storey=data.get("storey"),
        zones=data.get("zones") or [],
        bbox=BBox(bb[0], bb[1], bb[2], bb[3], bb[4], bb[5]),
        verts=data.get("verts"),
        faces=data.get("faces"),
    )


def _coerce_point(value: Any) -> Optional[Point]:
    """Return a finite XYZ tuple, or None when value is not a usable sample point."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            coords = (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
        if all(np.isfinite(c) for c in coords):
            return coords
    return None


def _sanitize_sample_points(points: Iterable[Any], *, context: str = "") -> List[Point]:
    """Drop corrupt sample points instead of aborting the whole roomstamp job."""
    kept: List[Point] = []
    for index, raw in enumerate(points):
        pt = _coerce_point(raw)
        if pt is not None:
            kept.append(pt)
            continue
        logger.warning(
            "Skipping invalid sample point context=%s index=%s type=%s value=%r",
            context or "unknown",
            index,
            type(raw).__name__,
            raw,
        )
    return kept


def _dedupe_sample_points(points: List[Point], max_points: int, min_sep: float = 0.05) -> List[Point]:
    """Keep up to max_points spatially distinct sample locations."""
    cleaned = _sanitize_sample_points(points, context="dedupe_sample_points")
    if not cleaned:
        return []
    kept: List[Point] = []
    min_sep_sq = min_sep * min_sep
    for pt in cleaned:
        if len(kept) >= max_points:
            break
        duplicate = False
        for existing in kept:
            dx = pt[0] - existing[0]
            dy = pt[1] - existing[1]
            dz = pt[2] - existing[2]
            if dx * dx + dy * dy + dz * dz < min_sep_sq:
                duplicate = True
                break
        if not duplicate:
            kept.append(pt)
    return kept


def _build_element_sample_points(
    product: Any,
    bbox: Optional[BBox],
    sample_strategy: TopologySampleStrategy,
    scale_factor: float,
    verts: Optional[List[float]],
    faces: Optional[List[int]],
    max_samples: int,
) -> List[Point]:
    """Multi-point footprint samples: primary point + mesh verts + face centroids."""
    points: List[Point] = []
    primary = _sample_point(product, bbox, sample_strategy, scale_factor) if bbox else _placement_point(product, scale_factor)
    if primary is not None:
        points.append(primary)

    if verts and len(verts) >= 9:
        vertex_count = len(verts) // 3
        vert_budget = max(1, max_samples // 2)
        step = max(1, vertex_count // vert_budget)
        for vi in range(0, vertex_count, step):
            base = vi * 3
            points.append((float(verts[base]), float(verts[base + 1]), float(verts[base + 2])))

    if faces and verts and len(faces) >= 3:
        face_count = len(faces) // 3
        face_budget = max(1, max_samples // 4)
        step = max(1, face_count // face_budget)
        vertex_count = len(verts) // 3
        for fi in range(0, face_count, step):
            i0, i1, i2 = faces[fi * 3], faces[fi * 3 + 1], faces[fi * 3 + 2]
            if max(i0, i1, i2) >= vertex_count:
                continue
            cx = (verts[i0 * 3] + verts[i1 * 3] + verts[i2 * 3]) / 3.0
            cy = (verts[i0 * 3 + 1] + verts[i1 * 3 + 1] + verts[i2 * 3 + 1]) / 3.0
            cz = (verts[i0 * 3 + 2] + verts[i1 * 3 + 2] + verts[i2 * 3 + 2]) / 3.0
            points.append((float(cx), float(cy), float(cz)))

    if not points and bbox is not None:
        points.append(_bbox_sample_point(bbox))

    return _dedupe_sample_points(points, max_samples)


def _geometry_from_iterator(
    model: Any,
    products: List[Any],
    settings: Any,
) -> Tuple[Dict[int, BBox], Dict[int, Tuple[List[float], List[int]]]]:
    """Compute bboxes and triangle meshes for products using geom.iterator."""
    if not products:
        return {}, {}

    bboxes: Dict[int, BBox] = {}
    meshes: Dict[int, Tuple[List[float], List[int]]] = {}
    num_threads = int(os.environ.get("IFCTOPOLOGY_ITERATOR_THREADS", "0"))
    if num_threads <= 0:
        num_threads = os.cpu_count() or 4

    try:
        iterator = ifcopenshell.geom.iterator(settings, model, num_threads, include=products)
        if iterator.initialize():
            while True:
                shape = iterator.get()
                if shape:
                    try:
                        verts = list(shape.geometry.verts)
                        faces = list(shape.geometry.faces)
                        if len(verts) >= 3:
                            xs = verts[0::3]
                            ys = verts[1::3]
                            zs = verts[2::3]
                            bboxes[shape.id] = BBox(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
                            if faces:
                                meshes[shape.id] = (verts, faces)
                    except Exception as e:
                        logger.debug("Error parsing geometry for product id %s: %s", shape.id, e)
                if not iterator.next():
                    break
    except Exception as exc:
        logger.warning("Multithreaded geometry iterator failed, falling back to sequential: %s", exc)
        for product in products:
            bbox = _bbox_from_shape(model, product, settings)
            if bbox:
                bboxes[product.id()] = bbox

    return bboxes, meshes


def _bboxes_from_iterator(
    model: Any,
    products: List[Any],
    settings: Any,
) -> Dict[int, BBox]:
    """Computes bounding boxes for a list of products using a multithreaded geom.iterator."""
    bboxes, _ = _geometry_from_iterator(model, products, settings)
    return bboxes


def _collect_elements(
    model: Any,
    source_file: str,
    query: str,
    settings: Any,
    sample_strategy: TopologySampleStrategy,
    max_elements: Optional[int],
    overlap_samples: int = _OVERLAP_SAMPLES,
) -> Tuple[List[ElementCandidate], int]:
    elements: List[ElementCandidate] = []
    geometry_failures = 0

    import ifcopenshell.util.unit
    try:
        scale_factor = ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0
    except Exception:
        scale_factor = 1.0

    all_products = _filter_elements(model, query)
    if max_elements is not None:
        all_products = all_products[:max_elements]

    # Always compute geometry bboxes (and meshes when available) so PLACEMENT
    # can be validated and overlap voting has footprint samples.
    products_needing_bbox = list(all_products)
    bboxes, meshes = _geometry_from_iterator(model, products_needing_bbox, settings)

    for product in all_products:
        pid = product.id()
        bbox = bboxes.get(pid)
        mesh = meshes.get(pid)
        verts = mesh[0] if mesh else None
        faces = mesh[1] if mesh else None
        if bbox is None:
            point = _placement_point(product, scale_factor)
            if point is None:
                geometry_failures += 1
                continue
            sample_points = [point]
        else:
            point = _sample_point(product, bbox, sample_strategy, scale_factor)
            if point is None:
                geometry_failures += 1
                continue
            sample_points = _build_element_sample_points(
                product,
                bbox,
                sample_strategy,
                scale_factor,
                verts,
                faces,
                overlap_samples,
            )
            if not sample_points:
                sample_points = [point]

        elements.append(
            ElementCandidate(
                source_file=source_file,
                element=product,
                global_id=getattr(product, "GlobalId", ""),
                ifc_class=product.is_a(),
                name=getattr(product, "Name", None),
                sample_point=point,
                bbox=bbox,
                sample_points=sample_points,
                verts=verts,
                faces=faces,
            )
        )

    return elements, geometry_failures


def _build_prism_cell(space: SpaceCandidate, tolerance: float) -> Any:
    from topologicpy.Vertex import Vertex
    from topologicpy.Cell import Cell

    bbox = space.bbox
    width = max(tolerance, bbox.max_x - bbox.min_x)
    length = max(tolerance, bbox.max_y - bbox.min_y)
    height = max(tolerance, bbox.max_z - bbox.min_z)
    origin = Vertex.ByCoordinates(*bbox.centroid)
    return Cell.Prism(
        origin=origin,
        width=width,
        length=length,
        height=height,
        placement="center",
        tolerance=max(tolerance, 0.0001),
    )


# Healing tolerances (metres) tried in order when turning a triangle mesh into a
# watertight solid: exact first, then progressively larger to bridge
# sub-millimetre/centimetre gaps in imperfect source geometry.
_MESH_SEW_TOLERANCES: Tuple[float, ...] = (0.001, 0.01, 0.05)


def _build_mesh_faces(space: SpaceCandidate, tol: float, stats: Optional[_RunStats]) -> List[Any]:
    from topologicpy.Vertex import Vertex
    from topologicpy.Face import Face

    v_list = [
        Vertex.ByCoordinates(space.verts[i], space.verts[i + 1], space.verts[i + 2])
        for i in range(0, len(space.verts), 3)
    ]
    vertex_count = len(v_list)
    faces_list: List[Any] = []
    for i in range(0, len(space.faces), 3):
        idx1, idx2, idx3 = space.faces[i], space.faces[i + 1], space.faces[i + 2]
        if idx1 == idx2 or idx2 == idx3 or idx1 == idx3 or max(idx1, idx2, idx3) >= vertex_count:
            if stats is not None:
                stats.mesh_faces_skipped += 1
            continue
        face = Face.ByVertices([v_list[idx1], v_list[idx2], v_list[idx3]], tolerance=tol, silent=True)
        if face is not None:
            faces_list.append(face)
    return faces_list


def _cell_volume_ok(cell: Any) -> bool:
    from topologicpy.Cell import Cell

    try:
        vol = Cell.Volume(cell)
    except Exception:
        return False
    return bool(vol) and vol > 0


def _build_mesh_cell(space: SpaceCandidate, tolerance: float, stats: Optional[_RunStats] = None) -> Optional[Any]:
    """Build a watertight topologic Cell from the space's triangle mesh.

    Many IfcSpace meshes are not perfectly closed (T-junctions, hairline gaps,
    unwelded coincident vertices). To keep the room's true shape for accurate
    point-in-room tests -- instead of collapsing to a coarse bbox prism -- the
    faces are sewn into a shell (``Shell.ByFaces``) and, if that shell is not yet
    closed, healed with ``Topology.Fix`` (OCCT ShapeFix) at increasing
    tolerances. A Cell is only accepted when built from a shell OCCT confirms is
    closed (``Shell.IsClosed``), so the result is a genuine watertight solid.

    Returns None when no closed shell can be produced; the caller then uses the
    convex-hull fallback (still a watertight mesh, just less precise).
    """
    from topologicpy.Shell import Shell
    from topologicpy.Cell import Cell
    from topologicpy.Topology import Topology

    if not space.verts or not space.faces:
        return None

    base_tol = max(tolerance, 0.0001)
    for tol in (base_tol, *_MESH_SEW_TOLERANCES):
        try:
            faces_list = _build_mesh_faces(space, tol, stats)
        except Exception:
            continue
        if not faces_list:
            continue
        try:
            shell = Shell.ByFaces(faces_list, tolerance=tol, silent=True)
        except Exception:
            shell = None
        if shell is None:
            continue

        closed_shell = None
        repaired = False
        try:
            if Shell.IsClosed(shell):
                closed_shell = shell
        except Exception:
            closed_shell = None

        if closed_shell is None:
            # Heal small gaps (T-junctions, hairline holes) into a closed shell.
            for fix_tol in (tol, *_MESH_SEW_TOLERANCES):
                try:
                    fixed = Topology.Fix(shell, topologyType="Shell", tolerance=fix_tol)
                except Exception:
                    fixed = None
                try:
                    if fixed is not None and Shell.IsClosed(fixed):
                        closed_shell = fixed
                        repaired = True
                        break
                except Exception:
                    continue

        if closed_shell is None:
            continue

        try:
            cell = Cell.ByShell(closed_shell, tolerance=tol, silent=True)
        except Exception:
            cell = None
        if cell is None or not _cell_volume_ok(cell):
            continue

        if stats is not None and repaired:
            stats.mesh_repaired_cells += 1
        return cell

    return None


def _build_hull_cell(space: SpaceCandidate, tolerance: float) -> Optional[Any]:
    """Convex-hull watertight fallback for rooms whose mesh cannot be healed into
    a closed solid (non-manifold or degenerate boundary loops). The hull is
    guaranteed watertight and follows the room's actual extent, so it is far
    tighter and more faithful than a bbox prism."""
    from topologicpy.Vertex import Vertex
    from topologicpy.Cluster import Cluster
    from topologicpy.Cell import Cell
    from topologicpy.Topology import Topology

    if not space.verts or len(space.verts) < 12:  # need >= 4 vertices
        return None
    tol = max(tolerance, 0.0001)
    verts = [
        Vertex.ByCoordinates(space.verts[i], space.verts[i + 1], space.verts[i + 2])
        for i in range(0, len(space.verts), 3)
    ]
    try:
        cluster = Cluster.ByTopologies(verts)
        hull = Topology.ConvexHull(cluster, tolerance=tol, silent=True)
    except Exception:
        return None
    if hull is None:
        return None
    if not Topology.IsInstance(hull, "Cell"):
        try:
            hull = Cell.ByShell(hull, tolerance=tol, silent=True)
        except Exception:
            hull = None
    if hull is None or not Topology.IsInstance(hull, "Cell") or not _cell_volume_ok(hull):
        return None
    return hull


def _lazy_build_cell(
    space: SpaceCandidate,
    tolerance: float,
    stats: Optional[_RunStats] = None,
    tuning: Optional[_JobTuning] = None,
) -> Any:
    if space.topology_cell is not None:
        return space.topology_cell

    cell_mode = (tuning.cell_mode if tuning else _CELL_MODE)

    try:
        from topologicpy.Vertex import Vertex  # noqa: F401
        from topologicpy.Cell import Cell  # noqa: F401
    except ImportError:
        return None

    if cell_mode == "mesh":
        try:
            cell = _build_mesh_cell(space, tolerance, stats)
            if cell is not None:
                space.topology_cell = cell
                space.cell_kind = "mesh"
                if stats is not None:
                    stats.mesh_cells += 1
                return space.topology_cell
        except Exception as exc:
            logger.debug(
                "Mesh cell build failed for %s: %s; trying watertight hull",
                space.global_id,
                exc,
            )

        # Watertight fallback: convex hull of the room verts. Keeps the cell
        # accurate (follows the room extent) instead of collapsing to a coarse
        # bbox prism when the mesh can't be healed into a closed solid.
        try:
            cell = _build_hull_cell(space, tolerance)
            if cell is not None:
                space.topology_cell = cell
                space.cell_kind = "hull"
                if stats is not None:
                    stats.hull_cells += 1
                return space.topology_cell
        except Exception as exc:
            logger.debug(
                "Hull cell build failed for %s: %s; falling back to prism",
                space.global_id,
                exc,
            )

    try:
        space.topology_cell = _build_prism_cell(space, tolerance)
        space.cell_kind = "prism"
        if stats is not None:
            stats.prism_cells += 1
        return space.topology_cell
    except Exception:
        return None


def _prebuild_space_cells(
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    stats: _RunStats,
    tuning: _JobTuning,
) -> int:
    if selected_engine != TopologyEngine.TOPOLOGICPY.value:
        return 0

    built = 0
    for space in spaces:
        if _lazy_build_cell(space, tolerance, stats, tuning) is not None:
            built += 1
    return built


def _prefill_cells_from_cache(
    spaces: Iterable[SpaceCandidate],
    cached_cells: List[Dict[str, Any]],
    stats: Optional[_RunStats],
) -> int:
    """Rebuild Topologic cells from cached BREP strings and attach them to the
    matching spaces (by GlobalId). ~200x faster than mesh rebuild. Spaces not
    present in the cache are left for _prebuild_space_cells to build fresh."""
    from topologicpy.Topology import Topology

    by_gid = {c["global_id"]: c for c in cached_cells if c.get("brep") and c.get("global_id")}
    filled = 0
    for space in spaces:
        rec = by_gid.get(space.global_id)
        if not rec:
            continue
        try:
            cell = Topology.ByBREPString(rec["brep"])
        except Exception:
            cell = None
        if cell is None:
            continue
        space.topology_cell = cell
        space.cell_kind = rec.get("kind")
        if stats is not None:
            kind = rec.get("kind")
            if kind == "prism":
                stats.prism_cells += 1
            elif kind == "hull":
                stats.hull_cells += 1
            else:
                stats.mesh_cells += 1
        filled += 1
    return filled


def _cells_to_cache(spaces: Iterable[SpaceCandidate]) -> List[Dict[str, Any]]:
    """Serialize built Topologic cells to BREP strings for the cell cache."""
    from topologicpy.Topology import Topology

    out: List[Dict[str, Any]] = []
    for space in spaces:
        if space.topology_cell is None:
            continue
        try:
            brep = Topology.BREPString(space.topology_cell)
        except Exception:
            continue
        if not brep:
            continue
        out.append({
            "global_id": space.global_id,
            "kind": space.cell_kind or "mesh",
            "brep": brep,
        })
    return out


def _space_contains_point(
    space: SpaceCandidate,
    point: Point,
    tolerance: float,
    selected_engine: str,
    stats: Optional[_RunStats],
    tuning: _JobTuning,
) -> bool:
    if not space.bbox.contains_point(point, tolerance):
        return False
    if not _use_topologic_containment(selected_engine):
        return True
    if stats is not None:
        stats.topologic_containment_calls += 1
    return _topologic_contains(space, point, tolerance, tuning)


def _nearest_spaces_by_bbox(
    point: Point,
    candidates: Iterable[SpaceCandidate],
    limit: int,
    max_distance: Optional[float] = None,
) -> List[SpaceCandidate]:
    ranked: List[Tuple[float, SpaceCandidate]] = []
    for space in candidates:
        dist = _distance_point_to_bbox(point, space.bbox)
        if max_distance is not None and dist > max_distance:
            continue
        ranked.append((dist, space))
    ranked.sort(key=lambda item: item[0])
    return [space for _, space in ranked[:limit]]


def _closest_space_by_bbox(point: Point, spaces: List[SpaceCandidate]) -> Optional[SpaceCandidate]:
    if not spaces:
        return None
    distances = [(_distance_point_to_bbox(point, space.bbox), space) for space in spaces]
    distances.sort(key=lambda item: item[0])
    return distances[0][1]


def _closest_space_by_topologic(
    point: Point,
    spaces: List[SpaceCandidate],
    tolerance: float,
    stats: _RunStats,
    tuning: _JobTuning,
) -> Optional[SpaceCandidate]:
    try:
        from topologicpy.Vertex import Vertex

        vertex = Vertex.ByCoordinates(*point)
        distances: List[Tuple[float, SpaceCandidate]] = []
        for space in spaces:
            cell = _lazy_build_cell(space, tolerance, stats, tuning)
            if cell is None:
                distances.append((_distance_point_to_bbox(point, space.bbox), space))
                continue
            stats.topologic_distance_calls += 1
            try:
                t_dist = Vertex.Distance(vertex, cell, includeCentroid=False)
                distances.append((t_dist, space))
            except Exception as exc:
                stats.topologic_distance_failures += 1
                logger.debug(
                    "TopologicPy distance failed on space %s: %s; using bbox distance",
                    space.global_id,
                    exc,
                )
                distances.append((_distance_point_to_bbox(point, space.bbox), space))
        if not distances:
            return None
        distances.sort(key=lambda item: item[0])
        return distances[0][1]
    except Exception as exc:
        stats.topologic_distance_failures += 1
        logger.warning("TopologicPy distance resolution failed: %s; using bbox distance", exc)
        return _closest_space_by_bbox(point, spaces)


def _topologic_contains_vertex(
    space: SpaceCandidate,
    vertex: Any,
    tolerance: float,
    tuning: Optional[_JobTuning] = None,
) -> bool:
    from topologicpy.Cell import Cell

    cell = _lazy_build_cell(space, tolerance, tuning=tuning)
    if cell is None:
        return space.bbox.contains_point((vertex.X(), vertex.Y(), vertex.Z()), tolerance)
    status = Cell.ContainmentStatus(cell, vertex, tolerance=max(tolerance, 0.0001))
    return status in (0, 1)


def _topologic_contains(
    space: SpaceCandidate,
    point: Point,
    tolerance: float,
    tuning: Optional[_JobTuning] = None,
) -> bool:
    try:
        from topologicpy.Vertex import Vertex
        vertex = Vertex.ByCoordinates(*point)
        return _topologic_contains_vertex(space, vertex, tolerance, tuning)
    except Exception:
        return space.bbox.contains_point(point, tolerance)


def _distance_point_to_bbox(point: Point, bbox: BBox) -> float:
    """Computes exact Euclidean distance from a 3D point to a bounding box (AABB)."""
    dx = max(bbox.min_x - point[0], 0.0, point[0] - bbox.max_x)
    dy = max(bbox.min_y - point[1], 0.0, point[1] - bbox.max_y)
    dz = max(bbox.min_z - point[2], 0.0, point[2] - bbox.max_z)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _element_sample_points(element: ElementCandidate) -> List[Point]:
    if element.sample_points:
        cleaned = _sanitize_sample_points(
            element.sample_points,
            context=f"element:{element.global_id}",
        )
        if cleaned:
            return cleaned
    coerced = _coerce_point(element.sample_point)
    if coerced is not None:
        return [coerced]
    logger.warning(
        "Element %s has no valid sample points; matching will treat it as unmatched",
        element.global_id,
    )
    return []


def _vote_rooms_by_samples(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex],
    stats: Optional[_RunStats],
    tuning: _JobTuning,
) -> Tuple[Dict[str, int], int, Dict[str, SpaceCandidate]]:
    """Tally per-room containment hits across element sample points.

    Multi-point voting uses bbox containment for throughput; ambiguous cases
    fall through to footprint overlap resolution.
    """
    hits: Dict[str, int] = {}
    space_by_gid: Dict[str, SpaceCandidate] = {s.global_id: s for s in spaces}
    points = _element_sample_points(element)
    points_with_hit = 0
    vote_engine = TopologyEngine.BBOX.value

    for point in points:
        if space_index is not None:
            candidates = space_index.query(point, tolerance)
        else:
            candidates = list(spaces)

        point_hit_any = False
        for space in candidates:
            if _space_contains_point(space, point, tolerance, vote_engine, stats, tuning):
                hits[space.global_id] = hits.get(space.global_id, 0) + 1
                point_hit_any = True
        if point_hit_any:
            points_with_hit += 1

    # When bbox vote finds a single room, confirm with the selected engine on the primary point.
    if selected_engine != vote_engine and len(hits) == 1:
        only_gid = next(iter(hits))
        only_space = space_by_gid.get(only_gid)
        if only_space is not None and not _space_contains_point(
            only_space,
            element.sample_point,
            tolerance,
            selected_engine,
            stats,
            tuning,
        ):
            hits.clear()
            points_with_hit = 0

    return hits, points_with_hit, space_by_gid


def _footprint_overlap_score(element_bbox: BBox, space_bbox: BBox) -> float:
    ix0 = max(element_bbox.min_x, space_bbox.min_x)
    iy0 = max(element_bbox.min_y, space_bbox.min_y)
    ix1 = min(element_bbox.max_x, space_bbox.max_x)
    iy1 = min(element_bbox.max_y, space_bbox.max_y)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter_area = (ix1 - ix0) * (iy1 - iy0)
    elem_area = max(
        (element_bbox.max_x - element_bbox.min_x) * (element_bbox.max_y - element_bbox.min_y),
        1e-9,
    )
    return inter_area / elem_area


def _best_room_by_overlap(
    element: ElementCandidate,
    candidate_rooms: List[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    stats: Optional[_RunStats],
    tuning: _JobTuning,
) -> Tuple[Optional[SpaceCandidate], float]:
    """Resolve ambiguous matches using horizontal footprint overlap (stable, no boolean ops)."""
    if not candidate_rooms or element.bbox is None:
        return None, 0.0

    scored: List[Tuple[float, SpaceCandidate]] = []
    for space in candidate_rooms:
        scored.append((_footprint_overlap_score(element.bbox, space.bbox), space))
    scored.sort(key=lambda item: item[0], reverse=True)

    if not scored or scored[0][0] <= 0.0:
        if _use_topologic_distance(selected_engine, tuning) and stats is not None:
            chosen = _closest_space_by_topologic(
                element.sample_point,
                candidate_rooms,
                tolerance,
                stats,
                tuning,
            )
            if chosen is not None:
                dist = _distance_point_to_bbox(element.sample_point, chosen.bbox)
                return chosen, max(0.0, 1.0 - min(dist, 2.0) / 2.0)
        chosen = _closest_space_by_bbox(element.sample_point, candidate_rooms)
        if chosen is not None:
            dist = _distance_point_to_bbox(element.sample_point, chosen.bbox)
            return chosen, max(0.0, 1.0 - min(dist, 2.0) / 2.0)
        return None, 0.0

    best_score, best_space = scored[0]
    confidence = min(1.0, best_score)
    return best_space, confidence


def _proximity_resolution(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex],
    stats: Optional[_RunStats],
    tuning: _JobTuning,
) -> Optional[SpaceCandidate]:
    space_list = list(spaces)
    if space_index is not None:
        proximate_pool = space_index.query(
            element.sample_point,
            tuning.proximity_threshold_m,
        )
    else:
        proximate_pool = space_list

    proximate_candidates = _nearest_spaces_by_bbox(
        element.sample_point,
        proximate_pool,
        limit=tuning.max_proximate_spaces,
        max_distance=tuning.proximity_threshold_m,
    )
    if not proximate_candidates:
        proximate_candidates = _nearest_spaces_by_bbox(
            element.sample_point,
            space_list,
            limit=tuning.max_proximate_spaces,
        )
    if not proximate_candidates:
        return None

    if stats is not None:
        stats.unmatched_resolved += 1
        stats.bbox_distance_resolutions += 1

    if _use_topologic_distance(selected_engine, tuning) and stats is not None:
        chosen = _closest_space_by_topologic(
            element.sample_point,
            proximate_candidates,
            tolerance,
            stats,
            tuning,
        )
        if chosen is not None:
            return chosen
    return _closest_space_by_bbox(element.sample_point, proximate_candidates)


def _resolve_dominant_room(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex],
    resolve_unmatched: bool,
    stats: Optional[_RunStats],
    tuning: _JobTuning,
) -> MatchResolution:
    points = _element_sample_points(element)
    if not points:
        return MatchResolution(None, "unmatched", "Unmatched", 0.0, 0, [])

    hits, points_with_hit, space_by_gid = _vote_rooms_by_samples(
        element,
        spaces,
        tolerance,
        selected_engine,
        space_index,
        stats,
        tuning,
    )
    coverage = points_with_hit / len(points) if points else 0.0

    if hits:
        ranked = sorted(hits.items(), key=lambda item: item[1], reverse=True)
        dom_gid, dom_hits = ranked[0]
        second_hits = ranked[1][1] if len(ranked) > 1 else 0
        total_hits = sum(hits.values())
        confidence = dom_hits / total_hits if total_hits else 0.0
        margin = (dom_hits - second_hits) / total_hits if total_hits else 0.0
        candidate_spaces = [space_by_gid[gid] for gid, _ in ranked if gid in space_by_gid]

        decisive = (
            confidence >= tuning.overlap_confidence_margin
            and coverage >= tuning.overlap_coverage_min
            and margin >= 0.1
        )
        if decisive:
            if stats is not None:
                stats.overlap_majority += 1
                stats.record_confidence(confidence)
            return MatchResolution(
                space_by_gid[dom_gid],
                "overlap_majority",
                "Contained",
                confidence,
                len(candidate_spaces),
                candidate_spaces,
            )

        if tuning.hybrid_geometric_fallback:
            top_k = candidate_spaces[: tuning.max_proximate_spaces]
            if stats is not None:
                stats.ambiguous_resolved += 1
            chosen, geo_conf = _best_room_by_overlap(
                element,
                top_k,
                tolerance,
                selected_engine,
                stats,
                tuning,
            )
            if chosen is not None:
                if stats is not None:
                    stats.overlap_geometric += 1
                    stats.record_confidence(geo_conf)
                return MatchResolution(
                    chosen,
                    "overlap_geometric",
                    "Resolved",
                    geo_conf,
                    len(candidate_spaces),
                    candidate_spaces,
                )

        if stats is not None:
            stats.bbox_distance_resolutions += 1
            stats.ambiguous_resolved += 1
        return MatchResolution(
            space_by_gid[dom_gid],
            "overlap_majority",
            "Resolved",
            confidence,
            len(candidate_spaces),
            candidate_spaces,
        )

    if resolve_unmatched:
        chosen = _proximity_resolution(
            element,
            spaces,
            tolerance,
            selected_engine,
            space_index,
            stats,
            tuning,
        )
        if chosen is not None:
            dist = _distance_point_to_bbox(element.sample_point, chosen.bbox)
            confidence = max(0.0, 1.0 - min(dist, tuning.proximity_threshold_m) / tuning.proximity_threshold_m)
            if stats is not None:
                stats.proximity_forced += 1
                stats.record_confidence(confidence)
            return MatchResolution(
                chosen,
                "proximity",
                "Proximity",
                confidence,
                0,
                [],
            )

    return MatchResolution(None, "unmatched", "Unmatched", 0.0, 0, [])


def _default_job_tuning() -> _JobTuning:
    return _JobTuning(
        cell_mode=_CELL_MODE,
        distance_mode=_DISTANCE_MODE,
        max_proximate_spaces=_MAX_PROXIMATE_SPACES,
        proximity_threshold_m=_PROXIMITY_THRESHOLD_M,
        progress_log_every=_PROGRESS_LOG_EVERY,
        overlap_resolution=_OVERLAP_RESOLUTION,
        overlap_samples=_OVERLAP_SAMPLES,
        overlap_confidence_margin=_OVERLAP_CONFIDENCE_MARGIN,
        overlap_coverage_min=_OVERLAP_COVERAGE_MIN,
        hybrid_geometric_fallback=_HYBRID_GEOMETRIC_FALLBACK,
    )


def _match_element_resolution(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex] = None,
    resolve_ambiguous: bool = True,
    resolve_unmatched: bool = False,
    stats: Optional[_RunStats] = None,
    tuning: Optional[_JobTuning] = None,
) -> MatchResolution:
    effective_tuning = tuning or _default_job_tuning()

    if effective_tuning.overlap_resolution:
        return _resolve_dominant_room(
            element,
            spaces,
            tolerance,
            selected_engine,
            space_index,
            resolve_unmatched,
            stats,
            effective_tuning,
        )

    matches = _match_element_to_spaces_legacy(
        element,
        spaces,
        tolerance,
        selected_engine,
        space_index=space_index,
        resolve_ambiguous=resolve_ambiguous,
        resolve_unmatched=resolve_unmatched,
        stats=stats,
        tuning=effective_tuning,
    )
    if not matches:
        return MatchResolution(None, "unmatched", "Unmatched", 0.0, 0, [])
    if len(matches) == 1:
        if stats is not None:
            stats.legacy_matched += 1
            stats.record_confidence(1.0)
        return MatchResolution(
            matches[0],
            "legacy",
            "Contained" if len(matches) == 1 else "Resolved",
            1.0,
            len(matches),
            matches,
        )
    if stats is not None:
        stats.record_confidence(1.0 / len(matches))
    return MatchResolution(
        matches[0],
        "legacy",
        "Resolved",
        1.0 / len(matches),
        len(matches),
        matches,
    )


def _match_element_to_spaces_legacy(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex] = None,
    resolve_ambiguous: bool = True,
    resolve_unmatched: bool = False,
    stats: Optional[_RunStats] = None,
    tuning: Optional[_JobTuning] = None,
) -> List[SpaceCandidate]:
    effective_tuning = tuning or _default_job_tuning()
    space_list = list(spaces)
    if space_index is not None:
        candidates = space_index.query(element.sample_point, tolerance)
    else:
        candidates = space_list

    bbox_matches = [
        space for space in candidates
        if _space_contains_point(
            space,
            element.sample_point,
            tolerance,
            selected_engine,
            stats,
            effective_tuning,
        )
    ]

    # Case A: Exactly 1 unambiguous match - we are done!
    if len(bbox_matches) == 1:
        return bbox_matches

    # Case B: Multiple overlapping spaces (Ambiguous) -> Resolve to the single closest space!
    if len(bbox_matches) > 1 and resolve_ambiguous:
        if stats is not None:
            stats.ambiguous_resolved += 1
        if _use_topologic_distance(selected_engine, effective_tuning) and stats is not None:
            chosen = _closest_space_by_topologic(
                element.sample_point,
                bbox_matches,
                tolerance,
                stats,
                effective_tuning,
            )
            if chosen is not None:
                return [chosen]
        if stats is not None:
            stats.bbox_distance_resolutions += 1
        chosen = _closest_space_by_bbox(element.sample_point, bbox_matches)
        return [chosen] if chosen is not None else bbox_matches

    # Case C: No matched spaces (Unmatched) -> Look for the closest space!
    if len(bbox_matches) == 0 and resolve_unmatched:
        if stats is not None:
            stats.unmatched_resolved += 1
        if space_index is not None:
            proximate_pool = space_index.query(
                element.sample_point,
                effective_tuning.proximity_threshold_m,
            )
        else:
            proximate_pool = space_list

        proximate_candidates = _nearest_spaces_by_bbox(
            element.sample_point,
            proximate_pool,
            limit=effective_tuning.max_proximate_spaces,
            max_distance=effective_tuning.proximity_threshold_m,
        )
        if not proximate_candidates:
            proximate_candidates = _nearest_spaces_by_bbox(
                element.sample_point,
                space_list,
                limit=effective_tuning.max_proximate_spaces,
            )

        if proximate_candidates:
            if _use_topologic_distance(selected_engine, effective_tuning) and stats is not None:
                chosen = _closest_space_by_topologic(
                    element.sample_point,
                    proximate_candidates,
                    tolerance,
                    stats,
                    effective_tuning,
                )
                if chosen is not None:
                    return [chosen]
            if stats is not None:
                stats.bbox_distance_resolutions += 1
            chosen = _closest_space_by_bbox(element.sample_point, proximate_candidates)
            if chosen is not None:
                return [chosen]

    return bbox_matches


def _match_element_to_spaces(
    element: ElementCandidate,
    spaces: Iterable[SpaceCandidate],
    tolerance: float,
    selected_engine: str,
    space_index: Optional[SpaceIndex] = None,
    resolve_ambiguous: bool = True,
    resolve_unmatched: bool = False,
    stats: Optional[_RunStats] = None,
    tuning: Optional[_JobTuning] = None,
) -> List[SpaceCandidate]:
    resolution = _match_element_resolution(
        element,
        spaces,
        tolerance,
        selected_engine,
        space_index=space_index,
        resolve_ambiguous=resolve_ambiguous,
        resolve_unmatched=resolve_unmatched,
        stats=stats,
        tuning=tuning,
    )
    if resolution.space is not None:
        return [resolution.space]
    return resolution.raw_matches or []


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


def _element_payload(element: ElementCandidate, resolution: MatchResolution) -> Dict[str, Any]:
    matches = resolution.raw_matches or ([resolution.space] if resolution.space else [])
    chosen = resolution.space
    return {
        "source_file": element.source_file,
        "global_id": element.global_id,
        "ifc_class": element.ifc_class,
        "name": element.name,
        "sample_point": list(element.sample_point),
        "sample_point_count": len(_element_sample_points(element)),
        "bbox": element.bbox.to_dict() if element.bbox else None,
        "matched_space": _space_payload(chosen),
        "matched_spaces": [_space_payload(space) for space in matches],
        "match_status": resolution.status.lower(),
        "match_method": resolution.method,
        "match_confidence": round(resolution.confidence, 4),
        "match_count": resolution.candidate_count,
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


def _stamp_element(
    model: Any,
    element: Any,
    space: SpaceCandidate,
    resolution: MatchResolution,
    pset_name: str,
    selected_engine: str,
    pset_cache: Optional[_PsetCache] = None,
) -> None:
    import ifcopenshell.api

    zone_names = [zone.get("name") for zone in space.zones if zone.get("name")]
    zone_ids = [zone.get("global_id") for zone in space.zones if zone.get("global_id")]
    properties = {
        "SpatialMatchStatus": resolution.status,
        "SpatialMatchMethod": resolution.method,
        "SpatialMatchConfidence": f"{resolution.confidence:.4f}",
        "SpatialMatchCount": str(max(resolution.candidate_count, 1)),
        "SpatialSourceFile": space.source_file,
        "SpatialRelationshipEngine": selected_engine,
        "SpaceGlobalId": space.global_id,
        "SpaceName": space.name or "",
        "SpaceLongName": space.long_name or "",
        "RoomGlobalId": space.global_id,
        "RoomName": space.name or "",
        "RoomLongName": space.long_name or "",
        "BuildingStoreyName": space.storey or "",
        "ZoneNames": ", ".join(zone_names),
        "ZoneGlobalIds": ", ".join(zone_ids),
        "StampedBy": "topologicpy-worker",
    }
    if pset_cache is not None:
        pset = pset_cache.get_or_create(element)
    else:
        pset = _get_or_create_pset(model, element, pset_name)
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=properties)


def _stage_inputs(
    files: List[str],
    tmpdir: str,
    request: Any = None,
) -> Tuple[Dict[str, str], List[str], Dict[str, Optional[str]]]:
    """Download input files to a local temp dir (S3) or resolve filesystem paths.

    Returns (local_paths_map, s3_keys, version_pins).
    """
    paths: Dict[str, str] = {}
    keys: List[str] = []
    pins: Dict[str, Optional[str]] = {}
    for index, filename in enumerate(files):
        if s3.is_enabled():
            key = s3.normalize_input_key(filename)
            keys.append(key)
            pin = s3.pin_for(request, filename) if request else None
            pins[key] = pin
            local_name = os.path.basename(key) or "input.ifc"
            local_path = os.path.join(tmpdir, f"{index}-{local_name}")
            s3.download_to_path(key, local_path, version_id=pin)
        else:
            normalized = filename.lstrip("/")
            if normalized.startswith("uploads/"):
                normalized = normalized[len("uploads/"):]
                local_path = os.path.join(UPLOADS_DIR, normalized)
            elif normalized.startswith("output/"):
                local_path = os.path.join(OUTPUT_DIR, normalized[len("output/"):])
            elif normalized.startswith("examples/"):
                local_path = os.path.join(EXAMPLES_DIR, normalized[len("examples/"):])
            else:
                local_path = os.path.join(UPLOADS_DIR, normalized)
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"Input IFC file not found: {filename}")
        paths[filename] = local_path
    return paths, keys, pins


def _path_under_output_root(output_dir: str, requested_path: str) -> str:
    normalized = requested_path.strip("/")
    if normalized.startswith("output/topology/"):
        normalized = normalized[len("output/topology/"):]
    elif normalized.startswith("output/"):
        normalized = os.path.basename(normalized)
    if not normalized:
        normalized = "topology_roomstamp_report.json"
    return os.path.join(output_dir, normalized)


def _resolve_element_original_filename(
    request: TopologicpyRequest,
    source_file: str,
    input_pins: Dict[str, Optional[str]],
) -> str:
    """Resolve the human-readable upload name for an element input (ifcpatch parity).

    Walks audit lineage from the pinned element key so derivative inputs still
    surface the root upload name (e.g. ``S2-200-MM-MASTER MODEL.ifc`` vs the
    sanitized object key ``S2-200-MM-MASTER_MODEL.ifc``).
    """
    fallback = os.path.basename(source_file) if source_file else ""
    try:
        source_key = s3.normalize_input_key(source_file)
    except Exception:
        return fallback

    version_id = input_pins.get(source_key)
    if not version_id:
        version_id = s3.pin_for(request, source_file)

    audit_id: Optional[int] = None
    raw_audit_id = getattr(request, "input_audit_id", None)
    if raw_audit_id is not None:
        try:
            candidate = int(raw_audit_id)
            if candidate > 0:
                row = audit_db.fetch_version_pin_by_audit_id(candidate)
                if row and row.get("object_key") == source_key:
                    audit_id = candidate
        except (TypeError, ValueError):
            pass

    try:
        name = audit_db.resolve_original_filename(
            object_key=source_key,
            version_id=version_id,
            audit_id=audit_id,
        )
        if name:
            return name
    except Exception as e:
        logger.debug(
            "original_filename lookup failed for %s: %s", source_file, e
        )
    return fallback


def _default_output_ifc_name(source_name: str) -> str:
    base, ext = os.path.splitext(os.path.basename(source_name))
    return f"{base or 'model'}{ext or '.ifc'}"


def _resolve_output_ifc_basenames(element_files: List[str]) -> Dict[str, str]:
    """Map each element source key to a unique output basename."""
    assigned: Dict[str, str] = {}
    usage: Dict[str, int] = {}
    for source in element_files:
        base_name = _default_output_ifc_name(source)
        if base_name not in usage:
            usage[base_name] = 1
            assigned[source] = base_name
            continue
        usage[base_name] += 1
        stem, ext = os.path.splitext(base_name)
        assigned[source] = f"{stem}_{usage[base_name]}{ext}"
    return assigned


def _output_ifc_path(
    request: TopologicpyRequest,
    output_dir: str,
    output_basename: str,
) -> str:
    if not request.output_ifc_prefix:
        return os.path.join(output_dir, output_basename)

    prefix = request.output_ifc_prefix.strip("/")
    if prefix.startswith("output/topology/"):
        prefix = prefix[len("output/topology/"):]
    elif prefix.startswith("output/"):
        prefix = os.path.basename(prefix)
    if not prefix:
        return os.path.join(output_dir, output_basename)
    if prefix.lower().endswith(".ifc") and len(request.element_files) == 1:
        return os.path.join(output_dir, os.path.basename(prefix))
    return os.path.join(output_dir, prefix, output_basename)


def _roomstamp_arguments_used(request: TopologicpyRequest) -> List[str]:
    return [
        json.dumps(
            {
                "stamp_ambiguous": request.stamp_ambiguous,
                "pset_name": request.pset_name,
                "element_query": request.element_query,
                "space_query": request.space_query,
            },
            separators=(",", ":"),
        )
    ]


def _build_ifc_result_item(
    *,
    local_path: str,
    source_file: str,
    request: TopologicpyRequest,
    input_pins: Dict[str, Optional[str]],
    stamped_element_count: int,
    skipped_ambiguous_count: int,
    skipped_unmatched_count: int,
    output_key: Optional[str] = None,
    audit: Optional[Dict[str, Any]] = None,
    original_filename: Optional[str] = None,
) -> Dict[str, Any]:
    output_size = os.path.getsize(local_path)
    original = original_filename or _resolve_element_original_filename(
        request, source_file, input_pins
    )
    output_filename = os.path.basename(output_key) if output_key else os.path.basename(local_path)

    item: Dict[str, Any] = {
        "success": True,
        "message": "Successfully stamped spatial relationships",
        "output_path": local_path,
        "recipe": "RoomStamp",
        "is_custom": False,
        "output_size_bytes": output_size,
        "arguments_used": _roomstamp_arguments_used(request),
        "original_filename": original,
        "output_filename": output_filename,
        "source_file": source_file,
        "stamped_element_count": stamped_element_count,
        "skipped_ambiguous_count": skipped_ambiguous_count,
        "skipped_unmatched_count": skipped_unmatched_count,
    }

    if s3.is_enabled() and output_key and audit:
        item.update(
            {
                "storage": "s3",
                "bucket": s3.bucket_name(),
                "output_key": output_key,
                "output_path": f"s3://{s3.bucket_name()}/{output_key}",
                "sha256": audit["sha256"],
                "size_bytes": audit["size_bytes"],
                "audit_id": audit["audit_id"],
                "version_id": audit.get("version_id"),
            }
        )
    else:
        item["storage"] = "filesystem"

    return item


def _nest_ifc_results(
    items: List[Dict[str, Any]],
) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]]]]:
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return items


def _build_topology_report_meta(
    *,
    request: TopologicpyRequest,
    summary: Dict[str, Any],
    benchmark: Dict[str, Any],
    report_path: str,
    input_keys: List[str],
    input_pins: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """Benchmark/report sidecar nested one level below the IFC result (ifcpatch-style top level)."""
    meta: Dict[str, Any] = {
        "summary": summary,
        "benchmark": benchmark,
        "storage": "filesystem",
        "report_path": report_path,
    }
    if s3.is_enabled():
        report_key = s3.normalize_output_key(request.output_file, "topology")
        report_audit = s3.upload_and_audit(
            report_path,
            key=report_key,
            operation="topologicpy_roomstamp",
            worker=WORKER_NAME,
            job_id=_current_job_id(),
            parents=[("input", key) for key in input_keys],
            parent_version_ids={k: v for k, v in input_pins.items() if v} or None,
            metadata=summary,
            content_type="application/json",
        )
        meta.update(
            {
                "storage": "s3",
                "report_key": report_key,
                "report_path": f"s3://{s3.bucket_name()}/{report_key}",
                "report_sha256": report_audit["sha256"],
                "report_size_bytes": report_audit["size_bytes"],
                "report_audit_id": report_audit["audit_id"],
                "report_version_id": report_audit.get("version_id"),
            }
        )
    return meta


def _finalize_roomstamp_result(
    *,
    request: TopologicpyRequest,
    summary: Dict[str, Any],
    benchmark: Dict[str, Any],
    report_path: str,
    stamped_outputs: List[Dict[str, Any]],
    input_keys: List[str],
    input_pins: Dict[str, Optional[str]],
    output_dir: str,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    topology_report = _build_topology_report_meta(
        request=request,
        summary=summary,
        benchmark=benchmark,
        report_path=report_path,
        input_keys=input_keys,
        input_pins=input_pins,
    )

    ifc_items: List[Dict[str, Any]] = []
    if request.stamp:
        for item in stamped_outputs:
            local_path = item["output_path"]
            source_file = item["source_file"]
            if s3.is_enabled():
                relative_output = os.path.relpath(local_path, output_dir)
                output_key = s3.normalize_output_key(relative_output, "topology")
                source_key = s3.normalize_input_key(source_file)
                audit_ifc = s3.upload_and_audit(
                    local_path,
                    key=output_key,
                    operation="topologicpy_roomstamp_ifc",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", source_key)],
                    parent_version_ids=(
                        {source_key: input_pins[source_key]}
                        if input_pins.get(source_key)
                        else None
                    ),
                    metadata={
                        "stamped_element_count": item["stamped_element_count"],
                        "skipped_ambiguous_count": item["skipped_ambiguous_count"],
                        "skipped_unmatched_count": item["skipped_unmatched_count"],
                        "pset_name": request.pset_name,
                    },
                    content_type="application/x-step",
                )
                ifc_items.append(
                    _build_ifc_result_item(
                        local_path=local_path,
                        source_file=source_file,
                        request=request,
                        input_pins=input_pins,
                        stamped_element_count=item["stamped_element_count"],
                        skipped_ambiguous_count=item["skipped_ambiguous_count"],
                        skipped_unmatched_count=item["skipped_unmatched_count"],
                        output_key=output_key,
                        audit=audit_ifc,
                    )
                )
            else:
                ifc_items.append(
                    _build_ifc_result_item(
                        local_path=local_path,
                        source_file=source_file,
                        request=request,
                        input_pins=input_pins,
                        stamped_element_count=item["stamped_element_count"],
                        skipped_ambiguous_count=item["skipped_ambiguous_count"],
                        skipped_unmatched_count=item["skipped_unmatched_count"],
                    )
                )

    if request.stamp and len(ifc_items) == 1:
        return {**ifc_items[0], "topology_report": topology_report}
    if request.stamp and len(ifc_items) > 1:
        return {"outputs": ifc_items, "topology_report": topology_report}
    return {
        "success": True,
        "message": "Topology roomstamp benchmark completed",
        "topology_report": topology_report,
    }


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _update_progress(
    processed: int,
    total: int,
    filename: str,
    *,
    phase: str = "matching",
    elements_per_second: Optional[float] = None,
    elapsed_seconds: Optional[float] = None,
) -> None:
    try:
        job = _resolve_job()
        if job:
            eta_seconds = None
            if elements_per_second and elements_per_second > 0 and total > processed:
                eta_seconds = round((total - processed) / elements_per_second, 1)
            job.meta["progress"] = {
                "phase": phase,
                "processed": processed,
                "total": total,
                "current_file": filename,
                "percentage": round((processed / total) * 100.0, 1) if total else 100.0,
                "elements_per_second": round(elements_per_second, 2) if elements_per_second else None,
                "elapsed_seconds": round(elapsed_seconds, 1) if elapsed_seconds is not None else None,
                "eta_seconds": eta_seconds,
            }
            job.save_meta()
    except Exception:
        pass


def _run_roomstamp_benchmark_core(job_data: dict) -> dict:
    """Benchmark room/zone containment and optionally stamp target IFC models with smart scaling."""
    request = TopologicpyRequest(**job_data)
    start = time.perf_counter()
    stats = _RunStats()
    tuning = _tuning_from_request(request)
    topologicpy = _topologicpy_status()
    selected_engine = _selected_engine(request, topologicpy)
    tmpdir = tempfile.mkdtemp(prefix="topologicpy-")

    _log_phase(
        "start",
        engine=selected_engine,
        cell_mode=tuning.cell_mode,
        distance_mode=tuning.distance_mode,
        max_proximate_spaces=tuning.max_proximate_spaces,
        spatial_files=len(request.spatial_files),
        element_files=len(request.element_files),
        stamp=request.stamp,
    )

    try:
        all_inputs = list(dict.fromkeys(request.spatial_files + request.element_files))
        stage_start = time.perf_counter()
        local_paths, input_keys, input_pins = _stage_inputs(all_inputs, tmpdir, request)
        stage_seconds = time.perf_counter() - stage_start
        _log_phase("inputs_staged", seconds=round(stage_seconds, 3), input_count=len(all_inputs))

        output_dir = os.path.join(tmpdir, "output") if s3.is_enabled() else os.path.join(OUTPUT_DIR, "topology")
        os.makedirs(output_dir, exist_ok=True)
        report_path = _path_under_output_root(output_dir, request.output_file)
        settings = _geometry_settings()

        # Step 1: Load and process spatial models.
        # Try the space cache first: an unchanged spatial file yields the same
        # collected verts/faces/bbox, so a hit skips the ifcopenshell.open +
        # per-space create_shape pass (load + space_collect).
        import gc

        spaces: List[SpaceCandidate] = []
        space_geometry_failures = 0
        load_seconds = 0.0
        space_seconds = 0.0
        spatial_shas: Optional[List[str]] = None
        space_cache_key: Optional[str] = None
        space_cache_status = "disabled"

        if space_cache.is_enabled():
            try:
                spatial_shas = [
                    space_cache.file_sha256(local_paths[f]) for f in request.spatial_files
                ]
                space_cache_key = space_cache.build_key(
                    spatial_shas, request.space_query, request.include_zones
                )
                cached = space_cache.load(space_cache_key)
                if cached is not None:
                    cache_load_start = time.perf_counter()
                    spaces = [_space_from_cache_dict(d) for d in cached]
                    spaces.sort(key=lambda item: item.bbox.volume)
                    space_seconds = time.perf_counter() - cache_load_start
                    space_cache_status = "hit"
                    _log_phase(
                        "spaces_cache_hit",
                        seconds=round(space_seconds, 3),
                        space_count=len(spaces),
                        cache_key=space_cache_key,
                    )
                else:
                    space_cache_status = "miss"
            except Exception:
                logger.warning("space_cache: lookup failed; falling back to load", exc_info=True)
                space_cache_key = None
                space_cache_status = "error"

        if not spaces:
            load_start = time.perf_counter()
            spatial_models = {
                filename: ifcopenshell.open(local_paths[filename])
                for filename in request.spatial_files
            }
            load_seconds = time.perf_counter() - load_start

            space_start = time.perf_counter()
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
            _log_phase(
                "spaces_collected",
                seconds=round(space_seconds, 3),
                space_count=len(spaces),
                geometry_failures=space_geometry_failures,
                cache=space_cache_status,
            )

            # Free spatial models as they are no longer needed
            spatial_models.clear()
            gc.collect()

            if space_cache_key is not None:
                space_cache.save(
                    space_cache_key, [_space_to_cache_dict(s) for s in spaces]
                )

        cell_start = time.perf_counter()
        # Cell cache: rebuild Topologic cells from cached BREP strings (~200x
        # faster than rebuilding mesh cells). Keyed additionally by cell_mode +
        # tolerance since the cells depend on them.
        cell_cache_key: Optional[str] = None
        cell_cache_status = "disabled"
        if (
            space_cache.cells_enabled()
            and spatial_shas is not None
            and selected_engine == TopologyEngine.TOPOLOGICPY.value
        ):
            try:
                cell_cache_key = space_cache.build_cell_key(
                    spatial_shas,
                    request.space_query,
                    request.include_zones,
                    tuning.cell_mode,
                    request.tolerance,
                )
                cached_cells = space_cache.load_cells(cell_cache_key)
                if cached_cells is not None:
                    filled = _prefill_cells_from_cache(spaces, cached_cells, stats)
                    cell_cache_status = "hit"
                    _log_phase(
                        "cells_cache_hit",
                        filled=filled,
                        space_count=len(spaces),
                        cache_key=cell_cache_key,
                    )
                else:
                    cell_cache_status = "miss"
            except Exception:
                logger.warning("space_cache: cell lookup failed", exc_info=True)
                cell_cache_key = None
                cell_cache_status = "error"

        topologic_cell_count = _prebuild_space_cells(
            spaces,
            request.tolerance,
            selected_engine,
            stats,
            tuning,
        )
        cell_seconds = time.perf_counter() - cell_start
        _log_phase(
            "cells_prebuilt",
            seconds=round(cell_seconds, 3),
            built=topologic_cell_count,
            prism_cells=stats.prism_cells,
            mesh_cells=stats.mesh_cells,
            mesh_repaired_cells=stats.mesh_repaired_cells,
            hull_cells=stats.hull_cells,
            mesh_faces_skipped=stats.mesh_faces_skipped,
            cell_cache=cell_cache_status,
        )

        # Persist freshly-built cells to the cache on a miss so the next run is fast.
        if cell_cache_key is not None and cell_cache_status == "miss":
            try:
                space_cache.save_cells(cell_cache_key, _cells_to_cache(spaces))
            except Exception:
                logger.warning("space_cache: cell save failed", exc_info=True)

        # Build SpaceIndex if requested
        space_index = None
        if request.use_spatial_index:
            space_index = SpaceIndex(spaces)
            _log_phase("spatial_index_built", space_count=len(spaces))

        # Step 2: Initialize stats and progress variables
        element_results: List[Dict[str, Any]] = []
        stamped_outputs: List[Dict[str, Any]] = []

        matched_count = 0
        ambiguous_count = 0
        total_elements = 0
        candidate_tests = 0
        stamped_count = 0
        skipped_ambiguous_count = 0
        skipped_unmatched_count = 0
        element_geometry_failures = 0

        # Capped samples for summary report
        sample_ambiguous_ids: List[str] = []
        sample_unmatched_ids: List[str] = []

        element_geometry_seconds = 0.0
        match_seconds = 0.0
        stamp_seconds = 0.0
        planned_element_total = 0
        output_basenames = _resolve_output_ifc_basenames(request.element_files)

        # Step 3: Process element files sequentially to keep memory flat
        for file_index, filename in enumerate(request.element_files, start=1):
            _log_phase(
                "element_file_start",
                file_index=file_index,
                file_total=len(request.element_files),
                filename=filename,
            )

            # Load model lazily
            file_load_start = time.perf_counter()
            model = ifcopenshell.open(local_paths[filename])
            load_seconds += time.perf_counter() - file_load_start

            # Collect elements
            file_element_start = time.perf_counter()
            collected_elements, failures = _collect_elements(
                model,
                filename,
                request.element_query,
                settings,
                request.sample_strategy,
                request.max_elements,
                overlap_samples=tuning.overlap_samples,
            )
            element_geometry_failures += failures
            element_geometry_seconds += time.perf_counter() - file_element_start

            file_total = len(collected_elements)
            planned_element_total += file_total
            _log_phase(
                "elements_collected",
                filename=filename,
                element_count=file_total,
                seconds=round(time.perf_counter() - file_element_start, 3),
                geometry_failures=failures,
            )

            file_stamped = 0
            file_skipped_ambiguous = 0
            file_skipped_unmatched = 0
            file_match_start = time.perf_counter()
            pset_cache = _PsetCache(model, request.pset_name) if request.stamp else None

            # Process elements in batches of 5000
            batch_size = 5000
            for i in range(0, file_total, batch_size):
                batch = collected_elements[i : i + batch_size]
                batch_end = min(file_total, i + batch_size)
                batch_started = time.perf_counter()
                _update_progress(
                    total_elements,
                    planned_element_total,
                    filename,
                    phase="matching",
                    elapsed_seconds=time.perf_counter() - start,
                )
                _log_phase(
                    "batch_start",
                    filename=filename,
                    batch_start=i,
                    batch_end=batch_end,
                    processed_total=total_elements,
                    planned_total=planned_element_total,
                )

                for element in batch:
                    resolution = _match_element_resolution(
                        element,
                        spaces,
                        request.tolerance,
                        selected_engine,
                        space_index=space_index,
                        resolve_ambiguous=request.resolve_ambiguous_with_topologicpy,
                        resolve_unmatched=request.resolve_unmatched_with_topologicpy,
                        stats=stats,
                        tuning=tuning,
                    )

                    candidate_tests += len(spaces) if space_index is None else len(space_index.query(element.sample_point, request.tolerance))
                    total_elements += 1

                    if resolution.space is not None:
                        matched_count += 1
                    else:
                        if len(sample_unmatched_ids) < 100:
                            sample_unmatched_ids.append(element.global_id)

                    is_ambiguous = (
                        resolution.space is not None
                        and resolution.status != "Contained"
                        and resolution.candidate_count > 1
                    )
                    if is_ambiguous:
                        ambiguous_count += 1
                        if len(sample_ambiguous_ids) < 100:
                            sample_ambiguous_ids.append(element.global_id)

                    if request.report_detail == "full":
                        element_results.append(_element_payload(element, resolution))

                    if request.stamp:
                        if resolution.space is None:
                            file_skipped_unmatched += 1
                        elif resolution.status != "Contained" and not request.stamp_ambiguous:
                            file_skipped_ambiguous += 1
                        else:
                            file_stamp_start = time.perf_counter()
                            _stamp_element(
                                model,
                                element.element,
                                resolution.space,
                                resolution,
                                request.pset_name,
                                selected_engine,
                                pset_cache=pset_cache,
                            )
                            file_stamped += 1
                            stamped_count += 1
                            stamp_seconds += time.perf_counter() - file_stamp_start

                    processed_now = total_elements
                    if (
                        processed_now > 0
                        and processed_now % tuning.progress_log_every == 0
                    ):
                        elapsed_match = time.perf_counter() - file_match_start
                        rate = processed_now / elapsed_match if elapsed_match > 0 else 0.0
                        _update_progress(
                            processed_now,
                            planned_element_total,
                            filename,
                            phase="matching",
                            elements_per_second=rate,
                            elapsed_seconds=time.perf_counter() - start,
                        )
                        _log_phase(
                            "matching_progress",
                            processed=processed_now,
                            planned_total=planned_element_total,
                            matched=matched_count,
                            ambiguous=ambiguous_count,
                            elements_per_second=round(rate, 2),
                            topologic_distance_calls=stats.topologic_distance_calls,
                            topologic_containment_calls=stats.topologic_containment_calls,
                            bbox_resolutions=stats.bbox_distance_resolutions,
                        )

                batch_elapsed = time.perf_counter() - batch_started
                match_seconds += batch_elapsed
                batch_rate = len(batch) / batch_elapsed if batch_elapsed > 0 else 0.0
                _log_phase(
                    "batch_done",
                    filename=filename,
                    batch_start=i,
                    batch_end=batch_end,
                    batch_size=len(batch),
                    elements_per_second=round(batch_rate, 2),
                    processed_total=total_elements,
                )

                # Free element geom / references inside batch
                for element in batch:
                    element.element = None

            skipped_ambiguous_count += file_skipped_ambiguous
            skipped_unmatched_count += file_skipped_unmatched
            file_elapsed = time.perf_counter() - file_match_start
            _log_phase(
                "element_file_done",
                filename=filename,
                element_count=file_total,
                seconds=round(file_elapsed, 3),
                elements_per_second=round(file_total / file_elapsed, 2) if file_elapsed else file_total,
                stamped=file_stamped,
                skipped_ambiguous=file_skipped_ambiguous,
                skipped_unmatched=file_skipped_unmatched,
            )

            if request.stamp:
                file_write_start = time.perf_counter()
                out_path = _output_ifc_path(
                    request, output_dir, output_basenames[filename]
                )
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                model.write(out_path)
                stamped_outputs.append(
                    {
                        "source_file": filename,
                        "stamped_element_count": file_stamped,
                        "skipped_ambiguous_count": file_skipped_ambiguous,
                        "skipped_unmatched_count": file_skipped_unmatched,
                        "output_path": out_path,
                    }
                )
                stamp_seconds += time.perf_counter() - file_write_start

            # Clean up model references to free memory
            del model
            del collected_elements
            gc.collect()

        # Step 4: Finalize reports and summaries
        elapsed_seconds = time.perf_counter() - start
        _log_phase(
            "complete",
            seconds=round(elapsed_seconds, 3),
            element_count=total_elements,
            matched=matched_count,
            unmatched=total_elements - matched_count,
            ambiguous=ambiguous_count,
            elements_per_second=round(total_elements / elapsed_seconds, 2) if elapsed_seconds else total_elements,
            topologic_distance_calls=stats.topologic_distance_calls,
            topologic_distance_failures=stats.topologic_distance_failures,
            topologic_containment_calls=stats.topologic_containment_calls,
            bbox_resolutions=stats.bbox_distance_resolutions,
        )

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
            "stamp_ambiguous": request.stamp_ambiguous,
            "stamped_count": stamped_count,
            "skipped_ambiguous_count": skipped_ambiguous_count,
            "skipped_unmatched_count": skipped_unmatched_count,
            "space_geometry_failures": space_geometry_failures,
            "element_geometry_failures": element_geometry_failures,
            "cell_mode": tuning.cell_mode,
            "distance_mode": tuning.distance_mode,
            "max_proximate_spaces": tuning.max_proximate_spaces,
            "topologic_distance_calls": stats.topologic_distance_calls,
            "topologic_distance_failures": stats.topologic_distance_failures,
            "topologic_containment_calls": stats.topologic_containment_calls,
            "bbox_distance_resolutions": stats.bbox_distance_resolutions,
            "prism_cell_count": stats.prism_cells,
            "mesh_cell_count": stats.mesh_cells,
            "mesh_repaired_cell_count": stats.mesh_repaired_cells,
            "hull_cell_count": stats.hull_cells,
            "mesh_faces_skipped": stats.mesh_faces_skipped,
            "ambiguous_resolved": stats.ambiguous_resolved,
            "unmatched_resolved": stats.unmatched_resolved,
            "overlap_majority_count": stats.overlap_majority,
            "overlap_geometric_count": stats.overlap_geometric,
            "proximity_forced_count": stats.proximity_forced,
            "legacy_matched_count": stats.legacy_matched,
            "confidence_histogram": stats.confidence_histogram,
            "overlap_resolution": tuning.overlap_resolution,
            "overlap_samples": tuning.overlap_samples,
            "overlap_confidence_margin": tuning.overlap_confidence_margin,
            "overlap_coverage_min": tuning.overlap_coverage_min,
            "hybrid_geometric_fallback": tuning.hybrid_geometric_fallback,
            "space_cache": space_cache_status,
            "cell_cache": cell_cache_status,
        }

        benchmark = {
            "stage_inputs_seconds": round(stage_seconds, 6),
            "load_seconds": round(load_seconds, 6),
            "space_collect_seconds": round(space_seconds, 6),
            "cell_prebuild_seconds": round(cell_seconds, 6),
            "element_geometry_seconds": round(element_geometry_seconds, 6),
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
            "stamped_outputs": stamped_outputs,
        }

        if request.report_detail == "full":
            report["elements"] = element_results
        else:
            report["sample_unmatched_global_ids"] = sample_unmatched_ids
            report["sample_ambiguous_global_ids"] = sample_ambiguous_ids

        _write_json(report_path, report)

        return _finalize_roomstamp_result(
            request=request,
            summary=summary,
            benchmark=benchmark,
            report_path=report_path,
            stamped_outputs=stamped_outputs,
            input_keys=input_keys,
            input_pins=input_pins,
            output_dir=output_dir,
        )
    except Exception:
        logger.exception("Error during topology roomstamp benchmark")
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Ingest scripts dispatcher
# ---------------------------------------------------------------------------


def _run_ingest_core(job_data: dict) -> dict:
    """Run a topologic ingest script to extract graph relationships from IFC.

    Dispatches to a named script in ingest_scripts/ (spaces, spatial, mep, structural).
    Returns standardized relationships JSON.
    """
    from shared.classes import TopologicIngestRequest
    from ingest_scripts import load_script, list_available_scripts
    from pathlib import Path

    request = TopologicIngestRequest(**job_data)
    script_name = request.script
    logger.info("ingest: starting script=%s, files=%s s3=%s", script_name, request.input_files, len(request.input_s3))

    tmpdir = tempfile.mkdtemp(prefix="topo_ingest_")
    try:
        staged_files: List[Path] = []
        staged_basenames: set[str] = set()

        if request.input_s3:
            for idx, ref in enumerate(request.input_s3):
                ref_dict = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
                label = request.input_files[idx] if idx < len(request.input_files) else f"input_{idx}.ifc"
                local_path = os.path.join(tmpdir, os.path.basename(label) or f"input_{idx}.ifc")
                src = str(ref_dict.get("source") or "default").lower()
                if src == "cde" or s3.is_enabled():
                    s3.download_s3_ref_to_path(ref_dict, local_path)
                else:
                    raise RuntimeError("input_s3 requires object storage on the worker.")
                staged_files.append(Path(local_path))
                staged_basenames.add(os.path.basename(local_path))

        # Federated ingest: door/extra models referenced only in input_files (+ input_version_ids).
        remaining = [
            f for f in request.input_files
            if os.path.basename(f) not in staged_basenames
        ]
        if remaining:
            local_paths, _, _ = _stage_inputs(remaining, tmpdir, request)
            for filename in remaining:
                staged_files.append(Path(local_paths[filename]))

        logger.info("ingest: staged %d file(s) for %s", len(staged_files), script_name)

        IngesterClass = load_script(script_name)

        # Support both positional args (list, ifcpatch-style) and kwargs (dict, legacy)
        if isinstance(request.arguments, list):
            from ingest_scripts import resolve_positional_arguments
            kwargs = resolve_positional_arguments(script_name, request.arguments)
        else:
            kwargs = request.arguments

        ingester = IngesterClass(
            ifc_files=staged_files,
            log=logger,
            **kwargs,
        )

        ingester.extract()
        output_data = ingester.build_output(source_files=request.input_files)

        ts = time.strftime("%Y%m%d_%H%M%S")
        output_filename = request.output_file or f"{script_name}_{ts}.relationships.json"
        output_json = json.dumps(output_data, indent=2, default=str)

        result = {
            "success": True,
            "script": script_name,
            "summary": output_data["summary"],
            "relationship_count": len(output_data["relationships"]),
            "element_count": len(output_data["elements"]),
        }

        if s3.is_enabled():
            output_key = f"output/topology/ingest/{output_filename}"
            local_out = os.path.join(tmpdir, output_filename)
            with open(local_out, "w") as f:
                f.write(output_json)

            parent_keys = [s3.normalize_input_key(fn) for fn in request.input_files]
            if not parent_keys and request.input_s3:
                parent_keys = [ref.key for ref in request.input_s3]
            try:
                audit_info = s3.upload_and_audit(
                    local_out,
                    key=output_key,
                    operation=f"topologicpy_ingest_{script_name}",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", key) for key in parent_keys],
                )
                result["storage"] = "s3"
                result["output_key"] = output_key
                result["output_path"] = f"s3://{s3.bucket_name()}/{output_key}"
                result["audit_id"] = audit_info.get("audit_id")
                result["sha256"] = audit_info.get("sha256")
                result["version_id"] = audit_info.get("version_id")
            except Exception as upload_exc:
                logger.warning(
                    "ingest: output upload failed for %s (relationships still returned): %s",
                    script_name,
                    upload_exc,
                )
                result["storage"] = "inline"
                result["output_upload_error"] = str(upload_exc)
        else:
            out_dir = os.path.join(OUTPUT_DIR, "topology", "ingest")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, output_filename)
            with open(out_path, "w") as f:
                f.write(output_json)
            result["storage"] = "filesystem"
            result["output_path"] = out_path

        # Optional side artifacts (e.g. RDF/Turtle from KnowledgeGraphExport).
        # Additive: scripts that don't override get_artifacts() return [] and skip this.
        try:
            artifacts = ingester.get_artifacts() or []
        except Exception:
            logger.warning("ingest: get_artifacts() failed for %s", script_name, exc_info=True)
            artifacts = []
        if artifacts:
            result_artifacts: List[dict] = []
            for art in artifacts:
                try:
                    art_name, art_data, art_ctype = art
                except (ValueError, TypeError):
                    logger.warning("ingest: malformed artifact from %s: %r", script_name, art)
                    continue
                art_bytes = art_data.encode("utf-8") if isinstance(art_data, str) else art_data
                if s3.is_enabled():
                    art_key = f"output/topology/kg/{art_name}"
                    art_local = os.path.join(tmpdir, art_name)
                    with open(art_local, "wb") as f:
                        f.write(art_bytes)
                    try:
                        art_audit = s3.upload_and_audit(
                            art_local,
                            key=art_key,
                            operation=f"topologicpy_ingest_{script_name}_artifact",
                            worker=WORKER_NAME,
                            job_id=_current_job_id(),
                            parents=[("input", key) for key in parent_keys],
                        )
                        result_artifacts.append({
                            "key": art_key,
                            "path": f"s3://{s3.bucket_name()}/{art_key}",
                            "content_type": art_ctype,
                            "bytes": len(art_bytes),
                            "audit_id": art_audit.get("audit_id"),
                            "sha256": art_audit.get("sha256"),
                            "version_id": art_audit.get("version_id"),
                        })
                    except Exception as art_exc:
                        logger.warning("ingest: artifact upload failed for %s: %s", art_name, art_exc)
                else:
                    art_dir = os.path.join(OUTPUT_DIR, "topology", "kg")
                    os.makedirs(art_dir, exist_ok=True)
                    art_path = os.path.join(art_dir, art_name)
                    with open(art_path, "wb") as f:
                        f.write(art_bytes)
                    result_artifacts.append({
                        "path": art_path, "content_type": art_ctype, "bytes": len(art_bytes),
                    })
            if result_artifacts:
                result["artifacts"] = result_artifacts

        result["relationships"] = output_data["relationships"]
        result["elements"] = output_data["elements"]

        logger.info("ingest: completed script=%s, relationships=%d, elements=%d",
                    script_name, result["relationship_count"], result["element_count"])
        return result

    except Exception:
        logger.exception("Error during ingest script=%s", script_name)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Spawn isolation
# ---------------------------------------------------------------------------
#
# The topologicpy worker runs under ``rq.worker.SimpleWorker`` (no work-horse
# fork) because the native topologic/OCCT geometry stack is not fork-safe. The
# downside is that everything runs in the long-lived worker process, so the
# native heap (OCCT cells, ifcopenshell models) and glibc arena fragmentation
# never get returned to the OS — RSS ratchets up across jobs and looks like a
# leak in Dozzle.
#
# The ifcpatch worker hit the same fork-safety wall with ifcopenshell and solved
# it with ``multiprocessing.get_context("spawn")``: a brand-new interpreter (no
# inherited fork state, so no fork-safety crashes) that is torn down after the
# job. When the child exits the kernel reclaims 100% of its memory, giving the
# fork-style reclamation back without the fork hazard. We mirror that pattern
# here. Set ``IFCTOPOLOGY_SPAWN_ISOLATION=0`` to run in-process (debugging).

_ISOLATED_FUNCS = {
    "roomstamp": _run_roomstamp_benchmark_core,
    "ingest": _run_ingest_core,
}


def _spawn_isolation_enabled() -> bool:
    return os.environ.get("IFCTOPOLOGY_SPAWN_ISOLATION", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _isolated_job_worker(result_queue, payload: dict) -> None:
    """Top-level entry for the spawn child. Runs the requested core function and
    returns its result (or a formatted error) over the queue. Kept recipe-
    agnostic so both roomstamp and ingest reuse it."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    wlog = _logging.getLogger("topologicpy.isolated")

    global _ISOLATED_JOB_ID, _ISOLATED_REDIS_URL
    try:
        _ISOLATED_JOB_ID = payload.get("job_id")
        _ISOLATED_REDIS_URL = payload.get("redis_url")
        func_name = payload["func"]
        core = _ISOLATED_FUNCS.get(func_name)
        if core is None:
            raise ValueError(f"unknown isolated func {func_name!r}")
        result = core(payload["job_data"])
        result_queue.put(("ok", result))
    except Exception as e:
        import traceback as _tb

        tb_str = _tb.format_exc()
        try:
            result_queue.put(("err", f"{type(e).__name__}: {e}\n{tb_str}"))
        except Exception:
            pass
        wlog.exception("Isolated topologicpy worker failed")
        raise


def _run_in_spawn_isolation(func_name: str, job_data: dict) -> dict:
    """Run a core function in a ``spawn`` subprocess so all native memory is
    reclaimed when the child exits. A child crash (e.g. OCCT SIGSEGV) kills only
    the child; the SimpleWorker parent survives and raises a clean RuntimeError
    that n8n can retry."""
    ctx = get_context("spawn")
    payload = {
        "func": func_name,
        "job_data": job_data,
        "job_id": _current_job_id(),
        "redis_url": os.environ.get("REDIS_URL"),
    }
    # The result is delivered on a multiprocessing.Queue. ingest results can be
    # large (relationships + elements), so we drain the queue BEFORE join() to
    # avoid the classic feeder-thread deadlock (child blocks flushing a payload
    # bigger than the pipe buffer while the parent waits in join()).
    q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_isolated_job_worker, args=(q, payload))
    proc.start()

    payload_out: Optional[Tuple[str, Any]] = None
    while True:
        try:
            payload_out = q.get(timeout=1.0)
            break
        except Empty:
            if not proc.is_alive():
                try:
                    payload_out = q.get_nowait()
                except Empty:
                    payload_out = None
                break
    proc.join()

    if payload_out is not None:
        status, data = payload_out
        if status == "ok":
            return data
        raise RuntimeError(f"topologicpy {func_name} isolated worker: {data}")

    raise RuntimeError(
        f"topologicpy {func_name} crashed in isolated subprocess "
        f"(exit={proc.exitcode}) with no result"
    )


def run_roomstamp_benchmark(job_data: dict) -> dict:
    """RQ entry point. Runs the benchmark in a spawn-isolated child so native
    topologic/OCCT memory is reclaimed when the job finishes."""
    if not _spawn_isolation_enabled():
        return _run_roomstamp_benchmark_core(job_data)
    return _run_in_spawn_isolation("roomstamp", job_data)


def run_ingest(job_data: dict) -> dict:
    """RQ entry point. Runs the ingest script in a spawn-isolated child so native
    topologic/OCCT memory is reclaimed when the job finishes."""
    if not _spawn_isolation_enabled():
        return _run_ingest_core(job_data)
    return _run_in_spawn_isolation("ingest", job_data)
