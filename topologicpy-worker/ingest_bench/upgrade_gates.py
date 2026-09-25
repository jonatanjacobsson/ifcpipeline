"""Version-bump gates for the topologicpy worker that bench.py fingerprints miss.

    PYTHONPATH=<repo>:<repo>/topologicpy-worker python upgrade_gates.py <uploads dir> [--out gates.json]

Prints one JSON document; run it on the old and the new pin and diff:

* runtime            -- kernel_smoke.runtime_info() (versions + Core backend)
* roomstamp_cells    -- tasks._build_mesh_cell/_build_hull_cell/_build_prism_cell on
                        A1_2b_BIM_XXX_0003_00.ifc (tolerance 0.01, the request default):
                        mesh/hull/prism counts, summed volume and cell_sha =
                        sha256([kinds, round(volume, 3)]) in GlobalId order
* space_interactions -- SpaceInteractions E1-640 x A1: rooms_indexed, relationships and a
                        sha over the relationships with the engine version string masked
* knowledge_graph    -- KnowledgeGraphExport on A1: triples, prefixes, legacy-vocabulary
                        counts (rdf:type bot:Space, top:aggregates, top:connectsTo,
                        top:ifcGUID), corrupted literals
* knowledge_graph_no_bot -- the same with include_bot=False; mapped_types counts the
                        rdf:type bot:*/brick:*/prov:* statements (0.9.65: 0)

Baseline (topologicpy 0.9.65 + topologic_core 8.0.4, 2026-09-25): 237 mesh + 4 hull cells,
cell_sha aae5349af340, 53316.696 m3; rooms_indexed 243; KG 66,235 triples / 15 prefixes /
481 bot:Space / 249 top:aggregates / 1,721 top:connectsTo / 0 corrupted; include_bot=False
65,745 triples / 0 mapped types.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

A1 = "A1_2b_BIM_XXX_0003_00.ifc"
E1_640 = "E1-640-SM-Telecommunication.ifc"


def _quiet(fn, *a, **k):
    """topologicpy prints its warnings; keep the JSON on stdout clean."""
    with redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def roomstamp_cells(uploads: Path) -> dict:
    import ifcopenshell
    from topologicpy.Cell import Cell

    import tasks

    model = ifcopenshell.open(str(uploads / A1))
    spaces, failures = _quiet(tasks._collect_spaces, model, A1, "IfcSpace", True, tasks._geometry_settings())
    spaces = sorted(spaces, key=lambda sp: sp.global_id)  # the 2026-09-25 evaluation's order
    tol = 0.01
    stats = tasks._RunStats()
    kinds, vols = [], []
    t0 = time.perf_counter()
    for sp in spaces:
        cell, kind = _quiet(tasks._build_mesh_cell, sp, tol, stats), "mesh"
        if cell is None:
            cell, kind = _quiet(tasks._build_hull_cell, sp, tol), "hull"
        if cell is None:
            cell, kind = _quiet(tasks._build_prism_cell, sp, tol), "prism"
        kinds.append(kind if cell is not None else None)
        vols.append(round(Cell.Volume(cell, mantissa=6), 3) if cell is not None else None)
    return {
        "spaces": len(spaces), "space_geometry_failures": failures,
        "mesh": kinds.count("mesh"), "hull": kinds.count("hull"), "prism": kinds.count("prism"),
        "none": kinds.count(None), "sum_vol_m3": round(sum(v for v in vols if v), 3),
        "cell_sha": hashlib.sha256(json.dumps([kinds, vols]).encode()).hexdigest()[:12],
        "build_s": round(time.perf_counter() - t0, 2),
    }


def space_interactions(uploads: Path) -> dict:
    import topologicpy

    from ingest_scripts import load_script

    ing = load_script("SpaceInteractions")([uploads / E1_640, uploads / A1], logging.getLogger("gate"))
    _quiet(ing.extract)
    out = ing.build_output(source_files=[])
    rels = sorted(json.dumps(r, sort_keys=True, default=str) for r in out["relationships"])
    blob = "\n".join(rels).replace(f'"topologicpy": "{topologicpy.__version__}"', '"topologicpy": "*"')
    return {"rooms_indexed": out["summary"].get("rooms_indexed"),
            "relationships": len(rels),
            "rel_sha_version_masked": hashlib.sha256(blob.encode()).hexdigest()[:12]}


def knowledge_graph(uploads: Path, include_bot: bool = True) -> dict:
    from ingest_scripts import load_script

    ing = load_script("KnowledgeGraphExport")([uploads / A1], logging.getLogger("gate"),
                                              include_bot=include_bot)
    _quiet(ing.extract)
    ttl = ing.get_artifacts()[0][1]
    body = [ln for ln in ttl.splitlines() if ln and not ln.startswith("@prefix")]
    entry = ing.get_summary()["files"][0]
    return {
        "triples": len(body), "prefixes": len(entry["prefixes"]),
        "guid_keyed_vertices": entry["guid_keyed_vertices"],
        "legacy_triples_added": entry.get("legacy_triples_added"),
        "bot_space": sum(1 for ln in body if ln.endswith(" rdf:type bot:Space .")),
        "mapped_types": sum(1 for ln in body if re.search(r" rdf:type (bot|brick|prov):\S+ \.$", ln)),
        "aggregates": sum(1 for ln in body if " top:aggregates " in ln),
        "connects_to": sum(1 for ln in body if " top:connectsTo " in ln),
        "ifc_guid": sum(1 for ln in body if " top:ifcGUID " in ln),
        "corrupted_literals": ttl.count("_RDFLiteral("),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("uploads", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    os.environ.setdefault("INGEST_TGRAPH_CACHE", "0")
    logging.basicConfig(level=logging.ERROR)
    import kernel_smoke

    res = {"runtime": kernel_smoke.runtime_info(), "kernel_smoke_failures": kernel_smoke.check()}
    for name, fn in (("roomstamp_cells", roomstamp_cells), ("space_interactions", space_interactions),
                     ("knowledge_graph", knowledge_graph),
                     ("knowledge_graph_no_bot", lambda u: knowledge_graph(u, include_bot=False))):
        try:
            res[name] = fn(args.uploads)
        except Exception as exc:  # report, keep going
            res[name] = {"error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(res, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
