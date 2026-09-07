#!/usr/bin/env bash
# probe_delegate_cycle_cap.sh — 2026-09-07  (RET-04 do ADR
# adr-tree-return-continuation.md)
#
# PERGUNTA: o ciclo `folha -> agente -> menu -> folha` tem FIM, e o mecanismo que
# o limita nao e anulado pelo mecanismo que o faz girar?
#
# AS DUAS METADES DESTA TAREFA QUASE SE ANULARAM
#   (1) Para o ciclo GIRAR, as sentinelas do `delegate` tem de ser limpas ao
#       reentrar no step. Sem isso a SEGUNDA volta devolve o resultado da
#       PRIMEIRA: o especialista nunca e chamado, o orquestrador segue como se
#       tivesse sido, e o step relata sucesso todas as vezes.
#       ⚠️ A ORQ-07 consertou so a familia do `invoke`, porque era o caso medido.
#       A razao nunca foi do `invoke` -- e de qualquer sentinela chaveada so pelo
#       `step.id`. `delegate`, `collect` e `suspend` ficaram de fora, e o defeito
#       reapareceu exatamente onde o ciclo novo o encontraria.
#   (2) Para o ciclo TERMINAR, ha um contador com teto.
#
#   Se o contador usasse o padrao `{id}:__x__`, a limpeza da metade (1) o zeraria
#   a cada volta e o teto NUNCA dispararia -- laco infinito com `max_iterations`
#   declarado ao lado, dando a impressao contraria. Por isso ele fica FORA do
#   padrao (`_delegate_iterations_{id}`, a mesma forma do `receive`): imunidade
#   por construcao, nao por alguem lembrar.
#
# TRES RAMOS
#   A  TETO — todo `delegate` que participa de um ciclo (ha caminho de volta ate
#      ele) declara `max_iterations`. Delegate FORA de ciclo nao precisa, e a
#      esmagadora maioria do parque nao cicla. Zero ciclicos e INCONCLUSIVO: o
#      teto nao seria exercido e o verde nao diria nada.
#   B  COLISAO — a chave do contador esta fora do padrao de sentinela. Reprova
#      tambem quem esteja NO padrao mas ainda nao listado: e um passo de virar
#      defeito, e o proximo a mexer na lista nao tem como saber.
#   C  SENTINELAS — a limpeza cobre as familias que SUSPENDEM (`delegate`,
#      `collect`), nao so a do `invoke`. Este ramo existe porque a lista JA
#      nasceu incompleta uma vez.
#
# ⚠️ Le o SNAPSHOT VIVO do slot para o ramo A, e o FONTE para B e C -- as duas
#    perguntas sao de naturezas diferentes: uma e sobre o que esta deployado, a
#    outra e sobre o mecanismo que o repositorio carrega.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_ret04_cap_probe.py"
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

echo "probe_delegate_cycle_cap — o ciclo tem fim, e o teto nao e anulado (RET-04)"

rodar "A — todo delegate em ciclo declara teto"  teto
rodar "B — o contador nao colide com a limpeza"  colisao
rodar "C — a limpeza cobre quem SUSPENDE"        sentinelas

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA ramo(s) reprovaram"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo "RESULTADO: INCONCLUSIVO — $INCONCL ramo(s) sem populacao"
  exit 3
fi
echo "RESULTADO: OK — o ciclo gira, tem fim, e as duas metades nao se anulam"
exit 0
