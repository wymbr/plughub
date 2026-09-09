FROM node:20-slim AS builder

WORKDIR /app

# Copy schemas package
COPY packages/schemas ./packages/schemas

# Copy mcp-server-plughub package
COPY packages/mcp-server-plughub ./packages/mcp-server-plughub

# Fix workspace:* reference to use file-based path
RUN sed -i 's|"@plughub/schemas": "workspace:\*"|"@plughub/schemas": "file:../schemas"|g' \
    packages/mcp-server-plughub/package.json

# Build schemas first
WORKDIR /app/packages/schemas
RUN npm install && npm run build

# Build mcp-server-plughub
# NODE_OPTIONS aumenta o heap do TypeScript — o grafo de dependências é grande
WORKDIR /app/packages/mcp-server-plughub
RUN npm install && NODE_OPTIONS="--max-old-space-size=4096" npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:20-slim AS runtime

# wget: required by Docker healthcheck (node:20-slim ships without it)
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/packages/schemas ./packages/schemas
COPY --from=builder /app/packages/mcp-server-plughub ./packages/mcp-server-plughub

# ⚠️ GAT-04 (2026-09-09) — as fixtures COMPARTILHADAS entram na imagem.
#
# `infra/test/fixtures/*.json` guarda vetores lidos por DUAS linguagens de
# proposito: o teste TS e o probe Python leem o MESMO arquivo, para que um caso
# novo obrigue as duas casas a concordarem (paridade PRESUMIDA entre linguagens e
# o defeito que esses vetores existem para pegar). Por isso a fixture nao migra
# para dentro do pacote: duplicar o arquivo recriaria a divergencia.
#
# Sem esta copia, o teste que a le estoura no import, executa ZERO testes e some
# de toda contagem — o `probe_ts_suites.sh` reportava "nenhum vermelho" com o
# arquivo inerte. E o mesmo "declara e nao instala" que o gemeo Python mediu nas
# 14 imagens, agora do lado do DADO em vez do runner.
COPY infra/test/fixtures ./infra/test/fixtures

WORKDIR /app/packages/mcp-server-plughub

EXPOSE 3100

CMD ["node", "dist/index.js"]
