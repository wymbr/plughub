# _a2a_fixture.sh — fixtures e chamadas do canal a2a, SOURCED pelos probes AAS-06 e AAS-07.
# Peça, não unidade medida: monta (idempotente) skill `skill_probe_aas06` (menu de botão →
# complete com result_from, sem LLM), pool `probe_aas06_a2a` + endpoint `probe-aas06`, usuário
# `probe-aas06-adm@` e dois principais; define rpc/send/estado/espera/limpa e roda `limpa`.
# Sai 2 (INCONCLUSIVO) se não monta. Quem sourceia já fez `cd` para a raiz e checou jq/curl/docker.

REG="${REGISTRY:-http://localhost:3300}"
GW="${GATEWAY:-http://localhost:8010}"
AUTHB="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS_C="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_aas06_a2a"; SLUG="probe-aas06"; SKILL="skill_probe_aas06"
PASS="probe_aas06_Senha!1"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }


C="curl -s --max-time 40"
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
body() { cat "$BODY"; }
login() { $C -X POST "$AUTHB/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'; }
req() { local extra=(); [ -n "${4:-}" ] && extra=(-d "$4")
  $C -o "$BODY" -w '%{http_code}' -X "$2" -H "Authorization: Bearer $1" -H "x-tenant-id: $TENANT" \
     -H 'Content-Type: application/json' "${extra[@]}" "$3"; }
rcli() { docker exec "$REDIS_C" redis-cli "$@" 2>/dev/null; }

ADMIN=$(login admin@plughub.local changeme_admin)
[ -n "$ADMIN" ] || { echo "INCONCLUSIVO — login do admin@ falhou"; exit 2; }

# ── fixtures ─────────────────────────────────────────────────────────────────
DESC='{"display_name":"probe AAS-06","description":"Fixture do probe AAS-06.","discoverable":true,
 "input_schema":{"type":"object","required":["linha"],"properties":{"linha":{"type":"string"}}},
 "output_schema":{"type":"string","enum":["boleto","pix"]},
 "skills":[{"id":"segunda_via","name":"segunda via","description":"segunda via de fatura"}],
 "principal_kinds":["partner"]}'
FLOW='{"entry":"pergunta","steps":[
 {"id":"pergunta","type":"menu","prompt":"Como prefere pagar?","interaction":"button",
  "options":[{"id":"boleto","label":"Boleto"},{"id":"pix","label":"PIX"}],
  "output_as":"escolha","timeout_s":120,"on_success":"fim","on_failure":"falha","on_timeout":"falha"},
 {"id":"fim","type":"complete","outcome":"resolved","result_from":"escolha"},
 {"id":"falha","type":"complete","outcome":"failed","issue_status":"sem resposta ao menu"}]}'
st=$(req "$ADMIN" PUT "$REG/v1/skills/$SKILL" "{\"skill_id\":\"$SKILL\",\"name\":\"probe AAS-06\",\"version\":\"1.0\",\"description\":\"Fixture do probe AAS-06: menu e complete com result_from.\",\"classification\":{\"type\":\"orchestrator\"},\"flow\":$FLOW}")
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — skill de fixture -> $st $(body | head -c 300)"; exit 2; }
if [ "$(req "$ADMIN" GET "$REG/v1/pools/$POOL")" = 404 ]; then
  st=$(req "$ADMIN" POST "$REG/v1/pools" "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":10,\"description\":\"fixture do probe AAS-06\",\"a2a\":$DESC}")
else st=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL" "{\"channel_types\":[\"a2a\"],\"max_concurrent_sessions\":10,\"a2a\":$DESC}"); fi
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — pool de fixture -> $st $(body | head -c 300)"; exit 2; }
req "$ADMIN" GET "$REG/v1/pools/$POOL/slots" >/dev/null
if [ "$(body | jq -r '.slots.current.skill_id // empty')" != "$SKILL" ] || [ "${AAS06_REDEPLOY:-}" = 1 ]; then
  s1=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL/slots/next" "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  s2=$(req "$ADMIN" POST "$REG/v1/pools/$POOL/promote" '{}')
  [ "${s1:0:1}" = 2 ] && [ "${s2:0:1}" = 2 ] || { echo "INCONCLUSIVO — deploy: set-next $s1 promote $s2 $(body | head -c 300)"; exit 2; }
fi
req "$ADMIN" GET "$REG/v1/channel-endpoints?channel=a2a" >/dev/null
EPID=$(body | jq -r --arg s "$SLUG" '(.endpoints // .)[]? | select(.identifier==$s) | .id' | head -1)
if [ -z "$EPID" ]; then
  st=$(req "$ADMIN" POST "$REG/v1/channel-endpoints" "{\"channel\":\"a2a\",\"identifier\":\"$SLUG\",\"pool_id\":\"$POOL\",\"display_name\":\"probe AAS-06\"}")
  [ "$st" = 201 ] || { echo "INCONCLUSIVO — endpoint -> $st $(body | head -c 200)"; exit 2; }
else req "$ADMIN" PUT "$REG/v1/channel-endpoints/$EPID" '{"active":true}' >/dev/null; fi

st=$(req "$ADMIN" POST "$AUTHB/auth/users" "{\"tenant_id\":\"$TENANT\",\"email\":\"probe-aas06-adm@plughub.local\",\"password\":\"$PASS\",\"name\":\"probe AAS-06\",\"roles\":[\"admin\"],\"accessible_pools\":[]}")
[ "$st" = 201 ] || [ "$st" = 409 ] || { echo "INCONCLUSIVO — usuário -> $st $(body | head -c 200)"; exit 2; }
MASTER=$(login probe-aas06-adm@plughub.local "$PASS")
[ -n "$MASTER" ] || { echo "INCONCLUSIVO — login do usuário de fixture"; exit 2; }
P="$AUTHB/auth/v1/agent-principals"
principal() {  # $1 nome → ecoa a credencial (rotacionada a cada rodada)
  req "$MASTER" GET "$P" >/dev/null
  local id; id=$(body | jq -r --arg n "$1" '.[]? | select(.display_name==$n) | .agent_principal_id' | head -1)
  if [ -z "$id" ]; then req "$MASTER" POST "$P" "{\"display_name\":\"$1\",\"allowed_pools\":[\"$POOL\"]}" >/dev/null
  else req "$MASTER" PUT "$P/$id" "{\"allowed_pools\":[\"$POOL\"],\"active\":true}" >/dev/null
       req "$MASTER" POST "$P/$id/credential" >/dev/null; fi
  body | jq -r '.credential // empty'
}
CRED=$(principal "probe AAS-06")
CRED2=$(principal "probe AAS-06 outro")
[ -n "$CRED" ] && [ -n "$CRED2" ] || { echo "INCONCLUSIVO — credenciais dos principais"; exit 2; }
PID=$(req "$MASTER" GET "$P" >/dev/null; body | jq -r '.[] | select(.display_name=="probe AAS-06") | .agent_principal_id')

rpc() {  # $1 credencial  $2 JSON do params  $3 método
  $C -o "$BODY" -w '%{http_code}' -X POST "$GW/a2a/$SLUG" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $1" -d "{\"jsonrpc\":\"2.0\",\"id\":\"p6\",\"method\":\"$3\",\"params\":$2}"; }
send() { rpc "$1" "{\"message\":$2${3:+,\"configuration\":$3}}" SendMessage; }
estado() { rpc "$1" "{\"id\":\"$2\"}" GetTask >/dev/null; body | jq -r '.result.status.state // .error.code'; }
espera() {  # $1 credencial $2 id $3 estado → 0 se chegou em 30 s
  for _ in $(seq 1 60); do [ "$(estado "$1" "$2")" = "$3" ] && return 0; sleep 0.5; done; return 1; }
limpa() {  # cancela o que rodadas anteriores (ou uma bateria de mutação) deixaram vivo no pool:
  # task esperando menu ocupa instância, e a próxima rodada ficaria em SUBMITTED por isso — o
  # vermelho seria da sobra, não do que o ramo mede
  local cr id
  for cr in "$CRED" "$CRED2"; do
    rpc "$cr" '{"pageSize":100}' ListTasks >/dev/null
    for id in $(body | jq -r '.result.tasks[]? | select(.status.state|test("SUBMITTED|WORKING|INPUT_REQUIRED")) | .id'); do
      rpc "$cr" "{\"id\":\"$id\"}" CancelTask >/dev/null
    done
  done
}
limpa
