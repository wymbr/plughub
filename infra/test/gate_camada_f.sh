#!/usr/bin/env bash
# ==============================================================================
# gate_camada_f.sh — as proposicoes da Camada F, re-executaveis
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   F1  o wrap-up e atribuido ao segmento da ORIGEM, nao ao seu proprio
#   F2  sessao INTERNA nao conta como contato — e o filtro alcanca a LISTA,
#       nao so a agregacao
#   F4  (informativo) a expiracao do reivindicado-e-abandonado
#
# POR QUE ELE EXISTE (PUL-02)
# ---------------------------
# A Camada F fechou o arco de detach em 2026-07-30 **validada a mao**, com sondas
# instrumentadas para aquela sessao. A ficha registrou desde entao: *"arco completo
# sem gate versionado e lembranca, nao verificacao"*.
#
# ⚠️ **Este gate NAO re-encena a validacao de julho.** Aquela coreografia media um
# ambiente montado (demo em `dispatch: detached`, `acw_timeout_hours: 0.03`), e
# reproduzi-la seria caro e fragil. O que ele afere sao as proposicoes que
# SOBREVIVEM no dado durvel e que regridem em SILENCIO — que e' onde um gate paga.
#
# ⚠️ **A F2 e' a que mais custa perder, e a razao esta na medicao original:**
# `handle_time_ms` e' **NULL** nas sessoes internas, entao a contaminacao nunca
# apareceria na media — apareceria na CONTAGEM. E media enviesada alguem questiona;
# *volume dobrado parece dia movimentado*.
#
# ⚠️ **F3 (pull direcionado) e D (hook detached) NAO sao duplicados aqui.** Cada um
# ja tem smoke proprio, e ambos passaram a ser executados pelo manifesto na PUL-02
# (`smoke_directed_pull.sh`, `smoke_detached_hook.sh`). Reimplementa-los criaria a
# segunda casa que diverge no primeiro ajuste.
#
# ⚠️ **A lease da F4 esta em outro instrumento:** `probe_claim_lease_invisibility.sh`
# (PUL-01, 2026-09-09), que mede a janela, o alcance das redes e o dano.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
. infra/test/_auth.sh 2>/dev/null || { echo "INCONCLUSIVO: _auth.sh ausente"; exit 2; }

ANALYTICS="${ANALYTICS:-http://localhost:3500}"
CH_C="${CH_C:-plughub-demo-clickhouse-1}"
CH_DB="${CH_DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
JANELA="from_dt=2020-01-01&to_dt=2030-01-01"

FALHOU=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m %s\n' "$1"; FALHOU=1; }
nota() { printf '  \033[33m%s\033[0m %s\n' "$1" "$2"; }

plughub_scope_line 2>/dev/null || true

# ══ F1 ═══════════════════════════════════════════════════════════════════════
printf '\n\033[1mF1. o wrap-up e atribuido ao segmento da ORIGEM\033[0m\n'
printf '    (o campo que prova e `issue_status`: ele recebe a classificacao CRUA do\n'
printf '     formulario e nao tem outro produtor. `outcome=resolved` nao serviria —\n'
printf '     o fechamento do contato tambem o produz, e seria o valor plausivel que\n'
printf '     confirma a hipotese errada)\n'

F1=$(docker exec "$CH_C" clickhouse-client -q "
  SELECT
    countIf(pool_id NOT LIKE '%-int' AND issue_status != '' AND issue_status IS NOT NULL),
    countIf(pool_id LIKE '%-int'     AND issue_status != '' AND issue_status IS NOT NULL),
    countIf(pool_id LIKE '%-int')
  FROM ${CH_DB}.segments FINAL
  WHERE tenant_id = '${TENANT}'
  FORMAT TabSeparated" 2>/dev/null)

if [ -z "$F1" ]; then
  nota "INCONCLUSIVO" "ClickHouse nao respondeu"; exit 2
fi
ORIG=$(printf '%s' "$F1" | cut -f1)
INT=$(printf  '%s' "$F1" | cut -f2)
INTT=$(printf '%s' "$F1" | cut -f3)

# Controle POSITIVO primeiro: sem classificacao em lugar nenhum, o zero do lado
# `-int` seria verde por ausencia — o defeito que esta casa persegue.
if [ "${ORIG:-0}" -eq 0 ] 2>/dev/null; then
  nota "SEM AMOSTRA" "nenhum segmento de origem carrega issue_status — F1 nao mediu nada"
  nota "" "(sem essa testemunha, um zero em -int nao prova atribuicao nenhuma)"
else
  ok "controle positivo: $ORIG segmento(s) de ORIGEM com issue_status"
  if [ "${INT:-0}" -eq 0 ] 2>/dev/null; then
    ok "nenhum dos $INTT segmentos de wrap-up (-int) carrega issue_status: a"
    printf '        classificacao pousa na ORIGEM, que e a proposicao da F1\n'
  else
    bad "$INT segmento(s) de wrap-up carregam issue_status — a atribuicao voltou"
    printf '        para o segmento do proprio wrap-up (G1 volta a inflar)\n'
  fi
fi

# ══ F2 ═══════════════════════════════════════════════════════════════════════
printf '\n\033[1mF2. sessao interna nao conta como contato\033[0m\n'

pegar() { # $1 = scope (vazio = default)
  local u="${ANALYTICS}/reports/sessions?tenant_id=${TENANT}&${JANELA}&limit=100"
  [ -n "$1" ] && u="${u}&scope=$1"
  acurl -s -m 30 "$u" 2>/dev/null
}

RESP_ALL=$(pegar all)
RESP_CON=$(pegar contacts)

ler() { python3 -c '
import json, sys
try: d = json.load(sys.stdin)
except Exception: print("erro erro"); raise SystemExit
linhas = d.get("data") or []
total  = (d.get("meta") or {}).get("total")
print(total if total is not None else "erro", sum(1 for r in linhas if r.get("is_internal")))'; }

read -r TOT_ALL INT_ALL <<EOF
$(printf '%s' "$RESP_ALL" | ler)
EOF
read -r TOT_CON INT_CON <<EOF
$(printf '%s' "$RESP_CON" | ler)
EOF

if [ "$TOT_ALL" = "erro" ] || [ "$TOT_CON" = "erro" ]; then
  nota "INCONCLUSIVO" "a rota /reports/sessions nao respondeu como esperado"
  exit 2
fi
printf '   scope=all      total=%-6s is_internal na pagina=%s\n' "$TOT_ALL" "$INT_ALL"
printf '   scope=contacts total=%-6s is_internal na pagina=%s\n' "$TOT_CON" "$INT_CON"

# (a) controle POSITIVO — tem de existir populacao interna, senao o zero abaixo
#     nao prova filtro nenhum: prova que nao ha o que filtrar.
if [ "${INT_ALL:-0}" -eq 0 ] 2>/dev/null; then
  nota "SEM AMOSTRA" "nenhuma sessao interna na pagina de scope=all — F2 sem testemunha"
else
  ok "controle positivo: $INT_ALL sessao(oes) interna(s) aparecem em scope=all"

  # (b) a assercao sobre a LISTA — o achado da F2 e' que o filtro alcanca as linhas
  if [ "${INT_CON:-0}" -eq 0 ] 2>/dev/null; then
    ok "scope=contacts nao traz nenhuma linha interna (o filtro alcanca a LISTA)"
  else
    bad "$INT_CON linha(s) interna(s) em scope=contacts — a contaminacao e' na CONTAGEM,"
    printf '        e handle_time_ms e NULL nelas: a media nao denuncia, o volume dobra\n'
  fi

  # (c) e sobre o TOTAL, que e' o numero que alguem le no cartao
  if [ "${TOT_ALL:-0}" -gt "${TOT_CON:-0}" ] 2>/dev/null; then
    ok "o total de contatos e MENOR que o de sessoes ($TOT_CON < $TOT_ALL): $(( TOT_ALL - TOT_CON )) fora"
  else
    bad "total(contacts)=$TOT_CON nao e menor que total(all)=$TOT_ALL — o filtro sumiu do agregado"
  fi
fi

# ══ F4 (informativo) ═════════════════════════════════════════════════════════
printf '\n\033[1mF4. expiracao do reivindicado-e-abandonado (informativo)\033[0m\n'
EXP=$(docker exec "$CH_C" clickhouse-client -q "
  SELECT countIf(close_reason = 'acw_expired'), count()
  FROM ${CH_DB}.segments FINAL
  WHERE tenant_id = '${TENANT}' AND pool_id LIKE '%-int'
  FORMAT TabSeparated" 2>/dev/null)
NEXP=$(printf '%s' "$EXP" | cut -f1); NTOT=$(printf '%s' "$EXP" | cut -f2)
if [ "${NEXP:-0}" -eq 0 ] 2>/dev/null; then
  nota "SEM AMOSTRA" "0 de ${NTOT:-0} itens com acw_expired — nada a aferir, e nao e' verde"
  printf '        (a AUSENCIA aqui e o estado saudavel, entao ela nao pode virar\n'
  printf '         assercao: um gate que exigisse expiracao pediria que algo falhe.\n'
  printf '         A lease vive em probe_claim_lease_invisibility.sh — PUL-01\n'
else
  ok "$NEXP item(ns) expirado(s) de $NTOT — o caminho de expiracao tem amostra"
fi

# ══ veredicto ════════════════════════════════════════════════════════════════
printf '\n'
if [ "$FALHOU" != 0 ]; then
  printf '\033[31mVERMELHO\033[0m — uma proposicao da Camada F regrediu.\n'
  exit 1
fi
printf '\033[32mVERDE\033[0m — F1 e F2 de pe. F3 e D vivem nos smokes proprios\n'
printf '(smoke_directed_pull.sh, smoke_detached_hook.sh), que o manifesto executa.\n'
exit 0
