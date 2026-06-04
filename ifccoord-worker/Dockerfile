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

# Create output and upload folders
RUN mkdir -p /output/coord /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker
CMD ["rq", "worker", "ifccoord", "--url", "redis://redis:6379/0"]
