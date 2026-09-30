#!/usr/bin/env bash
#
# probe_route_anon_sweep.sh — AUT-58 (2026-09-29)
#
# A PERGUNTA
# ==========
# Cada rota de cada serviço Python da plataforma decide sem credencial? E o que ela faz com
# um chamador ANÔNIMO bate com o que está DECLARADO em `route_credential_baseline.tsv`?
#
# POR QUE EXISTE
# ==============
# O censo por rota (`_route_principal_census.py`) cobria 2 dos serviços — analytics-api e,
# desde a SCH-01, scheduler-api. Medido em 2026-09-29, ao estendê-lo: 346 rotas em 16
# serviços, e pela BORDA pública (5174), sem credencial nenhuma, respondiam resultados e
# respostas de pesquisa (evaluation-api), fatura e recursos (pricing-api), e — com um
# `X-Tenant-ID` que qualquer um escreve — mailings, campanhas e formulários (mailing-api,
# dialog-api). O censo estático não bastava: ele não vê dependência de router, middleware,
# nem guard que falha aberto de propósito (`_require_any_evaluation`: "Bearer opcional").
#
# COMO MEDE (sem efeito colateral — ver `_route_anon_sweep.py`)
#   GET com ids/tenant inexistentes; escrita com corpo de tipo inválido (guard de dependência
#   responde 401/403 antes da validação); escrita sem corpo com id inexistente (nada a apagar);
#   escrita sem corpo e sem id: NÃO disparada, dita como tal.
#   Roda DENTRO da rede do compose, a partir do contêiner da analytics-api.
#
# O VEREDITO (`_route_baseline_judge.py`) exige UMA linha por rota e a linha coerente com a
# medição; rota nova sem linha, porta que reabre, guard que falha aberto e dívida que fecha sem
# a tabela acompanhar reprovam. O autoteste injeta uma regressão e uma rota nova e exige as duas.
#
# ⚠️ FORA DA VARREDURA, e dito: serviços TypeScript (agent-registry, skill-flow-service —
# a borda REST do mcp-server tem censo próprio, `probe_mcp_rest_surface.sh`) e o
# usage-aggregator (openapi ilegível). Ficha AUT-70.
#
# Veredicto: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 3
export PYTHONIOENCODING=utf-8

RUNNER="${RUNNER:-plughub-demo-analytics-api-1}"
TABELA=infra/test/route_credential_baseline.tsv
SERVICOS="ai-gateway=http://ai-gateway:3200 auth-api=http://auth-api:3200 calendar-api=http://calendar-api:3700
channel-gateway=http://channel-gateway:8010 config-api=http://config-api:3600 dialog-api=http://dialog-api:3760
evaluation-api=http://evaluation-api:3400 mailing-api=http://mailing-api:3660 pricing-api=http://pricing-api:3900
quality-export=http://quality-export:3852 quality-ingest=http://quality-ingest:3850 rules-engine=http://rules-engine:3201
usage-aggregator=http://usage-aggregator:3950 scheduler-api=http://scheduler-api:3650
analytics-api=http://analytics-api:3500 session-replayer=http://session-replayer:3880"

echo "== probe_route_anon_sweep =="
command -v docker >/dev/null || { echo "⏭️  INCONCLUSIVO: docker ausente"; exit 3; }
docker inspect "$RUNNER" >/dev/null 2>&1 || { echo "⏭️  INCONCLUSIVO: contêiner $RUNNER fora do ar"; exit 3; }
[ -f "$TABELA" ] || { echo "❌ tabela ausente: $TABELA"; exit 1; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
docker cp infra/test/_route_anon_sweep.py "$RUNNER:/tmp/_route_anon_sweep.py" >/dev/null || { echo "⏭️  INCONCLUSIVO: docker cp falhou"; exit 3; }
# shellcheck disable=SC2086
docker exec "$RUNNER" python /tmp/_route_anon_sweep.py $SERVICOS > "$TMP/sweep.json" 2>"$TMP/err" \
  || { echo "⏭️  INCONCLUSIVO: varredura falhou — $(head -c 200 "$TMP/err")"; exit 3; }

SAIDA=$(python3 infra/test/_route_baseline_judge.py "$TMP/sweep.json" "$TABELA" 2>&1)
echo "$SAIDA" | grep -E '^(POSTURAS|DIVIDA) ' | sed 's/^/   /'
if echo "$SAIDA" | grep -q '^ERRO '; then
  echo "$SAIDA" | grep '^ERRO ' | sed 's/^ERRO /  ❌ /'
  echo "❌ REPROVOU — $(echo "$SAIDA" | grep -c '^ERRO ') divergência(s) entre a medição e a tabela"
  exit 1
fi
if ! echo "$SAIDA" | grep -q '^POSTURAS '; then
  echo "⏭️  INCONCLUSIVO: o juiz não produziu veredito — $(echo "$SAIDA" | head -2)"; exit 3
fi
echo "✅ toda rota medida bate com a tabela, e o autoteste reprova regressão e rota nova"
exit 0
