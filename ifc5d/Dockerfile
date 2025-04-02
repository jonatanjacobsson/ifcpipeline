FROM python:3.10

WORKDIR /app

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifc5d/requirements.txt .
RUN pip install -r requirements.txt

COPY ifc5d/ifc5d-service.py .

CMD ["uvicorn", "ifc5d-service:app", "--host", "0.0.0.0", "--port", "80"]