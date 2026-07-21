# Dockerfile — mailing-api
# Build context: monorepo root (context: .)
FROM python:3.11-slim

WORKDIR /app
COPY packages/mailing-api ./packages/mailing-api

WORKDIR /app/packages/mailing-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_mailing_api.main:app", "--host", "0.0.0.0", "--port", "3660"]
