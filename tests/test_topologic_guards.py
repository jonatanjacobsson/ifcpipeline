"""Guards around the topologicpy geometry kernel and TGraph routing semantics.

* The worker image must never run without a geometry kernel, or on the
  PythonOCC backend (see topologicpy-worker/kernel_smoke.py).
* topograph.shortest_path (PathRouting) must route by hop count: under
  importMode="topology" the TGraph vertex coordinates are synthetic, so the
  0.9.65+ default (Dijkstra over edgeKey="Length") minimises meaningless
  distances.

Kernel-dependent tests skip when topologicpy / topologic_core are not
installed; the static Dockerfile/requirements checks run anywhere.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_WORKER = _REPO / "topologicpy-worker"
sys.path.insert(0, str(_WORKER))


def _kernel_available() -> bool:
    try:
        import topologic_core  # noqa: F401
        import topologicpy  # noqa: F401
    except Exception:
        return False
    return True


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="topologicpy/topologic_core not installed")


# --------------------------------------------------------------------------- #
# static: image + pins
# --------------------------------------------------------------------------- #

def test_requirements_pin_topologicpy_and_kernel_exactly():
    req = (_WORKER / "requirements.txt").read_text()
    pins = dict(re.findall(r"^(topologicpy|topologic_core)==(\S+)\s*$", req, re.MULTILINE))
    assert set(pins) == {"topologicpy", "topologic_core"}, (
        "topologicpy and topologic_core must both be pinned with == (topologicpy "
        "does not pull in a kernel; a range drifts silently)"
    )


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "tgraph_eval/Dockerfile.eval"])
def test_dockerfile_pins_backend_before_kernel_smoke(dockerfile):
    text = (_WORKER / dockerfile).read_text()
    env = text.find("ENV TOPOLOGICPY_CORE_BACKEND=topologic_core")
    smoke = text.find("kernel_smoke.py\n")
    run = re.search(r"^RUN python /app/kernel_smoke\.py\s*$", text, re.MULTILINE)
    assert env != -1, f"{dockerfile}: backend must be pinned to topologic_core"
    assert run is not None, f"{dockerfile}: build must run the kernel smoke check"
    assert env < run.start(), f"{dockerfile}: pin the backend before the smoke check runs"
    assert smoke != -1
    assert "patch_vertex" not in text, f"{dockerfile}: patch_vertex.py is retired"


# --------------------------------------------------------------------------- #
# kernel smoke
# --------------------------------------------------------------------------- #

@needs_kernel
def test_kernel_smoke_passes_on_this_runtime(monkeypatch):
    monkeypatch.setenv("TOPOLOGICPY_CORE_BACKEND", "topologic_core")
    import kernel_smoke

    assert kernel_smoke.check() == []
    info = kernel_smoke.runtime_info()
    assert info["backend"] == "TopologicCoreBackend"
    assert info["occ_importable"] is False
    assert info["topologicpy"] and info["topologic_core"]


@needs_kernel
def test_kernel_smoke_fails_without_a_kernel():
    # Simulate an image built without topologic_core: topologicpy still imports
    # but every geometry call silently returns None. The smoke must exit 1.
    code = (
        "import sys; sys.modules['topologic_core'] = None\n"
        "import kernel_smoke; sys.exit(kernel_smoke.main())\n"
    )
    env = dict(os.environ, TOPOLOGICPY_CORE_BACKEND="topologic_core",
               PYTHONPATH=os.pathsep.join([str(_WORKER), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ByBREPString(unit box) returned None" in proc.stderr


@needs_kernel
def test_kernel_smoke_fails_when_occ_is_importable(monkeypatch):
    import importlib.util

    import kernel_smoke

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "OCC":
            return importlib.util.spec_from_loader("OCC", loader=None)
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(kernel_smoke.importlib.util, "find_spec", fake_find_spec)
    failures = kernel_smoke.check()
    assert any("OCC" in f for f in failures), failures


# --------------------------------------------------------------------------- #
# PathRouting: hop semantics
# --------------------------------------------------------------------------- #

def _routing_graph():
    """A -> C two ways: via X (2 hops, ~100 m) or via P, Q (3 hops, 10 m)."""
    from topologicpy.TGraph import TGraph

    coords = {"A": (0, 0, 0), "X": (5, 50, 0), "C": (10, 0, 0), "P": (3, 0, 0), "Q": (7, 0, 0)}
    names = list(coords)
    idx = {n: i for i, n in enumerate(names)}
    edges = [(idx["A"], idx["X"]), (idx["X"], idx["C"]),
             (idx["A"], idx["P"]), (idx["P"], idx["Q"]), (idx["Q"], idx["C"])]
    return TGraph.ByMeshData(
        [list(coords[n]) for n in names],
        [list(e) for e in edges],
        vertexDictionaries=[{"IFC_global_id": n, "IFC_type": "IfcSpace"} for n in names],
    ), idx


@needs_kernel
def test_shortest_path_routes_by_hop_count_not_synthetic_length():
    from topologicpy.TGraph import TGraph

    from ingest_scripts import topograph

    g, idx = _routing_graph()
    # The fixture must discriminate: geometric weighting prefers the 3-hop leg.
    by_length = TGraph.ShortestPath(g, idx["A"], idx["C"], mode="all", edgeKey="Length")
    assert by_length == [idx["A"], idx["P"], idx["Q"], idx["C"]]

    assert topograph.shortest_path(g, "A", "C") == ["A", "X", "C"]
    assert topograph._SP_KWARGS.get("edgeKey") == "hop"
