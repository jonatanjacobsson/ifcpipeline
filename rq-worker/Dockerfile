FROM python:3.9

WORKDIR /app

# Copy shared libraries first
COPY shared /app/shared
RUN pip install -e /app/shared

# Copy requirements from all services
COPY ifctester-worker/requirements.txt /app/service_requirements/ifctester.txt
COPY ifcconvert/requirements.txt /app/service_requirements/ifcconvert.txt
COPY ifccsv/requirements.txt /app/service_requirements/ifccsv.txt
COPY ifcdiff/requirements.txt /app/service_requirements/ifcdiff.txt
COPY ifc5d/requirements.txt /app/service_requirements/ifc5d.txt

# Install all service requirements
RUN pip install --no-cache-dir -r /app/service_requirements/ifctester.txt \
                               -r /app/service_requirements/ifcconvert.txt \
                               -r /app/service_requirements/ifccsv.txt \
                               -r /app/service_requirements/ifcdiff.txt \
                               -r /app/service_requirements/ifc5d.txt \
                               rq

# Copy worker tasks module
COPY rq-worker/worker_tasks.py /app/worker_tasks.py
COPY rq-worker/__init__.py /app/

# Copy service task modules
COPY ifctester-worker /app/ifctester_worker

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Command to run the RQ worker for all queues
CMD ["rq", "worker", "default", "ifcconvert", "ifccsv", "ifctester", "ifcdiff", "ifc5d", "--url", "redis://redis:6379/0"] 