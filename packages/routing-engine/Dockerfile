FROM python:3.11-slim

WORKDIR /app

# Copy routing-engine package
# Gemeo Python das funcoes puras do ContextStore (packages/py-contextstore) — o funil
# de escrita que CARIMBA o `atributo` (ALW-02/D9.6). Instalado ANTES do servico pelo
# mesmo motivo do py-authz: o `pip install -e .` dele precisa achar a dependencia ja
# satisfeita, senao a procura no PyPI, onde ela nao existe.
COPY packages/py-contextstore ./packages/py-contextstore
RUN pip install --no-cache-dir -e ./packages/py-contextstore

COPY packages/routing-engine ./packages/routing-engine

WORKDIR /app/packages/routing-engine

# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["python", "-m", "plughub_routing.main"]
