#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# probe_segments_journey_window — F2 (ADR histórico unificado, D10)
#
# Afirma que `/reports/segments?root_session_id=` devolve os segmentos de TODAS
# as sessões do processo e **isenta a janela de data**.
#
# POR QUE O TESTE É DIFERENCIAL, e não uma contagem esperada:
#   • um total absoluto envelhece a cada corrida de demo/e2e;
#   • e — o modo de falha que importa — como as journeys do ambiente são
#     RECENTES, a janela default de 7 dias já as inclui. Um "voltou 11" com a
#     janela default passaria IGUAL se a isenção não existisse. O único corte
#     que separa as duas hipóteses é uma janela que EXCLUI o processo.
#
# Três medidas sobre a mesma journey, uma janela absurda (2026-01-01→02):
#   A = root_session_id + janela absurda   → a isenção
#   B = root_session_id + janela default   → o mesmo conjunto
#   C = session_id      + janela absurda   → a testemunha (a janela funciona)
#   D = session_id      + janela default   → o escopo de UMA sessão
#
# Asserções: A == B · A > 0 · C == 0 · A > D · window_applied flips.
# `A > D` é o que prova o ESCOPO (o processo é maior que a sessão) — sem ela,
# um endpoint que ignorasse `root_session_id` e caísse no session_id passaria.
#
# Veredicto de 3 estados: sem journey de 2+ sessões no ambiente → INCONCLUSIVO,
# nunca verde (o probe não pode afirmar sobre amostra que não existe).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

TENANT="${TENANT:-tenant_demo}"
API="${API:-http://localhost:3500}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
DB="${DB:-plughub_demo}"

OK=0; FAIL=0
ok()   { echo "   ✅ $1"; OK=$((OK+1)); }
bad()  { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
info() { echo "   → $1"; }

echo "══ probe_segments_journey_window — tenant=$TENANT ══"

# ── 0) preflight: o leitor lê? ────────────────────────────────────────────────
echo "0 · preflight — o endpoint responde e o substrato tem segmento?"
TOTAL_SEGS=$($DC exec -T clickhouse clickhouse-client -q "
  SELECT count() FROM ${DB}.segments FINAL
  WHERE tenant_id='${TENANT}' FORMAT TSV" 2>/dev/null | tr -d '\r')
if [[ -z "$TOTAL_SEGS" || "$TOTAL_SEGS" == "0" ]]; then
  echo "   ⚠️  nenhum segmento em ${DB}.segments — INCONCLUSIVO"
  exit 2
fi
info "${TOTAL_SEGS} segmentos no tenant; o leitor lê"

# ── 1) escolher uma journey de 2+ sessões (a amostra, não um id fixo) ─────────
echo "1 · uma journey com mais de uma sessão"
ROOT=$($DC exec -T clickhouse clickhouse-client -q "
  SELECT root_session_id
  FROM ${DB}.sessions FINAL
  WHERE tenant_id='${TENANT}'
  GROUP BY root_session_id
  HAVING uniqExact(session_id) > 1
  ORDER BY max(opened_at) DESC
  LIMIT 1 FORMAT TSV" 2>/dev/null | tr -d '\r')

if [[ -z "$ROOT" ]]; then
  echo "   ⚠️  nenhuma journey de 2+ sessões no ambiente — INCONCLUSIVO"
  echo "      (rode infra/test/smoke_limite_tres_acessos.sh para criar uma)"
  exit 2
fi
info "raiz = ${ROOT}"

# ── 2) as quatro medidas ──────────────────────────────────────────────────────
echo "2 · quatro leituras sobre a mesma raiz"
WIN="from_dt=2026-01-01&to_dt=2026-01-02"

read_total() {  # $1 = query string extra → imprime "<total> <window_applied> <error>"
  # ⚠️ `has("window_applied")`, NUNCA `.window_applied // "ausente"`: o `//` do jq
  # trata `false` como vazio do mesmo jeito que `null`, e o valor que este probe
  # precisa ler no ramo do processo É `false`. Com o `//`, a asserção reprovava um
  # endpoint correto — o teste acusava o alvo pelo defeito do próprio leitor.
  curl -s "${API}/reports/segments?tenant_id=${TENANT}&$1" \
    | jq -r '[(.meta.total // -1),
              (.meta | if has("window_applied")
                       then (.window_applied | tostring) else "ausente" end),
              (.error // "null")] | @tsv' \
    2>/dev/null | tr -d '\r'
}

IFS=$'\t' read -r A_N A_W A_E <<< "$(read_total "root_session_id=${ROOT}&${WIN}")"
IFS=$'\t' read -r B_N B_W B_E <<< "$(read_total "root_session_id=${ROOT}")"
IFS=$'\t' read -r C_N C_W C_E <<< "$(read_total "session_id=${ROOT}&${WIN}")"
IFS=$'\t' read -r D_N D_W D_E <<< "$(read_total "session_id=${ROOT}")"

info "A root+janela-absurda = ${A_N} (window_applied=${A_W}, error=${A_E})"
info "B root+janela-default = ${B_N} (window_applied=${B_W})"
info "C sess+janela-absurda = ${C_N} (window_applied=${C_W})"
info "D sess+janela-default = ${D_N} (window_applied=${D_W})"

# Campo não-numérico = o curl/jq não leu (API fora, porta errada). É INCONCLUSIVO,
# não vermelho — e precisa ser checado ANTES das comparações, senão `[[ -gt ]]`
# falha por sintaxe e o `||` a converte em ❌, culpando o alvo pela montagem.
# (Os defaults `// -1` no jq existem pelo mesmo motivo: campo vazio somado a
# IFS=tab — que é IFS *whitespace* — faria os campos seguintes escorregarem.)
for N in "$A_N" "$B_N" "$C_N" "$D_N"; do
  if ! [[ "$N" =~ ^-?[0-9]+$ ]]; then
    echo "   ⚠️  leitura não-numérica ('${N}') — INCONCLUSIVO (a API respondeu?)"
    exit 2
  fi
done

# `error` não-nulo é INCONCLUSIVO, não vermelho: `data_unavailable` devolve
# data:[] e total:0, indistinguível de "não há segmentos" para quem só conta.
for E in "$A_E" "$B_E" "$C_E" "$D_E"; do
  if [[ "$E" != "null" ]]; then
    echo "   ⚠️  endpoint devolveu error='${E}' — INCONCLUSIVO (a query estourou)"
    exit 2
  fi
done

# ── 3) asserções ──────────────────────────────────────────────────────────────
echo "3 · a isenção de janela (D10)"
[[ "$A_N" -gt 0 ]] \
  && ok "A > 0 — o processo devolve segmentos apesar da janela de janeiro" \
  || bad "A = ${A_N} — a isenção não está em vigor (ou a raiz não tem segmento)"

[[ "$A_N" -eq "$B_N" ]] \
  && ok "A == B (${A_N}) — a janela é irrelevante quando há root_session_id" \
  || bad "A=${A_N} != B=${B_N} — a janela ainda recorta o processo"

[[ "$C_N" -eq 0 ]] \
  && ok "C == 0 — TESTEMUNHA: a janela de janeiro de fato exclui" \
  || bad "C = ${C_N} — a janela não filtra; A>0 não prova nada"

echo "4 · o escopo é o PROCESSO, não a sessão"
[[ "$A_N" -gt "$D_N" ]] \
  && ok "A (${A_N}) > D (${D_N}) — a raiz traz segmentos de outras sessões" \
  || bad "A (${A_N}) <= D (${D_N}) — root_session_id não ampliou o escopo"

echo "5 · o cabeçalho não mente sobre a janela"
[[ "$A_W" == "false" ]] \
  && ok "window_applied=false no ramo do processo" \
  || bad "window_applied=${A_W} — meta publica uma janela que não filtrou"
[[ "$D_W" == "true" ]] \
  && ok "window_applied=true no ramo normal" \
  || bad "window_applied=${D_W} no ramo normal"

echo "══ ${OK} ok · ${FAIL} falha(s) ══"
if [[ "$FAIL" -eq 0 ]]; then
  echo "✅ VERDE — /reports/segments responde por processo e isenta a janela."
  exit 0
fi
echo "❌ VERMELHO"
exit 1
