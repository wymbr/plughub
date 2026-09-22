#!/usr/bin/env bash
# probe_navigation_routing_signal.sh — 2026-09-22 (ORQ-14)
#
# PERGUNTA: o sinal de re-roteamento mede o ATENDIMENTO, ou qualquer segmento que apareça
# depois do destino?
#
# POR QUE ISTO PRECISA DE UM GATE AO VIVO
#   As regras do sinal são três EXCLUSÕES (hook/convidado, agente de fila, volta ao
#   orquestrador), e exclusão é o tipo de coisa que some sem ficar vermelha: tirar o
#   `role = 'primary'` do SQL não quebra nada — a rota continua 200, a lista continua com
#   linhas, e a taxa simplesmente sobe. Num parque onde TODO contato tem NPS, ela subiria
#   para perto de 100% e ainda assim pareceria um número.
#
#   Os testes unitários prendem o SQL; este gate mede o EFEITO no dado vivo, que é a
#   metade que o unitário não alcança.
#
# RAMOS
#   A  A rota responde, e o meta DECLARA que o número é proxy (o nome do número é o que
#      ele mede; "errados" faria alguém tratar proxy como veredicto).
#   B  CONTRAPROVA — a mesma contagem SEM a exclusão de hook/fila tem de dar MAIOR. Se
#      der igual, ou a janela não tem hook nenhum (INCONCLUSIVO, e o gate diz isso) ou a
#      exclusão deixou de existir (VERMELHO).
#   C  A base da taxa exclui `sem_destino` — contato que o cliente abandonou antes de ser
#      atendido não é acerto: somá-lo à base faria a folha abandonada parecer a melhor.
#
# EXIT: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

API="${ANALYTICS_URL:-http://localhost:3500}"
TOKEN="${PLUGHUB_ANALYTICS_SERVICE_TOKEN:-changeme_analytics_service_token_demo}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
CH="plughub-demo-clickhouse-1"
DB="${CLICKHOUSE_DB:-plughub_demo}"
DESDE="${JANELA_DESDE:-$(date -u -d '30 days ago' '+%Y-%m-%dT%H:%M:%S')}"

falhou=0
inconcl=0

echo "════════════════════════════════════════════════════════════════════"
echo " navegação — o re-roteamento mede ATENDIMENTO, não qualquer segmento"
echo "════════════════════════════════════════════════════════════════════"

for dep in curl python3 docker; do
  command -v "$dep" >/dev/null 2>&1 || { echo "INCONCLUSIVO: falta '$dep'"; exit 2; }
done

RESP=$(curl -s -m 20 -H "X-Service-Token: $TOKEN" \
  "$API/reports/navigation/routing?tenant_id=$TENANT&from_dt=$DESDE")
if [ -z "$RESP" ]; then
  echo "INCONCLUSIVO: analytics-api não respondeu em $API"
  exit 2
fi

echo ""
echo "── A · a rota responde e DECLARA o que o número é ──────────────────"
LEITURA=$(printf '%s' "$RESP" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception as exc:
    print('ERRO %s' % exc); raise SystemExit
m = d.get('meta') or {}
print('%s|%s|%s|%s|%s' % (m.get('re_roteados', -1), m.get('contatos', -1),
                          m.get('atendidos', -1), m.get('sem_destino', -1),
                          'proxy' in str(m.get('sinal', ''))))
")
case "$LEITURA" in
  ERRO*|"") echo "  ✗ resposta ilegível: ${LEITURA:-vazia} / ${RESP:0:120}"; falhou=1; LEITURA="-1|-1|-1|-1|False" ;;
esac
RE=$(echo "$LEITURA" | cut -d'|' -f1)
CONTATOS=$(echo "$LEITURA" | cut -d'|' -f2)
ATENDIDOS=$(echo "$LEITURA" | cut -d'|' -f3)
SEM_DEST=$(echo "$LEITURA" | cut -d'|' -f4)
DECLARA=$(echo "$LEITURA" | cut -d'|' -f5)

if [ "$DECLARA" = "True" ]; then
  echo "  ✓ meta.sinal diz que é proxy (contatos=$CONTATOS re_roteados=$RE)"
else
  echo "  ✗ meta.sinal não declara o que o número é — proxy sem rótulo vira veredicto"
  falhou=1
fi

if [ "$CONTATOS" = "0" ]; then
  echo "  SEM AMOSTRA: nenhum contato roteado pelo orquestrador na janela"
  exit 2
fi

echo ""
echo "── B · CONTRAPROVA: sem a exclusão, a conta tem de dar MAIOR ───────"
SQL="
WITH nav AS (
    SELECT session_id, argMin(pool_id, emitted_at) AS orq_ref
    FROM ${DB}.agent_business_events
    WHERE tenant_id = '${TENANT}' AND emitted_at >= '$(echo "$DESDE" | tr 'T' ' ')'
      AND position(category, '.navegacao.destino.') > 0
    GROUP BY session_id
),
cadeia AS (
    SELECT session_id, groupArray(pool_id) AS pools_ref
    FROM (SELECT session_id, pool_id, started_at, segment_id
          FROM ${DB}.segments FINAL
          WHERE tenant_id = '${TENANT}' AND session_id IN (SELECT session_id FROM nav)
          ORDER BY started_at, segment_id)
    GROUP BY session_id
)
SELECT countIf(length(arrayFilter(p -> p != atendido_ref, janela_ref)) > 0)
FROM (
  SELECT
    arraySlice(pools_ref, indexOf(pools_ref, orq_ref) + 1) AS depois_ref,
    if(length(depois_ref) > 0, depois_ref[1], '')          AS atendido_ref,
    arraySlice(depois_ref, 2)                              AS janela_ref
  FROM nav LEFT JOIN cadeia USING (session_id)
)"
SEM_EXCLUSAO=$(docker exec "$CH" clickhouse-client -q "$SQL" 2>/dev/null | tr -d '[:space:]')
if [ -z "$SEM_EXCLUSAO" ]; then
  echo "  INCONCLUSIVO: não consegui contar sem a exclusão (ClickHouse fora?)"
  inconcl=1
elif [ "$SEM_EXCLUSAO" -gt "$RE" ]; then
  echo "  ✓ a exclusão faz trabalho: $SEM_EXCLUSAO sem ela × $RE com ela"
elif [ "$SEM_EXCLUSAO" = "$RE" ]; then
  echo "  INCONCLUSIVO: as duas contagens deram $RE — a janela não tem hook/fila depois"
  echo "                do destino, então este ramo não distingue nada hoje"
  inconcl=1
else
  echo "  ✗ a contagem SEM exclusão ($SEM_EXCLUSAO) ficou MENOR que a com ($RE) —"
  echo "    isso não é possível com as mesmas sessões: o sinal mudou de população"
  falhou=1
fi

echo ""
echo "── C · a base da taxa não conta quem nunca foi atendido ────────────"
BASE=$(printf '%s' "$RESP" | python3 -c "
import json,sys
d = json.load(sys.stdin); m = d['meta']
ok = m['atendidos'] == m['contatos'] - m['sem_destino']
linhas = all(l['atendidos'] == l['contatos'] - l['sem_destino'] for l in d['data'])
taxas  = all(l['taxa_re_roteio'] is None for l in d['data'] if l['atendidos'] == 0)
print('%s|%s|%s' % (ok, linhas, taxas))
" 2>/dev/null)
if [ "$BASE" = "True|True|True" ]; then
  echo "  ✓ atendidos = contatos − sem_destino (meta e linhas), e taxa NÃO MEDIDA sem base"
else
  echo "  ✗ base da taxa inconsistente: $BASE (meta|linhas|taxa-nula)"
  falhou=1
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$falhou" -gt 0 ]; then
  echo " VERMELHO — o sinal não mede o que promete"
  exit 1
fi
if [ "$inconcl" -gt 0 ]; then
  echo " INCONCLUSIVO — a amostra viva não permitiu decidir algum ramo"
  exit 2
fi
echo " VERDE — o re-roteamento conta atendimento, e a base exclui quem não foi atendido"
exit 0
