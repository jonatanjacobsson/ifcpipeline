FROM mcr.microsoft.com/dotnet/aspnet:6.0 AS runtime
WORKDIR /

# Install necessary dependencies
RUN apt-get update && apt-get install -y wget unzip python3 python3-pip

# Download and extract the pre-built binaries
RUN wget https://github.com/bimaps/ifc2json/archive/refs/heads/master.zip \
    && unzip master.zip \
    && mv ifc2json-master/linux/* / \
    && rm -rf master.zip ifc2json-master

# Copy the shared directory
COPY shared /shared

# Install FastAPI and uvicorn
RUN pip3 install fastapi uvicorn

# Install shared module
RUN pip3 install -e /shared

# Copy the FastAPI service file
COPY ifc2json/ifc2json-service.py .

# Make the ConvertIfc2Json executable
RUN chmod +x /ConvertIfc2Json

# Set the entry point to run the FastAPI service
CMD ["uvicorn", "ifc2json-service:app", "--host", "0.0.0.0", "--port", "80"]