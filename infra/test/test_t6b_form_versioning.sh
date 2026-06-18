#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T6b — deploy lifecycle do form: deploy_status (draft|published) + snapshot
# imutável por versão + edição de publicado bifurca novo draft (§16.1).
#
# Fluxo: create(draft v1) → publish(published v1) → edit(→draft v2) →
#        publish(published v2) → versions = [v1,v2 imutáveis]; snapshot v1 intacto.
#
# Endpoints de forms são abertos (sem token); só precisam de tenant_id.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 10"
FAIL=0

assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

dims() { # description -> dimensions json com 1 critério
  echo "[{\"dimension_id\":\"d1\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[{\"criterion_id\":\"clareza\",\"label\":\"Clareza\",\"description\":\"$1\",\"type\":\"score\",\"max_score\":10}]}]"
}
get_form() { $CURL "$EVAL/v1/evaluation/forms/$1?tenant_id=$TENANT"; }

echo "══ 1) create → draft v1 ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"t6b_lifecycle\",\"dimensions\":$(dims v1desc)}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ create falhou"; exit 1; }
echo "  form=$F"
J=$(get_form "$F")
assert "deploy_status" draft "$(echo "$J" | jq -r .deploy_status)"
assert "version"       1     "$(echo "$J" | jq -r .version)"

echo "══ 2) publish → published v1 (snapshot) ══"
J=$($CURL -X POST "$EVAL/v1/evaluation/forms/$F/publish?tenant_id=$TENANT" \
  -H 'Content-Type: application/json' -d '{"published_by":"admin"}')
assert "deploy_status" published "$(echo "$J" | jq -r .deploy_status)"
assert "version"       1         "$(echo "$J" | jq -r .version)"
V=$($CURL "$EVAL/v1/evaluation/forms/$F/versions?tenant_id=$TENANT")
assert "versions.count(após publish v1)" 1 "$(echo "$V" | jq -r .count)"

echo "══ 3) edit (PUT) form publicado → bifurca draft v2 ══"
J=$($CURL -X PUT "$EVAL/v1/evaluation/forms/$F?tenant_id=$TENANT" \
  -H 'Content-Type: application/json' -d "{\"dimensions\":$(dims v2desc)}")
assert "deploy_status(após edit)" draft "$(echo "$J" | jq -r .deploy_status)"
assert "version(após edit)"       2     "$(echo "$J" | jq -r .version)"
assert "descrição viva"  v2desc "$(echo "$J" | jq -r '.dimensions[0].criteria[0].description')"

echo "══ 4) publish v2 ══"
J=$($CURL -X POST "$EVAL/v1/evaluation/forms/$F/publish?tenant_id=$TENANT" \
  -H 'Content-Type: application/json' -d '{"published_by":"admin"}')
assert "deploy_status" published "$(echo "$J" | jq -r .deploy_status)"
assert "version"       2         "$(echo "$J" | jq -r .version)"

echo "══ 5) imutabilidade: snapshot v1 intacto; v2 reflete edição ══"
V=$($CURL "$EVAL/v1/evaluation/forms/$F/versions?tenant_id=$TENANT")
assert "versions.count" 2 "$(echo "$V" | jq -r .count)"
S1=$($CURL "$EVAL/v1/evaluation/forms/$F/versions/1?tenant_id=$TENANT")
S2=$($CURL "$EVAL/v1/evaluation/forms/$F/versions/2?tenant_id=$TENANT")
assert "snapshot v1.descrição (imutável)" v1desc "$(echo "$S1" | jq -r '.dimensions[0].criteria[0].description')"
assert "snapshot v2.descrição"            v2desc "$(echo "$S2" | jq -r '.dimensions[0].criteria[0].description')"

echo "══ 6) republish idempotente (v2 não muda) ══"
$CURL -X POST "$EVAL/v1/evaluation/forms/$F/publish?tenant_id=$TENANT" \
  -H 'Content-Type: application/json' -d '{"published_by":"admin"}' >/dev/null
V=$($CURL "$EVAL/v1/evaluation/forms/$F/versions?tenant_id=$TENANT")
assert "versions.count(após republish)" 2 "$(echo "$V" | jq -r .count)"

echo
[ "$FAIL" = 0 ] && echo "✅ T6b OK — deploy lifecycle + snapshots imutáveis" \
                || { echo "❌ T6b com falhas"; exit 1; }
