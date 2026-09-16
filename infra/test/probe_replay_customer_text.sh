#!/usr/bin/env bash
# probe_replay_customer_text.sh — 2026-09-16  (RPL-01)
#
# PERGUNTA: o texto que o CLIENTE escreveu chega ao que o avaliador de qualidade lê?
#
# O avaliador (`agente_avaliacao_v1`) recebe `ReplayContext.events`, montado do stream da sessão
# — ao vivo pelo replayer, e depois do fechamento pela tabela `session_stream_events` (Stream
# Persister → hydrator). Os dois leem `author` (JSON) e `payload`. O bridge gravava a mensagem do
# cliente em outro formato (`author_role`/`content` soltos), e a linha saía com autor nulo e
# payload `{}`. Medido em 2026-09-16: 1 276 linhas assim; em 69 sessões com agente, 284 de 857
# mensagens vazias — o mesmo texto existia no ClickHouse.
#
# RAMOS (um contato real por ramo; julgados na tabela persistida, que é o que o avaliador recebe
# depois que o stream expira)
#   IA  pool auth_form_ia: o cliente responde um formulário (menu_result)
#     A0 a sessão foi persistida e tem mensagem do AGENTE com texto (controle: o persister rodou e
#        sabe ler o formato canônico)
#     A1 a resposta do cliente está persistida com autor `customer` e texto no payload
#     A2 nenhuma mensagem persistida sem autor e sem payload
#     A3 a senha digitada (campo mascarado) não aparece em linha nenhuma
#   HU  pool probe_agh02 com um agente humano headless: o cliente escreve texto livre
#     H0 a sessão foi persistida — e se o persister RODOU e achou o stream vazio, é FALHA (o
#        fechamento apagava o stream antes dele), não inconclusivo
#     H1 o texto do cliente está persistido com autor `customer`
#     H2 nenhuma mensagem persistida sem autor e sem payload
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
REPLAYER="${REPLAYER_CONTAINER:-plughub-demo-session-replayer-1}"
BRIDGE="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
TENANT="${TENANT:-tenant_demo}"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
Q()     { docker exec "$PG" psql -U plughub -d plughub_demo -tAc "$1"; }

# espera o persister gravar a sessao (roda no session_closed)
persistida() {
  for _ in $(seq 1 60); do
    [ "$(Q "select count(*) from session_stream_events where session_id='$1'")" -gt 0 ] && return 0
    sleep 2
  done
  return 1
}
vazias() {
  Q "select count(*) from session_stream_events where session_id='$1' and event_type='message'
     and author is null and (payload is null or payload::text in ('{}','null'))"
}

SUF=$RANDOM$RANDOM
docker cp infra/test/_ws_chat.py "$GW:/tmp/_ws_chat.py" >/dev/null

echo "════════════════════════════════════════════════════════════════════"
echo " o texto do cliente chega ao que o avaliador le?"
echo "════════════════════════════════════════════════════════════════════"

echo "── IA · auth_form_ia (formulario) ──────────────────────────────────"
SENHA="7${SUF:0:5}"
RULES="[{\"match\":\"Preencha seus dados de acesso\",\"answer\":{\"email\":\"probe.rpl.$SUF@exemplo.com\",\"senha\":\"$SENHA\",\"codigo_2fa\":\"000000\"}}]"
OUT=$(docker exec "$GW" python3 /tmp/_ws_chat.py "$TENANT" auth_form_ia "probe-rpl-ia-$SUF" "$RULES" 25 2>&1)
SID=$(printf '%s\n' "$OUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
if [ -z "$SID" ] || ! printf '%s\n' "$OUT" | grep -q '^ANSWER'; then
  incon "IA o cliente nao respondeu o formulario (sid=$SID): $(printf '%s\n' "$OUT" | tail -3 | tr '\n' '|' | cut -c1-200)"
elif ! persistida "$SID"; then
  incon "IA sessao $SID nao foi persistida em 120 s — nada a julgar"
else
  AG=$(Q "select count(*) from session_stream_events where session_id='$SID' and event_type='message'
          and (author->>'role') <> 'customer' and coalesce(payload->'content'->>'text', payload->>'text','') <> ''")
  if [ "$AG" -gt 0 ]; then
    ok "A0 CONTROLE sessao persistida, $AG mensagem(ns) de agente com texto"
    CL=$(Q "select count(*) from session_stream_events where session_id='$SID' and event_type='message'
            and author->>'role'='customer' and coalesce(payload->'content'->>'text', payload->>'text','') like '%probe.rpl.$SUF%'")
    [ "$CL" -gt 0 ] && ok "A1 resposta do cliente persistida com autor customer e texto ($CL)" \
      || falha "A1 a resposta do cliente nao chegou ao avaliador (0 linhas com autor customer e o email do probe)"
    V=$(vazias "$SID")
    [ "$V" = 0 ] && ok "A2 nenhuma mensagem sem autor e sem payload" || falha "A2 $V mensagem(ns) persistida(s) VAZIA(s)"
  else
    incon "A0 nenhuma mensagem de agente com texto na sessao $SID — o persister nao le nem o formato canonico; A1/A2 nao medem nada"
  fi
  VAZ=$(Q "select count(*) from session_stream_events where session_id='$SID' and (payload::text like '%$SENHA%' or coalesce(original_content::text,'') like '%$SENHA%')")
  [ "$VAZ" = 0 ] && ok "A3 a senha digitada nao aparece em linha persistida" || falha "A3 a senha aparece em $VAZ linha(s)"
fi

echo "── HU · probe_agh02 (agente humano, texto livre) ───────────────────"
# pool-fixture humano (o mesmo da AGH-02); criado se ausente, para este gate nao depender daquele
REG="${REGISTRY:-http://localhost:3300}"; AUTH="${AUTH:-http://localhost:3202}"
if [ "$(docker exec "$REDIS" redis-cli sismember "$TENANT:pools" probe_agh02)" != 1 ]; then
  TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
  curl -s -o /dev/null -X POST -H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json' \
    "$REG/v1/pools" -d '{"pool_id":"probe_agh02","channel_types":["webchat"],"sla_target_ms":60000,"agent_kind":"human","description":"fixture AGH-02 / RPL-01"}'
  sleep 5
fi
IMG=$(docker inspect -f '{{.Image}}' "$GW")
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_AUTH_JWT_SECRET=' | sed 's/^/-e /' | tr '\n' ' ')
AG_USER="u-rpl-$SUF"
docker run --rm -i --name "probe_rpl_$SUF" --network "$NET" --entrypoint python $ENV \
  -e POOL=probe_agh02 -e TENANT="$TENANT" -e AGENT_USER="$AG_USER" -e HOLD_S=60 "$IMG" - \
  < infra/test/_agent_ws_restart_exercise.py > /tmp/probe_rpl_ag_$SUF.out 2>&1 &
PID_AG=$!
for _ in $(seq 1 30); do grep -q REGISTERED /tmp/probe_rpl_ag_$SUF.out 2>/dev/null && break; sleep 0.5; done
TEXTO="PROBE_RPL_CLIENTE_$SUF quero falar sobre minha fatura"
OUT=$(docker exec "$GW" python3 /tmp/_ws_chat.py "$TENANT" probe_agh02 "probe-rpl-hu-$SUF" '[]' 25 \
      "[{\"after_s\": 10, \"text\": \"$TEXTO\"}]" 2>&1)
SID=$(printf '%s\n' "$OUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
# testemunha de que o texto seguiu o caminho HUMANO do bridge (o ramo que a H1 julga); lida no
# log, porque `human_agents` ja foi limpo quando o cliente sai
ATEND=$( [ -n "$SID" ] && docker logs --since 5m "$BRIDGE" 2>&1 | grep -c "Forwarded text to human agent: session=$SID" )
if ! grep -q REGISTERED /tmp/probe_rpl_ag_$SUF.out || [ -z "$SID" ] || ! printf '%s\n' "$OUT" | grep -q '^SAID' || [ "${ATEND:-0}" = 0 ]; then
  incon "HU agente nao registrou, cliente nao autenticou, nao falou ou o texto nao foi ao humano (sid=$SID encaminhado=${ATEND:-0}): $(printf '%s\n' "$OUT" | tail -2 | tr '\n' '|' | cut -c1-160)"
elif ! persistida "$SID"; then
  # O persister RODOU e achou o stream vazio? Isto é o defeito (o DEL no fechamento vencia a
  # corrida), não ausência de amostra — medido na primeira rodada: `0 events persisted`.
  RODOU=$(docker logs --since 10m "$REPLAYER" 2>&1 | grep -c "Persister: 0 events persisted for session $SID")
  if [ "$RODOU" -gt 0 ]; then
    falha "H0 o persister rodou e achou o stream da sessao $SID VAZIO — o fechamento o apagou antes"
  else
    incon "HU sessao $SID nao foi persistida em 120 s e o persister nao registrou execucao — nada a julgar"
  fi
else
  ok "H0 sessao $SID persistida (texto encaminhado ao humano: $ATEND)"
  CL=$(Q "select count(*) from session_stream_events where session_id='$SID' and event_type='message'
          and author->>'role'='customer' and coalesce(payload->'content'->>'text', payload->>'text','') like '%PROBE_RPL_CLIENTE_$SUF%'")
  [ "$CL" -gt 0 ] && ok "H1 texto do cliente persistido com autor customer ($CL)" \
    || falha "H1 o texto do cliente nao chegou ao avaliador (0 linhas com autor customer e o texto do probe)"
  V=$(vazias "$SID")
  [ "$V" = 0 ] && ok "H2 nenhuma mensagem sem autor e sem payload" || falha "H2 $V mensagem(ns) persistida(s) VAZIA(s)"
fi
docker rm -f "probe_rpl_$SUF" >/dev/null 2>&1; wait "$PID_AG" 2>/dev/null; rm -f /tmp/probe_rpl_ag_$SUF.out

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
