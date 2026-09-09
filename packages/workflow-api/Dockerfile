FROM python:3.11-slim

WORKDIR /app

# ⚠️ RET-17 (2026-09-09): o build context deste servico passou a ser a RAIZ do
# monorepo, como o dos outros 20. Ele era o unico Python sem nenhuma dependencia
# interna, e o context proprio (`packages/workflow-api`) deixava `packages/*` fora
# de alcance — o que travava qualquer lib compartilhada, nao so esta.
#
# A morte de uma task tem de aparecer (packages/py-tasks). Instalado ANTES do
# servico pelo mesmo motivo das outras libs internas: o `pip install -e .` dele
# precisa achar a dependencia satisfeita, senao a procura no PyPI.
COPY packages/py-tasks ./packages/py-tasks
RUN pip install --no-cache-dir -e ./packages/py-tasks

COPY packages/workflow-api ./packages/workflow-api

WORKDIR /app/packages/workflow-api

# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir ".[dev]" --break-system-packages 2>/dev/null || pip install --no-cache-dir ".[dev]"

EXPOSE 3800
CMD ["plughub-workflow-api"]
