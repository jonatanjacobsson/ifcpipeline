FROM python:3.11-slim

# Using Python 3.11 for better performance and compatibility with latest ifcopenshell wheels
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

COPY ./shared /app/shared
COPY ./api-gateway/requirements.txt .
COPY ./api-gateway/api-gateway.py .

# Upgrade pip to latest version for better dependency resolution
# Install pip requirements
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir rq redis

ENV PYTHONUNBUFFERED=1

# Command to run the API gateway
CMD ["uvicorn", "api-gateway:app", "--host", "0.0.0.0", "--port", "80"]