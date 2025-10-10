# ifc5d-worker/Dockerfile
FROM python:3.11-slim AS base

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifc5d-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifc5d-worker/tasks.py /app/

# Create necessary directories
RUN mkdir -p /output/qto /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifc5d", "--url", "redis://redis:6379/0"] 