"""Execute AEC scenario catalog against AlphaGeometry2 DDAR."""

from __future__ import annotations

from pathlib import Path

from ag_ifc.ag2_runner import ProveResult, ensure_vendor, prove_problem
from ag_ifc.scenarios import Scenario, ScenarioOutcome


def run_scenario(scenario: Scenario, vendor: Path) -> ScenarioOutcome:
    result: ProveResult = prove_problem(scenario.id, scenario.ag2, vendor)
    setup_ok = result.error is None
    proven = result.proven if setup_ok else False

    expected_match: bool | None = None
    if scenario.expected_setup == "setup_error":
        expected_match = not setup_ok
    elif scenario.expected_proven is not None and setup_ok:
        expected_match = proven == scenario.expected_proven

    return ScenarioOutcome(
        scenario_id=scenario.id,
        category=scenario.category,
        subcategory=scenario.subcategory,
        aec_use_case=scenario.aec_use_case,
        aec_utility_hypothesis=scenario.aec_utility_hypothesis,
        setup_ok=setup_ok,
        proven=proven,
        goal=result.goal,
        elapsed_ms=result.elapsed_ms,
        error=result.error,
        expected_match=expected_match,
        ag2=scenario.ag2,
    )


def run_catalog(scenarios: list[Scenario], vendor: Path) -> list[ScenarioOutcome]:
    return [run_scenario(s, vendor) for s in scenarios]
