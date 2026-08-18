#!/usr/bin/env bash
# probe_contacts_count_internal_pools.sh — item 2: 118 → 285 na MESMA janela.
#
# ── POR QUE A HIPÓTESE DOS TIERS NÃO EXPLICA (leitura de código, 2026-08-18) ────
# O TODO promoveu "os 3 tiers do /reports/sessions degradam em silêncio" a explicação do
# salto. Mas `total_contacts` NÃO sai da query em tiers: sai de uma contagem PRÓPRIA,
# executada ANTES e FORA do try/except (`reports_query.py` §769-778):
#
#     SELECT count(), countIf(<_contact_only_predicate>) FROM sessions FINAL WHERE …
#
# Nenhum tier pode alterá-la. Os tiers mudam colunas (ANI/DNIS, segment_count), não a
# contagem — e desde o arco anterior eles gritam no log.
#
# ── A HIPÓTESE QUE A LEITURA DE CÓDIGO ENTREGA NO LUGAR ─────────────────────────
# `_contact_only_predicate(internal_pools)` devolve a STRING `"1"` quando o conjunto de
# pools internos vem VAZIO (§401-405) — isto é, `countIf(1)` = conta TUDO. E o conjunto
# vem do `pools_client`, que degrada para `frozenset()` quando o agent-registry não
# responde e não há "último bom" em memória (§100-107) — o próprio módulo documenta a
# consequência: *"contagens de contato e TMA sairão INFLADAS"*.
#
# Ou seja: mesma janela, mesmo build, mesmo dado — e DUAS respostas legítimas do endpoint,
# conforme o registry tenha respondido ou não. É exatamente a forma do relato (o dado não
# mudou: a F3 remediu 676 segmentos nas duas pontas).
#
# ── PREVISÃO, escrita ANTES de rodar ────────────────────────────────────────────
# O TETO de inflação é contável e é uma previsão falseável:
#     TETO = sessões da janela em pool INTERNO que passam a regra (1) (canal != '' ou
#            ainda abertas) — nada além disso pode virar contato a mais.
#   · previsto: TETO ≈ 167   (o salto relatado: 285 − 118)
#   · TETO < 167  ⇒ a hipótese NÃO fecha a conta sozinha — falta produtor
#   · TETO ≈ 0    ⇒ hipótese MORTA (não há o que inflar)
#   · CONTATOS_RESOLVIDO ≈ 118  e  CONTATOS_DEGRADADO ≈ 285  ⇒ confirmada nos dois números
#
# Read-only. Não roda e2e.
#
# Uso:  bash infra/test/probe_contacts_count_internal_pools.sh [tenant] [de] [ate]

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
FROM="${2:-2026-08-07 00:00:00}"
TO="${3:-2026-08-14 23:59:59}"
DB="plughub_demo"
REGISTRY="http://localhost:3300"
ANALYTICS="http://localhost:3500"
AUTH="http://localhost:3202"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

echo "════ preflight ═════════════════════════════════════════════════════════════"
PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

TOTAL=$(chq "SELECT count() FROM $DB.sessions FINAL
              WHERE tenant_id='$TENANT' AND opened_at >= '$FROM' AND opened_at <= '$TO'" | tr -d '\r')
echo "   tenant=$TENANT  janela='$FROM' → '$TO'  sessões=$TOTAL"
[ "${TOTAL:-0}" -gt 0 ] 2>/dev/null || { echo "⚠️  INCONCLUSIVO: zero sessões na janela."; exit 2; }

echo
echo "════ 1. o conjunto de pools INTERNOS, pela MESMA porta que o cliente usa ═══"
# `pools_client` chama GET {agent_registry}/v1/pools com x-tenant-id e filtra purpose=internal.
RAW=$(curl -s -m 5 -H "x-tenant-id: $TENANT" "$REGISTRY/v1/pools")
if [ -z "$RAW" ]; then
  echo "   ⚠️  agent-registry NÃO respondeu — e é exatamente este o caminho degradado."
  INTERNAL_LIST=""
else
  INTERNAL_LIST=$(printf '%s' "$RAW" | jq -r '.pools[]? | select(.purpose=="internal") | .pool_id' | sort)
fi
N_INT=$(printf '%s\n' "$INTERNAL_LIST" | grep -c . )
echo "   pools internos resolvidos: $N_INT"
printf '%s\n' "$INTERNAL_LIST" | sed 's/^/     · /'
if [ "${N_INT:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⚠️  conjunto VAZIO ⇒ neste instante o endpoint conta sessão interna como contato."
fi

echo
echo "════ 2. TETO de inflação — sessões internas que a regra (1) deixa passar ═══"
if [ "${N_INT:-0}" -gt 0 ] 2>/dev/null; then
  IN_SQL=$(printf '%s\n' "$INTERNAL_LIST" | sed "s/.*/'&'/" | paste -sd, -)
  echo "── por pool ──"
  chq "
    SELECT s.pool_id AS pool, s.channel AS canal, count() AS n
      FROM $DB.sessions AS s FINAL
     WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$FROM' AND s.opened_at <= '$TO'
       AND s.pool_id IN ($IN_SQL) AND (s.channel != '' OR s.closed_at IS NULL)
     GROUP BY pool, canal ORDER BY n DESC
     FORMAT PrettyCompactMonoBlock"
  TETO=$(chq "
    SELECT count() FROM $DB.sessions AS s FINAL
     WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$FROM' AND s.opened_at <= '$TO'
       AND s.pool_id IN ($IN_SQL) AND (s.channel != '' OR s.closed_at IS NULL)" | tr -d '\r')
  # As DUAS respostas possíveis do MESMO endpoint, calculadas aqui na mão.
  DEGRADADO=$(chq "
    SELECT count() FROM $DB.sessions AS s FINAL
     WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$FROM' AND s.opened_at <= '$TO'
       AND (s.channel != '' OR s.closed_at IS NULL)" | tr -d '\r')
  RESOLVIDO=$((DEGRADADO - TETO))
else
  TETO=0
  DEGRADADO=$(chq "
    SELECT count() FROM $DB.sessions AS s FINAL
     WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$FROM' AND s.opened_at <= '$TO'
       AND (s.channel != '' OR s.closed_at IS NULL)" | tr -d '\r')
  RESOLVIDO=$DEGRADADO
fi
echo
echo "   TETO de inflação (internas na janela) : ${TETO:-?}      (previsto ≈ 167)"
echo "   total_contacts com registry OK        : ${RESOLVIDO:-?}  (relatado: 118)"
echo "   total_contacts com registry DEGRADADO : ${DEGRADADO:-?}  (relatado: 285)"

echo
echo "════ 3. o endpoint, duas vezes — meta.internal_pools_known é o delator ═════"
TOK=$(curl -s -m 10 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
      -d '{"email":"admin@plughub.local","password":"changeme_admin","tenant_id":"tenant_demo"}' \
      | jq -r '.access_token // empty')
if [ -z "$TOK" ]; then
  echo "   ⚠️  login falhou — seção 3 INCONCLUSIVA (as seções 1-2 seguem válidas)."
else
  Q="tenant_id=$TENANT&from_dt=${FROM// /T}&to_dt=${TO// /T}&page_size=1"
  for i in 1 2; do
    curl -s -m 20 -H "Authorization: Bearer $TOK" "$ANALYTICS/reports/sessions?$Q" \
      | jq -c '{leitura: '"$i"', total: .meta.total, contatos: .meta.total_contacts,
                internas: .meta.total_internal, pools_conhecidos: .meta.internal_pools_known}'
    sleep 1
  done
  echo "   pools_conhecidos = 0 ⇒ leitura INFLADA (o número da tela não é do domínio de contato)."
fi

echo
echo "════ 4. o registry gritou? (a degradação é barulhenta POR DESENHO) ════════"
$DC logs --no-log-prefix -t analytics-api 2>/dev/null \
  | grep -Ei 'agent-registry indisponível|internal-pool scope DISABLED|reusando último conjunto' \
  | tail -n 20 | sed 's/^/   /'
echo "   (vazio = o cliente nunca degradou NESTA vida do container — o log morre no restart,"
echo "    então vazio aqui não inocenta a leitura de 14/08.)"

echo
echo "════ veredicto ═════════════════════════════════════════════════════════════"
if [ "${TETO:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⇒ HIPÓTESE MORTA: não há sessão interna na janela para inflar contagem alguma."
  exit 1
fi
DELTA=$((DEGRADADO - RESOLVIDO))
echo "   delta explicável pelo conjunto de pools internos: $DELTA (relatado: 167)"
if [ "$DELTA" -ge 150 ] && [ "$DELTA" -le 185 ]; then
  echo "   ⇒ HIPÓTESE SUSTENTADA: o salto cabe inteiro na degradação do pools_client."
  echo "     Conserto NÃO é no tier: é dar ao endpoint uma forma de RECUSAR responder um"
  echo "     número de contato que ele sabe não saber calcular (hoje ele responde 200 e"
  echo "     publica internal_pools_known=0, que a UI pode ignorar)."
  exit 0
fi
echo "   ⇒ PARCIAL: a degradação explica $DELTA dos 167. O resto tem outro dono."
exit 1
