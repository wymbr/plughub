#!/usr/bin/env bash
# infra/scripts/rebuild-all.sh
#
# Rebuild completo da stack demo (docker-compose.demo.yml), incluindo as imagens
# de infra (postgres/pgvector, clickhouse, kafka, redis, minio, kafdrop,
# redis-commander) e TODAS as imagens de aplicação construídas do monorepo.
#
# Uso:
#   ./infra/scripts/rebuild-all.sh                # preserva volumes (dados)
#   ./infra/scripts/rebuild-all.sh --no-cache     # rebuild sem cache de layer
#   ./infra/scripts/rebuild-all.sh --wipe         # DESTRUTIVO: apaga volumes
#   ./infra/scripts/rebuild-all.sh --wipe --no-cache
#
# Notas:
#   • --no-cache NÃO é necessário para arquivo novo. Esta nota dizia que era;
#     medido em 2026-09-16 (docker build no WSL, docker.exe do Windows e
#     docker compose build; arquivo e diretório novos) a camada COPY é refeita
#     e o arquivo chega à imagem. Os dois incidentes de 2026-07-29 que deram
#     origem à nota são reais e de causa desconhecida (TODO.md). Regra: confira
#     o arquivo DENTRO do container; se faltar, use --no-cache e registre.
#   • --wipe apaga postgres-data / clickhouse-data / kafka-data / redis-data /
#     minio-data / attachment-data. No próximo boot: initdb do postgres roda de
#     novo, agent-registry re-semeia de infra/registry/*.yaml (seed-if-absent),
#     dialog-seed re-semeia os DialogForms de infra/dialog/*.json (idem), e
#     QUALQUER pool/skill/config/form criado pela UI se perde.
#   • e2e-runner não sobe (--scale e2e-runner=0), mas é construído junto.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE="docker compose -f $REPO_ROOT/docker-compose.demo.yml"

WIPE=false
BUILD_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --wipe)     WIPE=true ;;
    --no-cache) BUILD_ARGS+=(--no-cache) ;;
    *) echo "Argumento desconhecido: $arg"; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

if [ "$WIPE" = true ]; then
  echo "⚠️  --wipe: os volumes (postgres, clickhouse, kafka, redis, minio) serão APAGADOS."
  read -r -p "Digite 'yes' para continuar: " CONFIRM
  [ "$CONFIRM" = "yes" ] || { echo "Abortado."; exit 1; }
  $COMPOSE down -v --remove-orphans
else
  $COMPOSE down --remove-orphans
fi

echo "── 1/3 · pull das imagens de infra ────────────────────────────────"
$COMPOSE pull --ignore-buildable \
  || $COMPOSE pull redis postgres kafka kafka-init clickhouse minio minio-init \
                   kafdrop redis-commander pricing-seed auth-seed eval-seed dialog-seed

echo "── 2/3 · build de todas as imagens de aplicação ───────────────────"
$COMPOSE build --pull "${BUILD_ARGS[@]}"

echo "── 3/3 · subindo a stack (sem e2e-runner) ─────────────────────────"
$COMPOSE up -d --scale e2e-runner=0

echo
echo "✅ Stack no ar. Acompanhe a convergência:"
echo "   $COMPOSE ps"
echo "   $COMPOSE logs -f agent-registry orchestrator-bridge analytics-api"
echo "   UI: http://localhost:5174/login  (admin@plughub.local / changeme_admin)"
