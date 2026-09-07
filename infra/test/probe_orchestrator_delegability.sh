#!/usr/bin/env bash
# probe_orchestrator_delegability.sh — 2026-09-06  (CTR-03 / G3 do ADR
# adr-orchestrator-specialist-contract.md)
#
# PERGUNTA: um destino de navegacao pode receber `delegate` do orquestrador sem
# deixar o contato PENDURADO?
#
# POR QUE ESTE GATE EXISTE, e por que ele nasceu no lugar da tarefa
#   A CTR-03 pedia trocar o `escalate` terminal do orquestrador por `delegate`,
#   para que ele mantivesse a titularidade do contato. A premissa foi MEDIDA e
#   esta certa: `limite_ia` delega ao `dialog_runner` e a trilha mostra
#   `limite_ia primary seq=0` -> `dialog_runner specialist seq=0` ->
#   `limite_ia primary seq=1`. 24 segmentos de alvo, 100% specialist; 49 do
#   chamador, 100% primary. O chamador RETOMA.
#
#   Mas delegar e SUSPENDER o chamador, e ele so volta se o especialista chamar
#   `workflow_resume`. Medido nos 6 destinos distintos de navegacao do parque:
#   ZERO sao delegaveis hoje -- 4 nao chamam `workflow_resume`, 1 tem cadeia de
#   delegate (colide no token) e 1 nao tem deploy. Ligar o delegate assim
#   penduraria o contato ate o `timeout_hours`, sem erro em lugar nenhum: o
#   cliente ve o especialista atender, o especialista encerra o segmento dele, e
#   o orquestrador fica esperando um sinal que nunca vem.
#
#   Ou seja: a ordem do ADR (G3 depois G4) esta INVERTIDA na dependencia real.
#   Um especialista que nao devolve torna o `delegate` uma suspensao sem retorno.
#
# TRES DISQUALIFICADORES, cada um com nome (nunca "nao da")
#   sem_deploy       pool sem slot `current` com snapshot (os pools humanos)
#   nao_retorna      o snapshot nao invoca `workflow_resume` -> pendura
#   cadeia_delegate  o snapshot usa `delegate`, e `core.workflow.delegate_resume_token`
#                    e tag UNICA da sessao: a delegacao de dentro SOBRESCREVE o
#                    token do orquestrador, que nunca retoma (CTR-06)
#
# TRES RAMOS
#   A  CENSO da delegabilidade por destino. Informativo e NOMEADO -- "0 de 6"
#      nao e falha, e o estado medido; quem o transforma em trava e o ramo B.
#   B  A TRAVA: orquestrador que DELEGUE a alvo nao-delegavel REPROVA. Hoje passa
#      porque nenhum delega; no dia em que alguem ligar sem preparar o alvo, fica
#      vermelho -- que e exatamente o defeito que esta tarefa quase cometeu.
#      ⚠️ `pool` por REF ($.pipeline_state.rota.pool) faz a trava cobrar o MAPA
#      INTEIRO: o alvo e decidido em runtime, entao todo destino e alcancavel, e
#      recusar-se a julgar a ref deixaria passar justamente a forma que o
#      orquestrador usa.
#   C  MUTACAO da trava: finge que o orquestrador delega a todos os destinos e
#      exige que ela ACUSE. Sem isso, o verde do B poderia ser cegueira -- ele
#      passa hoje por AUSENCIA de delegate, que e o modo de falha do catalogo.
#   D  MUTACAO da GUARDA (RET-02): desarma o `choice` que decide o verbo e exige
#      que a trava volte a cobrar o mapa inteiro. Existe porque o ramo C NAO passa
#      pela funcao que decide o alcance -- ele finge a lista de alvos. Sem o D, um
#      defeito naquela funcao deixaria a trava verde COM a mutacao C acusando ao
#      lado, dando falsa tranquilidade: a proposicao adjacente outra vez.
#
# ⚠️ Le o SNAPSHOT VIVO do slot, nunca o YAML do disco: skill e seed-if-absent, e
#    editar YAML ja semeado e no-op. Medir o arquivo responderia sobre um
#    artefato que pode nao ser o que roda.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA (nunca verde por ausencia)

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_delegability_probe.py"
FALHA=0
INCONCL=0

rodar() {
  rotulo="$1"
  modo="$2"
  echo ""
  echo "── $rotulo ─────────────────────────────────────────────────────────"
  rc=0
  python3 "$HELPER" "$modo" || rc=$?
  case "$rc" in
    0) ;;
    3) INCONCL=$((INCONCL + 1)) ;;
    *) FALHA=$((FALHA + 1)) ;;
  esac
}

echo "probe_orchestrator_delegability — quem pode receber delegate (CTR-03/G3)"

rodar "A — censo da delegabilidade"        censo
rodar "B — a trava"                        trava
rodar "C — mutacao: a trava acusa?"        trava-mut
rodar "D — mutacao da GUARDA do verbo"     guarda-mut

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA ramo(s) reprovaram"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo "RESULTADO: SEM AMOSTRA — $INCONCL ramo(s) sem populacao"
  exit 3
fi
echo "RESULTADO: OK — ninguem delega a alvo que nao devolve o controle"
exit 0
