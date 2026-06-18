#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T5 chunk 5c — teste e2e do contrato de contestação EM LOTE + gate "tratar todas".
#
# Cobre, contra docker-compose.demo.yml:
#   STEP 1  contest em lote (c1+c2) ...................... 200 under_review
#   STEP 2  review parcial (só c1) → GATE ................ 409 pending_contestations
#   STEP 3  review pelo avaliado (operator) → guarda ..... 403 reviewer==evaluated
#   STEP 4  review completo (c1 upheld + c2 revised) ..... 200 finalized
#
# Pré-requisitos: stack demo no ar; `jq`; pelo menos UM evaluation.results já
# existente em tenant_demo (rode uma avaliação antes, ou use evcampaign_8ce).
#
# Efeito colateral: MUTA um result existente (o mais recente com campanha) para
# contestation_open/round 1, dono=operator, e força a campanha a max_rounds=1
# para o STEP 4 finalizar no round 1. Só essa linha é afetada.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
AUTH="${AUTH:-http://localhost:3202}"
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
PG() { $COMPOSE exec -T postgres psql -U plughub -d plughub_demo -tAc "$1"; }

# decode do claim 'sub' de um JWT (sem verificar assinatura)
jwt_sub() {
  local t="$1" p; p="${t#*.}"; p="${p%%.*}"
  case $(( ${#p} % 4 )) in 2) p="$p==";; 3) p="$p=";; esac
  printf '%s' "$p" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r .sub
}

login() { # email password -> access_token
  curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r .access_token
}

req() { # method url token json  -> imprime corpo + HTTP code
  local m="$1" url="$2" tok="$3" body="${4:-}"
  curl -s -w $'\nHTTP %{http_code}\n' -X "$m" "$url" \
    -H "Authorization: Bearer $tok" -H "X-Tenant-ID: $TENANT" \
    -H 'Content-Type: application/json' ${body:+-d "$body"}
}

echo "══ login ══"
OP_TOK=$(login operator@plughub.local changeme_operator)
SV_TOK=$(login supervisor@plughub.local changeme_supervisor)
[ -n "$OP_TOK" ] && [ "$OP_TOK" != null ] || { echo "✗ login operator falhou"; exit 1; }
[ -n "$SV_TOK" ] && [ "$SV_TOK" != null ] || { echo "✗ login supervisor falhou"; exit 1; }
OP_SUB=$(jwt_sub "$OP_TOK"); SV_SUB=$(jwt_sub "$SV_TOK")
echo "  operator   sub=$OP_SUB"
echo "  supervisor sub=$SV_SUB"

echo "══ setup: result em contestation_open round 1, dono=operator, max_rounds=1 ══"
RID=$(PG "SELECT r.id FROM evaluation.results r
            JOIN evaluation.campaigns c ON c.id=r.campaign_id
           WHERE r.tenant_id='$TENANT' ORDER BY r.submitted_at DESC LIMIT 1;")
[ -n "$RID" ] || { echo "✗ nenhum result com campanha em $TENANT — rode uma avaliação antes"; exit 1; }
IID=$(PG "SELECT instance_id FROM evaluation.results WHERE id='$RID';")
CID=$(PG "SELECT campaign_id FROM evaluation.results WHERE id='$RID';")
echo "  result=$RID  instance=$IID  campaign=$CID"
PG "
  UPDATE evaluation.results
     SET contestation_state='contestation_open', result_state='open',
         round=1, current_round=1,
         evaluated_user_id='$OP_SUB', evaluated_agent_type='human_agent',
         finalized_at=NULL, final_score=NULL, finalize_reason=NULL,
         deadline_at=now()+interval '2 days', updated_at=now()
   WHERE id='$RID';
  DELETE FROM evaluation.contestation_threads WHERE evaluation_instance_id='$IID';
  UPDATE evaluation.campaigns
     SET contestation_policy = jsonb_set(coalesce(contestation_policy,'{}'::jsonb),'{max_rounds}','1')
   WHERE id='$CID';
" >/dev/null
echo "  ✓ setup ok"

echo; echo "══ STEP 1 — operator contesta c1+c2 (lote) → espera HTTP 200 / under_review ══"
req POST "$EVAL/v1/evaluation/instances/$IID/contest" "$OP_TOK" \
  '{"dimension_ids":["c1","c2"],"reasons":{"c1":"nota de clareza injusta","c2":"a resolução foi atingida"}}'

echo; echo "══ STEP 2 — GATE: supervisor revisa SÓ c1 → espera HTTP 409 pending_contestations (missing c2) ══"
req POST "$EVAL/v1/evaluation/instances/$IID/review" "$SV_TOK" \
  '{"dimension_decisions":[{"dimension_id":"c1","decision":"upheld","justification":"mantida, evidência confirma"}]}'

echo; echo "══ STEP 3 — guarda revisor≠avaliado: operator tenta revisar → espera HTTP 403 ══"
req POST "$EVAL/v1/evaluation/instances/$IID/review" "$OP_TOK" \
  '{"dimension_decisions":[{"dimension_id":"c1","decision":"upheld","justification":"nao deveria poder revisar"}]}'

echo; echo "══ STEP 4 — supervisor revisa c1(upheld)+c2(revised+override) → espera HTTP 200 / finalized ══"
req POST "$EVAL/v1/evaluation/instances/$IID/review" "$SV_TOK" \
  '{"dimension_decisions":[
      {"dimension_id":"c1","decision":"upheld","justification":"mantida conforme evidência"},
      {"dimension_id":"c2","decision":"revised","score_override":9,"justification":"procede; ajustando a nota"}
    ]}'

echo; echo "══ estado final ══"
echo "result:"; PG "SELECT result_state, contestation_state, round, finalize_reason, final_score
                      FROM evaluation.results WHERE id='$RID';"
echo "threads (round | critério | autor | decisão):"
PG "SELECT round||' | '||dimension_id||' | '||author_type||' | '||coalesce(decision,'-')
      FROM evaluation.contestation_threads
     WHERE evaluation_instance_id='$IID' ORDER BY round, dimension_id, author_type;"
