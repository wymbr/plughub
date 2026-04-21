FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" --break-system-packages 2>/dev/null || \
    pip install --no-cache-dir -e ".[dev]"

COPY src/ src/

CMD ["python", "-m", "usage_aggregator.main"]
