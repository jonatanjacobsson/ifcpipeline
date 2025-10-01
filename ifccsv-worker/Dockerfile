# ifccsv-worker/Dockerfile
FROM python:3.10 AS base

WORKDIR /app

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifccsv-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifccsv-worker/tasks.py /app/

# Create necessary directories 
# Ensure consistent output directories are created and accessible
RUN mkdir -p /output/csv /output/xlsx /output/ods /output/ifc_updated /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifccsv", "--url", "redis://redis:6379/0"] 