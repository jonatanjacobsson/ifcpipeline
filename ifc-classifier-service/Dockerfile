FROM python:3.11-slim

# Using Python 3.11 for better performance and compatibility
# Slim variant reduces image size while maintaining necessary dependencies

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to latest version for better dependency resolution
RUN python -m pip install --upgrade pip

# Copy requirements first to leverage Docker cache
COPY ifc-classifier-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ifc-classifier-service/ .
COPY shared/ /app/shared/

# Create models directory
RUN mkdir -p /app/models

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"] 