# Dockerfile — evaluation-api
# Build context: monorepo root (context: .)
FROM python:3.11-slim

WORKDIR /app

# Verificador canonico de JWT+ABAC (packages/py-authz). Instalado ANTES do servico
# para que o `pip install -e .` dele encontre a dependencia ja satisfeita em vez de
# procura-la no PyPI, onde ela nao existe.
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

COPY packages/evaluation-api ./packages/evaluation-api

WORKDIR /app/packages/evaluation-api
RUN pip install --no-cache-dir -e .

EXPOSE 3400

CMD ["uvicorn", "plughub_evaluation_api.main:app", "--host", "0.0.0.0", "--port", "3400"]
