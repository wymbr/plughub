#!/usr/bin/env bash
# probe_contacts_count_funnel.sh — item 2: QUAL predicado tira 166 contatos?
#
# ── O QUE A RODADA ANTERIOR ENTREGOU ────────────────────────────────────────────
# A hipótese do `pools_client` MORREU como explicação do salto: os pools internos do
# tenant são 2 (`retencao_humano-int`, `wrapup_detached_ia`) e valem **15** sessões na
# janela, não 167. Mas a mesma rodada abriu algo melhor, porque três números caíram
# em cima uns dos outros:
#
#     sessões na janela, cru .................. 300
#     menos os pools internos ................. 285   ← EXATAMENTE a leitura "depois"
#     o que o endpoint responde HOJE .......... 119   ← ≈ a leitura "antes" (118)
#
# Ou seja: as duas leituras do relato não são "dado que mudou". São **duas fotos do
# mesmo dado com um predicado a mais ou a menos** — e o predicado ausente vale ~166
# linhas, não 15. Enquanto ele não tiver NOME, o número da tela não tem dono.
#
# ── COMO ESTE PROBE RESPONDE, SEM ADIVINHAR ────────────────────────────────────
# Um FUNIL: reproduz o `WHERE` do endpoint (`reports_query.py:563-754`) uma condição
# por vez, imprimindo a contagem a cada passo. A queda de ~166 nomeia o culpado
# sozinho — seja ele `origin`, o escopo de contato, ou o ABAC `accessible_pools`.
#
# **O funil se auto-verifica:** o último passo TEM de bater com o que o endpoint
# responde. Se não bater, a reprodução está errada e o probe se declara INCONCLUSIVO
# em vez de acusar o predicado errado. (Foi assim que a rodada anterior se pegou: a
# conta à mão dava 285 e o endpoint 119 — a discrepância É o achado.)
#
# ── PREVISÃO, escrita ANTES de rodar ────────────────────────────────────────────
#   · passo 1 (tenant + janela) ....... 300
#   · passo 4 (− pools internos) ...... 285
#   · passo 5 (+ ABAC accessible_pools) 119  ← previsto: é AQUI que caem os ~166
#   · se a queda for no passo 2 (`origin`), a afirmação "300/300 live" da medição
#     anterior estava errada — ela foi feita SEM limite superior de janela
#   · se nenhum passo cair ~166, o predicado está fora do que reproduzi: INCONCLUSIVO
#
# Read-only.
#
# Uso:  bash infra/test/probe_contacts_count_funnel.sh [tenant] [de] [ate]

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
cnt() { chq "SELECT count() FROM $DB.sessions AS s FINAL WHERE $1" | tr -d '\r'; }

echo "════ preflight ═════════════════════════════════════════════════════════════"
PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

# O JWT é ENTRADA da query (ABAC), então ele entra no relatório — não no rodapé.
TOK=$(curl -s -m 10 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
      -d '{"email":"admin@plughub.local","password":"changeme_admin","tenant_id":"tenant_demo"}' \
      | jq -r '.access_token // empty')
if [ -z "$TOK" ]; then
  echo "⚠️  INCONCLUSIVO: login falhou — sem JWT não há como reproduzir o ABAC."; exit 2
fi
CLAIMS=$(printf '%s' "$TOK" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null)
echo "   principal:"
printf '%s' "$CLAIMS" | jq -c '{sub, role, accessible_pools, supervised_groups, supervised_user_ids}' \
  | sed 's/^/     /'
POOLS=$(printf '%s' "$CLAIMS" | jq -r '(.accessible_pools // [])[]' | sort)
N_POOLS=$(printf '%s\n' "$POOLS" | grep -c .)
echo "   accessible_pools: $N_POOLS  (0 = sem restrição — o predicado ABAC não entra)"
echo

# Pools internos, pela mesma porta do pools_client.
INTERNAL=$(curl -s -m 5 -H "x-tenant-id: $TENANT" "$REGISTRY/v1/pools" \
           | jq -r '.pools[]? | select(.purpose=="internal") | .pool_id' | sort)
N_INT=$(printf '%s\n' "$INTERNAL" | grep -c .)
echo "   pools internos: $N_INT"
echo

echo "════ o FUNIL — uma condição por vez ════════════════════════════════════════"
W1="s.tenant_id='$TENANT'"
C1=$(cnt "$W1")
printf '   1. tenant ............................. %s\n' "$C1"

W2="$W1 AND s.opened_at >= '$FROM' AND s.opened_at < '$TO'"
C2=$(cnt "$W2")
printf '   2. + janela [%s , %s) .. %s\n' "$FROM" "$TO" "$C2"

W3="$W2 AND s.origin IN ('live')"
C3=$(cnt "$W3")
printf '   3. + origin IN (live) ................. %s   (queda: %s)\n' "$C3" "$((C2-C3))"

W4="$W3 AND (s.channel != '' OR s.closed_at IS NULL)"
C4=$(cnt "$W4")
printf '   4. + canal != %s ou aberta ............ %s   (queda: %s)\n' "''" "$C4" "$((C3-C4))"

if [ "${N_INT:-0}" -gt 0 ] 2>/dev/null; then
  IN_SQL=$(printf '%s\n' "$INTERNAL" | sed "s/.*/'&'/" | paste -sd, -)
  W5="$W4 AND s.pool_id NOT IN ($IN_SQL)"
else
  W5="$W4"
fi
C5=$(cnt "$W5")
printf '   5. + pool NÃO interno ................. %s   (queda: %s)\n' "$C5" "$((C4-C5))"

if [ "${N_POOLS:-0}" -gt 0 ] 2>/dev/null; then
  P_SQL=$(printf '%s\n' "$POOLS" | sed "s/.*/'&'/" | paste -sd, -)
  W6="$W5 AND (s.pool_id IN ($P_SQL) OR s.pool_id = '' OR s.session_id IN (
        SELECT session_id FROM $DB.segments FINAL
         WHERE tenant_id='$TENANT' AND pool_id IN ($P_SQL)))"
  C6=$(cnt "$W6")
  printf '   6. + ABAC accessible_pools ............ %s   (queda: %s)\n' "$C6" "$((C5-C6))"
else
  C6=$C5
  printf '   6. + ABAC accessible_pools ............ %s   (sem restrição no JWT)\n' "$C6"
fi
echo

echo "════ o endpoint, para conferir a reprodução ════════════════════════════════"
Q="tenant_id=$TENANT&from_dt=${FROM// /T}&to_dt=${TO// /T}&page_size=1"
EP=$(curl -s -m 20 -H "Authorization: Bearer $TOK" "$ANALYTICS/reports/sessions?$Q")
printf '%s' "$EP" | jq -c '{total: .meta.total, contatos: .meta.total_contacts,
                            internas: .meta.total_internal,
                            pools_conhecidos: .meta.internal_pools_known,
                            janela_aplicada: .meta.window_applied}' | sed 's/^/   /'
EP_N=$(printf '%s' "$EP" | jq -r '.meta.total_contacts // empty')
echo

echo "════ veredicto ═════════════════════════════════════════════════════════════"
echo "   funil (passo 6): $C6   ·   endpoint: ${EP_N:-?}"
if [ -z "${EP_N:-}" ]; then
  echo "   ⇒ INCONCLUSIVO: o endpoint não devolveu total_contacts."; exit 2
fi
if [ "$C6" != "$EP_N" ]; then
  echo "   ⇒ INCONCLUSIVO: a reprodução NÃO bate com o endpoint (diferença"
  echo "     $((C6 - EP_N))). Há predicado fora do funil — nomeá-lo é o trabalho, e"
  echo "     acusar qualquer um dos passos acima agora seria acusar sem prova."; exit 2
fi
echo "   ⇒ reprodução exata. A maior queda do funil é o predicado que separa as duas"
echo "     leituras do relato (118 × 285). Passo a passo acima, com o delta ao lado."
exit 0
