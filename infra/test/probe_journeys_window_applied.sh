#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# probe_journeys_window_applied — dívida herdada da F2 (ADR histórico unificado)
#
# A F2 pôs `meta.window_applied` em `/reports/segments` e registrou que
# `/reports/journeys` "tem a MESMA mentira e segue sem marcador". Este probe MEDE
# a afirmação antes de consertá-la, e mede também uma SEGUNDA, que o CHANGELOG da
# F2 afirma e o código contradiz:
#
#   > "sem a isenção que /reports/sessions e /reports/journeys já tinham"
#
# `_fetch_sessions:543` isenta a janela SÓ para `origin_session_id`; com
# `root_session_id` a janela é aplicada. Se for verdade em execução, o drill de um
# processo mais velho que a janela vem TRUNCADO em silêncio — o mesmo defeito que
# a F2 consertou em `segments`, ainda vivo em `sessions`.
#
# Técnica (a mesma da F2, e pelo mesmo motivo): as journeys do ambiente são
# RECENTES, então a janela default já as inclui e uma contagem com ela passaria
# igual com ou sem isenção. O ÚNICO corte que separa as hipóteses é uma janela que
# EXCLUI o processo.
#
# Este probe NÃO conserta nada — ele nomeia o estado. 3 estados; leitura não
# numérica é INCONCLUSIVO, nunca vermelho.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

TENANT="${TENANT:-tenant_demo}"
API="${API:-http://localhost:3500}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
DB="${DB:-plughub_demo}"
WIN="from_dt=2026-01-01&to_dt=2026-01-02"

OK=0; FAIL=0; INC=0
ok()   { echo "   ✅ $1"; OK=$((OK+1)); }
bad()  { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
huh()  { echo "   ⚠️  INCONCLUSIVO: $1"; INC=$((INC+1)); }
info() { echo "   → $1"; }

echo "══ probe_journeys_window_applied — tenant=$TENANT ══"

echo "0 · preflight — há journey de 2+ sessões?"
ROOT=$($DC exec -T clickhouse clickhouse-client -q "
  SELECT root_session_id FROM ${DB}.sessions FINAL
  WHERE tenant_id='${TENANT}'
  GROUP BY root_session_id
  HAVING uniqExact(session_id) > 1
  ORDER BY max(opened_at) DESC LIMIT 1 FORMAT TSV" 2>/dev/null | tr -d '\r')
if [[ -z "$ROOT" ]]; then
  huh "nenhuma journey de 2+ sessões — nada a medir"
  echo "══ ok=$OK falha=$FAIL inconclusivo=$INC ══"; exit 2
fi
info "raiz = ${ROOT}"

# ⚠️ `has("window_applied")`, NUNCA `.window_applied // "ausente"`: o `//` do jq
# trata `false` igual a null, e `false` é justamente o valor a ler no ramo isento.
_read() {  # $1 = endpoint  $2 = querystring → "<total> <window_applied> <error>"
  curl -s "${API}/reports/$1?tenant_id=${TENANT}&$2" \
    | jq -r '[(.meta.total // -1),
              (.meta | if has("window_applied")
                       then (.window_applied | tostring) else "ausente" end),
              (.error // "null")] | @tsv' 2>/dev/null | tr -d '\r'
}

echo "1 · /reports/journeys — a isenção existe? o cabeçalho a confessa?"
IFS=$'\t' read -r J_A_N J_A_W J_A_E <<< "$(_read journeys "root_session_id=${ROOT}&${WIN}")"
IFS=$'\t' read -r J_B_N J_B_W J_B_E <<< "$(_read journeys "root_session_id=${ROOT}")"
IFS=$'\t' read -r J_C_N J_C_W J_C_E <<< "$(_read journeys "${WIN}")"
info "A root+janela-absurda = ${J_A_N} (window_applied=${J_A_W}, error=${J_A_E})"
info "B root+janela-default = ${J_B_N} (window_applied=${J_B_W})"
info "C TESTEMUNHA sem root, janela absurda = ${J_C_N} (a janela de fato exclui?)"

if ! [[ "$J_A_N" =~ ^-?[0-9]+$ ]] || ! [[ "$J_C_N" =~ ^-?[0-9]+$ ]]; then
  huh "/reports/journeys não devolveu número (API fora? porta 3500?)"
else
  if [ "$J_C_N" -ne 0 ]; then
    huh "a janela de janeiro NÃO esvazia a listagem (C=${J_C_N}) — o discriminador não discrimina"
  else
    ok "TESTEMUNHA: sem root, a janela de janeiro devolve 0"
  fi
  if [ "$J_A_N" -gt 0 ] && [ "$J_A_N" = "$J_B_N" ]; then
    ok "a isenção EXISTE: A=${J_A_N} == B=${J_B_N} apesar da janela"
  else
    bad "A=${J_A_N} ≠ B=${J_B_N} — a isenção não existe como o código sugere"
  fi
  case "$J_A_W" in
    ausente) bad "o cabeçalho NÃO tem window_applied — publica from_dt/to_dt que não filtraram nada (a dívida)";;
    false)   ok "o cabeçalho confessa: window_applied=false no ramo do processo";;
    true)    bad "window_applied=true num ramo que ignorou a janela — pior que ausente: afirma o falso";;
    *)       huh "window_applied=${J_A_W}, valor inesperado";;
  esac
fi

echo "2 · /reports/sessions — a isenção que o CHANGELOG afirma existe mesmo?"
IFS=$'\t' read -r S_A_N S_A_W S_A_E <<< "$(_read sessions "root_session_id=${ROOT}&${WIN}")"
IFS=$'\t' read -r S_B_N S_B_W S_B_E <<< "$(_read sessions "root_session_id=${ROOT}")"
info "A root+janela-absurda = ${S_A_N} (window_applied=${S_A_W}, error=${S_A_E})"
info "B root+janela-default = ${S_B_N} (window_applied=${S_B_W})"

if ! [[ "$S_A_N" =~ ^-?[0-9]+$ ]] || ! [[ "$S_B_N" =~ ^-?[0-9]+$ ]]; then
  huh "/reports/sessions não devolveu número"
elif [ "$S_B_N" -eq 0 ]; then
  huh "nem com a janela default o drill devolve sessão (B=0) — amostra errada, não julga"
elif [ "$S_A_N" = "$S_B_N" ]; then
  ok "a isenção existe também aqui: A=${S_A_N} == B=${S_B_N}"
else
  bad "DEFEITO VIVO: o drill de UM processo é truncado pela janela (A=${S_A_N} × B=${S_B_N})"
  info "→ abrir um processo mais velho que o período devolve menos sessões, sem dizer"
fi
case "$S_A_W" in
  ausente) bad "/reports/sessions: cabeçalho sem window_applied no ramo do processo";;
  false)   ok "/reports/sessions: o cabeçalho confessa (window_applied=false)";;
  true)    bad "/reports/sessions: window_applied=true num ramo isento — afirma o falso";;
  *)       huh "window_applied=${S_A_W}, valor inesperado";;
esac

echo "3 · TESTEMUNHA do marcador — na LISTAGEM ele tem de dizer true"
IFS=$'\t' read -r L_N L_W L_E <<< "$(_read sessions "")"
info "listagem sem root = ${L_N} (window_applied=${L_W})"
if [ "$L_W" = "true" ]; then
  ok "o marcador VARIA: true na listagem, false no drill"
else
  bad "marcador=${L_W} na listagem — um marcador constante não informa nada"
fi
IFS=$'\t' read -r LJ_N LJ_W LJ_E <<< "$(_read journeys "")"
info "journeys sem root = ${LJ_N} (window_applied=${LJ_W})"
if [ "$LJ_W" = "true" ]; then
  ok "idem em /reports/journeys"
else
  bad "journeys: marcador=${LJ_W} na listagem"
fi

echo "══ ok=$OK falha=$FAIL inconclusivo=$INC ══"
[ "$FAIL" -gt 0 ] && exit 1
[ "$INC"  -gt 0 ] && exit 2
exit 0
