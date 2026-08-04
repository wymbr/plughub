#!/usr/bin/env bash
# smoke_resume_terminal_once.sh — Fase F (D7) do ADR
# `adr-work-item-requeue-and-agent-affinity.md`.
#
# Prova, contra Redis REAL e HTTP REAL, que um resume é terminal UMA VEZ:
#
#   A  dois resumes CONCORRENTES sobre o mesmo token → exatamente um 2xx e
#      exatamente um 409. Contra o código anterior à Fase F os dois saíam 200,
#      publicavam `session_resumed` para o mesmo passo suspenso, e o fluxo podia
#      seguir o ramo de entrega E o `on_timeout`;
#   B  o terceiro, SEQUENCIAL, também é 409 — e com a CAUSA nomeada. Antes era
#      404 "token não encontrado ou expirado": a tela dizia ao agente que a
#      sessão dele tinha vencido quando o que houve foi outro encerramento;
#   C  o registro terminal existe no Redis, com causa do vocabulário conhecido, e
#      o token foi consumido UMA vez.
#
# POR QUE ESTE SMOKE E NÃO SÓ O PYTEST. A suíte
# `test_resume_terminal_once.py` (17) usa um fake com semântica NX escrita à mão:
# ela julga a LÓGICA. Aqui o `SET NX` é o do Redis, a exclusão atravessa dois
# processos curl de verdade, e o 409 passa pelo FastAPI. São perguntas
# diferentes, e a segunda já reprovou coisa que a primeira não vê (o detentor
# lido sem decodificar sairia como `b'agent'`).
#
# Pré-requisitos: demo no ar; pools formfill_demo{,_ia}; skill_formfill_demo_v1
# deployado; DialogForm dialog_formfill_demo publicado. Requer curl + jq.
#
# Uso (raiz do repo):
#   bash infra/test/smoke_resume_terminal_once.sh
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
UI="http://localhost:5174"
POOL_WH="formfill_demo_ia"
POOL_PULL="formfill_demo"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

echo "══ Fase F (D7) — resume terminal-uma-vez · tenant=$TENANT ══"

# ── PREFLIGHT — provar que cada instrumento responde ─────────────────────────
[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INSTRUMENTO — redis não respondeu PONG"; exit 3; }
command -v jq >/dev/null   || { echo "   ⛔ INSTRUMENTO — jq ausente"; exit 3; }
curl -fsS "$CG/health" >/dev/null 2>&1 || {
  echo "   ⛔ INSTRUMENTO — channel-gateway não respondeu em $CG/health"; exit 3; }

# ── 0) cria um item de trabalho real ────────────────────────────────────────
echo "0) disparando o workflow que parqueia o item ..."
RESP=$(curl -fsS -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_briefing_demo\"}}" \
  2>/dev/null)
SID=$(echo "$RESP" | jq -r '.session_id // empty')
[ -n "$SID" ] || {
  echo "   ⛔ INCONCLUSIVO — o trigger não devolveu session_id. Sem item, não há"
  echo "      o que encerrar; isto NÃO é 'a exclusão funciona'."
  echo "      Conferir: bash infra/test/smoke_formfill_renderer.sh"
  exit 2; }
echo "   session=$SID"

echo "1) aguardando o item parquear na fila $POOL_PULL ..."
FOUND=""
for _ in $(seq 1 15); do
  LIST=$(curl -fsS "$UI/api/work_queue/list?pools=$POOL_PULL" 2>/dev/null || true)
  FOUND=$(echo "$LIST" | jq -r --arg s "$SID" '.contacts[]? | select(.session_id==$s) | .session_id' 2>/dev/null || true)
  [ -n "$FOUND" ] && break
  sleep 1
done
[ -n "$FOUND" ] || {
  echo "   ⛔ INCONCLUSIVO — o item não apareceu na fila em 15 s."; exit 2; }
ok "item na fila"

# ── PREFLIGHT do LEITOR — o token tem de existir ANTES de qualquer comparação
LEDGER=$(R GET "${TENANT}:work_task:${SID}")
TOKEN=$(printf '%s' "$LEDGER" | jq -r '.resume_token // empty' 2>/dev/null)
[ -n "$TOKEN" ] || {
  echo "   ⛔ INSTRUMENTO — ledger sem resume_token para $SID. Sem token não há"
  echo "      corrida a construir, e dois 404 seriam 'iguais entre si'."; exit 3; }
[ -n "$(R HGET "${TENANT}:resume_tokens" "$TOKEN")" ] || {
  echo "   ⛔ INSTRUMENTO — o token do ledger não está em resume_tokens."; exit 3; }
ok "token vivo antes da corrida (preflight do leitor)"

# ── A) a corrida: dois resumes concorrentes ─────────────────────────────────
echo "2) disparando DOIS resumes concorrentes sobre o mesmo token ..."
curl -s -o "$TMP/b1" -w '%{http_code}' -X POST \
  "$CG/v1/channels/webhook/resume/$TOKEN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"answers\":{\"disposition\":\"resolved\"},\"source\":\"agent\"}}" \
  > "$TMP/c1" 2>/dev/null &
P1=$!
curl -s -o "$TMP/b2" -w '%{http_code}' -X POST \
  "$CG/v1/channels/webhook/resume/$TOKEN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"timeout\",\"source\":\"timeout_scanner\"}}" \
  > "$TMP/c2" 2>/dev/null &
P2=$!
wait $P1 $P2

C1=$(cat "$TMP/c1"); C2=$(cat "$TMP/c2")
echo "   códigos: $C1 e $C2"
N_OK=0; N_409=0; N_404=0
for C in "$C1" "$C2"; do
  case "$C" in
    2*)  N_OK=$((N_OK+1)) ;;
    409) N_409=$((N_409+1)) ;;
    404) N_404=$((N_404+1)) ;;
  esac
done

if [ "$N_OK" = 1 ] && [ "$N_409" = 1 ]; then
  ok "exatamente um venceu (2xx) e um foi recusado (409)"
elif [ "$N_OK" = 2 ]; then
  bad "OS DOIS venceram — a exclusão não aconteceu (é o defeito da D7 intacto)"
elif [ "$N_404" -gt 0 ]; then
  bad "houve 404 — a recusa saiu SEM NOME, que é o que a Fase F remove"
else
  bad "combinação inesperada: $C1 / $C2 (esperado um 2xx + um 409)"
fi

# Qual ramo a corrida exercitou. Não é critério de aprovação — os dois estão
# certos —, mas registrar QUAL saiu evita ler um caso sequencial como corrida.
BODY_409=""
[ "$C1" = "409" ] && BODY_409=$(cat "$TMP/b1")
[ "$C2" = "409" ] && BODY_409=$(cat "$TMP/b2")
if [ -n "$BODY_409" ]; then
  ST=$(echo "$BODY_409" | jq -r '.detail.state // empty' 2>/dev/null)
  BY=$(echo "$BODY_409" | jq -r '.detail.closed_by // empty' 2>/dev/null)
  case "$ST" in
    in_flight) echo "      ramo: in_flight (as duas dentro da janela) · por=$BY" ;;
    terminal)  echo "      ramo: terminal (a 1ª concluiu antes) · por=$BY" ;;
    *)         bad "o corpo do 409 não trouxe .detail.state — recusa sem forma" ;;
  esac
fi

# ── B) o terceiro, sequencial: 409 COM CAUSA, nunca 404 ────────────────────
echo "3) terceiro resume, sequencial, com o mesmo token ..."
C3=$(curl -s -o "$TMP/b3" -w '%{http_code}' -X POST \
  "$CG/v1/channels/webhook/resume/$TOKEN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"answers\":{\"disposition\":\"resolved\"}}}" 2>/dev/null)
CAUSE=$(jq -r '.detail.cause // empty' "$TMP/b3" 2>/dev/null)
CBY=$(jq -r '.detail.closed_by // empty' "$TMP/b3" 2>/dev/null)
if [ "$C3" = "409" ] && [ -n "$CAUSE" ]; then
  ok "409 com causa nomeada: cause=$CAUSE closed_by=$CBY"
elif [ "$C3" = "404" ]; then
  bad "404 — é exatamente a recusa-que-mente que a Fase F existe para remover"
else
  bad "esperado 409 com .detail.cause; veio $C3 corpo=$(head -c 200 "$TMP/b3")"
fi

# ── C) o rastro no Redis ───────────────────────────────────────────────────
echo "4) rastro no Redis ..."
TERM=$(R GET "${TENANT}:resume_terminal:${TOKEN}")
if [ -z "$TERM" ]; then
  bad "registro terminal AUSENTE — sem ele a próxima recusa volta a sair sem causa"
else
  TC=$(printf '%s' "$TERM" | jq -r '.cause // empty' 2>/dev/null)
  case "$TC" in
    task_done|acw_expired|acw_supervisor_closed)
      ok "registro terminal presente, causa do vocabulário: $TC" ;;
    *)
      bad "causa fora do vocabulário conhecido: '${TC:-∅}'" ;;
  esac
  TTL=$(R TTL "${TENANT}:resume_terminal:${TOKEN}")
  [ "${TTL:-0}" -gt 3600 ] \
    && ok "TTL do registro cobre o prazo do item (${TTL}s)" \
    || bad "TTL curto demais (${TTL:-?}s): a recusa perde a causa antes do prazo"
fi

if [ -z "$(R HGET "${TENANT}:resume_tokens" "$TOKEN")" ]; then
  ok "token consumido uma vez (some do hash)"
else
  bad "token AINDA no hash — o vencedor não consumiu, e o item segue retomável"
fi

LOCK=$(R GET "${TENANT}:resume_inflight:${TOKEN}")
[ -z "$LOCK" ] \
  && ok "lock liberado (nenhum resíduo de 45 s)" \
  || bad "lock RESIDUAL ($LOCK) — recusa legítima viraria indisponibilidade"

echo
echo "======================================"
echo "  passou=$pass  falhou=$fail"
[ "$fail" -eq 0 ] && { echo "  ✅ TERMINAL-UMA-VEZ"; exit 0; }
echo "  ❌ FALHOU"; exit 1
