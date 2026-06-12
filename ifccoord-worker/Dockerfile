ARG IFCCOORD_TAG=latest
FROM ghcr.io/jonatanjacobsson/ifc-coord:${IFCCOORD_TAG}

WORKDIR /app

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Copy worker requirements and install
COPY ifccoord-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy tasks file
COPY ifccoord-worker/tasks.py /app/

# RemoveElements recipe for fixed-only IFC export (ifc_coord.export_subset)
COPY ifcpatch-worker/custom_recipes/RemoveElements.py /app/custom_recipes/RemoveElements.py

# Local ifc-coord engine overrides (ag_ifc / ifc_coord) — wins via PYTHONPATH
COPY ifc-coord/ag_ifc /app/ag_ifc
COPY ifc-coord/ifc_coord /app/ifc_coord
ENV PYTHONPATH=/app

# Create output and upload folders
RUN mkdir -p /output/coord /uploads
RUN chmod -R 777 /output /uploads

# SimpleWorker: no fork per job — IfcOpenShell/native geometry is not fork-safe
# on long MEP-vs-struct runs (intermittent SIGSEGV waitpid 139).
CMD ["sh", "-c", "rq worker --worker-class rq.worker.SimpleWorker ifccoord --url \"${REDIS_URL:-redis://redis:6379/0}\" --path /app"]
