#!/usr/bin/env bash
# ==============================================================================
# gate_context_write_has_reader.sh — nao se oferece namespace do qual nada volta
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   P  o que a escrita ACEITA, a leitura DEVOLVE — `service.*` grava e volta ... 200 + presente
#   N  `agent.*` e recusado, e a recusa DIZ o motivo ................. 422 namespace_sem_leitor
#   C  o formulario nao OFERECE o que o backend recusa (as duas listas do
#      `ContextoTab` nao citam `agent.`)
#   D  a leitura continua descartando `agent.*` (a decisao P7 nao foi desfeita)
#
# ⚠️ **P e o ramo que impede o verde errado.** Um backend que recusasse TUDO deixaria N
# verde com o formulario inutil; e o defeito original nao era "aceita demais", era
# **aceitar sem leitor**. So o par (aceita+volta) x (recusa+diz) descreve o conserto.
#
# POR QUE ELE EXISTE (AUT-50, 2026-09-09)
# ---------------------------------------
# `agent.*` era ESCRIVEL e ILEGIVEL. Medido ao vivo: `POST /api/inject-context` ->
# **200**, **14 campos no Redis**, e o `supervisor_state` servindo **13** — com
# `context_withheld {total: 13, by_rule: [], by_pool_scope: []}`, isto e, a tag nem
# contava como retida: estava fora do conjunto de origem. O `ManualTagForm` oferecia
# `agent.` como PRIMEIRA sugestao.
#
# A doc promete o mecanismo que faltava (`visibility: [participant_id]` + filtro por
# participante no `supervisor_state`) e NENHUMA das duas metades existe. Populacao
# medida: **0** tags vivas, **0** no stream duravel — nao e "pouco usado", nao dava para
# usar. Fechou-se a AFORDANCIA; construir a feature reabriria a superficie que a decisao
# P7 de `context-masking-rules.md` fechou de proposito.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CG="${CG:-http://localhost:8010}"
MCP="${MCP:-http://localhost:3100}"
AUTH="${AUTH:-http://localhost:3202}"
POOL_WH=gate_promocao_ia
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
sec() { printf '\n\033[1m%s\033[0m\n' "$1"; }
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }

command -v jq >/dev/null || inc "jq ausente"

printf '\033[1mgate: namespace que a escrita aceita, a leitura devolve\033[0m\n'

T="$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')"
[ -n "$T" ] || inc "login do admin falhou"

SID="$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON -d "{\"tenant_id\":\"$TENANT\"}" | jq -r '.session_id // empty')"
[ -n "$SID" ] || inc "nao consegui criar a sessao descartavel"
for _ in $(seq 1 20); do [ -n "$(redis EXISTS "$TENANT:ctx:$SID" | grep 1)" ] && break; sleep 1; done
echo "  sessao descartavel: $SID"

escreve() {  # $1=key $2=value -> "codigo|reason"
  local c b
  c="$($CURL -o /tmp/gate50_w.json -w '%{http_code}' -X POST "$MCP/api/inject-context/$SID" \
      -H "Authorization: Bearer $T" $JSON -d "{\"key\":\"$1\",\"value\":\"$2\"}")"
  b="$(jq -r '.error // ""' /tmp/gate50_w.json 2>/dev/null)"
  printf '%s|%s' "$c" "$b"
}
serve() {  # $1=key -> "sim|nao"
  $CURL "$MCP/api/supervisor_state/$SID" -H "Authorization: Bearer $T" > /tmp/gate50_r.json
  jq -e --arg k "$1" '(.customer_context.context_snapshot // {}) | has($k)' /tmp/gate50_r.json >/dev/null 2>&1 \
    && echo sim || echo nao
}

sec "P — o que a escrita ACEITA, a leitura DEVOLVE"
R="$(escreve service.gate50 "volta para a tela")"
if [ "${R%%|*}" = "200" ]; then
  ok "POST service.gate50 -> 200"
  [ "$(serve service.gate50)" = "sim" ] && ok "e o supervisor_state DEVOLVE a tag" \
    || bad "o supervisor_state NAO devolve service.gate50 — aceitar sem leitor voltou, noutro namespace"
else
  bad "POST service.gate50 -> ${R%%|*} (esperava 200); o formulario ficou sem namespace nenhum"
fi

sec "N — agent.* e recusado, e a recusa DIZ o motivo"
R="$(escreve agent.gate50 "nao deveria entrar")"
case "$R" in
  422\|namespace_sem_leitor) ok "POST agent.gate50 -> 422 namespace_sem_leitor" ;;
  200\|*) bad "POST agent.gate50 -> 200: voltou a aceitar o que ninguem le" ;;
  403\|*) bad "POST agent.gate50 -> 403 generico: manda pedir permissao para porta que nao existe" ;;
  *)      bad "POST agent.gate50 -> $R (esperava 422 namespace_sem_leitor)" ;;
esac
[ "$(serve agent.gate50)" = "nao" ] && ok "e nada de agent.* aparece no snapshot" \
  || bad "agent.gate50 apareceu no snapshot — a decisao P7 foi desfeita sem dizer"

sec "C — o formulario nao OFERECE o que o backend recusa"
ARQ=packages/platform-ui/src/modules/agent-assist/components/tabs/ContextoTab.tsx
LISTAS="$(grep -E '^const (OPERATOR|SUPERVISOR)_WRITE_PREFIXES' "$ARQ" || true)"
if [ -z "$LISTAS" ]; then
  inc "nao achei as listas de prefixos em $ARQ — o ramo C nao mediu nada"
elif printf '%s' "$LISTAS" | grep -q '"agent\.'; then
  bad "o datalist ainda sugere \`agent.\` — o 422 chega como surpresa"
  printf '%s\n' "$LISTAS" | sed 's/^/                 /'
else
  ok "nenhuma das duas listas cita \`agent.\`"
fi

sec "D — a leitura continua descartando agent.* (P7 intacta)"
grep -q 'if (ns === "agent") continue' packages/mcp-server-plughub/src/server.ts \
  && ok "o descarte na leitura continua no lugar" \
  || bad "o descarte sumiu: uma regra de mascaramento pode expor nota privada"

sec "limpeza"
redis HDEL "$TENANT:ctx:$SID" service.gate50 agent.gate50 >/dev/null
RTOK="$(redis HGET "$TENANT:ctx:$SID" core.workflow.delegate_resume_token | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)"
if [ -n "$RTOK" ] && [ -n "$(redis HGET "$TENANT:resume_tokens" "$RTOK" | tr -d '\r')" ]; then
  C="$($CURL -o /dev/null -w '%{http_code}' -X POST "$CG/v1/channels/webhook/resume/$RTOK" $JSON \
      -H "Authorization: Bearer $T" \
      -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"timeout\",\"source\":\"gate50\"}}")"
  echo "  sessao encerrada (HTTP $C)"
fi
redis ZREM "$TENANT:pool:aprovacao_deploy:queue" "$SID" >/dev/null

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — escrita e leitura falam do mesmo conjunto.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
