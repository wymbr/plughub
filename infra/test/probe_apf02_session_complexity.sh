#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_apf02_session_complexity — o relatório de complexidade conta SEGMENTOS.
# APF-02 (docs/arcos/arc5-segments.md).
#
# A PROPOSIÇÃO
# ────────────
# *`/reports/sessions/complexity` responde, e cada contagem por sessão é a que se
# calcula sobre os segmentos DEDUPLICADOS — uma linha por segmento —; a MV que
# contava versões não existe mais para ser lida nem recriada.*
#
# POR QUE UM CENSO INDEPENDENTE
# ─────────────────────────────
# Comparar a função com a mesma query que ela roda provaria só que a query é
# determinística. O censo deduplica por outro caminho: `argMax(…, row_version)` por
# `segment_id` sobre a tabela CRUA, sem `FINAL`, e só então agrega por sessão.
#
# RAMOS
#   A  `mv_segment_summary` e `v_segment_summary` AUSENTES do ClickHouse.
#   B  a função REAL (`query_session_complexity`), chamada dentro do container, não
#      devolve `error` — até a APF-02 ela sempre devolvia (code 47, alias `s` ausente).
#   C  para cada tenant com sessões live, o conjunto de sessões e as 9 contagens de cada
#      uma == censo. População: ≥ 1 sessão, senão INCONCLUSIVO.
#   D  as transferências entram em `transferred_count` e NÃO em `resolved_count`
#      (TRF-01). População: ≥ 1 transferência no CENSO, senão INCONCLUSIVO.
#
# As duas populações são checadas ANTES dos veredictos: um INCONCLUSIVO emitido
# depois de um ✗ trocaria o `exit 1` pelo `exit 2` e esconderia o defeito.
#
# Somente leitura. Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
CT="${CT:-plughub-demo-analytics-api-1}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }

echo "${BLD}probe_apf02_session_complexity — a complexidade conta segmentos, não versões${RST}"
echo
command -v docker >/dev/null || inc "docker ausente"
docker exec "$CT" true 2>/dev/null || inc "container $CT não está de pé"
docker exec "$CT" grep -q "APF-02" /app/packages/analytics-api/src/plughub_analytics_api/reports_query.py \
  || inc "a imagem de $CT não tem o código da APF-02 (rebuild + up -d antes)"

OUT="$(docker exec -i "$CT" python - <<'PY'
import os, json, asyncio
from datetime import datetime, timedelta, timezone
from plughub_analytics_api.clickhouse import AnalyticsStore
from plughub_analytics_api.reports_query import query_session_complexity

db = os.environ["PLUGHUB_CLICKHOUSE_DATABASE"]
kw = dict(host=os.environ["PLUGHUB_CLICKHOUSE_HOST"], port=int(os.environ["PLUGHUB_CLICKHOUSE_PORT"]),
          user=os.environ["PLUGHUB_CLICKHOUSE_USER"], password=os.environ["PLUGHUB_CLICKHOUSE_PASSWORD"])
store = AnalyticsStore(database=db, **kw)
c = store.new_client()
FROM, TO = "2020-01-01T00:00:00Z", (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
COLS = ["segment_count", "primary_segments", "specialist_segments", "human_segments",
        "total_duration_ms", "handoff_count", "escalation_count", "resolved_count", "transferred_count"]

objs = [r[0] for r in c.query(
    f"SELECT name FROM system.tables WHERE database='{db}' "
    f"AND name IN ('mv_segment_summary','v_segment_summary')").result_rows]

# CENSO: uma linha por segmento via argMax(row_version) na tabela CRUA, depois por sessão.
censo_rows = c.query(f"""
SELECT tenant_id, session_id, count(),
       countIf(r = 'primary'), countIf(r = 'specialist'), countIf(at = 'human'),
       sum(d), max(toInt64(sq)),
       countIf(o = 'escalated' AND cr != 'agent_transfer'),
       countIf(o = 'resolved'  AND cr != 'agent_transfer'),
       countIf(cr = 'agent_transfer')
FROM (
  SELECT tenant_id, segment_id,
         argMax(session_id, row_version)               AS session_id,
         argMax(role, row_version)                     AS r,
         argMax(agent_type, row_version)               AS at,
         argMax(ifNull(duration_ms, 0), row_version)   AS d,
         argMax(sequence_index, row_version)           AS sq,
         argMax(ifNull(outcome, ''), row_version)      AS o,
         argMax(ifNull(close_reason, ''), row_version) AS cr
  FROM {db}.segments GROUP BY tenant_id, segment_id)
WHERE (tenant_id, session_id) IN (
  SELECT tenant_id, session_id FROM {db}.sessions FINAL WHERE origin = 'live')
GROUP BY tenant_id, session_id
""").result_rows
censo = {(t, s): list(v) for t, s, *v in censo_rows}
tenants = sorted({t for t, _ in censo})

got, errors = {}, {}
async def roda():
    for t in tenants:
        r = await query_session_complexity(store.new_client(), db, t, FROM, TO, page_size=1_000_000)
        if r.get("error"):
            errors[t] = r["error"]; continue
        for row in r["data"]:
            got[(t, row["session_id"])] = [int(row[k]) for k in COLS]
asyncio.run(roda())

div = {f"{t}/{s}": dict(censo=censo.get((t, s)), relatorio=got.get((t, s)))
       for (t, s) in set(censo) | set(got) if censo.get((t, s)) != got.get((t, s))}
# População do ramo D medida no CENSO, nunca no relatório sob teste: um relatório
# quebrado não pode transformar o próprio defeito em "não havia o que medir".
transfers = sum(v[COLS.index("transferred_count")] for v in censo.values())
print(json.dumps(dict(objs=objs, n=len(censo), tenants=len(tenants), errors=errors,
                      div_n=len(div), div=dict(list(div.items())[:5]), transfers=transfers)))
PY
)" || inc "a execução dentro do container falhou: $OUT"

j() { printf '%s' "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); v=d['$1']; print(v if isinstance(v,(int,str)) else json.dumps(v))"; }

# População ANTES de qualquer veredicto: um INCONCLUSIVO depois de um ✗ esconderia o defeito.
N=$(j n); [ "$N" -ge 1 ] || inc "nenhuma sessão live com segmento — sem população"
T=$(j transfers); [ "$T" -ge 1 ] || inc "nenhuma transferência no censo — o ramo D não teria o que medir"

echo "── A · a MV e a view não existem mais ──────────────────────────────────"
[ "$(j objs)" = "[]" ] && ok "mv_segment_summary e v_segment_summary ausentes" \
                        || bad "ainda existem: $(j objs)"

echo ""
echo "── B · a função real responde (sem error) ──────────────────────────────"
[ "$(j errors)" = "{}" ] && ok "query_session_complexity respondeu para os $(j tenants) tenant(s)" \
                         || bad "a função devolveu error: $(j errors)"

echo ""
echo "── C · cada sessão == censo deduplicado por argMax(row_version) ────────"
if [ "$(j div_n)" = 0 ]; then ok "as $N sessões batem com o censo nas 9 contagens"
else bad "$(j div_n) sessão(ões) divergem (5 primeiras): $(j div)"; fi

echo ""
echo "── D · transferência fora do resolved, dentro do transferred (TRF-01) ──"
[ "$(j div_n)" = 0 ] && [ "$(j errors)" = "{}" ] \
  && ok "$T transferência(s) do censo em transferred_count e fora do resolved_count (comparados no ramo C)" \
  || echo "  — sem veredicto próprio: o ramo C já reprovou, e é ele que compara as duas colunas"

echo ""
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}REPROVOU${RST} — $FAIL defeito(s)"; exit 1; fi
echo "${GRN}${BLD}VERDE${RST} — a MV saiu, e o relatório conta cada segmento uma vez"
exit 0
