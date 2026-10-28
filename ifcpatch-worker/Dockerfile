# ifcpatch-worker/Dockerfile
FROM python:3.11-slim AS base

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Install system dependencies for IfcOpenShell
RUN apt-get update && apt-get install -y \
    build-essential \
    libfcl-dev \
    liboctomap-dev \
    libccd-dev \
    && rm -rf /var/lib/apt/lists/*

# PipeBottomLinesDwg writes DWG with ACadSharp (MIT, https://github.com/DomCR/ACadSharp),
# a .NET 8 library, through pythonnet (requirements.txt): the .NET runtime goes
# to /opt/dotnet and the DLL from the ACadSharp NuGet package to /opt/acadsharp,
# checked against its sha256. PipeBottomLinesIfc does not need them.
ARG DOTNET_RUNTIME_VERSION=8.0.21
ARG ACADSHARP_VERSION=3.8.0
ARG ACADSHARP_DLL_SHA256=13544512673b3124a8d6452ab02a3a8e11d40da05a1c6a8b586c14da3b322d22
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --runtime dotnet --version "${DOTNET_RUNTIME_VERSION}" --install-dir /opt/dotnet \
    && curl -fsSL "https://www.nuget.org/api/v2/package/ACadSharp/${ACADSHARP_VERSION}" -o /tmp/acadsharp.nupkg \
    && mkdir -p /opt/acadsharp \
    && unzip -j /tmp/acadsharp.nupkg lib/net8.0/ACadSharp.dll -d /opt/acadsharp \
    && echo "${ACADSHARP_DLL_SHA256}  /opt/acadsharp/ACadSharp.dll" | sha256sum -c - \
    && rm -f /tmp/dotnet-install.sh /tmp/acadsharp.nupkg \
    && rm -rf /var/lib/apt/lists/*
ENV DOTNET_ROOT=/opt/dotnet \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    PYTHONNET_RUNTIME=coreclr \
    ACADSHARP_DLL=/opt/acadsharp/ACadSharp.dll

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

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

# MEP coordination (`ifc_coord` / `ag_ifc`) lives in ifccoord-worker, which is
# Compose profile `ifccoord` and needs the private ifc-coord submodule.
# CoordinateClashesFromReport still exists here; it errors at import if the
# engine is not on PYTHONPATH (do not COPY ifc-coord — that breaks public clones).

# Create necessary directories (compose also runs this service as 1000:1000)
RUN mkdir -p /output/patch /uploads \
    && chown -R 1000:1000 /app /output /uploads
USER 1000:1000

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcpatch", "--url", "redis://redis:6379/0", "--path", "/app"]
