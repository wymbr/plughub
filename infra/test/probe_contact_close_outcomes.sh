#!/usr/bin/env bash
# probe_contact_close_outcomes.sh — 2026-09-07
#
# PERGUNTA: quantas casas do bridge decidem *"o contato acabou?"*, e elas concordam?
#
# O DEFEITO, achado por CONTATO REAL
#   Eram DUAS, e divergiam em tres outcomes:
#
#     process_routed   ("escalated_human","escalated_ai","transferred","suspended")
#     session_resumed  != "suspended"                        <- um elemento so
#
#   O caminho do resume nasceu para o Arc 19, onde workflow retomado termina em
#   `complete`. Fluxo retomado que termina em `escalate` NAO EXISTIA -- passou a
#   existir quando a RET-02 deu `delegate` ao orquestrador. A partir dali:
#
#     escalate SEM delegate antes  ->  process_routed   ->  contato SEGUE   OK
#     escalate DEPOIS de delegar   ->  session_resumed  ->  contato FECHA   BUG
#
#   Medido no contato `13484fe6`: o contato entrou na fila humana e foi retirado
#   dela 30 ms depois (`outcome=abandoned wait_ms=21`), porque o fechamento
#   chegou antes da alocacao. Para quem olhava a tela, "encerrou". O comentario
#   do caminho certo JA dizia *"fechar aqui causaria race condition"* -- e a
#   outra casa nao o lia.
#
#   Exposicao x dano, medidos separados: 3 de 31 deploys sao retomaveis E
#   escalam (`demo_ia`, `demo_llm_ia`, `limite_ia`); dano confirmado em 2
#   contatos. `limite_ia` escalou 1 vez na historia e foi pelo caminho
#   nao-resume, entao la e exposicao, nao dano.
#
# TRES RAMOS
#   A  CASAS — censo AST: toda condicao cujo corpo fecha o contato decide pelo
#      predicado unico. Reprova a TERCEIRA casa no dia em que ela nascer.
#      ⚠️ AST e nao `grep`: o literal "suspended" aparece em comentario, em log e
#      numa comparacao legitima de outro assunto (o ramo que publica
#      `session_suspended`). Contar por texto acusaria inocentes.
#   B  PREDICADO — ele existe e cobre os QUATRO outcomes de continuidade. Aparar
#      a lista e como o defeito nasceu.
#   C  MUTACAO do ramo A — injeta uma terceira casa e exige que ele acuse.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_contact_close_census.py"
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
echo " bridge — UM juiz para \"o contato acabou?\""
echo "════════════════════════════════════════════════════════════════════"

rodar "A · CASAS     — toda decisao de fechamento usa o predicado" casas
rodar "B · PREDICADO — a lista nao foi aparada"                    predicado
rodar "C · MUTACAO   — o censo acusa uma terceira casa"            casas-mut

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
