FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

EXPOSE 3200

CMD ["python", "-m", "plughub_auth_api.main"]
