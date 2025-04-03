FROM python:3.9

WORKDIR /

COPY shared /shared
RUN pip install -e /shared

COPY api-gateway/requirements.txt .
RUN pip install -r requirements.txt

COPY api-gateway/api-gateway.py .

CMD ["uvicorn", "api-gateway:app", "--host", "0.0.0.0", "--port", "80"]