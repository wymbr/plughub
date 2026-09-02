#!/usr/bin/env bash
# migrate_conference_pools.sh — Fase 3c: migra pools de @mention/conferência/hook
# para deploy-driven (slot+promote). Mesma mecânica do migrate_entry_pools.sh.
#
# PRÉ-REQUISITO (Track B): rebuild do orchestrator-bridge para que o RegistrySyncer
# re-sincronize as skills com mention_commands embutido no flow:
#     docker compose -f docker-compose.demo.yml up -d --build orchestrator-bridge
# O preflight abaixo verifica se skill_copilot_sac_v1.flow.mention_commands existe;
# se imprimir None, o rebuild não pegou (imagem velha ou SKILLS_DIR não montado).
#
# Uso:  bash scripts/migrate_conference_pools.sh [pool_id ...]
#   sem args  → migra todos (após preflight OK)
#   com args  → migra apenas os pools nomeados (recomendado: um por vez, validando)

set -euo pipefail

REGISTRY="${AGENT_REGISTRY_URL:-http://localhost:3300}"
TENANT="${TENANT_ID:-tenant_demo}"
USER_ID="${USER_ID:-deploy_migration}"

# pool_id -> "skill_id max_concurrent_sessions"
declare -A POOLS=(
  [copilot_sac]="skill_copilot_sac_v1 10"
  [nps_ia]="skill_nps_v1 20"
  # ⚠️ MORTA desde a Camada E2 Fase 3 (wrap-up unificado): o pool `wrapup_ia` foi
  # removido (ver `infra/registry/tenant_demo.yaml`) e o `skill_wrapup_v1.yaml`
  # apagado em 2026-09-01 (CNS-15). A linha FICA porque este script e um registro
  # do que a Fase 3c migrou — remove-la faria o script mentir sobre o que ele fez.
  # Quem o re-executar hoje ja falharia neste pool, com ou sem o arquivo.
  [wrapup_ia]="skill_wrapup_v1 20"
  [portabilidade_processo_ia]="skill_portabilidade_demo_v1 20"
  [portabilidade_confirmacao]="skill_agente_confirmacao_portabilidade_v1 10"
  [evaluador_echo_ia]="skill_evaluador_echo_v1 20"
  # avaliacao_ia: skill_avaliacao_v1 NÃO tem step complete/escalate → 422 no agent-registry
  # (known issue em docs/arcos/instance-bootstrap.md). O PUT /slots/next falhará com
  # 404 "Skill não encontrada" enquanto a skill não registrar. Migrar só após corrigir.
  [avaliacao_ia]="skill_avaliacao_v1 20"
)

ORDER=(copilot_sac nps_ia wrapup_ia portabilidade_processo_ia portabilidade_confirmacao evaluador_echo_ia avaliacao_ia)

hdr=(-H "x-tenant-id: ${TENANT}" -H "x-user-id: ${USER_ID}" -H "content-type: application/json")

preflight() {
  echo "── preflight: mention_commands round-trip (Track B rebuild) ──"
  local mc
  mc=$(curl -sf "${REGISTRY}/v1/skills/skill_copilot_sac_v1" -H "x-tenant-id: ${TENANT}" \
        | python3 -c "import sys,json; f=json.load(sys.stdin).get('flow') or {}; print(f.get('mention_commands'))" 2>/dev/null || echo "ERR")
  if [[ "$mc" == "None" || "$mc" == "ERR" || -z "$mc" ]]; then
    echo "‼  skill_copilot_sac_v1.flow.mention_commands = ${mc}" >&2
    echo "   O rebuild do orchestrator-bridge (Track B) ainda não re-sincronizou as skills." >&2
    echo "   Rode:  docker compose -f docker-compose.demo.yml up -d --build orchestrator-bridge" >&2
    return 1
  fi
  echo "   ✓ mention_commands presente no flow — embed OK."
  echo
}

migrate_one() {
  local pool="$1"
  local spec="${POOLS[$pool]:-}"
  if [[ -z "$spec" ]]; then echo "‼  pool desconhecido: $pool" >&2; return 1; fi
  local skill="${spec%% *}"
  local n="${spec##* }"

  echo "── ${pool}  (skill=${skill}  N=${n}) ───────────────────────────"
  echo "  1) PUT /slots/next"
  curl -sf -X PUT "${REGISTRY}/v1/pools/${pool}/slots/next" "${hdr[@]}" \
    -d "{\"skill_id\":\"${skill}\",\"config_json\":{\"max_concurrent_sessions\":${n}}}" \
    | sed 's/^/     /' || { echo "     ‼ PUT falhou (skill registrada? veja caveat avaliacao_ia)"; return 1; }
  echo
  echo "  2) POST /promote"
  curl -sf -X POST "${REGISTRY}/v1/pools/${pool}/promote" "${hdr[@]}" | sed 's/^/     /'
  echo
  echo "  3) verificar deployed_skill_id"
  curl -sf "${REGISTRY}/v1/pools" "${hdr[@]}" \
    | python3 -c "import sys,json; p=[x for x in json.load(sys.stdin)['pools'] if x['pool_id']=='${pool}'][0]; print('     deployed_skill_id =', p.get('deployed_skill_id'), '| N =', p.get('deployed_max_concurrent_sessions'))"
  echo "  ✓ ${pool} migrado. Valide (copilot: @mention; nps/wrapup: hook on_human_end; echo: on_human_start)."
  echo
}

preflight

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then targets=("${ORDER[@]}"); fi
for pool in "${targets[@]}"; do migrate_one "$pool"; done

echo "Concluído. reconcile dispara via registry.changed (sem restart)."
