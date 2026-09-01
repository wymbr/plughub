#!/usr/bin/env bash
# probe_supervisor_tool_masking.sh — a SEGUNDA PORTA do ContextStore tem política?
#
# Arco ALLOWLIST (`docs/adr/adr-contextstore-allowlist.md` §1.5 e §D4).
#
# ── O que este gate protege ──────────────────────────────────────────────────
#
# O ContextStore era lido por DUAS portas com o mesmo nome e políticas opostas:
#
#   · `GET /api/supervisor_state/:sid` (server.ts) — máscara + portão de namespace
#   · tool MCP `supervisor_state`      (tools/supervisor.ts) — `JSON.parse` do hash
#                                        CRU, sem máscara nenhuma
#
# É a duplicação que mordeu a leitura de SENTIMENTO em 2026-08-25 (duas
# implementações idênticas, só uma consertada, e a que desenhava a tela era a
# outra), desta vez sobre PII. Desde 08-26 as duas compartilham `lib/context-masking.ts`
# e o tool entrega em grau OPERATOR.
#
# ── Por que INJETA, e por que o ORÁCULO é o endpoint ─────────────────────────
#
# Injeta porque um teste de igualdade só julga se a população contiver o caso em
# que A ≠ B — sessão comum só tem `core.pool.*`, que nenhuma regra alcança, e
# então "porta fechada" e "porta aberta" produzem a MESMA saída.
#
# E o valor esperado NÃO é escrito à mão aqui: ele é lido do endpoint HTTP, que é
# a implementação de referência. Hardcodar `***00` mediria a REGRA do tenant (que
# muda na tela de Masking) em vez da porta — o gate reprovaria um código correto
# no dia em que alguém editasse a regra. Quando a regra não está viva, o oráculo
# não tem o que dizer e o ramo sai INCONCLUSIVO, nunca verde.
#
# ⚠️ O oráculo só serve a tags DENTRO do default de namespace do operador
# (`service`, `session`), porque fora dele o endpoint retém por PORTÃO e não
# publica valor nenhum. Por isso as tags injetadas são todas `session.*` — usar
# `caller.cpf` faria o oráculo emudecer e o gate julgar com um instrumento cego.
#
# ── Os ramos ─────────────────────────────────────────────────────────────────
#
#   P. PREFLIGHT — meta, ctx, tool de pé, e o extrator de JSON conferido (hit/miss).
#   A. TESTEMUNHA DE PRESENÇA — o tool devolve `context_snapshot` com as injetadas.
#   B. 🔴 A PORTA — `session.probe.cpf` chega MASCARADA, e igual ao que o endpoint
#      entrega ao operator. Ramo triplo: igual ao oráculo ✓ · igual ao CRU ✗ ·
#      oráculo em claro (regra ausente) INCONCLUSIVO.
#   C. TESTEMUNHA NEGATIVA — `session.probe_witness` (nenhuma regra casa) volta em
#      CLARO. É o ramo que pega o conserto preguiçoso "mascare tudo", que passaria
#      em B sozinho.
#   D. `agent.*` FORA — a tag some do snapshot, com C provando que o produtor emite.
#   E. `hidden` é CONTADO, não sumido — a chave permanece com `value: null` e
#      `hidden_count ≥ 1`. Um `hidden` que dropasse a chave faria o leitor concluir
#      que ninguém escreveu nada.
#   F. ARITMÉTICA — `context_masking.total` == campos não-`agent.` no hash CRU.
#      Mede por outro caminho a mesma propriedade de A/D: quando E e F discordam,
#      é o instrumento que está cego, não o código (lição de 2026-08-26).
#
# Limpa o que injetou (HDEL) em qualquer saída.
#
# Uso:  bash infra/test/probe_supervisor_tool_masking.sh <session_id>
#       (sem argumento, lista as sessões com ContextStore vivo)
set -u

DC=${DC:-docker compose -f docker-compose.demo.yml}
AUTH=${AUTH:-http://localhost:3202/auth}
MCP=${MCP:-http://localhost:3100}
TENANT=${TENANT:-tenant_demo}
OP_EMAIL=${OP_EMAIL:-operator@plughub.local}
OP_PASS=${OP_PASS:-changeme_operator}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

redis() { $DC exec -T redis redis-cli "$@"; }

SID=${1:-}
if [ -z "$SID" ]; then
  echo "uso: $0 <session_id>"
  echo
  echo "sessões com ContextStore vivo (exclui o ctx de PROCESSO, que é outra chave):"
  redis --raw KEYS "$TENANT:ctx:*" | grep -v ':ctx:journey:' | sed "s/^$TENANT:ctx://" | head -20
  exit 2
fi

CTX="$TENANT:ctx:$SID"

# ── P. preflight ─────────────────────────────────────────────────────────────
echo "P · preflight:"
fail=0
incon=0
note() { echo "  $*"; }

# O input do tool é `z.string().uuid()` — id fora do formato devolve erro de
# validação, que não é evidência sobre máscara nenhuma.
if ! echo "$SID" | grep -Eq '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'; then
  echo "INCONCLUSIVO: '$SID' não é UUID — o schema do tool recusaria antes de ler o ctx"
  exit 2
fi
if [ "$(redis --raw EXISTS "$CTX")" != "1" ]; then
  echo "INCONCLUSIVO: $CTX não existe (sessão fechada? TTL expirou?)"
  exit 2
fi
# O tool resolve o tenant do META, não do argumento. Sem meta ele lê ctxHash de
# tenant "" — hash vazio — e devolveria `context_snapshot: null` por AUSÊNCIA de
# dado, que este gate leria como "nada a mascarar". Recusa alto.
if [ "$(redis --raw EXISTS "session:$SID:meta")" != "1" ]; then
  echo "INCONCLUSIVO: session:$SID:meta AUSENTE — o tool não resolve tenant_id sem ele"
  exit 2
fi
note "✓ ctx + meta presentes"

TOK_OP=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$OP_EMAIL\",\"password\":\"$OP_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -z "$TOK_OP" ] && { echo "INCONCLUSIVO: login operator falhou (OP_PASS=… sobrescreve)"; exit 2; }
note "✓ token de operator obtido (o oráculo tem de ser OPERATOR — admin é"
note "  supervisor_role, vê tudo em claro e não julga política de masking)"

# ── injeção ──────────────────────────────────────────────────────────────────
# Todas em `session.*`, DENTRO do default de namespace do operador — ver o
# cabeçalho. O ponto antes de `cpf`/`resume_token` é deliberado: o glob de sufixo
# casa em FRONTEIRA DE SEGMENTO, então `session.probe_cpf` (underscore) NÃO casaria.
TAG_PII="session.probe.cpf"
TAG_WIT="session.probe_witness"
TAG_HID="session.probe.resume_token"
TAG_AGT="agent.probe_secret"

VAL_PII="123.456.789-00"
VAL_WIT="testemunha-em-claro"
VAL_HID="tok_probe_segredo"

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkentry() { printf '{"value":"%s","confidence":1.0,"source":"probe","visibility":"agents_only","updated_at":"%s"}' "$1" "$NOW"; }

SSE_PID=""
SSE_OUT=""
cleanup() {
  redis HDEL "$CTX" "$TAG_PII" "$TAG_WIT" "$TAG_HID" "$TAG_AGT" >/dev/null 2>&1 || true
  [ -n "$SSE_PID" ] && kill "$SSE_PID" >/dev/null 2>&1
  [ -n "$SSE_OUT" ] && rm -f "$SSE_OUT"
  return 0
}
trap cleanup EXIT

redis HSET "$CTX" "$TAG_PII" "$(mkentry "$VAL_PII")" >/dev/null
redis HSET "$CTX" "$TAG_WIT" "$(mkentry "$VAL_WIT")" >/dev/null
redis HSET "$CTX" "$TAG_HID" "$(mkentry "$VAL_HID")" >/dev/null
redis HSET "$CTX" "$TAG_AGT" "$(mkentry 'segredo-de-agente')" >/dev/null

# Testemunha de que a injeção CHEGOU ao hash. Sem ela, uma escrita que falhasse
# faria todo ramo abaixo medir a ausência da tag em vez da política sobre ela.
RAW_PII=$(redis --raw HGET "$CTX" "$TAG_PII" | jq -r '.value // empty' 2>/dev/null)
RAW_WIT=$(redis --raw HGET "$CTX" "$TAG_WIT" | jq -r '.value // empty' 2>/dev/null)
if [ "$RAW_PII" != "$VAL_PII" ] || [ "$RAW_WIT" != "$VAL_WIT" ]; then
  echo "INCONCLUSIVO: a injeção não voltou do Redis (pii='$RAW_PII' wit='$RAW_WIT')"
  exit 2
fi
note "✓ injetadas 4 tags em $CTX e relidas CRUAS do Redis"

# ── as duas leituras ─────────────────────────────────────────────────────────
# ⚠️ **Não existe `POST /mcp` neste servidor.** O transporte é SSE: `GET /sse` abre
# a conexão e o servidor anuncia, num evento `endpoint`, a URL de escrita
# (`/messages?sessionId=…`); as RESPOSTAS voltam pelo stream, não pelo POST — o POST
# devolve 202 e um corpo vazio. Quem trata o 202 como resposta lê ausência e chama
# de "campo faltando".
#
# Achado de 2026-08-26, ao escrever este gate: o cenário e2e 17 Parte E faz
# `POST ${mcpServerUrl}/mcp` à mão e recebe o HTML de 404 do Express — ou seja,
# **aquela metade do cenário nunca exerceu o tool**, apesar de o mesmo pacote ter um
# cliente MCP correto (`packages/e2e-tests/lib/mcp-client.ts`, que usa
# `SSEClientTransport`). Registrado no backlog.
SSE_OUT=$(mktemp)
curl -sN "$MCP/sse" > "$SSE_OUT" 2>/dev/null &
SSE_PID=$!

EP=""
for _ in $(seq 1 40); do
  EP=$(sed -n 's#^data: \(/messages?[^ ]*\)#\1#p' "$SSE_OUT" | head -1)
  [ -n "$EP" ] && break
  sleep 0.25
done
if [ -z "$EP" ]; then
  echo "INCONCLUSIVO: o transporte SSE não anunciou o endpoint de escrita em 10 s"
  echo "  (mcp-server de pé? \`$DC ps mcp-server-plughub\`)"
  exit 2
fi

send() { curl -s -o /dev/null "$MCP$EP" -H 'content-type: application/json' -d "$1"; }
# O Protocol do SDK recusa requisições antes do `initialize` — pular o handshake
# devolveria erro de protocolo, que não é evidência sobre máscara nenhuma.
send '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-supervisor-masking","version":"1"}}}'
send '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
send "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"supervisor_state\",\"arguments\":{\"session_id\":\"$SID\",\"tenant_id\":\"$TENANT\"}}}"

RESP=""
for _ in $(seq 1 60); do
  # `-R` + `fromjson?` porque o stream MISTURA linhas: a primeira é a URL do
  # endpoint, que não é JSON. Um `jq` sem isso ABORTA no primeiro parse-error e
  # nunca chega à resposta — falharia por instrumento, não por código.
  RESP=$(sed -n 's/^data: //p' "$SSE_OUT" | jq -Rc 'fromjson? | select(.id? == 2)' 2>/dev/null | head -1)
  [ -n "$RESP" ] && break
  sleep 0.25
done
if [ -z "$RESP" ]; then
  echo "INCONCLUSIVO: o tool não respondeu ao id=2 em 15 s"
  echo "  stream: $(sed -n 's/^data: //p' "$SSE_OUT" | head -3 | tr '\n' ' ' | head -c 300)"
  exit 2
fi

# O corpo útil do MCP vem como TEXTO dentro de content[0].text — dois níveis de
# JSON. Um `jq` que falhe aqui devolve vazio, e vazio pareceria "campo ausente".
TOOL=$(echo "$RESP" | jq -r '.result.content[0].text // empty' 2>/dev/null)
if [ -z "$TOOL" ]; then
  echo "INCONCLUSIVO: resposta sem content[0].text (erro do tool?)"
  echo "  corpo: $(echo "$RESP" | head -c 400)"
  exit 2
fi
note "✓ tool alcançado por SSE (endpoint $EP)"

HTTP=$(curl -s "$MCP/api/supervisor_state/$SID" -H "Authorization: Bearer $TOK_OP")

# PREFLIGHT DO EXTRATOR: o mesmo caminho jq usado abaixo tem de ACHAR num
# documento sintético que contém a tag, e NÃO achar num que não contém. Sem isto,
# um extrator quebrado e uma porta consertada dão a mesma saída vazia — a família
# de defeito que este gate existe para pegar.
VPATH='.customer_context.context_snapshot["'"$TAG_PII"'"].value // empty'
pf_hit=$(jq -rn --arg t "$TAG_PII" '{customer_context:{context_snapshot:{($t):{value:"HIT"}}}}' | jq -r "$VPATH")
pf_mis=$(jq -rn '{customer_context:{context_snapshot:{"outra.tag":{value:"X"}}}}'               | jq -r "$VPATH")
if [ "$pf_hit" != "HIT" ] || [ -n "$pf_mis" ]; then
  echo "INCONCLUSIVO: o extrator não passa no próprio controle (hit='$pf_hit' esperado HIT;"
  echo "  miss='$pf_mis' esperado vazio). Não julgo o código com um instrumento cego."
  exit 2
fi
note "✓ extrator conferido (hit=HIT · miss=vazio)"
echo

val_tool() { echo "$TOOL" | jq -r ".customer_context.context_snapshot[\"$1\"].value // empty"; }
val_http() { echo "$HTTP" | jq -r ".customer_context.context_snapshot[\"$1\"].value // empty"; }
has_tool() { echo "$TOOL" | jq -e ".customer_context.context_snapshot | has(\"$1\")" >/dev/null 2>&1; }

# ── A. testemunha de presença ────────────────────────────────────────────────
echo "A · o tool devolve context_snapshot com as tags injetadas:"
SNAP_N=$(echo "$TOOL" | jq -r '.customer_context.context_snapshot // {} | length')
if [ "${SNAP_N:-0}" -lt 3 ]; then
  note "INCONCLUSIVO: snapshot com $SNAP_N entradas — a injeção não chegou ao tool"
  incon=1
else
  note "✓ $SNAP_N entradas"
fi
echo

# ── B. A PORTA ───────────────────────────────────────────────────────────────
echo "B · \`$TAG_PII\` sai MASCARADA, e igual ao que o endpoint entrega ao operator:"
T_PII=$(val_tool "$TAG_PII")
H_PII=$(val_http "$TAG_PII")
note "cru='$VAL_PII' · tool='$T_PII' · endpoint(operator)='$H_PII'"
if [ -z "$H_PII" ]; then
  note "INCONCLUSIVO: o endpoint não entregou valor para esta tag — ou o portão de"
  note "  namespace do POOL desta sessão não inclui \`session\`, ou a regra a esconde."
  note "  Sem oráculo, não julgo. Confira em /config/masking e no pool da sessão."
  incon=1
elif [ "$H_PII" = "$VAL_PII" ]; then
  note "INCONCLUSIVO: o endpoint devolveu o valor CRU — nenhuma regra viva alcança"
  note "  \`*.cpf\`. Neste estado, porta aberta e porta fechada são indistinguíveis."
  note "  Ative a regra em /config/masking e repita."
  incon=1
elif [ "$T_PII" = "$VAL_PII" ]; then
  note "✗ o tool devolveu o valor CRU enquanto o endpoint mascarou — A SEGUNDA"
  note "  PORTA ESTÁ ABERTA. É o defeito exato do ADR §1.5."
  fail=1
elif [ "$T_PII" = "$H_PII" ]; then
  note "✓ mascarada e IGUAL ao oráculo"
else
  note "✗ mascarada, mas DIFERENTE do endpoint — duas políticas outra vez, agora"
  note "  divergindo no grau. O ponto da extração era não ter duas."
  fail=1
fi
echo

# ── C. testemunha negativa ───────────────────────────────────────────────────
echo "C · \`$TAG_WIT\` (nenhuma regra casa) volta em CLARO:"
T_WIT=$(val_tool "$TAG_WIT")
if [ -z "$T_WIT" ]; then
  note "INCONCLUSIVO: a testemunha não veio no snapshot"
  incon=1
elif [ "$T_WIT" = "$VAL_WIT" ]; then
  note "✓ '$T_WIT' — o conserto não foi 'mascare tudo'"
else
  note "✗ veio '$T_WIT' em vez de '$VAL_WIT' — máscara aplicada onde nenhuma regra"
  note "  pede. Um blanket-mask passaria no ramo B sozinho; é este que o pega."
  note "  (se o default do tenant já foi invertido para hidden, este ramo precisa"
  note "   de revisão — a V4 muda o que é 'correto' aqui)"
  fail=1
fi
echo

# ── D. agent.* fora ──────────────────────────────────────────────────────────
echo "D · \`$TAG_AGT\` NÃO aparece no snapshot:"
if has_tool "$TAG_AGT"; then
  note "✗ presente — visibilidade por participante vazando pelo tool"
  fail=1
else
  note "✓ ausente (e o ramo C prova que o produtor emite — ausência aqui é política,"
  note "  não silêncio)"
fi
echo

# ── E. hidden é CONTADO, não sumido ──────────────────────────────────────────
echo "E · \`$TAG_HID\` permanece na linha com value null:"
HID_LIVE=$(echo "$HTTP" | jq -e --arg t "$TAG_HID" '(.customer_context.context_withheld.by_rule // []) | index($t) != null' >/dev/null 2>&1 && echo yes || echo no)
if [ "$HID_LIVE" = "no" ]; then
  note "INCONCLUSIVO: o endpoint não reteve esta tag por REGRA — o glob"
  note "  \`*.resume_token\` (hidden) não está vivo. Sem ele não há 'hidden' a contar."
  incon=1
elif ! has_tool "$TAG_HID"; then
  note "✗ a chave SUMIU do snapshot do tool — 'nunca foi escrito' e 'existe e está"
  note "  oculto' voltam a ser indistinguíveis, que é o defeito que a V1 fechou."
  fail=1
else
  hv=$(echo "$TOOL" | jq -r ".customer_context.context_snapshot[\"$TAG_HID\"].value")
  hc=$(echo "$TOOL" | jq -r '.customer_context.context_masking.hidden_count // 0')
  note "value=$hv · hidden_count=$hc"
  if [ "$hv" = "null" ] && [ "${hc:-0}" -ge 1 ]; then
    note "✓ chave preservada, valor nulo, e o contador diz quantos"
  else
    note "✗ esperado value=null com hidden_count ≥ 1"
    fail=1
  fi
fi
echo

# ── F. aritmética ────────────────────────────────────────────────────────────
echo "F · context_masking.total == campos não-\`agent.\` no hash CRU:"
# `grep -c -v` contaria a linha VAZIA que o redis-cli deixa no fim como se fosse
# um campo — o denominador nasceria +1 e a aritmética reprovaria código correto.
RAW_N=$(redis --raw HKEYS "$CTX" | tr -d '\r' | grep -v '^agent\.' | grep -c .)
TOT=$(echo "$TOOL" | jq -r '.customer_context.context_masking.total // empty')
if [ -z "$TOT" ]; then
  note "✗ \`context_masking\` AUSENTE — a máscara não se anuncia, e 'veio ***' fica"
  note "  indistinguível de 'o campo é assim'. Build antigo? O campo é novo."
  fail=1
elif [ "$TOT" = "$RAW_N" ]; then
  note "✓ total=$TOT == cru não-agent=$RAW_N"
else
  note "✗ total=$TOT · cru não-agent=$RAW_N — há caminho de saída não contabilizado"
  note "  (lembrete: só \`agent.*\` sai antes do total, de propósito)"
  fail=1
fi

echo
[ "$fail"  != 0 ] && { echo "GATE supervisor_tool_masking: REPROVADO"; exit 1; }
[ "$incon" != 0 ] && { echo "GATE supervisor_tool_masking: INCONCLUSIVO"; exit 2; }
echo "GATE supervisor_tool_masking: OK"; exit 0
