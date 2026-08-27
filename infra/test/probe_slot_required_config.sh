#!/usr/bin/env bash
# ==============================================================================
# probe_slot_required_config.sh — todo slot PROMOVIDO satisfaz o contrato do skill?
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Um skill pode declarar `config_params` com `required: true`. Isso e um CONTRATO
# entre o artefato e o deploy — mas medido em 2026-08-27 nada o impunha: o pool
# `survey_multi_ia` estava com o slot `current` promovido carregando
# `config_json: {"max_concurrent_sessions": 5}`, sem o `form_id` que
# `skill_survey_multi_v1` declara obrigatorio. O primeiro step do fluxo e um
# `form_get` com `form_id: "$.config.form_id"`, e o proprio comentario do skill diz
# "sem isso, form_get falha" — o step cai no `on_failure` e o fluxo morre calado.
#
# Declaracao sem mecanismo que a imponha e a familia de defeito que este repositorio
# persegue: o `required: true` estava escrito, correto, e valia nada.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   qualquer pool cujo slot `current` esteja promovido sem uma chave que o skill
#   dele declara `required`.
#
# TESTEMUNHAS (sem elas, "0 violacoes" e vacuo):
#   · ao menos UM pool com slot `current` promovido;
#   · ao menos UM skill declarando `required: true` — se nenhum declarar, o gate
#     nao tem contrato para conferir e diz isso, em vez de sair verde.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

REG="${REG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
H="x-tenant-id: $TENANT"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }

command -v jq >/dev/null || { inc "jq ausente"; exit 2; }

printf '\033[1mprobe: slot promovido x contrato do skill\033[0m\n'
printf '  agent-registry: %s   tenant: %s\n\n' "$REG" "$TENANT"

POOLS_JSON="$(curl -s --max-time 20 "$REG/v1/pools" -H "$H")"
POOLS="$(printf '%s' "$POOLS_JSON" | jq -r '(.pools // [])[].pool_id' 2>/dev/null)"
N_POOLS="$(printf '%s' "$POOLS" | grep -c . || true)"
if [ "${N_POOLS:-0}" -eq 0 ]; then
  inc "o registry nao devolveu pools — sem populacao nao ha experimento"
  info "resposta: $(printf '%s' "$POOLS_JSON" | head -c 160)"
  exit 2
fi
info "pools no tenant: $N_POOLS"

# cache de config_params por skill: evita N chamadas iguais
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

req_keys() {  # $1 = skill_id -> uma chave obrigatoria por linha
  local sid="$1" f="$TMP/skill_$1.json"
  [ -f "$f" ] || curl -s --max-time 20 "$REG/v1/skills/$sid" -H "$H" > "$f"
  jq -r '[.config_params // []][0][] | select(.required == true) | .key' "$f" 2>/dev/null
}

n_slots=0
n_contratos=0
violacoes=""

while IFS= read -r pid; do
  [ -z "$pid" ] && continue
  S="$(curl -s --max-time 20 "$REG/v1/pools/$pid/slots" -H "$H")"
  SKILL="$(printf '%s' "$S" | jq -r '.slots.current.skill_id // empty' 2>/dev/null)"
  [ -z "$SKILL" ] && continue
  n_slots=$((n_slots + 1))
  CFG="$(printf '%s' "$S" | jq -c '.slots.current.config_json // {}' 2>/dev/null)"
  while IFS= read -r key; do
    [ -z "$key" ] && continue
    n_contratos=$((n_contratos + 1))
    have="$(printf '%s' "$CFG" | jq -r --arg k "$key" 'if has($k) and (.[$k] != null) and (.[$k] != "") then "sim" else "nao" end' 2>/dev/null)"
    if [ "$have" != "sim" ]; then
      violacoes="$violacoes\n  $pid  (skill $SKILL)  falta \`$key\`"
    fi
  done <<< "$(req_keys "$SKILL")"
done <<< "$POOLS"

info "pools com slot \`current\` promovido: $n_slots"
info "chaves obrigatorias conferidas: $n_contratos"

# ── testemunhas ──────────────────────────────────────────────────────────────
if [ "$n_slots" -eq 0 ]; then
  inc "nenhum pool tem slot \`current\` promovido — nao ha deploy a conferir"
  exit 2
fi
if [ "$n_contratos" -eq 0 ]; then
  inc "nenhum skill deployado declara \`config_params.required\` — nao ha CONTRATO"
  info "a conferir. Verde aqui seria vacuo: o gate nao testou proposicao nenhuma."
  exit 2
fi

if [ -n "$violacoes" ]; then
  bad "ha slot promovido sem chave que o skill declara obrigatoria:"
  printf '%b\n' "$violacoes" | sed 's/^/  /'
  info ""
  info "Consequencia tipica: o step que le \`\$.config.<chave>\` cai no \`on_failure\`"
  info "e o fluxo morre CALADO. Corrigir com set-next + promote carregando a chave."
else
  ok "todos os $n_slots slots promovidos satisfazem as $n_contratos chaves obrigatorias"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - nenhum deploy viola o contrato de config do seu skill.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver acima.\n'
fi
exit "$fail"
