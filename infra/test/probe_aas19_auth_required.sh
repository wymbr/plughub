#!/usr/bin/env bash
# probe_aas19_auth_required.sh — AAS-19 (2026-10-02; adr-a2a-server-binding D12 item 2)
#
# PERGUNTA: quando o skill exige que a PESSOA prove quem é, e quem conversa é um agente pelo canal
#           `a2a`, a task para em `TASK_STATE_AUTH_REQUIRED` com um link, a pessoa prova NO
#           NAVEGADOR (o agente nunca vê o código), e a task segue SOZINHA — e nada além da prova
#           concluída da pessoa a libera?
#
# Fixture: cliente IMPORTADO como autoritativo (sistema `__probe_aas19__`, telefone novo a cada
# rodada — o OTP limita 3 desafios por âncora em 15 min), skill `skill_probe_aas19` (sem LLM):
#   identity_proof_link → menu (prompt com o link) → identity_proof_status → complete com o veredito
# com cliente e telefone na config do slot (`$.config`), pool `probe_aas19_a2a`, endpoint
# `probe-aas19`, principal `partner`. O código do OTP é lido do LOG do gateway (modo dev do demo),
# como a pessoa o leria no telefone.
#
# RAMOS
#   A  a task para em AUTH_REQUIRED, com o link no status (texto e DataPart) e o prazo; o telefone
#      da pessoa não aparece na task (só a dica)
#   P  a página: GET não envia código; enviar desafia; a página não mostra o código; código ERRADO
#      não passa e a task continua em AUTH_REQUIRED
#   C  com o código certo a task CONCLUI sem o chamador mandar nada; o veredito é `verified` por
#      `otp`; a evidência está na journey, desta sessão, deste cliente; e o que acordou o fluxo foi
#      um SINAL — nenhuma fala de cliente entrou no stream
#   U  o link é de uso único (410 depois)
#   S  pelo stream: ele NÃO fecha em AUTH_REQUIRED (spec § 7.6.1) e termina em COMPLETED
#   N  o chamador responde "não" em AUTH_REQUIRED: a mensagem chega ao menu, o veredito é do status
#      e a task FALHA — a mensagem do agente nunca vale como prova
#   R  telefone que não é o do cadastro: o link é RECUSADO e o fluxo falha nomeando
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
for b in jq curl docker python3; do command -v "$b" >/dev/null || { echo "INCONCLUSIVO — falta $b"; exit 2; }; done

REG="${REGISTRY:-http://localhost:3300}"
GW="${GATEWAY:-http://localhost:8010}"
AUTHB="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS_C="${REDIS_CONTAINER:-plughub-demo-redis-1}"
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POOL="probe_aas19_a2a"; SLUG="probe-aas19"; SKILL="skill_probe_aas19"; SYSTEM="__probe_aas19__"
PASS="probe_aas19_Senha!1"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

C="curl -s --max-time 40"
TMPD=$(mktemp -d); BODY="$TMPD/body"
body() { cat "$BODY"; }
login() { $C -X POST "$AUTHB/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'; }
req() { local extra=(); [ -n "${4:-}" ] && extra=(-d "$4")
  $C -o "$BODY" -w '%{http_code}' -X "$2" -H "Authorization: Bearer $1" -H "x-tenant-id: $TENANT" \
     -H 'Content-Type: application/json' "${extra[@]}" "$3"; }
rcli() { docker exec "$REDIS_C" redis-cli "$@" 2>/dev/null; }

ADMIN=$(login admin@plughub.local changeme_admin)
[ -n "$ADMIN" ] || { echo "INCONCLUSIVO — login do admin@ falhou"; exit 2; }

echo "════════════════════════════════════════════════════════════════════"
echo " AAS-19 — AUTH_REQUIRED: a pessoa prova fora de banda, e a task segue"
echo "════════════════════════════════════════════════════════════════════"

# ── cliente autoritativo (telefone novo por rodada) ──────────────────────────
SUF=$(date +%s%N | cut -c9-16)
FONE="+55119${SUF}"
CID=""
limpeza() {
  [ -n "$CID" ] && docker exec -i "$GWC" python - "{\"system\":\"$SYSTEM\",\"fones\":{\"a\":\"$FONE\"},\"customers\":[\"$CID\"]}" \
      < infra/test/_arrival_evidence_cleanup.py >/dev/null 2>&1
  rm -rf "$TMPD"
}
trap limpeza EXIT INT TERM
st=$(req "$ADMIN" POST "$GW/v1/channels/webhook/identity/import" \
  "{\"system\":\"$SYSTEM\",\"customers\":[{\"external_id\":\"a19-$SUF\",\"anchors\":[{\"kind\":\"phone\",\"value\":\"$FONE\"}]}]}")
CID=$(body | jq -r '.results[0].customer_id // empty')
[ "$st" = 200 ] && [ -n "$CID" ] || { echo "INCONCLUSIVO — importação do cliente -> $st $(body | head -c 300)"; exit 2; }

# ── fixtures ─────────────────────────────────────────────────────────────────
DESC='{"display_name":"probe AAS-19","description":"Fixture do probe AAS-19.","discoverable":false,
 "input_schema":{"type":"object"},
 "output_schema":{"type":"object","required":["verified"],"properties":{"verified":{"type":"boolean"}}},
 "skills":[{"id":"prova","name":"prova","description":"exige prova do titular"}],
 "principal_kinds":["partner"]}'
FLOW='{"entry":"pedir_prova","steps":[
 {"id":"pedir_prova","type":"invoke","tool":"identity_proof_link",
  "input":{"customer_id":"$.config.customer_id","kind":"phone","value":"$.config.phone"},
  "output_as":"prova","on_success":"ja_provado","on_failure":"link_recusado"},
 {"id":"ja_provado","type":"choice","conditions":[
   {"field":"$.pipeline_state.prova.already_proven","operator":"eq","value":true,"next":"conferir"}],
  "default":"aguardar"},
 {"id":"aguardar","type":"menu","interaction":"text","timeout_s":300,
  "prompt":"Para continuar, a pessoa precisa confirmar a identidade no link: {{$.pipeline_state.prova.url}}",
  "output_as":"espera","on_success":"conferir","on_failure":"nao_provado","on_timeout":"nao_provado"},
 {"id":"conferir","type":"invoke","tool":"identity_proof_status",
  "input":{"customer_id":"$.config.customer_id"},"output_as":"veredito",
  "on_success":"avaliar","on_failure":"nao_provado"},
 {"id":"avaliar","type":"choice","conditions":[
   {"field":"$.pipeline_state.veredito.verified","operator":"eq","value":true,"next":"fim"}],
  "default":"nao_provado"},
 {"id":"fim","type":"complete","outcome":"resolved","result_from":"veredito"},
 {"id":"nao_provado","type":"complete","outcome":"failed","issue_status":"identidade nao confirmada"},
 {"id":"link_recusado","type":"complete","outcome":"failed","issue_status":"link de prova recusado"}]}'
st=$(req "$ADMIN" PUT "$REG/v1/skills/$SKILL" "{\"skill_id\":\"$SKILL\",\"name\":\"probe AAS-19\",\"version\":\"1.0\",\"description\":\"Fixture do probe AAS-19: prova fora de banda.\",\"classification\":{\"type\":\"orchestrator\"},\"flow\":$FLOW}")
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — skill de fixture -> $st $(body | head -c 300)"; exit 2; }
if [ "$(req "$ADMIN" GET "$REG/v1/pools/$POOL")" = 404 ]; then
  st=$(req "$ADMIN" POST "$REG/v1/pools" "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":10,\"description\":\"fixture do probe AAS-19\",\"a2a\":$DESC}")
else st=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL" "{\"channel_types\":[\"a2a\"],\"max_concurrent_sessions\":10,\"a2a\":$DESC}"); fi
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — pool de fixture -> $st $(body | head -c 300)"; exit 2; }
deploy() {  # $1 telefone que o fluxo vai desafiar
  local s1 s2
  s1=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL/slots/next" "{\"skill_id\":\"$SKILL\",\"config_json\":{\"customer_id\":\"$CID\",\"phone\":\"$1\"}}")
  s2=$(req "$ADMIN" POST "$REG/v1/pools/$POOL/promote" '{}')
  [ "${s1:0:1}" = 2 ] && [ "${s2:0:1}" = 2 ] || { echo "INCONCLUSIVO — deploy: set-next $s1 promote $s2 $(body | head -c 300)"; exit 2; }
  sleep 2                                        # o bridge invalida o cache do pool pelo registry.changed
}
deploy "$FONE"
req "$ADMIN" GET "$REG/v1/channel-endpoints?channel=a2a" >/dev/null
EPID=$(body | jq -r --arg s "$SLUG" '(.endpoints // .)[]? | select(.identifier==$s) | .id' | head -1)
if [ -z "$EPID" ]; then
  st=$(req "$ADMIN" POST "$REG/v1/channel-endpoints" "{\"channel\":\"a2a\",\"identifier\":\"$SLUG\",\"pool_id\":\"$POOL\",\"display_name\":\"probe AAS-19\"}")
  [ "$st" = 201 ] || { echo "INCONCLUSIVO — endpoint -> $st $(body | head -c 200)"; exit 2; }
else req "$ADMIN" PUT "$REG/v1/channel-endpoints/$EPID" '{"active":true}' >/dev/null; fi
st=$(req "$ADMIN" POST "$AUTHB/auth/users" "{\"tenant_id\":\"$TENANT\",\"email\":\"probe-aas19-adm@plughub.local\",\"password\":\"$PASS\",\"name\":\"probe AAS-19\",\"roles\":[\"admin\"],\"accessible_pools\":[]}")
[ "$st" = 201 ] || [ "$st" = 409 ] || { echo "INCONCLUSIVO — usuário -> $st $(body | head -c 200)"; exit 2; }
MASTER=$(login probe-aas19-adm@plughub.local "$PASS")
[ -n "$MASTER" ] || { echo "INCONCLUSIVO — login do usuário de fixture"; exit 2; }
P="$AUTHB/auth/v1/agent-principals"
req "$MASTER" GET "$P" >/dev/null
PRID=$(body | jq -r '.[]? | select(.display_name=="probe AAS-19") | .agent_principal_id' | head -1)
if [ -z "$PRID" ]; then req "$MASTER" POST "$P" "{\"display_name\":\"probe AAS-19\",\"allowed_pools\":[\"$POOL\"]}" >/dev/null
else req "$MASTER" PUT "$P/$PRID" "{\"allowed_pools\":[\"$POOL\"],\"active\":true}" >/dev/null
     req "$MASTER" POST "$P/$PRID/credential" >/dev/null; fi
CRED=$(body | jq -r '.credential // empty')
[ -n "$CRED" ] || { echo "INCONCLUSIVO — credencial do principal"; exit 2; }

rpc() {  # $1 JSON do params  $2 método
  $C -o "$BODY" -w '%{http_code}' -X POST "$GW/a2a/$SLUG" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
    -H "Authorization: Bearer $CRED" -d "{\"jsonrpc\":\"2.0\",\"id\":\"p19\",\"method\":\"$2\",\"params\":$1}"; }
nova() { rpc '{"message":{"role":"ROLE_USER","messageId":"m1","parts":[{"text":"quero seguir"}]},"configuration":{"returnImmediately":true}}' SendMessage >/dev/null
  body | jq -r '.result.task.id // empty'; }
estado() { rpc "{\"id\":\"$1\"}" GetTask >/dev/null; body | jq -r '.result.status.state // .error.code'; }
espera() {  # $1 id $2 estado → 0 se chegou em 30 s
  for _ in $(seq 1 60); do [ "$(estado "$1")" = "$2" ] && return 0; sleep 0.5; done; return 1; }
limpa() {  # tasks vivas de rodadas anteriores ocupam instância
  rpc '{"pageSize":100}' ListTasks >/dev/null
  for id in $(body | jq -r '.result.tasks[]? | select(.status.state|test("SUBMITTED|WORKING|INPUT_REQUIRED|AUTH_REQUIRED")) | .id'); do
    rpc "{\"id\":\"$id\"}" CancelTask >/dev/null
  done
}
pagina() {  # $1 método $2 url [$3 corpo form] → status; html em $TMPD/page
  if [ "$1" = GET ]; then curl -s --max-time 20 -o "$TMPD/page" -w '%{http_code}' "$2"
  else curl -s --max-time 20 -o "$TMPD/page" -w '%{http_code}' -X POST -H 'Content-Type: application/x-www-form-urlencoded' --data "$3" "$2"; fi; }
codigo_do_log() {  # o último código mandado ao telefone (modo dev: o OtpService o loga)
  docker logs --since "${1:-60s}" "$GWC" 2>&1 | grep 'OTP-DEV' | tail -1 | sed -n 's/.*code=\([0-9]\{4,8\}\).*/\1/p'; }
desafios() { rcli --scan --pattern "$TENANT:otp:chal:phone:*" | wc -l; }
provar() {  # $1 link → prova pela página; 0 se confirmou
  [ "$(pagina POST "$1" 'action=send')" = 200 ] || return 1
  local c; c=$(codigo_do_log 30s); [ -n "$c" ] || return 1
  [ "$(pagina POST "$1" "action=verify&code=$c")" = 200 ]; }
limpa

# ── A · AUTH_REQUIRED ─────────────────────────────────────────────────────────
echo ""; echo "── A · a task para em AUTH_REQUIRED, com o link ───────────────────────"
SID=$(nova)
[ -n "$SID" ] || { echo "INCONCLUSIVO — a task não nasceu: $(body | head -c 300)"; exit 2; }
if espera "$SID" TASK_STATE_AUTH_REQUIRED; then ok "A1 a task parou em AUTH_REQUIRED"
else falha "A1 a task não chegou a AUTH_REQUIRED (estado $(estado "$SID"))"; fi
rpc "{\"id\":\"$SID\"}" GetTask >/dev/null; cp "$BODY" "$TMPD/task"
LINK=$(jq -r '[.result.status.message.parts[]? | .data.authorization.url? // empty][0] // empty' "$TMPD/task")
case "$LINK" in
  */a2a/proof/prf_*) ok "A2 o link vem na DataPart do status: ${LINK%/*}/prf_…";;
  *) falha "A2 sem link de prova no status: $(jq -c '.result.status' "$TMPD/task" | head -c 300)";;
esac
if jq -e --arg l "$LINK" '[.result.status.message.parts[]? | .text? // "" | contains($l)] | any' "$TMPD/task" >/dev/null && [ -n "$LINK" ]; then
  ok "A3 e no texto do status (para quem só lê texto)"
else falha "A3 o texto do status não traz o link"; fi
[ "$(jq -r '.result.metadata.plughub.reason' "$TMPD/task")" = identity_proof ] && [ -n "$(jq -r '.result.metadata.plughub.auth_deadline // empty' "$TMPD/task")" ] \
  && ok "A4 motivo identity_proof e prazo do link publicados" || falha "A4 metadata: $(jq -c '.result.metadata' "$TMPD/task")"
grep -q "${FONE#+}" "$TMPD/task" && falha "A5 o telefone da pessoa aparece na task" || ok "A5 o telefone da pessoa não aparece na task (só a dica)"

# ── P · a página ──────────────────────────────────────────────────────────────
echo ""; echo "── P · a página do link ───────────────────────────────────────────────"
antes=$(desafios)
st=$(pagina GET "$LINK")
DICA="•••• ${FONE: -2}"
if [ "$st" = 200 ] && grep -q "$DICA" "$TMPD/page" && ! grep -q "${FONE#+}" "$TMPD/page"; then ok "P1 GET 200 com a dica ($DICA), sem o telefone"
else falha "P1 GET -> $st"; fi
[ "$(desafios)" = "$antes" ] && ok "P2 o GET não enviou código (desafios: $antes)" || falha "P2 o GET criou desafio de OTP"
st=$(pagina POST "$LINK" 'action=send'); COD=$(codigo_do_log 30s)
if [ "$st" = 200 ] && grep -q 'name="code"' "$TMPD/page" && [ -n "$COD" ]; then ok "P3 enviar: 200, campo do código, código no canal (log dev)"
else falha "P3 enviar -> $st, código no log: ${COD:-nenhum}"; fi
[ -n "$COD" ] && grep -q "$COD" "$TMPD/page" && falha "P4 a página MOSTRA o código" || ok "P4 a página não mostra o código"
ERRADO=$(printf '%06d' $(( (10#${COD:-0} + 1) % 1000000 )))
st=$(pagina POST "$LINK" "action=verify&code=$ERRADO")
[ "$st" = 400 ] && [ "$(estado "$SID")" = TASK_STATE_AUTH_REQUIRED ] && ok "P5 código errado: 400 e a task continua em AUTH_REQUIRED" \
  || falha "P5 código errado -> $st, task $(estado "$SID")"

# ── C · concluída sem o chamador mandar nada ──────────────────────────────────
echo ""; echo "── C · a task segue sozinha ───────────────────────────────────────────"
st=$(pagina POST "$LINK" "action=verify&code=$COD")
[ "$st" = 200 ] && grep -qi 'confirmada' "$TMPD/page" && ok "C1 código certo: identidade confirmada" || falha "C1 código certo -> $st"
if espera "$SID" TASK_STATE_COMPLETED; then
  rpc "{\"id\":\"$SID\"}" GetTask >/dev/null
  V=$(body | jq -c '.result.artifacts[0].parts[0].data // empty')
  [ "$(printf '%s' "$V" | jq -r '.verified')" = true ] && [ "$(printf '%s' "$V" | jq -r '.mechanism')" = otp ] \
    && ok "C2 COMPLETED sem mensagem do chamador; veredito $V" || falha "C2 veredito inesperado: $V"
else falha "C2 a task não concluiu depois da prova (estado $(estado "$SID"))"; fi
ev() { rcli hget "$TENANT:ctx:journey:$SID" "core.journey.identity.otp.$1" | jq -r '.value // empty' 2>/dev/null; }
if [ "$(ev status)" = verified ] && [ "$(ev customer_id)" = "$CID" ] && [ "$(ev proven_in_session)" = "$SID" ]; then
  ok "C3 evidência na journey: otp verified, deste cliente, desta sessão"
else falha "C3 evidência: status=$(ev status) customer=$(ev customer_id) sessão=$(ev proven_in_session)"; fi
CLI_FALA=$(rcli xrange "session:$SID:stream" - + | grep -A1 -x 'author_role' | grep -cx customer || true)
[ "${CLI_FALA:-0}" = 0 ] && ok "C4 a prova acordou o fluxo por SINAL: nenhuma fala de cliente no stream"   || falha "C4 $CLI_FALA entrada(s) de cliente no stream — a prova virou fala"
[ "$(pagina GET "$LINK")" = 410 ] && ok "U1 o link é de uso único (410 depois)" || falha "U1 o link ainda responde depois de usado"

# ── S · o stream fica aberto em AUTH_REQUIRED ─────────────────────────────────
echo ""; echo "── S · pelo stream ────────────────────────────────────────────────────"
SSE="$TMPD/sse"
curl -sN --max-time 90 -X POST "$GW/a2a/$SLUG" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -H "Authorization: Bearer $CRED" -d '{"jsonrpc":"2.0","id":"s19","method":"SendStreamingMessage","params":{"message":{"role":"ROLE_USER","messageId":"m2","parts":[{"text":"de novo"}]}}}' > "$SSE" &
CURL=$!
SLINK=""
for _ in $(seq 1 60); do
  SLINK=$(grep '^data: ' "$SSE" | sed 's/^data: //' | jq -r '.. | .authorization?.url? // empty' 2>/dev/null | head -1)
  [ -n "$SLINK" ] && break; sleep 0.5
done
sleep 3
if [ -n "$SLINK" ] && kill -0 "$CURL" 2>/dev/null; then ok "S1 AUTH_REQUIRED pelo stream, e o stream continua aberto"
else falha "S1 link no stream: ${SLINK:-nenhum}; stream $(kill -0 "$CURL" 2>/dev/null && echo aberto || echo FECHADO)"; fi
if [ -n "$SLINK" ] && provar "$SLINK"; then
  wait "$CURL" 2>/dev/null
  ULT=$(grep '^data: ' "$SSE" | tail -1 | sed 's/^data: //' | jq -r '.result.statusUpdate.status.state // empty')
  [ "$ULT" = TASK_STATE_COMPLETED ] && ok "S2 depois da prova o MESMO stream terminou em COMPLETED" || falha "S2 último estado do stream: ${ULT:-nenhum}"
else falha "S2 a prova pelo link do stream falhou"; kill "$CURL" 2>/dev/null; fi

# ── N · o chamador diz "não": a mensagem não é prova ──────────────────────────
echo ""; echo "── N · o chamador responde em AUTH_REQUIRED ───────────────────────────"
NID=$(nova)
if espera "$NID" TASK_STATE_AUTH_REQUIRED; then
  rpc "{\"message\":{\"role\":\"ROLE_USER\",\"messageId\":\"m3\",\"taskId\":\"$NID\",\"parts\":[{\"text\":\"a pessoa nao quer confirmar\"}]},\"configuration\":{\"returnImmediately\":true}}" SendMessage >/dev/null
  if [ -n "$(body | jq -r '.result.task.id // empty')" ]; then ok "N1 a resposta em AUTH_REQUIRED é aceita (spec § 7.6.1)"
  else falha "N1 resposta recusada: $(body | head -c 200)"; fi
  if espera "$NID" TASK_STATE_FAILED; then
    rpc "{\"id\":\"$NID\"}" GetTask >/dev/null
    [ "$(body | jq -r '.result.metadata.plughub.issue_status // empty')" = "identidade nao confirmada" ] \
      && ok "N2 FAILED: a mensagem do agente não vale como prova" || falha "N2 desfecho: $(body | jq -c '.result.metadata' | head -c 200)"
    # testemunha do C4: a resposta do chamador É fala de cliente no stream — se o instrumento não a
    # vê aqui, o "nenhuma fala" do C4 não mediu nada
    N_FALA=$(rcli xrange "session:$NID:stream" - + | grep -A1 -x 'author_role' | grep -cx customer || true)
    [ "${N_FALA:-0}" -ge 1 ] && ok "N3 testemunha do C4: a resposta do chamador aparece como fala ($N_FALA)"       || falha "N3 o instrumento do C4 não vê nem a fala real do chamador — C4 não mede"
  else falha "N2 a task não falhou (estado $(estado "$NID"))"; fi
else falha "N1 a task N não chegou a AUTH_REQUIRED (estado $(estado "$NID"))"; fi

# ── R · telefone que não é o do cadastro ─────────────────────────────────────
echo ""; echo "── R · âncora que não é a autoritativa ────────────────────────────────"
deploy "+55119$(date +%s%N | cut -c10-17)"
RID=$(nova)
if espera "$RID" TASK_STATE_FAILED; then
  rpc "{\"id\":\"$RID\"}" GetTask >/dev/null
  [ "$(body | jq -r '.result.metadata.plughub.issue_status // empty')" = "link de prova recusado" ] \
    && ok "R1 link recusado (âncora não autoritativa) e o fluxo falha nomeando" || falha "R1 desfecho: $(body | jq -c '.result.metadata' | head -c 200)"
else falha "R1 a task não falhou (estado $(estado "$RID"))"; fi
docker logs --since 60s "$(docker ps --format '{{.Names}}' | grep -m1 'mcp-server-plughub')" 2>&1 | grep -q 'anchor_not_authoritative' \
  && ok "R2 a recusa está nomeada no log do mcp-server" || falha "R2 a recusa não aparece nomeada no log"
limpa

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo " OK"; exit 0
