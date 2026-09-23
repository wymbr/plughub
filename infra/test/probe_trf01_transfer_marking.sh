#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_trf01_transfer_marking — a transferência se conta pelo TRANSPORTE.
# TRF-01 (docs/arcos/g7-segment-contact-decoupling.md).
#
# A PROPOSIÇÃO
# ────────────
# *Os relatórios de performance contam como transferência EXATAMENTE os segmentos
# com `close_reason = 'agent_transfer'`, e a resolução os exclui — sem perder da
# conta as linhas com `close_reason` NULL.*
#
# POR QUE AO VIVO, tendo unitário
# ───────────────────────────────
# O unitário prende o TEXTO do SQL. A falha que importa aqui é SEMÂNTICA e só o
# ClickHouse a produz: `close_reason` é `Nullable` e está NULL na maioria das
# linhas, e `close_reason != 'agent_transfer'` vale NULL — o `countIf` a descarta
# calado, e o contador de resolvidos encolhe para quem TEM `close_reason`. Nenhum
# mock reproduz o NULL de três valores.
#
# RAMOS (as funções de relatório rodam DENTRO do container, sobre o ClickHouse vivo,
# e são comparadas a um CENSO independente, escrito aqui):
#   A  transferred_count (agregado)  == censo de close_reason = 'agent_transfer'
#   B  resolved_count    (agregado)  == censo de resolvidos NÃO transferidos,
#      INCLUINDO os de close_reason NULL. Controle de população: tem de haver
#      resolvido com close_reason NULL, senão B não distingue o defeito.
#   C  transfer_rate     (diário)    reconstrói o mesmo total que o censo diário
#   D  outcome='transferred' sem close_reason NÃO conta como transferência
#      (as linhas do harness de 2026-08-21) — contado, e só informativo se zero.
#
# ⚠️ Requer `docker` e o container `plughub-demo-analytics-api-1` com a imagem nova.
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

CT="${CT:-plughub-demo-analytics-api-1}"
TENANT="${TENANT:-tenant_demo}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}probe_trf01_transfer_marking — a transferência se conta pelo transporte${RST}"
echo

command -v docker >/dev/null || inc "docker ausente"
docker exec "$CT" true 2>/dev/null || inc "container $CT não está de pé"
docker exec "$CT" grep -q "_IS_TRANSFER_SQL" /app/packages/analytics-api/src/plughub_analytics_api/reports_query.py \
  || inc "a imagem de $CT não tem o código da TRF-01 (rebuild + up -d antes)"

OUT="$(docker exec -i -e TENANT="$TENANT" "$CT" python - <<'PY'
import os, sys, json
import clickhouse_connect
from plughub_analytics_api import reports_query as rq

db = os.environ["PLUGHUB_CLICKHOUSE_DATABASE"]
c = clickhouse_connect.get_client(
    host=os.environ["PLUGHUB_CLICKHOUSE_HOST"], port=int(os.environ["PLUGHUB_CLICKHOUSE_PORT"]),
    username=os.environ["PLUGHUB_CLICKHOUSE_USER"], password=os.environ["PLUGHUB_CLICKHOUSE_PASSWORD"],
    database=db)
t = os.environ["TENANT"]
SINCE, UNTIL = "2000-01-01 00:00:00", "2100-01-01 00:00:00"

def one(q):
    return c.query(q, parameters={"t": t}).result_rows[0]

# CENSO — escrito aqui, independente do reports_query; mesmos filtros de cada leitor.
# (a faixa de `started_at` dos leitores exclui `started_at` nulo; o censo repete isso)
agg_base = (f"FROM {db}.segments FINAL WHERE tenant_id={{t:String}} AND agent_type != 'system' "
            f"AND origin = 'live' AND started_at >= '{SINCE}' AND started_at < '{UNTIL}'")
day_base = (f"FROM {db}.segments FINAL WHERE tenant_id={{t:String}} AND ended_at IS NOT NULL "
            f"AND origin = 'live' AND toDate(started_at) >= toDate('{SINCE[:10]}') "
            f"AND toDate(started_at) <= toDate('{UNTIL[:10]}')")
T_agg, R_agg, R_null = one(f"""SELECT countIf(close_reason = 'agent_transfer'),
    countIf(outcome = 'resolved' AND (close_reason IS NULL OR close_reason != 'agent_transfer')),
    countIf(outcome = 'resolved' AND close_reason IS NULL) {agg_base}""")
T_day, = one(f"SELECT countIf(close_reason = 'agent_transfer') {day_base}")
H, = one(f"SELECT countIf(outcome = 'transferred' AND (close_reason IS NULL OR close_reason != 'agent_transfer')) {agg_base}")

# LEITORES — as funções de verdade
agg = rq._fetch_agent_performance(c, db, t, SINCE, UNTIL, None, None, None)
day = rq._fetch_agent_performance_daily(c, db, t, SINCE[:10], UNTIL[:10], None, None, None)
got_T = sum(int(r["transferred_count"]) for r in agg["data"])
got_R = sum(int(r["resolved_count"])    for r in agg["data"])
got_Td = round(sum(float(r["transfer_rate"]) * int(r["total_sessions"]) for r in day["data"]))
print(json.dumps(dict(T_agg=T_agg, R_agg=R_agg, R_null=R_null, T_day=T_day, H=H,
                      got_T=got_T, got_R=got_R, got_Td=got_Td,
                      n_agg=len(agg["data"]), n_day=len(day["data"]),
                      err=agg.get("error") or day.get("error"))))
PY
)" || inc "a execução dentro do container falhou: $OUT"

j() { printf '%s' "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
ERR="$(j err)"; [ "$ERR" = "None" ] || inc "o leitor devolveu erro ($ERR) — data: [] por falha não é 'não há dado'"
T_AGG=$(j T_agg); R_AGG=$(j R_agg); R_NULL=$(j R_null); T_DAY=$(j T_day); H=$(j H)
GOT_T=$(j got_T); GOT_R=$(j got_R); GOT_TD=$(j got_Td)

[ "$T_AGG" -ge 1 ] || inc "nenhum segmento com close_reason = 'agent_transfer' — sem população não há o que medir"
[ "$R_NULL" -ge 1 ] || inc "nenhum resolvido com close_reason NULL — o ramo B não distinguiria o defeito do NULL"

FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }

echo "── A · transferred_count = censo do transporte ─────────────────────────"
[ "$GOT_T" = "$T_AGG" ] && ok "transferred_count = $GOT_T = censo ($T_AGG)" \
                        || bad "transferred_count = $GOT_T, censo = $T_AGG"

echo ""
echo "── B · resolved_count exclui transferência e NÃO perde o NULL ──────────"
[ "$GOT_R" = "$R_AGG" ] && ok "resolved_count = $GOT_R = censo ($R_AGG, dos quais $R_NULL com close_reason NULL)" \
                        || bad "resolved_count = $GOT_R, censo = $R_AGG ($R_NULL com close_reason NULL — perdeu-os?)"

echo ""
echo "── C · transfer_rate diário reconstrói o censo ─────────────────────────"
[ "$GOT_TD" = "$T_DAY" ] && ok "Σ transfer_rate × total = $GOT_TD = censo diário ($T_DAY)" \
                         || bad "Σ transfer_rate × total = $GOT_TD, censo diário = $T_DAY"

echo ""
echo "── D · outcome='transferred' sem transporte não é transferência ────────"
if [ "$H" -ge 1 ]; then ok "$H linha(s) 'transferred' sem close_reason ficaram FORA de A (A bateu com o censo do transporte)"
else echo "  ${YEL}—${RST} nenhuma linha 'transferred' sem close_reason: D informativo, sem população"; fi

echo ""
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVOU${RST} — $FAIL defeito(s): a transferência não é contada pelo transporte"
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — transferência pelo close_reason, resolução sem ela, e o NULL na conta"
exit 0
