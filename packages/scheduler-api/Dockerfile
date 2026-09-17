# Dockerfile — scheduler-api
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app

# Verificador canonico de JWT+ABAC (packages/py-authz). Instalado ANTES do servico para
# que o `pip install -e .` dele encontre a dependencia ja satisfeita em vez de procura-la
# no PyPI, onde ela nao existe. SCH-01: as rotas de Agenda passaram a exigir credencial.
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

COPY packages/scheduler-api ./packages/scheduler-api

WORKDIR /app/packages/scheduler-api
# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "plughub_scheduler_api.main:app", "--host", "0.0.0.0", "--port", "3650"]
