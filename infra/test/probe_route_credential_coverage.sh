#!/usr/bin/env bash
# ==============================================================================
# probe_route_credential_coverage.sh — QUAIS ROTAS EXIGEM CREDENCIAL?
# ==============================================================================
#
# O TERCEIRO EIXO (2026-08-29)
# ----------------------------
# Ja havia dois censos de autorizacao, e nenhum responde esta pergunta:
#
#   · `probe_authz_single_verifier.sh` C1 — conta **quem decodifica JWT**;
#   · `probe_authz_single_verifier.sh` C4 — conta **quem resolve escopo de pool**.
#
# Os dois medem QUEM DECIDE. Uma rota que nao declara dependencia nenhuma nao aparece
# em nenhum dos dois: nao ha decisor para contar. Foi assim que 19 rotas da
# analytics-api — 12 delas `/reports/*`, quatro respondendo **200 anonimo**, uma
# `/reports/customers/{id}/360` — atravessaram os dois censos intactas.
#
# E a regra do CLAUDE.md pela terceira vez: *"um censo desenhado para um eixo nao
# prova nada sobre o eixo vizinho"*. Este eixo e o mais grosseiro dos tres, porque nao
# depende de sutileza de claim nenhuma: a rota simplesmente nao pede nada.
#
# DUAS METADES, e uma so nao basta
# --------------------------------
#   (A) CODIGO  — censo AST (`_route_principal_census.py`): a rota DECLARA principal?
#   (B) DEPLOY  — medicao ao vivo: ela RESPONDE 401 sem credencial?
#
# (A) sozinha pega a regressao no commit; (B) sozinha nao diz onde consertar e some
# quando a stack nao esta de pe. E (A) pode estar certa e (B) errada — um `Depends`
# declarado num router que ninguem inclui nao gateia nada.
#
# ISENCOES SAO NOMEADAS, uma a uma, com o MOTIVO
# ----------------------------------------------
# Nao existe "por enquanto". Rota descoberta que nao esta na lista de isencao
# reprova; isencao nova exige uma linha aqui dizendo por que aquela rota pode ser
# anonima. A lista e curta de proposito — se crescer, e sinal de que alguem esta
# usando-a como estacionamento.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
AN="${AN:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()   { printf "  ${GRN}OK${RST}           %s\n" "$*"; }
bad()  { printf "  ${RED}VERMELHO${RST}     %s\n" "$*"; FAIL=1; }
warn() { printf "  ${YLW}ATENCAO${RST}      %s\n" "$*"; }
inc()  { printf "  ${YLW}INCONCLUSIVO${RST} %s\n" "$*"; FAIL=1; }
info() { printf "               %s\n" "$*"; }

# ── ISENCOES — cada uma com o motivo, nunca uma lista solta ───────────────────
#
#   GET  /v1/health
#     Liveness. O `healthcheck` do compose e o `depends_on: service_healthy` de outros
#     servicos batem nela ANTES de qualquer auth-api existir. Exigir credencial aqui
#     acoplaria o boot da stack ao boot do emissor de token, e o modo de falha seria
#     um deadlock de inicializacao, nao um 401 legivel. Nao devolve dado de tenant:
#     `{status, clickhouse}`.
#
# Formato: "<METODO> <path>".
ISENTAS=(
  "GET /v1/health"
)

printf "${BLD}probe: quais rotas da analytics-api exigem credencial?${RST}\n"
printf "  escopo: packages/analytics-api/src   alvo ao vivo: %s\n\n" "$AN"

# ══════════════════════════════════════════════════════════════════════════════
# A — CODIGO: o censo AST
# ══════════════════════════════════════════════════════════════════════════════
printf "${BLD}A. Codigo — a rota declara principal?${RST}\n"

PY="$(command -v python3 || command -v python)"
CENSO="$HERE/_route_principal_census.py"
if [ -z "$PY" ] || [ ! -f "$CENSO" ]; then
  inc "censo indisponivel (python=$PY, script=$CENSO) — a metade (A) NAO rodou"
  exit 2
fi

SAIDA="$("$PY" "$CENSO")" || { inc "o censo falhou ao executar"; exit 2; }
[ -n "$SAIDA" ] || { inc "o censo nao devolveu rota alguma — instrumento morto"; exit 2; }

N_TOTAL=$(printf '%s\n' "$SAIDA" | wc -l | tr -d ' ')
N_COB=$(printf '%s\n' "$SAIDA" | grep -c '^COBERTA|' || true)

# Controle POSITIVO do instrumento: uma rota sabidamente gateada tem de sair COBERTA.
# Sem isto, um censo que classificasse tudo como DESCOBERTA (ou tudo como COBERTA)
# passaria por medicao.
if printf '%s\n' "$SAIDA" | grep -q '^COBERTA|GET|/reports/sessions|'; then
  ok "controle positivo: /reports/sessions sai COBERTA ($N_COB de $N_TOTAL rotas)"
else
  inc "controle positivo FALHOU: /reports/sessions deveria sair COBERTA."
  info "O censo esta medindo outra coisa — nao confie no resto desta execucao."
  exit 2
fi

# INDECIDIVEL: rota sem principal conhecido mas com outro `Depends`. Nao e acusacao —
# e o pedido para alguem dizer se aquele nome e um portao. Foi a lista fechada que fez
# a v1 deste censo acusar sete rotas gateadas de `/admin/*` e `/dashboard/*`.
IND="$(printf '%s\n' "$SAIDA" | grep '^INDECIDIVEL|' || true)"
if [ -n "$IND" ]; then
  while IFS='|' read -r _ m p loc deps; do
    warn "INDECIDIVEL  $m $p  ($loc)"
    info "sem principal conhecido, mas depende de: $deps"
    info "Se for portao, acrescente o nome a PRINCIPAIS no censo; se nao for, gateie."
  done <<< "$IND"
  FAIL=1
fi

DESC="$(printf '%s\n' "$SAIDA" | grep '^DESCOBERTA|' || true)"
n_desc=0; n_isenta=0
if [ -n "$DESC" ]; then
  while IFS='|' read -r _ m p loc _; do
    chave="$m $p"
    isenta=0
    for e in "${ISENTAS[@]}"; do [ "$e" = "$chave" ] && isenta=1 && break; done
    if [ "$isenta" = 1 ]; then
      n_isenta=$((n_isenta + 1))
      ok "isenta      $chave  (motivo declarado no cabecalho)"
    else
      n_desc=$((n_desc + 1))
      bad "DESCOBERTA  $chave  ($loc)"
      info "nenhum principal na assinatura: qualquer um chama sem credencial."
    fi
  done <<< "$DESC"
fi
[ "$n_desc" -eq 0 ] && ok "nenhuma rota descoberta fora da lista de isencao"

# Isencao que sobra e isencao que envelhece: se a rota sumiu ou foi gateada, a linha
# aqui vira ficcao. Contamos as duas pontas.
if [ "$n_isenta" -ne "${#ISENTAS[@]}" ]; then
  bad "a lista de isencao tem ${#ISENTAS[@]} entrada(s), mas $n_isenta casaram."
  info "Uma isencao que nao casa e uma rota que mudou de nome, sumiu ou foi gateada."
  info "Apague a linha — isencao orfa e a que ninguem releu."
fi

printf "\n"

# ══════════════════════════════════════════════════════════════════════════════
# B — DEPLOY: o que o servico responde sem credencial
# ══════════════════════════════════════════════════════════════════════════════
printf "${BLD}B. Deploy — a rota responde 401 sem credencial?${RST}\n"

PING="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN/v1/health" 2>/dev/null)"
if [ "$PING" != "200" ]; then
  inc "analytics-api nao respondeu /v1/health (HTTP $PING) — a metade (B) NAO rodou."
  info "A metade (A) acima vale; esta e a que precisa da stack de pe."
  printf "\n"
  if [ "$FAIL" -eq 0 ]; then exit 2; else exit "$FAIL"; fi
fi

# Testemunhas: sem as duas, "tudo 401" e indistinguivel de "o servico caiu" e "tudo
# 200" de "a flag de bypass esta ligada".
W_GATE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AN/reports/sessions?tenant_id=$TENANT&page_size=1")"
if [ "$W_GATE" != "401" ]; then
  inc "testemunha /reports/sessions devolveu $W_GATE (esperado 401)."
  info "Ou \`PLUGHUB_ANALYTICS_OPEN_ACCESS\` esta ligada neste ambiente — e ai o probe"
  info "nao consegue separar gateada de aberta —, ou o gate canonico caiu."
  printf "\n"
  exit 2
fi
ok "testemunhas: /reports/sessions -> 401   /v1/health -> 200"

n_vivo_bad=0; n_vivo_ok=0
while IFS='|' read -r estado m p loc _; do
  [ "$m" = "GET" ] || continue                 # POST vivo mutaria estado: fora
  chave="$m $p"
  isenta=0
  for e in "${ISENTAS[@]}"; do [ "$e" = "$chave" ] && isenta=1 && break; done
  if [ "$isenta" = 1 ]; then continue; fi
  # `{param}` -> valor sintetico. Interessa o CODIGO, nunca o corpo.
  url_path="$(printf '%s' "$p" | sed -E 's/\{[^}]+\}/__probe__/g')"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
          "$AN$url_path?tenant_id=$TENANT&pool_id=__probe__" 2>/dev/null)"
  if [ "$code" = "401" ]; then
    n_vivo_ok=$((n_vivo_ok + 1))
  else
    n_vivo_bad=$((n_vivo_bad + 1))
    bad "$chave responde HTTP $code sem credencial (esperado 401) — $loc"
    if [ "$estado" = "COBERTA" ]; then
      info "a assinatura DECLARA principal e o deploy nao recusa: o router foi incluido?"
    fi
  fi
done <<< "$SAIDA"

if [ "$n_vivo_bad" -eq 0 ]; then
  ok "as $n_vivo_ok rotas GET nao-isentas recusam sem credencial"
fi

printf "\n"
if [ "$FAIL" -eq 0 ]; then
  printf "${GRN}VERDE${RST} — toda rota da analytics-api exige credencial, exceto as isentas NOMEADAS.\n"
else
  printf "${RED}VERMELHO${RST} — ha rota servindo dado de tenant sem pedir credencial.\n"
fi
exit "$FAIL"
