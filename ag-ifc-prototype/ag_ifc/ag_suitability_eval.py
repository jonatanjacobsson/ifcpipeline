"""Batch-evaluate IfcClash clashes against AG viability → prefilter rules."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ag_ifc.clash_prefilter import ClashSuitability, assess_clash_suitability, prefilter_clash_dict
from ag_ifc.clash_runner import run_clash_set
from ag_ifc.ifc_models import load_manifest, resolve_model_path
from ag_ifc.ifc_scenarios import _build_clash_set, _resolve_scenario_paths


@dataclass
class ClashEvalRecord:
    scenario_id: str
    clash_key: str
    assessment: ClashSuitability
    ag2_sample: str | None = None


@dataclass
class ScenarioEvalSummary:
    scenario_id: str
    clash_count: int
    solve_count: int
    review_count: int
    exclude_count: int
    ag_proven_clashes: int
    skipped: bool = False
    skip_reason: str | None = None


def _aggregate_rules(records: list[ClashEvalRecord]) -> dict[str, Any]:
  by_pair: dict[str, dict] = defaultdict(
      lambda: {"total": 0, "solve": 0, "ag_proven": 0, "avg_confidence": 0.0}
  )
  by_movable: dict[str, dict] = defaultdict(lambda: {"total": 0, "solve": 0})
  by_mode: dict[str, dict] = defaultdict(lambda: {"total": 0, "solve": 0})

  for rec in records:
      a = rec.assessment
      pair = a.class_pair
      by_pair[pair]["total"] += 1
      by_pair[pair]["avg_confidence"] += a.confidence
      if a.tier == "solve":
          by_pair[pair]["solve"] += 1
      if a.ag_proven_count and a.ag_proven_count > 0:
          by_pair[pair]["ag_proven"] += 1

      by_movable[a.movable_class]["total"] += 1
      if a.tier == "solve":
          by_movable[a.movable_class]["solve"] += 1

  for stats in by_pair.values():
      if stats["total"]:
          stats["avg_confidence"] = round(stats["avg_confidence"] / stats["total"], 3)
          stats["solve_rate_pct"] = round(100 * stats["solve"] / stats["total"], 1)

  recommended_solve_pairs = [
      p for p, s in by_pair.items() if s["total"] >= 1 and s.get("solve_rate_pct", 0) >= 50
  ]
  recommended_exclude_pairs = [
      p for p, s in by_pair.items() if s["total"] >= 1 and s.get("solve", 0) == 0
  ]

  return {
      "by_class_pair": dict(sorted(by_pair.items(), key=lambda x: -x[1]["total"])),
      "by_movable_class": dict(sorted(by_movable.items(), key=lambda x: -x[1]["total"])),
      "recommended_auto_solve_pairs": recommended_solve_pairs[:30],
      "recommended_manual_pairs": recommended_exclude_pairs[:30],
      "ag_guidance": {
          "use_for_certification": [
              "mep_coordination (para/coll/perp)",
              "plan-view parallel offsets",
              "orthogonal route segments (per-segment para)",
          ],
          "do_not_use_for": [
              "solid intersection truth (IfcClash)",
              "metric cong / distseq clearance",
              "optimal 3D volume routing without abstraction",
          ],
      },
  }


# re-export for rules doc
AG_STRONG_CATEGORIES = {
    "mep_coordination",
    "clash_resolution",
    "structural_grid",
}


def run_scenario_clash_eval(
    scenario: dict[str, Any],
    manifest: dict[str, Any],
    *,
    logger: logging.Logger,
    verify_ag: bool = True,
    vendor: Path | None = None,
    output_json: str,
) -> tuple[list[ClashEvalRecord], ScenarioEvalSummary]:
    paths = _resolve_scenario_paths(scenario, manifest)
    if paths is None:
        return [], ScenarioEvalSummary(
            scenario_id=scenario["id"],
            clash_count=0,
            solve_count=0,
            review_count=0,
            exclude_count=0,
            ag_proven_clashes=0,
            skipped=True,
            skip_reason="models unavailable",
        )

    path_a, path_b = paths
    clash_set = _build_clash_set(scenario, manifest.get("defaults", {}), path_a, path_b)
    clash_set["_output_path"] = output_json
    clash_mode = clash_set.get("mode", "intersection")
    clearance_m = float(scenario.get("clearance_m", 0.05))

    result = run_clash_set(clash_set, logger)
    clashes = result.get("clashes", {})
    records: list[ClashEvalRecord] = []

    for key, data in clashes.items():
        assessment = assess_clash_suitability(
            key,
            data,
            clash_mode=clash_mode,
            move_side=scenario.get("move_side", "auto"),
            clearance_m=clearance_m,
            verify_ag=verify_ag,
            vendor=vendor,
            ifc_paths=[str(path_a), str(path_b)],
            require_geometry=False,
        )
        records.append(ClashEvalRecord(scenario["id"], key, assessment))

    solve = sum(1 for r in records if r.assessment.tier == "solve")
    review = sum(1 for r in records if r.assessment.tier == "review")
    exclude = sum(1 for r in records if r.assessment.tier == "exclude")
    ag_ok = sum(1 for r in records if r.assessment.ag_proven_count > 0)

    return records, ScenarioEvalSummary(
        scenario_id=scenario["id"],
        clash_count=len(records),
        solve_count=solve,
        review_count=review,
        exclude_count=exclude,
        ag_proven_clashes=ag_ok,
    )


def run_full_evaluation(
    scenarios: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    work_dir: Path,
    verify_ag: bool,
    vendor: Path | None,
    logger: logging.Logger,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    all_records: list[ClashEvalRecord] = []
    summaries: list[ScenarioEvalSummary] = []
    start = time.perf_counter()

    for scenario in scenarios:
        out = str(work_dir / f"{scenario['id']}_clash.json")
        records, summary = run_scenario_clash_eval(
            scenario,
            manifest,
            logger=logger,
            verify_ag=verify_ag,
            vendor=vendor,
            output_json=out,
        )
        all_records.extend(records)
        summaries.append(summary)

    rules = _aggregate_rules(all_records)
    total_clashes = len(all_records)
    solve_total = sum(1 for r in all_records if r.assessment.tier == "solve")

    return {
        "summary": {
            "scenarios": len(scenarios),
            "total_clashes": total_clashes,
            "solve_tier": solve_total,
            "review_tier": sum(1 for r in all_records if r.assessment.tier == "review"),
            "exclude_tier": sum(1 for r in all_records if r.assessment.tier == "exclude"),
            "ag_proven_clashes": sum(1 for r in all_records if (r.assessment.ag_proven_count or 0) > 0),
            "solve_rate_pct": round(100 * solve_total / total_clashes, 1) if total_clashes else 0,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        },
        "scenario_summaries": [
            {
                "scenario_id": s.scenario_id,
                "clash_count": s.clash_count,
                "solve": s.solve_count,
                "review": s.review_count,
                "exclude": s.exclude_count,
                "ag_proven": s.ag_proven_clashes,
                "skipped": s.skipped,
                "skip_reason": s.skip_reason,
            }
            for s in summaries
        ],
        "prefilter_rules": rules,
        "clashes": [
            {
                "scenario_id": r.scenario_id,
                **r.assessment.to_dict(),
            }
            for r in all_records
        ],
    }
