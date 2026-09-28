"""Geometry-kernel smoke check + runtime report for the topologicpy worker.

Why this exists: topologicpy no longer declares topologic_core as a
dependency (dropped in the 0.9.6x line; backends resolve lazily). An image
built without a kernel does not fail loudly -- Vertex.ByCoordinates prints
a warning and returns None, and Topology.ByBREPString(silent=True) returns
None -- so SpaceInteractions indexes 0 rooms and roomstamp builds 0 cells while
the jobs report success. Core.Backend() also silently prefers a PythonOCC
backend whenever import OCC succeeds, and that backend gives different
cells/containment answers on our data.

python kernel_smoke.py runs at image build time (Dockerfile) and exits
non-zero unless ALL of these hold:

  * Vertex.ByCoordinates(0, 0, 0) returns a vertex;
  * Topology.ByBREPString parses an OCCT BREP of a unit box (the exact call
    SpaceInteractions makes on IfcSpace BREPs) into a Cell of volume 1 that
    contains its centre (Cell.ContainmentStatus == 0, the roomstamp /
    SpaceInteractions probe);
  * Core.Backend() is TopologicCoreBackend;
  * OCC (pythonocc-core) is NOT importable.

runtime_info() is also used by tasks.py to log the versions + backend
once per job process.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Dict, List

# Topology.BREPString(Cell.Prism(width=1, length=1, height=1)) -- a unit box
# centred on the origin, in OCCT's "CASCADE Topology V3" text format.
UNIT_BOX_BREP = """
CASCADE Topology V3, (c) Open Cascade
Locations 0
Curve2ds 12
1 0.5 0.5 -1 0 
1 -0.5 0.5 0 -1 
1 -0.5 -0.5 1 0 
1 0.5 -0.5 0 1 
1 0.5 0.5 -1 0 
1 0.5 0.5 -1 0 
1 -0.5 -0.5 1 0 
1 0.5 0.5 -1 0 
1 -0.5 0.5 0 -1 
1 -0.5 -0.5 1 0 
1 -0.5 -0.5 1 0 
1 0.5 -0.5 0 1 
Curves 12
1 -0.5 0.5 -0.5 1 0 0 
1 0.5 0.5 -0.5 0 -1 0 
1 0.5 -0.5 -0.5 -1 0 0 
1 -0.5 -0.5 -0.5 0 1 0 
1 0.5 0.5 -0.5 0 0 1 
1 -0.5 0.5 0.5 1 0 0 
1 -0.5 0.5 0.5 0 0 -1 
1 0.5 -0.5 -0.5 0 0 1 
1 0.5 0.5 0.5 0 -1 0 
1 -0.5 -0.5 -0.5 0 0 1 
1 0.5 -0.5 0.5 -1 0 0 
1 -0.5 -0.5 0.5 0 1 0 
Polygon3D 0
PolygonOnTriangulations 0
Surfaces 6
1 0 0 -0.5 -0 -0 -1 -1 0 0 0 1 -0 
1 0 0.5 0 -0 -1 -0 0 0 -1 1 -0 0 
1 0 0 0.5 -0 -0 -1 -1 0 0 0 1 -0 
1 0.5 0 0 -1 -0 -0 0 0 -1 0 -1 0 
1 0 -0.5 0 0 1 0 0 -0 1 1 0 -0 
1 -0.5 0 0 1 0 0 -0 0 1 0 -1 0 
Triangulations 0

TShapes 34
Ve
1.00000000055511e-07
-0.5 0.5 -0.5
0 0

0101101
*
Ve
1.00000000055511e-07
0.5 0.5 -0.5
0 0

0101101
*
Ed
 1e-07 1 1 0
1  1 0 0 1
2  1 1 0 0 1
0

0101000
+34 0 -33 0 *
Ve
1.00000000055511e-07
0.5 -0.5 -0.5
0 0

0101101
*
Ed
 1e-07 1 1 0
1  2 0 0 1
2  2 1 0 0 1
0

0101000
+33 0 -31 0 *
Ve
1.00000000055511e-07
-0.5 -0.5 -0.5
0 0

0101101
*
Ed
 1e-07 1 1 0
1  3 0 0 1
2  3 1 0 0 1
0

0101000
+31 0 -29 0 *
Ed
 1e-07 1 1 0
1  4 0 0 1
2  4 1 0 0 1
0

0101000
+29 0 -34 0 *
Wi

0101100
+32 0 +30 0 +28 0 +27 0 *
Fa
0  1e-07 1 0

0111000
+26 0 *
Ve
1.00000000055511e-07
0.5 0.5 0.5
0 0

0101101
*
Ed
 1.00000000055511e-07 1 1 0
1  5 0 0 1
2  5 2 0 0 1
0

0101000
+33 0 -24 0 *
Ve
1.00000000055511e-07
-0.5 0.5 0.5
0 0

0101101
*
Ed
 1.00000000055511e-07 1 1 0
1  6 0 0 1
2  6 3 0 0 1
0

0101000
+22 0 -24 0 *
Ed
 1.00000000055511e-07 1 1 0
1  7 0 0 1
2  7 2 0 0 1
0

0101000
+22 0 -34 0 *
Wi

0101100
+32 0 +23 0 -21 0 +20 0 *
Fa
0  1e-07 2 0

0111000
+19 0 *
Ve
1.00000000055511e-07
0.5 -0.5 0.5
0 0

0101101
*
Ed
 1.00000000055511e-07 1 1 0
1  8 0 0 1
2  8 4 0 0 1
0

0101000
+31 0 -17 0 *
Ed
 1.00000000055511e-07 1 1 0
1  9 0 0 1
2  9 3 0 0 1
0

0101000
+24 0 -17 0 *
Wi

0101100
+30 0 +16 0 -15 0 -23 0 *
Fa
0  1e-07 4 0

0111000
+14 0 *
Ve
1.00000000055511e-07
-0.5 -0.5 0.5
0 0

0101101
*
Ed
 1.00000000055511e-07 1 1 0
1  10 0 0 1
2  10 5 0 0 1
0

0101000
+29 0 -12 0 *
Ed
 1.00000000055511e-07 1 1 0
1  11 0 0 1
2  11 3 0 0 1
0

0101000
+17 0 -12 0 *
Wi

0101100
+28 0 +11 0 -10 0 -16 0 *
Fa
0  1e-07 5 0

0111000
+9 0 *
Ed
 1.00000000055511e-07 1 1 0
1  12 0 0 1
2  12 3 0 0 1
0

0101000
+12 0 -22 0 *
Wi

0101100
+27 0 -20 0 -7 0 -11 0 *
Fa
0  1e-07 6 0

0111000
+6 0 *
Wi

0101100
+21 0 +15 0 +10 0 +7 0 *
Fa
0  1e-07 3 0

0111000
+4 0 *
Sh

0101100
+25 0 -18 0 -13 0 -8 0 -5 0 -3 0 *
So

1100000
+2 0 *

+1 0 """


def _dist_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def runtime_info() -> Dict[str, Any]:
    """Versions + active backend. Never raises (safe to call from a job)."""
    info: Dict[str, Any] = {
        "topologicpy": None,
        "topologic_core": _dist_version("topologic_core"),
        "backend": None,
        "backend_env": os.environ.get("TOPOLOGICPY_CORE_BACKEND") or None,
        "occ_importable": importlib.util.find_spec("OCC") is not None,
    }
    try:
        import topologicpy

        info["topologicpy"] = getattr(topologicpy, "__version__", None) or _dist_version("topologicpy")
    except Exception as exc:  # pragma: no cover - image without topologicpy
        info["topologicpy_error"] = repr(exc)
        return info
    try:
        from topologicpy.Core import Core

        info["backend"] = type(Core.Backend()).__name__
    except Exception as exc:
        info["backend_error"] = repr(exc)
    return info


def check() -> List[str]:
    """Return the list of failed conditions (empty == kernel usable)."""
    failures: List[str] = []
    info = runtime_info()
    if info.get("occ_importable"):
        failures.append(
            "pythonocc-core (OCC) is importable; topologicpy's auto backend would switch to it"
        )
    if info.get("backend") != "TopologicCoreBackend":
        failures.append(
            "Core.Backend() is %r, expected TopologicCoreBackend (%s)"
            % (info.get("backend"), info.get("backend_error", "no error"))
        )
    if not info.get("topologic_core"):
        failures.append("topologic_core is not installed")
    try:
        from topologicpy.Cell import Cell
        from topologicpy.Topology import Topology
        from topologicpy.Vertex import Vertex

        origin = Vertex.ByCoordinates(0, 0, 0)
        if origin is None:
            failures.append("Vertex.ByCoordinates(0, 0, 0) returned None (no geometry kernel)")
        topo = Topology.ByBREPString(UNIT_BOX_BREP, silent=True)
        if topo is None:
            failures.append("Topology.ByBREPString(unit box) returned None (no geometry kernel)")
        elif not Topology.IsInstance(topo, "Cell"):
            failures.append("Topology.ByBREPString(unit box) returned %s, not a Cell" % Topology.TypeAsString(topo))
        else:
            volume = Cell.Volume(topo)
            if volume is None or abs(volume - 1.0) > 1e-6:
                failures.append("unit box Cell.Volume is %r, expected 1.0" % (volume,))
            if origin is not None:
                status = Cell.ContainmentStatus(topo, origin, tolerance=0.0001)
                if status != 0:
                    failures.append("unit box ContainmentStatus(origin) is %r, expected 0 (inside)" % (status,))
    except Exception as exc:
        failures.append("kernel smoke raised %s: %s" % (type(exc).__name__, exc))
    return failures


def main() -> int:
    info = runtime_info()
    failures = check()
    print("topologicpy kernel smoke: %s" % json.dumps(info, sort_keys=True))
    if failures:
        for failure in failures:
            print("KERNEL SMOKE FAILED: %s" % failure, file=sys.stderr)
        return 1
    print("topologicpy kernel smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
