#!/usr/bin/env bash
# probe_agent_ws_restart_ghost.sh — 2026-09-15  (AGH-02)
#
# PERGUNTA: quando o mcp-server REINICIA (deploy, `up -d`, crash), o agente humano cujo socket
# morreu sem `close` sai dos pools — sem derrubar quem reconectou e continua vivo?
#
# O DEFEITO (medido em 2026-09-15, ao fechar a AGH-01)
#   O desregistro do humano só roda no `close` do `/agent/ws`. Processo recriado derruba todos os
#   sockets sem que o timer de graça chegue a disparar, e a instância `human-*` fica `ready` em todos
#   os pools, SEM TTL e sem ninguém do outro lado. A do admin ficou em 12 pools e recebeu os contatos
#   de `probe_webrtc_agent_console` e `probe_webrtc_contact_entry`, que reprovaram por isso.
#
# RAMOS
#   R0 os dois agentes registram antes do reinício (sem isto nada adiante mede)
#   G1 FANTASMA: G conectou antes do reinício e não voltou → depois da janela, G não está no pool
#      e a chave da instância não existe
#   L1 CONTROLE: L reconectou logo depois do reinício e SEGUROU o socket a janela inteira (mais
#      que o TTL da liveness) → continua instância. Sem ele, um varredor que derrubasse todo mundo
#      passaria em G1
#   L2 o socket de L não caiu durante a janela
#   L3 a liveness de L foi sustentada pelos PONGS, e não pelo cinto local do varredor (que numa
#      réplica só mascara a renovação quebrada — medido na mutação que a desligava)
#   C1 o CONTATO que G atendia não fica preso ao fantasma: depois da janela, `human_agents` da
#      sessão não tem G (o close publicaria `agent_disconnect`; o reinício não publica nada)
#   LIMPEZA pelo produto
#
# ⚠️ REINICIA o mcp-server. Assistido: derruba todo Console conectado (que reconecta sozinho).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
MCPC="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_agh02"
JANELA_S="${JANELA_S:-150}"
FALHA=0; INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
rc()    { docker exec "$REDIS" redis-cli "$@"; }
no_pool()   { [ "$(rc sismember "$TENANT:pool:$POOL:instances" "human-$1")" = 1 ]; }
tem_chave() { [ "$(rc exists "$TENANT:instance:human-$1")" = 1 ]; }

echo "════════════════════════════════════════════════════════════════════"
echo " o reinicio do mcp-server deixa agente humano FANTASMA no pool?"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "gateway fora do ar ou login do admin falhou"; echo " INCONCLUSIVO"; exit 2
fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
  curl -s -o /dev/null -X POST "${H[@]}" "$REG/v1/pools" \
    -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webchat\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture AGH-02 (reinicio do mcp-server)\"}"
  sleep 5
fi

ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_AUTH_JWT_SECRET=' | sed 's/^/-e /' | tr '\n' ' ')
SUF=$RANDOM$RANDOM
G="u-agh02-g-$SUF"; L="u-agh02-l-$SUF"
agente() {  # $1 usuario · $2 segundos · $3 arquivo de saida
  docker run --rm -i --name "probe_agh02_$1" --network "$NET" --entrypoint python $ENV \
    -e POOL="$POOL" -e TENANT="$TENANT" -e AGENT_USER="$1" -e HOLD_S="$2" "$IMG" - \
    < infra/test/_agent_ws_restart_exercise.py > "$3" 2>&1
}
saudavel() {
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$MCPC" 2>/dev/null)" = healthy ] && return 0
    curl -s -o /dev/null --max-time 2 http://localhost:3100/health && return 0
    sleep 2
  done
  return 1
}

TMP=$(mktemp -d)
agente "$G" 600 "$TMP/g.out" &
PID_G=$!
agente "$L" 20 "$TMP/l0.out"   # L também existe antes do reinicio (e sai limpo)
for _ in $(seq 1 30); do grep -q REGISTERED "$TMP/g.out" && break; sleep 0.5; done
# L0 fecha o socket, mas só sai do pool depois da graça de 2,5 s. Um cliente que chegue antes vai
# para L, é devolvido na saída e só então para G — medido: passou dos 30 s de espera e C0 saiu
# INCONCLUSIVO. Espera L sair, para G ser o único com vaga.
for _ in $(seq 1 40); do no_pool "$L" || break; sleep 0.5; done
# um cliente webchat que G atende (G é o único do pool com vaga)
docker cp infra/test/_ws_chat.py "$GW:/tmp/_ws_chat.py" >/dev/null
docker exec "$GW" python3 /tmp/_ws_chat.py "$TENANT" "$POOL" "probe-agh02-c-$SUF" '[]' $((JANELA_S + 120)) > "$TMP/c.out" 2>&1 &
PID_C=$!
SID=""; ANEXADO=""
for _ in $(seq 1 120); do
  SID=$(sed -n 's/^AUTHENTICATED session_id=//p' "$TMP/c.out" | head -1)
  [ -n "$SID" ] && [ "$(rc sismember "session:$SID:human_agents" "human-$G")" = 1 ] && { ANEXADO=1; break; }
  sleep 0.5
done
if [ -n "$ANEXADO" ]; then
  echo "  INFO    contato $SID atribuido a G antes do reinicio"
else
  incon "C0 o contato do cliente nao foi atribuido a G antes do reinicio (sid=$SID) — C1 nao mede nada"
fi
if ! grep -q "REGISTERED $G" "$TMP/g.out" || ! grep -q "REGISTERED $L" "$TMP/l0.out"; then
  incon "R0 agentes nao registraram antes do reinicio (G: $(tr '\n' ' ' < "$TMP/g.out" | cut -c1-120))"
else
  ok "R0 G e L registrados no pool $POOL antes do reinicio"
  echo "  INFO    reiniciando $MCPC (o socket de G morre sem close)"
  T_RESTART=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker restart "$MCPC" >/dev/null
  saudavel || incon "mcp-server nao voltou saudavel"
  wait "$PID_G" 2>/dev/null
  no_pool "$G" && echo "  INFO    logo apos o reinicio G continua no pool (o estado que a AGH-02 descreve)"

  agente "$L" "$JANELA_S" "$TMP/l.out" &
  PID_L=$!
  for _ in $(seq 1 30); do grep -q REGISTERED "$TMP/l.out" && break; sleep 0.5; done
  wait "$PID_L" 2>/dev/null

  if no_pool "$G" || tem_chave "$G"; then
    falha "G1 FANTASMA: $JANELA_S s depois do reinicio G segue no pool=$(no_pool "$G" && echo sim || echo nao) chave=$(tem_chave "$G" && echo sim || echo nao) (TTL da chave: $(rc ttl "$TENANT:instance:human-$G"))"
  else
    ok "G1 G saiu do pool e a chave da instancia nao existe"
  fi
  if grep -q "REGISTERED $L" "$TMP/l.out" && no_pool "$L" && tem_chave "$L"; then
    ok "L1 CONTROLE: L, vivo por $JANELA_S s, continua instancia"
  else
    falha "L1 L (vivo) nao e instancia ao fim da janela: registrou=$(grep -c REGISTERED "$TMP/l.out") no_pool=$(no_pool "$L" && echo sim || echo nao)"
  fi
  grep -q "HELD $L" "$TMP/l.out" && ok "L2 o socket de L nao caiu durante a janela" \
    || falha "L2 socket de L caiu: $(tr '\n' ' ' < "$TMP/l.out" | cut -c1-160)"
  # L1 sozinho nao prova a RENOVACAO: numa replica so, o varredor reafirma a chave de quem tem
  # conexao local (cinto de seguranca), e L passaria com os pongs sem renovar nada — medido na
  # mutacao LM2. Com varias replicas o cinto nao existe; o que sustenta L la e o pong.
  CINTO=$(docker logs --since "$T_RESTART" "$MCPC" 2>&1 | grep -c "reafirmada instance=human-$L ")
  [ "$CINTO" = 0 ] && ok "L3 a liveness de L se sustentou pelos pongs, sem o cinto local" \
    || falha "L3 o varredor precisou reafirmar a liveness de L $CINTO vez(es) — os pongs nao a renovaram"
  if [ -n "$ANEXADO" ]; then
    if [ "$(rc sismember "session:$SID:human_agents" "human-$G")" = 1 ]; then
      falha "C1 o contato $SID segue preso ao fantasma G (human_agents=[$(rc smembers "session:$SID:human_agents" | tr '\n' ' ')])"
    else
      ok "C1 o contato $SID nao esta mais com G (human_agents=[$(rc smembers "session:$SID:human_agents" | tr '\n' ' ')])"
    fi
  fi
fi
docker exec "$GW" pkill -f "probe-agh02-c-$SUF" >/dev/null 2>&1; wait "$PID_C" 2>/dev/null

# limpeza pelo produto: conecta e sai sozinho
for u in "$G" "$L"; do
  if no_pool "$u" || tem_chave "$u"; then agente "$u" 2 /dev/null; sleep 5; fi
done
RESTO=""; for u in "$G" "$L"; do (no_pool "$u" || tem_chave "$u") && RESTO="$RESTO $u"; done
[ -z "$RESTO" ] && ok "LIMPEZA sem instancias do probe" || falha "LIMPEZA restaram:$RESTO"
rm -rf "$TMP"

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
