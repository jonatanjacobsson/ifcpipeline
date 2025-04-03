FROM python:3.9

# Install required system packages
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /

# Download and set up IfcConvert
RUN wget https://github.com/IfcOpenShell/IfcOpenShell/releases/download/ifcconvert-0.8.0/ifcconvert-0.8.0-linux64.zip \
    && unzip ifcconvert-0.8.0-linux64.zip \
    && mv IfcConvert /usr/local/bin/ \
    && chmod +x /usr/local/bin/IfcConvert \
    && rm ifcconvert-0.8.0-linux64.zip

# Create directories
RUN mkdir -p /models /output/converted

# Set up the application
COPY shared /shared
RUN pip install -e /shared

COPY ifcconvert/requirements.txt .
RUN pip install -r requirements.txt

COPY ifcconvert/ifcconvert-service.py .

# Make sure the application has access to the directories
RUN chmod -R 755 /models /output

CMD ["uvicorn", "ifcconvert-service:app", "--host", "0.0.0.0", "--port", "80"]