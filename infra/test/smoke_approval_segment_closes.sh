#!/usr/bin/env bash
# smoke_approval_segment_closes.sh — a APROVAÇÃO fecha o segmento do humano?
#
# POR QUE ESTE SMOKE EXISTE. O `TODO.md` afirmava que "a aprovação SEGUE produzindo
# segmentos órfãos" e tratava os 17 órfãos de `aprovacao_deploy` como evidência viva
# de um defeito aberto. A medição de 2026-08-03 (`probe_block2.sh`) mostrou que os 17
# vão de 2026-07-16 a **2026-07-24** — o último é SEIS DIAS anterior ao fix H1/H2
# (2026-07-30). Nenhum depois do corte.
#
# Só que isso NÃO prova que a aprovação está consertada: não houve UMA aprovação
# desde 24/07. "Nenhum órfão novo" e "nenhuma aprovação nova" são indistinguíveis na
# tabela — ausência de amostra, não evidência de correção. Este smoke produz a
# amostra que faltava, e fica como gate: a aprovação não tinha smoke nenhum.
#
# O QUE ELE PROVA (ou refuta): que o ciclo trigger → claim → submit deixa o segmento
# do humano FECHADO, com `duration_ms` e `close_reason` — do mesmo jeito que o
# wrap-up passou a fazer (`task_submitted`, comprovado: 20 linhas em
# `retencao_humano-int`). O H1 é genérico (dispara com qualquer
# `_claimant_instance_id` que comece com `human-`), então a previsão é que cubra a
# aprovação também. Se cobrir, o item do TODO é stale. Se NÃO cobrir, apareceu o
# defeito que os 17 órfãos sugeriam — e aí há um caminho de resume diferente.
#
# Uso (da raiz do repo, demo no ar):  bash infra/test/smoke_approval_segment_closes.sh
# Requer: curl, jq.
# Sai: 0 verde · 1 vermelho (defeito REAL) · 2 INCONCLUSIVO (não conseguiu medir)

set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
AUTH="${AUTH:-http://localhost:3202}"    # 3200 do host é o ai-gateway, não a auth-api
DIALOG="http://localhost:3760"
CH_DB="plughub_demo"
POOL_WH="gate_promocao_ia"               # workflow webhook que monta o pacote
POOL_PULL="aprovacao_deploy"             # pool pull onde o aprovador reivindica
FORM="dialog_promocao_deploy"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }
chq()   { $COMPOSE exec -T clickhouse clickhouse-client -d "$CH_DB" --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0; INCONCL=0
ok()      { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad()     { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
unknown() { echo "   ⚠️  INCONCLUSIVO: $1"; INCONCL=$((INCONCL+1)); }
die()     { echo "   ⚠️  INCONCLUSIVO (abortou): $1"; exit 2; }

echo "══ 0) login — o resume de APROVAÇÃO exige ABAC \`approvals.decide\` ══"
# `resume_required_abac` (webhook.py:594) resolve a capacidade SERVER-SIDE a partir
# do contexto da workflow: `session.decisions` presente ⇒ ("approvals","decide").
# Wrap-up não tem decisions e cai no caminho genérico — é a diferença entre os dois,
# e é a primeira suspeita de por que um teria sido coberto e o outro não.
LOGIN=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}")
TOK=$(echo "$LOGIN" | jq -r '.access_token // empty')
[ -n "$TOK" ] || die "login falhou em $AUTH — ${LOGIN:0:200}"
# `sub` do JWT = user_id; a instância humana do claim é `human-{sub}` (main.py:1517).
SUB=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+' | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } | base64 -d 2>/dev/null | jq -r '.sub // empty')
[ -n "$SUB" ] || die "não consegui extrair o sub do JWT"
INST="human-${SUB}"
echo "   ✓ token obtido · aprovador = $INST"

echo "══ 1) o DialogForm '$FORM' está publicado? ══"
if $CURL -f "$DIALOG/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1; then
  echo "   ✓ publicado"
else
  echo "   → seedando via infra/test/seed_dialog_promocao_deploy_form.sh"
  DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_promocao_deploy_form.sh \
    || die "não consegui semear o form"
fi

echo "══ 2) semeia o aprovador PRONTO no pool pull ══"
# Por que semear: no fluxo real quem registra a instância é o login do Console. O
# smoke não abre navegador. Semeamos a MESMA forma que o `smoke_directed_pull.sh`
# usa (source=human_login), porque o gate de claim vive no Routing Engine e é ele
# que este teste precisa exercitar — não o login.
redis SET "${TENANT}:instance:${INST}" \
  "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$POOL_PULL\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:ready" "$INST" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:instances" "$INST" >/dev/null
echo "   ✓ $INST pronto em $POOL_PULL"

echo "══ 3) dispara o gate de promoção ══"
TRIG=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON \
  -d "{\"tenant_id\":\"$TENANT\"}")
SID=$(echo "$TRIG" | jq -r '.session_id // empty')
[ -n "$SID" ] || die "trigger não devolveu session_id — ${TRIG:0:200}"
echo "   sessão do workflow: $SID"

echo "══ 4) o item parqueia na fila de $POOL_PULL? ══"
QUEUED=""
for _ in $(seq 1 25); do
  Z=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
  [ -n "$Z" ] && { QUEUED=1; break; }
  sleep 1
done
[ -n "$QUEUED" ] || die "o item não entrou na fila pull — sem item não há claim nem segmento"
ok "item na fila pull"

echo "══ 5) claim pelo aprovador (Routing Engine é o único árbitro) ══"
CLAIM=$($COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL_PULL','session_id':'$SID','instance_id':'$INST'}).encode()
req=urllib.request.Request('http://localhost:3550/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
" < /dev/null 2>&1)
echo "   → ${CLAIM:0:200}"
echo "$CLAIM" | grep -q '"claimed": *true' || die "claim recusado — sem claim não há segmento humano a fechar"
ok "item reivindicado"

echo "══ 6) o claim CRIA o segmento humano? (participant_joined via process_routed) ══"
SEG_OPEN=""
for _ in $(seq 1 30); do
  SEG_OPEN=$(chq "
    SELECT count() FROM $CH_DB.segments FINAL
     WHERE tenant_id='$TENANT' AND session_id='$SID'
       AND agent_type='human' AND ended_at IS NULL" | tr -d '\r')
  [ "${SEG_OPEN:-0}" -gt 0 ] 2>/dev/null && break
  sleep 2
done
if [ "${SEG_OPEN:-0}" -gt 0 ] 2>/dev/null; then
  ok "segmento humano ABERTO existe (é este que precisa fechar)"
else
  die "nenhum segmento humano apareceu em 60 s. Sem ele o teste não pode reprovar
        nem aprovar — não confundir com 'fechou'."
fi

echo "══ 7) submete a decisão (resume com Bearer + ABAC approvals.decide) ══"
RTOK=$(redis HGET "${TENANT}:ctx:${SID}" "core.workflow.delegate_resume_token" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
if [ -z "$RTOK" ]; then
  RTOK=$(redis HGET "${TENANT}:ctx:${SID}" "core.workflow.resume_token" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
fi
[ -n "$RTOK" ] || die "não achei resume token no ctx da sessão $SID"
# Corpo conferido em `WebhookResumeRequest` (channel-gateway/main.py:736), não
# adivinhado: `tenant_id` é OBRIGATÓRIO, e a aprovação exige ainda o par
# `pool_id`+`instance_id` — o "A5 claimant binding": o ingress lê a lease do claim
# e exige caller == claimant. A v1 mandou só `payload` e levou 422; o submit nunca
# aconteceu.
RCODE=$(curl -s -o /tmp/_appr_resume -w '%{http_code}' --max-time 20 \
  -X POST "$CG/v1/channels/webhook/resume/$RTOK" $JSON \
  -H "Authorization: Bearer $TOK" \
  -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INST\",
       \"payload\":{\"choice\":\"aprovar\",\"edits\":{}}}")
echo "   → HTTP $RCODE · $(head -c 200 /tmp/_appr_resume)"
# ABORTA se o submit não aconteceu. Na v1 este passo apenas marcava INCONCLUSIVO e
# o passo 8 seguia julgando — e reportou "DEFEITO REAL na aprovação" quando o
# segmento estava aberto pelo motivo mais banal possível: ninguém tinha submetido.
# Um portão que acusa defeito no ALVO quando a falha é na PRÓPRIA montagem é pior
# que portão nenhum: ele produz um vermelho convincente e manda consertar o lugar
# errado. Pré-condição falha ⇒ INCONCLUSIVO, e o teste PARA.
case "$RCODE" in
  200|202) ok "resume aceito (HTTP $RCODE)" ;;
  401|403) die "resume recusado por AUTORIZAÇÃO (HTTP $RCODE) — o gate ABAC
        \`approvals.decide\` ou o binding do claimante barrou. É achado sobre o
        GATE, não sobre o fechamento do segmento: o passo 8 não julga." ;;
  *)       die "resume não completou (HTTP $RCODE) — pré-condição do passo 8
        falhou; NÃO existe conclusão sobre o segmento." ;;
esac

echo "══ 8) O SEGMENTO DO APROVADOR FECHOU? ══"
ROW=""
for _ in $(seq 1 30); do
  ROW=$(chq "
    SELECT concat(toString(duration_ms), '|', ifNull(close_reason,''), '|', ifNull(outcome,''))
      FROM $CH_DB.segments FINAL
     WHERE tenant_id='$TENANT' AND session_id='$SID'
       AND agent_type='human' AND ended_at IS NOT NULL
     LIMIT 1" | tr -d '\r')
  [ -n "$ROW" ] && break
  sleep 2
done
if [ -z "$ROW" ]; then
  # Só se chega aqui com o resume ACEITO (o passo 7 aborta em qualquer outro caso).
  # Logo, segmento aberto aqui é o defeito de verdade.
  bad "o segmento do aprovador continua ABERTO 60 s APÓS UM RESUME ACEITO —
        a aprovação REPRODUZ o defeito dos 17 órfãos, e o item do TODO está certo
        quanto ao defeito (embora errado quanto às datas)."
else
  DUR=$(echo "$ROW" | cut -d'|' -f1); CR=$(echo "$ROW" | cut -d'|' -f2)
  echo "   duration_ms=$DUR  close_reason='${CR:-<vazio>}'"
  ok "segmento FECHADO"
  [ "${DUR:-0}" -gt 0 ] 2>/dev/null \
    && ok "duration_ms > 0 (o tempo de trabalho do aprovador existe como número)" \
    || bad "duration_ms=$DUR — fechou sem duração; o número não serve para relatório"
  [ -n "$CR" ] \
    && ok "close_reason='$CR' (separa submetido de expirado — D5)" \
    || bad "close_reason vazio — submetido e expirado ficam indistinguíveis"
fi

echo
echo "── veredicto ──────────────────────────────────────────────────────────"
echo "   ✅ $PASS · ❌ $FAIL · ⚠️  $INCONCL"
[ "$FAIL"    -gt 0 ] && { echo "   ❌ DEFEITO REAL na aprovação."; exit 1; }
[ "$INCONCL" -gt 0 ] && { echo "   ⚠️  inconclusivo — não é verde."; exit 2; }
echo "   ✅ a aprovação fecha o segmento do humano; o item do TODO é stale."
exit 0
