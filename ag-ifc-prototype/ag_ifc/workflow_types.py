"""Shared workflow result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ag_ifc.ag2_runner import ProveResult, prove_problem


@dataclass
class AgProofRecord:
    problem_id: str
    proven: bool
    goal: str | None
    plane: str
    error: str | None = None


def prove_stubs(stubs: list, vendor, prefix: str) -> list[AgProofRecord]:
    records: list[AgProofRecord] = []
    for stub in stubs:
        pid = f"{prefix}_{stub.clash_id}"
        result: ProveResult = prove_problem(pid, stub.ag2, vendor)
        plane = stub.mapping.get("plane", "xy") if isinstance(stub.mapping, dict) else "xy"
        records.append(
            AgProofRecord(
                problem_id=pid,
                proven=result.proven,
                goal=result.goal,
                plane=str(plane),
                error=result.error,
            )
        )
    return records


@dataclass
class WorkflowFix:
    iteration: int
    clash_key: str
    severity: str
    cluster_id: str
    moved_guid: str
    moved_class: str
    route_waypoints: list[list[float]]
    route_reached_goal: bool
    translation: list[float]
    clash_count_before: int
    clash_count_after: int
    ag_proofs: list[AgProofRecord] = field(default_factory=list)
    triage_rationale: list[str] = field(default_factory=list)


@dataclass
class Workflow3DResult:
    case_id: str
    passed: bool
    initial_clash_count: int
    final_clash_count: int
    iterations_used: int
    max_iterations: int
    fixes: list[WorkflowFix] = field(default_factory=list)
    triage_order: list[dict[str, Any]] = field(default_factory=list)
    work_dir: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    elapsed_ms: float = 0
    regression_passed: bool = True
    regression_reports: list[dict[str, Any]] = field(default_factory=list)
    validated_fixes: list[dict[str, Any]] = field(default_factory=list)
    bcf_export: str | None = None
