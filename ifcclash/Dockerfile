FROM python:3.9


COPY shared /shared
RUN pip install -e /shared

COPY ifcclash/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcclash/ifcclash-service.py .

CMD ["uvicorn", "ifcclash-service:app", "--host", "0.0.0.0", "--port", "80"]