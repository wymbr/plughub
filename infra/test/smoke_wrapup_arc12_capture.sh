#!/usr/bin/env bash
# smoke_wrapup_arc12_capture.sh — fatia 3: a captura do wrap-up chega ao Arc 12.
#
# O QUE ESTE SMOKE PROVA, e por que ele é o gate desta fatia. Até 2026-08-03
# `agent_business_events` tinha UMA linha, de seed (2026-06-10 12:00:00.000), e
# NENHUM produtor no repositório inteiro — nem YAML de skill, nem smoke, nem
# serviço. A infra do Arc 12 estava completa de ponta a ponta e ociosa. Este é o
# primeiro dado real, e o teste que impede a regressão para o estado anterior —
# que era invisível justamente por não haver nada que ficasse vermelho.
#
# Prova três coisas distintas, e a terceira é a que a fatia 2 preparou:
#   1. `capture.kind: scored`  → UMA linha, value = a resposta numérica (FCR);
#   2. `capture.kind: nominal` → N linhas, a resposta virando FOLHA da categoria
#      (multi-select ⇒ um evento por opção), value 1;
#   3. toda linha com `segment_id` preenchido — o CAMINHO A da atribuição por
#      participante, que nenhum produtor exercitava.
#   E o negativo, que vale tanto quanto: `resumo`/`proximos_passos` NÃO viram
#   evento (prosa fica no segmento — §D6).
#
# ATALHOS DECLARADOS. O wrap-up nasce de um hook `on_human_end` de contato real.
# Aqui o workflow é disparado direto pelo webhook do pool e o hash `seg_signal` é
# semeado à mão, porque o alvo do teste é a CAPTURA, não o hook (que a Camada D já
# tem gate próprio). Sem o seg_signal semeado a tool retorna antes — por desenho,
# para não publicar linha parcial — e o teste sairia INCONCLUSIVO, não verde.
#
# Uso (da raiz do repo, demo no ar):  bash infra/test/smoke_wrapup_arc12_capture.sh
# Sai: 0 verde · 1 vermelho · 2 INCONCLUSIVO (não conseguiu medir)

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
AUTH="${AUTH:-http://localhost:3202}"
DIALOG="http://localhost:3760"
CH_DB="plughub_demo"
POOL_WH="wrapup_detached_ia"
POOL_ORIGIN="retencao_humano"
POOL_PULL="retencao_humano-int"
FORM="dialog_wrapup_arc12_v1"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $DC exec -T redis redis-cli "$@" < /dev/null; }
chq()   { $DC exec -T clickhouse clickhouse-client -d "$CH_DB" --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⚠️  INCONCLUSIVO (abortou): $1"; exit 2; }

ORIGIN="sess_wrapup_arc12_$(date +%s)"
SEG="seg_wrapup_arc12_$(date +%s)"

echo "══ 0) login + form publicado ══"
TOK=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOK" ] || die "login falhou em $AUTH"
SUB=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+' | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } | base64 -d 2>/dev/null | jq -r '.sub // empty')
[ -n "$SUB" ] || die "não extraí o sub do JWT"
INST="human-${SUB}"
$CURL -f "$DIALOG/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1 \
  || DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_wrapup_arc12_form.sh \
  || die "não consegui publicar $FORM"
ok "token + form $FORM publicado (agente = $INST)"

echo "══ 1) semeia o seg_signal da ORIGEM (o que o hook faria) ══"
# Sem os ESTÁTICOS a tool não publica (guarda deliberada: linha parcial zeraria
# colunas no ReplacingMergeTree). O `pool_id` daqui vira o l1 da categoria.
redis HSET "session:${ORIGIN}:seg_signal:${SEG}" \
  segment_id "$SEG" tenant_id "$TENANT" pool_id "$POOL_ORIGIN" \
  instance_id "$INST" agent_type_id "human" sequence_index "0" \
  joined_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" duration_ms "12345" >/dev/null
ok "seg_signal semeado (origem=$ORIGIN seg=$SEG pool=$POOL_ORIGIN)"

echo "══ 2) semeia o agente pronto na fila interna ══"
redis SET "${TENANT}:instance:${INST}" \
  "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$POOL_PULL\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:ready" "$INST" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:instances" "$INST" >/dev/null
ok "$INST pronto em $POOL_PULL"

echo "══ 3) dispara o workflow de wrap-up ══"
TRIG=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON -d "{
  \"tenant_id\":\"$TENANT\",
  \"context\":{
    \"core.workflow.origin_session_id\":\"$ORIGIN\",
    \"core.survey.segment_id\":\"$SEG\",
    \"core.survey.agent_key\":\"$SUB\",
    \"hook.wrapup_pool\":\"$POOL_PULL\",
    \"hook.dialog_form_id\":\"$FORM\"
  }}")
SID=$(echo "$TRIG" | jq -r '.session_id // empty')
[ -n "$SID" ] || die "trigger não devolveu session_id — ${TRIG:0:200}"
ok "workflow disparado: $SID"

echo "══ 4) item parqueia em $POOL_PULL ══"
for _ in $(seq 1 25); do
  [ -n "$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')" ] && break
  sleep 1
done
[ -n "$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')" ] \
  || die "o item não entrou na fila — sem claim não há submit"
ok "item na fila"

echo "══ 5) claim ══"
CLAIM=$($DC exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL_PULL','session_id':'$SID','instance_id':'$INST'}).encode()
req=urllib.request.Request('http://localhost:3550/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
" < /dev/null 2>&1)
echo "$CLAIM" | grep -q '"claimed": *true' || die "claim recusado — ${CLAIM:0:200}"
ok "item reivindicado"

echo "══ 6) submete o form COM as duas capturas ══"
RTOK=$(redis HGET "${TENANT}:ctx:${SID}" "core.workflow.delegate_resume_token" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
[ -n "$RTOK" ] || die "sem resume token no ctx de $SID"
RCODE=$(curl -s -o /tmp/_wrapup_resume -w '%{http_code}' --max-time 20 \
  -X POST "$CG/v1/channels/webhook/resume/$RTOK" $JSON -H "Authorization: Bearer $TOK" -d "{
    \"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INST\",
    \"payload\":{\"answers\":{
      \"classificacao\":\"resolvido\",
      \"fcr\":\"sim\",
      \"servico\":[\"segunda_via\",\"alteracao_plano\"],
      \"resumo\":\"Cliente pediu segunda via e trocou de plano.\",
      \"proximos_passos\":\"Nenhum.\"
    }}}")
echo "   → HTTP $RCODE · $(head -c 160 /tmp/_wrapup_resume)"
# Pré-condição falha ⇒ INCONCLUSIVO e PARA. Julgar o Arc 12 sobre um submit que não
# aconteceu foi o defeito do smoke de aprovação (§ Erros de método item 6).
case "$RCODE" in
  200|202) ok "resume aceito" ;;
  *)       die "resume não completou (HTTP $RCODE) — nada a concluir sobre a captura" ;;
esac

echo "══ 7) OS EVENTOS CHEGARAM AO agent_business_events? ══"
ROWS=""
for _ in $(seq 1 30); do
  ROWS=$(chq "
    SELECT category, toString(value), if(isNotNull(segment_id),'com_seg','SEM_SEG')
      FROM $CH_DB.agent_business_events
     WHERE tenant_id='$TENANT' AND session_id='$ORIGIN'
     ORDER BY category FORMAT TSV")
  [ -n "$ROWS" ] && break
  sleep 2
done
if [ -z "$ROWS" ]; then
  bad "nenhum evento em 60 s — a captura não produziu dado (o Arc 12 continua sem produtor)"
else
  echo "$ROWS" | sed 's/^/     /'
  N=$(printf '%s\n' "$ROWS" | grep -c .)
  [ "$N" -eq 3 ] && ok "3 eventos (1 scored + 2 nominal do multi-select)" \
                 || bad "esperava 3 eventos, vieram $N"
  printf '%s\n' "$ROWS" | grep -q "${POOL_ORIGIN}.wrapup.fcr	1" \
    && ok "scored: ${POOL_ORIGIN}.wrapup.fcr = 1 (avg_value É a taxa de FCR)" \
    || bad "não achei o evento scored de FCR com value 1"
  for leaf in segunda_via alteracao_plano; do
    printf '%s\n' "$ROWS" | grep -q "${POOL_ORIGIN}.wrapup.servico.${leaf}	1" \
      && ok "nominal: folha '$leaf' virou categoria, value 1" \
      || bad "não achei a folha nominal '$leaf'"
  done
  printf '%s\n' "$ROWS" | grep -q "SEM_SEG" \
    && bad "há evento SEM segment_id — o caminho A não atribuiu" \
    || ok "todos os eventos com segment_id (caminho A do Arc 12)"
  printf '%s\n' "$ROWS" | grep -qiE 'resumo|proximos|prosa|cliente pediu' \
    && bad "PROSA virou evento — §D6 violada (texto livre deve ficar no segmento)" \
    || ok "prosa NÃO virou evento (resumo/próximos passos ficam no segmento)"
fi

echo "══ 8) a prosa foi para o SEGMENTO (o outro lado da §D6) ══"
PROSE=$(chq "
  SELECT ifNull(wrapup_summary,'') FROM $CH_DB.segments FINAL
   WHERE tenant_id='$TENANT' AND segment_id='$SEG' LIMIT 1" | tr -d '\r')
[ -n "$PROSE" ] && ok "segments.wrapup_summary preenchido: '${PROSE:0:40}…'" \
                || bad "wrapup_summary vazio — a prosa se perdeu"

echo
echo "── veredicto ──────────────────────────────────────────────────────────"
echo "   ✅ $PASS · ❌ $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
echo "   ✅ fatia 3: o Arc 12 tem produtor, com atribuição por segmento."
exit 0
