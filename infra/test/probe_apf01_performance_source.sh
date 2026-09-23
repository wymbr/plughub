#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_apf01_performance_source — o score de roteamento conta SEGMENTOS, não versões.
# APF-01 (docs/arcos/arc5-segments.md).
#
# A PROPOSIÇÃO
# ────────────
# *O score que o `performance_job` grava em `{t}:agent_perf:*` é o que se calcula
# sobre os segmentos DEDUPLICADOS — uma linha por segmento —, e a MV que contava
# versões não existe mais para ser lida nem recriada.*
#
# POR QUE UM CENSO INDEPENDENTE
# ─────────────────────────────
# Comparar o job com a MESMA query que ele roda provaria só que a query é
# determinística. O censo aqui deduplica por outro caminho: `argMax(…, row_version)`
# por `segment_id` sobre a tabela CRUA, sem `FINAL`. Se o job voltar a contar
# versões (sem `FINAL`, ou de uma MV), os dois divergem.
#
# RAMOS
#   A  `mv_agent_performance_daily` e `v_agent_performance` AUSENTES do ClickHouse.
#   B  para cada agent_type com ≥ MIN_SESSIONS segmentos na janela: o score que o
#      job grava == o score do censo (±1e-4). População: ≥ 1 agent_type, senão
#      INCONCLUSIVO. O job roda DENTRO do container imediatamente antes da leitura.
#   C  nenhum score é calculado para `agent_type = 'system'` (admissão sintética).
#
# Efeito colateral declarado: o gate RODA o `run_performance_sync`, que grava
# `{t}:agent_perf:*` — o mesmo que o laço de 5 min do serviço faz. Sob a bateria de
# mutação, uma rodada mutada grava score mutado; a rodada seguinte o sobrescreve, e o
# que não for reescrito expira pelo TTL (6 h).
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
CT="${CT:-plughub-demo-analytics-api-1}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }

echo "${BLD}probe_apf01_performance_source — o score conta segmentos, não versões${RST}"
echo
command -v docker >/dev/null || inc "docker ausente"
docker exec "$CT" true 2>/dev/null || inc "container $CT não está de pé"
docker exec "$CT" grep -q "APF-01" /app/packages/analytics-api/src/plughub_analytics_api/performance_job.py \
  || inc "a imagem de $CT não tem o código da APF-01 (rebuild + up -d antes)"

OUT="$(docker exec -i "$CT" python - <<'PY'
import os, json, asyncio
import clickhouse_connect, redis.asyncio as aioredis
from plughub_analytics_api import performance_job as pj
from plughub_analytics_api.clickhouse import AnalyticsStore

db = os.environ["PLUGHUB_CLICKHOUSE_DATABASE"]
kw = dict(host=os.environ["PLUGHUB_CLICKHOUSE_HOST"], port=int(os.environ["PLUGHUB_CLICKHOUSE_PORT"]),
          user=os.environ["PLUGHUB_CLICKHOUSE_USER"], password=os.environ["PLUGHUB_CLICKHOUSE_PASSWORD"])
store = AnalyticsStore(database=db, **kw)
c = store.new_client()

objs = [r[0] for r in c.query(
    f"SELECT name FROM system.tables WHERE database='{db}' "
    f"AND name IN ('mv_agent_performance_daily','v_agent_performance')").result_rows]

# CENSO independente: uma linha por segmento via argMax(row_version) na tabela CRUA.
censo = c.query(f"""
SELECT tenant_id, agent_type_id, count() AS n,
       countIf(o = 'resolved' AND cr != 'agent_transfer') / count() AS res,
       countIf(o IN ('escalated','escalated_human','escalated_ai') OR cr = 'agent_transfer') / count() AS esc
FROM (
  SELECT tenant_id, segment_id,
         argMax(agent_type_id, row_version)             AS agent_type_id,
         argMax(agent_type, row_version)                AS at,
         argMax(ifNull(outcome, ''), row_version)       AS o,
         argMax(ifNull(close_reason, ''), row_version)  AS cr,
         argMax(ended_at, row_version)                  AS ea,
         argMax(started_at, row_version)                AS sa,
         argMax(origin, row_version)                    AS og
  FROM {db}.segments GROUP BY tenant_id, segment_id)
WHERE ea IS NOT NULL AND sa >= today() - {pj.LOOKBACK_DAYS} AND og = 'live' AND at != 'system'
GROUP BY tenant_id, agent_type_id HAVING n >= {pj.MIN_SESSIONS}
""").result_rows
esperado = {f"{t}:agent_perf:{a}": pj.compute_performance_score(res, esc) for t, a, n, res, esc in censo}

# As linhas que o JOB produz — a query dele, formatada como ele formata.
q = pj._PERF_QUERY.format(db=db, lookback=pj.LOOKBACK_DAYS, min_sessions=pj.MIN_SESSIONS,
                          is_transfer=pj._IS_TRANSFER_SQL, not_transfer=pj._NOT_TRANSFER_SQL,
                          escalate_family=pj._ESCALATE_FAMILY_SQL)
job_keys = {f"{t}:agent_perf:{a}" for t, a, *_ in c.query(q).result_rows}

# C: os agent_type_id que a admissão sintética usa, e se o JOB produziu score para algum.
sys_ids = {r[0] for r in c.query(
    f"SELECT DISTINCT agent_type_id FROM {db}.segments FINAL WHERE agent_type = 'system' "
    f"AND ended_at IS NOT NULL AND started_at >= today() - {pj.LOOKBACK_DAYS}").result_rows}
sys_no_job = sorted(k for k in job_keys if k.rsplit(":", 1)[1] in sys_ids)

async def roda():
    r = aioredis.from_url(os.environ.get("PLUGHUB_REDIS_URL") or os.environ["REDIS_URL"], decode_responses=True)
    sync = await pj.run_performance_sync(store, r)
    got = {k: (await r.get(k)) for k in esperado}
    return sync, got
sync, got = asyncio.run(roda())
div = {k: (v, got[k]) for k, v in esperado.items() if got[k] is None or abs(float(got[k]) - v) > 1e-4}
print(json.dumps(dict(objs=objs, n=len(esperado), sync=sync, div=div,
                      so_no_job=sorted(job_keys - set(esperado)), so_no_censo=sorted(set(esperado) - job_keys),
                      sys_ids=sorted(sys_ids), sys_no_job=sys_no_job)))
PY
)" || inc "a execução dentro do container falhou: $OUT"

j() { printf '%s' "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['$1']) if not isinstance(d['$1'],(int,str)) else d['$1'])"; }

echo "── A · a MV e a view não existem mais ──────────────────────────────────"
OBJS="$(j objs)"
[ "$OBJS" = "[]" ] && ok "mv_agent_performance_daily e v_agent_performance ausentes" \
                   || bad "ainda existem: $OBJS"

echo ""
echo "── B · o score gravado == censo deduplicado por argMax(row_version) ────"
N=$(j n); [ "$N" -ge 1 ] || inc "nenhum agent_type com ≥ MIN_SESSIONS segmentos na janela — sem população"
DIV="$(j div)"; SJ="$(j so_no_job)"; SC="$(j so_no_censo)"
[ "$SJ" = "[]" ] && [ "$SC" = "[]" ] && ok "o job e o censo produzem o MESMO conjunto de $N chave(s)" \
                                     || bad "conjuntos diferem — só no job: $SJ · só no censo: $SC"
if [ "$DIV" = "{}" ]; then ok "score gravado = censo independente em todas (sync: $(j sync))"
else bad "divergências (esperado, gravado): $DIV"; fi

echo ""
echo "── C · admissão sintética fora do score (lido da query do JOB) ─────────"
SIDS="$(j sys_ids)"
[ "$SIDS" != "[]" ] || inc "nenhum segmento system na janela — o ramo C não teria o que excluir"
[ "$(j sys_no_job)" = "[]" ] && ok "o job não produz score para os ids de admissão $SIDS" \
                             || bad "o job produziu score para admissão sintética: $(j sys_no_job)"

echo ""
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}REPROVOU${RST} — $FAIL defeito(s)"; exit 1; fi
echo "${GRN}${BLD}VERDE${RST} — a MV saiu, e o score conta cada segmento uma vez"
exit 0
