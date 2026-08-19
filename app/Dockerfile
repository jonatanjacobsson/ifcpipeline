FROM python:3.12-slim

WORKDIR /pipeline

COPY dashboard/requirements.txt ./dashboard/requirements.txt
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r dashboard/requirements.txt -r app/requirements.txt

COPY dashboard/ ./dashboard/
COPY app/ ./app/

ENV PYTHONPATH=/pipeline/dashboard:/pipeline/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

WORKDIR /pipeline/app

CMD ["python", "main.py"]
