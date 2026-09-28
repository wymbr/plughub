#!/usr/bin/env bash
# test-with-redis.sh — roda o vitest do engine com um Redis REAL e descartável.
#
# Por quê: `menu-park.test.ts` (DUR-01) testa um Lua atômico, e mock de Redis sem Lua
# não testa atomicidade nenhuma. Sem `REDIS_URL` aquela suíte aparece PULADA.
#
# Roda de DENTRO do WSL (não há Node no WSL; o teste roda em `node:20`). O código vai
# por `tar` e não por bind mount, que no Docker Desktop não enxerga a árvore do WSL.
# O Redis usa a mesma imagem do demo (sem pull) e some ao fim.
#
# Uso: bash packages/skill-flow-engine/scripts/test-with-redis.sh [filtro do vitest]
#      (sem filtro: a suíte inteira)
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=$(docker inspect plughub-demo-redis-1 --format '{{.Config.Image}}' 2>/dev/null || echo redis:7-alpine)
NET=sfe-test-$$
docker network create "$NET" >/dev/null
docker run -d --rm --name "$NET-redis" --network "$NET" "$IMG" >/dev/null
trap 'docker rm -f "$NET-redis" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1' EXIT
cd "$ROOT"
tar c --exclude=.git packages/skill-flow-engine packages/schemas 2>/dev/null | \
  docker run --rm -i --network "$NET" -e REDIS_URL="redis://$NET-redis:6379" -w /w node:20 \
  sh -c "tar x && cd packages/skill-flow-engine && ./node_modules/.bin/tsc --noEmit -p . && ./node_modules/.bin/vitest run ${1:-}"
