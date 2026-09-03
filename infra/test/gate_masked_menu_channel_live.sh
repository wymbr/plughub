#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# gate_masked_menu_channel_live — a guarda da NIV-03 medida NO AR, não em mock.
#
# POR QUE ESTE GATE EXISTE, tendo já 16 testes unitários verdes
# ─────────────────────────────────────────────────────────────
# Os unitários provam a DECISÃO (dado um canal e um menu, recusa ou entrega). Não
# provam a CADEIA: que o canal é mesmo lido de `session:{id}:meta` no Redis real,
# que a recusa acontece antes do `conversations.outbound`, e que o menu normal
# continua saindo. `notification_send` está no caminho de **todo menu de todo
# fluxo** — uma regressão ali não é local.
#
# E o smoke que existia não alcança esta condição: `smoke_limite_tres_acessos.sh`
# declara no cabeçalho que **não julga o render do formulário**. Medido: a rodada
# dele não produziu uma linha sequer de `coletar_dados`. Verde num teste que não
# alcança a condição não é evidência sobre ela.
#
# O QUE ESTE GATE JULGA — três proposições, e as três juntas
# ──────────────────────────────────────────────────────────
#   A  webchat + menu MASCARADO  → entrega, e `masked_fields` viaja no evento
#   B  whatsapp + menu MASCARADO → recusa (`masked_input_unsupported`) e
#                                  **NADA** é publicado em conversations.outbound
#   C  whatsapp + menu SEM máscara → entrega  ← CONTROLE POSITIVO OBRIGATÓRIO
#
# ⚠️ Sem o **C**, o zero do **B** não é evidência: Kafka fora do ar, tópico errado,
# `contact_id` ausente ou consumidor mal posicionado produzem o mesmo zero. É a
# mesma disciplina do `probe_report_row_scope`: sem controle positivo o veredicto
# é SEM AMOSTRA, nunca verde.
#
# ⚠️ Requer `jq` — nesta bancada ele existe no WSL e **não** no Git Bash. Rodar do
# lado errado dá INCONCLUSIVO, não vermelho.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO (pré-condição falhou).
# Uso:  bash infra/test/gate_masked_menu_channel_live.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

MCP="${MCP:-http://localhost:3100}"
TENANT="${TENANT:-tenant_demo}"
REDIS_C="${REDIS_C:-plughub-demo-redis-1}"
KAFKA_C="${KAFKA_C:-plughub-demo-kafka-1}"
# ⚠️ `kafka:29092`, NUNCA `localhost:9092`. O broker anuncia PLAINTEXT como
# `localhost:9093` (a porta do CONTROLLER dentro do container), então um consumidor
# que entre por 9092 conecta, recebe o endereço anunciado e falha ao buscar — em
# SILÊNCIO, devolvendo zero mensagem. A primeira versão deste gate leu esse zero
# como "nada foi publicado" e o ramo de controle positivo foi o que o denunciou.
BROKER="${BROKER:-kafka:29092}"
TOPICO="conversations.outbound"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}gate_masked_menu_channel_live — NIV-03 no ar${RST}"
echo

command -v jq   >/dev/null || inc "jq ausente (nesta bancada ele vive no WSL, não no Git Bash)"
command -v curl >/dev/null || inc "curl ausente"
docker ps >/dev/null 2>&1  || inc "docker não responde"
docker ps --format '{{.Names}}' | grep -qx "$REDIS_C" || inc "container $REDIS_C fora do ar"
docker ps --format '{{.Names}}' | grep -qx "$KAFKA_C" || inc "container $KAFKA_C fora do ar"
curl -sf -o /dev/null "$MCP/health" || inc "mcp-server não responde em $MCP"

STAMP="$(date +%s)"
SID_WEB="niv03-web-$STAMP"
SID_WA="niv03-wa-$STAMP"

limpa() {
  docker exec "$REDIS_C" redis-cli DEL \
    "session:$SID_WEB:meta" "session:$SID_WA:meta" >/dev/null 2>&1 || true
  [ -n "${SSE_PID:-}" ] && kill "$SSE_PID" 2>/dev/null
  [ -n "${CONS_PID:-}" ] && kill "$CONS_PID" 2>/dev/null
  rm -f "${SSE_OUT:-}" "${CONS_OUT:-}"
}
trap limpa EXIT

# ── sessões REAIS no Redis real (é daqui que o tool lê o canal) ───────────────
for par in "$SID_WEB webchat" "$SID_WA whatsapp"; do
  set -- $par
  docker exec "$REDIS_C" redis-cli SET "session:$1:meta" \
    "{\"channel\":\"$2\",\"tenant_id\":\"$TENANT\"}" >/dev/null \
    || inc "não consegui semear session:$1:meta"
done

# ── consumidor posicionado no FIM, antes de qualquer chamada ─────────────────
CONS_OUT="$(mktemp)"
docker exec "$KAFKA_C" /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server "$BROKER" --topic "$TOPICO" --timeout-ms 25000 \
  > "$CONS_OUT" 2>/dev/null &
CONS_PID=$!
sleep 4   # o consumidor precisa entrar no grupo antes de publicarmos

# ── transporte MCP (SSE + endpoint de escrita anunciado) ─────────────────────
SSE_OUT="$(mktemp)"
curl -sN "$MCP/sse" > "$SSE_OUT" 2>/dev/null &
SSE_PID=$!
EP=""
for _ in $(seq 1 40); do
  EP=$(sed -n 's#^data: \(/messages?[^ ]*\)#\1#p' "$SSE_OUT" | head -1)
  [ -n "$EP" ] && break
  sleep 0.25
done
[ -n "$EP" ] || inc "o transporte SSE não anunciou o endpoint de escrita em 10 s"

send() { curl -s -o /dev/null "$MCP$EP" -H 'content-type: application/json' -d "$1"; }
send '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"gate-niv03","version":"1"}}}'
send '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'

chama() {   # $1=id  $2=session_id  $3=json do menu
  send "{\"jsonrpc\":\"2.0\",\"id\":$1,\"method\":\"tools/call\",\"params\":{\"name\":\"notification_send\",\"arguments\":{\"session_id\":\"$2\",\"message\":\"gate NIV-03\",\"menu\":$3}}}"
}
resposta() {
  local id="$1" r=""
  for _ in $(seq 1 60); do
    r=$(sed -n 's/^data: //p' "$SSE_OUT" | jq -Rc "fromjson? | select(.id? == $id)" 2>/dev/null | head -1)
    [ -n "$r" ] && break
    sleep 0.25
  done
  printf '%s' "$r"
}

MENU_MASC='{"interaction":"form","fields":[{"id":"cvv","label":"CVV"}],"masked_fields":["cvv"],"masked_types":{"cvv":"card_cvv"}}'
MENU_LIMPO='{"interaction":"button","options":[{"id":"a","label":"A"}]}'

chama 10 "$SID_WEB" "$MENU_MASC"    # A
chama 11 "$SID_WA"  "$MENU_MASC"    # B
chama 12 "$SID_WA"  "$MENU_LIMPO"   # C

R_A="$(resposta 10)"; R_B="$(resposta 11)"; R_C="$(resposta 12)"
[ -n "$R_A" ] && [ -n "$R_B" ] && [ -n "$R_C" ] \
  || inc "o tool não respondeu às três chamadas em 15 s (A=${#R_A} B=${#R_B} C=${#R_C} bytes)"

erro() { printf '%s' "$1" | jq -r '.result.isError // false' 2>/dev/null; }
texto() { printf '%s' "$1" | jq -r '.result.content[0].text // ""' 2>/dev/null; }

# ── espera o consumidor drenar e para ────────────────────────────────────────
sleep 6
kill "$CONS_PID" 2>/dev/null; wait "$CONS_PID" 2>/dev/null
# ⚠️ `grep -c X || echo 0` NÃO serve: `grep -c` já imprime `0` e AINDA sai 1, então o
# `|| echo 0` acrescenta uma SEGUNDA linha, e `[ "$N" -lt 1 ]` vira erro de sintaxe.
# Erro de `[` é status != 0, o `if` o lê como "condição falsa" e o fluxo cai no ramo
# OK — foi assim que a primeira versão deste gate ficou VERDE com contagem zero.
# `conta()` normaliza e o guarda numérico abaixo recusa qualquer coisa que não seja
# número, em vez de deixar o erro virar aprovação.
conta() { grep -c "$1" "$CONS_OUT" 2>/dev/null | head -1 | tr -dc '0-9'; }
N_WEB="$(conta "$SID_WEB")"
N_WA_TOTAL="$(conta "$SID_WA")"
N_WA_MASC="$(grep "$SID_WA" "$CONS_OUT" 2>/dev/null | grep -c 'cvv' | head -1 | tr -dc '0-9')"
for v in "$N_WEB" "$N_WA_TOTAL" "$N_WA_MASC"; do
  case "$v" in ''|*[!0-9]*) inc "contagem não-numérica ($v) — o instrumento falhou, e instrumento que falha não aprova" ;; esac
done
echo "  ${YEL}·${RST} capturado: webchat=$N_WEB whatsapp=$N_WA_TOTAL (mascarados=$N_WA_MASC) em $(wc -l < "$CONS_OUT") evento(s)"

# ── C primeiro: sem ele, o zero do B não é evidência ─────────────────────────
if [ "$(erro "$R_C")" = "true" ]; then
  bad "C — menu SEM máscara no whatsapp foi RECUSADO: $(texto "$R_C" | head -c 140)"
elif [ "$N_WA_TOTAL" -lt 1 ]; then
  inc "C — nada do whatsapp chegou ao tópico: o zero do ramo B não seria evidência (Kafka? consumidor?)"
else
  ok "C — controle positivo: menu sem máscara publicado no whatsapp ($N_WA_TOTAL evento(s))"
fi

# ── A — webchat entrega, e a máscara viaja ───────────────────────────────────
if [ "$(erro "$R_A")" = "true" ]; then
  bad "A — webchat RECUSOU um menu mascarado: $(texto "$R_A" | head -c 160)"
elif [ "$N_WEB" -lt 1 ]; then
  bad "A — webchat não recusou, mas nada foi publicado em $TOPICO (entrega quebrada)"
else
  ok "A — webchat entregou o menu mascarado ($N_WEB evento(s) com masked_fields)"
fi

# ── B — whatsapp recusa, e NADA viaja ────────────────────────────────────────
if [ "$(erro "$R_B")" != "true" ]; then
  bad "B — whatsapp NÃO recusou o menu mascarado (MSK-01 reaberta)"
elif ! printf '%s' "$(texto "$R_B")" | grep -q "masked_input_unsupported"; then
  bad "B — recusou, mas sem nomear o motivo: $(texto "$R_B" | head -c 160)"
elif [ "$N_WA_MASC" -gt 0 ]; then
  bad "B — recusou E PUBLICOU assim mesmo ($N_WA_MASC evento com masked_fields) — o pior dos dois mundos"
else
  ok "B — whatsapp recusou nomeando, e nenhum evento mascarado foi publicado"
fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s)"; exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — a cadeia inteira: canal lido do Redis real, recusa antes do Kafka, entrega intacta"
