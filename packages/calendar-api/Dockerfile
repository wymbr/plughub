# Dockerfile — calendar-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
COPY packages/calendar-api ./packages/calendar-api

WORKDIR /app/packages/calendar-api
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_calendar_api.main:app", "--host", "0.0.0.0", "--port", "3700"]
