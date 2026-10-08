"""Insert the shadow hook into the (older) live tasks.py. Fails the build if the anchor is absent."""
p = "/app/tasks.py"
s = open(p).read()
anchor = "            json_reporter.report()\n            max_rows = _ifctester_json_max_rows_per_spec()\n"
assert s.count(anchor) == 1, "anchor not found exactly once"
hook = ("            json_reporter.report()\n"
        "            _maybe_shadow(ifc_path, ids_path, json_reporter.results)\n"
        "            max_rows = _ifctester_json_max_rows_per_spec()\n")
s = s.replace(anchor, hook)
func = '''
def _maybe_shadow(ifc_path, ids_path, results):
    """ifcfast IDS shadow comparison (IDS_SHADOW=1). Never raises."""
    try:
        import shadow
        if shadow.enabled():
            job = {"ifc": os.path.basename(ifc_path), "ids": os.path.basename(ids_path)}
            shadow.write_record(shadow.shadow_compare(ifc_path, ids_path, results, job=job))
    except Exception as e:  # noqa: BLE001
        logger.warning("ifcfast shadow failed: %s", type(e).__name__)

'''
# duplicate-pset patch applied at import time (live tasks.py validates in-process)
imp = "from shared.db_client import save_tester_result\n"
assert s.count(imp) == 1
s = s.replace(imp, imp + "import dup_pset_patch\ndup_pset_patch.apply()\n")
marker = "def run_ifctester_validation("
assert s.count(marker) == 1
s = s.replace(marker, func.lstrip("\n") + "\n" + marker)
open(p, "w").write(s)
