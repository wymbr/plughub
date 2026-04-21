FROM python:3.11-slim

WORKDIR /app

# Copy channel-gateway package
COPY packages/channel-gateway ./packages/channel-gateway

WORKDIR /app/packages/channel-gateway

RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_channel_gateway.main:app", "--host", "0.0.0.0", "--port", "8010"]
