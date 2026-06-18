#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T6a — modelo do critério enriquecido + normalização-na-leitura (§5.3 / §16.1).
#
# Verifica que a evaluation-api preenche, NA LEITURA, os campos derivados/default
# por critério (sem reescrever o form armazenado):
#   - legado (só label/description/max_score) → type=score, question=description,
#     min_score=0, evidence_required=true, contestable=true
#   - auto_computed → contestable=false, evidence_required=false
#   - text         → contestable=true,  evidence_required=false
#
# Endpoints de forms são abertos (sem token); só precisam de tenant_id.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail   # sem -e: as asserts controlam o exit code via $FAIL

EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 10"

echo "══ aguardando evaluation-api ($EVAL/health) ══"
for i in $(seq 1 30); do
  if $CURL "$EVAL/health" >/dev/null 2>&1; then echo "  ✓ no ar"; break; fi
  [ "$i" = 30 ] && { echo "  ✗ evaluation-api não respondeu em 30s"; exit 1; }
  sleep 1
done

create_form() { # name dimensions_json -> form_id (aborta com a resposta crua se falhar)
  local resp; resp=$($CURL -X POST "$EVAL/v1/evaluation/forms" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"name\":\"$1\",\"dimensions\":$2}")
  local fid; fid=$(echo "$resp" | jq -r '.form_id // .id // empty' 2>/dev/null)
  if [ -z "$fid" ]; then echo "  ✗ create_form falhou. Resposta:" >&2; echo "$resp" >&2; exit 1; fi
  echo "$fid"
}

get_crit() { # form_id criterion_id -> criterion json (normalizado)
  $CURL "$EVAL/v1/evaluation/forms/$1?tenant_id=$TENANT" \
    | jq ".dimensions[0].criteria[] | select(.criterion_id==\"$2\")"
}

assert() { # label expected actual
  if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"
  else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi
}

FAIL=0

echo "══ CASO 1 — critério legado (sem type) → defaults derivados ══"
F1=$(create_form "t6a_legacy" '[
  {"dimension_id":"d1","name":"Atendimento","weight":1,"criteria":[
    {"criterion_id":"clareza","label":"Clareza","description":"O agente foi claro?","max_score":10}
  ]}
]')
echo "  form=$F1"
C=$(get_crit "$F1" clareza)
assert "type"              score    "$(echo "$C" | jq -r .type)"
assert "question"          "O agente foi claro?" "$(echo "$C" | jq -r .question)"
assert "min_score"         0        "$(echo "$C" | jq -r .min_score)"
assert "evidence_required" true     "$(echo "$C" | jq -r .evidence_required)"
assert "contestable"       true     "$(echo "$C" | jq -r .contestable)"

echo "══ CASO 2 — auto_computed → não contestável, sem evidência ══"
F2=$(create_form "t6a_auto" '[
  {"dimension_id":"d1","name":"Métricas","weight":1,"criteria":[
    {"criterion_id":"aht","label":"AHT","type":"auto_computed","computation_source":"session_metric.duration_ms","max_score":1}
  ]}
]')
echo "  form=$F2"
C=$(get_crit "$F2" aht)
assert "type"              auto_computed "$(echo "$C" | jq -r .type)"
assert "evidence_required" false    "$(echo "$C" | jq -r .evidence_required)"
assert "contestable"       false    "$(echo "$C" | jq -r .contestable)"

echo "══ CASO 3 — text → contestável, sem evidência obrigatória ══"
F3=$(create_form "t6a_text" '[
  {"dimension_id":"d1","name":"Qualitativo","weight":1,"criteria":[
    {"criterion_id":"obs","label":"Observação","type":"text"}
  ]}
]')
echo "  form=$F3"
C=$(get_crit "$F3" obs)
assert "type"              text     "$(echo "$C" | jq -r .type)"
assert "evidence_required" false    "$(echo "$C" | jq -r .evidence_required)"
assert "contestable"       true     "$(echo "$C" | jq -r .contestable)"

echo "══ CASO 4 — campos explícitos NÃO são sobrescritos ══"
F4=$(create_form "t6a_explicit" '[
  {"dimension_id":"d1","name":"Atendimento","weight":1,"criteria":[
    {"criterion_id":"resol","label":"Resolução","type":"score","max_score":10,
     "scoring_guidance":"0=não resolveu, 10=resolveu","contestable":false,"evidence_required":false}
  ]}
]')
echo "  form=$F4"
C=$(get_crit "$F4" resol)
assert "scoring_guidance"  "0=não resolveu, 10=resolveu" "$(echo "$C" | jq -r .scoring_guidance)"
assert "contestable(override)"       false "$(echo "$C" | jq -r .contestable)"
assert "evidence_required(override)" false "$(echo "$C" | jq -r .evidence_required)"

echo
[ "$FAIL" = 0 ] && echo "✅ T6a OK — normalização-na-leitura derivando corretamente" \
                || { echo "❌ T6a com falhas"; exit 1; }
