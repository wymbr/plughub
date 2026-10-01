#!/usr/bin/env bash
# probe_aas06_a2a_tasks.sh — 2026-10-01  (AAS-06 · adr-a2a-server-binding D4, D5, D8, D15)
#
# PERGUNTA: um agente de fora, com credencial `partner`, conversa com um pool pelo canal `a2a` do
# começo ao fim — o pedido entra como contrato, o menu volta como INPUT_REQUIRED, a resposta
# fecha com o artefato conferido — e a task é SÓ dele, deduzida dos fatos da sessão?
#
# RAMOS
#   A  CRIAÇÃO: pedido fora do `input_schema` → -32602 nomeando o campo; dentro, `SendMessage`
#      bloqueante assenta em INPUT_REQUIRED com o menu numerado E o JSON Schema da resposta; o
#      pedido está em `core.a2a.request` e o principal no meta da sessão.
#   B  CONTINUAÇÃO: "2" responde o menu → COMPLETED com o artefato (contrato válido); continuar
#      depois do fim → -32004.
#   C  CONTEXTO: task nova no mesmo contextId pelo mesmo principal → aceita; contextId inventado e
#      contextId de OUTRO principal → a mesma recusa (-32602), nunca adotado.
#   D  CANCELAR: `CancelTask` → CANCELED, `closed_recorded = caller_cancel`; cancelar de novo →
#      -32002.
#   E  DONO: `ListTasks` pelo contextId lista as duas; OUTRO principal no mesmo pool não lê a task
#      (-32001, a mesma resposta de task inexistente).
#   F  CARD: publica os prazos do executor (extensão task-lifetime).
#
# NÃO MEDIDO AO VIVO (declarado): o carimbo do principal no `AuditRecord` — a única borda que
# audita é o `invoke` do external-mcp, sem `MCP_SERVER_*_URL` neste deploy (CAP-07); guardado por
# `invoke-audit.test.ts` e `test_segment_enricher.py`. E o menu MASCARADO recusado no canal: é o
# portão do `notification_send` (NIV-03), guardado por `masked-menu-channel-gate.test.ts` com a
# capacidade nova em `a2a-channel.test.ts`.
#
# Fixtures: pool `probe_aas06_a2a` (endpoint `probe-aas06`), skill `skill_probe_aas06` (menu de
# botão → complete com `result_from`, sem LLM), usuário `probe-aas06-adm@` (admin) e dois
# principais. EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

echo "════════════════════════════════════════════════════════════════════"
echo " um agente de fora conversa com o pool pelo canal a2a — e a task é só dele?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

. infra/test/_a2a_fixture.sh

# ── A ────────────────────────────────────────────────────────────────────────
echo ""; echo "── A · CRIAÇÃO ────────────────────────────────────────────────────────"
st=$(send "$CRED" '{"role":"ROLE_USER","messageId":"a1","parts":[{"text":"quero a segunda via"}]}')
[ "$st" = 200 ] && [ "$(body | jq -r .error.code)" = -32602 ] && body | grep -q linha \
  && ok "A1 pedido fora do input_schema -> -32602 nomeando o campo" || falha "A1 -> $st $(body | head -c 200)"
st=$(send "$CRED" '{"role":"ROLE_USER","messageId":"a2","parts":[{"text":"quero a segunda via"},{"data":{"linha":"11999990000"}}]}')
T1=$(body | jq -r '.result.task.id // empty'); CTX=$(body | jq -r '.result.task.contextId // empty')
s=$(body | jq -r '.result.task.status.state // empty')
txt=$(body | jq -r '.result.task.status.message.parts[0].text // empty')
enum=$(body | jq -c '.result.task.status.message.parts[1].data.response_schema.properties.choice.enum // empty')
if [ -z "$T1" ]; then incon "A2 a task não nasceu: $st $(body | head -c 300)"
elif [ "$s" = TASK_STATE_INPUT_REQUIRED ] && echo "$txt" | grep -q '2. PIX' && [ "$enum" = '["boleto","pix"]' ] && [ "$CTX" = "$T1" ]; then
  ok "A2 SendMessage bloqueante assenta em INPUT_REQUIRED: menu numerado + JSON Schema da resposta"
else falha "A2 estado=$s enum=$enum texto=$(echo "$txt" | head -c 80)"; fi
if [ -n "$T1" ]; then
  reqv=$(rcli HGET "$TENANT:ctx:$T1" core.a2a.request | jq -c '.value.data // empty' 2>/dev/null)
  [ "$reqv" = '{"linha":"11999990000"}' ] && ok "A3 o pedido está em core.a2a.request (entrada de contrato)" || falha "A3 ctx=$reqv"
  m=$(rcli GET "session:$T1:meta" | jq -c '{c:.channel, p:.a2a_principal_id}' 2>/dev/null)
  [ "$m" = "{\"c\":\"a2a\",\"p\":\"$PID\"}" ] && ok "A4 meta da sessão: canal a2a e o principal" || falha "A4 meta=$m (esperado principal $PID)"
fi

# ── B ────────────────────────────────────────────────────────────────────────
echo ""; echo "── B · CONTINUAÇÃO ────────────────────────────────────────────────────"
if [ -n "$T1" ]; then
  send "$CRED" "{\"role\":\"ROLE_USER\",\"messageId\":\"b1\",\"taskId\":\"$T1\",\"parts\":[{\"text\":\"2\"}]}" >/dev/null
  s=$(body | jq -r '.result.task.status.state // .error.code')
  [ "$s" = TASK_STATE_COMPLETED ] || { espera "$CRED" "$T1" TASK_STATE_COMPLETED; rpc "$CRED" "{\"id\":\"$T1\"}" GetTask >/dev/null; }
  r=$(body | jq -c '(.result.task // .result) | {s:.status.state, a:.artifacts[0].parts[0].data, c:.metadata.plughub.contract.valid}')
  [ "$r" = '{"s":"TASK_STATE_COMPLETED","a":"pix","c":true}' ] && ok "B1 \"2\" responde o menu -> COMPLETED com o artefato conferido" || falha "B1 -> $r"
  send "$CRED" "{\"role\":\"ROLE_USER\",\"messageId\":\"b2\",\"taskId\":\"$T1\",\"parts\":[{\"text\":\"de novo\"}]}" >/dev/null
  [ "$(body | jq -r .error.code)" = -32004 ] && ok "B2 continuar depois do fim -> -32004 (é task nova no contexto)" || falha "B2 -> $(body | head -c 200)"
else incon "B sem task (A2)"; fi

# ── C ────────────────────────────────────────────────────────────────────────
echo ""; echo "── C · CONTEXTO ───────────────────────────────────────────────────────"
T2=""
if [ -n "$CTX" ]; then
  send "$CRED" "{\"role\":\"ROLE_USER\",\"messageId\":\"c1\",\"contextId\":\"$CTX\",\"parts\":[{\"data\":{\"linha\":\"1\"}}]}" '{"returnImmediately":true}' >/dev/null
  T2=$(body | jq -r '.result.task.id // empty'); c2=$(body | jq -r '.result.task.contextId // empty')
  [ -n "$T2" ] && [ "$T2" != "$T1" ] && [ "$c2" = "$CTX" ] && ok "C1 task nova no mesmo contextId, pelo mesmo principal" || falha "C1 -> $(body | head -c 200)"
  # id ALEATÓRIO por rodada: com um literal fixo, a rodada da mutação M2 (que adota contextId
  # alheio) o registrava como deste principal por 30 dias, e o C2 passava a reprovar sem mutação
  send "$CRED" "{\"role\":\"ROLE_USER\",\"messageId\":\"c2\",\"contextId\":\"ctx-inventado-$(date +%s%N)\",\"parts\":[{\"data\":{\"linha\":\"1\"}}]}" >/dev/null
  e1=$(body | jq -c .error)
  send "$CRED2" "{\"role\":\"ROLE_USER\",\"messageId\":\"c3\",\"contextId\":\"$CTX\",\"parts\":[{\"data\":{\"linha\":\"1\"}}]}" >/dev/null
  e2=$(body | jq -c .error)
  [ "$(echo "$e1" | jq -r .code)" = -32602 ] && [ "$e1" = "$e2" ] \
    && ok "C2 contextId inventado e contextId de outro principal -> a MESMA recusa" || falha "C2 inventado=$e1 alheio=$e2"
else incon "C sem contexto (A2)"; fi

# ── D ────────────────────────────────────────────────────────────────────────
echo ""; echo "── D · CANCELAR ───────────────────────────────────────────────────────"
if [ -n "$T2" ]; then
  espera "$CRED" "$T2" TASK_STATE_INPUT_REQUIRED || true
  rpc "$CRED" "{\"id\":\"$T2\"}" CancelTask >/dev/null
  espera "$CRED" "$T2" TASK_STATE_CANCELED && cr=$(rcli GET "session:$T2:closed_recorded") || cr="(não chegou)"
  [ "$cr" = caller_cancel ] && ok "D1 CancelTask -> CANCELED, closed_recorded=caller_cancel" || falha "D1 estado=$(estado "$CRED" "$T2") closed=$cr"
  rpc "$CRED" "{\"id\":\"$T2\"}" CancelTask >/dev/null
  [ "$(body | jq -r .error.code)" = -32002 ] && ok "D2 cancelar de novo -> -32002" || falha "D2 -> $(body | head -c 200)"
  # D3/D4 medem o que o Redis da task NÃO mostra, e que enganou a primeira versão deste probe:
  # o menu estacionado tem de SAIR com o fechamento (não no prazo de 120 s do menu), e o registro
  # durável tem de dizer `caller_cancel`, não `flow_complete`.
  saiu=""
  for _ in $(seq 1 30); do
    [ -z "$(rcli EXISTS "session:$T2:parked_run:${POOL}-001" | grep -v '^0$')" ] && [ -z "$(rcli HKEYS "menu:waiting:$T2")" ] \
      && { saiu=1; break; }
    sleep 0.5
  done
  [ -n "$saiu" ] && ok "D3 o menu estacionado sai com o cancelamento (não espera o prazo do menu)" \
    || falha "D3 a conversa segue estacionada 15 s depois do cancelamento (instância presa até o prazo)"
  cr_ch=""
  for _ in $(seq 1 30); do
    cr_ch=$(docker exec "${CH_CONTAINER:-plughub-demo-clickhouse-1}" clickhouse-client -q \
      "select close_reason from ${CH_DB:-plughub_demo}.sessions FINAL where session_id='$T2' format TSV" 2>/dev/null)
    [ -n "$cr_ch" ] && break; sleep 1
  done
  [ "$cr_ch" = caller_cancel ] && ok "D4 registro durável: sessions.close_reason = caller_cancel" \
    || { [ -z "$cr_ch" ] && incon "D4 a sessão não chegou ao ClickHouse em 30 s" || falha "D4 sessions.close_reason = $cr_ch"; }
  [ "$(estado "$CRED" "$T2")" = TASK_STATE_CANCELED ] && ok "D5 depois do fluxo encerrado a task SEGUE cancelada (a primeira causa manda)" \
    || falha "D5 a task virou $(estado "$CRED" "$T2")"
else incon "D sem segunda task (C1)"; fi

# ── E ────────────────────────────────────────────────────────────────────────
echo ""; echo "── E · DONO ───────────────────────────────────────────────────────────"
if [ -n "$T1" ] && [ -n "$T2" ]; then
  rpc "$CRED" "{\"contextId\":\"$CTX\"}" ListTasks >/dev/null
  ids=$(body | jq -c '[.result.tasks[].id] | sort')
  [ "$ids" = "$(jq -cn --arg a "$T1" --arg b "$T2" '[$a,$b] | sort')" ] && ok "E1 ListTasks pelo contextId -> as duas tasks" || falha "E1 -> $ids"
  a=$(estado "$CRED2" "$T1"); b=$(estado "$CRED2" "task-que-nao-existe")
  [ "$a" = -32001 ] && [ "$b" = -32001 ] && ok "E2 outro principal no mesmo pool não lê a task (-32001, igual a inexistente)" || falha "E2 alheia=$a inexistente=$b"
else incon "E sem as duas tasks"; fi

# ── F ────────────────────────────────────────────────────────────────────────
echo ""; echo "── F · CARD ───────────────────────────────────────────────────────────"
st=$($C -o "$BODY" -w '%{http_code}' "$GW/a2a/$SLUG/.well-known/agent-card.json")
tl=$(body | jq -c '.capabilities.extensions[]? | select(.uri=="urn:plughub:a2a:extension:task-lifetime:v1") | .params.blocking_ceiling_s')
[ "$st" = 200 ] && [ -n "$tl" ] && ok "F1 o card publica os prazos do executor (teto ${tl}s)" || falha "F1 -> $st $(body | head -c 200)"

limpa
echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — o agente de fora conversa do pedido ao artefato, e a task é só dele."
exit 0
