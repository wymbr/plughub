FROM python:3.11-slim

WORKDIR /app

# Copy ai-gateway package
COPY packages/ai-gateway ./packages/ai-gateway

WORKDIR /app/packages/ai-gateway

RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_ai_gateway.main:app", "--host", "0.0.0.0", "--port", "3200"]
