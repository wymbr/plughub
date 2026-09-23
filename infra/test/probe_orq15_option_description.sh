#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_orq15_option_description — a segunda linha da opção chega ao que os canais leem.
# ORQ-15 (ADR adr-orchestrator-tree-navigation § D8; `packages/channel-gateway/CLAUDE.md`).
#
# A PROPOSIÇÃO
# ────────────
# *A `description` que o autor escreveu numa opção sai das tools VIVAS até o stream
# que o canal lê — igual ao que o classificador lê, sem `examples`, e sem a chave
# quando não há texto.*
#
# POR QUE AO VIVO, tendo unitário nos três pacotes
# ────────────────────────────────────────────────
# Os unitários prendem cada casa isolada (render, schema da tool, adapter). O que
# nenhum deles alcança é a IMAGEM rodando: a `description` morria calada no Zod do
# `notification_send` — uma casa a mais do que o `render` —, e só uma chamada à
# tool viva prova que o contêiner em pé tem as duas metades juntas.
#
# RAMOS
#   A  RENDER VIVO — `dialog_tree_level` devolve `description` nas opções, e em cada
#      FOLHA ela é a MESMA do `vocabulary` (o que o cliente lê = o que o classificador
#      leu). População: ≥ 1 opção com descrição, senão INCONCLUSIVO.
#   B  `examples` NUNCA em `.options`, em nível nenhum — só no `vocabulary`.
#      Controle positivo ao lado: o `vocabulary` TEM examples (senão B passaria vazio).
#   C  TOOL VIVA — `notification_send` com uma opção descrita e uma sem: o stream
#      canônico (o que o webchat lê por XREAD) leva a descrição na primeira e NÃO tem
#      a chave na segunda. Sem este ramo, a tool que descarta passaria em A e B.
#   D  `examples` injetado nos argumentos da tool NÃO chega ao stream.
#
# Efeito colateral declarado: C/D escrevem numa sessão SINTÉTICA `probe-orq15-*`
# (stream + Kafka `conversations.outbound`, que o gateway descarta por não haver
# conexão). As chaves Redis `session:{sid}:*` são apagadas no fim.
#
# ⚠️ Requer `jq` e `docker` — nesta bancada vivem no WSL, não no Git Bash.
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

MCP="${MCP:-http://localhost:3100}"
TENANT="${TENANT:-tenant_demo}"
FORM="${FORM:-dialog_navegacao_atendimento_v1}"
OUTPUT_KEY="${OUTPUT_KEY:-destino}"
REDIS_CT="${REDIS_CT:-plughub-demo-redis-1}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}probe_orq15_option_description — a segunda linha chega ao canal${RST}"
echo

command -v jq     >/dev/null || inc "jq ausente (nesta bancada ele vive no WSL)"
command -v curl   >/dev/null || inc "curl ausente"
command -v docker >/dev/null || inc "docker ausente"
curl -sf -o /dev/null "$MCP/health" || inc "mcp-server não responde em $MCP"
docker exec "$REDIS_CT" redis-cli PING 2>/dev/null | grep -q PONG || inc "redis ($REDIS_CT) não responde"

SID="probe-orq15-$(date +%s)-$$"
SSE_OUT="$(mktemp)"
curl -sN "$MCP/sse" > "$SSE_OUT" 2>/dev/null &
SSE_PID=$!
limpa() {
  kill "$SSE_PID" 2>/dev/null; rm -f "$SSE_OUT"
  # só as chaves DESTA sessão sintética
  for k in $(docker exec "$REDIS_CT" redis-cli --scan --pattern "session:${SID}:*" 2>/dev/null); do
    docker exec "$REDIS_CT" redis-cli DEL "$k" >/dev/null 2>&1
  done
}
trap limpa EXIT

EP=""
for _ in $(seq 1 40); do
  EP="$(sed -n 's#^data: \(/messages?[^ ]*\)#\1#p' "$SSE_OUT" | head -1)"
  [ -n "$EP" ] && break
  sleep 0.25
done
[ -n "$EP" ] || inc "o transporte SSE não anunciou o endpoint em 10 s"

envia() { curl -s -o /dev/null "$MCP$EP" -H 'content-type: application/json' -d "$1"; }
envia '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-orq15","version":"1"}}}'
envia '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'

# chama <id> <tool> <args-json> → TEXTO do resultado. O id vem por ARGUMENTO: contador
# dentro de `$( )` roda em subshell e não sobe (armadilha paga no probe_orq13_clarify).
chama() {
  local id="$1" tool="$2"
  envia "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":$3}}"
  local resp=""
  for _ in $(seq 1 60); do
    resp="$(sed -n 's/^data: //p' "$SSE_OUT" | jq -Rc "fromjson? | select(.id? == $id)" 2>/dev/null | head -1)"
    [ -n "$resp" ] && break
    sleep 0.25
  done
  [ -n "$resp" ] || return 1
  printf '%s' "$resp" | jq -e '.result.isError == true' >/dev/null 2>&1 && return 2
  printf '%s' "$resp" | jq -r '.result.content[0].text'
}

BASE="$(chama 2 dialog_tree_level "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"tenant_id\":\"$TENANT\"}")" \
  || inc "dialog_tree_level não respondeu (ou devolveu erro) para a forma '$FORM'"

echo "── A · RENDER VIVO: a opção traz a segunda linha, igual à do classificador ─"
N_DESC="$(printf '%s' "$BASE" | jq '[.options | .. | objects | select(has("description"))] | length')"
[ "${N_DESC:-0}" -ge 1 ] || inc "a forma '$FORM' não tem opção com description — sem população não há o que medir"
ok "$N_DESC opção(ões) com description na árvore renderizada"

VAZIAS="$(printf '%s' "$BASE" | jq '[.options | .. | objects | select(has("description")) | select((.description|tostring|gsub("\\s";"")) == "")] | length')"
[ "$VAZIAS" = "0" ] && ok "nenhuma description vazia (ausência é AUSÊNCIA, nunca \"\")" \
                    || bad "$VAZIAS description(s) vazia(s) — o canal desenharia uma linha em branco"

# Para cada folha do vocabulário: a description no RENDER (caminhando pelos ids) é a do vocabulário.
DIVERG="$(printf '%s' "$BASE" | jq -r '
  . as $b
  | [ $b.vocabulary[]? as $v
      | ($v.path | split(".")) as $p
      | (reduce $p[] as $s ({o: $b.options, cur: null};
            (.o | map(select(.id == $s)) | first) as $n | {o: ($n.options // []), cur: $n})
        ).cur as $leaf
      | select(($leaf.description // null) != ($v.description // null))
      | "\($v.path): render=\($leaf.description // "∅") voc=\($v.description // "∅")" ]
  | .[]')"
N_VOC="$(printf '%s' "$BASE" | jq '[.vocabulary[]?] | length')"
[ "$N_VOC" -ge 1 ] || inc "vocabulary ausente — a paridade não é medível"
if [ -z "$DIVERG" ]; then ok "as $N_VOC folhas dizem ao cliente o que disseram ao classificador"
else bad "folhas divergentes:"; printf '      %s\n' "$DIVERG"; fi

echo ""
echo "── B · examples NUNCA nas opções (só no vocabulary) ────────────────────"
EX_VOC="$(printf '%s' "$BASE" | jq '[.vocabulary[]? | select((.examples // []) | length > 0)] | length')"
[ "$EX_VOC" -ge 1 ] || inc "nenhuma folha com examples — o ramo B passaria vazio"
EX_OPT="$(printf '%s' "$BASE" | jq '[.options | .. | objects | select(has("examples"))] | length')"
[ "$EX_OPT" = "0" ] && ok "0 examples em .options (controle: $EX_VOC folha(s) com examples no vocabulary)" \
                    || bad "$EX_OPT opção(ões) com examples — frase de classificador indo ao canal"

echo ""
echo "── C · TOOL VIVA: notification_send leva a description ao stream ───────"
D1="$(printf '%s' "$BASE" | jq -c '[.options | .. | objects | select(has("description"))] | first | {id, label, description}')"
DESC1="$(printf '%s' "$D1" | jq -r '.description')"
ARGS="$(jq -nc --arg sid "$SID" --argjson d "$D1" \
  '{session_id: $sid, message: "probe ORQ-15", menu: {interaction: "list",
    options: [$d, {id: "sem_desc", label: "Sem descrição"}]}}')"
chama 3 notification_send "$ARGS" >/dev/null || bad "notification_send falhou (rc=$?)"

stream_opts() {  # options do ÚLTIMO interaction_request da sessão sintética
  docker exec "$REDIS_CT" redis-cli --raw XREVRANGE "session:${SID}:stream" + - COUNT 5 2>/dev/null \
    | grep -m1 '"options"' | jq -c '.options' 2>/dev/null
}
S="$(stream_opts)"
if [ -z "$S" ]; then
  bad "nenhum interaction_request no stream session:${SID}:stream"
else
  GOT="$(printf '%s' "$S" | jq -r '.[0].description // "∅"')"
  [ "$GOT" = "$DESC1" ] && ok "a opção descrita chegou ao stream com a MESMA description" \
                        || bad "description no stream = '$GOT', esperado '$DESC1' — a tool a descarta"
  HAS2="$(printf '%s' "$S" | jq -r '.[1] | has("description")')"
  [ "$HAS2" = "false" ] && ok "a opção sem texto saiu SEM a chave (controle)" \
                        || bad "a opção sem texto saiu com description — o canal desenharia linha vazia"
fi

echo ""
echo "── D · examples nos argumentos NÃO chega ao stream ─────────────────────"
ARGS_D="$(jq -nc --arg sid "$SID" '{session_id: $sid, message: "probe ORQ-15 D", menu: {interaction: "list",
    options: [{id: "a", label: "A", examples: ["frase-de-classificador-orq15"]}]}}')"
chama 4 notification_send "$ARGS_D" >/dev/null || bad "notification_send (D) falhou"
if docker exec "$REDIS_CT" redis-cli --raw XRANGE "session:${SID}:stream" - + 2>/dev/null | grep -q 'frase-de-classificador-orq15'; then
  bad "examples atravessou a tool e está no stream"
else
  N_ENT="$(docker exec "$REDIS_CT" redis-cli XLEN "session:${SID}:stream" 2>/dev/null)"
  [ "${N_ENT:-0}" -ge 2 ] && ok "examples descartado pela tool (controle: $N_ENT entradas no stream)" \
                          || bad "o stream tem ${N_ENT:-0} entrada(s) — D não mediu nada"
fi

echo ""
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVOU${RST} — $FAIL defeito(s): a segunda linha não chega ao canal como deveria"
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — render, tool e stream carregam a description; examples fica no classificador"
exit 0
