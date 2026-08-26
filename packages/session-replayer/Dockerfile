FROM python:3.11-slim

WORKDIR /app

# Install dependencies
#
# ⚠️ **As aspas são obrigatórias, e a falta delas foi um defeito real** (achado
# 2026-08-26). Sem elas o shell lê `aiokafka>=0.10.0` como REDIRECIONAMENTO: o pip
# recebe `aiokafka` e o `>` cria um arquivo chamado `=0.10.0` no WORKDIR. Efeito: as
# cinco restrições de versão eram **inertes** — tudo instalava sem pin, e um major
# novo entraria em silêncio no próximo build. A evidência estava visível havia meses
# (`ls /app` mostrava `=0.10.0`, `=0.29.0`, `=1.0.0`, `=2.5.0`, `=5.0.0`) e ninguém
# olhou, porque o build ficava VERDE — a restrição que não restringe não reprova.
RUN pip install --no-cache-dir --break-system-packages \
    "aiokafka>=0.10.0" \
    "redis[asyncio]>=5.0.0" \
    "asyncpg>=0.29.0" \
    "pydantic>=2.5.0" \
    "python-dotenv>=1.0.0"

# Copy source
COPY packages/session-replayer/src ./src

# Testes JUNTO do código. Sem isto a suíte deste pacote é **inalcançável no
# container** — e um pacote cuja suíte ninguém consegue rodar tem cobertura que
# nunca é exercida. Era por isso que o `session-replayer` não aparecia em baseline
# nenhuma das passagens, ao contrário de routing-engine/analytics-api/bridge.
COPY packages/session-replayer/tests ./tests

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "session_replayer.main"]
