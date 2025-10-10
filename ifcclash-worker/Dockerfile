FROM python:3.11-slim

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

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