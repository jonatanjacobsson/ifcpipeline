FROM python:3.9

WORKDIR /app

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifccsv/requirements.txt .
RUN pip install -r requirements.txt

COPY ifccsv/ifccsv-service.py .

CMD ["uvicorn", "ifccsv-service:app", "--host", "0.0.0.0", "--port", "80"]