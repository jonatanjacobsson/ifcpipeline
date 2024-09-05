FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ifcopenshell \
    python3-ifcopenshell

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ifc_pipeline.py .

CMD ["uvicorn", "ifc_pipeline:app", "--host", "0.0.0.0", "--port", "8000"]