#!/usr/bin/env bash
# probe_context_visibility.sh — o portão de namespace do ContextStore é REAL?
#
# ── Por que este probe existe (2026-08-26) ───────────────────────────────────
#
# Pergunta do dono: *"o Console respeita `Context Store Visibility`, ou exibe
# tudo?"*. A tentativa óbvia — abrir a aba Contexto como admin e como operator e
# comparar — deu as duas telas IDÊNTICAS, e isso **não julgou nada**: os únicos
# tags presentes na sessão eram `session.pool.*`, que estão DENTRO do default
# (`["service","session"]`). O portão nunca teve o que barrar.
#
# **Um teste de igualdade só julga se a população contiver o caso em que A ≠ B.**
# Este probe INJETA esse caso — dois tags do namespace `caller`, que fica fora do
# default — e só então compara. Sem a injeção, "respeita" e "não filtra nada"
# produzem a mesma tela.
#
# ── O que cada ramo prova ────────────────────────────────────────────────────
#
#   A. TESTEMUNHA DE PRESENÇA — os dois papéis leem o snapshot (há `session.*`).
#      Sem ela, um endpoint quebrado devolveria "nada" e passaria por "filtrou".
#   B. PORTÃO — o operator NÃO vê `caller.nome` (ns `caller` fora do default).
#      É o ramo que reprova se a config for decorativa.
#   C. ALLOW-TAG — o operator VÊ `caller.customer_id` em CLARO, apesar do ns
#      barrado. Sem ele, "filtra certo" e "filtra demais" ficam iguais — e o
#      Console perde o id que carrega histórico/360.
#   D. BYPASS DE SUPERVISOR — o admin vê os DOIS. É o controle positivo: sem ele,
#      um bug que escondesse tudo de todos passaria nos ramos A–C.
#
# Limpa o que injetou (HDEL) em qualquer saída.
#
# Uso:  bash infra/test/probe_context_visibility.sh <session_id>
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
  echo "INCONCLUSIVO: $CTX não existe (sessão fechada? TTL de 24h expirou?)"
  exit 2
fi

# ── tokens ───────────────────────────────────────────────────────────────────
login() {
  curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | jq -r '.access_token // empty'
}
TOK_ADMIN=$(login "$ADMIN_EMAIL" "$ADMIN_PASS")
TOK_OP=$(login "$OP_EMAIL" "$OP_PASS")
[ -z "$TOK_ADMIN" ] && { echo "INCONCLUSIVO: login admin falhou (auth-api de pé?)"; exit 2; }
[ -z "$TOK_OP" ]    && { echo "INCONCLUSIVO: login operator falhou (senha mudou? OP_PASS=… sobrescreve)"; exit 2; }

# ── injeção do DISCRIMINADOR ─────────────────────────────────────────────────
# `caller.nome` → PII, namespace fora do default ⇒ o operator não pode ver.
# `caller.customer_id` → id interno, no `operator_allow_tags` default ⇒ ele PODE,
# em claro, apesar do namespace barrado. Os dois no MESMO namespace de propósito:
# é o par que separa "portão por namespace" de "bloqueio por namespace".
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkentry() { printf '{"value":"%s","confidence":1.0,"source":"probe","visibility":"agents_only","updated_at":"%s"}' "$1" "$NOW"; }

cleanup() { redis HDEL "$CTX" caller.nome caller.customer_id >/dev/null 2>&1 || true; }
trap cleanup EXIT

redis HSET "$CTX" caller.nome            "$(mkentry 'Maria Probe')"  >/dev/null
redis HSET "$CTX" caller.customer_id     "$(mkentry 'cus_probe_42')" >/dev/null
echo "injetado em $CTX: caller.nome + caller.customer_id"
echo

snap() {
  curl -s "$MCP/api/supervisor_state/$SID" -H "Authorization: Bearer $1" \
    | jq -c '.customer_context.context_snapshot // {}'
}
S_ADMIN=$(snap "$TOK_ADMIN")
S_OP=$(snap "$TOK_OP")

has() { echo "$1" | jq -e --arg t "$2" 'has($t)' >/dev/null 2>&1; }
val() { echo "$1" | jq -r --arg t "$2" '.[$t].value // "—"'; }

fail=0
incon=0
note() { echo "  $*"; }

# ── A. testemunha de presença ────────────────────────────────────────────────
echo "A · os dois papéis leem o snapshot (testemunha):"
n_admin=$(echo "$S_ADMIN" | jq 'length')
n_op=$(echo "$S_OP" | jq 'length')
note "admin=$n_admin campos · operator=$n_op campos"
if [ "${n_admin:-0}" -lt 1 ] || [ "${n_op:-0}" -lt 1 ]; then
  note "INCONCLUSIVO: snapshot vazio para algum papel — endpoint/token, não política"
  incon=1
else
  note "✓ ambos leem"
fi
echo

# ── B. o portão de namespace ─────────────────────────────────────────────────
echo "B · o operator NÃO vê \`caller.nome\` (ns fora do default):"
if has "$S_OP" caller.nome; then
  note "✗ VIU: '$(val "$S_OP" caller.nome)' — o portão de namespace não está agindo;"
  note "  a config \`Context Store Visibility\` é decorativa neste caminho"
  fail=1
else
  note "✓ ausente do snapshot do operator"
fi
echo

# ── C. a allow-tag ───────────────────────────────────────────────────────────
echo "C · o operator VÊ \`caller.customer_id\` em claro (allow-tag):"
if has "$S_OP" caller.customer_id; then
  v=$(val "$S_OP" caller.customer_id)
  if [ "$v" = "cus_probe_42" ]; then
    note "✓ '$v' em claro"
  else
    note "✗ presente mas MASCARADO ('$v') — a allow-tag deveria passar por cima"
    note "  do namespace E das regras de masking"
    fail=1
  fi
else
  note "✗ ausente — filtrou DEMAIS: o Console perde o id que carrega histórico/360"
  fail=1
fi
echo

# ── D. controle positivo: o supervisor passa por cima ────────────────────────
echo "D · o admin (supervisor_role) vê os dois, em claro:"
if has "$S_ADMIN" caller.nome && has "$S_ADMIN" caller.customer_id; then
  note "✓ nome='$(val "$S_ADMIN" caller.nome)' · id='$(val "$S_ADMIN" caller.customer_id)'"
  if [ "$(val "$S_ADMIN" caller.nome)" != "Maria Probe" ]; then
    note "  ⚠ o admin vê MASCARADO — o bypass de supervisor não está valendo"
    note "    (falha SEGURA: mascara demais. Ninguém abre chamado por ver ***)"
    fail=1
  fi
else
  note "✗ o admin NÃO vê os dois — sem este controle, um bug que escondesse tudo"
  note "  de todos passaria nos ramos A–C"
  fail=1
fi

echo
[ "$fail" != 0 ] && { echo "GATE context_visibility: REPROVADO"; exit 1; }
[ "$incon" != 0 ] && { echo "GATE context_visibility: INCONCLUSIVO"; exit 2; }
echo "GATE context_visibility: OK"; exit 0
