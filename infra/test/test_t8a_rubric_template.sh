#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T8-A — Rubrica-template: fundação backend (spec §16.3).
# Valida CRUD + versionamento + resolução (default tenant / override campanha):
#   1. cria default do tenant (draft v1) → publish → snapshot v1;
#   2. resolve (sem campanha) → tenant_default v1;
#   3. editar publicada bifurca p/ draft e BUMPa versão (v2); resolve ainda devolve a
#      publicada (v1) — snapshot imutável;
#   4. publish v2 → resolve passa a devolver v2;
#   5. override de campanha publicado vence o default no resolve(campaign=X);
#   6. segundo default do tenant → 409 (índice único parcial).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CAMP="${CAMP:-camp_t8a_$RANDOM}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
RT="$EVAL/v1/evaluation/rubric-templates"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# limpa default pré-existente do tenant (idempotência entre execuções) via UID próprio
TUID="t8a_$RANDOM"; T="${TENANT}_${TUID}"   # tenant isolado p/ não colidir no índice único

echo "══ CASO 1 — cria default do tenant (draft v1) ══"
R=$($CURL -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"tenant\",\"name\":\"Régua padrão\",\"body\":\"v1: pontue 0/5/10; cite evidência por stream_entry_id.\"}")
RID=$(echo "$R" | jq -r '.id // empty')
assert "criada"        true  "$([ -n "$RID" ] && echo true || echo false)"
assert "deploy_status" draft "$(echo "$R" | jq -r '.deploy_status')"
assert "version"       1     "$(echo "$R" | jq -r '.version')"
[ -n "$RID" ] || { echo "  ✗ abortando"; exit 1; }

echo "══ CASO 2 — publish → snapshot v1 + resolve ══"
$CURL -X POST "$RT/$RID/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
assert "deploy_status após publish" published "$($CURL "$RT/$RID?tenant_id=$T" | jq -r '.deploy_status')"
assert "versions count" 1 "$($CURL "$RT/$RID/versions?tenant_id=$T" | jq -r '.count')"
RES=$($CURL "$RT/resolve?tenant_id=$T")
assert "resolve source" tenant_default "$(echo "$RES" | jq -r '.resolved.source')"
assert "resolve version" 1 "$(echo "$RES" | jq -r '.resolved.version')"

echo "══ CASO 3 — editar publicada bifurca draft + bump v2; resolve ainda devolve v1 ══"
$CURL -X PUT "$RT/$RID?tenant_id=$T" $JSON -d '{"body":"v2: idem + anti-viés explícito."}' >/dev/null
G=$($CURL "$RT/$RID?tenant_id=$T")
assert "deploy_status após edit" draft "$(echo "$G" | jq -r '.deploy_status')"
assert "version após edit"       2     "$(echo "$G" | jq -r '.version')"
RES=$($CURL "$RT/resolve?tenant_id=$T")
assert "resolve ainda v1 (publicada)" 1 "$(echo "$RES" | jq -r '.resolved.version')"
assert "resolve body ainda v1" "v1: pontue 0/5/10; cite evidência por stream_entry_id." "$(echo "$RES" | jq -r '.resolved.body')"

echo "══ CASO 4 — publish v2 → resolve passa a v2 ══"
$CURL -X POST "$RT/$RID/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
assert "versions count (1+2)" 2 "$($CURL "$RT/$RID/versions?tenant_id=$T" | jq -r '.count')"
RES=$($CURL "$RT/resolve?tenant_id=$T")
assert "resolve agora v2" 2 "$(echo "$RES" | jq -r '.resolved.version')"

echo "══ CASO 5 — override de campanha publicado vence no resolve(campaign) ══"
OV=$($CURL -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"campaign\",\"campaign_id\":\"$CAMP\",\"body\":\"override da campanha.\"}" | jq -r '.id')
$CURL -X POST "$RT/$OV/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
assert "resolve(campaign) = override" campaign_override "$($CURL "$RT/resolve?tenant_id=$T&campaign_id=$CAMP" | jq -r '.resolved.source')"
assert "resolve(sem campaign) = default" tenant_default "$($CURL "$RT/resolve?tenant_id=$T" | jq -r '.resolved.source')"

echo "══ CASO 6 — segundo default do tenant → 409 ══"
CODE=$($CURL -o /dev/null -w '%{http_code}' -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"tenant\",\"body\":\"dup\"}")
assert "http (default duplicado)" 409 "$CODE"

echo
[ "$FAIL" = 0 ] && echo "✅ T8-A OK — rubric-template: CRUD + versionamento imutável + resolução default/override" \
                || { echo "❌ T8-A com falhas"; exit 1; }
