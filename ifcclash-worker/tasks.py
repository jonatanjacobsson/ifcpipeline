from shared.classes import IfcClashRequest, ClashSet, ClashFile, ClashMode
import logging
import json
import os
import tempfile
import time
import shutil
import ifcopenshell
import ifcopenshell.util.selector
import ifcopenshell.geom
import ifcopenshell.ifcopenshell_wrapper as _ios_wrap
import multiprocessing
from multiprocessing import get_context
from queue import Empty
from ifcclash.ifcclash import Clasher, ClashSettings
from shared.db_client import save_clash_result
from shared import audit_db
from shared import object_storage as s3
from typing import Tuple, List, Dict, Any, Optional

import bvh_cache
import validation_cache

# Set up logging
logging.basicConfig(level=logging.INFO)

WORKER_NAME = "ifcclash-worker"

# ---------------------------------------------------------------------------
# Hardening knobs (2026-05-14). All three are read from the environment at
# import time so flips do not require a code edit. See ifcpipeline/.env for
# documentation. Default kernel is CGAL because the OCC kernel SIGSEGVs on
# certain real-world IFCs (P1_0001, A1_2b_BIM_XXX_0001_00.ifc, M1_0001_*).
# ---------------------------------------------------------------------------
_VALID_GEOMETRY_LIBRARIES = (
    "opencascade",
    "cgal",
    "cgal-simple",
    "hybrid-cgal-simple-opencascade",
)
IFCCLASH_GEOMETRY_LIBRARY = os.environ.get("IFCCLASH_GEOMETRY_LIBRARY", "cgal")
if IFCCLASH_GEOMETRY_LIBRARY not in _VALID_GEOMETRY_LIBRARIES:
    logging.getLogger(__name__).warning(
        "Unknown IFCCLASH_GEOMETRY_LIBRARY=%r; falling back to 'cgal'. Valid: %s",
        IFCCLASH_GEOMETRY_LIBRARY, _VALID_GEOMETRY_LIBRARIES,
    )
    IFCCLASH_GEOMETRY_LIBRARY = "cgal"

# Default to cpu_count() capped at 8 when the env is unset/empty/invalid.
# Multi-threaded iteration is the single biggest perf knob for ifcclash
# (see ifcopenshell PR #4282 + issue #6905). Spawn isolation
# (IFCCLASH_ISOLATE=1, default) already absorbs SIGSEGVs from the OCC
# kernel's threading races, so the historical "pin to 1" rationale no
# longer applies. The cap of 8 keeps memory bounded on monster hosts
# (issue #6905 — multi-threaded iterator memory blowup scales with
# threads). Set IFCCLASH_ITERATOR_THREADS=1 to roll back without rebuild.
def _resolve_iterator_threads() -> int:
    raw = os.environ.get("IFCCLASH_ITERATOR_THREADS", "").strip()
    fallback = min(os.cpu_count() or 4, 8)
    if not raw:
        return fallback
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "Invalid IFCCLASH_ITERATOR_THREADS=%r; falling back to %d (min(cpu_count,8))",
            raw, fallback,
        )
        return fallback


IFCCLASH_ITERATOR_THREADS = _resolve_iterator_threads()

IFCCLASH_ISOLATE = os.environ.get("IFCCLASH_ISOLATE", "1").strip().lower() not in (
    "0", "false", "no", "off", "",
)


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None

logger = logging.getLogger(__name__)


class IFCValidationError(Exception):
    """Custom exception for IFC validation errors with detailed information."""
    def __init__(self, message: str, file_path: str, error_type: str, details: str = None):
        self.file_path = file_path
        self.error_type = error_type
        self.details = details
        super().__init__(f"{error_type}: {message}")


def _clash_rows_from_report(clash_results):
    """Flatten an ifcclash result tree into `(guid_a, guid_b, distance, kind)`
    tuples for `audit_db.record_clash_pairs`. Defensive: any non-dict,
    missing GlobalId, or unexpected nesting is silently skipped.

    ifcclash's JSON shape has evolved between versions. Common shapes:
      [{"clashes": {guid_key: {"a_global_id": .., "b_global_id": ..,
                               "distance": .., "clash_type": ..}, ...}},
       ...]
    or:
      {"clashes": [{"a": {"GlobalId": ..}, "b": {"GlobalId": ..}, ...}, ...]}
    The function recursively looks for dicts that carry `a_global_id`/`b_global_id`
    (preferred) or `a`/`b` children with `GlobalId`.
    """
    def _walk(node):
        if isinstance(node, dict):
            a = node.get("a_global_id") or _guid_of(node.get("a"))
            b = node.get("b_global_id") or _guid_of(node.get("b"))
            if a and b:
                dist = node.get("distance")
                if not isinstance(dist, (int, float)):
                    dist = None
                kind = node.get("clash_type") or node.get("kind")
                yield (a, b, dist, kind if isinstance(kind, str) else None)
            for v in node.values():
                yield from _walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from _walk(v)
    yield from _walk(clash_results)


def _guid_of(entity):
    if isinstance(entity, dict):
        v = entity.get("GlobalId") or entity.get("global_id") or entity.get("guid")
        return v if isinstance(v, str) and v else None
    if isinstance(entity, str) and len(entity) == 22:
        return entity
    return None


def validate_ifc_file(file_path: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate an IFC file before processing.
    
    Performs two levels of validation:
    1. Header check - file exists and contains ISO-10303-21
    2. Deep validation - can be opened and geometry iterator created
    
    Args:
        file_path: Full path to the IFC file
        
    Returns:
        Tuple of (is_valid, error_message, metadata)
        - is_valid: True if file passed all validation
        - error_message: Description of the error if invalid, empty string if valid
        - metadata: Dictionary with file info (schema, element_count, etc.)
    """
    metadata = {
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "validated": False,
        "schema": None,
        "element_count": None,
    }
    
    # Level 1: Check file exists and has valid IFC header
    if not os.path.exists(file_path):
        return False, f"File not found: '{metadata['file_name']}'", metadata
    
    try:
        with open(file_path, 'rb') as f:
            # Read first 200 bytes to check header
            header = f.read(200).decode('utf-8', errors='ignore')
            if 'ISO-10303-21' not in header:
                return False, f"Invalid IFC file: '{metadata['file_name']}' does not contain a valid IFC header (ISO-10303-21). The file may be corrupted or not a valid IFC file.", metadata
    except Exception as e:
        return False, f"Cannot read file '{metadata['file_name']}': {str(e)}", metadata

    # Level 2: deep validation — but first consult validation_cache. The
    # cache is keyed by sha256(file_contents) + kernel, so a re-run against
    # the same input file skips the ~2-3 s ifcopenshell.open() + iterator
    # probe. Cache hits also auto-promote from the BVH tessellation cache
    # (a file that successfully tessellated is provably valid).
    try:
        cached_hit = validation_cache.lookup(file_path, IFCCLASH_GEOMETRY_LIBRARY)
    except Exception as exc:
        logger.debug("validation_cache: lookup raised for %s: %s", file_path, exc)
        cached_hit = None
    if cached_hit is not None:
        meta = cached_hit.to_metadata(file_path)
        logger.info(
            "validation_cache: HIT for %s (source=%s schema=%s elements=%s sha=%s)",
            metadata['file_name'], cached_hit.source, cached_hit.schema,
            cached_hit.element_count, cached_hit.sha[:12],
        )
        return True, "", meta

    try:
        logger.info(f"Deep validating IFC file: {metadata['file_name']}")
        ifc = ifcopenshell.open(file_path)
        metadata["schema"] = ifc.schema
        
        # Count elements
        try:
            elements = ifc.by_type("IfcProduct")
            metadata["element_count"] = len(elements)
        except:
            metadata["element_count"] = "unknown"
        
        # Test geometry iterator creation - this catches IFC4X3 issues
        logger.info(f"Testing geometry iterator for: {metadata['file_name']}")
        settings = ifcopenshell.geom.settings()
        
        # Try to create an iterator with a small subset to test
        try:
            # Get a few elements to test with
            test_elements = ifc.by_type("IfcProduct")[:5] if ifc.by_type("IfcProduct") else []
            if test_elements:
                iterator = ifcopenshell.geom.iterator(settings, ifc, include=test_elements)
                # Just initialize, don't iterate
                logger.info(f"Geometry iterator created successfully for: {metadata['file_name']}")
        except TypeError as e:
            error_msg = str(e)
            if "AGGREGATE OF STRING" in error_msg:
                return False, f"IFC schema compatibility issue with '{metadata['file_name']}' (schema: {metadata['schema']}): The file uses IFC attributes that are incompatible with the current geometry processor. This is a known issue with some IFC4X3 files. Error: {error_msg}", metadata
            else:
                # Other TypeError - might still be processable, log warning but continue
                logger.warning(f"Geometry iterator warning for {metadata['file_name']}: {error_msg}")
        except Exception as e:
            # Log but don't fail - some files might still work for clash detection
            logger.warning(f"Geometry iterator test warning for {metadata['file_name']}: {str(e)}")
        
        metadata["validated"] = True
        logger.info(f"Validation passed for: {metadata['file_name']} (schema: {metadata['schema']}, elements: {metadata['element_count']})")

        # Persist into validation_cache so the next clash against this same
        # input file (same sha + kernel) skips the deep-validation pass.
        # Failure is non-fatal — `store` swallows everything.
        try:
            stored = validation_cache.store(
                file_path,
                IFCCLASH_GEOMETRY_LIBRARY,
                metadata["schema"],
                metadata["element_count"],
            )
            if stored is not None:
                metadata["sha256"] = stored.sha
                metadata["validation_source"] = "fresh"
        except Exception as exc:
            logger.debug("validation_cache: store raised for %s: %s", file_path, exc)

        return True, "", metadata
        
    except ifcopenshell.Error as e:
        error_msg = str(e)
        if "Unable to parse IFC SPF header" in error_msg:
            return False, f"Corrupted or invalid IFC file: '{metadata['file_name']}' cannot be parsed. The file header is malformed or the file is not a valid IFC.", metadata
        return False, f"IFC parsing error for '{metadata['file_name']}': {error_msg}", metadata
    except Exception as e:
        return False, f"Failed to validate IFC file '{metadata['file_name']}': {str(e)}", metadata


def validate_all_clash_files(clash_sets: List, models_dir: str) -> Tuple[bool, List[str], List[Dict]]:
    """
    Validate all IFC files in the clash sets before processing.
    
    Args:
        clash_sets: List of ClashSet objects
        models_dir: Base directory for model files
        
    Returns:
        Tuple of (all_valid, error_messages, file_metadata)
    """
    errors = []
    metadata_list = []
    validated_files = set()  # Avoid re-validating the same file
    
    for clash_set in clash_sets:
        for file in clash_set.a + clash_set.b:
            file_path = os.path.join(models_dir, file.file)
            
            # Skip if already validated
            if file_path in validated_files:
                continue
            validated_files.add(file_path)
            
            is_valid, error_msg, metadata = validate_ifc_file(file_path)
            metadata_list.append(metadata)
            
            if not is_valid:
                errors.append(error_msg)
                logger.error(f"Validation failed: {error_msg}")
    
    return len(errors) == 0, errors, metadata_list

# Define a custom clasher class for better logging
class CustomClashSettings(ClashSettings):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

class CustomClasher(Clasher):

    def __init__(self, settings):
        super().__init__(settings)
        self.logger = logging.getLogger(__name__)
        if not hasattr(self.settings, 'logger') or self.settings.logger is None:
            self.settings.logger = self.logger
        self.cache_lookups = []
        self._group_shape_counts: Dict[str, int] = {}

    def process_clash_set(self, clash_set) -> None:
        import ifcopenshell
        self._group_shape_counts = {"a": 0, "b": 0}
        self.tree = ifcopenshell.geom.tree()
        self.create_group("a")
        for source in clash_set["a"]:
            source["ifc"] = self.load_ifc(source["file"])
            self.add_collision_objects("a", source["ifc"], source)

        if "b" in clash_set and clash_set["b"]:
            self.create_group("b")
            for source in clash_set["b"]:
                source["ifc"] = self.load_ifc(source["file"])
                self.add_collision_objects("b", source["ifc"], source)
            b = "b"
        else:
            b = "a"

        # CGAL/ifcopenshell SIGSEGV when clash_*_many runs with an empty BVH
        # (0 selected elements, or elements present but no tessellated shapes).
        a_meta = list(self.groups["a"]["elements"].values())
        b_meta = list(self.groups[b]["elements"].values())
        a_shapes = self._group_shape_counts.get("a", 0)
        b_shapes = self._group_shape_counts.get(b, 0)

        if not a_meta or not b_meta or a_shapes == 0 or b_shapes == 0:
            self.logger.warning(
                "Skipping clash detection for %r: group 'a' meta=%d shapes=%d, "
                "group '%s' meta=%d shapes=%d (empty geometry — CGAL SIGSEGV guard)",
                clash_set.get("name"),
                len(a_meta),
                a_shapes,
                b,
                len(b_meta),
                b_shapes,
            )
            results = []
        else:
            mode = clash_set["mode"]
            if mode == "intersection":
                assert "tolerance" in clash_set and "check_all" in clash_set
                results = self.tree.clash_intersection_many(
                    a_meta,
                    b_meta,
                    tolerance=clash_set["tolerance"],
                    check_all=clash_set["check_all"],
                )
            elif mode == "collision":
                assert "allow_touching" in clash_set
                results = self.tree.clash_collision_many(
                    a_meta,
                    b_meta,
                    allow_touching=clash_set["allow_touching"],
                )
            elif mode == "clearance":
                assert "clearance" in clash_set and "check_all" in clash_set
                results = self.tree.clash_clearance_many(
                    a_meta,
                    b_meta,
                    clearance=clash_set["clearance"],
                    check_all=clash_set["check_all"],
                )
            else:
                from typing import assert_never
                assert_never(mode)

        from ifcclash.ifcclash import ClashResult
        processed_results = {}
        for result in results:
            element1 = result.a
            element2 = result.b

            processed_results[f"{element1.get_argument(0)}-{element2.get_argument(0)}"] = ClashResult(
                a_global_id=element1.get_argument(0),
                b_global_id=element2.get_argument(0),
                a_ifc_class=element1.is_a(),
                b_ifc_class=element2.is_a(),
                a_name=element1.get_argument(2),
                b_name=element2.get_argument(2),
                type=self.tree.get_clash_type(result.clash_type),
                p1=list(result.p1),
                p2=list(result.p2),
                distance=result.distance,
            )
        clash_set["clashes"] = processed_results
        self.logger.info(f"Found clashes: {len(processed_results.keys())}")

    def add_collision_objects(self, name, ifc_file, source):
        """Override of upstream ifcclash.add_collision_objects to address
        two crash classes seen in production:

        1. ``include=set(...)`` is a TypeError on certain IFC files because
           the C++ wrapper expects a ``list[entity_instance]`` /
           ``list[str]`` (see iterator signature in ifcopenshell 0.8.5).
           Coerce to ``list`` defensively.
        2. Default kernel ``opencascade`` SIGSEGVs on `P1_0001`,
           `A1_2b_BIM_XXX_0001_00.ifc`, `M1_0001_*.ifc` and similar.
           Switch to ``IFCCLASH_GEOMETRY_LIBRARY`` (default ``cgal``).
        3. ``num_threads = multiprocessing.cpu_count()`` makes any future
           SIGSEGV harder to localise and may race the OCC kernel into
           inconsistent state on borderline geometry. Pin to
           ``IFCCLASH_ITERATOR_THREADS`` (default 1).

        Body otherwise mirrors upstream
        ``IfcOpenshell/src/ifcclash/ifcclash/ifcclash.py`` ``add_collision_objects``
        with one extension: when ``IFCCLASH_BVH_CACHE=on`` the iterator is
        attached to a sha-keyed ``HdfSerializer`` so per-element tessellation
        is read from a local + MinIO-backed cache on back-to-back calls
        against the same input file. See ``bvh_cache.py`` for the policy and
        ``Tree finished`` log line for the wallclock savings (cache hits drop
        the per-element loop from O(seconds) to O(milliseconds)).
        """
        assert self.tree
        mode = source.get("mode")
        selector = source.get("selector")
        source_file_path = source.get("file")
        start = time.time()
        self.settings.logger.info(
            "Creating iterator (kernel=%s, threads=%d)",
            IFCCLASH_GEOMETRY_LIBRARY,
            IFCCLASH_ITERATOR_THREADS,
        )

        if not mode or mode == "a" or not selector:
            elements = set(ifc_file.by_type("IfcElement"))
            elements -= set(ifc_file.by_type("IfcFeatureElement"))
        elif mode == "e":
            elements = set(ifc_file.by_type("IfcElement"))
            elements -= set(ifc_file.by_type("IfcFeatureElement"))
            elements -= set(ifcopenshell.util.selector.filter_elements(ifc_file, selector))
        elif mode == "i":
            elements = set(ifcopenshell.util.selector.filter_elements(ifc_file, selector))
        else:
            raise ValueError(f"Unknown clash source mode: {mode!r}")

        iterator = ifcopenshell.geom.iterator(
            self.geom_settings,
            ifc_file,
            IFCCLASH_ITERATOR_THREADS,
            include=list(elements),
            geometry_library=IFCCLASH_GEOMETRY_LIBRARY,
        )
        self.settings.logger.info(f"Iterator creation finished {time.time() - start}")

        # ------------------------------------------------------------------
        # BVH/tessellation cache (IFCCLASH_BVH_CACHE=on). Failure path is
        # always "no cache attached" — the cache can never block clash.
        # ------------------------------------------------------------------
        cache_lookup = None
        cache_serializer = None
        if bvh_cache.is_enabled() and source_file_path:
            try:
                cache_lookup = bvh_cache.prewarm(source_file_path, IFCCLASH_GEOMETRY_LIBRARY)
            except Exception as exc:
                self.logger.warning(
                    "bvh_cache: prewarm raised for %s: %s", source_file_path, exc,
                )
                cache_lookup = None

            if cache_lookup is not None and cache_lookup.local_path is not None:
                try:
                    ser_settings = _ios_wrap.SerializerSettings()
                    cache_serializer = _ios_wrap.HdfSerializer(
                        str(cache_lookup.local_path),
                        self.geom_settings,
                        ser_settings,
                        False,
                    )
                    iterator.set_cache(cache_serializer)
                    self.logger.info(
                        "bvh_cache[%s]: %s source=%s pre_size=%d sha_ms=%.1f dl_ms=%.1f path=%s",
                        name,
                        "warm" if cache_lookup.is_warm() else "cold",
                        cache_lookup.source,
                        cache_lookup.pre_size,
                        cache_lookup.sha_ms,
                        cache_lookup.download_ms,
                        cache_lookup.local_path,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "bvh_cache: HdfSerializer attach failed for %s: %s — falling through to no-cache",
                        source_file_path, exc,
                    )
                    cache_serializer = None

        start = time.time()
        self.logger.info(f"Adding objects {name} ({len(elements)} elements)")
        if not iterator.initialize():
            self.logger.warning(
                "Iterator returned no shapes for group %s (kernel=%s); group will be empty",
                name, IFCCLASH_GEOMETRY_LIBRARY,
            )
        else:
            while True:
                self.tree.add_element(iterator.get())
                self._group_shape_counts[name] = self._group_shape_counts.get(name, 0) + 1
                if not iterator.next():
                    break
        tree_ms = (time.time() - start) * 1000.0
        self.logger.info(f"Tree finished {tree_ms / 1000.0}")

        # Finalize + push cache before recording element metadata so the
        # MinIO upload runs in the same "geometry" critical section the user
        # is timing in `Tree finished`. Errors are swallowed.
        if cache_serializer is not None:
            try:
                cache_serializer.finalize()
            except Exception as exc:
                self.logger.warning(
                    "bvh_cache: HdfSerializer.finalize() raised for %s: %s",
                    source_file_path, exc,
                )
        if cache_lookup is not None and cache_lookup.local_path is not None:
            # Save the tree_ms so we can log it later
            cache_lookup.tree_ms = tree_ms
            cache_lookup.name = name
            self.cache_lookups.append(cache_lookup)

        # Periodic LRU eviction so the cache dir stays bounded.
        try:
            bvh_cache.maybe_evict()
        except Exception as exc:
            self.logger.debug("bvh_cache: maybe_evict raised: %s", exc)

        start = time.time()
        self.groups[name]["elements"].update({e.GlobalId: e for e in elements})
        self.logger.info(f"Element metadata finished {time.time() - start}")

# Function for preprocessing clash data (used for smart grouping)
def preprocess_clash_data(clash_sets):
    for clash_set in clash_sets:
        clashes = clash_set["clashes"]
        for clash in clashes.values():
            p1 = clash["p1"]
            p2 = clash["p2"]
            # Calculate the midpoint and add it as the "position" key
            clash["position"] = [(p1[i] + p2[i]) / 2 for i in range(3)]
    return clash_sets


KNOWN_GEOMETRY_WARNING_MARKERS = (
    "AGGREGATE OF STRING needs a python sequence of strs",
)


def _is_known_geometry_warning(exc: Exception) -> bool:
    message = str(exc)
    return any(marker in message for marker in KNOWN_GEOMETRY_WARNING_MARKERS)


def _warning_clash_report(request: IfcClashRequest, exc: Exception, file_metadata: List[Dict]) -> List[Dict]:
    """Build a zero-clash report for known IfcOpenShell geometry incompatibilities."""
    return [
        {
            "name": clash_set.name,
            "clashes": {},
            "warning": True,
            "warning_type": "ifcopenshell_geometry_compatibility",
            "warning_message": str(exc),
            "warning_details": (
                "IfcOpenShell could not construct a geometry iterator for one or "
                "more IFC files in this clash set. The clash set was skipped."
            ),
            "files": file_metadata,
            "mode": request.mode.value,
            "tolerance": request.tolerance,
            "smart_grouping": request.smart_grouping,
            "max_cluster_distance": request.max_cluster_distance,
        }
        for clash_set in request.clash_sets
    ]


def _run_clash_core(
    request: IfcClashRequest,
    models_dir: str,
    output_path: str,
    file_metadata: List[Dict],
) -> Dict[str, Any]:
    """Run the geometry-heavy phase of clash detection: build clasher, call
    ``clasher.clash()``, do smart grouping, and export the JSON report to
    ``output_path``. Returns a small status dict for the caller; the actual
    clash report is on disk at ``output_path``.

    This function is invoked **either** in-process (when
    ``IFCCLASH_ISOLATE`` is disabled) **or** inside a
    ``multiprocessing.spawn`` child (the default). Anything that touches
    ``ifcopenshell.geom`` / ``ifcopenshell.entity_instance`` happens here so
    a SIGSEGV inside ``_ifcopenshell_wrapper`` can be confined to the child
    process.
    """
    settings = CustomClashSettings()
    settings.output = output_path
    logger.info(f"Clash output will be saved to: {output_path}")

    clasher = CustomClasher(settings)

    for clash_set in request.clash_sets:
        clasher_set = {
            "name": clash_set.name,
            "a": [],
            "b": [],
            "tolerance": request.tolerance,
            "mode": request.mode.value,
            "check_all": request.check_all,
            "allow_touching": request.allow_touching,
            "clearance": request.clearance,
        }

        logger.info(f"Setting up clash set '{clash_set.name}' with mode: {request.mode.value}")

        if request.mode == ClashMode.CLEARANCE and request.clearance <= 0:
            raise ValueError("Clearance value must be greater than 0 when using clearance mode")

        for side in ('a', 'b'):
            for file in getattr(clash_set, side):
                file_path = os.path.join(models_dir, file.file)
                logger.info(f"Adding file to clash set: {file_path}")
                clasher_set[side].append({
                    "file": file_path,
                    "mode": file.mode,
                    "selector": file.selector,
                })

        clasher.clash_sets.append(clasher_set)

    start_time = time.time()
    logger.info("Starting clash detection")
    warning_report_written = False
    try:
        clasher.clash()
    except TypeError as e:
        if not _is_known_geometry_warning(e):
            raise
        logger.warning(
            "Known IfcOpenShell geometry incompatibility; writing warning report: %s",
            e,
            exc_info=True,
        )
        warning_report = _warning_clash_report(request, e, file_metadata)
        with open(output_path, "w") as json_file:
            json.dump(warning_report, json_file, indent=4)
        warning_report_written = True

    if warning_report_written:
        logger.info("Skipping smart grouping/export because a warning report was written")
    else:
        logger.info(f"Smart grouping? {request.smart_grouping}")
        if request.smart_grouping:
            logger.info("Starting Smart Clashes....")
            try:
                preprocessed_clash_sets = preprocess_clash_data(clasher.clash_sets)
                clasher.smart_group_clashes(preprocessed_clash_sets, request.max_cluster_distance)
            except Exception as e:
                logger.error(f"Error during smart grouping: {str(e)}")
                logger.info("Continuing without smart grouping")
        else:
            logger.info("Skipping Smart Clashes (disabled)")

        logger.info("Exporting clash results")
        try:
            clasher.export()
        except AttributeError:
            logger.info("Using export_json instead of export")
            clasher.export_json(output_path)

    execution_time = time.time() - start_time
    logger.info(f"Clash detection and export completed in {execution_time:.2f} seconds")

    return {
        "warning_report_written": warning_report_written,
        "execution_time": execution_time,
        "cache_lookups": getattr(clasher, "cache_lookups", []),
    }


def _isolated_clash_worker(result_queue, payload: dict) -> None:
    """Top-level entry for ``multiprocessing.get_context('spawn').Process``.

    Mirrors the proven pattern in ``ifcpatch-worker/tasks.py``
    (``_isolated_recipe_worker``). A SIGSEGV inside
    ``_ifcopenshell_wrapper`` here kills only this child; the rq work-horse
    in the parent stays alive and converts the non-zero exit into a
    retryable RuntimeError.

    Must be a top-level function (importable by name) so the spawn context
    can locate it after re-importing the module in the child.
    """
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    wlog = _logging.getLogger("ifcclash.isolated_clash")
    try:
        env_overrides = payload.get("env_overrides") or {}
        for k, v in env_overrides.items():
            os.environ[k] = str(v)
        if env_overrides:
            wlog.info("Isolated clash env overrides: %s", env_overrides)

        request = IfcClashRequest(**payload["request"])
        status = _run_clash_core(
            request,
            payload["models_dir"],
            payload["output_path"],
            payload["file_metadata"],
        )
        result_queue.put(("ok", status))
    except Exception as e:
        import traceback as _tb

        tb_str = _tb.format_exc()
        try:
            result_queue.put(("err", f"{type(e).__name__}: {e}\n{tb_str}"))
        except Exception:
            pass
        wlog.exception("Isolated clash worker failed")
        raise


def _run_clash_in_spawn_isolation(
    request: IfcClashRequest,
    models_dir: str,
    output_path: str,
    file_metadata: List[Dict],
    *,
    env_overrides: Optional[dict] = None,
    label: str = "default",
) -> Dict[str, Any]:
    """Run ``_run_clash_core`` in a ``multiprocessing.get_context('spawn')``
    subprocess. A SIGSEGV inside ifcopenshell kills only the spawned child;
    the rq work-horse survives and raises a retryable RuntimeError that
    matches ``RETRYABLE_ERROR_PATTERNS`` in the n8n custom node so n8n can
    retry / route to ``onError: continueErrorOutput``.
    """
    ctx = get_context("spawn")
    if hasattr(request, "model_dump"):
        request_dict = request.model_dump(mode="json")
    else:
        request_dict = request.dict()

    payload = {
        "request": request_dict,
        "models_dir": models_dir,
        "output_path": output_path,
        "file_metadata": file_metadata,
        "env_overrides": env_overrides or {},
    }

    q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_isolated_clash_worker, args=(q, payload))
    logger.info(
        "Spawning isolated clash subprocess (label=%s, kernel=%s, threads=%d)",
        label, IFCCLASH_GEOMETRY_LIBRARY, IFCCLASH_ITERATOR_THREADS,
    )
    proc.start()
    proc.join()

    if proc.exitcode == 0:
        try:
            status, data = q.get(timeout=60)
        except Empty as exc:
            raise RuntimeError(
                "ifcclash isolated worker exited 0 but sent no result"
            ) from exc
        if status == "ok":
            return data
        raise RuntimeError(f"ifcclash isolated worker reported error: {data}")

    child_err: Optional[str] = None
    try:
        status, data = q.get_nowait()
        if status == "err":
            child_err = data
    except Empty:
        pass

    # Normalise the exit code into the same wording that RQ uses when the
    # work-horse itself dies, so RETRYABLE_ERROR_PATTERNS in
    # n8n-nodes-ifcpipeline/nodes/shared/GenericFunctions.ts (matches
    # /Work-horse terminated/i and /signal \d+/i) classifies this as
    # retryable.
    exit_code = proc.exitcode
    if exit_code is not None and exit_code < 0:
        signal_num = -exit_code
        signal_descr = f"signal {signal_num}"
        waitpid_value = 128 + signal_num
    else:
        signal_descr = f"exit code {exit_code}"
        waitpid_value = exit_code if exit_code is not None else -1

    raise RuntimeError(
        f"ifcclash isolated subprocess: Work-horse terminated unexpectedly; "
        f"waitpid returned {waitpid_value} ({signal_descr}); strategy={label!r}"
        + (f"; child={child_err!r}" if child_err else "")
    )

def run_ifcclash_detection(job_data: dict) -> dict:
    """
    Process an IFC clash detection job
    
    Args:
        job_data: Dictionary containing the job parameters
        
    Returns:
        Dictionary containing the job results
    """
    try:
        # Parse the request from the job data
        request = IfcClashRequest(**job_data)

        # --- Object-storage staging -------------------------------------
        # In S3 mode, download every distinct IFC into a tempdir, rename the
        # ClashFile entries to point at the local copies, and plan to upload
        # the final JSON report to the bucket.
        s3_ctx = None
        if s3.is_enabled():
            tmp_models = tempfile.mkdtemp(prefix="ifcclash-in-")
            tmp_out = tempfile.mkdtemp(prefix="ifcclash-out-")
            output_key = s3.normalize_output_key(request.output_filename, "clash")
            models_dir = tmp_models
            output_dir = tmp_out
            output_path = os.path.join(output_dir, os.path.basename(output_key))

            seen: Dict[str, str] = {}
            input_keys: List[str] = []
            for clash_set in request.clash_sets:
                for cf in clash_set.a + clash_set.b:
                    if cf.file in seen:
                        cf.file = seen[cf.file]
                        continue
                    key = s3.normalize_input_key(cf.file)
                    input_keys.append(key)
                    local_name = os.path.basename(key) or f"clash-in-{len(seen)}.ifc"
                    local_path = os.path.join(tmp_models, local_name)
                    if not os.path.exists(local_path):
                        s3.download_to_path(key, local_path)
                    seen[cf.file] = local_name
                    cf.file = local_name  # ClashFile.file is now relative to models_dir
            s3_ctx = {
                "tmp_models": tmp_models,
                "tmp_out": tmp_out,
                "output_key": output_key,
                "input_keys": input_keys,
            }
            logger.info(
                "[s3] staged %d clash input file(s) under %s, output → s3://%s/%s",
                len(seen), tmp_models, s3.bucket_name(), output_key,
            )
        else:
            models_dir = "/uploads"
            output_dir = "/output/clash"
            output_path = os.path.join(output_dir, request.output_filename)

        os.makedirs(output_dir, exist_ok=True)

        logger.info(f"Starting clash detection for {len(request.clash_sets)} clash sets")

        # === PRE-VALIDATION STEP ===
        # Validate all IFC files before processing to fail fast with clear error messages
        logger.info("Validating all IFC files before clash detection...")
        all_valid, validation_errors, file_metadata = validate_all_clash_files(
            request.clash_sets, models_dir
        )
        
        if not all_valid:
            # Create a detailed error message
            error_summary = f"Validation failed for {len(validation_errors)} file(s):\n"
            for i, err in enumerate(validation_errors, 1):
                error_summary += f"  {i}. {err}\n"
            
            logger.error(error_summary)
            
            # Raise a clear validation error
            raise IFCValidationError(
                message=f"{len(validation_errors)} file(s) failed validation",
                file_path=", ".join([m["file_name"] for m in file_metadata if not m["validated"]]),
                error_type="IFC Validation Error",
                details=error_summary
            )
        
        # Log successful validation
        validated_count = len([m for m in file_metadata if m["validated"]])
        schemas_found = set(m["schema"] for m in file_metadata if m["schema"])
        logger.info(f"All {validated_count} IFC file(s) validated successfully. Schemas: {', '.join(schemas_found)}")

        # ---- geometry-heavy phase: in-process or spawn-isolated ------------
        # Anything that touches ifcopenshell.geom / entity_instance happens in
        # _run_clash_core. When IFCCLASH_ISOLATE=1 (default) we run that
        # function in a multiprocessing.spawn child so a SIGSEGV inside
        # _ifcopenshell_wrapper kills only the child, not the rq work-horse.
        if IFCCLASH_ISOLATE:
            logger.info(
                "Running clash detection in spawn-isolated subprocess "
                "(kernel=%s, threads=%d)",
                IFCCLASH_GEOMETRY_LIBRARY, IFCCLASH_ITERATOR_THREADS,
            )
            data = _run_clash_in_spawn_isolation(
                request,
                models_dir,
                output_path,
                file_metadata,
                label=f"{IFCCLASH_GEOMETRY_LIBRARY}-{IFCCLASH_ITERATOR_THREADS}t",
            )
        else:
            logger.info(
                "Running clash detection in-process (IFCCLASH_ISOLATE=0; "
                "kernel=%s, threads=%d)",
                IFCCLASH_GEOMETRY_LIBRARY, IFCCLASH_ITERATOR_THREADS,
            )
            data = _run_clash_core(
                request, models_dir, output_path, file_metadata,
            )


        # Sync BVH cache to MinIO AFTER the subprocess has exited.
        # This ensures the HDF5 file is fully closed and flushed by the OS / C++ destructors.
        cache_lookups = data.get("cache_lookups", [])
        for lookup in cache_lookups:
            try:
                sync_status = bvh_cache.sync_to_minio(lookup)
                post_size = lookup.local_path.stat().st_size if lookup.local_path.exists() else 0
                all_hit = (
                    lookup.pre_size > 0
                    and post_size == lookup.pre_size
                )
                logger.info(
                    "bvh_cache[%s]: outcome=%s pre=%d post=%d tree_ms=%.0f upload=%s",
                    getattr(lookup, "name", "unknown"),
                    "all-hit" if all_hit else "partial-or-miss",
                    lookup.pre_size,
                    post_size,
                    getattr(lookup, "tree_ms", 0.0),
                    sync_status,
                )
            except Exception as exc:
                logger.warning("bvh_cache: sync_to_minio raised: %s", exc)

        # Read the results from the output file
        try:
            with open(output_path, 'r') as json_file:
                clash_results = json.load(json_file)
            
            # Count clashes
            clash_count = 0
            clash_set_names = []
            for clash_set in clash_results:
                clash_count += len(clash_set.get("clashes", {}))
                clash_set_names.append(clash_set.get("name", "Unnamed"))
            has_warning = any(clash_set.get("warning") for clash_set in clash_results)
            
            # Create a comma-separated string of clash set names
            clash_set_name = ", ".join(clash_set_names)
            
            # Save to PostgreSQL
            logger.info("Saving clash result to PostgreSQL database")
            db_id = save_clash_result(
                clash_set_name=clash_set_name,
                output_filename=output_path,
                clash_count=clash_count,
                clash_data=clash_results,
                original_clash_id=None  # Set to None for new clash sets
            )
            
            result = {
                "success": True,
                "warning": has_warning,
                "result": clash_results,
                "clash_count": clash_count,
                "output_path": output_path
            }

            if s3_ctx is not None and os.path.exists(output_path):
                audit = s3.upload_and_audit(
                    output_path,
                    key=s3_ctx["output_key"],
                    operation="ifcclash",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", k) for k in s3_ctx["input_keys"]],
                    metadata={
                        "clash_count": clash_count,
                        "clash_set_name": clash_set_name,
                        "mode": request.mode.value,
                        "tolerance": request.tolerance,
                        "smart_grouping": request.smart_grouping,
                    },
                    content_type="application/json",
                )
                if audit.get("audit_id"):
                    try:
                        rows = list(_clash_rows_from_report(clash_results))
                        if rows:
                            audit_db.record_clash_pairs(audit["audit_id"], rows)
                    except Exception as e:
                        logger.warning("clash_pairs write failed: %s", e)
                result.update({
                    "storage": "s3",
                    "bucket": s3.bucket_name(),
                    "output_key": s3_ctx["output_key"],
                    "output_path": f"s3://{s3.bucket_name()}/{s3_ctx['output_key']}",
                    "sha256": audit["sha256"],
                    "size_bytes": audit["size_bytes"],
                    "audit_id": audit["audit_id"],
                })
                shutil.rmtree(s3_ctx["tmp_models"], ignore_errors=True)
                shutil.rmtree(s3_ctx["tmp_out"], ignore_errors=True)

            if db_id:
                result["db_id"] = db_id

            return result
        except Exception as e:
            logger.error(f"Error reading result file: {str(e)}")
            fallback_result = {
                "success": True,
                "message": "Clash detection completed but result file could not be read",
                "output_path": output_path,
            }
            if s3_ctx is not None:
                shutil.rmtree(s3_ctx["tmp_models"], ignore_errors=True)
                shutil.rmtree(s3_ctx["tmp_out"], ignore_errors=True)
            return fallback_result

    except Exception as e:
        logger.error(f"Error during clash detection: {str(e)}", exc_info=True)
        raise