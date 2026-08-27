#!/usr/bin/env bash
# ==============================================================================
# probe_nav_grant_first.sh — o menu tem UM portao, e a porta larga e DECLARADA?
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Ate 2026-08-27 o menu tinha TRES mecanismos empilhados, e dois deles eram invisiveis
# para quem lia so o `Sidebar.tsx`:
#
#   1. `roles:` por item/grupo — portao de PAPEL, nao editavel pela tela de Acesso;
#   2. dentro de `passesAbacRule`, o ramo nao-estrito liberava para papel
#      admin/supervisor;
#   3. o MESMO ramo liberava para `module_config` VAZIO — bastava um usuario sem grants
#      para ver a plataforma inteira, com o menu parecendo normal.
#
# O (1) fazia grant concedido ficar INERTE (o cabecalho barrava antes da ABAC); o (3)
# era o mais silencioso dos tres. Este probe existe para que nenhum dos tres volte.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S1  qualquer `roles:` de volta ao `Sidebar.tsx` (fora de comentario);
#   S2  `passesAbacRule` voltar a decidir por PAPEL;
#   S3  `passesAbacRule` voltar a liberar por config VAZIO;
#   S4  a flag `strict` de volta — ela e o caminho de regressao mais provavel, porque
#       basta uma entrada nova ESQUECER de marca-la para os dois bypasses voltarem.
#
# TESTEMUNHA DE PRESENCA (sem ela, "nao achei nada" seria indistinguivel de "o arquivo
# mudou de lugar e o grep nao casa mais"):
#   S5  o arquivo existe, tem regras `abac:`, e a porta larga DECLARADA (`unrestricted`)
#       esta implementada — senao o portao ficou fechado sem saida nenhuma.
#
# ESTRUTURAL de proposito: a proposicao e sobre a FORMA da decisao ("existe um so
# portao"), e uma simulacao em bash seria uma segunda implementacao da regra — o
# defeito que este arco passou o tempo todo evitando.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SIDEBAR="$ROOT/packages/platform-ui/src/shell/Sidebar.tsx"
PERMS="$ROOT/packages/platform-ui/src/lib/permissions.ts"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

printf '\033[1mprobe: o menu e grant-first, com porta larga declarada\033[0m\n'

for f in "$SIDEBAR" "$PERMS"; do
  [ -f "$f" ] || { inc "arquivo ausente: $f"; exit 2; }
done

# ── S5 — testemunhas de presenca, ANTES das ausencias ───────────────────────
sec "S5 - testemunhas de presenca"
N_ABAC="$(grep -c 'abac:' "$SIDEBAR" || true)"
if [ "${N_ABAC:-0}" -lt 10 ]; then
  inc "so $N_ABAC regras \`abac:\` no Sidebar — o arquivo mudou de forma?"
  info "Sem regras, todas as ausencias abaixo passariam por vacuidade."
  exit 2
fi
ok "$N_ABAC regras \`abac:\` no Sidebar — ha o que gatear"

if grep -q 'unrestricted === true' "$PERMS"; then
  ok "a porta larga DECLARADA (\`unrestricted\`) esta implementada em passesAbacRule"
else
  bad "passesAbacRule nao honra \`unrestricted\`"
  info "Sem ela o portao fechou sem saida: nenhum principal consegue ver tudo, e a"
  info "decisao do dono ('a porta larga e o claim, declarado') vira meia decisao."
fi

# ── S1 — nenhum portao de papel no menu ─────────────────────────────────────
sec "S1 - nenhum \`roles:\` no Sidebar (fora de comentario)"
ROLES="$(grep -nE '^[[:space:]]*roles:[[:space:]]*\[' "$SIDEBAR" | grep -v '^[0-9]*:[[:space:]]*//' || true)"
if [ -z "$ROLES" ]; then
  ok "nenhum portao de papel"
else
  bad "portao(oes) de papel de volta:"
  printf '%s\n' "$ROLES" | sed 's/^/                 /'
  info "Enquanto existir, conceder o campo do filho nao muda o que a pessoa ve."
fi

# ── S2/S3 — o portao nao decide por papel nem por config vazio ──────────────
sec "S2/S3 - passesAbacRule nao volta a liberar por papel ou por config vazio"
CORPO="$(awk '/export function passesAbacRule/,/^}/' "$PERMS")"
if [ -z "$CORPO" ]; then
  inc "nao consegui isolar o corpo de passesAbacRule — assinatura mudou?"
else
  if printf '%s' "$CORPO" | grep -qE "'admin'|'supervisor'"; then
    bad "S2: passesAbacRule voltou a citar papel"
    printf '%s' "$CORPO" | grep -nE "'admin'|'supervisor'" | sed 's/^/                 /'
  else
    ok "S2: nenhuma decisao por papel"
  fi
  if printf '%s' "$CORPO" | grep -q 'Object.keys(moduleConfig)'; then
    bad "S3: passesAbacRule voltou a inspecionar se o config esta VAZIO"
    info "Config vazio liberando e o bypass silencioso: basta um usuario sem grants."
  else
    ok "S3: config vazio nao e mais uma autorizacao"
  fi
fi

# ── S4 — a flag `strict` nao volta ──────────────────────────────────────────
sec "S4 - a flag \`strict\` nao volta"
# ⚠️ `\bstrict\b`, nao `strict`: sem a fronteira de palavra o padrao casa
# `unrestricted` — e o gate reprovaria justamente o conserto que ele deveria proteger.
S4="$(grep -nE '\bstrict\b' "$PERMS" "$SIDEBAR" | grep -v '//' | grep -v '\*' || true)"
if [ -z "$S4" ]; then
  ok "nenhuma flag \`strict\` em codigo"
else
  bad "flag \`strict\` de volta:"
  printf '%s\n' "$S4" | sed 's/^/                 /'
  info "E o caminho de regressao mais provavel: com a flag, basta uma entrada nova"
  info "ESQUECER de marca-la para os dois bypasses voltarem, em silencio."
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - um portao (o grant), uma porta larga (declarada).\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
