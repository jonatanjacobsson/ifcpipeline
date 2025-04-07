FROM python:3.10

WORKDIR /app

# Copy shared first if needed, assuming it's relative to build context
COPY shared /shared
RUN pip install -e /shared

# Copy requirements first for better layer caching
COPY ifcdiff_worker/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Copy our worker files
COPY ifcdiff_worker/worker.py /app/worker.py
COPY ifcdiff_worker/task_runner.py /app/task_runner.py

# Make scripts executable
RUN chmod +x /app/task_runner.py

# No CMD or ENTRYPOINT needed here if specified in docker-compose