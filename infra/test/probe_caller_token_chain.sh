#!/usr/bin/env bash
# probe_caller_token_chain.sh — 2026-09-07  (CTR-06 do ADR
# adr-tree-return-continuation.md § Riscos, onde ela era questao ABERTA)
#
# PERGUNTA: numa cadeia `A -> B -> C`, B ainda consegue devolver o controle a A?
#
# O DEFEITO, QUE E DE ESCOPO
#   `core.workflow.delegate_resume_token` e tag UNICA da sessao, e o token que ela
#   carrega e fato da ARESTA (chamador -> chamado). Guardar fato estreito em campo
#   largo e o invariante do CLAUDE.md, e aqui ele cobra:
#
#     A delega a B   -> tag = T_A
#     B delega a C   -> tag = T_B   (T_A foi SOBRESCRITO)
#     C devolve a B  -> B le a tag, acha T_B, e retoma A SI MESMO
#     A nunca volta  -> contato pendurado ate o `timeout_hours`
#
#   Modo de falha: o cliente ve o especialista atender, o especialista encerra o
#   proprio segmento, e NADA fica vermelho.
#
# O CONSERTO TEM DUAS METADES QUE SO VALEM JUNTAS
#   (1) CAPTURAR — ao nascer, o pipeline copia a tag para o proprio
#       `pipeline_state`, que e isolado por segmento (`{sid}--seg--{iso}`) e e a
#       casa mais estreita que conhece a aresta.
#   (2) RESTAURAR — ao retomar (desempilhar), a tag volta a valer o token do MEU
#       chamador.
#   Capturar sem restaurar e uma chave que ninguem le; restaurar sem capturar nao
#   tem o que escrever. E a metade (2) e o que dispensa migrar os 8 skills que
#   leem a tag: o nome volta a significar o que promete.
#
# TRES RAMOS
#   A  MECANISMO — as duas metades estao no engine, CHAMADAS nos dois ramos certos
#      do `_execute`, e a chave do capturado esta FORA do padrao `{id}:__x__`
#      (aquela familia e apagada ao entrar num step, e o `delegate` e o step em que
#      se reentra — seria a RET-04 outra vez, e muda).
#   B  CRITERIO — o `decideVerb` concorda com o mecanismo. Duas casas respondem
#      *"este destino pode receber delegate?"*, e quando discordam vence a mais
#      permissiva — que aqui pendura o contato.
#      ⚠️ Este ramo tambem cobra que `nao_retorna` afira o TOKEN, e nao a mera
#      presenca da tool: `agente_portabilidade_intake_v1` invoca `workflow_resume`
#      cinco vezes e nenhuma delas retoma o chamador (retoma um `suspend` PROPRIO).
#      Ele so nao era delegavel porque o outro criterio o barrava antes.
#   C  VIVO — o `dist` que esta de pe tem o reparo, e ha cadeia entre os destinos
#      do mapa para exerce-lo. Sem cadeia nenhuma o resultado e INCONCLUSIVO: o
#      verde nao seria evidencia de que o reparo funciona.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_ctr06_chain_probe.py"
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

echo "════════════════════════════════════════════════════════════════════"
echo " CTR-06 — a cadeia \`delegate -> delegate\` e o token do chamador"
echo "════════════════════════════════════════════════════════════════════"

rodar "A · MECANISMO — capturar no nascimento, restaurar na retomada" mecanismo
rodar "B · CRITERIO  — o \`decideVerb\` concorda com o mecanismo"      criterio
rodar "C · VIVO      — o reparo esta no ar, e ha cadeia a exercer"     vivo

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " RESULTADO: FALHA em $FALHA ramo(s)"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo " RESULTADO: $INCONCL ramo(s) INCONCLUSIVO(s), nenhum vermelho"
  exit 3
fi
echo " RESULTADO: OK"
exit 0
