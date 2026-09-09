# Dockerfile — analytics-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app

# Verificador canonico de JWT+ABAC (packages/py-authz). Instalado ANTES do servico
# para que o `pip install -e .` dele encontre a dependencia ja satisfeita em vez de
# procura-la no PyPI, onde ela nao existe.
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

# RET-16/17: a morte de uma task tem de aparecer (packages/py-tasks). Instalado
# ANTES do servico pelo mesmo motivo das outras libs internas: o `pip install -e .`
# dele precisa achar a dependencia satisfeita, senao a procura no PyPI.
COPY packages/py-tasks ./packages/py-tasks
RUN pip install --no-cache-dir -e ./packages/py-tasks

COPY packages/analytics-api ./packages/analytics-api

WORKDIR /app/packages/analytics-api
# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "plughub_analytics_api.main:app", "--host", "0.0.0.0", "--port", "3500"]
