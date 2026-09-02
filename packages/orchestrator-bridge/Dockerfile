# Dockerfile — orchestrator-bridge
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
# Gemeo Python das funcoes puras do ContextStore (packages/py-contextstore) — o funil
# de escrita que CARIMBA o `atributo` (ALW-02/D9.6). Instalado ANTES do servico pelo
# mesmo motivo do py-authz: o `pip install -e .` dele precisa achar a dependencia ja
# satisfeita, senao a procura no PyPI, onde ela nao existe.
COPY packages/py-contextstore ./packages/py-contextstore
RUN pip install --no-cache-dir -e ./packages/py-contextstore

COPY packages/orchestrator-bridge  ./packages/orchestrator-bridge
# Skills YAML fallback (used when Agent Registry has no skills registered)
COPY packages/skill-flow-engine/skills ./packages/skill-flow-engine/skills

WORKDIR /app/packages/orchestrator-bridge
# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["plughub-bridge"]
