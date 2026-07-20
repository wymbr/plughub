# Dockerfile — scheduler-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
COPY packages/scheduler-api ./packages/scheduler-api

WORKDIR /app/packages/scheduler-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_scheduler_api.main:app", "--host", "0.0.0.0", "--port", "3650"]
