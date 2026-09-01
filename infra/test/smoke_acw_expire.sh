#!/usr/bin/env bash
# smoke_acw_expire.sh — ADR adr-internal-work-queue-author-bound, fase I5 (núcleo A+B).
#
# Prova, contra o DADO, o caminho de ENCERRAMENTO de um item de trabalho author-bound:
#
#   A  o ledger `{t}:work_task:{session}` nasce no despacho do delegate, com o pool
#      REAL do item e o resume_token — é o único lugar que liga a sessão ao item
#      parqueado (o `session:{id}:meta` aponta para o pool do WORKFLOW enquanto
#      ninguém reivindica, ou seja, mente exatamente no caso que interessa);
#   B  o TTL do JSON do contato acompanha o DEADLINE do delegate (24 h) em vez do
#      default de 4 h. Sem isso, entre 4 h e 24 h o membro do ZSET sobrevive sozinho:
#      o item segue listado na inbox, SEM `assigned_to` (perde o author-binding) e
#      irreivindicável — `work_task_claim` lê o JSON e devolve `not_in_queue`;
#   C  o supervisor encerra a pendência (D4): o item sai da fila, o ledger é
#      consumido e a workflow segue pelo `on_timeout` até fechar;
#   D  o encerramento é one-shot: repetir devolve 404 `no_work_task`, não um 200 que
#      não fez nada.
#
# Cenário REIVINDICADO (segmento humano fechando com `acw_supervisor_closed`) só é
# exercitado com INSTANCE=human-<user_id> de um agente logado — senão é anunciado
# como NÃO EXERCITADO, nunca como sucesso.
#
# Usa o par formfill_demo_ia → formfill_demo (mesmo harness do smoke_formfill_renderer):
# é o caminho pull genérico, não exige atendimento no Console e tem `timeout_hours: 24`.
#
# Pré-requisitos: demo no ar; pools formfill_demo{,_ia}; skill_formfill_demo_v1
# deployado; DialogForm dialog_formfill_demo publicado. Requer curl + jq.
#
# Uso (raiz do repo):
#   bash infra/test/smoke_acw_expire.sh
#   INSTANCE=human-<user_id> bash infra/test/smoke_acw_expire.sh    # inclui o cenário reivindicado
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
MCP="http://localhost:3100"
# auth-api = 3202 no HOST (mapeado 3202:3200). A 3200 do host é o ai-gateway — apontar
# para lá devolve "login falhou" sem que haja nada errado com as credenciais.
AUTH="${AUTH:-http://localhost:3202}"
CH_DB="plughub_demo"
CH="$COMPOSE exec -T clickhouse clickhouse-client"
POOL_WH="formfill_demo_ia"
POOL_PULL="formfill_demo"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
INSTANCE="${INSTANCE:-}"

# CAP-12: /api/work_queue/claim passou a exigir credencial (o /expire ja exigia).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"; plughub_auth_curl_shim
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $COMPOSE exec -T redis redis-cli "$@"; }

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

echo "══ 0) login (o gatilho de supervisor exige role supervisor|admin) ══"
LOGIN=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}")
TOK=$(echo "$LOGIN" | jq -r '.access_token // empty')
[ -n "$TOK" ] || {
  echo "   ✗ login falhou em $AUTH — resposta: ${LOGIN:0:200}"
  echo "     (auth-api = 3202 no host; 3200 é o ai-gateway)"
  exit 1
}
echo "   ✓ token obtido"

echo "══ 1) dispara o workflow (delega o form ao pool pull) ══"
SID=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_briefing_demo\"}}" \
  | jq -r '.session_id // empty')
[ -n "$SID" ] || { echo "   ✗ trigger não devolveu session_id"; exit 1; }
echo "   sessão do workflow: $SID"

echo "══ 2) aguarda o item parquear em $POOL_PULL ══"
QUEUED=""
for _ in $(seq 1 20); do
  Z=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
  [ -n "$Z" ] && { QUEUED=1; break; }
  sleep 1
done
[ -n "$QUEUED" ] || { echo "   ✗ o item não entrou na fila — nada a expirar; abortando"; exit 1; }
echo "   ✓ item na fila"

echo "══ A) ledger do item de trabalho ══"
LEDGER=$(redis GET "${TENANT}:work_task:${SID}" | tr -d '\r')
if [ -z "$LEDGER" ]; then
  bad "ledger {t}:work_task:$SID AUSENTE — o despacho não o gravou (channel-gateway rebuildado?)"
else
  L_POOL=$(echo "$LEDGER"  | jq -r '.pool_id // empty')
  L_QSID=$(echo "$LEDGER"  | jq -r '.queue_session_id // empty')
  L_TOK=$(echo "$LEDGER"   | jq -r '.resume_token // empty')
  L_DL=$(echo "$LEDGER"    | jq -r '.deadline // empty')
  [ "$L_POOL" = "$POOL_PULL" ] && ok "pool_id do ledger = $POOL_PULL (o pool REAL do item)" \
                               || bad "pool_id do ledger = '$L_POOL' (esperado $POOL_PULL)"
  [ "$L_QSID" = "$SID" ] && ok "queue_session_id = a sessão parqueada" \
                         || bad "queue_session_id = '$L_QSID' (esperado $SID)"
  [ -n "$L_TOK" ] && ok "resume_token presente (é o que o supervisor usa)" \
                  || bad "resume_token ausente — o gatilho de supervisor não teria como agir"
  [ -n "$L_DL" ] && echo "   deadline=$L_DL"
fi

echo "══ B) TTL do JSON do contato acompanha o deadline (não os 4 h default) ══"
TTL=$(redis TTL "${TENANT}:queue_contact:${SID}" | tr -d '\r')
echo "   TTL=$TTL s"
if [ "$TTL" -gt 14400 ]; then
  ok "TTL > 4 h — o JSON sobrevive ao prazo do delegate (24 h)"
else
  bad "TTL=$TTL ≤ 14400 — o item viraria fantasma (listado, sem assigned_to, irreivindicável)"
fi

# ── Cenário REIVINDICADO (opcional): só com uma instância humana logada ───────────
CLAIMED=0
if [ -n "$INSTANCE" ]; then
  echo "══ C0) claim por $INSTANCE (cenário reivindicado) ══"
  R=$($CURL -X POST "$MCP/api/work_queue/claim/$SID" $JSON \
      -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INSTANCE\"}")
  echo "   → $R"
  if [ "$(echo "$R" | jq -r '.claimed // false')" = "true" ]; then
    CLAIMED=1; ok "item reivindicado"
    sleep 3
  else
    bad "claim falhou ($(echo "$R" | jq -r '.reason // "?"')) — cenário reivindicado não exercitado"
  fi
fi

echo "══ C) supervisor encerra a pendência ══"
HTTP=$($CURL -o /tmp/_acw_exp -w '%{http_code}' -X POST "$MCP/api/work_queue/expire/$SID" \
  -H "Authorization: Bearer $TOK" $JSON -d "{\"tenant_id\":\"$TENANT\"}")
echo "   HTTP $HTTP — $(cat /tmp/_acw_exp)"
[ "$HTTP" = "200" ] && ok "encerramento aceito" || bad "esperado 200, veio $HTTP"

sleep 6
Z=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
[ -z "$Z" ] && ok "item saiu do ZSET" || bad "item AINDA na fila (score=$Z) — o expire não alcançou o árbitro"

JSN=$(redis EXISTS "${TENANT}:queue_contact:${SID}" | tr -d '\r')
[ "$JSN" = "0" ] && ok "JSON do contato removido" || bad "JSON do contato ainda existe"

LED=$(redis EXISTS "${TENANT}:work_task:${SID}" | tr -d '\r')
[ "$LED" = "0" ] && ok "ledger consumido" || bad "ledger não foi consumido — um segundo encerramento agiria sobre item morto"

# A workflow tem de RETOMAR pelo on_timeout e fechar. Não se pergunta isso ao
# `{t}:session:{id}:status`: esse valor só distingue suspended↔active (nada escreve
# "closed"; o "closed" do get_status é a AUSÊNCIA da chave, que só acontece por TTL,
# horas depois). Perguntar ali devolveria "active" — um valor plausível e errado.
# A fonte é o fechamento gravado no analytics.
CLOSED=""
for _ in $(seq 1 12); do
  CLOSED=$($CH -q "SELECT if(closed_at IS NULL OR toUnixTimestamp(closed_at)=0,'','closed') \
           FROM ${CH_DB}.sessions FINAL \
           WHERE tenant_id='$TENANT' AND session_id='$SID'" | tr -d '\r')
  [ "$CLOSED" = "closed" ] && break
  sleep 2
done
[ "$CLOSED" = "closed" ] && ok "workflow seguiu o on_timeout e fechou" \
                         || bad "workflow não fechou — o resume não chegou ao bridge"

echo "══ D) one-shot: repetir o encerramento ══"
HTTP2=$($CURL -o /tmp/_acw_exp2 -w '%{http_code}' -X POST "$MCP/api/work_queue/expire/$SID" \
  -H "Authorization: Bearer $TOK" $JSON -d "{\"tenant_id\":\"$TENANT\"}")
[ "$HTTP2" = "404" ] && ok "404 no_work_task (nada a encerrar) — não finge sucesso" \
                     || bad "esperado 404, veio $HTTP2 ($(cat /tmp/_acw_exp2))"

if [ "$CLAIMED" = "1" ]; then
  echo "══ E) segmento humano fechou com acw_supervisor_closed ══"
  sleep 4
  CR=$($CH -q "SELECT close_reason FROM ${CH_DB}.segments FINAL \
       WHERE tenant_id='$TENANT' AND session_id='$SID' AND agent_type='human' \
       ORDER BY started_at DESC LIMIT 1" | tr -d '\r')
  echo "   close_reason=$CR"
  [ "$CR" = "acw_supervisor_closed" ] && ok "causa nomeada (≠ acw_expired do prazo, ≠ task_submitted)" \
    || bad "esperado acw_supervisor_closed, veio '$CR'"
else
  echo "══ E) cenário REIVINDICADO — NÃO EXERCITADO ══"
  echo "   ⚠️  rode com INSTANCE=human-<user_id> de um agente logado para cobrir o"
  echo "      fechamento do segmento humano (acw_supervisor_closed). Sem isso, esta"
  echo "      execução NÃO diz nada sobre esse caminho."
fi

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "  ✅ I5 núcleo A+B em vigor"
