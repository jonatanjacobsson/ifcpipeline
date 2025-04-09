FROM python:3.10-slim

WORKDIR /
# Copy shared module first
COPY shared /shared

COPY ifc5d/requirements.txt .

# Install dependencies and clean up in one step to save space
RUN pip install --no-cache-dir -e /shared && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip

# Copy service file
COPY ifc5d/ifc5d-service.py .

CMD ["uvicorn", "ifc5d-service:app", "--host", "0.0.0.0", "--port", "80"]