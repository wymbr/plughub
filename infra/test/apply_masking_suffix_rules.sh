#!/usr/bin/env bash
# apply_masking_suffix_rules.sh — aplica as regras de SUFIXO na config VIVA.
#
# Por que existe, e por que não é um curl colado (2026-08-26):
#
#   1. **Seed é seed-if-absent.** `masking.context_rules` já existe no tenant, então
#      editar `config-api/seed.py` é NO-OP aqui — o arquivo passa a valer só em base
#      nova. Quem já roda precisa da API oficial, que é a invariante de
#      provisionamento ("todo provisionamento escreve ATRAVÉS da API do store").
#   2. **Cadeia de curl colada no terminal não roda como se lê** (`A=… TOK=$(…) curl`
#      vira atribuição PREFIXANDO o comando, com as expansões antes). Já custou uma
#      sessão neste repo.
#   3. **Antes e depois, sempre.** Escrita de política que não imprime o valor
#      anterior não deixa como saber o que mudou nem como desfazer.
#
# O que aplica: regras de SUFIXO, que protegem por TIPO DE CAMPO em vez de por
# namespace. Motivo medido em `sweep_ctx_tags.sh`: `session.cpf` e
# `journey.numero_cartao` estavam em CLARO, enquanto `caller.cpf` e
# `session.numero_cartao` estavam mascarados — porque `session.*`/`journey.*` não
# podem ter catch-all (derrubaria a tela de aprovação) e é exatamente ali que o
# `delegate.context` dos workflows deposita os campos.
#
# Idempotente: rodar duas vezes não duplica (dedup por `pattern`+`role`).
set -u

CFG=${CFG:-http://localhost:3600}
TENANT=${TENANT:-tenant_demo}
ADMIN=${CONFIG_ADMIN_TOKEN:-demo_config_admin_token}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

NEW_RULES='[
  {"pattern":"*.resume_token",      "role":"operator","type":"hidden",      "label":"Token de retomada — CAPACIDADE, nunca retida"},
  {"pattern":"*.cpf",               "role":"operator","type":"last_2",      "label":"CPF em qualquer namespace"},
  {"pattern":"*.cnpj",              "role":"operator","type":"last_2",      "label":"CNPJ em qualquer namespace"},
  {"pattern":"*.telefone",          "role":"operator","type":"last_4",      "label":"Telefone em qualquer namespace"},
  {"pattern":"*.email",             "role":"operator","type":"email_domain","label":"E-mail em qualquer namespace"},
  {"pattern":"*.numero_cartao",     "role":"operator","type":"last_4",      "label":"Número de cartão em qualquer namespace"},
  {"pattern":"*.vencimento_cartao", "role":"operator","type":"last_2",      "label":"Vencimento de cartão em qualquer namespace"},
  {"pattern":"*.limite_solicitado", "role":"operator","type":"financial",   "label":"Valor solicitado em qualquer namespace"},
  {"pattern":"*.limite_aprovado",   "role":"operator","type":"financial",   "label":"Limite aprovado em qualquer namespace"}
]'

# ── 1. valor ANTERIOR ────────────────────────────────────────────────────────
#
# ⚠️ `GET /config/{ns}` devolve `entries.{key}` com o valor DIRETO — **não** aninhado
# em `.value` (esse envelope é do corpo do PUT, não da resposta do GET). A primeira
# versão deste script lia `.entries.context_rules.value`, achava vazio e concluía
# *"a chave não existe"* — e a conclusão errada era grave, porque significaria que a
# página de Masking é decorativa e o que vale é o default do CÓDIGO. **O leitor
# quebrado e a ausência real produzem exatamente a mesma saída.** O `select` abaixo
# é o que impede o script de aceitar um objeto que não seja a config.
CUR=$(curl -s "$CFG/config/masking?tenant_id=$TENANT" \
      | jq -c '.entries.context_rules as $c | ($c.value // $c) | select(type=="object" and has("rules")) // empty')
if [ -z "$CUR" ]; then
  echo "REPROVADO: não consegui ler masking.context_rules de $CFG"
  echo "           (config-api de pé? namespace semeado?)"
  exit 1
fi
N_BEFORE=$(echo "$CUR" | jq '.rules | length')
echo "antes:  $N_BEFORE regras"
echo "$CUR" | jq -r '.rules[] | "        \(.pattern)  [\(.role)]  → \(.type)"'
echo

# ── 2. merge idempotente ─────────────────────────────────────────────────────
# Insere as novas ANTES dos catch-alls de namespace. A ordem não decide mais nada
# (o score é que decide, e sufixo=15 > prefixo=10), mas mantém a lista legível.
MERGED=$(jq -c --argjson new "$NEW_RULES" '
  .rules as $old
  | ($new | map(.pattern + "|" + .role)) as $newkeys
  | .rules = (($old | map(select((.pattern + "|" + .role) as $k | $newkeys | index($k) | not))) as $kept
      | ($kept | map(select(.pattern | test("^\\*$|\\.\\*$") | not)))
        + $new
        + ($kept | map(select(.pattern | test("^\\*$|\\.\\*$")))))
' <<< "$CUR")

N_AFTER=$(echo "$MERGED" | jq '.rules | length')

# ── 3. escrita pela API oficial ──────────────────────────────────────────────
OUT=$(curl -s -X PUT "$CFG/config/masking/context_rules" \
      -H "x-admin-token: $ADMIN" -H 'content-type: application/json' \
      -d "$(jq -nc --argjson v "$MERGED" --arg t "$TENANT" '{value:$v, tenant_id:$t}')")

# ── 4. LEIA DE VOLTA — o 200 não é prova ─────────────────────────────────────
BACK=$(curl -s "$CFG/config/masking?tenant_id=$TENANT" \
       | jq -c '.entries.context_rules as $c | ($c.value // $c) | select(type=="object" and has("rules")) // empty')
N_BACK=$(echo "$BACK" | jq '.rules | length' 2>/dev/null || echo 0)
echo "depois: $N_BACK regras (esperado $N_AFTER)"

MISSING=$(echo "$BACK" | jq -r --argjson new "$NEW_RULES" '
  ($new | map(.pattern)) - ([.rules[].pattern]) | .[]' 2>/dev/null)
if [ -n "$MISSING" ]; then
  echo
  echo "REPROVADO: estes padrões não voltaram na leitura:"
  echo "$MISSING" | sed 's/^/  /'
  echo "resposta do PUT: $OUT"
  exit 1
fi

echo
echo "OK — regras de sufixo aplicadas e CONFERIDAS na leitura."
echo "⚠️  O mcp-server cacheia a config de masking por 60s (CONTEXT_MASKING_CACHE_TTL_MS)."
echo "    Medir antes disso lê a política ANTIGA e parece que a escrita não pegou."
exit 0
