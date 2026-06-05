"""
CoordinateClashesFromReport — ifc_coord worker recipe (Phase 4)

Runs the ``ifc_coord`` MEP clash coordination engine on a federated pair
(A = ``self.file`` input, B = ``file_b`` argument). Outputs:

- **Primary patched IFC** (side A work copy) via ``get_output()`` → worker S3
- **Sidecar artifacts** via ``get_artifacts()``:
  - ``proposals_json`` — full proposal + gate audit trail
  - ``bcf`` — BCF 2.1 topics (Resolved + In Progress) with clash viewpoints
  - ``manifest`` — CDE hand-off manifest (patched paths, applied fixes, moved GUIDs)
  - ``patched_b`` — partner IFC work copy when present (upload for federation review)

n8n orchestration pattern::

    IfcClash → IfcPatch(CoordinateClashesFromReport) → download artifacts → StreamBIM

Positional arguments (IfcPatch node order):

1. file_b — partner IFC (S3 key, s3:// URI, or local path)
2. clash_report — reserved for future baseline-from-report mode (optional today)
3. policy — inline JSON, local path, or S3 key (empty → defaults)
4. mode — ``propose_only`` | ``propose_and_apply``
5. max_rounds — default 10
6. max_auto_apply — default 20
7. output_prefix — case id / artifact prefix (optional)

Solver mode selection (next-phase): the fix engine is selected by
``policy.fix_planner.synthesis_mode``, which flows through the ``policy``
argument unchanged — no recipe code change is needed to switch engines:

- ``mtv_candidates`` (a.k.a. "nudge") — locked production baseline.
- ``route`` — sampling motion-planning reroute over the occupancy substrate
  (preset: ``scenarios/coord/nobel_elec_vs_mech_route.json``).
- ``global`` — corridor CP-SAT lane/track coordination with discipline-yield
  (preset: ``scenarios/coord/nobel_elec_vs_mech_global.json``).

``route`` / ``global`` are opt-in and must beat the locked nudge baseline on
``nobel_elec_acceptance.py --compare-modes`` before production promotion.

Recipe Name: CoordinateClashesFromReport
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import ifcopenshell

logger = logging.getLogger(__name__)

try:
    from shared import object_storage as s3
except Exception:  # pragma: no cover — local dev without shared
    s3 = None  # type: ignore[assignment]


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger,
        file_b: str = "",
        clash_report: str = "",
        policy: str = "",
        mode: str = "propose_only",
        max_rounds: int = 10,
        max_auto_apply: int = 20,
        output_prefix: str = "",
    ) -> None:
        self.file = file
        self.logger = logger
        self.file_b_arg = file_b
        self.clash_report_arg = clash_report
        self.policy_arg = policy
        self.mode = mode if mode in {"propose_only", "propose_and_apply"} else "propose_only"
        self.max_rounds = int(max_rounds) if str(max_rounds).strip() else 10
        self.max_auto_apply = int(max_auto_apply) if str(max_auto_apply).strip() else 20
        self.output_prefix = (output_prefix or "").strip()
        self._tempfiles: list[str] = []
        self._artifacts: dict[str, str] = {}
        self._result_summary: dict[str, Any] = {}

    def patch(self) -> None:
        try:
            from ifc_coord import run_coordination
            from ifc_coord.policy import Policy
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "ifc_coord is not installed in this worker image. "
                "Rebuild ifcpatch-worker with ifc-coord/ copied into the image "
                "(see docs/PHASE4_INTEGRATION.md)."
            ) from exc

        if not self.file_b_arg:
            raise ValueError("file_b is required (partner IFC path or S3 key)")

        path_a = self._resolve_to_local_file(self._input_path(), what="file_a")
        path_b = self._resolve_to_local_file(self.file_b_arg, what="file_b")
        policy_obj = self._load_policy()
        if self.clash_report_arg.strip():
            self.logger.info(
                "clash_report argument supplied but baseline-from-report is not "
                "wired yet; engine runs its own solid IfcClash baseline."
            )

        work_root = Path(os.environ.get("IFC_COORD_WORK", "/tmp/ifc_coord_jobs"))
        case_id = self.output_prefix or f"{Path(path_a).stem}__vs__{Path(path_b).stem}"
        output_dir = work_root / case_id

        self.logger.info(
            "[CoordinateClashesFromReport] mode=%s rounds=%d apply_cap=%d",
            self.mode,
            self.max_rounds,
            self.max_auto_apply,
        )

        result = run_coordination(
            Path(path_a),
            Path(path_b),
            case_id=case_id,
            mode=self.mode,  # type: ignore[arg-type]
            max_rounds=self.max_rounds,
            max_auto_apply=self.max_auto_apply,
            policy=policy_obj,
            output_dir=output_dir,
            work_root=work_root / "work",
        )

        self._result_summary = result.summary.to_dict()

        if result.report_json_path and Path(result.report_json_path).is_file():
            self._artifacts["proposals_json"] = result.report_json_path
        if result.bcf_path and Path(result.bcf_path).is_file():
            self._artifacts["bcf"] = result.bcf_path
        if result.manifest_path and Path(result.manifest_path).is_file():
            self._artifacts["manifest"] = result.manifest_path

        patched_a = Path(result.patched_ifc_a or result.work_dir) / Path(path_a).name
        if not patched_a.is_file() and result.patched_ifc_a:
            patched_a = Path(result.patched_ifc_a)
        if patched_a.is_file():
            self.file = ifcopenshell.open(str(patched_a))
        else:
            self.logger.warning("Patched A work copy not found at %s", patched_a)

        if result.patched_ifc_b and Path(result.patched_ifc_b).is_file():
            self._artifacts["patched_b"] = result.patched_ifc_b
        if result.fixed_only_ifc_a and Path(result.fixed_only_ifc_a).is_file():
            self._artifacts["fixed_only"] = result.fixed_only_ifc_a

        self.logger.info(
            "[CoordinateClashesFromReport] applied=%d proposed=%d rejected=%d "
            "final_clashes=%d artifacts=%s",
            result.summary.applied_count,
            result.summary.proposed_count,
            result.summary.rejected_count,
            result.summary.final_clash_count,
            list(self._artifacts.keys()),
        )

    def get_output(self) -> ifcopenshell.file:
        return self.file

    def get_artifacts(self) -> dict[str, str]:
        return dict(self._artifacts)

    def get_summary(self) -> dict[str, Any]:
        return dict(self._result_summary)

    # ------------------------------------------------------------------
    # Resolution helpers (same pattern as PropagatePropertyFromClashPairs)
    # ------------------------------------------------------------------

    def _input_path(self) -> str:
        staged = getattr(self.file, "_input_file_path", None)
        if staged:
            return str(staged)
        for env_key in ("IFCPATCH_INPUT_PATH", "IFCPATCH_FILE_PATH"):
            value = os.environ.get(env_key)
            if value:
                return value
        raise FileNotFoundError(
            "Cannot resolve input IFC path (set file._input_file_path in worker)"
        )

    def _resolve_to_local_file(self, ref: str, *, what: str) -> str:
        if not ref:
            raise FileNotFoundError(f"{what}: empty reference")
        for candidate in self._local_candidates(ref):
            if os.path.exists(candidate):
                self.logger.info("%s: using local file %s", what, candidate)
                return candidate
        if s3 is None or not s3.is_enabled():
            raise FileNotFoundError(
                f"{what}: cannot find {ref!r} on disk and object storage is disabled"
            )
        candidates: list[str] = []
        if ref.startswith("s3://"):
            _, _, rest = ref.partition("s3://")
            _, _, key = rest.partition("/")
            candidates.append(key)
        else:
            stripped = ref.lstrip("/")
            candidates.append(stripped)
            base = os.path.basename(stripped) or stripped
            candidates.append(s3.normalize_input_key(stripped))
            candidates.append(f"uploads/{base}")
            candidates.append(f"output/clash/{base}")

        seen: list[str] = []
        for key in candidates:
            if not key or key in seen:
                continue
            seen.append(key)
            if s3.object_exists(key):
                tmp = tempfile.NamedTemporaryFile(
                    delete=False,
                    prefix=f"coord-{what}-",
                    suffix=os.path.splitext(key)[1] or ".ifc",
                )
                tmp.close()
                self._tempfiles.append(tmp.name)
                self.logger.info(
                    "%s: downloading s3://%s/%s → %s",
                    what,
                    s3.bucket_name(),
                    key,
                    tmp.name,
                )
                s3.download_to_path(key, tmp.name)
                return tmp.name

        raise FileNotFoundError(
            f"{what}: {ref!r} not found locally or in s3://{s3.bucket_name()} "
            f"(tried keys: {seen})"
        )

    @staticmethod
    def _local_candidates(ref: str) -> list[str]:
        if not ref or ref.startswith("s3://"):
            return []
        candidates = [ref]
        if not ref.startswith("/"):
            candidates.append(os.path.join("/uploads", ref))
            candidates.append(os.path.join("/output", ref))
        if ref.startswith("uploads/"):
            candidates.append("/" + ref)
        if ref.startswith("output/"):
            candidates.append("/" + ref)
        return candidates

    def _load_policy(self):
        from ifc_coord.policy import Policy

        s = (self.policy_arg or "").strip()
        if not s:
            return Policy.default()
        if s.startswith("{"):
            try:
                return Policy.from_dict(json.loads(s))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid inline policy JSON: {exc}") from exc
        if Path(s).is_file():
            return Policy.from_file(s)
        if s3 is not None and s3.is_enabled():
            local = self._resolve_to_local_file(s, what="policy")
            return Policy.from_file(local)
        raise FileNotFoundError(f"Policy source not found: {s}")
