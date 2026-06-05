#!/bin/sh
# SeaweedFS pilot — one-shot initial backfill from MinIO into SeaweedFS.
#
# Runs inside the `seaweedfs-initial-sync` compose service (image
# quay.io/minio/mc:latest). Configures aliases for both backends, mirrors
# the contents of the MinIO bucket into the SeaweedFS bucket, then writes
# the post-mirror diff summary to /reports/initial-sync.json.
#
# Re-run safely: mc mirror is incremental.

set -eu

BUCKET="${S3_BUCKET:-ifcpipeline}"
SHADOW_BUCKET="${S3_SHADOW_BUCKET:-ifcpipeline}"
REPORT="/reports/initial-sync.json"

echo "[initial-sync] primary=http://minio:9000 bucket=${BUCKET}"
echo "[initial-sync] shadow=http://seaweedfs:8333 bucket=${SHADOW_BUCKET}"

mkdir -p /reports

# mc 2024+ uses `alias set`; older clients accept `config host add` too.
attempts=0
until mc alias set local http://minio:9000 "${S3_ACCESS_KEY}" "${S3_SECRET_KEY}" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "${attempts}" -gt 60 ]; then
        echo "[initial-sync] giving up waiting for minio alias"
        echo '{"status":"error","stage":"alias_minio"}' > "${REPORT}"
        exit 1
    fi
    echo "[initial-sync] waiting for minio alias..."
    sleep 1
done

attempts=0
until mc alias set seaweed http://seaweedfs:8333 "${S3_SHADOW_ACCESS_KEY}" "${S3_SHADOW_SECRET_KEY}" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "${attempts}" -gt 60 ]; then
        echo "[initial-sync] giving up waiting for seaweed alias"
        echo '{"status":"error","stage":"alias_seaweed"}' > "${REPORT}"
        exit 1
    fi
    echo "[initial-sync] waiting for seaweed alias..."
    sleep 1
done

started=$(date -u +%s)

# `mc mirror --preserve` keeps mtime + etag. mc skips identical objects by
# size+etag, so it's incremental and safe to re-run. The first run against
# a brand-new SeaweedFS often races at 100+ PUT/s and hits a few
# "connection refused" mid-flight; we retry up to MIRROR_MAX_RETRIES times
# until mc reports zero outbound diffs.
MIRROR_MAX_RETRIES="${MIRROR_MAX_RETRIES:-5}"
# Throttle the mirror upload to keep SeaweedFS volume server happy.
# At 124 MiB/s (mc's default) SeaweedFS' single-node volume server has
# been observed to mark volumes as "not writable" mid-stream, leading
# to connection-refused storms. 50 MiB/s is comfortable on a 1 GiB
# volume cap (~20s per volume rollover). Override via MIRROR_LIMIT_MBPS.
MIRROR_LIMIT_MBPS="${MIRROR_LIMIT_MBPS:-50}"
mirror_attempts=0
mirror_ok=0
while [ "${mirror_attempts}" -lt "${MIRROR_MAX_RETRIES}" ]; do
    mirror_attempts=$((mirror_attempts + 1))
    echo "[initial-sync] mc mirror attempt ${mirror_attempts}/${MIRROR_MAX_RETRIES} (limit-upload=${MIRROR_LIMIT_MBPS}MiB/s)..."
    if mc mirror --preserve --limit-upload "${MIRROR_LIMIT_MBPS}MiB" \
            "local/${BUCKET}" "seaweed/${SHADOW_BUCKET}" 2>&1; then
        remaining=$(mc diff "local/${BUCKET}" "seaweed/${SHADOW_BUCKET}" 2>/dev/null | wc -l | tr -d ' ')
        if [ "${remaining}" = "0" ]; then
            mirror_ok=1
            break
        fi
        echo "[initial-sync] post-mirror diff still ${remaining} lines; retrying..."
    else
        echo "[initial-sync] mc mirror returned non-zero on attempt ${mirror_attempts}, will retry"
    fi
    # Back-off: SeaweedFS volume server settles within ~10s after a
    # spike. Longer waits help when crash-recovery from a volume
    # integrity check is involved.
    sleep 10
done

elapsed=$(( $(date -u +%s) - started ))
echo "[initial-sync] mirror finished in ${elapsed}s (ok=${mirror_ok} attempts=${mirror_attempts})"

# Final `mc diff` snapshot. Count lines without grep (mc image has neither
# grep nor coreutils' awk on some recent variants; wc -l is universally
# available).
mc diff "local/${BUCKET}" "seaweed/${SHADOW_BUCKET}" > /tmp/diff.out 2>&1 || true
diff_count=$(wc -l < /tmp/diff.out | tr -d ' ')
if [ -z "${diff_count}" ]; then diff_count=0; fi
# Quick count check via mc ls. SeaweedFS reports a stable count once the
# bucket is settled.
primary_count=$(mc ls --recursive "local/${BUCKET}" 2>/dev/null | wc -l | tr -d ' ')
shadow_count=$(mc ls --recursive "seaweed/${SHADOW_BUCKET}" 2>/dev/null | wc -l | tr -d ' ')
if [ -z "${primary_count}" ]; then primary_count=0; fi
if [ -z "${shadow_count}" ]; then shadow_count=0; fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "${REPORT}" <<EOF
{
  "ts": "${ts}",
  "duration_s": ${elapsed},
  "mirror_exit_ok": ${mirror_ok},
  "primary": {
    "alias": "local",
    "endpoint": "http://minio:9000",
    "bucket": "${BUCKET}",
    "object_count": ${primary_count}
  },
  "shadow": {
    "alias": "seaweed",
    "endpoint": "http://seaweedfs:8333",
    "bucket": "${SHADOW_BUCKET}",
    "object_count": ${shadow_count}
  },
  "diff_line_count": ${diff_count},
  "level": "$( [ "${mirror_ok}" = "1" ] && [ "${diff_count}" = "0" ] && echo ok || echo warn )"
}
EOF

# Also keep the raw mc diff output for forensics — capped at the first 2 MiB.
head -c 2097152 /tmp/diff.out > /reports/initial-sync.diff.txt 2>/dev/null || true

echo "[initial-sync] wrote ${REPORT} (primary=${primary_count} shadow=${shadow_count} diff_lines=${diff_count})"

if [ "${mirror_ok}" = "0" ]; then
    exit 1
fi
exit 0
