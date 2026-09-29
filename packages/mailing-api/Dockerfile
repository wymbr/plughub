# Dockerfile — mailing-api
# Build context: monorepo root (context: .)
FROM python:3.11-slim

WORKDIR /app

# AUT-60 — verificador canônico de JWT+ABAC (packages/py-authz), instalado ANTES do
# serviço para que o `pip install -e .` o encontre satisfeito (ele não existe no PyPI).
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

COPY packages/mailing-api ./packages/mailing-api

WORKDIR /app/packages/mailing-api
# `.[dev]` e não `.`: o extra traz o pytest, e sem ele a suíte só roda onde alguém o
# instalou à mão (a imagem desta API era uma das que declaravam testes sem runner).
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "plughub_mailing_api.main:app", "--host", "0.0.0.0", "--port", "3660"]
