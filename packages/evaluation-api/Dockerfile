# Dockerfile — evaluation-api
# Build context: monorepo root (context: .)
FROM python:3.11-slim

WORKDIR /app
COPY packages/evaluation-api ./packages/evaluation-api

WORKDIR /app/packages/evaluation-api
RUN pip install --no-cache-dir -e .

EXPOSE 3400

CMD ["uvicorn", "plughub_evaluation_api.main:app", "--host", "0.0.0.0", "--port", "3400"]
