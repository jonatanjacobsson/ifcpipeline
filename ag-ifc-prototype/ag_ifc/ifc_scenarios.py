"""Run IfcClash scenarios from catalog and optional AG formalization."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ag_ifc.compiler import clash_to_ag2_stub
from ag_ifc.ifc_models import ensure_model_set, load_manifest, resolve_model_path


@dataclass
class IfcClashScenarioResult:
    scenario_id: str
    name: str
    aec_use_case: str
    clash_count: int
    elapsed_ms: float
    skipped: bool = False
    skip_reason: str | None = None
    sample_clash: dict[str, Any] | None = None
    ag_formalization: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)


def _build_clash_set(
    scenario: dict[str, Any],
    defaults: dict[str, Any],
    path_a: Path,
    path_b: Path,
) -> dict[str, Any]:
    cs: dict[str, Any] = {
        "name": scenario.get("name", scenario["id"]),
        "a": [{"file": str(path_a)}],
        "b": [{"file": str(path_b)}],
        "mode": scenario.get("mode", defaults.get("mode", "intersection")),
        "tolerance": scenario.get("tolerance", defaults.get("tolerance", 0.01)),
        "check_all": scenario.get("check_all", defaults.get("check_all", False)),
        "allow_touching": scenario.get(
            "allow_touching", defaults.get("allow_touching", False)
        ),
        "clearance": scenario.get("clearance", defaults.get("clearance", 0)),
    }
    if scenario.get("a_selector"):
        cs["a"][0]["selector"] = scenario["a_selector"]
    if scenario.get("b_selector"):
        cs["b"][0]["selector"] = scenario["b_selector"]
    return cs


def _run_ifcclash(clash_set: dict[str, Any], logger: logging.Logger) -> list[dict]:
    from ifcclash.ifcclash import Clasher, ClashSettings  # type: ignore

    settings = ClashSettings()
    settings.logger = logger
    settings.output = "/tmp/ag_ifc_clash_out.json"
    clasher = Clasher(settings)
    clasher.clash_sets = [clash_set]
    clasher.clash()
    clasher.export_json()
    with open(settings.output, encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_scenario_paths(scenario: dict[str, Any], manifest: dict[str, Any]) -> tuple[Path, Path] | None:
    model_sets = {ms["id"]: ms for ms in manifest["model_sets"]}
    set_a = model_sets.get(scenario["model_set"])
    if set_a is None:
        return None
    path_a = resolve_model_path(set_a, scenario["a_file"], fetch=True)
    if path_a is None:
        return None

    set_b_id = scenario.get("b_model_set", scenario["model_set"])
    set_b = model_sets.get(set_b_id)
    if set_b is None:
        return None
    path_b = resolve_model_path(set_b, scenario["b_file"], fetch=True)
    if path_b is None:
        return None
    return path_a, path_b


def run_ifc_scenario(
    scenario: dict[str, Any],
    manifest: dict[str, Any],
    *,
    logger: logging.Logger,
    formalize_ag: bool = False,
    vendor: Path | None = None,
) -> IfcClashScenarioResult:
    start = time.perf_counter()
    base = IfcClashScenarioResult(
        scenario_id=scenario["id"],
        name=scenario.get("name", scenario["id"]),
        aec_use_case=scenario.get("aec_use_case", ""),
        clash_count=0,
        elapsed_ms=0,
        tags=list(scenario.get("tags", [])),
    )

    paths = _resolve_scenario_paths(scenario, manifest)
    if paths is None:
        base.skipped = True
        base.skip_reason = "model file(s) not available"
        base.elapsed_ms = (time.perf_counter() - start) * 1000
        return base

    path_a, path_b = paths
    clash_set = _build_clash_set(
        scenario, manifest.get("defaults", {}), path_a, path_b
    )

    try:
        results = _run_ifcclash(clash_set, logger)
    except Exception as exc:  # noqa: BLE001
        base.skipped = True
        base.skip_reason = str(exc)
        base.elapsed_ms = (time.perf_counter() - start) * 1000
        return base

    clashes = results[0].get("clashes", {}) if results else {}
    base.clash_count = len(clashes)
    if clashes:
        sample = next(iter(clashes.values()))
        base.sample_clash = {
            "a_global_id": sample.get("a_global_id"),
            "b_global_id": sample.get("b_global_id"),
            "a_ifc_class": sample.get("a_ifc_class"),
            "b_ifc_class": sample.get("b_ifc_class"),
            "a_name": sample.get("a_name"),
            "b_name": sample.get("b_name"),
            "p1": sample.get("p1"),
            "p2": sample.get("p2"),
            "distance": sample.get("distance"),
            "position": [
                (sample["p1"][i] + sample["p2"][i]) / 2
                for i in range(3)
            ]
            if sample.get("p1") and sample.get("p2")
            else None,
        }

        if formalize_ag and vendor is not None:
            from ag_ifc.ag2_runner import prove_problem

            clash_record = {
                "clash_id": scenario["id"],
                **base.sample_clash,
                "clearance_required_m": scenario.get("clearance", 0.05),
            }
            stub = clash_to_ag2_stub(clash_record)
            proof = prove_problem(f"{scenario['id']}_ag", stub.ag2, vendor)
            base.ag_formalization = {
                "ag2": stub.ag2,
                "proven": proof.proven,
                "goal": proof.goal,
                "error": proof.error,
                "assumptions": stub.assumptions,
            }

    base.elapsed_ms = (time.perf_counter() - start) * 1000
    return base


def load_ifc_scenarios(path: Path | None = None) -> tuple[dict[str, Any], list[dict]]:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "scenarios" / "ifc_clash_scenarios.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data, data["scenarios"]
