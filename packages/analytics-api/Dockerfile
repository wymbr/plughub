# Dockerfile — analytics-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
COPY packages/analytics-api ./packages/analytics-api

WORKDIR /app/packages/analytics-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_analytics_api.main:app", "--host", "0.0.0.0", "--port", "3500"]
