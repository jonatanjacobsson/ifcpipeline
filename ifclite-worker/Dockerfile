FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pin CLI for reproducible builds; bump intentionally when upgrading IFClite.
RUN npm install -g @ifc-lite/cli@0.6.0

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifclite-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifclite-worker/ /app/

# @ifc-lite/cli IDS path expects DOMParser + DOM query APIs; jsdom provides them in Node.
RUN npm install jsdom@24.1.0 --prefix /app

CMD ["rq", "worker", "ifclite", "--url", "redis://redis:6379/0"]
