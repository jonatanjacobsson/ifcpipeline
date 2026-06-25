"""Topologic ingest scripts — selectable graph extraction from IFC models.

Each script is a Python module exporting an `Ingester` class that extracts
discipline-specific graph relationships using TopologicPy and IfcOpenShell.

Contract:
    - `__init__(self, ifc_files, logger, **kwargs)` — receive staged paths + args
    - `extract()` — run extraction, populate internal state
    - `get_relationships()` — return list of edge dicts
    - `get_elements()` — return list of discovered element dicts (optional)
    - `get_summary()` — return execution summary dict
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def ifc_schema(ifc) -> str:
    """Return the IFC schema identifier (e.g. IFC2X3, IFC4)."""
    return str(getattr(ifc, "schema", "") or "")


def safe_by_type(ifc, type_name: str) -> List:
    """Query entities by IFC type, returning [] when the class is absent in the schema."""
    try:
        return list(ifc.by_type(type_name))
    except RuntimeError:
        return []


def safe_by_types(ifc, type_names: List[str]) -> List:
    """Union of safe_by_type results for each type name (deduped by entity id)."""
    seen_ids: set = set()
    result: List = []
    for type_name in type_names:
        for entity in safe_by_type(ifc, type_name):
            eid = entity.id()
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            result.append(entity)
    return result


def default_mep_system_types(ifc) -> List[str]:
    """IFC classes to query for MEP distribution systems (schema-dependent)."""
    if ifc_schema(ifc).upper() == "IFC2X3":
        return ["IfcSystem"]
    return ["IfcDistributionSystem"]


@dataclass
class Relationship:
    subject_global_id: str
    object_global_id: str
    relationship_family: str
    relationship_type: str
    confidence: float = 1.0
    source_kind: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "subject_global_id": self.subject_global_id,
            "object_global_id": self.object_global_id,
            "relationship_family": self.relationship_family,
            "relationship_type": self.relationship_type,
            "confidence": self.confidence,
            "source_kind": self.source_kind,
            "evidence": self.evidence,
        }


@dataclass
class Element:
    global_id: str
    ifc_class: str
    name: str = ""
    storey: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "global_id": self.global_id,
            "ifc_class": self.ifc_class,
            "name": self.name,
        }
        if self.storey:
            d["storey"] = self.storey
        if self.extra:
            d.update(self.extra)
        return d


class Ingester(ABC):
    """Base class for topologic ingest scripts."""

    SCRIPT_NAME: str = ""
    DESCRIPTION: str = ""

    def __init__(self, ifc_files: List[Path], log: logging.Logger, **kwargs: Any):
        self.ifc_files = ifc_files
        self.log = log
        self.kwargs = kwargs
        self._relationships: List[Relationship] = []
        self._elements: List[Element] = []
        self._summary: Dict[str, Any] = {}

    @abstractmethod
    def extract(self) -> None:
        """Run extraction. Populate self._relationships and self._elements."""
        ...

    def get_relationships(self) -> List[dict]:
        return [r.to_dict() for r in self._relationships]

    def get_elements(self) -> List[dict]:
        return [e.to_dict() for e in self._elements]

    def get_summary(self) -> dict:
        return {
            "script": self.SCRIPT_NAME,
            "element_count": len(self._elements),
            "relationship_count": len(self._relationships),
            **self._summary,
        }

    def build_output(self, source_files: List[str]) -> dict:
        """Build the standardized output JSON."""
        return {
            "script": self.SCRIPT_NAME,
            "version": "1.0",
            "source_files": source_files,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": self.get_summary(),
            "elements": self.get_elements(),
            "relationships": self.get_relationships(),
        }

    def get_artifacts(self) -> List[Tuple[str, Any, str]]:
        """Optional non-JSON side artifacts to persist alongside the relationships JSON.

        Returns a list of ``(filename, data, content_type)`` tuples where ``data`` is
        ``str`` or ``bytes``. Default: none. Scripts that emit a derived artifact —
        e.g. ``KnowledgeGraphExport`` writing RDF/Turtle — override this. The worker
        uploads each to ``output/topology/kg/<filename>`` with the same audit lineage
        as the relationships JSON.
        """
        return []


@dataclass
class ScriptParameter:
    """Descriptor for a single ingest script parameter."""
    name: str
    type: str
    description: str
    required: bool
    default: Optional[Any] = None

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }
        if not self.required:
            d["default"] = self.default
        return d


@dataclass
class ScriptInfo:
    """Full descriptor for an ingest script (ifcpatch-style metadata)."""
    name: str
    description: str
    parameters: List[ScriptParameter]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
        }


def _parse_docstring_params(docstring: str) -> Dict[str, str]:
    """Extract :param name: description mappings from a docstring."""
    params: Dict[str, str] = {}
    if not docstring:
        return params
    for line in docstring.split("\n"):
        line = line.strip()
        if line.startswith(":param "):
            rest = line[7:]
            if ":" in rest:
                pname, desc = rest.split(":", 1)
                params[pname.strip()] = desc.strip()
    return params


def _format_type(annotation) -> str:
    """Format a type annotation into a display string."""
    if annotation is inspect.Parameter.empty:
        return "Any"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    type_str = str(annotation)
    for prefix in ("typing.", "<class '", "'>"):
        type_str = type_str.replace(prefix, "")
    return type_str


def _extract_description(cls) -> str:
    """Extract the short description from a class (first paragraph of __init__ docstring)."""
    doc = inspect.getdoc(cls.__init__) or ""
    if doc:
        for sep in (":param", "Args:", "\n\n"):
            if sep in doc:
                doc = doc.split(sep)[0]
                break
        return " ".join(doc.split()).strip()
    return getattr(cls, "DESCRIPTION", "") or (cls.__doc__ or "").strip()


def list_available_scripts() -> List[Dict[str, Any]]:
    """Discover all ingest scripts with full ifcpatch-style parameter introspection."""
    scripts_dir = Path(__file__).parent
    results = []
    for info in pkgutil.iter_modules([str(scripts_dir)]):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"ingest_scripts.{info.name}")
            cls = getattr(mod, "Ingester", None)
            if cls is None:
                continue

            sig = inspect.signature(cls.__init__)
            docstring = inspect.getdoc(cls.__init__) or ""
            param_docs = _parse_docstring_params(docstring)

            parameters: List[ScriptParameter] = []
            for param_name, param in sig.parameters.items():
                if param_name in ("self", "ifc_files", "log", "kwargs"):
                    continue
                if param_name.startswith("**") or param_name.startswith("*"):
                    continue

                parameters.append(ScriptParameter(
                    name=param_name,
                    type=_format_type(param.annotation),
                    description=param_docs.get(param_name, ""),
                    required=param.default is inspect.Parameter.empty,
                    default=None if param.default is inspect.Parameter.empty else param.default,
                ))

            script_info = ScriptInfo(
                name=info.name,
                description=_extract_description(cls),
                parameters=parameters,
            )
            results.append(script_info.to_dict())
        except Exception as exc:
            logger.warning("Failed to inspect ingest script %s: %s", info.name, exc)
    return results


# Legacy name → CamelCase mapping for backward compatibility
_LEGACY_NAMES = {
    "spaces": "ExtractSpaces",
    "spatial": "SpatialContainment",
    "mep": "MepTopology",
    "structural": "StructuralConnectivity",
}


def load_script(name: str) -> type:
    """Load an Ingester class by script name (supports CamelCase or legacy lowercase)."""
    resolved = _LEGACY_NAMES.get(name, name)
    if not resolved.isidentifier():
        raise ValueError(f"Invalid script name: {name}")
    try:
        mod = importlib.import_module(f"ingest_scripts.{resolved}")
    except ModuleNotFoundError:
        mod = importlib.import_module(f"ingest_scripts.{name}")
    cls = getattr(mod, "Ingester", None)
    if cls is None:
        raise ValueError(f"Script '{name}' does not export an Ingester class")
    return cls


def resolve_positional_arguments(script_name: str, positional_args: list) -> Dict[str, Any]:
    """Map positional argument values to keyword arguments by __init__ parameter order.

    This mirrors the ifcpatch pattern where arguments are passed positionally
    (in the order defined by __init__) and coerced to the annotated types.
    """
    cls = load_script(script_name)
    sig = inspect.signature(cls.__init__)

    param_names = [
        name for name, _ in sig.parameters.items()
        if name not in ("self", "ifc_files", "log", "kwargs")
    ]

    kwargs: Dict[str, Any] = {}
    for i, value in enumerate(positional_args):
        if i >= len(param_names):
            break
        pname = param_names[i]
        param = sig.parameters[pname]

        # Coerce string values to the annotated type
        coerced = _coerce_value(value, param.annotation)
        kwargs[pname] = coerced

    return kwargs


def _coerce_value(value: Any, annotation) -> Any:
    """Coerce a string value to the annotated type (bool, int, float, str).

    Handles both real type objects and string annotations (from __future__ annotations).
    """
    if not isinstance(value, str):
        return value
    if value == "":
        return value

    ann_str = str(annotation).lower().replace("<class '", "").replace("'>", "")

    if ann_str == "bool":
        return value.lower() in ("true", "1", "yes")

    if ann_str == "float":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    if ann_str == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value

    return value


