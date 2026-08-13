FROM python:3.11-slim

# Self-contained worker: do not FROM ghcr.io/jonatanjacobsson/ifc-coord
# (that registry is private and blocks a fresh clone `docker compose up`).

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libfcl-dev \
    liboctomap-dev \
    libccd-dev \
    libgomp1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifccoord-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifccoord-worker/tasks.py /app/

# RemoveElements recipe for fixed-only IFC export (ifc_coord.export_subset)
COPY ifcpatch-worker/custom_recipes/RemoveElements.py /app/custom_recipes/RemoveElements.py

# In-tree coordination engine (ag_ifc / ifc_coord) + bundled policies
COPY ifc-coord/ag_ifc /app/ag_ifc
COPY ifc-coord/ifc_coord /app/ifc_coord
COPY ifc-coord/scenarios /app/scenarios
ENV PYTHONPATH=/app

RUN mkdir -p /output/coord /uploads /cache/bvh \
    && chown -R 1000:1000 /app /output /uploads /cache/bvh
USER 1000:1000

# SimpleWorker: no fork per job — IfcOpenShell/native geometry is not fork-safe
# on long MEP-vs-struct runs (intermittent SIGSEGV waitpid 139).
CMD ["sh", "-c", "rq worker --worker-class rq.worker.SimpleWorker ifccoord --url \"${REDIS_URL:-redis://redis:6379/0}\" --path /app"]
