#!/usr/bin/env bash
# Gate do relatório Fila/SLA depois da D14-i (uma linha por ESPERA) — 2026-08-24.
#
# Por que existe: até esta data **não havia teste nenhum** cobrindo
# `/reports/pools/queue` — nem em `packages/analytics-api/.../tests/` nem aqui.
# A query foi reescrita (colapso por sessão → linha por passagem) e três
# semânticas de aderência mudaram, tudo sem rede.
#
# Três propriedades, e cada uma diz o que a faria REPROVAR:
#
#   A  Σ waits(API) == nº de segmentos role='queue' no ledger
#      → reprova se a leitura voltar a colapsar (perde linha) ou se o JOIN
#        duplicar (inventa linha). É o par exato do defeito original: 71
#        esperas em 59 sessões, 12 invisíveis.
#      ⚠️ Pode divergir POR PROJETO: o lado `sessions` exclui `outcome='outage'`
#        e origem != 'live'. Divergência ⇒ INCONCLUSIVO com o delta impresso,
#        nunca vermelho automático — quem julga é quem lê o delta.
#
#   B  pool com waits == 0 NÃO pode ter sla_attainment
#      → reprova se voltar a contar contato-que-nunca-esperou como aderente.
#        Sintoma que motivou: `limite_entrega`, 37 contatos, ZERO esperas,
#        aderência 100%. Verde que não podia ficar vermelho.
#
#   C  within_sla <= waits - abandoned, em TODO pool
#      → reprova se espera ABANDONADA voltar a contar como aderente.
#        Sintoma que motivou: `especialista_onboarding`, 2 esperas, as duas
#        abandonadas, aderência 100% — quem desistia mais cedo melhorava o
#        indicador.
#
#   T  TESTEMUNHA DE PRESENÇA: ledger com zero esperas ⇒ INCONCLUSIVO.
#      Sem ela, A/B/C ficam todos verdes num tenant vazio, que é verde por
#      ausência de amostra — o modo de falha que este repositório cataloga.
#
# Uso: infra/test/gate_queue_report_per_wait.sh [tenant] [analytics_url]
set -uo pipefail
# Credencial (2026-08-27, passo 2 do plano `accessible_pools`).
#
# Este gate compara um agregado da API contra um ledger lido DIRETO (Redis/ClickHouse),
# logo so fecha sob um principal que enxergue o TENANT INTEIRO. Medido: o tenant tem 36
# pools e o `admin` alcanca 22 — com token de admin o gate caia de 80 para 71 esperas
# (falta `formfill_demo_ia`) e saia INCONCLUSIVO. Ate o passo 2 ele dependia do caminho
# SEM HEADER, e era por isso que endurecer o demo estava bloqueado aqui.
#
# Criar um usuario com `accessible_pools: []` teria resolvido e seria retrabalho por
# construcao: o passo 3 inverte `[]` para "nenhum pool". O principal usado abaixo declara
# `unrestricted: true` COM lista vazia — e o unico arranjo que sobrevive ao passo 3, ja
# que o ramo restritivo vence a lista nao-vazia.
PLUGHUB_TEST_EMAIL="${PLUGHUB_TEST_EMAIL:-probe@plughub.local}"
PLUGHUB_TEST_PASS="${PLUGHUB_TEST_PASS:-changeme_probe}"
export PLUGHUB_TEST_EMAIL PLUGHUB_TEST_PASS
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim
# Ver `TODO.md` § "endurecer o DEMO" e `_auth.sh`.

TENANT="${1:-tenant_demo}"
API="${2:-http://localhost:3500}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
FROM="2026-01-01T00:00:00"
TO="2026-12-31T00:00:00"

fail=0
inconclusive=0

echo "== gate Fila/SLA per-espera — tenant $TENANT"

# ── T: testemunha de presença, no LEDGER (não na API) ────────────────────────
LEDGER=$($DC exec -T clickhouse clickhouse-client -q "
SELECT count() FROM plughub_demo.segments AS s FINAL
WHERE tenant_id = '$TENANT' AND role = 'queue' FORMAT TSV" < /dev/null | tr -d '[:space:]')

if [ -z "$LEDGER" ]; then
  echo "INCONCLUSIVO — ClickHouse não respondeu (ledger ilegível)"
  exit 2
fi
echo "-- testemunha: $LEDGER segmentos role='queue' no ledger"
if [ "$LEDGER" -eq 0 ]; then
  echo "INCONCLUSIVO — tenant sem espera nenhuma; A/B/C não têm o que julgar"
  exit 2
fi

BODY=$(curl -s "$API/reports/pools/queue?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO")
if [ -z "$BODY" ] || [ "$(echo "$BODY" | jq -r '.error // "-"')" != "-" ]; then
  # `query_pools_queue` engole a exceção e devolve `data: []` + error — que na
  # tela é indistinguível de "não há dado". Aqui é vermelho explícito.
  echo "VERMELHO — a rota falhou: $(echo "$BODY" | jq -r '.error // "resposta vazia"')"
  echo "   diagnóstico: \$DC logs analytics-api --since 5m | grep query_pools_queue"
  exit 1
fi

# ── A: nenhuma espera perdida nem inventada ──────────────────────────────────
API_WAITS=$(echo "$BODY" | jq '[.data.by_pool[].waits] | add // 0')
echo "-- A: Σ waits(API) = $API_WAITS   ledger = $LEDGER"
if [ "$API_WAITS" -ne "$LEDGER" ]; then
  echo "   INCONCLUSIVO — delta $((API_WAITS - LEDGER)). Pode ser exclusão legítima"
  echo "   (sessão outage / origin != live). Conte antes de chamar de defeito:"
  echo "   esperas cuja sessão é outage ou não-live NÃO entram por decisão de projeto."
  inconclusive=1
fi

# ── B: pool sem espera não tem aderência ─────────────────────────────────────
B=$(echo "$BODY" | jq -r '[.data.by_pool[] | select(.waits == 0 and .sla_attainment != null) | .pool_id] | join(",")')
if [ -n "$B" ]; then
  echo "-- B: VERMELHO — pools sem espera reportando aderência: $B"
  fail=1
else
  echo "-- B: ok — nenhum pool sem espera reporta aderência"
fi

# ── C: espera abandonada nunca é aderente ────────────────────────────────────
C=$(echo "$BODY" | jq -r '[.data.by_pool[] | select(.within_sla > (.waits - .abandoned)) | "\(.pool_id)(within=\(.within_sla) waits=\(.waits) aband=\(.abandoned))"] | join(" ")')
if [ -n "$C" ]; then
  echo "-- C: VERMELHO — within_sla conta espera abandonada: $C"
  fail=1
else
  echo "-- C: ok — within_sla nunca excede as esperas não-abandonadas"
fi

# Contexto (não é asserção): a decomposição que explica um `sla_eligible` baixo.
echo "-- contexto: eligible por pool (waits > 0)"
echo "$BODY" | jq -r '.data.by_pool[] | select(.waits > 0) | "     \(.pool_id): waits=\(.waits) eligible=\(.sla_eligible) within=\(.within_sla) sla=\(.sla_attainment)"'
echo "   (waits - eligible = esperas ABERTAS + esperas cuja SESSÃO não tem alvo."
echo "    Medido em 2026-08-24 no retencao_humano: 48 = 5 abertas + 10 sem alvo + 33."
echo "    Os 10 sem alvo são a evidência da D14 (ii): o alvo mora na SESSÃO, e o"
echo "    pool tinha alvo configurado o tempo todo.)"

if [ "$fail" -ne 0 ]; then
  echo "== VERMELHO"
  exit 1
fi
if [ "$inconclusive" -ne 0 ]; then
  echo "== INCONCLUSIVO (ver delta acima)"
  exit 2
fi
echo "== VERDE"
