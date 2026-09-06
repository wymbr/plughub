#!/usr/bin/env bash
# probe_skill_profile_steps.sh — 2026-09-06  (CTR-01 / G1 do ADR
# adr-orchestrator-specialist-contract.md)
#
# PERGUNTA: a regra "cada PERFIL admite certos tipos de step" tem MECANISMO, e a
# lista que ela impõe corresponde ao parque?
#
# POR QUE ELE EXISTE
#   O Arc 19 declarou a segregacao workflow x agente, o CLAUDE.md afirmava que ela
#   era "validada em parse do YAML + guard no engine", e o schema do step delegate
#   dizia "Agents must never use delegate". Medido em 2026-09-06: NAO HAVIA
#   allowlist em lugar nenhum — nem no validador do agent-registry, nem no
#   executor.ts, nem no engine.ts. Promessa sem mecanismo, a familia do DDL de
#   participation_intervals.
#
# O QUE A MEDICAO MUDOU NA PROPRIA REGRA
#   delegate em perfil de agente e caminho VIVO: limite_ia (186 segmentos, ultimo
#   em 2026-09-05) e portabilidade_ia delegam ao dialog_runner (24 segmentos).
#   Impor a regra ao pe da letra recusaria dois pools em producao. delegate saiu
#   da lista; suspend/collect ficaram. Com a lista corrigida: 0 violacoes em 31
#   deploys vivos, ou seja ela pode ser imposta hoje sem migracao nenhuma.
#
# CINCO RAMOS, e nenhum substitui outro
#   A  censo do parque VIVO (slot current) sob a lista canonica, com TESTEMUNHA
#      de presenca: tem de existir delegate em perfil agente. Sem ela o censo
#      ficaria verde sem lastro, e a decisao que removeu delegate perderia a base
#      — por isso a ausencia da testemunha e INCONCLUSIVO, nunca verde.
#   B  MUTACAO do censo: injeta delegate nos proibidos do agente e exige que o
#      censo ACUSE. Um censo que fica verde depois disso estava verde por
#      cegueira, nao por conformidade.
#   C  ao vivo — o portao RECUSA suspend em pool de perfil agente (422
#      step_fora_do_perfil).
#   D  ao vivo — o portao RECUSA menu em pool de perfil workflow.
#   E  ao vivo — o portao DEIXA PASSAR delegate em perfil agente. Este e o ramo
#      que impede o portao de recusar tudo e passar em C e D; e e o UNICO lugar
#      onde a decisao real do servico (TypeScript) e comparada com a lista do
#      helper (Python). Devolver delegate a lista dos proibidos deixa E vermelho.
#   F  ao vivo — o portao DEIXA PASSAR suspend em perfil workflow.
#
# FIXTURES: probe_profile_agent e probe_profile_workflow, criadas seed-if-absent
#   pelo proprio probe. Ficam INERTES: so recebem o slot next e NUNCA sao
#   promovidas, e o bridge executa exclusivamente o snapshot do slot current —
#   pool com next e sem current nao instancia agente, nao consome licenca e nao
#   recebe contato. Nao ha DELETE de pool no registry; por isso sao nomeadas.
#
# EXIT: 0 OK · 1 FALHA · 3 INCONCLUSIVO/SEM AMOSTRA (nunca verde por ausencia)

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_profile_steps_probe.py"
FALHA=0
INCONCL=0

rodar() {
  local rotulo="$1"; shift
  echo ""
  echo "── $rotulo ──────────────────────────────────────────────────────────"
  local rc=0
  python3 "$HELPER" "$@" || rc=$?
  case "$rc" in
    0) ;;
    3) INCONCL=$((INCONCL + 1)) ;;
    *) FALHA=$((FALHA + 1)) ;;
  esac
}

echo "probe_skill_profile_steps — perfil de execucao x tipos de step (CTR-01/G1)"

rodar "A/B — censo do parque vivo"          censo
rodar "B — mutacao: o censo enxerga?"       censo-mut
rodar "C..F — o portao de deploy, ao vivo"  vivo

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA modo(s) reprovaram"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo "RESULTADO: INCONCLUSIVO — $INCONCL modo(s) sem amostra ou sem lastro"
  exit 3
fi
echo "RESULTADO: OK — a regra de perfil tem mecanismo, e ele recusa e deixa passar"
exit 0
