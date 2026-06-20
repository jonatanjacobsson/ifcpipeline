"""Contract + smoke tests for the TGraph-migrated ingest scripts.

Each Graph-using ingest script is run end-to-end (load_script -> extract ->
build_output) on a real IFC model and its output contract is asserted. The
scripts now build their graph through the shared ``topograph`` TGraph adapter
(``ingest_scripts/topograph.py``); these tests guard that migration.

Fixture resolution (first hit wins):
  1. $INGEST_TEST_IFC
  2. first *.ifc under /models_extra, /uploads, /models_xl  (eval-image mounts)
If none is found the tests skip — so this is safe to collect anywhere, and
meaningful wherever a model is mounted (the eval image, or the worker).

Runnable two ways:
  * pytest:  pytest ifcpipeline/tests/test_ingest_scripts.py
  * plain:   python -m tests.test_ingest_scripts   (no pytest needed; used in
             the eval image, which has no pytest installed)
"""

from __future__ import annotations

import glob
import logging
import os
import sys
from pathlib import Path

# Make `import ingest_scripts` work regardless of CWD (the package lives in the
# topologicpy-worker dir, or at /app/ingest_scripts in the eval/worker image).
_WORKER = Path(__file__).resolve().parents[1] / "topologicpy-worker"
for cand in (_WORKER, Path("/app")):
    if (cand / "ingest_scripts" / "__init__.py").exists():
        sys.path.insert(0, str(cand))
        break

log = logging.getLogger("test_ingest")
logging.basicConfig(level=logging.WARNING)

# Scripts migrated onto topograph that should yield output on a model with a
# spatial structure. (EgressCirculation is Phase 2 and excluded here.)
GRAPH_SCRIPTS = [
    "GraphCentrality",
    "SpaceAdjacency",
    "SpatialContainment",
    "BridgesAndCuts",
    "ZonePartition",
    "PathRouting",
]

_REQUIRED_OUTPUT_KEYS = {"script", "version", "source_files", "summary", "elements", "relationships"}
_REL_KEYS = {"subject_global_id", "object_global_id", "relationship_family", "relationship_type"}


def _find_fixture():
    env = os.environ.get("INGEST_TEST_IFC")
    if env and os.path.exists(env):
        return env
    for d in ("/models_extra", "/uploads", "/models_xl"):
        hits = sorted(glob.glob(os.path.join(d, "*.ifc")))
        if hits:
            return hits[0]
    return None


def _kwargs_for(name: str) -> dict:
    # Keep the slow/quadratic ops cheap so the suite stays fast.
    if name == "GraphCentrality":
        return {"metric": "degree"}        # avoid O(V*E) betweenness
    if name == "PathRouting":
        return {"max_paths": 5}            # cap the pairwise shortest-path loop
    return {}


def _run_one(name: str, fixture: str) -> dict:
    from ingest_scripts import load_script
    cls = load_script(name)
    ingester = cls([Path(fixture)], log, **_kwargs_for(name))
    ingester.extract()
    return ingester.build_output(source_files=[fixture])


def _assert_contract(name: str, out: dict):
    assert _REQUIRED_OUTPUT_KEYS.issubset(out), f"{name}: missing keys {_REQUIRED_OUTPUT_KEYS - set(out)}"
    assert out["script"], f"{name}: empty script name"
    assert isinstance(out["elements"], list) and isinstance(out["relationships"], list), f"{name}: bad types"
    for r in out["relationships"][:50]:
        assert _REL_KEYS.issubset(r), f"{name}: relationship missing {_REL_KEYS - set(r)}"
        assert r["subject_global_id"] and r["object_global_id"], f"{name}: empty gid in relationship"
    for e in out["elements"][:50]:
        assert e.get("global_id"), f"{name}: element missing global_id"


# --------------------------------------------------------------------------- #
# pytest entry points
# --------------------------------------------------------------------------- #

def _fixture_or_skip():
    fx = _find_fixture()
    if not fx:
        try:
            import pytest
            pytest.skip("no IFC fixture available (set $INGEST_TEST_IFC or mount a model)")
        except ImportError:
            raise SystemExit("no fixture")
    return fx


def test_topograph_adapter():
    from ingest_scripts import topograph
    fx = _fixture_or_skip()
    g = topograph.build_graph(fx)
    assert g is not None, "build_graph returned None"
    nodes = topograph.vertices(g)
    assert nodes, "no vertices"
    assert any(n.gid for n in nodes), "no node carried an IFC GlobalId"
    es = topograph.edges(g)
    assert isinstance(es, list)
    if es:
        assert len(es[0]) == 2 and all(es[0]), "edge is not a (gid, gid) pair"


def test_migrated_scripts_contract():
    fx = _fixture_or_skip()
    failures = []
    for name in GRAPH_SCRIPTS:
        try:
            out = _run_one(name, fx)
            _assert_contract(name, out)
        except AssertionError as exc:
            failures.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: raised {type(exc).__name__}: {exc}")
    assert not failures, "ingest script failures:\n" + "\n".join(failures)


# --------------------------------------------------------------------------- #
# plain-script entry point (eval image has no pytest)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    fixture = _find_fixture()
    if not fixture:
        print("SKIP: no IFC fixture (set INGEST_TEST_IFC or mount a model)")
        raise SystemExit(0)
    print(f"fixture: {fixture}\n")
    from ingest_scripts import topograph
    g = topograph.build_graph(fixture)
    print(f"adapter: order={topograph.order(g)} size={topograph.size(g)} "
          f"gid_nodes={sum(1 for n in topograph.vertices(g) if n.gid)}")
    ok = True
    for name in GRAPH_SCRIPTS:
        try:
            out = _run_one(name, fixture)
            _assert_contract(name, out)
            print(f"  PASS {name:20} elements={len(out['elements']):6} "
                  f"relationships={len(out['relationships']):6}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAIL {name:20} {type(exc).__name__}: {exc}")
    print("\n" + ("ALL PASS" if ok else "FAILURES"))
    raise SystemExit(0 if ok else 1)
