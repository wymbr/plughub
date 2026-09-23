#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_orq13_clarify — a pergunta de esclarecimento oferece o que a ÁRVORE declara.
# ORQ-13 (ADR adr-orchestrator-tree-navigation § D8).
#
# A PROPOSIÇÃO
# ────────────
# *`dialog_tree_level` com `only_paths` devolve uma opção por caminho CONFERIDO,
# com o rótulo da árvore, e o `id` de cada opção volta a resolver numa folha pela
# MESMA tool.*
#
# POR QUE ISTO PRECISA DE UM GATE AO VIVO, tendo teste unitário
# ─────────────────────────────────────────────────────────────
# O unitário (`dialog.test.ts`) prende a função PURA contra um vocabulário de
# mentira. O que ele não alcança é a metade que decide em produção: se o
# `vocabulary` da forma VIVA tem os caminhos que o classificador vai propor, e se
# o `id` que vira botão volta a resolver como `chosen_id` — a ida e a volta pela
# mesma projeção. Se essa volta quebrar, a pergunta funciona, o cliente escolhe, e
# a navegação REINICIA parecendo certa (o defeito que a F2 já pagou uma vez).
#
# RAMOS
#   A  dois caminhos REAIS ⇒ duas opções, e o rótulo é o da árvore, NUNCA o caminho.
#   B  CONTRAPROVA — um real + um inventado ⇒ `candidate_count: 1` e o inventado em
#      `candidates_dropped`. Sem este ramo, uma tool que aceitasse tudo passaria no A.
#   C  IDA E VOLTA — o `id` da opção, devolvido como `chosen_id`, resolve
#      `found && is_leaf`. É o que prova que o botão leva a algum lugar.
#   D  vocabulário INDISPONÍVEL não vira rótulo fabricado: pedir só caminho
#      inventado dá zero candidatos, e zero faz o fluxo escapar (o `gte 2`).
#
# ⚠️ Requer `jq` — nesta bancada ele vive no WSL, não no Git Bash.
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

MCP="${MCP:-http://localhost:3100}"
TENANT="${TENANT:-tenant_demo}"
FORM="${FORM:-dialog_navegacao_atendimento_v1}"
OUTPUT_KEY="${OUTPUT_KEY:-destino}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}probe_orq13_clarify — o esclarecimento pergunta o que a árvore declara${RST}"
echo

command -v jq   >/dev/null || inc "jq ausente (nesta bancada ele vive no WSL)"
command -v curl >/dev/null || inc "curl ausente"
curl -sf -o /dev/null "$MCP/health" || inc "mcp-server não responde em $MCP"

# ── transporte SSE ────────────────────────────────────────────────────────────
SSE_OUT="$(mktemp)"
curl -sN "$MCP/sse" > "$SSE_OUT" 2>/dev/null &
SSE_PID=$!
trap 'kill "$SSE_PID" 2>/dev/null; rm -f "$SSE_OUT"' EXIT

EP=""
for _ in $(seq 1 40); do
  EP="$(sed -n 's#^data: \(/messages?[^ ]*\)#\1#p' "$SSE_OUT" | head -1)"
  [ -n "$EP" ] && break
  sleep 0.25
done
[ -n "$EP" ] || inc "o transporte SSE não anunciou o endpoint em 10 s"

envia() { curl -s -o /dev/null "$MCP$EP" -H 'content-type: application/json' -d "$1"; }
envia '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-orq13","version":"1"}}}'
envia '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'

# chama <id> <args-json> → imprime o TEXTO do resultado (o corpo da tool)
#
# ⚠️ O id vem por ARGUMENTO, e não de um contador. A primeira versão fazia
# `ID=$((ID+1))` dentro da função, chamada como `$(chama …)` — comando
# substituído roda em SUBSHELL, então o contador do pai nunca subia: toda
# chamada pedia `id: 2` e o `select(.id == 2)` devolvia, para sempre, a resposta
# da PRIMEIRA. O gate reprovou quatro ramos sobre um produto que estava certo, e
# só uma chamada à mão, fora dele, mostrou a diferença. Contador mutável dentro
# de `$( )` é a armadilha; o id explícito não tem como envelhecer.
chama() {
  local id="$1"
  envia "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"tools/call\",\"params\":{\"name\":\"dialog_tree_level\",\"arguments\":$2}}"
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

# ── o vocabulário VIVO: de onde saem os candidatos de verdade ─────────────────
BASE="$(chama 2 "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"tenant_id\":\"$TENANT\"}")" \
  || inc "dialog_tree_level não respondeu (ou devolveu erro) para a forma '$FORM'"

# Duas folhas quaisquer da árvore VIVA. Fixar caminhos aqui faria o gate reprovar
# quando a árvore do demo mudasse — que é conteúdo, não contrato.
P1="$(printf '%s' "$BASE" | jq -r '.leaves[0] // empty')"
P2="$(printf '%s' "$BASE" | jq -r '.leaves[1] // empty')"
[ -n "$P1" ] && [ -n "$P2" ] || inc "a forma '$FORM' não tem duas folhas — sem população não há o que medir"
TEM_VOC="$(printf '%s' "$BASE" | jq -r 'has("vocabulary")')"
[ "$TEM_VOC" = "true" ] || inc "a forma '$FORM' veio SEM vocabulary (divergência logada pela tool) — o esclarecimento depende dele"

echo "── A · dois caminhos reais viram duas opções, com rótulo da ÁRVORE ────"
A="$(chama 3 "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"only_paths\":[\"$P1\",\"$P2\"],\"tenant_id\":\"$TENANT\"}")" \
  || { bad "a chamada com only_paths falhou"; A='{}'; }
N_A="$(printf '%s' "$A" | jq -r '.candidate_count // -1')"
if [ "$N_A" = "2" ]; then ok "candidate_count = 2 ($P1, $P2)"; else bad "candidate_count = $N_A, esperado 2"; fi

IDS="$(printf '%s' "$A" | jq -r '[.options[].id] | join(",")')"
[ "$IDS" = "$P1,$P2" ] && ok "a ordem é a PEDIDA, e o id é o caminho pontuado" \
                        || bad "ids = [$IDS], esperado [$P1,$P2]"

# O rótulo TEM de ser texto de conteúdo, não o caminho. Um `label` igual ao `id`
# significa rótulo fabricado — o cliente leria `sac.info_plano` como pergunta.
IGUAIS="$(printf '%s' "$A" | jq -r '[.options[] | select(.label == .id)] | length')"
[ "$IGUAIS" = "0" ] && ok "nenhum rótulo é o próprio caminho" \
                     || bad "$IGUAIS rótulo(s) iguais ao caminho — rótulo fabricado"
VAZIOS="$(printf '%s' "$A" | jq -r '[.options[] | select((.label // "") == "")] | length')"
[ "$VAZIOS" = "0" ] && ok "nenhum rótulo vazio" || bad "$VAZIOS rótulo(s) vazios"

echo ""
echo "── B · CONTRAPROVA: caminho inventado NÃO vira botão ──────────────────"
FALSO="nao_existe_essa_folha_orq13"
B="$(chama 4 "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"only_paths\":[\"$P1\",\"$FALSO\"],\"tenant_id\":\"$TENANT\"}")" \
  || { bad "a chamada de contraprova falhou"; B='{}'; }
N_B="$(printf '%s' "$B" | jq -r '.candidate_count // -1')"
DROP="$(printf '%s' "$B" | jq -r '.candidates_dropped // [] | join(",")')"
if [ "$N_B" = "1" ] && [ "$DROP" = "$FALSO" ]; then
  ok "candidate_count = 1 e o inventado saiu em candidates_dropped"
  ok "com 1 candidato o fluxo NÃO pergunta (o \`gte 2\` do avaliar_esclarecimento)"
else
  bad "candidate_count = $N_B, dropped = [$DROP] — esperado 1 e [$FALSO]"
fi

echo ""
echo "── C · IDA E VOLTA: o id do botão resolve numa folha ───────────────────"
VOLTA="$(chama 5 "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"chosen_id\":\"$P1\",\"tenant_id\":\"$TENANT\"}")" \
  || { bad "a volta com chosen_id falhou"; VOLTA='{}'; }
F="$(printf '%s' "$VOLTA" | jq -r '.found')"
L="$(printf '%s' "$VOLTA" | jq -r '.is_leaf')"
CAT="$(printf '%s' "$VOLTA" | jq -r '.category_path // ""')"
if [ "$F" = "true" ] && [ "$L" = "true" ]; then
  ok "found && is_leaf — o botão leva a uma folha (category_path=$CAT)"
else
  bad "found=$F is_leaf=$L — o id oferecido não resolve de volta; a navegação reiniciaria"
fi

echo ""
echo "── D · sem candidato conferido, ZERO — nunca rótulo fabricado ──────────"
D="$(chama 6 "{\"form_id\":\"$FORM\",\"output_key\":\"$OUTPUT_KEY\",\"path\":[],\"only_paths\":[\"$FALSO\"],\"tenant_id\":\"$TENANT\"}")" \
  || { bad "a chamada D falhou"; D='{}'; }
N_D="$(printf '%s' "$D" | jq -r '.candidate_count // -1')"
O_D="$(printf '%s' "$D" | jq -r '.options | length')"
if [ "$N_D" = "0" ] && [ "$O_D" = "0" ]; then
  ok "candidate_count = 0 e nenhuma opção — o fluxo escapa, que é contável"
else
  bad "candidate_count = $N_D com $O_D opção(ões) — um caminho inventado virou oferta"
fi

echo ""
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVOU${RST} — $FAIL defeito(s): o esclarecimento não oferece o que a árvore declara"
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — candidatos conferidos, rótulo da árvore, e o botão resolve de volta"
exit 0
