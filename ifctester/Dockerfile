FROM python:3.9

WORKDIR /

COPY shared /shared
RUN pip install -e /shared

COPY ifctester/requirements.txt .
RUN pip install -r requirements.txt

COPY ifctester/ifctester-service.py .

CMD ["uvicorn", "ifctester-service:app", "--host", "0.0.0.0", "--port", "80"]