FROM python:3.10

WORKDIR /

COPY shared /shared
RUN pip install -e /shared

COPY ifccsv/requirements.txt .
RUN pip install -r requirements.txt

COPY ifccsv/ifccsv-service.py .

CMD ["uvicorn", "ifccsv-service:app", "--host", "0.0.0.0", "--port", "80"]