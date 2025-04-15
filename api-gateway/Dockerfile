FROM python:3.9-slim

WORKDIR /app

COPY ./shared /app/shared
COPY ./api-gateway/requirements.txt .
COPY ./api-gateway/api-gateway.py .

# Install pip requirements
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir rq redis

ENV PYTHONUNBUFFERED=1

# Command to run the API gateway
CMD ["uvicorn", "api-gateway:app", "--host", "0.0.0.0", "--port", "80"]