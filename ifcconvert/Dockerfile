FROM python:3.9

WORKDIR /app

COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifcconvert/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcconvert/ifcconvert-service.py .

CMD ["uvicorn", "ifcconvert-service:app", "--host", "0.0.0.0", "--port", "80"]