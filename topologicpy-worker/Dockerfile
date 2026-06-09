FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY topologicpy-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY topologicpy-worker/patch_vertex.py /tmp/patch_vertex.py
RUN python /tmp/patch_vertex.py

COPY topologicpy-worker/space_cache.py /app/
COPY topologicpy-worker/tasks.py /app/
COPY topologicpy-worker/ingest_scripts /app/ingest_scripts

RUN mkdir -p /output/topology /uploads
RUN chmod -R 777 /output /uploads

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["rq", "worker", "topologicpy-worker", "--url", "redis://redis:6379/0", "--path", "/app"]
