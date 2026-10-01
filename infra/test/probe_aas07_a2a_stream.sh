#!/usr/bin/env bash
# probe_aas07_a2a_stream.sh — 2026-10-01  (AAS-07 · adr-a2a-server-binding A5; spec A2A v1.0 § 3.1.2,
# § 3.1.6, § 9.4.2)
#
# PERGUNTA: o canal a2a TRANSMITE a task (SSE) do jeito que a spec v1.0 manda — primeiro o `Task`,
# depois as atualizações, e o servidor FECHA o stream quando a task para (terminal ou interrompida)
# — e quem não pode assinar recebe JSON-RPC comum, não um stream?
#
# RAMOS
#   S1  `SendStreamingMessage` (task nova): `text/event-stream`; todo evento é resposta JSON-RPC com
#       o `id` do pedido; o primeiro é `task`; o último é `statusUpdate` INPUT_REQUIRED com o menu;
#       e o SERVIDOR fecha (curl termina antes do próprio teto).
#   S2  `SendStreamingMessage` (continuação, "2"): `artifactUpdate` com o resultado ANTES do
#       `statusUpdate` COMPLETED, e fecha.
#   S3  `SubscribeToTask` em task terminal → JSON-RPC -32004, sem SSE.
#   S4  `SubscribeToTask` em task interrompida → um evento (`task` INPUT_REQUIRED) e fecha.
#   S5  `SubscribeToTask` por OUTRO principal → -32001 (igual a inexistente), sem SSE.
#   S6  o card anuncia `capabilities.streaming: true` e o teto do stream (extensão task-lifetime).
#
# NÃO MEDIDO AO VIVO (declarado): a fala do agente NO MEIO do trabalho (`statusUpdate` WORKING com
# a mensagem) e o batimento/teto do stream ocioso — a fixture sem LLM não fala antes do menu, e o
# teto é de 600 s; guardados por `test_aas07_a2a_stream.py`.
# Fixtures: as da AAS-06 (`_a2a_fixture.sh`). EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

echo "════════════════════════════════════════════════════════════════════"
echo " o canal a2a transmite a task como a spec v1.0 manda — e fecha quando ela para?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

. infra/test/_a2a_fixture.sh

SSE=$(mktemp); HDR=$(mktemp); trap 'rm -f "$BODY" "$SSE" "$HDR"' EXIT
stream() {  # $1 credencial $2 método $3 params → SSE em $SSE, cabeçalhos em $HDR; ecoa o rc do curl
  curl -sN --max-time 40 -D "$HDR" -o "$SSE" -X POST "$GW/a2a/$SLUG" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
    -H "Authorization: Bearer $1" -d "{\"jsonrpc\":\"2.0\",\"id\":\"p7\",\"method\":\"$2\",\"params\":$3}"
  echo $?; }
eventos() { grep '^data: ' "$SSE" | sed 's/^data: //'; }
tipos() { eventos | jq -r '.result | keys[0]' | tr '\n' ' '; }
ctype() { grep -i '^content-type:' "$HDR" | tr -d '\r' | cut -d' ' -f2 | cut -d';' -f1; }

# ── S1 ───────────────────────────────────────────────────────────────────────
echo ""; echo "── S · STREAMING ──────────────────────────────────────────────────────"
inicio=$(date +%s)
rc=$(stream "$CRED" SendStreamingMessage '{"message":{"role":"ROLE_USER","messageId":"s1","parts":[{"text":"segunda via"},{"data":{"linha":"11999990000"}}]}}')
dur=$(( $(date +%s) - inicio ))
T=$(eventos | head -1 | jq -r '.result.task.id // empty')
ids=$(eventos | jq -r .id | sort -u | tr '\n' ' ')
# o prompt sai UMA vez: medido ao vivo, ele chega ao stream antes do `menu:waiting` e saía como
# progresso WORKING e de novo no INPUT_REQUIRED
n_prompt=$(eventos | grep -c '2\. PIX')
ult=$(eventos | tail -1 | jq -c '{k: (.result|keys[0]), s: .result.statusUpdate.status.state, t: (.result.statusUpdate.status.message.parts[0].text // "")}')
if [ -z "$T" ]; then incon "S1 a task não nasceu (rc=$rc ct=$(ctype)): $(head -c 300 "$SSE")"
elif [ "$(ctype)" = text/event-stream ] && [ "$ids" = "p7 " ] && [ "$(tipos | cut -d' ' -f1)" = task ] \
     && [ "$(echo "$ult" | jq -r .s)" = TASK_STATE_INPUT_REQUIRED ] && echo "$ult" | jq -r .t | grep -q '2. PIX' \
     && [ "$rc" = 0 ] && [ "$dur" -lt 35 ] && [ "$n_prompt" = 1 ]; then
  ok "S1 SSE: primeiro o Task, por último INPUT_REQUIRED com o menu (uma vez só), id ecoado, e o servidor fechou (${dur}s)"
else falha "S1 rc=$rc ct=$(ctype) ids=[$ids] tipos=[$(tipos)] prompt=${n_prompt}x último=$ult (${dur}s)"; fi

# ── S2 ───────────────────────────────────────────────────────────────────────
if [ -n "$T" ]; then
  rc=$(stream "$CRED" SendStreamingMessage "{\"message\":{\"role\":\"ROLE_USER\",\"messageId\":\"s2\",\"taskId\":\"$T\",\"parts\":[{\"text\":\"2\"}]}}")
  fim=$(tipos | awk '{print $(NF-1), $NF}')
  art=$(eventos | jq -c 'select(.result.artifactUpdate) | .result.artifactUpdate.artifact.parts[0].data' | head -1)
  st=$(eventos | tail -1 | jq -r '.result.statusUpdate.status.state // empty')
  [ "$rc" = 0 ] && [ "$fim" = "artifactUpdate statusUpdate" ] && [ "$art" = '"pix"' ] && [ "$st" = TASK_STATE_COMPLETED ] \
    && ok "S2 continuação: artifactUpdate com o resultado ANTES do statusUpdate COMPLETED, e fecha" \
    || falha "S2 rc=$rc tipos=[$(tipos)] artefato=$art estado=$st"
  rc=$(stream "$CRED" SubscribeToTask "{\"id\":\"$T\"}")
  [ "$(ctype)" = application/json ] && [ "$(jq -r .error.code "$SSE")" = -32004 ] \
    && ok "S3 SubscribeToTask em task terminal -> JSON-RPC -32004, sem SSE" || falha "S3 ct=$(ctype) $(head -c 200 "$SSE")"
else incon "S2/S3 sem task (S1)"; fi

# ── S4/S5 ────────────────────────────────────────────────────────────────────
send "$CRED" '{"role":"ROLE_USER","messageId":"s4","parts":[{"data":{"linha":"1"}}]}' >/dev/null
T2=$(body | jq -r '.result.task.id // empty')
if [ -n "$T2" ] && [ "$(body | jq -r .result.task.status.state)" = TASK_STATE_INPUT_REQUIRED ]; then
  rc=$(stream "$CRED" SubscribeToTask "{\"id\":\"$T2\"}")
  [ "$rc" = 0 ] && [ "$(tipos)" = "task " ] && [ "$(eventos | jq -r .result.task.status.state)" = TASK_STATE_INPUT_REQUIRED ] \
    && ok "S4 SubscribeToTask em task interrompida -> o Task e fecha" || falha "S4 rc=$rc tipos=[$(tipos)]"
  stream "$CRED2" SubscribeToTask "{\"id\":\"$T2\"}" >/dev/null
  a=$(jq -r .error.code "$SSE" 2>/dev/null)
  stream "$CRED2" SubscribeToTask '{"id":"task-que-nao-existe"}' >/dev/null
  b=$(jq -r .error.code "$SSE" 2>/dev/null)
  [ "$a" = -32001 ] && [ "$b" = -32001 ] && ok "S5 outro principal não assina a task (-32001, igual a inexistente)" \
    || falha "S5 alheia=$a inexistente=$b"
else incon "S4/S5 sem task interrompida: $(body | head -c 200)"; fi

# ── S6 ───────────────────────────────────────────────────────────────────────
$C -o "$BODY" "$GW/a2a/$SLUG/.well-known/agent-card.json" >/dev/null
r=$(body | jq -c '{s: .capabilities.streaming, t: (.capabilities.extensions[]? | select(.uri=="urn:plughub:a2a:extension:task-lifetime:v1") | .params.stream_ceiling_s)}')
[ "$(echo "$r" | jq -r .s)" = true ] && [ "$(echo "$r" | jq -r .t)" != null ] \
  && ok "S6 o card anuncia streaming e o teto do stream ($r)" || falha "S6 card=$r"

limpa
echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — a task é transmitida como a spec manda, e o stream fecha quando ela para."
exit 0
