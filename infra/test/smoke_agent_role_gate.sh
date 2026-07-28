#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# smoke — `agent_role` como propósito do agente (Fatia A do resíduo `role`).
#
# O que este teste prova, na ordem em que importa:
#
#   T1  o avaliador legítimo é AUTORIZADO pelo gate fechado (regressão principal:
#       fechar o fail-open não pode matar o avaliador real)
#   T2  o `agent_login` CARIMBA `agent_role` no hash da instância (o produtor que
#       não existia — era esta ausência que deixava o gate sem dado)
#   T3  o resultado NÃO sai com `evaluator_unknown` (identidade do avaliador
#       chegando ao Arc 13)
#   T4  gate NEGATIVO: com o skill declarado `executor`, o contexto NÃO é entregue.
#       (Antes da correção este caso PASSAVA: `if (role && …)` curto-circuitava
#       na string vazia e liberava o ReplayContext com original_content cru.)
#
# T1 e T4 são o MESMO caminho com um único bit diferente (o agent_role), e a prova
# é a assimetria entre eles. Ambos medem "o contexto foi entregue?" — lido da linha
# `evaluation_context_get evidence:`, que só é impressa DEPOIS do gate.
#
# Por que NÃO medir "a avaliação completou": o passo `reason` a jusante chama o
# ai-gateway, e um 502 lá derruba os dois lados igualmente — o teste passaria a
# medir a saúde do LLM e um T4 verde deixaria de significar qualquer coisa. Foi
# exatamente o que aconteceu na 1ª rodada deste smoke (2026-07-28).
#   T5  regressão do PUT: um PUT que NÃO declara `agent_role` preserva o valor do
#       DB. Sem isto, todo boot do RegistrySyncer reverteria evaluator→executor.
#   T6  o valor sobrevive ao restart do orchestrator-bridge (seed-if-absent).
#
# Pré: stack demo no ar; agent-registry + mcp-server-plughub + orchestrator-bridge
#      REBUILDADOS; pool avaliador (avaliacao_ia) e ai-gateway operantes; jq.
#
# Este script MUTA o `agent_role` de skill_avaliacao_v1 durante o T4 e restaura no
# fim (trap EXIT, também em falha/Ctrl-C). Se abortar de forma anômala, restaure:
#   ver a seção "restore manual" no final deste arquivo.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
AREG="${AREG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-changeme_agent_registry_service_token_demo}"
# Gate G-PROBE da evaluation-api: create_form/publish/create_campaign exigem
# Bearer + ABAC `evaluation.formularios:read_write`; o dispatch aceita Bearer rw OU
# X-Service-Token. Um Bearer mintado cobre os quatro. (X-Admin-Token NÃO serve —
# `_require_service` não tem fallback para admin; é o que quebra o test_r6 antigo.)
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
EVALUATOR_POOL="${EVALUATOR_POOL:-avaliacao_ia}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
EVAL_SKILL="${EVAL_SKILL:-skill_avaliacao_v1}"
COMPOSE="docker compose -f docker-compose.demo.yml"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FAIL=0

assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }
uuid() { cat /proc/sys/kernel/random/uuid; }

reg_hdr=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")

db_role() {
  $COMPOSE exec -T postgres psql -U plughub -d plughub_registry -tAc \
    "SELECT agent_role FROM skills WHERE skill_id='$EVAL_SKILL';" 2>/dev/null | tr -d '\r' | head -1
}

# Escreve agent_role via API (nunca SQL direto — config se escreve pelo store oficial).
# PUT exige o corpo completo; pegamos o atual e injetamos só o campo.
set_role() {
  local role="$1" body
  body=$($CURL "$AREG/v1/skills/$EVAL_SKILL" -H "x-tenant-id: $TENANT" \
         | jq -c --arg r "$role" 'del(.id,.tenant_id,.status,.created_by,.created_at,.updated_at,
                                      .deploy_status,.published_at,.flow_model,.unpublished_draft)
                                  | with_entries(select(.value != null))
                                  | .agent_role = $r')
  $CURL -X PUT "$AREG/v1/skills/$EVAL_SKILL" "${reg_hdr[@]}" $JSON -d "$body" >/dev/null
}

ORIGINAL_ROLE="$(db_role)"
restore() {
  [ -n "${ORIGINAL_ROLE:-}" ] || return 0
  local now; now="$(db_role)"
  if [ "$now" != "$ORIGINAL_ROLE" ]; then
    echo "── restaurando $EVAL_SKILL.agent_role=$ORIGINAL_ROLE (estava $now)"
    set_role "$ORIGINAL_ROLE"
    echo "   agora: $(db_role)"
  fi
}
trap restore EXIT INT TERM

echo "══ pré-condições ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; \
  [ "$i" = 30 ] && { echo "  ✗ evaluation-api fora do ar"; exit 1; }; sleep 1; done

# Bearer HS256 assinado com o MESMO jwt_secret que a evaluation-api valida, mintado
# dentro do container (que tem pyjwt). Independe do seed de usuários — o gate confere
# assinatura + module_config. Mesmo padrão de smoke_gprobe_service_auth.sh.
TOK=$($COMPOSE exec -T evaluation-api python - "$JWT_SECRET" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, jwt
print(jwt.encode({"sub":"u_smoke_agent_role","tenant_id":"tenant_demo","roles":["operator"],
                  "module_config":{"evaluation":{"formularios":{"access":"read_write","scope":[]}}}},
                 sys.argv[1], algorithm="HS256"))
PY
)
[ -n "$TOK" ] || { echo "  ✗ mint do Bearer falhou (evaluation-api no ar? pyjwt presente?)"; exit 1; }
BH="Authorization: Bearer $TOK"
echo "  evaluation-api no ar · Bearer formularios:rw mintado · $EVAL_SKILL.agent_role=$ORIGINAL_ROLE"
assert_true "skill de avaliação declara agent_role=evaluator" \
  "$([ "$ORIGINAL_ROLE" = "evaluator" ] && echo true || echo false)"
[ "$ORIGINAL_ROLE" = "evaluator" ] || { echo "  → rode o backfill antes (ver CHANGELOG)"; exit 1; }

# ── helpers de cenário ────────────────────────────────────────────────────────
# Semeia form+campanha+instância+ReplayContext e dispara. Ecoa "SID CAMPAIGN".
seed_and_dispatch() {
  local sid="$1" tag="$2" f c inst ctx now e1 e2 form_json raw
  raw=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -H "$BH" -d "{
    \"tenant_id\":\"$TENANT\",\"name\":\"$tag\",\"min_passing_score\":7.0,\"dimensions\":[
      {\"dimension_id\":\"atendimento\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[
        {\"criterion_id\":\"cordialidade\",\"label\":\"Cordialidade\",\"type\":\"score\",
         \"weight\":1,\"max_score\":10,\"na_allowed\":true,
         \"description\":\"O agente foi cordial e resolveu o pedido do cliente.\",
         \"scoring_guidance\":\"Atribua 0-10 com base no transcript.\"}]}]}")
  f=$(printf '%s' "$raw" | jq -r '.form_id // .id // empty' 2>/dev/null)
  # Degradação nunca é silenciosa: sem o corpo da resposta, um 403 do gate ABAC e um
  # 422 de schema viram a mesma linha inútil ("form falhou") — foi o que custou um
  # ciclo de diagnóstico aqui.
  [ -n "$f" ] || { echo "  ✗ form falhou — resposta: $(printf '%s' "$raw" | head -c 400)" >&2; return 1; }
  $CURL -X POST "$EVAL/v1/evaluation/forms/$f/publish?tenant_id=$TENANT" $JSON -H "$BH" \
    -d '{"published_by":"smoke"}' >/dev/null
  form_json=$($CURL "$EVAL/v1/evaluation/forms/$f?tenant_id=$TENANT")

  c=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -H "$BH" -d "{
    \"tenant_id\":\"$TENANT\",\"name\":\"${tag}_camp\",\"form_id\":\"$f\",
    \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
    \"evaluator_pool\":\"$EVALUATOR_POOL\"}" | jq -r '.campaign_id // .id // empty')
  [ -n "$c" ] || { echo "  ✗ campaign falhou" >&2; return 1; }

  inst=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
    -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$c\",\"session_id\":\"$sid\"}" \
    | jq -r '.id // .instance_id // empty')
  [ -n "$inst" ] || { echo "  ✗ instance falhou" >&2; return 1; }

  now=$(date -u +%Y-%m-%dT%H:%M:%SZ); e1=$(uuid); e2=$(uuid)
  ctx=$(jq -nc --argjson form "$form_json" --arg sid "$sid" --arg cid "$c" \
        --arg iid "$inst" --arg now "$now" --arg e1 "$e1" --arg e2 "$e2" '{
    session_id:$sid, tenant_id:"tenant_demo",
    evaluation_form:$form, campaign_id:$cid, instance_id:$iid, comparison_mode:false,
    session_meta:{session_id:$sid, outcome:"resolved", channel:"webchat",
                  agent_type_id:"agente_retencao_ia_v1", started_at:$now,
                  closed_at:$now, duration_ms:120000},
    participants:[{participant_id:"p_agent",role:"primary",agent_type_id:"agente_retencao_ia_v1"},
                  {participant_id:"p_cust",role:"customer"}],
    sentiment:[{score:0.0},{score:0.6}], knowledge_snippets:[],
    events:[
      {event_id:$e1,type:"message",turn_index:0,author_role:"customer",author_id:"p_cust",
       content:"Quero cancelar meu plano.",original_content:"Quero cancelar meu plano."},
      {event_id:$e2,type:"message",turn_index:1,author_role:"agent",author_id:"p_agent",
       content:"Apliquei 20% de desconto por 6 meses. Fica bom assim?",
       original_content:"Apliquei 20% de desconto por 6 meses. Fica bom assim?"}]}')
  printf '%s' "$ctx" | $COMPOSE exec -T redis redis-cli -x SET "tenant_demo:replay:${sid}:context" >/dev/null
  $COMPOSE exec -T redis redis-cli EXPIRE "tenant_demo:replay:${sid}:context" 3600 >/dev/null

  $CURL -X POST "$EVAL/v1/evaluation/campaigns/$c/dispatch?tenant_id=$TENANT" \
    -H "$BH" $JSON -d '{}' >/dev/null
  echo "$c"
}

# O gate AUTORIZOU esta sessão? A linha "evidence" só é impressa DENTRO do handler do
# evaluation_context_get, depois do gate — logo, sua presença prova autorização e sua
# ausência prova que o contexto nunca foi entregue. Sinal determinístico, independente
# do LLM (o passo `reason` a jusante pode falhar por 502 do ai-gateway sem que isso
# diga nada sobre o gate — foi o que confundiu a primeira rodada deste smoke).
gate_authorized() {
  $COMPOSE logs --since 15m mcp-server-plughub 2>/dev/null \
    | grep -q "evaluation_context_get evidence: session=$1" && echo true || echo false
}

# Houve negação por agent_role na janela recente?
gate_denied_recently() {
  $COMPOSE logs --since 5m skill-flow-service 2>/dev/null \
    | grep -q "unauthorized.*agent_role 'evaluator'" && echo true || echo false
}

wait_result() {  # wait_result <session_id> <segundos> → ecoa result_id (ou vazio)
  local sid="$1" secs="$2" rid=""
  for _ in $(seq 1 $((secs / 3))); do
    rid=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$sid&limit=20" \
          | jq -r '(.results // .data // [])[0].id // empty')
    [ -n "$rid" ] && break
    sleep 3
  done
  echo "$rid"
}

# ── T1 · pipeline sobrevive ao gate fechado ───────────────────────────────────
echo
echo "══ T1 · avaliador real AUTORIZADO com gate fechado (regressão principal) ══"
SID_OK="sess-arole-ok-$(date +%s)"
seed_and_dispatch "$SID_OK" "arole_ok" >/dev/null || exit 1
sleep 25   # tempo do dispatch → replayer → skill-flow → evaluation_context_get

# ASSERT DURO: o gate deixou passar. Não depende do LLM.
AUTHORIZED=$(gate_authorized "$SID_OK")
assert_true "gate AUTORIZOU o avaliador (contexto entregue)" "$AUTHORIZED"
[ "$AUTHORIZED" = "true" ] || \
  echo "     $COMPOSE logs --since 10m skill-flow-service | grep THREW | tail -10"

# INFORMATIVO: a avaliação inteira só fecha se o ai-gateway responder. Um 502 em
# /v1/reason derruba isto sem dizer NADA sobre o gate — por isso não é assert.
RID=$(wait_result "$SID_OK" 90)
if [ -n "$RID" ]; then
  echo "  ✓ (bônus) avaliação completa produzida — result=$RID"
else
  echo "  ⓘ avaliação não fechou — esperado se o ai-gateway estiver fora."
  echo "    checar: $COMPOSE logs --since 10m ai-gateway | grep -c '502 Bad Gateway'"
fi

# ── T2 · agent_login carimbou o hash ──────────────────────────────────────────
echo
echo "══ T2 · agent_login carimba agent_role no hash da instância ══"
STAMPED=$($COMPOSE exec -T redis sh -lc '
  n=0
  for k in $(redis-cli --scan --pattern "tenant_demo:agent:instance:*" | grep -v ":conversations$"); do
    [ "$(redis-cli HGET "$k" agent_role)" = "evaluator" ] && n=$((n+1))
  done
  echo $n' 2>/dev/null | tr -d '\r' | tail -1)
assert_true "≥1 instância com agent_role=evaluator no Redis (=${STAMPED:-0})" \
  "$([ "${STAMPED:-0}" -ge 1 ] && echo true || echo false)"
echo "     (antes da Fatia A este contador seria 0 — nenhum produtor escrevia o campo)"

# ── T3 · identidade do avaliador chega ao resultado ───────────────────────────
echo
echo "══ T3 · resultado não sai como evaluator_unknown ══"
if [ -n "$RID" ]; then
  RES=$($CURL "$EVAL/v1/evaluation/results/$RID?tenant_id=$TENANT")
  assert_true "resultado sem 'evaluator_unknown'" \
    "$(echo "$RES" | grep -q 'evaluator_unknown' && echo false || echo true)"
else
  echo "  ⊘ pulado (T1 não produziu resultado)"
fi

# ── T4 · gate NEGATIVO ────────────────────────────────────────────────────────
echo
echo "══ T4 · com agent_role=executor o avaliador é NEGADO ══"
set_role "executor"
sleep 2
NOW_ROLE="$(db_role)"
assert_true "skill temporariamente em executor (=$NOW_ROLE)" \
  "$([ "$NOW_ROLE" = "executor" ] && echo true || echo false)"

SID_DENY="sess-arole-deny-$(date +%s)"
seed_and_dispatch "$SID_DENY" "arole_deny" >/dev/null || true
sleep 25

# Espelho exato do T1 — e é a assimetria entre os dois que carrega a prova:
# mesma máquina, mesmo caminho, só o agent_role muda; um passa, o outro não.
# "Nenhum resultado" sozinho NÃO provaria nada (o 502 do ai-gateway também produz
# zero resultado); o que prova é o contexto NÃO ter sido entregue.
assert_true "gate NEGOU (contexto nunca entregue a um executor)" \
  "$([ "$(gate_authorized "$SID_DENY")" = "false" ] && echo true || echo false)"
assert_true "negação registrada com motivo nomeado nos logs" "$(gate_denied_recently)"

set_role "$ORIGINAL_ROLE"
sleep 2
assert_true "restaurado para evaluator" \
  "$([ "$(db_role)" = "evaluator" ] && echo true || echo false)"

# ── T5 · PUT sem agent_role preserva o DB ─────────────────────────────────────
echo
echo "══ T5 · PUT que NÃO declara agent_role preserva o valor (regressão) ══"
BODY_NO_ROLE=$($CURL "$AREG/v1/skills/$EVAL_SKILL" -H "x-tenant-id: $TENANT" \
  | jq -c 'del(.id,.tenant_id,.status,.created_by,.created_at,.updated_at,
               .deploy_status,.published_at,.flow_model,.unpublished_draft,.agent_role)
           | with_entries(select(.value != null))')
$CURL -X PUT "$AREG/v1/skills/$EVAL_SKILL" "${reg_hdr[@]}" $JSON -d "$BODY_NO_ROLE" >/dev/null
sleep 1
assert_true "agent_role continua evaluator após PUT sem o campo" \
  "$([ "$(db_role)" = "evaluator" ] && echo true || echo false)"
echo "     (sem esta correção o PUT aplicaria o default do Zod e reverteria p/ executor)"

# ── T6 · sobrevive ao restart do bridge (seed-if-absent) ──────────────────────
echo
echo "══ T6 · valor sobrevive ao restart do orchestrator-bridge ══"
$COMPOSE restart orchestrator-bridge >/dev/null 2>&1
sleep 15
assert_true "agent_role continua evaluator após restart do syncer" \
  "$([ "$(db_role)" = "evaluator" ] && echo true || echo false)"

echo
[ "$FAIL" = 0 ] \
  && echo "✅ agent_role gate OK — propósito declarado no registry, carimbado no login, gate decide" \
  || { echo "❌ agent_role gate com falhas"; exit 1; }

# ── restore manual (se o script abortar de forma anômala) ─────────────────────
#   BODY=$(curl -s http://localhost:3300/v1/skills/skill_avaliacao_v1 \
#            -H 'x-tenant-id: tenant_demo' \
#          | jq -c 'del(.id,.tenant_id,.status,.created_by,.created_at,.updated_at,
#                       .deploy_status,.published_at,.flow_model,.unpublished_draft)
#                   | with_entries(select(.value != null)) | .agent_role = "evaluator"')
#   curl -s -X PUT http://localhost:3300/v1/skills/skill_avaliacao_v1 \
#     -H 'x-tenant-id: tenant_demo' \
#     -H 'x-service-token: changeme_agent_registry_service_token_demo' \
#     -H 'Content-Type: application/json' -d "$BODY"
