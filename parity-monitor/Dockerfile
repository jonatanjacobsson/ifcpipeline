FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime deps. psycopg[binary] avoids needing libpq-dev at build time.
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY parity_check.py /app/parity_check.py
COPY daily_smoke.py /app/daily_smoke.py

# Reports dir is bind-mounted in compose; create the in-image path so the
# container can run without the mount during local sanity checks.
RUN mkdir -p /reports

USER 1000:1000

CMD ["python", "/app/parity_check.py"]
