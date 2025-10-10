FROM python:3.11-slim

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifctester-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the *contents* of ifctester-worker directly into /app
COPY ifctester-worker/ /app/

# WORKDIR /app is usually automatically in path, so explicit PYTHONPATH might not be needed
# ENV PYTHONPATH=/app 

# Command to run the RQ worker for the 'ifctester' queue
CMD ["rq", "worker", "ifctester", "--url", "redis://redis:6379/0"]