"""Guarded wrappers for topologicpy calls that regressed in 0.9.70+.

No worker code path calls these APIs today (checked 2026-09-25 by grep and by
counting calls during the 0.9.80 gate runs; see UPGRADE-0.9.80.md).
``tests/test_topologic_guards.py`` fails if a direct call appears anywhere in
``topologicpy-worker/`` outside this module, so a future use has to come
through here:

* ``CellComplex.ByCells`` -- on 0.9.80 + topologic_core it can return an EMPTY
  CellComplex without any warning (243 A1 room cells -> 0 cells): the native
  result passes the type check, so the ``_ByFaces`` fallback never runs.
* ``Topology.InternalVertex`` -- 0.9.80 dropped the thread timeout 0.9.65 had
  (``timeout`` is accepted but ignored), so a pathological cell can hang the job.
  A hung native call cannot be killed from Python; the daemon thread is left
  behind and dies with the spawn child that runs every job.

Kept import-light (topologicpy is imported lazily) so the api-gateway, which
copies ``ingest_scripts`` without installing topologicpy, can still import the
package.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

log = logging.getLogger(__name__)

INTERNAL_VERTEX_TIMEOUT_S = 30.0


def internal_vertex(topology: Any, tolerance: float = 0.0001,
                    timeout_s: float = INTERNAL_VERTEX_TIMEOUT_S) -> Optional[Any]:
    """``Topology.InternalVertex`` with our own timeout; None on timeout/failure."""
    from topologicpy.Topology import Topology

    result: List[Any] = []

    def run() -> None:
        try:
            result.append(Topology.InternalVertex(topology, tolerance=tolerance, silent=True))
        except Exception:  # pragma: no cover - logged by the caller's None handling
            log.debug("Topology.InternalVertex raised", exc_info=True)

    worker = threading.Thread(target=run, name="topologic-internal-vertex", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        log.warning("Topology.InternalVertex exceeded %.1fs; returning None", timeout_s)
        return None
    return result[0] if result else None


def cellcomplex_by_cells(cells: List[Any], tolerance: float = 0.0001) -> Optional[Any]:
    """``CellComplex.ByCells`` that never returns an empty CellComplex.

    Returns None (with a warning) when the input has cells but the result has
    none -- the silent 0.9.80 failure -- so callers take their fallback path.
    """
    from topologicpy.CellComplex import CellComplex
    from topologicpy.Topology import Topology

    if not cells:
        return None
    try:
        cc = CellComplex.ByCells(cells, tolerance=tolerance, silent=True)
    except Exception:
        log.warning("CellComplex.ByCells raised on %d cells", len(cells), exc_info=True)
        return None
    if cc is None:
        return None
    got = Topology.Cells(cc) or []
    if not got:
        log.warning("CellComplex.ByCells returned an empty CellComplex for %d cells", len(cells))
        return None
    return cc
