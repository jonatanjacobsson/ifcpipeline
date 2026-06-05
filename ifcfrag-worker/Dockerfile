FROM python:3.11-slim

WORKDIR /app

RUN python -m pip install --upgrade pip

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifcfrag-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifcfrag-worker/tasks.py /app/

RUN mkdir -p /output/frag && chmod -R 777 /output

CMD ["rq", "worker", "ifcfrag", "--url", "redis://redis:6379/0"]
