FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir . --break-system-packages 2>/dev/null || pip install --no-cache-dir .

EXPOSE 3800
CMD ["plughub-workflow-api"]
