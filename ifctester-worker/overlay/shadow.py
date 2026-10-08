"""Shadow-run ifcfast's native IDS engine next to IfcTester and log differences.

IfcTester stays authoritative. Nothing here may raise into, alter or delay-fail
a job: ``shadow_compare`` swallows every exception and returns an error record.
Records carry no GUIDs or IDS/pset/property names in ``mismatches`` (only the
spec/requirement index, facet type, cardinality and dataType), so a summary can
be shared upstream; job identifiers live in the top-level ``job`` fields only.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

DEFAULT_MAX_MB = 150


def enabled() -> bool:
    return os.environ.get("IDS_SHADOW", "").strip().lower() in ("1", "true", "yes")


def _guid(e):
    return e.get("global_id") or e.get("id")


def compare_reports(fast: dict, tester: dict) -> dict:
    """Compare two IfcTester-shaped JSON reports; return counts + mismatches."""
    sa, sb = fast["specifications"], tester["specifications"]
    mm = []
    nreq = 0
    if len(sa) != len(sb):
        mm.append({"spec": None, "kind": "spec_count", "fast": len(sa), "tester": len(sb)})
    for si, (x, y) in enumerate(zip(sa, sb)):
        def add(kind, **k):
            mm.append({"spec": si, "kind": kind, "spec_card": x.get("cardinality"), **k})

        if (x["status"], x["is_skipped"]) != (y["status"], y["is_skipped"]):
            add("spec_status", fast=[x["status"], x["is_skipped"]], tester=[y["status"], y["is_skipped"]])
        ca = (x["total_applicable"], x["total_applicable_pass"], x["total_applicable_fail"])
        cb = (y["total_applicable"], y["total_applicable_pass"], y["total_applicable_fail"])
        if ca != cb:
            add("spec_counts", fast=list(ca), tester=list(cb))
        if {_guid(e) for e in x.get("applicable_entities", [])} != {_guid(e) for e in y.get("applicable_entities", [])}:
            add("applicable_set")
        if len(x["requirements"]) != len(y["requirements"]):
            add("req_count", fast=len(x["requirements"]), tester=len(y["requirements"]))
            continue
        for ri, (p, q) in enumerate(zip(x["requirements"], y["requirements"])):
            nreq += 1
            md = q.get("metadata", {})
            d = {"req": ri, "facet": q.get("facet_type"), "card": md.get("@cardinality"), "dtype": md.get("@dataType")}
            if p["status"] != q["status"] or (p["total_pass"], p["total_fail"]) != (q["total_pass"], q["total_fail"]):
                add("req_counts", **d, fast=[p["status"], p["total_pass"], p["total_fail"]],
                    tester=[q["status"], q["total_pass"], q["total_fail"]])
            fp = {_guid(e) for e in p["failed_entities"]}
            fq = {_guid(e) for e in q["failed_entities"]}
            if fp != fq:
                add("fail_set", **d, only_fast=len(fp - fq), only_tester=len(fq - fp), common=len(fp & fq))
            elif {e.get("reason") for e in p["failed_entities"]} != {e.get("reason") for e in q["failed_entities"]}:
                add("reason_text", **d)
    return {"n_specs": len(sb), "n_requirements": nreq, "mismatches": mm}


def shadow_compare(ifc_path: str, ids_path: str, tester_report: dict, *, job: dict | None = None) -> dict:
    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "job": job or {},
        "ifc_mb": None,
    }
    try:
        rec["ifc_mb"] = round(os.path.getsize(ifc_path) / 1e6, 1)
        limit = float(os.environ.get("IDS_SHADOW_MAX_MB", DEFAULT_MAX_MB))
        if rec["ifc_mb"] > limit:
            rec["skipped"] = f"size>{limit:g}MB"
            return rec
        import ifcfast

        rec["ifcfast"] = getattr(ifcfast, "__version__", None)
        t = time.time()
        fast = ifcfast.open(ifc_path, write_cache=False).validate_ids(ids_path, on_unsupported="mark").to_ifctester_json(ids_index=0)
        rec["fast_s"] = round(time.time() - t, 2)
        rec.update(compare_reports(fast, tester_report))
        rec["identical"] = not rec["mismatches"]
    except BaseException as e:  # noqa: BLE001 - shadow must never affect the job
        rec["error"] = type(e).__name__
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    return rec


def write_record(rec: dict) -> None:
    """Append one JSON line to the shadow log dir and to the worker log."""
    line = json.dumps(rec, default=str)
    logger.info("IDS_SHADOW %s", line)
    try:
        d = os.environ.get("IDS_SHADOW_DIR", "/output/ids/shadow")
        os.makedirs(d, exist_ok=True)
        day = rec["ts"][:10]
        with open(os.path.join(d, f"{day}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("shadow record file write failed: %s", e)


# --------------------------------------------------------------------------- #
#  Deferred mode: run the comparison as its own RQ job after IfcTester's job   #
# --------------------------------------------------------------------------- #
#
# Run in-process, ifcfast's model sat on top of IfcTester's model and full
# report and pushed a 101 MB file to 4.5 GB, so the work horse was OOM-killed
# and the real IDS job failed with it. ``defer`` instead writes IfcTester's
# untruncated results to disk and enqueues ``run_deferred`` on the same queue.
# The worker runs one job at a time, so the comparison starts after the IDS job
# has returned, in a fresh work horse; if it dies, only the shadow job fails.

PENDING_TTL_S = 24 * 3600


def _pending_dir() -> str:
    return os.path.join(os.environ.get("IDS_SHADOW_DIR", "/output/ids/shadow"), "pending")


def _ifc_stamp(path: str) -> list:
    st = os.stat(path)
    return [st.st_size, int(st.st_mtime)]


def defer(ifc_path: str, ids_path: str, tester_report: dict, *, job: dict | None = None) -> None:
    """Hand the comparison to a follow-up job. Never raises."""
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "job": job or {}}
    try:
        rec["ifc_mb"] = round(os.path.getsize(ifc_path) / 1e6, 1)
        limit = float(os.environ.get("IDS_SHADOW_MAX_MB", DEFAULT_MAX_MB))
        if rec["ifc_mb"] > limit:
            rec["skipped"] = f"size>{limit:g}MB"
            write_record(rec)
            return

        d = _pending_dir()
        os.makedirs(d, exist_ok=True)
        now = time.time()
        for name in os.listdir(d):  # leftovers from shadow jobs that were killed
            p = os.path.join(d, name)
            try:
                if now - os.path.getmtime(p) > PENDING_TTL_S:
                    os.remove(p)
            except OSError:
                pass

        import uuid
        from rq import Queue, get_current_job

        current = get_current_job()
        if current is None:
            raise RuntimeError("not inside an RQ job")
        results_path = os.path.join(d, f"{uuid.uuid4().hex}.json")
        with open(results_path, "w", encoding="utf-8") as fh:
            json.dump(tester_report, fh, default=str)
        Queue(current.origin, connection=current.connection).enqueue(
            "shadow.run_deferred",
            ifc_path, ids_path, results_path, _ifc_stamp(ifc_path), dict(job or {}, tester_job_id=current.id),
            job_timeout=1800,
            result_ttl=0,
            failure_ttl=7 * 24 * 3600,
            description=f"ifcfast IDS shadow for {os.path.basename(ifc_path)}",
        )
    except BaseException as e:  # noqa: BLE001 - shadow must never affect the job
        rec["error"] = f"defer: {type(e).__name__}"
        write_record(rec)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise


def run_deferred(ifc_path: str, ids_path: str, results_path: str, stamp: list, job: dict) -> None:
    """RQ job: compare ifcfast against the IfcTester report written by ``defer``."""
    try:
        with open(results_path, encoding="utf-8") as fh:
            tester_report = json.load(fh)
    finally:
        try:
            os.remove(results_path)
        except OSError:
            pass
    rec_job = dict(job or {}, deferred=True)
    try:
        changed = _ifc_stamp(ifc_path) != list(stamp)
    except OSError:
        changed = True
    if changed:
        # The pipeline patched or re-exported the file after IfcTester read it;
        # comparing would report differences that are not engine differences.
        write_record({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "job": rec_job, "skipped": "ifc changed since IfcTester ran",
        })
        return
    write_record(shadow_compare(ifc_path, ids_path, tester_report, job=rec_job))
