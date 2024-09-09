FROM python:3.10-slim

WORKDIR /app

# Install system dependencies and IfcConvert
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && wget https://github.com/IfcOpenShell/IfcOpenShell/releases/download/v0.5.0-preview2/IfcConvert-master-9ad68db-linux64.zip \
    && unzip IfcConvert-master-9ad68db-linux64.zip \
    && mv IfcConvert /usr/local/bin/ \
    && rm IfcConvert-master-9ad68db-linux64.zip


# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY ifcpipeline.py .

# Create necessary directories
RUN mkdir -p /app/models /app/output/converted /app/ids

# Expose the port the app runs on
EXPOSE 8000

# Set the command to run the application
CMD ["uvicorn", "ifcpipeline:app", "--host", "0.0.0.0", "--port", "8000"]