FROM python:3.9

WORKDIR /app

COPY shared /app/shared
RUN pip install -e /app/shared

COPY api-gateway/requirements.txt .
RUN pip install -r requirements.txt

COPY api-gateway/api-gateway.py .
COPY api-gateway/config.json .

CMD ["uvicorn", "api-gateway:app", "--host", "0.0.0.0", "--port", "80"]