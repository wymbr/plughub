#!/usr/bin/env bash
# probe_reclaim_duplication.sh — item em trabalho ATIVO que voltou a ser reivindicável.
#
# O ACHADO QUE MOTIVA (2026-08-04, probe_console_restore_after_reload): depois de um F5 no
# Console, o item reivindicado apareceu SIMULTANEAMENTE como contato em atendimento (formulário
# na tela, submetível) e como linha da inbox pull — e o relatório de pendências o classificou
# `unclaimed`, isto é, DE VOLTA no ZSET. Se isso se confirmar, não é a lacuna 2b (invisibilidade):
# é o oposto — o mesmo trabalho oferecido a um segundo dono enquanto o primeiro o executa.
#
# Este probe NÃO conclui pela tela. Ele lê o estado e o classifica, porque "aparecer em duas
# listas" tem pelo menos três explicações com consequências muito diferentes:
#
#   (1) RE-ENFILEIRADO de fato — o item está no ZSET e alguém ainda ocupa vaga por ele.
#       Duplicação real: dois donos possíveis para um trabalho. Grave.
#   (2) RE-ENFILEIRADO e LIBERADO — está no ZSET e ninguém ocupa vaga. O F5 devolveu o item
#       corretamente, e quem mente é a TELA, que seguiu mostrando um formulário órfão.
#   (3) NUNCA VOLTOU — não está no ZSET. Aí a linha da inbox era cache do polling e o defeito
#       é de frontend, não de estado.
#
# A digital do re-enfileiramento é `queued_at_ms` × `first_queued_ms`: o segundo é preservado na
# devolução à fila e o primeiro é reescrito (P3). Divergirem prova que houve requeue, sem depender
# de log — e é a única evidência que sobrevive ao tempo.
#
# Uso:  bash infra/test/probe_reclaim_duplication.sh [session_id] [pool]
# Saída: 0 = estado íntegro · 1 = duplicação/incoerência achada · 2 = INCONCLUSIVO

set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
SID="${1:-dbdb1e94-1b86-4e2c-ab84-cd9498e1fa73}"
POOL="${2:-formfill_demo}"
UI="http://localhost:5174"

rcli() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "══ estado do item $SID (pool=$POOL) ══"
echo

if ! rcli PING | grep -q PONG; then
  echo "⚠️  INCONCLUSIVO — redis não respondeu."; exit 2
fi

IN_Z=$(rcli ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID")
LEASE=$(rcli GET "${TENANT}:pool:${POOL}:claim:${SID}")
LEDGER=$(rcli GET "${TENANT}:work_task:${SID}")
QJSON=$(rcli GET "${TENANT}:queue_contact:${SID}")

echo "── chaves ─────────────────────────────────────────────────────────────────"
echo "   ZSET (reivindicável) ... $([ -n "$IN_Z" ] && echo "SIM (score=$IN_Z)" || echo "não")"
echo "   lease do claim ......... $([ -n "$LEASE" ] && echo "$LEASE" || echo "ausente")"
echo "   ledger work_task ....... $([ -n "$LEDGER" ] && echo presente || echo ausente)"
echo "   JSON do contato ........ $([ -n "$QJSON" ] && echo presente || echo AUSENTE)"
echo

# ── TTL: a JANELA é legível agora; esperá-la vencer não acrescenta nada ─────────
# O prazo do item é 24 h, e o JSON só vive tanto se o re-enqueue tiver carimbado
# `work_item_deadline`. Um TTL na casa de 4 h (o default) contra prazo de 24 h já É
# a prova de que o campo se perdeu — o membro do ZSET vai sobreviver ao JSON e o item
# passará a mentir (listado na inbox, irreivindicável). Ler o TTL responde hoje o que
# a expiração só confirmaria daqui a horas.
TTL_JSON=$(rcli TTL "${TENANT}:queue_contact:${SID}")
TTL_LEDG=$(rcli TTL "${TENANT}:work_task:${SID}")
hms() { local s="${1:-}"; case "$s" in ''|-*|*[!0-9]*) echo "$s";; *) printf '%dh%02dm' $((s/3600)) $(((s%3600)/60));; esac; }
echo "── TTL (a janela, medida em vez de esperada) ──────────────────────────────"
echo "   JSON do contato ........ ${TTL_JSON:-?}s  ($(hms "${TTL_JSON:-}"))   [-1 = sem TTL · -2 = inexistente]"
echo "   ledger work_task ....... ${TTL_LEDG:-?}s  ($(hms "${TTL_LEDG:-}"))"
if [ "${TTL_JSON:-0}" -gt 0 ] 2>/dev/null && [ "${TTL_LEDG:-0}" -gt 0 ] 2>/dev/null; then
  if [ "$TTL_JSON" -lt "$TTL_LEDG" ]; then
    echo "   ► O JSON morre ANTES do ledger ⇒ o membro do ZSET sobreviverá ao JSON."
    echo "     A partir daí o item fica listado na inbox e irreivindicável (not_in_queue) —"
    echo "     é a assinatura de \`work_item_deadline\` perdido no re-enqueue."
  else
    echo "   ► JSON vive ao menos tanto quanto o ledger — o prazo do item foi respeitado."
  fi
fi
echo

# ── Digital do requeue ──────────────────────────────────────────────────────────
if [ -n "$QJSON" ]; then
  FIRST=$(jq -r '.first_queued_ms // empty' <<<"$QJSON" 2>/dev/null)
  QUEUED=$(jq -r '.queued_at_ms // empty'   <<<"$QJSON" 2>/dev/null)
  ASSIGNED=$(jq -r '.assigned_to // ""'     <<<"$QJSON" 2>/dev/null)
  AUTO=$(jq -r '.auto_attend // false'      <<<"$QJSON" 2>/dev/null)
  echo "── digital do re-enfileiramento ───────────────────────────────────────────"
  echo "   first_queued_ms = ${FIRST:-?}"
  echo "   queued_at_ms    = ${QUEUED:-?}"
  echo "   assigned_to     = '${ASSIGNED}'   auto_attend=${AUTO}"
  # TRÊS casos, não dois. A versão anterior deste bloco colapsava "ausente" em
  # "iguais" e imprimia *"nenhum requeue registrado"* — um valor que FALTAVA saindo
  # como veredicto tranquilizador. Foi assim que a duplicação quase passou.
  if [ -z "$FIRST" ] || [ -z "$QUEUED" ]; then
    echo "   ► INCONCLUSIVA — campo ausente (first='${FIRST:-∅}' queued='${QUEUED:-∅}')."
    echo "     NÃO leia como 'não houve requeue'. Ausência de first_queued_ms significa que"
    echo "     o JSON foi escrito por um caminho que não o carimba — o que é, ele próprio,"
    echo "     um fato sobre QUEM re-enfileirou."
  elif [ "$FIRST" != "$QUEUED" ]; then
    DELTA=$(( (QUEUED - FIRST) / 1000 ))
    echo "   ► DIVERGEM em ${DELTA}s ⇒ o item FOI devolvido à fila depois do enqueue original."
  else
    echo "   ► iguais ⇒ este JSON não registra requeue (mas veja o ZSET: o score é a"
    echo "     evidência independente, e ele pode ter sido reescrito sem tocar no JSON)."
  fi
  echo
fi

# ── Quem ocupa vaga por esta sessão ─────────────────────────────────────────────
# A vaga é o fato; o semáforo é onde ele mora. Se alguém ainda ocupa vaga por esta
# sessão E o item está no ZSET, o mesmo trabalho tem dono e está à venda ao mesmo tempo.
echo "── ocupação (semáforo do recurso) ─────────────────────────────────────────"
OCCUPANTS=""
while read -r k; do
  [ -z "$k" ] && continue
  if rcli SMEMBERS "$k" | grep -q "$SID"; then
    OCCUPANTS="$OCCUPANTS ${k}"
    echo "   ocupa: $k"
    rcli SMEMBERS "$k" | grep "$SID" | sed 's/^/       membro: /'
  fi
done < <($COMPOSE exec -T redis redis-cli --scan --pattern "${TENANT}:instance:*:sessions" < /dev/null 2>/dev/null | tr -d '\r')
[ -z "$OCCUPANTS" ] && echo "   nenhuma instância ocupa vaga por esta sessão."
echo

# ── Como as duas superfícies o mostram ──────────────────────────────────────────
echo "── superfícies ────────────────────────────────────────────────────────────"
IN_LIST=$(curl -fsS -m 5 "$UI/api/work_queue/list?pools=$POOL" 2>/dev/null \
          | jq -r --arg s "$SID" '[.contacts[]? | select(.session_id==$s)] | length' 2>/dev/null)
CLS=$(curl -fsS -m 10 "$UI/api/work_queue/pending?tenant_id=$TENANT&all=1&max_keys=20000" 2>/dev/null \
      | jq -r --arg s "$SID" '.items[]? | select(.session_id==$s or .queue_session_id==$s) | .state' 2>/dev/null)
echo "   inbox pull (/work_queue/list) ....... ${IN_LIST:-?} linha(s)"
echo "   Monitor › Pendências (state) ........ ${CLS:-<fora do ledger>}"
echo

# ── Veredicto ───────────────────────────────────────────────────────────────────
echo "══ VEREDICTO ══════════════════════════════════════════════════════════════"
if [ -n "$IN_Z" ] && [ -n "$OCCUPANTS" ]; then
  echo "   ❌ DUPLICAÇÃO REAL — o item está no ZSET (reivindicável por qualquer agente do"
  echo "      pool) E há instância ocupando vaga por ele. Dois donos possíveis para o mesmo"
  echo "      trabalho: o segundo a puxar entra numa sessão já em atendimento, e o primeiro"
  echo "      submete um formulário de um item que não detém mais."
  echo "      Isto NÃO é a lacuna 2b — é o defeito oposto, e mais grave: 2b perde trabalho de"
  echo "      vista; isto o entrega duas vezes."
  exit 1
fi
if [ -n "$IN_Z" ] && [ -z "$OCCUPANTS" ]; then
  echo "   ⚠️  DEVOLVIDO, MAS A TELA NÃO SOUBE — o item voltou à fila e ninguém ocupa vaga."
  echo "      O estado do backend está íntegro; quem mente é o Console, que seguiu exibindo"
  echo "      um formulário de item que já não é seu. O submit desse formulário é a pergunta"
  echo "      seguinte: se ele passa, a devolução não protegeu nada."
  exit 1
fi
if [ -z "$IN_Z" ] && [ "${IN_LIST:-0}" -gt 0 ] 2>/dev/null; then
  echo "   ⚠️  INCOERENTE — fora do ZSET mas listado na inbox. Defeito de leitura/cache."
  exit 1
fi
echo "   ✅ íntegro — item fora do ZSET$([ -n "$OCCUPANTS" ] && echo " e com vaga ocupada" || echo "")."
echo "      (Se o achado original foi observado e agora sumiu, o estado MUDOU entre as duas"
echo "       leituras — anote a hora; um resultado limpo depois do fato não desmente o fato.)"
exit 0
