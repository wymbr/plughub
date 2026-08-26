#!/usr/bin/env bash
# q_type_catalog_state.sh — estado VIVO dos inventários de categoria (V2, arco ALLOWLIST)
#
# Responde QUATRO perguntas, cada uma com testemunha de presença ao lado:
#   1. o catálogo de tipos (`masking.types`) já existe?           → esperado AUSENTE
#   2. a tela gravou chaves-fantasma (`rule.iban`/`rule.passport`)? → contador de AUSÊNCIA
#   3. quais ContextMaskingType a config VIVA realmente usa?        → alcançabilidade (gate §7)
#   4. seed × config viva divergem (D7)?                            → nos DOIS sentidos
#
# Roda do HOST, sem build. GET do config-api é anônimo (router.py:170, sem Depends).
set -u

CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"

RAW="$(curl -s --max-time 10 "${CFG}/config/masking?tenant_id=${TENANT}")"

if [ -z "$RAW" ]; then
  echo "INCONCLUSIVO — config-api em ${CFG} não respondeu (nada a julgar)"
  exit 2
fi

NKEYS="$(echo "$RAW" | jq -r 'if has("entries") then (.entries | length) else "ERR" end' 2>/dev/null)"
if [ "$NKEYS" = "ERR" ] || [ -z "$NKEYS" ]; then
  echo "INCONCLUSIVO — resposta sem campo .entries; leitor quebrado, não ausência de dado"
  echo "corpo: $(echo "$RAW" | head -c 300)"
  exit 2
fi
if [ "$NKEYS" = "0" ]; then
  echo "INCONCLUSIVO — ns masking VAZIA (0 chaves). Base não semeada; zero fantasma aqui não prova nada"
  exit 2
fi

echo "══ ns masking @ ${TENANT} ═════════════════════════════════════"
echo "TOTAL_KEYS=${NKEYS}   (testemunha de presença)"
echo "$RAW" | jq -r '.entries | keys[]' | sed 's/^/  · /'
echo

# ── 1. o catálogo já existe? ──────────────────────────────────────────────────
HAS_TYPES="$(echo "$RAW" | jq -r 'if (.entries | has("types")) then "PRESENTE" else "AUSENTE" end')"
echo "── 1. masking.types ──────────────────────────────────────────"
echo "CATALOGO=${HAS_TYPES}"
echo

# ── 2. chaves-fantasma gravadas pela tela ─────────────────────────────────────
echo "── 2. chaves rule.* gravadas ─────────────────────────────────"
RULE_KEYS="$(echo "$RAW" | jq -r '.entries | keys[] | select(startswith("rule."))')"
N_RULE="$(echo "$RAW" | jq -r '[.entries | keys[] | select(startswith("rule."))] | length')"
N_GHOST="$(echo "$RAW" | jq -r '[.entries | keys[] | select(. == "rule.iban" or . == "rule.passport")] | length')"
echo "RULE_KEYS_TOTAL=${N_RULE}   FANTASMAS_GRAVADOS=${N_GHOST}"
if [ "$N_RULE" != "0" ]; then
  echo "$RULE_KEYS" | sed 's/^/  · /'
fi
echo

# ── 3. alcançabilidade dos ContextMaskingType ─────────────────────────────────
echo "── 3. ContextMaskingType — usados na config VIVA ─────────────"
NRULES="$(echo "$RAW" | jq -r 'if (.entries.context_rules | type) == "object" then (.entries.context_rules.rules | length) else "ERR" end')"
if [ "$NRULES" = "ERR" ]; then
  echo "INCONCLUSIVO — context_rules ausente ou não é objeto"
  exit 2
fi
echo "REGRAS_VIVAS=${NRULES}"
echo "$RAW" | jq -r '.entries.context_rules.rules | group_by(.type)[] | "  · \(.[0].type)  ×\(length)"'
echo "TIPOS_USADOS=$(echo "$RAW" | jq -r '[.entries.context_rules.rules[].type] | unique | length') de 9"
echo "NAO_USADOS: $(echo "$RAW" | jq -r '(["plain","hidden","full","last_2","last_4","first_1","first_word","email_domain","financial"] - ([.entries.context_rules.rules[].type] | unique)) | join(", ")')"
echo "DEFAULT_UNMATCHED=$(echo "$RAW" | jq -r 'if (.entries.context_rules | has("default_unmatched_operator")) then .entries.context_rules.default_unmatched_operator else "AUSENTE" end')"
echo

# ── 4. seed × config viva (D7) ────────────────────────────────────────────────
echo "── 4. seed × viva (D7) ───────────────────────────────────────"
SEED_FILE="$(mktemp)"
cat > "$SEED_FILE" <<'SEEDEOF'
caller.customer_id
caller.cpf
caller.cnpj
caller.telefone
caller.email
account.numero_contrato
account.valor_fatura
account.limite_credito
session.numero_cartao
session.vencimento_cartao
session.limite_solicitado
*.resume_token
*.cpf
*.cnpj
*.telefone
*.email
*.numero_cartao
*.vencimento_cartao
*.limite_solicitado
*.limite_aprovado
caller.*
account.*
*
SEEDEOF
LIVE_FILE="$(mktemp)"
echo "$RAW" | jq -r '.entries.context_rules.rules[].pattern' > "$LIVE_FILE"

echo "SEED_N=$(wc -l < "$SEED_FILE")  VIVA_N=$(wc -l < "$LIVE_FILE")"
echo "SO_NO_SEED (o arquivo promete, a config não tem):"
comm -23 <(sort "$SEED_FILE") <(sort "$LIVE_FILE") | sed 's/^/  · /'
echo "SO_NA_VIVA (a config tem, o arquivo não promete):"
comm -13 <(sort "$SEED_FILE") <(sort "$LIVE_FILE") | sed 's/^/  · /'
rm -f "$SEED_FILE" "$LIVE_FILE"
echo
echo "══ fim ═══════════════════════════════════════════════════════"
