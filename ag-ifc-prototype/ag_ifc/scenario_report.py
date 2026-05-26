"""Summarize batch scenario results for AEC capability discovery."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ag_ifc.scenarios import ScenarioOutcome


def _rate(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def build_summary(outcomes: list[ScenarioOutcome]) -> dict[str, Any]:
    total = len(outcomes)
    setup_ok = sum(1 for o in outcomes if o.setup_ok)
    proven = sum(1 for o in outcomes if o.proven)
    with_expected = [o for o in outcomes if o.expected_match is not None]
    expected_pass = sum(1 for o in with_expected if o.expected_match)

    by_category: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "setup_ok": 0,
            "proven": 0,
            "setup_error": 0,
            "hypothesis_high_proven": 0,
            "hypothesis_high_total": 0,
        }
    )
    for o in outcomes:
        bucket = by_category[o.category]
        bucket["total"] += 1
        if o.setup_ok:
            bucket["setup_ok"] += 1
        if o.proven:
            bucket["proven"] += 1
        if not o.setup_ok:
            bucket["setup_error"] += 1
        if o.aec_utility_hypothesis == "high":
            bucket["hypothesis_high_total"] += 1
            if o.proven:
                bucket["hypothesis_high_proven"] += 1

    for cat, bucket in by_category.items():
        bucket["prove_rate_pct"] = _rate(bucket["proven"], bucket["total"])
        bucket["setup_ok_rate_pct"] = _rate(bucket["setup_ok"], bucket["total"])

    # AEC utility tiers from empirical prove rates (discovery mode)
    utility_tiers = _infer_utility_tiers(by_category, outcomes)

    return {
        "total_scenarios": total,
        "setup_ok": setup_ok,
        "setup_ok_rate_pct": _rate(setup_ok, total),
        "proven": proven,
        "prove_rate_pct": _rate(proven, total),
        "expected_checks": len(with_expected),
        "expected_pass": expected_pass,
        "by_category": dict(sorted(by_category.items())),
        "aec_utility_recommendations": utility_tiers,
    }


def _infer_utility_tiers(
    by_category: dict[str, dict[str, Any]],
    outcomes: list[ScenarioOutcome],
) -> list[dict[str, str]]:
    """Heuristic recommendations from batch results."""
    recs: list[dict[str, str]] = []

    def cat_rate(category: str) -> float:
        b = by_category.get(category, {})
        return float(b.get("prove_rate_pct", 0))

    if cat_rate("mep_coordination") >= 80:
        recs.append(
            {
                "tier": "STRONG",
                "aec_use": "Plan-view parallel MEP runs vs structure (post-offset verification)",
                "ag_role": "Certify para/coll/perp relations after coordinator moves duct/pipe axes",
            }
        )
    if cat_rate("negative_control") >= 50:
        recs.append(
            {
                "tier": "STRONG",
                "aec_use": "Reject invalid coordination claims",
                "ag_role": "DDAR fails or cannot prove when geometry does not satisfy stated rules",
            }
        )

    clearance = by_category.get("clearance_distance", {})
    if clearance.get("prove_rate_pct", 0) < 30:
        recs.append(
            {
                "tier": "WEAK",
                "aec_use": "Metric clearance (50 mm) via cong/distseq",
                "ag_role": "Use IfcClash + numeric solver; AG2 hits numerical assertion on forced cong",
            }
        )

    recs.append(
        {
            "tier": "NOT_APPLICABLE",
            "aec_use": "Solid–solid clash detection, volume penetration, optimal 3D routing",
            "ag_role": "IfcClash + pathfinder/MILP; AG does not consume IFC meshes",
        }
    )
    recs.append(
        {
            "tier": "MEDIUM",
            "aec_use": "Orthogonal route corners, angle equality between trades",
            "ag_role": "eqangle/aconst sometimes provable; validate per layout with scenario batch",
        }
    )
    return recs


def format_markdown(summary: dict[str, Any], outcomes: list[ScenarioOutcome]) -> str:
    lines = [
        "# AEC × AlphaGeometry2 scenario report",
        "",
        f"- **Scenarios run:** {summary['total_scenarios']}",
        f"- **Setup OK:** {summary['setup_ok']} ({summary['setup_ok_rate_pct']}%)",
        f"- **Proven:** {summary['proven']} ({summary['prove_rate_pct']}%)",
        "",
        "## By category",
        "",
        "| Category | Total | Setup OK | Proven | Prove % |",
        "|----------|------:|---------:|-------:|--------:|",
    ]
    for cat, b in summary["by_category"].items():
        lines.append(
            f"| {cat} | {b['total']} | {b['setup_ok']} | {b['proven']} | {b['prove_rate_pct']} |"
        )

    lines.extend(["", "## AEC utility recommendations", ""])
    for rec in summary.get("aec_utility_recommendations", []):
        lines.append(f"### {rec['tier']}: {rec['aec_use']}")
        lines.append(f"- **AG role:** {rec['ag_role']}")
        lines.append("")

    failed_setup = [o for o in outcomes if not o.setup_ok][:15]
    if failed_setup:
        lines.extend(["## Sample setup failures (numerical / inconsistent premises)", ""])
        for o in failed_setup:
            lines.append(f"- `{o.scenario_id}`: {o.error}")

    not_proven = [o for o in outcomes if o.setup_ok and not o.proven][:15]
    if not_proven:
        lines.extend(["", "## Sample goals not proved (setup OK)", ""])
        for o in not_proven:
            lines.append(f"- `{o.scenario_id}` ({o.category}): goal={o.goal}")

    return "\n".join(lines) + "\n"
