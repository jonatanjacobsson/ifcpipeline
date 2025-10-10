# ifcdiff-worker/Dockerfile
FROM python:3.11-slim as base

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcdiff-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifcdiff-worker/tasks.py /app/

# Create necessary directories
RUN mkdir -p /output/diff /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcdiff", "--url", "redis://redis:6379/0"] 