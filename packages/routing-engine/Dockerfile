FROM python:3.11-slim

WORKDIR /app

# Copy routing-engine package
COPY packages/routing-engine ./packages/routing-engine

WORKDIR /app/packages/routing-engine

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "plughub_routing.main"]
