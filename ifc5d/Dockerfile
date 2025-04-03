FROM python:3.10

WORKDIR /

COPY shared /shared
RUN pip install -e /shared

COPY ifc5d/requirements.txt .
RUN pip install -r requirements.txt

COPY ifc5d/ifc5d-service.py .

CMD ["uvicorn", "ifc5d-service:app", "--host", "0.0.0.0", "--port", "80"]