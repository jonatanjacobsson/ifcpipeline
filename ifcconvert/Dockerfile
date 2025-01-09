FROM python:3.9

# Install required system packages
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download and set up IfcConvert
RUN wget https://github.com/IfcOpenShell/IfcOpenShell/releases/download/ifcconvert-0.8.0/ifcconvert-0.8.0-linux64.zip \
    && unzip ifcconvert-0.8.0-linux64.zip \
    && mv IfcConvert /usr/local/bin/ \
    && chmod +x /usr/local/bin/IfcConvert \
    && rm ifcconvert-0.8.0-linux64.zip

# Create necessary directories
RUN mkdir -p /app/models /app/output/converted

# Install Python dependencies
COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifcconvert/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcconvert/ifcconvert-service.py .

# Make sure the application has access to the directories
RUN chmod -R 755 /app/models /app/output

CMD ["uvicorn", "ifcconvert-service:app", "--host", "0.0.0.0", "--port", "80"]