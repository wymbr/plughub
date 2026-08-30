FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
COPY src/ ./src/
# ⚠️ `.[dev]` e NAO `.` — o extra traz o pytest, e sem ele a suite do servico
# so roda em container onde alguem instalou o pytest A MAO. Medido em 2026-08-30:
# NENHUMA das 14 imagens Python tinha pytest; quatro containers tinham, por estado
# herdado que um `up -d` apaga. Um ambiente que so verifica porque ja verificou
# antes nao esta sendo verificado — esta sendo lembrado.
RUN pip install --no-cache-dir ".[dev]" --break-system-packages 2>/dev/null || pip install --no-cache-dir ".[dev]"

EXPOSE 3800
CMD ["plughub-workflow-api"]
