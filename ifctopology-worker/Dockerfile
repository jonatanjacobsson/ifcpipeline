FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifctopology-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifctopology-worker/tasks.py /app/

RUN mkdir -p /output/topology /uploads
RUN chmod -R 777 /output /uploads

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["rq", "worker", "ifctopology", "--url", "redis://redis:6379/0", "--path", "/app"]
