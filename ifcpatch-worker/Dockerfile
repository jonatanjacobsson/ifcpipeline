# ifcpatch-worker/Dockerfile
FROM python:3.11-slim AS base

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Install system dependencies for IfcOpenShell
RUN apt-get update && apt-get install -y \
    build-essential \
    libfcl-dev \
    liboctomap-dev \
    libccd-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcpatch-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code
COPY ifcpatch-worker/tasks.py /app/
COPY ifcpatch-worker/recipe_loader.py /app/

# Copy custom recipes directory
COPY ifcpatch-worker/custom_recipes /app/custom_recipes/

# MEP coordination (`ifc_coord` / `ag_ifc`) lives in ifccoord-worker, which is
# Compose profile `ifccoord` and needs the private ifc-coord submodule.
# CoordinateClashesFromReport still exists here; it errors at import if the
# engine is not on PYTHONPATH (do not COPY ifc-coord — that breaks public clones).

# Create necessary directories (compose also runs this service as 1000:1000)
RUN mkdir -p /output/patch /uploads \
    && chown -R 1000:1000 /app /output /uploads
USER 1000:1000

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcpatch", "--url", "redis://redis:6379/0", "--path", "/app"]
