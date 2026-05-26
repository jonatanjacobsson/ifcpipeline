"""Thin wrapper around google-deepmind/alphageometry2 DDAR."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProveResult:
    problem_id: str
    proven: bool
    goal: str | None
    elapsed_ms: float
    error: str | None = None


def vendor_path(root: Path) -> Path:
    return root / "vendor" / "alphageometry2"


def ensure_vendor(root: Path) -> Path:
    path = vendor_path(root)
    if not path.is_dir():
        raise FileNotFoundError(
            f"AlphaGeometry2 not found at {path}. Run: ./scripts/setup_ag2.sh"
        )
    return path


def _import_ag2(vendor: Path):
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from ddar import DDAR  # pylint: disable=import-outside-toplevel
    from parse import AGProblem  # pylint: disable=import-outside-toplevel

    return AGProblem, DDAR


def prove_problem(problem_id: str, ag2_string: str, vendor: Path) -> ProveResult:
    AGProblem, DDAR = _import_ag2(vendor)
    start = time.perf_counter()
    try:
        problem = AGProblem.parse(ag2_string.strip())
        ddar = DDAR(problem.points)
        for pred in problem.preds:
            ddar.force_pred(pred)
        ddar.deduction_closure()
        proven = bool(problem.goal and ddar.check_pred(problem.goal))
        goal = str(problem.goal) if problem.goal else None
        elapsed_ms = (time.perf_counter() - start) * 1000
        return ProveResult(
            problem_id=problem_id,
            proven=proven,
            goal=goal,
            elapsed_ms=elapsed_ms,
        )
    except (Exception, AssertionError) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return ProveResult(
            problem_id=problem_id,
            proven=False,
            goal=None,
            elapsed_ms=elapsed_ms,
            error=str(exc),
        )


def run_upstream_smoke(vendor: Path, timeout_s: int = 120) -> dict[str, Any]:
    """Run AG2's bundled `python -m test` as a reference smoke check."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "test"],
            cwd=str(vendor),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        output = proc.stdout + proc.stderr
        proven_count = output.count("Proven :-)")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "proven_count": proven_count,
            "elapsed_ms": (time.perf_counter() - start) * 1000,
            "output_tail": output[-2000:] if len(output) > 2000 else output,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"timeout after {timeout_s}s",
            "elapsed_ms": (time.perf_counter() - start) * 1000,
        }
