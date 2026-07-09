#!/usr/bin/env bash
# Journey J1 — smoke: prova de propagação TRANSITIVA de root_session_id.
#
# O delegate do demo roda o alvo como especialista de CONFERÊNCIA dentro do
# chamador (não cria sessão-filha separada), então não serve para observar
# propagação numa linha `sessions` distinta. O caminho que cria sessão própria
# herdando a raiz é `handle_trigger` com origin_session_id (cenário 2: uma
# sessão dispara um workflow → herda a raiz do chamador).
#
# Encadeamos 3 triggers para provar TRANSITIVIDADE (a diferença entre origin,
# que é 1 salto, e root, que é a raiz transitiva):
#   W1: sem origin            → root = W1
#   W2: origin_session_id=W1  → root = W1        (herda a raiz de W1)
#   W3: origin_session_id=W2  → root = W1  (NÃO W2!)  ← origin=W2, root=W1
#
# Uso (da raiz do repo, demo no ar):  bash infra/test/smoke_journey_root.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
SKILL="skill_portabilidade_demo_v1"
CG_URL="http://localhost:8010"   # channel-gateway publica 8010 no host
CH="$COMPOSE exec -T clickhouse clickhouse-client"

trigger() {  # $1 = origin_session_id (vazio = sem origin) → ecoa o session_id criado
  local origin="$1" body
  if [ -n "$origin" ]; then
    body="{\"tenant_id\":\"$TENANT\",\"origin_session_id\":\"$origin\"}"
  else
    body="{\"tenant_id\":\"$TENANT\"}"
  fi
  curl -s -X POST "$CG_URL/v1/channels/webhook/$SKILL" \
    -H 'content-type: application/json' -d "$body" \
    | sed -n 's/.*"session_id":"\([^"]*\)".*/\1/p'
}

echo "1) Trigger W1 (sem origin) ..."
W1=$(trigger "")
[ -n "$W1" ] || { echo "FALHA: W1 sem session_id"; exit 1; }
echo "   W1=$W1"

echo "2) Trigger W2 (origin=W1) ..."
W2=$(trigger "$W1")
[ -n "$W2" ] || { echo "FALHA: W2 sem session_id"; exit 1; }
echo "   W2=$W2"

echo "3) Trigger W3 (origin=W2) — deve herdar a raiz de W1, não W2 ..."
W3=$(trigger "$W2")
[ -n "$W3" ] || { echo "FALHA: W3 sem session_id"; exit 1; }
echo "   W3=$W3"

echo "4) Aguardando o analytics gravar as 3 linhas ..."
sleep 6

echo "5) Linhas das 3 sessões (esperado: root_session_id == $W1 em TODAS):"
$CH -q "SELECT session_id, origin_session_id, root_session_id, journey_id \
        FROM ${CH_DB}.sessions FINAL \
        WHERE tenant_id='$TENANT' AND session_id IN ('$W1','$W2','$W3') \
        ORDER BY opened_at FORMAT PrettyCompact"

echo
echo "PASS-CHECK (o coração do J1):"
echo "  W1: origin=NULL      root=$W1   (raiz = self)"
echo "  W2: origin=$W1  root=$W1   (herdou)"
echo "  W3: origin=$W2  root=$W1   (TRANSITIVO: origin != root)"
echo "  journey_id == root_session_id em todas."
