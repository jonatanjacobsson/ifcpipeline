# ifcfast-worker/Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifcfast-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifcfast-worker/tasks.py /app/

RUN mkdir -p /output/csv /uploads /tmp/.cache/ifcfast
RUN chmod -R 777 /output /uploads /tmp/.cache

CMD ["rq", "worker", "ifcfast", "--url", "redis://redis:6379/0"]
