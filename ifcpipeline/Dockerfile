FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY ifcpipeline.py .

# Copy the config
COPY config.json .

# Create necessary directories
RUN mkdir -p /app/models /app/output /app/ids

# Expose the port the app runs on
EXPOSE 8000

# Set the command to run the application
CMD ["uvicorn", "ifcpipeline:app", "--host", "ifcpipeline", "--port", "80"]