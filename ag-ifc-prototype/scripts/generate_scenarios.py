#!/usr/bin/env python3
"""Generate parametric AEC scenario grids for bulk AG2 evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scenarios" / "catalog_generated.json"


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def parallel_offset_scenarios() -> list[dict]:
    """MEP parallel runs at many offsets (mm-scale metres)."""
    items = []
    offsets_m = [round(x * 0.01, 2) for x in range(1, 101)]  # 0.01–1.00 m
    offsets_m += [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    for off in offsets_m:
        off_s = _fmt(off)
        items.append(
            {
                "id": f"gen_parallel_offset_{off_s.replace('.', 'p')}m",
                "category": "mep_coordination",
                "subcategory": "parallel_run_parametric",
                "aec_use_case": f"Parametric: duct offset {off_s} m parallel to beam axis",
                "aec_utility_hypothesis": "high",
                "ag2": (
                    f"a@0.0_0.0 = ; b@20.0_0.0 = ; c@0.0_{off_s} = ; d@20.0_{off_s} = "
                    f"para a b c d ? para c d a b"
                ),
                "expected": {"setup": "ok", "proven": True},
                "tags": ["generated", "parametric", "parallel"],
            }
        )
    return items


def span_length_scenarios() -> list[dict]:
    """Vary run length while keeping offset fixed."""
    items = []
    for length in range(2, 52, 2):
        items.append(
            {
                "id": f"gen_parallel_span_{length}m",
                "category": "mep_coordination",
                "subcategory": "span_length",
                "aec_use_case": f"Parallel run over {length} m span at 200 mm offset",
                "aec_utility_hypothesis": "high",
                "ag2": (
                    f"a@0.0_0.0 = ; b@{length}.0_0.0 = ; c@0.0_0.2 = ; d@{length}.0_0.2 = "
                    f"para a b c d ? para c d a b"
                ),
                "expected": {"setup": "ok", "proven": True},
                "tags": ["generated", "span"],
            }
        )
    return items


def clearance_metric_attempts() -> list[dict]:
    """Document metric clearance attempts (mostly setup_error expected)."""
    items = []
    for off in [0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.2, 0.25, 0.3]:
        off_s = _fmt(off)
        items.append(
            {
                "id": f"gen_clearance_cong_{off_s.replace('.', 'p')}m",
                "category": "clearance_distance",
                "subcategory": "metric_cong_parametric",
                "aec_use_case": f"Attempt exact cong clearance {off_s} m (often numerical failure)",
                "aec_utility_hypothesis": "low",
                "ag2": (
                    f"a@0.0_0.0 = ; b@6.0_0.0 = ; c@0.0_{off_s} = ; d@6.0_{off_s} = "
                    f"cong a c a d ? cong a c a d"
                ),
                "expected": {"setup": "setup_error", "proven": False},
                "tags": ["generated", "clearance", "metric"],
            }
        )
    return items


def perpendicular_crossing_grid() -> list[dict]:
    """Riser/crossing at varied station along beam."""
    items = []
    for station in range(1, 21):
        items.append(
            {
                "id": f"gen_perp_crossing_station_{station}m",
                "category": "mep_coordination",
                "subcategory": "perpendicular_crossing_parametric",
                "aec_use_case": f"Perpendicular riser at {station} m along corridor beam",
                "aec_utility_hypothesis": "high",
                "ag2": (
                    f"a@0.0_0.0 = ; b@25.0_0.0 = ; c@{station}.0_0.0 = ; d@{station}.0_2.5 = "
                    f"coll c a b, perp c d a b ? perp c d a b"
                ),
                "expected": {"setup": "ok", "proven": True},
                "tags": ["generated", "perpendicular"],
            }
        )
    return items


def main() -> int:
    scenarios = []
    scenarios.extend(parallel_offset_scenarios())
    scenarios.extend(span_length_scenarios())
    scenarios.extend(clearance_metric_attempts())
    scenarios.extend(perpendicular_crossing_grid())

    payload = {
        "version": "1.0.0",
        "description": "Auto-generated parametric AEC scenarios",
        "generated_count": len(scenarios),
        "scenarios": scenarios,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"Wrote {len(scenarios)} scenarios → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
