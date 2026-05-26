"""Global clash regression: detect new clashes after local fixes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


def clash_stable_id(clash: dict[str, Any]) -> str:
    """Stable pair key (IfcClash keys change when geometry moves)."""
    a = str(clash.get("a_global_id") or "")
    b = str(clash.get("b_global_id") or "")
    return "|".join(sorted((a, b)))


def clash_keys_from_result(result: dict[str, Any]) -> dict[str, str]:
    """Map stable_id -> ifcclash clash_key."""
    out: dict[str, str] = {}
    for key, data in result.get("clashes", {}).items():
        sid = clash_stable_id(data)
        out[sid] = key
    return out


@dataclass
class ClashSnapshot:
    stable_ids: set[str]
    clash_key_by_stable: dict[str, str]
    count: int
    clashes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_clash_result(cls, result: dict[str, Any]) -> ClashSnapshot:
        clashes = result.get("clashes", {})
        mapping = clash_keys_from_result(result)
        raw = {k: dict(v) for k, v in clashes.items()}
        return cls(
            stable_ids=set(mapping.keys()),
            clash_key_by_stable=mapping,
            count=len(clashes),
            clashes=raw,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "stable_ids": sorted(self.stable_ids),
        }


@dataclass
class RegressionReport:
    passed: bool
    baseline_count: int
    current_count: int
    resolved_stable_ids: list[str]
    remaining_stable_ids: list[str]
    new_global_clashes: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "baseline_count": self.baseline_count,
            "current_count": self.current_count,
            "resolved_count": len(self.resolved_stable_ids),
            "new_global_clash_count": len(self.new_global_clashes),
            "resolved_stable_ids": self.resolved_stable_ids,
            "remaining_stable_ids": self.remaining_stable_ids,
            "new_global_clashes": self.new_global_clashes,
            "message": self.message,
        }


def compare_regression(
    baseline: ClashSnapshot,
    current: ClashSnapshot,
    *,
    allow_new_global: bool = False,
    target_resolved: set[str] | None = None,
) -> RegressionReport:
    """
    Compare full clash sets after local fixes.

    Fails if new element-pair clashes appear (global regression) unless allow_new_global.
    """
    resolved = sorted(baseline.stable_ids - current.stable_ids)
    remaining = sorted(baseline.stable_ids & current.stable_ids)
    new_ids = sorted(current.stable_ids - baseline.stable_ids)

    passed = True
    messages: list[str] = []

    if new_ids and not allow_new_global:
        passed = False
        messages.append(f"{len(new_ids)} new global clash(es) after local fix")

    if target_resolved and not target_resolved.issubset(set(resolved)):
        missing = sorted(target_resolved - set(resolved))
        passed = False
        messages.append(f"target clash(s) not resolved: {missing[:3]}")

    if current.count > 0 and not remaining and not new_ids and baseline.count > current.count:
        messages.append("all baseline clashes cleared")

    if not messages:
        messages.append("regression ok")

    return RegressionReport(
        passed=passed,
        baseline_count=baseline.count,
        current_count=current.count,
        resolved_stable_ids=resolved,
        remaining_stable_ids=remaining,
        new_global_clashes=new_ids,
        message="; ".join(messages),
    )


def save_snapshot(path: Path, snapshot: ClashSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    payload["clashes"] = {sid: snapshot.clashes.get(sid, {}) for sid in snapshot.stable_ids}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_snapshot(path: Path) -> ClashSnapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    clashes = data.get("clashes", {})
    mapping = {sid: sid for sid in data.get("stable_ids", clashes.keys())}
    return ClashSnapshot(
        stable_ids=set(data.get("stable_ids", [])),
        clash_key_by_stable=mapping,
        count=int(data.get("count", len(clashes))),
        clashes=clashes,
    )
