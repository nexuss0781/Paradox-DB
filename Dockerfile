FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/app/ app/
COPY gateway/container_health.py container_health.py
COPY gateway/docker-entrypoint.sh docker-entrypoint.sh
COPY gateway/alembic/ alembic/
COPY gateway/alembic.ini .

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=5 \
  CMD ["python", "container_health.py"]

RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
