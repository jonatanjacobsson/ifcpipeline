FROM python:3.9

WORKDIR /app

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcclash-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifcclash-worker/ /app/

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcclash", "--url", "redis://redis:6379/0"] 