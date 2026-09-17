#!/usr/bin/env bash
# probe_speech_check_surface.sh — 2026-09-17  (VOZ-27, a superfície da verificação de fala)
#
# PERGUNTA: a verificação de fala é PEDÍVEL por uma pessoa com portão, é PEDIDA sozinha pela Agenda,
# e a que NÃO rodou deixa rastro legível no mesmo lugar em que as outras aparecem?
#
# A VOZ-23 já media o caminho de fala; tudo isso morava em `curl` com token de serviço. Sem esta
# superfície, duas coisas ficam invisíveis e parecem iguais a "está tudo bem": ninguém mediu há meses,
# e a medição de hoje foi RECUSADA porque outra corria.
#
# O QUE ELA EXISTE PARA PEGAR
#   · a rota nova do gateway aberta (sem Bearer) ou frouxa (basta estar logado) — ela dispara chamada
#     sintética e consome o pool de calibração, então é ATO, não leitura;
#   · autoria declarada pelo chamador (`requested_by` vindo do corpo) — campo de autoria que o
#     chamador preenche não é autoria;
#   · a agenda semeada ausente, apontando outro pool, ou consumindo a recorrência ao disparar à mão;
#   · a recusa por "já corre" sumindo: o 409 morre na resposta HTTP que a Agenda não lê.
#
# RAMOS
#   K0  pré-condições: gateway e serviço de pé, tenant OCIOSO, pool de calibração com instância,
#       agenda semeada presente apontando o pool de disparo e com o `seed_id` no payload
#   G1  `POST /v1/agendas/{id}/fire` → ledger `dispatched` com session_id, e a recorrência INTACTA
#   A1  a rota do gateway recusa sem Bearer (401) e recusa quem não tem `config.channels` em escrita
#       (403) — e nenhuma das duas abre verificação
#   A2  com credencial de quem configura canais, o pedido é RECUSADO com 409 enquanto a da Agenda
#       corre, e a resposta diz `recorded_as`
#   A3  essa recusa aparece no RELATÓRIO que a tela lê (`/reports/speech/checks`), com motivo
#       `check_running`, agregados NULOS e autoria `user:…` — nunca zeros que se leriam como medida
#   G2  a verificação da Agenda termina e aparece no relatório com autoria `agenda:…`
#   C1  CONTROLE: com o tenant ocioso, a MESMA rota aceita (202) — o 403/409 de cima era portão e
#       ocupação, não rota quebrada
#
# EXIT: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

SC="${SPEECH_CHECK_CONTAINER:-plughub-demo-speech-check-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CH="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
DB="${CH_DB:-plughub_demo}"
AUTH="${AUTH:-http://localhost:3202}"
AN="${ANALYTICS:-http://localhost:3500}"
SCHED="${SCHEDULER:-http://localhost:3650}"
GWURL="${GW_URL:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"
POOL="speech_check"
TRIGGER="speech_check_trigger"
SEED_ID="speech_check_default"
SVC_TOKEN="${SPEECH_CHECK_SERVICE_TOKEN:-changeme_speech_check_service_token_demo}"
SONDA="probe_voz27@plughub.local"
SENHA="probe_voz27_123"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
fim() {
  echo "────────────────────────────────────────────────────────────────────"
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}
chq() { docker exec "$CH" clickhouse-client -d "$DB" -q "$1" 2>&1; }

# O serviço não publica porta no host — de propósito. Pergunta-se de dentro.
corrente() {
  docker exec "$SC" python -c "
import json,urllib.request
r=urllib.request.Request('http://localhost:3870/v1/speech-checks', headers={'x-service-token':'$SVC_TOKEN'})
print(json.load(urllib.request.urlopen(r, timeout=5)).get('running') or 'null')
" 2>/dev/null
}
# devolve só o código HTTP da rota do gateway; $1 = cabeçalho de credencial (pode ser vazio)
pede() { curl -s -o /tmp/voz27_body.json -w '%{http_code}' -X POST "$GWURL/v1/speech-checks" \
          ${1:+-H "$1"} -H 'Content-Type: application/json' -d '{}' --max-time 20; }

echo "════════════════════════════════════════════════════════════════════"
echo " a verificacao de fala e pedivel com portao, periodica, e a recusa aparece?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOKEN" ] || { incon "login do admin falhou — nada medido"; fim; }
# SCH-01: as rotas de Agenda passaram a exigir credencial. O gate entra como gente.
HS=(-H "X-Tenant-ID: $TENANT" -H "Authorization: Bearer $TOKEN")
SEM_CRED=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Tenant-ID: $TENANT" "$SCHED/v1/agendas")

# Sonda SEM `config.channels`: é o controle de que o portão recorta por CAPACIDADE, e não só por
# "tem token". Papel `supervisor` não nasce com escrita em canais.
uid() { curl -s -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users?tenant_id=$TENANT&limit=500" \
          | jq -r --arg e "$SONDA" '.[] | select(.email==$e) | .id' | head -1; }
limpa() { i=$(uid); [ -n "$i" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users/$i"; }
trap limpa EXIT INT TERM
limpa
curl -s -o /dev/null -X POST "$AUTH/auth/users" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$SONDA\",\"name\":\"Probe VOZ-27\",\"password\":\"$SENHA\",\"roles\":[\"supervisor\"],\"accessible_pools\":[\"$POOL\"]}"
T_SONDA=$(curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$SONDA\",\"password\":\"$SENHA\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$T_SONDA" ] || { incon "usuario-sonda nao criado — o portao nao pode ser medido"; fim; }

# ── K0 ──
docker inspect -f '{{.State.Running}}' "$SC" 2>/dev/null | grep -qx true || { incon "K0 servico speech-check fora do ar"; fim; }
docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null | grep -qx true || { incon "K0 channel-gateway fora do ar"; fim; }
docker exec "$GW" grep -c 'async def speech_check_run' /app/packages/channel-gateway/src/plughub_channel_gateway/main.py >/dev/null 2>&1 \
  || { incon "K0 a imagem do gateway nao tem a rota — build/up antes de medir"; fim; }
for _ in $(seq 1 30); do [ "$(corrente)" = "null" ] && break; sleep 10; done
[ "$(corrente)" = "null" ] || { incon "K0 ja ha verificacao em curso — o probe mediria a chamada de outro"; fim; }
docker exec "$REDIS" redis-cli scard "$TENANT:pool:$POOL:instances" | grep -qE '^[1-9]' \
  || { incon "K0 pool de calibracao sem instancia"; fim; }

AG=$(curl -s "${HS[@]}" "$SCHED/v1/agendas" | jq -c --arg s "$SEED_ID" '.agendas[] | select(.payload.seed_id == $s)')
[ -n "$AG" ] || { falha "K0 agenda semeada '$SEED_ID' ausente — rode o job agenda-seed (docker compose up agenda-seed)"; fim; }
AG_ID=$(echo "$AG" | jq -r '.id')
AG_POOL=$(echo "$AG" | jq -r '.target_pool_id')
[ "$AG_POOL" = "$TRIGGER" ] || { falha "K0 a agenda semeada aponta '$AG_POOL', nao o pool de disparo '$TRIGGER'"; fim; }
if [ "$SEM_CRED" = "401" ]; then
  ok "K0 servico ocioso, pool com instancia, agenda '$SEED_ID' ativa apontando $TRIGGER; e a porta do scheduler recusa sem credencial (401)"
else
  falha "K0 o scheduler respondeu $SEM_CRED sem credencial — a porta de Agenda esta ABERTA (SCH-01)"
fi

# ── G1 ──
NEXT_ANTES=$(echo "$AG" | jq -r '.next_fire_at')
curl -s -o /dev/null -X POST "${HS[@]}" "$SCHED/v1/agendas/$AG_ID/fire"
sleep 8
DISP=$(curl -s "${HS[@]}" "$SCHED/v1/agendas/$AG_ID/dispatches" \
        | jq -c '(.dispatches // .) | if type=="array" then .[0] else . end')
D_RES=$(echo "$DISP" | jq -r '.result // empty')
D_SID=$(echo "$DISP" | jq -r '.session_id // empty')
NEXT_DEPOIS=$(curl -s "${HS[@]}" "$SCHED/v1/agendas/$AG_ID" | jq -r '.next_fire_at')
if [ "$D_RES" = "dispatched" ] && [ -n "$D_SID" ]; then
  if [ "$NEXT_ANTES" = "$NEXT_DEPOIS" ]; then
    ok "G1 a agenda acionou o pool (sessao $D_SID) e a recorrencia seguiu em $NEXT_DEPOIS"
  else
    falha "G1 o disparo manual CONSUMIU a recorrencia: $NEXT_ANTES → $NEXT_DEPOIS"
  fi
else
  falha "G1 a agenda nao acionou o pool: ledger=$DISP"
fi

# ── A1 ── (a verificação da agenda está correndo: é ela que ocupa o tenant)
C_SEM=$(pede "")
C_SONDA=$(pede "Authorization: Bearer $T_SONDA")
EM_CURSO=$(corrente)
if [ "$C_SEM" = "401" ] && [ "$C_SONDA" = "403" ]; then
  ok "A1 sem credencial 401; com credencial sem \`config.channels\` em escrita 403"
else
  falha "A1 a rota nao fechou: sem credencial=$C_SEM, sonda sem capacidade=$C_SONDA (esperado 401 e 403)"
fi

# ── A2 ──
C_ADM=$(pede "Authorization: Bearer $TOKEN")
REC=$(jq -r '.detail.recorded_as // empty' /tmp/voz27_body.json 2>/dev/null)
MOTIVO=$(jq -r '.detail.reason // empty' /tmp/voz27_body.json 2>/dev/null)
if [ "$C_ADM" = "409" ] && [ "$MOTIVO" = "check_running" ] && [ -n "$REC" ]; then
  ok "A2 com a da agenda em curso ($EM_CURSO), o pedido de gente e recusado 409 e registrado como $REC"
else
  falha "A2 esperado 409 check_running com recorded_as; veio http=$C_ADM corpo=$(head -c 200 /tmp/voz27_body.json)"
  REC=""
fi

# ── A3 ── a recusa lida pelo MESMO endpoint da tela, não pelo ClickHouse cru
if [ -n "$REC" ]; then
  LINHA=""
  for _ in $(seq 1 20); do
    LINHA=$(curl -s -H "Authorization: Bearer $T_SONDA" \
              "$AN/reports/speech/checks?tenant_id=$TENANT&speech_profile_id=&limit=50" \
            | jq -c --arg c "$REC" '.data[]? | select(.check_id == $c)')
    [ -n "$LINHA" ] && break
    sleep 5
  done
  if [ -z "$LINHA" ]; then
    falha "A3 a recusa $REC nao apareceu no relatorio — a verificacao pulada ficaria invisivel"
  else
    R_ST=$(echo "$LINHA" | jq -r '.status')
    R_MO=$(echo "$LINHA" | jq -r '.failure_reason')
    R_AC=$(echo "$LINHA" | jq -r '.accuracy')
    R_BY=$(echo "$LINHA" | jq -r '.requested_by')
    if [ "$R_ST" = "failed" ] && [ "$R_MO" = "check_running" ] && [ "$R_AC" = "null" ] && [[ "$R_BY" == user:* ]]; then
      ok "A3 a recusa aparece no relatorio: failed/check_running, agregados nulos, autoria $R_BY"
    else
      falha "A3 a recusa esta no relatorio com forma errada: status=$R_ST motivo=$R_MO accuracy=$R_AC autoria=$R_BY"
    fi
  fi
fi

# ── G2 ──
ACHOU=""
for _ in $(seq 1 90); do
  ACHOU=$(curl -s -H "Authorization: Bearer $T_SONDA" \
            "$AN/reports/speech/checks?tenant_id=$TENANT&speech_profile_id=&limit=50" \
          | jq -c '[.data[]? | select(.requested_by | startswith("agenda:"))] | max_by(.started_at) // empty')
  [ -n "$ACHOU" ] && [ "$(echo "$ACHOU" | jq -r '.status')" != "running" ] && break
  sleep 10
done
if [ -z "$ACHOU" ]; then
  falha "G2 a verificacao pedida pela agenda nao chegou ao relatorio"
else
  G_ST=$(echo "$ACHOU" | jq -r '.status')
  G_OK=$(echo "$ACHOU" | jq -r '.phrases_correct // 0')
  G_TT=$(echo "$ACHOU" | jq -r '.phrases_total // 0')
  G_BY=$(echo "$ACHOU" | jq -r '.requested_by')
  if [ "$G_ST" = "completed" ] && [ "${G_OK:-0}" -gt 0 ]; then
    ok "G2 a verificacao da agenda mediu de verdade: $G_OK/$G_TT frases certas, autoria $G_BY"
  else
    falha "G2 a verificacao da agenda terminou $G_ST ($(echo "$ACHOU" | jq -r '.failure_reason')) — o caminho periodico nao mede"
  fi
fi

# ── C1 ── controle positivo da rota
for _ in $(seq 1 30); do [ "$(corrente)" = "null" ] && break; sleep 10; done
if [ "$(corrente)" != "null" ]; then
  incon "C1 o tenant nao ficou ocioso — controle positivo da rota nao medido"
else
  C_OK=$(pede "Authorization: Bearer $TOKEN")
  CID=$(jq -r '.check_id // empty' /tmp/voz27_body.json 2>/dev/null)
  if [ "$C_OK" = "202" ] && [ -n "$CID" ]; then
    ok "C1 CONTROLE com o tenant ocioso a mesma rota aceita (202, $CID) — 401/403/409 eram portao e ocupacao"
  else
    falha "C1 a rota nao aceitou nem com credencial e tenant ocioso: http=$C_OK corpo=$(head -c 200 /tmp/voz27_body.json)"
  fi
fi

fim
