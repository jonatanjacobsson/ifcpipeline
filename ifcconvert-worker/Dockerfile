# ifcconvert-worker/Dockerfile
FROM python:3.11-slim

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

# Install system dependencies and IfcConvert (similar to original service)
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /

# Download and set up IfcConvert
RUN wget https://github.com/IfcOpenShell/IfcOpenShell/releases/download/ifcconvert-0.8.0/ifcconvert-0.8.0-linux64.zip \
    && unzip ifcconvert-0.8.0-linux64.zip \
    && mv IfcConvert /usr/local/bin/ \
    && chmod +x /usr/local/bin/IfcConvert \
    && rm ifcconvert-0.8.0-linux64.zip

WORKDIR /app

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcconvert-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifcconvert-worker/tasks.py /app/

# Create necessary directories (matching original service)
RUN mkdir -p /output/converted /models
RUN chmod -R 777 /output /models # Ensure worker has write permissions

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcconvert", "--url", "redis://redis:6379/0"] 