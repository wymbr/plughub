#!/usr/bin/env bash
# Camada E2 (wrap-up-α) sub-fatia 2 — tool `segment_outcome_record`.
#
# Valida que o wrap-up destacado GRAVA o outcome no segmento da ORIGEM por
# referência: o workflow (on_resume) chama `segment_outcome_record`, que acumula no
# seg_signal e re-publica `participant_left` (linha COMPLETA) → analytics.segments.
#
# Dois passos (a coleta do form é interativa no Console):
#   seed  — semeia os campos ESTÁTICOS do segmento (o que o hook on_human_end faria)
#           e dispara o workflow de wrap-up p/ (ORIGIN, SEG). Depois você reivindica e
#           submete no Console.
#   check — confere o seg_signal (outcome persistido) + a linha em analytics.segments
#           (outcome presente E os estáticos preservados — prova de não-corrupção do
#           ReplacingMergeTree).
#
# Uso (raiz do repo, demo no ar):
#   bash infra/test/smoke_segment_outcome.sh seed
#   ... (no Console: reivindique o item, escolha "Resolvido", submeta) ...
#   bash infra/test/smoke_segment_outcome.sh check
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
CG="http://localhost:8010"
CH="$COMPOSE exec -T clickhouse clickhouse-client"
R() { $COMPOSE exec -T redis redis-cli "$@"; }

# IDs FIXOS p/ os dois passos casarem. ORIGIN = sessão real (dá briefing com transcrição).
ORIGIN="506c0d78-a099-4499-bf89-335265848cb5"
SEG="seg_e2e_wrapup"
# Instância do humano "atendente" (o operador do teste) — para os estáticos do segmento.
INST="human-9573dcd0-8ea8-43d0-92b5-0a29391710d0"
OP_USER="9573dcd0-8ea8-43d0-92b5-0a29391710d0"
KEY="session:${ORIGIN}:seg_signal:${SEG}"

case "${1:-}" in
  seed)
    echo "1) Semeando os campos ESTÁTICOS do segmento (mimetiza _seed_segment_signal do hook) ..."
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
    R HSET "$KEY" \
      segment_id     "$SEG" \
      instance_id    "$INST" \
      pool_id        "retencao_humano" \
      agent_type_id  "human_agent" \
      user_login     "operator@plughub.local" \
      joined_at      "$NOW" \
      duration_ms    "125000" \
      sequence_index "0" \
      tenant_id      "$TENANT" >/dev/null
    R EXPIRE "$KEY" 604800 >/dev/null
    echo "   seg_signal semeado: $KEY"

    echo "2) Disparando o workflow de wrap-up p/ (ORIGIN=$ORIGIN, SEG=$SEG), direcionado ao operador ..."
    curl -s -X POST "$CG/v1/channels/webhook/pool/wrapup_detached_ia" \
      -H 'content-type: application/json' \
      -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.surveyed_agent_key\":\"$OP_USER\",\"session.origin_session_id\":\"$ORIGIN\",\"session.surveyed_segment_id\":\"$SEG\"}}"
    echo
    echo
    echo "AGORA no Console (como Demo Operator):"
    echo "  • o item aparece com 'RESERVED TO YOU' → reivindique"
    echo "  • escolha uma classificação (ex.: Resolvido), preencha Resumo, SUBMETA"
    echo "Depois rode:  bash infra/test/smoke_segment_outcome.sh check"
    ;;

  check)
    echo "1) seg_signal após o submit (esperado: outcome + issue_status preenchidos):"
    R HGETALL "$KEY"
    echo
    echo "2) last_outcome da sessão de origem:"
    R GET "session:${ORIGIN}:last_outcome"
    echo
    echo "3) Linha em analytics.segments (esperado: outcome preenchido E pool_id preservado):"
    sleep 3
    $CH -q "SELECT segment_id, pool_id, outcome, issue_status, handoff_reason \
            FROM ${CH_DB}.segments FINAL \
            WHERE tenant_id='$TENANT' AND segment_id='$SEG' FORMAT PrettyCompact"
    echo
    echo "PASS-CHECK:"
    echo "  • seg_signal.outcome não vazio (a tool acumulou)  [passo 1]"
    echo "  • segments: 1 linha com outcome preenchido E pool_id='retencao_humano' (linha COMPLETA"
    echo "    publicada pela tool — como seg_e2e_wrapup não tinha linha prévia, a existência dela já"
    echo "    prova que o participant_left carregou os estáticos, sem zerar colunas no RMT)"
    ;;

  clean)
    R DEL "$KEY" "session:${ORIGIN}:last_outcome" >/dev/null 2>&1 || true
    echo "limpo."
    ;;

  *)
    echo "uso: $0 {seed|check|clean}"; exit 1 ;;
esac
