FROM python:3.9-slim

WORKDIR /app

# Install Docker CLI
RUN apt-get update && \
    apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the worker task definitions and shared module
COPY rq-worker/worker_tasks.py .
COPY shared /app/shared

# Install dependencies including pydantic and shared module
RUN pip install --no-cache-dir -e /app/shared && \
    pip install --no-cache-dir rq redis requests pydantic && \
    rm -rf /root/.cache/pip

# Define environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="${PYTHONPATH}:/app"

# Command to run (will be overridden by docker-compose)
CMD ["rq", "worker", "default", "--url", "redis://redis:6379/0"] 