FROM python:3.10

WORKDIR /

COPY shared /shared
RUN pip install -e /shared

COPY ifcdiff/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcdiff/ifcdiff-service.py .

CMD ["uvicorn", "ifcdiff-service:app", "--host", "0.0.0.0", "--port", "80"]