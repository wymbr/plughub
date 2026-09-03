#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# gate_form_by_node — o roteiro do fluxo vive na FORMA, e é endereçável por NÓ.
# NIV-04 fatias A e B (ADR adr-agent-flow-single-authored-level § F4).
#
# POR QUE ESTE PROBE EXISTE
# ─────────────────────────
# O `render` do `form_get` é **single-turn**: statements viram satélites de UMA
# pergunta (`menu_prompt` os junta com `\n\n`). Isso serve o dialog-runner e **não
# serve** o roteiro de um fluxo de agente, cujos avisos vivem em ramos diferentes
# (saudação, transferência, encerramento). Sem endereçamento por nó, migrar roteiro
# exigiria uma forma por frase: medido em 2026-09-03, **79 pontos estáticos em 24
# skills** virariam ~79 formas e ~79 `invoke` novos — quase dobrando os fluxos.
#
# `render.by_node` (`node_id → texto`) permite UMA carga por fluxo e N referências.
#
# O QUE ESTE PROBE PODE REPROVAR
# ──────────────────────────────
#   A  `by_node` sumir, ou não cobrir todos os nós da forma          → VERMELHO
#   B  o texto do nó divergir do que a forma publicada declara       → VERMELHO
#   C  o piloto (`skill_limite_entrada_v1`) voltar a ter roteiro
#      estático cravado, OU perder a referência ao nó                → VERMELHO
#   D  a frase de DEGRADAÇÃO (`falha_roteiro`) sair do YAML          → VERMELHO
#      — ela é a única que TEM de continuar cravada: é o caminho de quem
#        falhou ao carregar o roteiro. Buscá-la na forma que acabou de falhar
#        deixaria o fluxo MUDO, e mensagem vazia é um valor plausível.
#   E  ids de nó repetidos numa forma semeada                        → VERMELHO
#      — o mapa ficaria ambíguo (o segundo sobrescreve o primeiro) e a
#        referência ao id perdido resolveria para string VAZIA. O `form_get`
#        recusa; este ramo pega antes, no arquivo.
#
# ⚠️ Requer `jq` — nesta bancada ele vive no WSL, não no Git Bash.
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MCP="${MCP:-http://localhost:3100}"
TENANT="${TENANT:-tenant_demo}"
FORM="${FORM:-dialog_limite_roteiro}"
SKILL="$RAIZ/packages/skill-flow-engine/skills/skill_limite_entrada_v1.yaml"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ${YEL}—${RST} INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}gate_form_by_node — roteiro na forma, endereçável por nó${RST}"
echo

command -v jq >/dev/null || inc "jq ausente (nesta bancada ele vive no WSL)"
curl -sf -o /dev/null "$MCP/health" || inc "mcp-server não responde em $MCP"

# ── chama form_get no servidor VIVO ──────────────────────────────────────────
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
envia '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-by-node","version":"1"}}}'
envia '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
envia "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"form_get\",\"arguments\":{\"form_id\":\"$FORM\",\"tenant_id\":\"$TENANT\"}}}"

RESP=""
for _ in $(seq 1 60); do
  RESP="$(sed -n 's/^data: //p' "$SSE_OUT" | jq -Rc 'fromjson? | select(.id? == 2)' 2>/dev/null | head -1)"
  [ -n "$RESP" ] && break
  sleep 0.25
done
[ -n "$RESP" ] || inc "form_get não respondeu em 15 s"

CORPO="$(printf '%s' "$RESP" | jq -r '.result.content[0].text' 2>/dev/null)"
printf '%s' "$RESP" | jq -e '.result.isError == true' >/dev/null 2>&1 \
  && inc "form_get devolveu erro: $(printf '%s' "$CORPO" | head -c 200)"

# ── A — by_node existe e cobre TODOS os nós ──────────────────────────────────
N_NOS="$(printf '%s' "$CORPO" | jq '.form.nodes | length' 2>/dev/null)"
N_MAPA="$(printf '%s' "$CORPO" | jq '.render.by_node // {} | keys | length' 2>/dev/null)"
case "${N_NOS:-x}${N_MAPA:-x}" in *x*) inc "não consegui ler as contagens do form_get" ;; esac
if [ "$N_MAPA" -eq 0 ]; then
  bad "A — render.by_node AUSENTE ou vazio: sem endereçamento por nó, o roteiro volta ao YAML"
elif [ "$N_MAPA" -ne "$N_NOS" ]; then
  bad "A — by_node cobre $N_MAPA de $N_NOS nós; nó sem entrada resolve para string VAZIA, sem erro"
else
  ok "A — by_node cobre os $N_NOS nós da forma"
fi

# ── B — o texto do nó é o da forma publicada ─────────────────────────────────
# ⚠️ O `jq` precisa RODAR. Se a expressão falhar, `CONFERE` sai vazia — que é o
# mesmo valor de "nada divergiu". Por isso o sentinela: a expressão devolve
# `OK:` na frente, e a ausência dele é INCONCLUSIVO, não aprovação.
CONFERE="$(printf '%s' "$CORPO" | jq -r --argjson mapa "$(printf '%s' "$CORPO" | jq -c '.render.by_node')" '
  "OK:" + ([ .form.nodes[]
             | select(.kind == "statement")
             | select(.text["pt-BR"] != $mapa[.id])
             | .id ] | join(","))' 2>/dev/null)"
case "$CONFERE" in
  OK:*) CONFERE="${CONFERE#OK:}" ;;
  *)    inc "B — a comparação forma × by_node não executou (jq); veredicto ausente não é veredicto verde" ;;
esac
if [ -n "$CONFERE" ]; then
  bad "B — texto divergente entre a forma e by_node em: $CONFERE"
else
  ok "B — todo statement de by_node bate com o texto da forma publicada"
fi

# ── C — o piloto referencia os nós e não voltou a cravar roteiro ─────────────
[ -f "$SKILL" ] || inc "skill do piloto não encontrada: $SKILL"
N_REF="$(grep -c 'render\.by_node\.' "$SKILL" | head -1 | tr -dc '0-9')"
CRAVADO="$(cd "$RAIZ" && python3 - <<'PY'
import io, re
p = "packages/skill-flow-engine/skills/skill_limite_entrada_v1.yaml"
cur, sobra = None, []
for l in io.open(p, encoding="utf-8"):
    m = re.match(r"^\s*- id:\s*(\S+)", l)
    if m:
        cur = m.group(1); continue
    m2 = re.match(r'^\s*(message|prompt):\s*"(.*)"\s*$', l.rstrip("\n"))
    if not m2: continue
    t = m2.group(2)
    if t.startswith("$.") or t.startswith("@") or "{{" in t: continue
    sobra.append(cur)
print(",".join(sobra))
PY
)"
if [ "${N_REF:-0}" -lt 12 ]; then
  bad "C — o piloto tem só ${N_REF:-0} referências a by_node (esperado ≥ 12): roteiro voltou ao YAML?"
elif [ "$CRAVADO" != "falha_roteiro" ]; then
  bad "C — literal estático fora do caminho de degradação: [$CRAVADO]"
else
  ok "C — $N_REF referências a by_node; único literal restante é o de degradação"
fi

# ── D — a frase de degradação CONTINUA cravada ───────────────────────────────
if grep -q 'id: falha_roteiro' "$SKILL" && \
   grep -A 3 'id: falha_roteiro' "$SKILL" | grep -q 'message: "[^{$@]'; then
  ok "D — a frase de degradação segue no YAML (o fluxo fala mesmo com o dialog-api fora)"
else
  bad "D — `falha_roteiro` perdeu a mensagem cravada: se o carregamento do roteiro falhar, o fluxo fica MUDO"
fi

# ── E — nenhuma forma semeada repete id de nó ────────────────────────────────
DUP="$(cd "$RAIZ" && python3 - <<'PY'
import glob, io, json
ruins = []
for f in sorted(glob.glob("infra/dialog/*.json")):
    try:
        ids = [n.get("id") for n in json.load(io.open(f, encoding="utf-8")).get("nodes", [])]
    except Exception as e:
        ruins.append("%s(ilegivel:%s)" % (f, type(e).__name__)); continue
    d = sorted({i for i in ids if ids.count(i) > 1})
    if d: ruins.append("%s%s" % (f.split("/")[-1], d))
print(" ".join(ruins))
PY
)"
if [ -n "$DUP" ]; then
  bad "E — id de nó repetido: $DUP (o mapa ficaria ambíguo e a referência perdida viraria texto vazio)"
else
  ok "E — nenhuma das formas semeadas repete id de nó"
fi

# ── F — CONTATO REAL: a frase que sai é a da forma, não a do YAML ────────────
# ⚠️ Os ramos A–E provam o mecanismo e a costura no arquivo. Nenhum prova que o
# cliente RECEBE o texto da forma — e essa é a proposição do piloto. O smoke que
# existe (`smoke_limite_tres_acessos.sh`) não serve: ele dispara o pool do PROCESSO
# por webhook e nunca passa pelo agente de entrada. Medido: rodada verde, com ZERO
# ocorrências de `carregar_roteiro` nos logs.
#
# O discriminador é a frase de DEGRADAÇÃO. Se o `form_get` falhasse, o fluxo diria
# "Estamos com uma instabilidade…" — então ver a saudação da forma prova, de uma
# vez, que a carga funcionou E que a referência ao nó resolveu.
if [ "${PULA_LIVE:-}" = "1" ]; then
  echo "  ${YEL}—${RST} F — pulado por PULA_LIVE=1 (abre um contato real no limite_ia)"
else
  K="${KAFKA_C:-plughub-demo-kafka-1}"; BRK="${BROKER:-kafka:29092}"
  if ! docker ps --format '{{.Names}}' | grep -qx "$K"; then
    inc "F — container $K fora do ar"
  fi
  SID_LIVE="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "roteiro-$$-$(date +%s)")"
  OUT_LIVE="$(mktemp)"
  docker exec "$K" /opt/kafka/bin/kafka-console-consumer.sh     --bootstrap-server "$BRK" --topic conversations.outbound --timeout-ms 45000     > "$OUT_LIVE" 2>/dev/null &
  CONS_LIVE=$!
  sleep 4
  printf '%s
' "{\"session_id\":\"$SID_LIVE\",\"tenant_id\":\"$TENANT\",\"customer_id\":\"probe-roteiro-$$\",\"channel\":\"webchat\",\"pool_id\":\"limite_ia\",\"started_at\":\"$(date -u +%Y-%m-%dT%H:%M:%S.000Z)\"}"     | docker exec -i "$K" /opt/kafka/bin/kafka-console-producer.sh       --bootstrap-server "$BRK" --topic conversations.inbound >/dev/null 2>&1
  for _ in $(seq 1 20); do
    grep -q "$SID_LIVE" "$OUT_LIVE" 2>/dev/null && break
    sleep 2
  done
  kill "$CONS_LIVE" 2>/dev/null; wait "$CONS_LIVE" 2>/dev/null

  ESPERADO="$(printf '%s' "$CORPO" | jq -r '.render.by_node.saudacao')"
  FALLBACK="Estamos com uma instabilidade"
  PRIMEIRA="$(grep "$SID_LIVE" "$OUT_LIVE" 2>/dev/null | head -1 | jq -r '.content.text // .text // ""' 2>/dev/null)"
  N_LIVE="$(grep -c "$SID_LIVE" "$OUT_LIVE" 2>/dev/null | head -1 | tr -dc '0-9')"
  rm -f "$OUT_LIVE"

  if [ "${N_LIVE:-0}" -eq 0 ]; then
    inc "F — o contato não produziu saída em 40 s: SEM AMOSTRA, e sem amostra não há veredicto (pool limite_ia com instância? routing de pé?)"
  elif printf '%s' "$PRIMEIRA" | grep -q "$FALLBACK"; then
    bad "F — saiu a frase de DEGRADAÇÃO: o carregamento do roteiro FALHOU (forma publicada? dialog-api de pé?)"
  elif [ "$PRIMEIRA" != "$ESPERADO" ]; then
    bad "F — a primeira frase não é a da forma. saiu=[$(printf '%s' "$PRIMEIRA" | head -c 80)] esperado=[$(printf '%s' "$ESPERADO" | head -c 80)]"
  else
    ok "F — contato real: a saudação entregue é a da FORMA ($N_LIVE evento(s))"
  fi
fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s)"; exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — roteiro na forma, endereçável por nó, e a degradação segue falante"
