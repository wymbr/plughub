#!/usr/bin/env bash
# migrate_entry_pools.sh — Fase 3c: migra pools de entrada/jornada para deploy-driven.
#
# Para cada pool: configura o slot "next" (skill + max_concurrent_sessions) e promove
# (next → current). O promote publica registry.changed → orchestrator-bridge reconcilia
# automaticamente: cria instâncias {pool}-{n} e drena as legadas agente_*-{n}
# (precedência "deploy vence" remove o pool do agent_type no _build_desired_state).
#
# NÃO migra pools @mention/conferência (copilot_sac, nps_ia, wrapup_ia, etc.) — esses
# dependem do enriquecimento da síntese (mention_commands) ainda pendente.
#
# Uso:  bash scripts/migrate_entry_pools.sh [pool_id ...]
#   sem args  → migra todos os pools de entrada
#   com args  → migra apenas os pools nomeados (um por vez, para validar)
#
# Pré-req: agent-registry em localhost:3300; tenant_demo; skills já registradas (sync YAML).

set -euo pipefail

REGISTRY="${AGENT_REGISTRY_URL:-http://localhost:3300}"
TENANT="${TENANT_ID:-tenant_demo}"
USER_ID="${USER_ID:-deploy_migration}"

# pool_id -> "skill_id max_concurrent_sessions"
declare -A POOLS=(
  [sac_ia]="skill_atendimento_sac_v1 10"
  [portabilidade_ia]="skill_portabilidade_intake_v1 10"
  [reembolso_ia]="skill_reembolso_intake_v1 10"
  [auth_sac_ia]="skill_atendimento_auth_v1 10"
  [auth_ia]="skill_auth_ia_v1 20"
  [auth_form_ia]="skill_auth_form_v1 20"
  [contexto_ia]="skill_contexto_ia_v1 20"
)

# Ordem determinística de migração (segura → mais composta).
ORDER=(sac_ia portabilidade_ia reembolso_ia auth_sac_ia auth_ia auth_form_ia contexto_ia)

hdr=(-H "x-tenant-id: ${TENANT}" -H "x-user-id: ${USER_ID}" -H "content-type: application/json")

migrate_one() {
  local pool="$1"
  local spec="${POOLS[$pool]:-}"
  if [[ -z "$spec" ]]; then
    echo "‼  pool desconhecido: $pool" >&2
    return 1
  fi
  local skill="${spec%% *}"
  local n="${spec##* }"

  echo "── ${pool}  (skill=${skill}  N=${n}) ───────────────────────────"

  echo "  1) PUT /slots/next"
  curl -sf -X PUT "${REGISTRY}/v1/pools/${pool}/slots/next" "${hdr[@]}" \
    -d "{\"skill_id\":\"${skill}\",\"config_json\":{\"max_concurrent_sessions\":${n}}}" \
    | sed 's/^/     /'
  echo

  echo "  2) POST /promote"
  curl -sf -X POST "${REGISTRY}/v1/pools/${pool}/promote" "${hdr[@]}" \
    | sed 's/^/     /'
  echo

  echo "  3) verificar deployed_skill_id em GET /v1/pools"
  curl -sf "${REGISTRY}/v1/pools" "${hdr[@]}" \
    | python3 -c "import sys,json; p=[x for x in json.load(sys.stdin)['pools'] if x['pool_id']=='${pool}'][0]; print('     deployed_skill_id =', p.get('deployed_skill_id'), '| N =', p.get('deployed_max_concurrent_sessions'))"
  echo "  ✓ ${pool} migrado. Valide no webchat antes do próximo."
  echo
}

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=("${ORDER[@]}")
fi

for pool in "${targets[@]}"; do
  migrate_one "$pool"
done

echo "Concluído. reconcile do bridge dispara via registry.changed (sem restart)."
echo "Confira instâncias:  redis-cli KEYS '${TENANT}:instance:*' | grep -E '<pool>-'"
