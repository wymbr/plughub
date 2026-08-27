#!/usr/bin/env bash
# ==============================================================================
# probe_ui_credential_coverage.sh — o BROWSER manda credencial onde precisa?
# ==============================================================================
#
# POR QUE ELE EXISTE (2026-08-27)
# -------------------------------
# Ao endurecer o demo (`analytics_open_access` -> false) os SCRIPTS ficaram cobertos
# — `_auth.sh` converteu 18 e o runner os roda. O browser nao: o platform-ui tem
# chamadas com `fetch` CRU para a analytics-api, e `apiFetch` (que anexa o Bearer)
# existe justamente para isso mas nao e usado em todas.
#
# O modo de falha e o pior que existe aqui: **a tela fica vazia**. `res.ok` falso
# vira `catch` e o hook seta lista vazia — indistinguivel de "este cliente nao tem
# historico". Nenhum gate de backend pega isso, porque do lado do backend o 401 esta
# CERTO.
#
# O QUE ELE MEDE
# --------------
# Para cada chamada do platform-ui a um endpoint de analytics, cruza DUAS coisas:
#   (a) a chamada manda credencial?  (`apiFetch` sim · `fetch` cru nao)
#   (b) o endpoint EXIGE credencial? (medido AO VIVO: 401 sem header)
#
#   (a)=nao e (b)=sim  ->  QUEBRADO  (tela vazia sem erro)
#   (a)=nao e (b)=nao  ->  exposto, mas funcionando — divida, nao quebra
#   (a)=sim            ->  ok
#
# A dimensao (b) e medida ao vivo de proposito: ler o codigo do backend diria qual
# dependencia o handler declara, nao o que o deploy responde — e e o segundo que
# derruba a tela.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
AN="${AN:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
UI_SRC="$ROOT/packages/platform-ui/src"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mQUEBRADO\033[0m     %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mEXPOSTO\033[0m      %s\n' "$1"; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }

[ -d "$UI_SRC" ] || { inc "platform-ui/src nao encontrado em $UI_SRC"; exit 2; }

# Preflight: o serviço responde? Sem isto todo endpoint pareceria "nao gateado".
PING="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN/v1/health")"
[ "$PING" = "200" ] || { inc "analytics-api nao respondeu /v1/health (HTTP $PING)"; exit 2; }

printf '\033[1mprobe: o browser manda credencial onde o backend exige?\033[0m\n'
printf '  analytics-api: %s   tenant: %s\n\n' "$AN" "$TENANT"

# ── Testemunha da MEDICAO (b) ────────────────────────────────────────────────
# Precisamos de um endpoint sabidamente GATEADO e de um sabidamente ABERTO. Sem os
# dois, "tudo 200" e indistinguivel de "o probe nao esta medindo nada".
G="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN/reports/sessions?tenant_id=$TENANT&page_size=1")"
A="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN/v1/health")"
printf '  testemunhas: /reports/sessions -> %s (esperado 401)   /v1/health -> %s (esperado 200)\n\n' "$G" "$A"
if [ "$G" != "401" ]; then
  inc "o endpoint de controle NAO exige credencial — ou a flag \`analytics_open_access\`"
  info "esta ligada neste ambiente, e ai o probe nao consegue separar gateado de aberto."
  exit 2
fi
[ "$A" = "200" ] || { inc "o endpoint aberto de controle devolveu $A — medicao nao confiavel"; exit 2; }

# ── Inventario: chamadas do platform-ui a analytics ──────────────────────────
# Cada linha: <arquivo>|<usa_apiFetch>|<path de teste>
# O path de teste e um representante do endpoint; interessa o CODIGO, nao o corpo.
probe_call() {  # $1=arquivo relativo  $2=path de teste  $3=descricao
  local f="$UI_SRC/$1" rel="$1" path="$2" desc="$3" cred code
  if [ ! -f "$f" ]; then
    inc "$rel nao existe — inventario desatualizado"
    return
  fi
  if grep -q "apiFetch(" "$f"; then cred="apiFetch"; else cred="fetch cru"; fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN$path")"
  case "$cred:$code" in
    "fetch cru:401")
      bad "$rel"
      info "chama $desc com fetch CRU e o endpoint exige credencial (401)."
      info "A tela fica VAZIA, sem erro: o catch do hook seta lista vazia, que e"
      info "indistinguivel de 'nao ha dado'. Trocar por apiFetch." ;;
    "fetch cru:"*)
      warn "$rel"
      info "fetch CRU para $desc (HTTP $code — endpoint ainda nao exige credencial)."
      info "Funciona hoje; quebra no dia em que o endpoint for gateado." ;;
    "apiFetch:"*)
      ok "$rel  ($desc -> HTTP $code, com apiFetch)" ;;
  esac
}

printf '\033[1mChamadas do platform-ui\033[0m\n'
probe_call "modules/agent-assist/hooks/useSessionTranscript.ts" \
  "/v1/transcript/sessions/__probe__?tenant_id=$TENANT" "transcricao de sessao"
probe_call "modules/agent-assist/hooks/useCustomerHistory.ts" \
  "/sessions/customer/__probe__?tenant_id=$TENANT&limit=1" "historico do cliente"
probe_call "modules/agent-assist/hooks/useCustomerSearch.ts" \
  "/sessions/customer/__probe__/search?tenant_id=$TENANT&q=x" "busca no historico"
probe_call "api/evaluation-hooks.ts" \
  "/reports/evaluations?tenant_id=$TENANT" "relatorios de avaliacao"

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m — nenhuma chamada do browser cai em endpoint que exige credencial.\n'
else
  printf '\033[31mVERMELHO\033[0m — ha chamada sem credencial contra endpoint que a exige.\n'
fi
exit "$fail"
