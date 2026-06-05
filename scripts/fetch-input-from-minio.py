#!/usr/bin/env python3
"""
Download the production reproducer input file from the local MinIO
(running in the ifcpipeline compose project on localhost:9000) into
/tmp/repro-ifcpatch/input.ifc, ready for repro-local-wrapper.py.

Idempotent: skips if the file is already present at the expected
size (~50 MiB).

Reads S3 creds from ifcpipeline/.env (S3_ACCESS_KEY / S3_SECRET_KEY).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

EXPECTED_SIZE = 52_397_322  # bytes, sha256 ed013a91...
DST = Path("/tmp/repro-ifcpatch/input.ifc")
KEY = "uploads/A--40_V00000.ifc"
BUCKET = "ifcpipeline"


def load_env(path: Path) -> dict:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    env = load_env(env_file)
    access = env.get("S3_ACCESS_KEY") or os.environ.get("S3_ACCESS_KEY")
    secret = env.get("S3_SECRET_KEY") or os.environ.get("S3_SECRET_KEY")
    if not access or not secret:
        print(
            "ERROR: S3 credentials missing (set S3_ACCESS_KEY/S3_SECRET_KEY "
            "or ensure ifcpipeline/.env has them).",
            file=sys.stderr,
        )
        return 2

    DST.parent.mkdir(parents=True, exist_ok=True)
    if DST.exists() and DST.stat().st_size == EXPECTED_SIZE:
        print(f"OK already present: {DST}  ({EXPECTED_SIZE} bytes)")
        return 0

    import boto3
    from botocore.client import Config

    s3 = boto3.client(
        "s3",
        endpoint_url="http://localhost:9000",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )
    print(f"downloading s3://{BUCKET}/{KEY} -> {DST} ...")
    s3.download_file(BUCKET, KEY, str(DST))
    size = DST.stat().st_size
    print(f"OK {size} bytes")
    if size != EXPECTED_SIZE:
        print(
            f"WARNING: size mismatch (got {size}, expected {EXPECTED_SIZE})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
