# ifcpatch-worker/Dockerfile
FROM python:3.10-slim AS base

WORKDIR /app

# Install system dependencies for IfcOpenShell
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

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

# Create necessary directories
RUN mkdir -p /output/patch /uploads
RUN chmod -R 777 /output /uploads

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcpatch", "--url", "redis://redis:6379/0", "--path", "/app"]
