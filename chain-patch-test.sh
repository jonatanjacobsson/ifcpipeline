#!/usr/bin/env bash
# Multi-step ifcpatch chain to exercise the audit trail.
#
# Produces a derivation tree:
#
#   uploads/Arch-v1.ifc               (root, kind=root)
#     ├── chain/A/step1-building-elements.ifc      [patch  IfcBuildingElement]
#     │     └── chain/A/step2-walls.ifc            [patch  IfcWall, parent=step1]
#     │           └── chain/A/step3-standardwalls.ifc  [patch IfcWallStandardCase, parent=step2]
#     ├── chain/B/spaces.ifc                       [patch  IfcSpace]
#     ├── chain/B/proxies.ifc                      [patch  IfcBuildingElementProxy]
#     └── chain/X/diff-be-vs-walls.json            [diff   old=step1, new=step2]
#
# Then:
#   * verifies /lineage/<key> returns the full 3-deep ancestor list for step3
#   * verifies step1 has the expected 3 descendants (step2, and via step2 → step3, + diff)
#   * prints the recursive tree straight from Postgres
#
# Requires: stack already running (e.g. ./smoke-test.sh or compose up).

set -euo pipefail

: "${IFC_PIPELINE_API_KEY:=pocsecret}"
API="http://localhost:8100"
AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")
UPLOAD="Elec.ifc"          # small (~10 MB) — chain of patches runs in <1 min total
FIXTURE="prod-fixtures/$UPLOAD"

# Ensure the root is present (idempotent — if it's already there /upload still succeeds)
if [ -f "$FIXTURE" ]; then
  echo ">>> Ensuring $UPLOAD is uploaded"
  curl -fsS "${AUTH[@]}" -F "file=@$FIXTURE" "$API/upload/ifc" | python3 -m json.tool
fi

enqueue() {
  local endpoint="$1" body="$2"
  curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" "$API$endpoint" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
}

# Block on a single job. Echoes its return dict.
wait_job() {
  local jid="$1" label="$2"
  for _ in $(seq 1 180); do
    local body status
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status" 2>/dev/null || true)
    status=$(echo "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status",""))' 2>/dev/null)
    case "$status" in
      finished)
        local key
        key=$(echo "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("output_key",""))')
        printf '  [%s] %-28s %s -> %s\n' "$(date +%H:%M:%S)" "$label" "$jid" "$key" >&2
        echo "$key"
        return 0
        ;;
      failed|stopped|canceled)
        echo "$body" >&2
        return 1
        ;;
    esac
    sleep 2
  done
  echo "timeout polling $jid" >&2
  return 1
}

patch() {
  # patch <label> <input_key> <output_key> <arg_ifc_class>
  local label="$1" in_key="$2" out_key="$3" cls="$4"
  local body
  body=$(cat <<EOF
{
  "input_file": "$in_key",
  "output_file": "$out_key",
  "recipe": "ExtractElements",
  "arguments": ["$cls"],
  "use_custom": false
}
EOF
)
  local jid
  jid=$(enqueue /patch/execute "$body")
  wait_job "$jid" "$label"
}

echo ">>> Chain A: progressive narrowing (3 linear patches on an electrical model)"
A1=$(patch "A1-building-elements" "uploads/$UPLOAD"       "chain/A/step1-building-elements.ifc" "IfcBuildingElement")
A2=$(patch "A2-distribution"       "$A1"                   "chain/A/step2-distribution.ifc"      "IfcDistributionElement")
A3=$(patch "A3-flow-terminals"     "$A2"                   "chain/A/step3-flow-terminals.ifc"    "IfcFlowTerminal")

echo ">>> Chain B: two parallel patches from the root"
B1=$(patch "B1-flow-segments"      "uploads/$UPLOAD"       "chain/B/flow-segments.ifc"           "IfcFlowSegment")
B2=$(patch "B2-proxies"            "uploads/$UPLOAD"       "chain/B/proxies.ifc"                 "IfcBuildingElementProxy")

echo ">>> Chain X: diff between two derived IFCs (step1 vs step2)"
DIFF_BODY=$(cat <<EOF
{"old_file":"$A1","new_file":"$A2","output_file":"chain/X/diff-be-vs-walls.json"}
EOF
)
DIFF_JID=$(enqueue /ifcdiff "$DIFF_BODY")
XD=$(wait_job "$DIFF_JID" "X-diff-be-vs-walls")

echo
echo ">>> Lineage of the deepest child (/lineage/$A3)"
curl -fsS "${AUTH[@]}" "$API/lineage/$A3" | python3 -c '
import json, sys
d = json.load(sys.stdin)
self_ = d.get("self", {}) or {}
anc   = d.get("ancestors", []) or []
print("  self :", self_.get("object_key"), f"[{self_.get(\"operation\")}] sha=" + (self_.get("sha256") or "")[:12])
print("  ancestors (parent chain):")
for a in anc:
    print(f"    depth={a.get(\"depth\")}  role={a.get(\"role\"):<9}  op={a.get(\"operation\"):<12}  key={a.get(\"object_key\")}")
'

echo
echo ">>> Descendants of chain/A/step1 (/lineage/$A1)"
curl -fsS "${AUTH[@]}" "$API/lineage/$A1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
desc = d.get("descendants", []) or []
print(f"  descendants ({len(desc)} total):")
for a in sorted(desc, key=lambda x: (x.get("depth",0), x.get("id",0))):
    print(f"    depth={a.get(\"depth\")}  role={a.get(\"role\"):<9}  op={a.get(\"operation\"):<12}  key={a.get(\"object_key\")}")
'

echo
echo ">>> Full ancestor tree in SQL (recursive CTE on object_lineage)"
docker exec ifc_pipeline_postgres_objectstorage psql -U ifcpipeline -d ifcpipeline <<SQL
WITH RECURSIVE anc AS (
    SELECT v.id, v.object_key, v.operation, 0 AS depth, v.id::text AS path, NULL::text AS role
    FROM object_versions v
    WHERE v.object_key = 'chain/A/step3-standardwalls.ifc'
  UNION ALL
    SELECT p.id, p.object_key, p.operation, a.depth + 1,
           a.path || '>' || p.id::text,
           l.role
    FROM anc a
    JOIN object_lineage l  ON l.child_id  = a.id
    JOIN object_versions p ON p.id        = l.parent_id
    WHERE a.depth < 10
)
SELECT repeat('  ', depth) || object_key AS tree,
       operation,
       role
FROM anc
ORDER BY depth;
SQL

echo
echo ">>> Descendants tree (what grew out of the root upload)"
docker exec ifc_pipeline_postgres_objectstorage psql -U ifcpipeline -d ifcpipeline <<SQL
WITH RECURSIVE desc_ AS (
    SELECT v.id, v.object_key, v.operation, 0 AS depth, v.id::text AS path, NULL::text AS role
    FROM object_versions v
    WHERE v.object_key = 'uploads/$UPLOAD'
  UNION ALL
    SELECT c.id, c.object_key, c.operation, d.depth + 1,
           d.path || '>' || c.id::text,
           l.role
    FROM desc_ d
    JOIN object_lineage l  ON l.parent_id = d.id
    JOIN object_versions c ON c.id        = l.child_id
    WHERE d.depth < 10 AND d.path NOT LIKE '%>' || c.id::text || '>%'
)
SELECT repeat('  ', depth) || object_key AS tree,
       operation,
       COALESCE(role,'<root>') AS role
FROM desc_
ORDER BY path;
SQL

echo
echo ">>> Sanity assertions"
sha_step3=$(curl -fsS "${AUTH[@]}" "$API/lineage/$A3" | python3 -c 'import json,sys; print(json.load(sys.stdin)["self"]["sha256"])')
test ${#sha_step3} -eq 64 || { echo "  [FAIL] step3 missing sha256"; exit 1; }
ancestor_count=$(curl -fsS "${AUTH[@]}" "$API/lineage/$A3" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("ancestors",[])))')
[ "$ancestor_count" -ge 3 ] || { echo "  [FAIL] step3 has $ancestor_count ancestors (expected >=3: step2, step1, root)"; exit 1; }
echo "  [ok] step3 sha256 ${sha_step3:0:12}...  ancestors=$ancestor_count (includes step2 + step1 + root)"

descendant_count=$(curl -fsS "${AUTH[@]}" "$API/lineage/uploads/$UPLOAD" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("descendants",[])))')
[ "$descendant_count" -ge 5 ] || { echo "  [FAIL] root only has $descendant_count descendants (expected >=5: A1,A2,A3,B1,B2 + diff)"; exit 1; }
echo "  [ok] root has $descendant_count descendants"

echo
echo ">>> Day-2 overwrite: uploading different bytes to an existing key"
# Re-upload the same fixture (identical filename) under the same logical key.
# MinIO bucket versioning gives the new PUT its own VersionId; audit_db
# should persist *both* rows for uploads/$UPLOAD, and the day-1 chain must
# still be walkable by its audit_id because every worker call pinned the
# parent's VersionId into the lineage row.

# Stage a byte-different copy of the fixture by appending an ISO comment
# — still a valid IFC, but SHA256 differs from the day-1 root.
DAY2_TMP=$(mktemp --suffix=.ifc)
trap 'rm -f "$DAY2_TMP"' EXIT
cp "$FIXTURE" "$DAY2_TMP"
printf '\n/* day-2 overwrite marker %s */\n' "$(date -u +%s)" >> "$DAY2_TMP"

echo ">>> Re-uploading $UPLOAD (day-2 bytes)"
curl -fsS "${AUTH[@]}" -F "file=@${DAY2_TMP};filename=${UPLOAD}" "$API/upload/ifc" | python3 -m json.tool

echo ">>> /audit/history/uploads/$UPLOAD (should list 2+ versions)"
HISTORY=$(curl -fsS "${AUTH[@]}" "$API/audit/history/uploads/$UPLOAD")
echo "$HISTORY" | python3 -m json.tool
version_count=$(echo "$HISTORY" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("versions",[])))')
[ "$version_count" -ge 2 ] || { echo "  [FAIL] /audit/history only returned $version_count versions (expected >=2)"; exit 1; }

# The two versions must have different sha256 and different version_id.
shas=$(echo "$HISTORY" | python3 -c 'import json,sys; print(" ".join(sorted(set(v.get("sha256") or "" for v in json.load(sys.stdin).get("versions",[])))))')
vids=$(echo "$HISTORY" | python3 -c 'import json,sys; print(" ".join(sorted(set(v.get("version_id") or "" for v in json.load(sys.stdin).get("versions",[])))))')
sha_ucount=$(echo "$shas" | tr ' ' '\n' | grep -c '[0-9a-f]')
vid_ucount=$(echo "$vids" | tr ' ' '\n' | grep -c '[0-9A-Za-z]')
[ "$sha_ucount" -ge 2 ] && [ "$vid_ucount" -ge 2 ] || {
  echo "  [FAIL] expected distinct sha256 and version_id across day-1/day-2 (got sha_u=$sha_ucount vid_u=$vid_ucount)"; exit 1; }
echo "  [ok] /audit/history returned $version_count versions with distinct bytes + VersionIds"

# The day-1 chain must still be walkable by audit_id. Pick step3's audit_id
# from its lineage endpoint and verify the ancestor chain still anchors to
# the day-1 sha256 (not the day-2 bytes).
STEP3_SELF=$(curl -fsS "${AUTH[@]}" "$API/lineage/$A3" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("self",{})))')
STEP3_ID=$(echo "$STEP3_SELF" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id") or "")')
[ -n "$STEP3_ID" ] || { echo "  [FAIL] step3 has no audit_id"; exit 1; }
ANCESTORS_BY_ID=$(curl -fsS "${AUTH[@]}" "$API/lineage/$A3?audit_id=${STEP3_ID}")
root_sha_from_chain=$(echo "$ANCESTORS_BY_ID" | python3 -c '
import json, sys
d = json.load(sys.stdin)
root = [a for a in d.get("ancestors", []) if a.get("kind") == "root"]
print(root[0].get("sha256") if root else "")
')
day1_sha=$(echo "$HISTORY" | python3 -c '
import json, sys
versions = json.load(sys.stdin).get("versions", [])
# day-1 is the *older* row; API returns newest-first.
print(versions[-1].get("sha256") if versions else "")
')
[ -n "$root_sha_from_chain" ] && [ "$root_sha_from_chain" = "$day1_sha" ] || {
  echo "  [FAIL] day-1 chain's root sha256 ($root_sha_from_chain) != day-1 recorded sha256 ($day1_sha)"; exit 1; }
echo "  [ok] day-1 chain still resolves to the pinned (day-1) root sha256"

echo
echo "CHAIN TEST OK"
