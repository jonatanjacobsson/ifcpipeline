FROM python:3.11-slim AS base

WORKDIR /app

RUN python -m pip install --upgrade pip

# Shared library (audit_db, object_storage, guid_extract)
COPY shared /app/shared
RUN pip install -e /app/shared

COPY guid-index-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY guid-index-worker/tasks.py /app/

# Run the RQ worker pointing to the guid_index queue.
CMD ["rq", "worker", "guid_index", "--url", "redis://redis:6379/0"]
