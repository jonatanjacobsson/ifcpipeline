FROM python:3.9

WORKDIR /app

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifcdiff/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcdiff/ifcdiff-service.py .

CMD ["uvicorn", "ifcdiff-service:app", "--host", "0.0.0.0", "--port", "80"]