"""Switch the live tasks.py from the in-process shadow run to the deferred job.
Fails the build if the anchor is absent."""
p = "/app/tasks.py"
s = open(p).read()
old = "shadow.write_record(shadow.shadow_compare(ifc_path, ids_path, results, job=job))"
assert s.count(old) == 1, "anchor not found exactly once"
s = s.replace(old, "shadow.defer(ifc_path, ids_path, results, job=job)")
open(p, "w").write(s)
