FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY topologicpy-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Since topologicpy 0.9.62 Core.Backend() auto-prefers a bundled PythonOCC
# backend whenever `import OCC` succeeds. No OCC ships in this image today, but
# pin the kernel so a future transitive pythonocc-core can never swap it
# silently (the PythonOCC backend gives different cells/containment answers on
# our data and no-ops RemoveCoplanarFaces, which we reach via
# Topology.ConvexHull). Set before the smoke check so the build tests the
# backend the worker will actually run.
ENV TOPOLOGICPY_CORE_BACKEND=topologic_core

# Fail the build if no geometry kernel is usable. Without topologic_core the
# failure is otherwise silent (ByBREPString/Vertex.ByCoordinates return None ->
# 0 rooms / 0 cells reported as success). See kernel_smoke.py.
COPY topologicpy-worker/kernel_smoke.py /app/
RUN python /app/kernel_smoke.py

COPY topologicpy-worker/space_cache.py /app/
COPY topologicpy-worker/tasks.py /app/
COPY topologicpy-worker/ingest_scripts /app/ingest_scripts
COPY ifcpatch-worker/custom_recipes/RemoveElements.py /app/custom_recipes/RemoveElements.py

RUN mkdir -p /output/topology /uploads
RUN chmod -R 777 /output /uploads

RUN useradd -m -u 1000 worker
USER worker

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# SimpleWorker: topologic/OCCT is not fork-safe under the default RQ work-horse.
CMD ["sh", "-c", "rq worker --worker-class rq.worker.SimpleWorker topologicpy-worker --url \"${REDIS_URL:-redis://redis:6379/0}\" --path /app"]
