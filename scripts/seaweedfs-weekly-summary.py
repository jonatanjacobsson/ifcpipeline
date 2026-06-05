#!/usr/bin/env python3
"""SeaweedFS pilot — weekly summary aggregator.

Reads every parity-*.json + smoke-*.json under /reports (or --reports-dir),
produces SEAWEEDFS_PILOT_REPORT.md with:

- Time series of object-count drift
- Cumulative shadow PUT success / failure / latency p50/p99
- Daily smoke pass/fail matrix
- Lifecycle behaviour per day
- Top-5 ever-drifted keys with timestamps
- GO / NO-GO / EXTEND_PILOT recommendation

Run on day 7:

  docker compose run --rm parity-monitor \\
      python /scripts/seaweedfs-weekly-summary.py \\
      --reports-dir /reports --out /reports/SEAWEEDFS_PILOT_REPORT.md

It only reads files; safe to re-run any time during or after the pilot.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _load_jsons(pattern: str) -> List[Dict[str, Any]]:
    """Load every JSON matching `pattern`, skipping the rolling
    `*-latest.json` files (which are byte-for-byte copies of the most
    recent timestamped report)."""
    out: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(pattern)):
        if os.path.basename(path).endswith("-latest.json"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception as e:
            print(f"WARN: could not parse {path}: {e}", file=sys.stderr)
    return out


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _drift_series(parity: List[Dict[str, Any]]) -> List[Tuple[str, int, int, int, int]]:
    out: List[Tuple[str, int, int, int, int]] = []
    for r in parity:
        if r.get("level") == "error":
            continue
        drift = r.get("drift") or {}
        out.append((
            r.get("ts", "?"),
            int((r.get("primary") or {}).get("count") or 0),
            int((r.get("shadow") or {}).get("count") or 0),
            int(drift.get("only_in_primary_count") or 0),
            int(drift.get("only_in_shadow_count") or 0),
        ))
    return out


def _top_drift(parity: List[Dict[str, Any]], top: int = 5) -> List[Tuple[str, int]]:
    counter: Counter = Counter()
    for r in parity:
        for key in (r.get("drift") or {}).get("only_in_primary", []) or []:
            counter[key] += 1
        for key in (r.get("drift") or {}).get("only_in_shadow", []) or []:
            counter[key] += 1
    return counter.most_common(top)


def _shadow_counters_summary(parity: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the latest non-empty shadow-counters block from the parity
    reports so we don't have to read shadow-counter.json separately."""
    for r in reversed(parity):
        sc = r.get("shadow_counters") or {}
        if isinstance(sc, dict) and (sc.get("success") or sc.get("failure")):
            return sc
    return {}


def _smoke_matrix(smoke: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in smoke:
        steps = r.get("steps") or {}
        rows.append({
            "ts": r.get("ts"),
            "level": r.get("level", "?"),
            "lifecycle_applied": (steps.get("lifecycle") or {}).get("applied"),
            "put_v1_ok": (steps.get("put_v1") or {}).get("size_match"),
            "put_v2_different_version": (steps.get("put_v2") or {}).get("different_version"),
            "list_versions_match": (steps.get("list_versions") or {}).get("match"),
            "lifecycle_reaped": (steps.get("lifecycle_reap") or {}).get("reaped"),
            "lifecycle_elapsed_s": (steps.get("lifecycle_reap") or {}).get("elapsed_s"),
            "cleanup_ok": (steps.get("cleanup") or {}).get("deleted"),
        })
    return rows


def _audit_mismatch_count(parity: List[Dict[str, Any]]) -> int:
    return sum(len((r.get("audit") or {}).get("mismatched") or []) for r in parity)


def _recommendation(
    *,
    drift_series: List[Tuple[str, int, int, int, int]],
    shadow_counters: Dict[str, Any],
    smoke_rows: List[Dict[str, Any]],
    audit_mismatches: int,
    parity_errors: int,
) -> Tuple[str, List[str]]:
    """Apply the criteria from SEAWEEDFS_PILOT.md §5."""
    reasons: List[str] = []
    verdict = "GO"

    success = int(shadow_counters.get("success") or 0)
    failure = int(shadow_counters.get("failure") or 0)
    total = success + failure
    if total == 0:
        reasons.append("No shadow PUTs observed (counter file empty) — extend pilot to gather signal.")
        verdict = "EXTEND_PILOT"
    else:
        rate = success / float(total)
        if rate < 0.999:
            reasons.append(
                f"Shadow PUT success rate {rate*100:.3f}% (success={success} failure={failure}) below 99.9% threshold."
            )
            verdict = "NO-GO"
        else:
            reasons.append(
                f"Shadow PUT success rate {rate*100:.3f}% (success={success} failure={failure}) within threshold."
            )

    # Sustained drift = both consecutive non-zero only_in_* counts.
    sustained_pairs = 0
    for prev, cur in zip(drift_series, drift_series[1:]):
        if prev[3] > 0 and cur[3] > 0:
            sustained_pairs += 1
        if prev[4] > 0 and cur[4] > 0:
            sustained_pairs += 1
    if sustained_pairs > 0:
        reasons.append(f"Drift persisted across {sustained_pairs} consecutive parity ticks — investigate before cutover.")
        if verdict == "GO":
            verdict = "EXTEND_PILOT"

    if audit_mismatches > 0:
        reasons.append(f"Audit cross-check found {audit_mismatches} mismatched samples (primary or shadow version_id != DB row).")
        verdict = "NO-GO"

    # Smoke must be green every day.
    failed_smokes = [s for s in smoke_rows if s.get("level") != "ok"]
    lifecycle_failures = [s for s in smoke_rows if s.get("lifecycle_reaped") is False]
    if failed_smokes:
        reasons.append(f"{len(failed_smokes)}/{len(smoke_rows)} daily smoke iteration(s) flagged warn/error.")
        if verdict == "GO":
            verdict = "EXTEND_PILOT"
    if lifecycle_failures:
        reasons.append(
            f"{len(lifecycle_failures)} day(s) saw non-current versions NOT reaped within the wait window — "
            "lifecycle is broken on this SeaweedFS build."
        )
        verdict = "NO-GO"

    if parity_errors > 0:
        reasons.append(f"{parity_errors} parity ticks were level=error (parity loop itself crashed).")

    if not reasons:
        reasons.append("All criteria met within thresholds.")
    return verdict, reasons


def _md_table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return f"| {' | '.join(headers)} |\n|{'|'.join(['---'] * len(headers))}|\n| _(no rows)_ |"
    out = [f"| {' | '.join(headers)} |", f"|{'|'.join(['---'] * len(headers))}|"]
    for r in rows:
        out.append("| " + " | ".join(("" if c is None else str(c)) for c in r) + " |")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports-dir", default="/reports", help="Directory containing parity-*.json / smoke-*.json")
    ap.add_argument("--out", default=None, help="Output markdown path (default: <reports-dir>/SEAWEEDFS_PILOT_REPORT.md)")
    ap.add_argument("--keep-last", type=int, default=None, help="Limit to the last N parity reports (sort by filename)")
    args = ap.parse_args(argv)

    parity_pattern = os.path.join(args.reports_dir, "parity-*.json")
    smoke_pattern = os.path.join(args.reports_dir, "smoke-*.json")

    parity = _load_jsons(parity_pattern)
    # Filter out the rolling "parity-latest.json" duplicate if it sneaks in.
    parity = [p for p in parity if p.get("ts")]
    if args.keep_last is not None:
        parity = parity[-args.keep_last :]
    parity_errors = sum(1 for p in parity if p.get("level") == "error")
    smoke = [s for s in _load_jsons(smoke_pattern) if s.get("ts") and "smoke-latest.json" not in (s.get("_path") or "")]

    drift = _drift_series(parity)
    top_drift = _top_drift(parity, top=5)
    shadow_counters = _shadow_counters_summary(parity)
    smoke_rows = _smoke_matrix(smoke)
    audit_mismatches = _audit_mismatch_count(parity)

    verdict, reasons = _recommendation(
        drift_series=drift,
        shadow_counters=shadow_counters,
        smoke_rows=smoke_rows,
        audit_mismatches=audit_mismatches,
        parity_errors=parity_errors,
    )

    success = int(shadow_counters.get("success") or 0)
    failure = int(shadow_counters.get("failure") or 0)
    total_puts = success + failure
    # The parity report stores cumulative sum_elapsed_ms only; we don't have
    # per-PUT samples here, so just emit the running mean.
    sum_ms = float(shadow_counters.get("sum_elapsed_ms") or 0.0)
    mean_ms = sum_ms / total_puts if total_puts else None

    out_path = args.out or os.path.join(args.reports_dir, "SEAWEEDFS_PILOT_REPORT.md")
    now_iso = datetime.now(timezone.utc).isoformat()

    md = []
    md.append(f"# SeaweedFS dual-write pilot — weekly summary\n")
    md.append(f"_Generated {now_iso}_\n")
    md.append(f"**Verdict: `{verdict}`**\n")
    md.append("Why:\n")
    for r in reasons:
        md.append(f"- {r}")
    md.append("")

    md.append("## Shadow PUT counters\n")
    md.append(_md_table(
        ["success", "failure", "success_rate", "mean_elapsed_ms", "host", "updated_at"],
        [[
            success, failure,
            f"{(success/total_puts*100):.3f}%" if total_puts else "n/a",
            f"{mean_ms:.1f}" if mean_ms is not None else "n/a",
            shadow_counters.get("host"),
            shadow_counters.get("updated_at"),
        ]],
    ))
    md.append("")

    by_op = shadow_counters.get("by_op") or {}
    if by_op:
        md.append("### Per-operation breakdown\n")
        md.append(_md_table(
            ["operation", "success", "failure"],
            [[op, v.get("success", 0), v.get("failure", 0)] for op, v in sorted(by_op.items())],
        ))
        md.append("")

    md.append("## Object-count drift over time\n")
    md.append(_md_table(
        ["tick_ts", "primary", "shadow", "only_in_primary", "only_in_shadow"],
        [[ts, p, s, op_, os_] for ts, p, s, op_, os_ in drift],
    ))
    md.append("")

    md.append("## Top ever-drifted keys\n")
    md.append(_md_table(
        ["key", "ticks_observed"],
        [[k, c] for k, c in top_drift],
    ))
    md.append("")

    md.append("## Daily smoke matrix\n")
    md.append(_md_table(
        ["ts", "level", "lifecycle_applied", "put_v1_ok", "v2_new_version", "2_versions_listed", "noncurrent_reaped", "lifecycle_elapsed_s", "cleanup_ok"],
        [[
            s.get("ts"), s.get("level"),
            s.get("lifecycle_applied"), s.get("put_v1_ok"), s.get("put_v2_different_version"),
            s.get("list_versions_match"), s.get("lifecycle_reaped"),
            s.get("lifecycle_elapsed_s"), s.get("cleanup_ok"),
        ] for s in smoke_rows],
    ))
    md.append("")

    md.append("## Audit cross-check (samples per tick)\n")
    audit_total = sum((r.get("audit") or {}).get("checked", 0) for r in parity)
    legacy_null_total = sum((r.get("audit") or {}).get("legacy_null", 0) for r in parity)
    md.append(f"- Total sample-key audits performed: **{audit_total}**")
    md.append(f"- Mismatched samples (primary/shadow VersionId != `object_versions` row): **{audit_mismatches}**")
    md.append(
        f"- Benign legacy samples (object on shadow but audit row predates dual-write, "
        f"`shadow_version_id` NULL): **{legacy_null_total}** — not counted as defects"
    )
    md.append("")

    md.append("## Parity loop health\n")
    md.append(f"- Total parity ticks parsed: **{len(parity)}**")
    md.append(f"- Ticks with `level=error` (loop itself crashed): **{parity_errors}**")
    md.append("")

    md.append("---")
    md.append("")
    md.append("Run again any time: `docker compose -f docker-compose.yml -f docker-compose.seaweedfs.yml run --rm parity-monitor python /scripts/seaweedfs-weekly-summary.py`.\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Wrote {out_path} (verdict={verdict})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
