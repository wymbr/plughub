#!/usr/bin/env bash
# probe_force_complete_branches.sh
#
# Lacuna 4 — `POST /api/force-complete/:sessionId` devolvia SEMPRE `200 ok:true`
# gravando um campo que só tem leitores de exibição. Agora ramifica em três, e
# este probe mede os TRÊS, porque um veredicto que só sabe reprovar (ou só
# aprovar) não é veredicto.
#
#   A) sessão COM item de trabalho parqueado  → 200  `via: work_task_resume`
#   B) sessão inexistente                     → 404  `nothing_to_complete`
#   C) pipeline em execução, sem item         → 501  `abort_not_supported`
#
# O ramo A é a TESTEMUNHA: sem ele, um 404 em tudo passaria por "ramificou".
#
# Semeadura do ramo C: `{t}:pipeline:{sid}:running` é lido por mais dois lugares
# — o crash detector (`crash_detector.py` §148) e o despacho do bridge (§3685) —,
# e nenhum dos dois alcança uma sessão sintética: o detector itera as conversas do
# `meta` de uma instância CAÍDA, e o bridge só consulta a sessão que vai despachar.
# Ainda assim a chave nasce com TTL e é apagada no fim (`pool inventado não é
# sandbox`, 2026-08-05).
#
# Uso:  bash infra/test/probe_force_complete_branches.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
MCP="http://localhost:3100"
# auth-api = 3202 no HOST (3200 é o ai-gateway — apontar para lá devolve
# "login falhou" sem que haja nada errado com a credencial).
AUTH="${AUTH:-http://localhost:3202}"
POOL_WH="formfill_demo_ia"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }

pass=0; fail=0; inc=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }
huh() { echo "   ⚠️  INCONCLUSIVO: $1"; inc=$((inc+1)); }

# status + corpo numa chamada só
call() { # sessionId -> "HTTP<status>|<body>"
  $CURL -o /tmp/_fc_body -w 'HTTP%{http_code}' -X POST \
    "$MCP/api/force-complete/$1" $JSON -H "Authorization: Bearer $TOK" \
    -d '{"reason":"probe"}'
  echo -n "|"; cat /tmp/_fc_body
}

echo "══ 0) preflight — o código novo está NO ARTEFATO servido? ══"
# Sem isto, "404 em tudo" seria indistinguível de "não rebuildou".
N=$($COMPOSE exec -T mcp-server-plughub \
      grep -c "nothing_to_complete" /app/packages/mcp-server-plughub/dist/server.js < /dev/null || echo 0)
N=$(echo "$N" | tr -dc '0-9')
if [ "${N:-0}" -ge 1 ]; then ok "dist/server.js contém o ramo novo (n=$N)"
else huh "dist/server.js NÃO contém 'nothing_to_complete' — rebuild do mcp-server pendente; abortando"; exit 1; fi

echo "══ 1) login (o endpoint exige role supervisor|admin) ══"
LOGIN=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}")
TOK=$(echo "$LOGIN" | jq -r '.access_token // empty')
[ -n "$TOK" ] || { echo "   ✗ login falhou em $AUTH — ${LOGIN:0:200}"; exit 1; }
ok "token de supervisor obtido"

echo "══ 2) ramo A — sessão COM item parqueado → 200 ══"
SID=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_briefing_demo\"}}" \
  | jq -r '.session_id // empty')
if [ -z "$SID" ]; then
  huh "webhook não devolveu session_id — sem item, o ramo A não pode ser julgado"
else
  sleep 4   # o delegate precisa parquear o item antes de haver ledger
  LEDGER=$(redis GET "$TENANT:work_task:$SID" | tr -d '\r')
  if [ -z "$LEDGER" ]; then
    huh "sessão $SID sem ledger work_task — o ramo A mediria outra coisa"
  else
    ok "pré-condição do ramo A: ledger presente em $SID"
    R=$(call "$SID"); S="${R%%|*}"; B="${R#*|}"
    if [ "$S" = "HTTP200" ] && echo "$B" | grep -q 'work_task_resume'; then
      ok "A: 200 com via=work_task_resume"
    else
      bad "A: esperado 200/work_task_resume, veio $S — ${B:0:200}"
    fi
    # Consequência, não só código: o item tem de deixar de existir.
    sleep 3
    AFTER=$(redis GET "$TENANT:work_task:$SID" | tr -d '\r')
    RT=$(echo "$AFTER" | jq -r '.resume_token // empty' 2>/dev/null || echo "")
    if [ -z "$AFTER" ] || [ -z "$RT" ]; then ok "A: item encerrado de fato (ledger sem resume_token)"
    else bad "A: 200 devolvido mas o item CONTINUA parqueado — o 200 mentiu de novo"; fi
  fi
fi

echo "══ 3) ramo B — sessão inexistente → 404 ══"
GHOST="sess_probe_ghost_$RANDOM$RANDOM"
R=$(call "$GHOST"); S="${R%%|*}"; B="${R#*|}"
if [ "$S" = "HTTP404" ] && echo "$B" | grep -q 'nothing_to_complete'; then
  ok "B: 404 nothing_to_complete"
else
  bad "B: esperado 404/nothing_to_complete, veio $S — ${B:0:200}"
fi

echo "══ 4) ramo C — pipeline em execução, sem item → 501 ══"
RSID="sess_probe_running_$RANDOM$RANDOM"
redis SET "$TENANT:pipeline:$RSID:running" "probe-instance" EX 120 >/dev/null
R=$(call "$RSID"); S="${R%%|*}"; B="${R#*|}"
if [ "$S" = "HTTP501" ] && echo "$B" | grep -q 'abort_not_supported'; then
  ok "C: 501 abort_not_supported"
else
  bad "C: esperado 501/abort_not_supported, veio $S — ${B:0:200}"
fi
redis DEL "$TENANT:pipeline:$RSID:running" >/dev/null
ok "C: chave semeada removida"

echo "══ 5) controle negativo — sem Bearer o endpoint recusa ══"
# Prova que o gate de role está EM JOGO: se este devolvesse 200, os três ramos
# acima estariam sendo medidos num endpoint sem porteiro.
S=$($CURL -o /dev/null -w '%{http_code}' -X POST "$MCP/api/force-complete/$GHOST")
if [ "$S" = "401" ] || [ "$S" = "403" ]; then ok "sem Bearer → $S (gate em jogo)"
else bad "sem Bearer → $S; o gate de role NÃO está em jogo"; fi

echo
echo "──────────────────────────────────────────"
echo "  passou=$pass  falhou=$fail  inconclusivo=$inc"
if [ "$fail" -gt 0 ]; then echo "  VEREDICTO: ❌ VERMELHO"; exit 1; fi
if [ "$inc"  -gt 0 ]; then echo "  VEREDICTO: ⚠️  INCONCLUSIVO"; exit 2; fi
echo "  VEREDICTO: ✅ VERDE"
