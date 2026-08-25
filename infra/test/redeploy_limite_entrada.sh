#!/usr/bin/env bash
# Publica skill_limite_entrada_v1 do arquivo e re-snapshota o slot de limite_ia.
# Cirúrgico de propósito: REGISTRY_SYNC_RECONCILE=true reaplicaria o YAML sobre
# skills, pools E slots do tenant inteiro, revertendo config DB-owned deliberada
# (ex.: retencao_humano.queue_config.pool_id). Ver o cabeçalho de _publish_skill.py.
# Uso: bash infra/test/redeploy_limite_entrada.sh
set -uo pipefail
COMPOSE="docker compose -f docker-compose.demo.yml"
$COMPOSE cp infra/test/_publish_skill.py orchestrator-bridge:/tmp/_publish_skill.py >/dev/null \
  || { echo "⚠️  docker compose cp falhou"; exit 2; }
$COMPOSE exec -T orchestrator-bridge python3 /tmp/_publish_skill.py \
  skill_limite_entrada_v1.yaml limite_ia "${TENANT:-tenant_demo}"
