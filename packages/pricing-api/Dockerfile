# Dockerfile — pricing-api
# Build context: monorepo root (context: .)
FROM python:3.11-slim

WORKDIR /app
COPY packages/pricing-api ./packages/pricing-api

WORKDIR /app/packages/pricing-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_pricing_api.main:app", "--host", "0.0.0.0", "--port", "3900"]
