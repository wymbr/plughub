#!/usr/bin/env bash
# probe_context_withheld.sh — a omissão do ContextStore deixou de ser MUDA?
#
# V1 do arco ALLOWLIST (`docs/adr/adr-contextstore-allowlist.md` §D5).
#
# ── O que este gate protege ──────────────────────────────────────────────────
#
# `applyContextMaskingDynamic` tinha dois `continue` que faziam o campo sumir sem
# dizer: o PORTÃO de namespace (config do pool) e o tipo `hidden` (regra do tenant).
# Enquanto o default for `plain` isso é ruído; no dia da inversão para
# deny-by-default, "não existe" e "existe e você não pode ver" ficam
# indistinguíveis — e a inversão trocaria um vazamento de PII por uma quebra MUDA
# de tela. O ADR marca a ordem como inegociável: **V1 antes de V4**.
#
# ── Por que INJETA, em vez de olhar o que houver ─────────────────────────────
#
# Mesma razão do `probe_context_visibility.sh`: um teste de igualdade só julga se a
# população contiver o caso em que A ≠ B. Sessão comum só tem `core.pool.*`,
# que está DENTRO do default — nada é retido, e um produtor que nunca emite passa.
# Injeta-se UM caso de cada CAUSA, porque elas se consertam em telas diferentes.
#
# ── Os ramos ─────────────────────────────────────────────────────────────────
#
#   A. TESTEMUNHA DE PRESENÇA — há ContextStore e `total > 0`.
#   B. by_pool_scope — `caller.nome` (ns fora do default) é CONTADO, não sumido.
#   C. by_rule       — `session.probe.resume_token` (casa `*.resume_token` → hidden)
#                      passa o portão e é retido pela REGRA. Separa as duas causas.
#   D. 🔴 TESTEMUNHA NEGATIVA — para o ADMIN (supervisor_role) as duas listas têm de
#      vir VAZIAS. É o ramo que reprova um produtor que "conta" sempre. Sem ele,
#      `by_rule: [tudo]` passaria em A–C.
#   E. DISJUNÇÃO — nenhuma tag pode estar entregue E retida ao mesmo tempo.
#   F. ARITMÉTICA — total == entregues + by_rule + by_pool_scope, exato. Toda tag
#      não-`agent.*` cai em exatamente um dos três; se não fecha, há caminho de
#      saída não contabilizado.
#
# Limpa o que injetou (HDEL) em qualquer saída.
#
# Uso:  bash infra/test/probe_context_withheld.sh <session_id>
#       (sem argumento, lista as sessões com ContextStore vivo)
set -u

DC=${DC:-docker compose -f docker-compose.demo.yml}
AUTH=${AUTH:-http://localhost:3202/auth}
MCP=${MCP:-http://localhost:3100}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}
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
if [ "$(redis --raw EXISTS "$CTX")" != "1" ]; then
  echo "INCONCLUSIVO: $CTX não existe (sessão fechada? TTL expirou?)"
  exit 2
fi

login() {
  curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | jq -r '.access_token // empty'
}
TOK_ADMIN=$(login "$ADMIN_EMAIL" "$ADMIN_PASS")
TOK_OP=$(login "$OP_EMAIL" "$OP_PASS")
[ -z "$TOK_ADMIN" ] && { echo "INCONCLUSIVO: login admin falhou (auth-api de pé?)"; exit 2; }
[ -z "$TOK_OP" ]    && { echo "INCONCLUSIVO: login operator falhou (OP_PASS=… sobrescreve)"; exit 2; }

# ── injeção: UM caso de cada CAUSA ───────────────────────────────────────────
# `caller.nome`               → ns `caller` fora do default ⇒ PORTÃO (config do pool)
# `session.probe.resume_token`→ ns `session` DENTRO do default, casa o glob de sufixo
#                               `*.resume_token` (type hidden) ⇒ REGRA (config do tenant)
# ⚠️ o sufixo casa em FRONTEIRA DE SEGMENTO: `session.probe_resume_token` (underscore)
#    NÃO casaria. É de propósito que o nome tem o ponto.
TAG_GATE="caller.nome"
TAG_RULE="session.probe.resume_token"

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkentry() { printf '{"value":"%s","confidence":1.0,"source":"probe","visibility":"agents_only","updated_at":"%s"}' "$1" "$NOW"; }

cleanup() { redis HDEL "$CTX" "$TAG_GATE" "$TAG_RULE" >/dev/null 2>&1 || true; }
trap cleanup EXIT

redis HSET "$CTX" "$TAG_GATE" "$(mkentry 'Maria Probe')"      >/dev/null
redis HSET "$CTX" "$TAG_RULE" "$(mkentry 'tok_probe_segredo')" >/dev/null
echo "injetado em $CTX: $TAG_GATE (portão) + $TAG_RULE (regra)"
echo

state() { curl -s "$MCP/api/supervisor_state/$SID" -H "Authorization: Bearer $1"; }
A=$(state "$TOK_ADMIN")
O=$(state "$TOK_OP")

jqx() { echo "$1" | jq -c "$2" 2>/dev/null; }
W_OP=$(jqx  "$O" '.customer_context.context_withheld // null')
W_ADM=$(jqx "$A" '.customer_context.context_withheld // null')
S_OP=$(jqx  "$O" '.customer_context.context_snapshot // {}')

fail=0
incon=0
note() { echo "  $*"; }
inlist() { echo "$1" | jq -e --arg t "$2" --arg k "$3" '(.[$k] // []) | index($t) != null' >/dev/null 2>&1; }

# ── A. testemunha de presença ────────────────────────────────────────────────
echo "A · o campo \`context_withheld\` existe e há população:"
if [ -z "$W_OP" ] || [ "$W_OP" = "null" ]; then
  note "✗ AUSENTE na resposta do operator — o endpoint não publica a retenção"
  note "  (build antigo? o campo é novo na V1)"
  fail=1
else
  tot=$(echo "$W_OP" | jq -r '.total // 0')
  note "total=$tot · by_rule=$(echo "$W_OP" | jq -r '.by_rule|length') · by_pool_scope=$(echo "$W_OP" | jq -r '.by_pool_scope|length')"
  if [ "${tot:-0}" -lt 2 ]; then
    note "INCONCLUSIVO: total=$tot — a injeção não chegou ao snapshot"
    incon=1
  else
    note "✓ presente"
  fi
fi
echo

# ── B. o portão CONTA, em vez de sumir ───────────────────────────────────────
echo "B · \`$TAG_GATE\` aparece em by_pool_scope (portão do pool):"
if [ "$W_OP" != "null" ] && inlist "$W_OP" "$TAG_GATE" by_pool_scope; then
  note "✓ contado"
else
  note "✗ NÃO contado — o campo sumiu em silêncio, que é exatamente o defeito da V1"
  fail=1
fi
echo

# ── C. a regra CONTA, e numa lista DIFERENTE ─────────────────────────────────
echo "C · \`$TAG_RULE\` aparece em by_rule (regra do tenant):"
if [ "$W_OP" != "null" ] && inlist "$W_OP" "$TAG_RULE" by_rule; then
  note "✓ contado na lista certa"
elif [ "$W_OP" != "null" ] && inlist "$W_OP" "$TAG_RULE" by_pool_scope; then
  note "✗ contado em by_pool_scope — as duas CAUSAS estão trocadas, e o operador"
  note "  seria mandado à tela errada para consertar"
  fail=1
else
  note "✗ NÃO contado — a regra \`*.resume_token\` (hidden) está viva na config?"
  note "  se não estiver, este ramo é INCONCLUSIVO, não falha: confira em /config/masking"
  fail=1
fi
echo

# ── D. TESTEMUNHA NEGATIVA — o supervisor não perde nada ─────────────────────
echo "D · para o admin (supervisor_role) as duas listas vêm VAZIAS:"
if [ -z "$W_ADM" ] || [ "$W_ADM" = "null" ]; then
  note "INCONCLUSIVO: admin não recebeu context_withheld"
  incon=1
else
  nr=$(echo "$W_ADM" | jq -r '.by_rule|length')
  np=$(echo "$W_ADM" | jq -r '.by_pool_scope|length')
  if [ "$nr" = "0" ] && [ "$np" = "0" ]; then
    note "✓ by_rule=0 · by_pool_scope=0 — nada retido de quem pode ver tudo"
  else
    note "✗ by_rule=$nr · by_pool_scope=$np — um produtor que conta SEMPRE passaria"
    note "  nos ramos A–C. É este ramo que o pega."
    fail=1
  fi
fi
echo

# ── E. disjunção — entregue e retido são conjuntos separados ─────────────────
# PREFLIGHT DO INSTRUMENTO: a expressão tem de acusar 1 quando HÁ duplicata e 0
# quando não há. Sem isto, um leitor quebrado e a ausência de duplicata produzem a
# mesma saída — foi exatamente o que aconteceu em 2026-08-26.
DUP_EXPR='[($w.by_rule + $w.by_pool_scope)[] as $tag | select($s | has($tag))] | length'
pf_hit=$(jq -n --argjson s '{"a":1}' --argjson w '{"by_rule":["a"],"by_pool_scope":[]}' "$DUP_EXPR" 2>/dev/null)
pf_mis=$(jq -n --argjson s '{"b":1}' --argjson w '{"by_rule":["a"],"by_pool_scope":[]}' "$DUP_EXPR" 2>/dev/null)
echo "E · nenhuma tag está entregue E retida:"
if [ "$pf_hit" != "1" ] || [ "$pf_mis" != "0" ]; then
  note "INCONCLUSIVO: o próprio detector não passa no controle (hit=$pf_hit, esperado 1;"
  note "  miss=$pf_mis, esperado 0). Não julgo o código com um instrumento cego."
  incon=1
elif [ "$W_OP" != "null" ]; then
  # ⚠️ `as $tag` é obrigatório. A forma "natural" — `select(($s|keys) | index(.))` —
  # é SEMPRE VERDADEIRA: dentro de `index(.)` o `.` é reavaliado contra a entrada do
  # pipe (o próprio array de chaves), então ela procura o array dentro dele mesmo,
  # acha na posição 0, e `0 != null` passa. Um ramo que não pode reprovar
  # corretamente — a família que este gate existe para pegar. Medido em 2026-08-26:
  # imprimiu "3 tags em ambos" com os conjuntos disjuntos, e só o ramo F (aritmética)
  # o desmentiu. É por isso que E e F medem a mesma coisa por caminhos diferentes.
  dup=$(jq -n --argjson s "$S_OP" --argjson w "$W_OP" "$DUP_EXPR" 2>/dev/null)
  if [ "${dup:-0}" = "0" ]; then
    note "✓ conjuntos disjuntos (detector conferido: hit=1 · miss=0)"
  else
    note "✗ $dup tag(s) em ambos — o contador inflaria a percepção de retenção"
    fail=1
  fi
else
  note "INCONCLUSIVO: sem context_withheld para comparar"; incon=1
fi
echo

# ── F. aritmética — a linha fecha ────────────────────────────────────────────
echo "F · total == entregues + by_rule + by_pool_scope:"
if [ "$W_OP" != "null" ]; then
  ent=$(echo "$S_OP" | jq 'length')
  tot=$(echo "$W_OP" | jq -r '.total')
  soma=$(( ent + $(echo "$W_OP" | jq -r '.by_rule|length') + $(echo "$W_OP" | jq -r '.by_pool_scope|length') ))
  note "entregues=$ent · soma=$soma · total=$tot"
  if [ "$soma" = "$tot" ]; then
    note "✓ fecha"
  else
    note "✗ NÃO fecha — há caminho de saída não contabilizado em applyContextMaskingDynamic"
    note "  (lembrete: \`agent.*\` sai ANTES do total, de propósito)"
    fail=1
  fi
else
  note "INCONCLUSIVO: sem context_withheld"; incon=1
fi

echo
[ "$fail"  != 0 ] && { echo "GATE context_withheld: REPROVADO"; exit 1; }
[ "$incon" != 0 ] && { echo "GATE context_withheld: INCONCLUSIVO"; exit 2; }
echo "GATE context_withheld: OK"; exit 0
