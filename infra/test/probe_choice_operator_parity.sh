#!/usr/bin/env bash
# probe_choice_operator_parity.sh — 2026-09-07
#
# PERGUNTA: toda condicao de `choice` declarada num deploy vivo PODE casar?
#
# O DEFEITO QUE O ORIGINOU, e ele foi encontrado por CONTATO REAL
#   O `choice` tem DOIS avaliadores — `evaluateCondition` para `$.` e
#   `evaluateCtxCondition` para `@ctx.`/`@segment.` — e um `switch` cada. O
#   operador `exists` existia so no segundo; no primeiro caia no `default` do
#   switch e devolvia `false`.
#
#   O modo de falha e o pior do catalogo, porque nao ha erro em lugar nenhum:
#   condicao sempre falsa nao explode, ela vira o `default` do STEP -- e o
#   `default` quase sempre e um caminho legitimo. Medido no contato
#   `c97ed196` (2026-09-07): o `pipeline_state` do orquestrador mostrava
#
#       "nivel": { "on_return": "pos_atendimento" }        <- o dado ESTAVA la
#       continuar  ->  finalizar   default                 <- e a condicao nao casou
#
#   O cliente viu "Atendimento encerrado" e isso parecia um fim normal. Cinco
#   gates verdes ao redor, e nenhum perguntava se a condicao podia disparar --
#   todos mediam a proposicao vizinha (o ponteiro existe? a rota existe?).
#
# TRES RAMOS
#   A  CODIGO — todo operador do enum do `ConditionSchema` tem `case` no avaliador
#      do seu ramo. A excecao (`confidence_gte`, so `@ctx.`) e DECLARADA com
#      motivo no helper: lista de excecao sem motivo envelhece como permissao.
#   B  VIVO — nenhum snapshot PROMOVIDO usa operador SO-CTX sobre campo `$.`, nem
#      operador fora do enum. Le o deploy, nao o YAML.
#   C  MUTACAO do ramo B — injeta a combinacao invalida e exige que ele acuse.
#      Sem ela, um ramo B que nao olhasse nada ficaria verde para sempre.
#
# ⚠️ O ramo A e de FONTE e o B e de DEPLOY, e um nao substitui o outro: o A pega
#    o operador novo no dia em que alguem o acrescenta a um ramo so; o B pega o
#    YAML que usa uma combinacao que o codigo nunca suportou.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_choice_operator_probe.py"
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
echo " choice — toda condicao declarada PODE casar?"
echo "════════════════════════════════════════════════════════════════════"

rodar "A · CODIGO   — o operador tem avaliador no ramo do seu campo" codigo
rodar "B · VIVO     — nenhum deploy usa combinacao sem suporte"      vivo
rodar "C · MUTACAO  — o ramo B acusa quando existe o que pegar"      vivo-mut

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
