FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY packages/session-replayer/pyproject.toml ./
RUN pip install --no-cache-dir --break-system-packages \
    aiokafka>=0.10.0 \
    redis[asyncio]>=5.0.0 \
    asyncpg>=0.29.0 \
    pydantic>=2.5.0 \
    python-dotenv>=1.0.0

# Copy source
COPY packages/session-replayer/src ./src

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "session_replayer.main"]
