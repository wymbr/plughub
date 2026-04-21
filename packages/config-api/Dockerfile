# Dockerfile — config-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
COPY packages/config-api ./packages/config-api

WORKDIR /app/packages/config-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_config_api.main:app", "--host", "0.0.0.0", "--port", "3600"]
