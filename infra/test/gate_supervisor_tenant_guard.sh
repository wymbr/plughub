#!/usr/bin/env bash
# gate_supervisor_tenant_guard.sh — 2026-08-21
#   TODO § "`session:{id}:meta` — partição de propriedade não declarada", defeito 2
#
# PERGUNTA: o `/supervisor/join` do serviço RODANDO recusa quem não pode ter o
# tenant conferido?
#
# Existe separado do unit test porque eles respondem coisas diferentes: o unit
# test julga a FUNÇÃO, este julga o DEPLOY. Um serviço rodando imagem antiga passa
# no primeiro e reprova no segundo — e é exatamente o modo de falha que este
# repositório já pagou ("`build` verde NÃO recria container").
#
# TRÊS pontos, e o primeiro é o que dá poder de reprovar:
#   P0  meta COM tenant_id correto        → 200   ← CONTROLE
#   P1  meta SEM tenant_id                → 403   ← o caminho que o guard antigo
#                                                    deixava passar
#   P2  meta com tenant_id DIVERGENTE     → 403   ← a metade que sempre funcionou
#
# Sem P0 e P2, um guard que recusasse tudo ficaria verde em P1 sem provar nada.
#
# ESCOPO DA ESCRITA: o gate cria DUAS chaves de rascunho (`session:__gate_*__:meta`)
# e apaga tudo o que tocou no `trap`, inclusive o `supervisor:*:active` e o stream
# que um join bem-sucedido cria. Nenhuma sessão real é tocada.
#
# USO:   bash infra/test/gate_supervisor_tenant_guard.sh
# SAÍDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO

set -u

# Credencial (2026-08-27). `/supervisor/join` passou a exigir token no passo T2 —
# sem ele TODO probe volta 401 e o gate lia isso como "aceito", invertendo o
# veredicto. Usa o principal IRRESTRITO de proposito: com escopo irrestrito o
# `_authorize_live_session` retorna cedo e a dimensao POOL sai do caminho, de modo
# que o que este gate mede e SO o tenant. Um gate por proposicao.
PLUGHUB_TEST_EMAIL="${PLUGHUB_TEST_EMAIL:-probe@plughub.local}"
PLUGHUB_TEST_PASS="${PLUGHUB_TEST_PASS:-changeme_probe}"
export PLUGHUB_TEST_EMAIL PLUGHUB_TEST_PASS
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"

TENANT="${TENANT:-tenant_demo}"
OUTRO="${OUTRO:-tenant_outro}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
AN="${AN:-http://localhost:3500}"

DC="docker compose -f $COMPOSE"
CURL="curl -s -o /dev/null -w %{http_code} --max-time 15"
JSON='-H Content-Type:application/json'
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

S_OK="__gate_tenant_ok__"
S_NO="__gate_tenant_missing__"

cleanup() {
  for s in "$S_OK" "$S_NO"; do
    r DEL "session:${s}:meta" "supervisor:${s}:active" "session:${s}:stream" > /dev/null 2>&1
  done
}
trap cleanup EXIT

inconclusivo() { echo; echo "VEREDICTO: INCONCLUSIVO — $1"; exit 2; }

[ "$(r PING)" = "PONG" ] || inconclusivo "redis mudo"

echo "══ gate: /supervisor/join confere o tenant contra o meta? ══"
echo "   analytics-api: $AN   tenant: $TENANT"

# Preflight: o serviço responde? Sem isto, um 000 de conexão recusada seria lido
# como "recusou por tenant" — o veredicto certo pelo motivo errado.
# `/v1/health` — o `/health` sem prefixo devolve 404 nesta API (medido: o único
# `@app.get` de saúde é `/v1/health`, main.py:163). O preflight recusou em vez de
# adivinhar, que é o comportamento desejado: um 000/404 lido como "recusou por
# tenant" daria o veredicto certo pelo motivo errado.
PING_HTTP="$($CURL "$AN/v1/health")"
[ "$PING_HTTP" = "200" ] || inconclusivo "analytics-api não respondeu /v1/health (HTTP $PING_HTTP)"

join() { # session_id  tenant_declarado → código HTTP
  $CURL -X POST "$AN/supervisor/join" $JSON \
    -H "$(plughub_auth_header)" \
    -d "{\"tenant_id\":\"$2\",\"session_id\":\"$1\",\"operator_id\":\"gate\"}"
}

# Testemunha: a MESMA chamada SEM credencial tem de ser 401. Sem ela, o dia em que
# o header parar de ser enviado devolve 401 em tudo e o gate volta a reportar as
# falhas de tenant de antes — veredicto certo pelo motivo errado, outra vez.
noauth_join() {
  $CURL -X POST "$AN/supervisor/join" $JSON \
    -d "{\"tenant_id\":\"$TENANT\",\"session_id\":\"$1\",\"operator_id\":\"gate\"}"
}

FAIL=""

# ── PA — TESTEMUNHA: a camada de autenticacao esta na frente ────────────────
r SET "session:${S_OK}:meta" "{\"tenant_id\":\"$TENANT\",\"channel\":\"webchat\"}" EX 300 > /dev/null
CA="$(noauth_join "$S_OK")"
echo "   PA sem credencial ............................... HTTP $CA   (esperado 401)"
[ "$CA" = "401" ] || FAIL="$FAIL\n   · PA devolveu $CA — a rota aceitou SEM token. Ou o guard do T2 sumiu,"$'\n'"     ou o servico roda imagem antiga; nos dois casos os probes abaixo"$'\n'"     medem outra coisa."

# ── P0 — CONTROLE: meta com o tenant certo ──────────────────────────────────
r SET "session:${S_OK}:meta" "{\"tenant_id\":\"$TENANT\",\"channel\":\"webchat\"}" EX 300 > /dev/null
C0="$(join "$S_OK" "$TENANT")"
echo "   P0 meta COM tenant_id, declarado igual .......... HTTP $C0   (esperado 200)"
[ "$C0" = "200" ] || FAIL="$FAIL\n   · P0 devolveu $C0 — o guard está recusando quem PODE entrar,"$'\n'"     e nesse estado o P1 abaixo ficaria verde sem provar nada"

# ── P1 — o caminho que o guard antigo deixava passar ────────────────────────
r SET "session:${S_NO}:meta" '{"channel":"webchat","contact_id":"c1"}' EX 300 > /dev/null
C1="$(join "$S_NO" "$TENANT")"
echo "   P1 meta SEM tenant_id ........................... HTTP $C1   (esperado 403)"
if [ "$C1" != "403" ]; then
  FAIL="$FAIL\n   · P1 devolveu $C1 — meta sem \`tenant_id\` foi ACEITO. É o fail-open:"$'\n'"     \`meta.get(\"tenant_id\", body.tenant_id) != body.tenant_id\` compara o"$'\n'"     valor com ele mesmo. Serviço rodando imagem antiga também dá isto."
fi

# ── P2 — divergencia de tenant ──────────────────────────────────────────────
# Desde o T2 este probe recusa por um motivo MELHOR do que o original: o tenant vem
# do TOKEN, entao declarar outro no corpo e `tenant_mismatch_token` — a recusa
# acontece ANTES de olhar o meta. O codigo esperado nao mudou (403); a razao, sim.
C2="$(join "$S_OK" "$OUTRO")"
echo "   P2 meta com tenant DIVERGENTE ................... HTTP $C2   (esperado 403)"
[ "$C2" = "403" ] || FAIL="$FAIL\n   · P2 devolveu $C2 — tenant divergente aceito (regressão na metade que já funcionava)"

# ── P3..P5 — os três sites do mcp-server que resolviam tenant da MESMA chave ──
# `supervisor_capabilities` e `copilot_state` LEEM usando o tenant como prefixo
# (config de pool no agent-registry; `{tenant}:ctx:{session}` no ContextStore) —
# um tenant inventado devolve dado de OUTRO tenant ao Console.
# `session_transfer` ESCREVE (publica roteamento).
MCP="${MCP:-http://localhost:3100}"
BODY="curl -s --max-time 15"
echo
echo "── mcp-server: identidade sem fallback nos três sites ──"
MH="$($CURL "$MCP/health")"
if [ "$MH" != "200" ]; then
  echo "   ⚠ mcp-server não respondeu /health (HTTP $MH) — seção NÃO exercitada."
else
  CAP_NO="$($BODY "$MCP/api/supervisor_capabilities/${S_NO}")"
  CAP_OK="$($BODY "$MCP/api/supervisor_capabilities/${S_OK}")"
  COP_NO="$($BODY "$MCP/api/copilot_state/${S_NO}")"
  COP_OK="$($BODY "$MCP/api/copilot_state/${S_OK}")"
  echo "   P3 supervisor_capabilities  sem tenant → $(printf '%s' "$CAP_NO" | head -c 90)"
  echo "      CONTROLE   com tenant → $(printf '%s' "$CAP_OK" | head -c 90)"
  echo "   P4 copilot_state            sem tenant → $(printf '%s' "$COP_NO" | head -c 90)"
  echo "      CONTROLE   com tenant → $(printf '%s' "$COP_OK" | head -c 90)"
  case "$CAP_NO" in *tenant_unknown*) : ;; *) FAIL="$FAIL\n   · P3 sem tenant NÃO recusou — leitura cross-tenant de config de pool" ;; esac
  case "$CAP_OK" in *tenant_unknown*) FAIL="$FAIL\n   · P3 CONTROLE recusou com tenant presente — recusa indiscriminada" ;; esac
  case "$COP_NO" in *tenant_unknown*) : ;; *) FAIL="$FAIL\n   · P4 sem tenant NÃO recusou — leitura cross-tenant do ContextStore" ;; esac
  case "$COP_OK" in *tenant_unknown*) FAIL="$FAIL\n   · P4 CONTROLE recusou com tenant presente — recusa indiscriminada" ;; esac

  # P5 — o caminho de ESCRITA. Só a RECUSA é exercitada: o ramo positivo
  # publicaria evento de roteamento e XADD para uma sessão de rascunho, e um gate
  # não deve deixar rastro num tópico de produção. Declarado, não escondido.
  # Token pelo helper unico. Antes havia um login proprio aqui, e ele quebrou no dia
  # em que o gate passou a usar `_auth.sh`: os dois definem `AUTH`, mas com
  # significados diferentes (o helper ja inclui `/auth`), e a concatenacao virou
  # `.../auth/auth/login` → 404 → token vazio → P5 silenciosamente nao exercitado.
  # Duas implementacoes de "obter um token" divergem no primeiro ajuste.
  TOK="$(plughub_token 2>/dev/null)"
  if [ -z "$TOK" ]; then
    echo "   ⚠ P5 (session_transfer) NÃO exercitado: sem credencial (plughub_token)."
  else
    C5="$($CURL -X POST "$MCP/api/session_transfer/${S_NO}" $JSON \
          -H "Authorization: Bearer $TOK" -d '{"target_pool":"retencao_humano"}')"
    echo "   P5 session_transfer         sem tenant → HTTP $C5   (esperado 409)"
    [ "$C5" = "409" ] || FAIL="$FAIL\n   · P5 devolveu $C5 — a transferência foi executada (ou falhou por outro motivo)"$'\n'"     com tenant desconhecido: contato re-roteado para namespace inventado"
    echo "   ⚠ o ramo POSITIVO do P5 não é exercitado de propósito — publicaria"
    echo "     evento de roteamento real para uma sessão de rascunho."
  fi
fi

if [ -n "$FAIL" ]; then
  echo; echo "FALHAS:"; printf "$FAIL\n"
  echo; echo "VEREDICTO: VERMELHO"
  exit 1
fi
echo
echo "VEREDICTO: VERDE — join P0 200 · P1 403 · P2 403 · mcp-server P3/P4/P5 recusam sem tenant"
exit 0
