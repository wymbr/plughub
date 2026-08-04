#!/usr/bin/env bash
# PROBE MANUAL (precisa do Console) — a Fase B trocou mesmo o caminho de devolução?
#
# Os testes do bridge são de MOCK: provam a decisão (`_routing_holds_item` /
# `_release_work_item`), não a fiação dentro do handler de `agent_disconnect`.
# Só um F5 real mostra qual caminho rodou. Este probe faz o antes/depois.
#
# O DISCRIMINADOR NÃO DEPENDE DE LOG. Os dois caminhos deixam marcas diferentes no
# pacote do item, e é isso que se mede:
#
#   caminho ANTIGO (re-publish de 6 campos em conversations.inbound)
#     · conference_id      → null        (era preenchido)
#     · work_item_deadline → ""          (era ISO)
#     · claim_record       → apagado pelo re-parque
#     · item de volta no ZSET **com a vaga ainda ocupada** = duplicação
#
#   caminho NOVO (work_task_release)
#     · conference_id      → PRESERVADO
#     · work_item_deadline → PRESERVADO
#     · vaga devolvida (release_instance) antes do re-enfileiramento
#
# O log é conferido também, como segunda testemunha — mas o veredicto sai do estado.
#
# NÃO cobre (Fase E, D8): o `agent_done` falso e o segmento sem `close_reason`
# continuam sendo publicados no drop. Ver um deles aqui é ESPERADO, não regressão.
#
# Uso:  bash infra/test/probe_fase_b_release_on_reload.sh <session_id> [pool_id]
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
SID="${1:-}"
POOL="${2:-formfill_demo}"

if [ -z "$SID" ]; then
  echo "uso: $0 <session_id> [pool_id]"
  echo "     (o session_id do item na fila — o smoke_formfill_renderer.sh imprime um)"
  exit 64
fi

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

# O parser roda no ROUTING-ENGINE, não no redis: a imagem do redis NÃO tem python3.
#
# ⚠️ ESTE HELPER JÁ PRODUZIU UM FALSO VERDE (2026-08-04). A versão anterior rodava
# `python3` no container do redis, o exec falhava, e o `|| echo ABSENT` deixava a
# MENSAGEM DE ERRO sair como se fosse valor. O veredicto comparou "antes" com
# "depois", achou as duas mensagens de erro idênticas, e imprimiu ✅ FASE B ATIVA.
# Duas falhas iguais viraram prova de preservação.
#
# Duas defesas, porque uma só já se mostrou insuficiente:
#   1. `field` devolve string vazia e código ≠ 0 quando o exec falha — nunca texto;
#   2. `preflight` exercita o helper contra um JSON conhecido ANTES de qualquer
#      medição e ABORTA se o instrumento não sabe ler. Instrumento não conferido
#      não mede — só produz caracteres.
field() {  # $1 = json  $2 = chave → valor em stdout; ABSENT se a chave falta; exit≠0 se o helper quebrou
  local out rc
  out=$(_J="$1" _K="$2" $COMPOSE exec -T -e _J -e _K routing-engine python3 -c "
import json, os
try:
    d = json.loads(os.environ['_J'])
except Exception:
    print('ABSENT'); raise SystemExit
v = d.get(os.environ['_K'], '__missing__')
print('ABSENT' if v == '__missing__' else json.dumps(v).strip('\"'))
" < /dev/null 2>/dev/null)
  rc=$?
  if [ "$rc" != 0 ]; then return 1; fi
  case "$out" in
    *"OCI runtime"*|*"executable file not found"*|*"not found in \$PATH"*) return 1 ;;
  esac
  printf '%s' "$out"
}

preflight() {
  local got
  got=$(field '{"probe":"ok","n":1}' probe) || {
    echo "  ❌ INSTRUMENTO QUEBRADO — o parser JSON não executou."
    echo "     (python3 no container 'routing-engine'; o container do redis não o tem)"
    echo "     Nada foi medido. Corrija o ambiente antes de tirar conclusão."
    exit 3
  }
  if [ "$got" != "ok" ]; then
    echo "  ❌ INSTRUMENTO MENTINDO — parser devolveu '$got', esperado 'ok'."
    exit 3
  fi
  echo "  instrumento conferido (parser JSON responde valor conhecido)"
}

snapshot() {  # imprime o estado relevante
  local qc inq rec
  qc=$(R GET "${TENANT}:queue_contact:${SID}")
  inq=$(R ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID")
  rec=$(R GET "${TENANT}:pool:${POOL}:claim_record:${SID}")
  echo "    no ZSET .............. $([ -n "$inq" ] && echo "SIM (score $inq)" || echo "não")"
  echo "    claim_record ......... $([ -n "$rec" ] && echo "presente" || echo "ausente")"
  if [ -z "$qc" ]; then
    echo "    queue_contact ........ AUSENTE (o JSON do item já não existe)"
  else
    local f
    for f in conference_id work_item_deadline assigned_to auto_attend; do
      # Falha do helper aborta: valor não lido nunca vira linha de relatório.
      printf '    %-20s %s\n' "$f" "$(field "$qc" "$f" || { echo; echo "  ❌ helper falhou lendo '$f' — abortando"; exit 3; })"
    done
  fi
}

echo "Item sob observação: $SID (pool $POOL)"
echo "── PREFLIGHT ───────────────────────────────────────────────────────────────"
preflight
echo
echo "── ANTES (o item deve estar na fila, sem dono) ─────────────────────────────"
snapshot
QC_BEFORE=$(R GET "${TENANT}:queue_contact:${SID}")
CONF_BEFORE=$(field "$QC_BEFORE" conference_id)
DL_BEFORE=$(field "$QC_BEFORE" work_item_deadline)

echo
echo "── AGORA, no Console ───────────────────────────────────────────────────────"
echo "   1. ative o pool '$POOL' e REIVINDIQUE o item $SID"
echo "   2. com o formulário na tela, dê F5 (recarregue a página)"
echo "   3. volte aqui e pressione ENTER"
echo
# `read` sem docker exec no caminho: todo exec acima leva `< /dev/null`, senão o
# stdin do script é consumido e este read volta vazio SEM ERRO.
read -r _ || true

echo "── DEPOIS ──────────────────────────────────────────────────────────────────"
snapshot
QC_AFTER=$(R GET "${TENANT}:queue_contact:${SID}")
CONF_AFTER=$(field "$QC_AFTER" conference_id)
DL_AFTER=$(field "$QC_AFTER" work_item_deadline)

echo
echo "── LOG DO BRIDGE (segunda testemunha) ──────────────────────────────────────"
LOG=$($COMPOSE logs --tail 400 orchestrator-bridge < /dev/null 2>/dev/null || true)
# `grep -c` com zero matches sai 1 — nunca encadear com &&.
N_NEW=$(printf '%s' "$LOG" | grep -c "Fase B: item de trabalho devolvido" || true)
N_OLD=$(printf '%s' "$LOG" | grep -c "last human dropped — re-routing" || true)
N_DEG=$(printf '%s' "$LOG" | grep -c "Fase B: holder lookup" || true)
echo "    'devolvido pelo work_task_release' ... ${N_NEW}x"
echo "    'last human dropped — re-routing' .... ${N_OLD}x   (caminho ANTIGO)"
echo "    'holder lookup falhou/HTTP' .......... ${N_DEG}x   (degradou p/ o antigo)"

echo
echo "======================================"
# VEREDICTO — sobre o valor DEPOIS, não sobre a igualdade antes/depois.
#
# A comparação antes==depois foi o que produziu o falso verde: duas leituras
# quebradas são iguais entre si. O sinal honesto é a FORMA do valor final, porque
# os dois caminhos deixam formas diferentes:
#   caminho antigo → work_item_deadline "" e conference_id null (defaults Pydantic)
#   caminho novo   → work_item_deadline ISO e conference_id preservado
# O "antes" entra só como corroboração, nunca como o critério.
echo "     conference_id  '$CONF_BEFORE' → '$CONF_AFTER'"
echo "     work_item_dead '$DL_BEFORE' → '$DL_AFTER'"
echo
if [ -z "$QC_AFTER" ]; then
  echo "  ⚠️  INCONCLUSIVO — o pacote do item sumiu."
  echo "     Sem queue_contact não há o que medir. Ou o item foi encerrado"
  echo "     (submetido/expirado) durante o teste, ou o F5 não chegou a ocorrer."
  exit 2
fi
case "$DL_AFTER" in
  ""|null|ABSENT)
    echo "  ❌ CAMINHO ANTIGO — work_item_deadline voltou VAZIO ('$DL_AFTER')."
    echo "     É a assinatura do re-publish de seis campos: as chaves ficam, os"
    echo "     valores viram default do Pydantic."
    if [ "$N_DEG" != "0" ]; then
      echo "     O log traz ${N_DEG}x 'holder lookup' — a Fase B degradou por árbitro"
      echo "     inalcançável (ROUTING_ENGINE_URL). O defeito volta, mas não calado."
    fi
    exit 1 ;;
  20[0-9][0-9]-*)
    echo "  ✅ FASE B ATIVA — o pacote atravessou o F5 intacto."
    echo "     work_item_deadline continua ISO; o caminho antigo o teria zerado."
    if [ "$N_NEW" = "0" ]; then
      echo "     ⚠️  mas o log NÃO traz 'devolvido pelo work_task_release'. Estado bom"
      echo "         sem a linha que o explica: conferir se o F5 realmente ocorreu."
    fi
    [ "$N_OLD" != "0" ] && \
      echo "     ⚠️  o log traz ${N_OLD}x o caminho antigo — conferir se é de OUTRA sessão."
    echo
    echo "  LEMBRETE — o formulário reaparecer na tela NÃO contradiz este verde."
    echo "  É a D5 (a tela não é fonte de posse), que é a Fase D: o Console redesenha"
    echo "  o form por replay do 'conversation.assigned' na reconexão. O que a Fase A"
    echo "  garante é que o SUBMIT desse form órfão seja recusado com 403."
    ;;
  *)
    echo "  ⚠️  INCONCLUSIVO — work_item_deadline com forma inesperada: '$DL_AFTER'"
    echo "     Não é ISO nem vazio. Não dá para atribuir a caminho nenhum."
    exit 2 ;;
esac
