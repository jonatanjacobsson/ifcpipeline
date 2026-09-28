# ifc2json-worker/Dockerfile
# The .NET runtime ConvertIfc2Json needs, on a Debian that still has its package
# pool. The plain `:6.0` tag is Debian 11 (bullseye), whose LTS ended 2026-08-31:
# deb.debian.org still serves a bullseye-security index listing package versions
# whose .deb files have been pruned, so `apt-get install` dies on 404s (exit 100)
# and this image has failed to build on main since 2026-09-10. Same .NET 6.0.36
# runtime, Debian 12.
FROM mcr.microsoft.com/dotnet/aspnet:6.0-bookworm-slim AS base

WORKDIR /

# Install Python and essential tools (wget, unzip needed for setup)
# Add python3-pip explicitly
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip wget unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Download and extract the ConvertIfc2Json binaries (same as original service)
RUN wget https://github.com/bimaps/ifc2json/archive/refs/heads/master.zip \
    && unzip master.zip \
    && mv ifc2json-master/linux/* / \
    && chmod +x /ConvertIfc2Json \
    && rm -rf master.zip ifc2json-master

WORKDIR /app

# Copy shared library and install it using pip3
COPY shared /app/shared
RUN pip3 install --break-system-packages -e /app/shared

# Install worker-specific Python dependencies using pip3
COPY ifc2json-worker/requirements.txt /app/
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifc2json-worker/tasks.py /app/

# Create necessary directories
RUN mkdir -p /output/json /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue using the rq executable
CMD ["rq", "worker", "ifc2json", "--url", "redis://redis:6379/0"] 