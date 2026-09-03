#!/usr/bin/env bash
#
# probe_contextstore_cadastro.sh — nenhuma tag do ContextStore é escrita fora do
# cadastro, e o instrumento que mede isso não perde tag por distância (ALW-11).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# O portão da V4 recusa PUBLICAR uma skill que escreva tag não cadastrada. Ele lê
# **YAML de skill** — e por isso não alcança a metade que carrega mais peso:
# **código de plataforma**, que roda para todo tenant e pode tocar o `core.*`,
# o namespace fechado da plataforma. Código não se publica.
#
# Medido em 2026-09-02, antes desta promoção: **1 tag não declarada**, e ela é o
# caso exemplar — `core.workflow.reviewer_id`, escrita pela evaluation-api em duas
# rotas, **sem leitor nenhum**, ao lado de duas irmãs devidamente declaradas no
# MESMO dicionário. Valor: `caller_user_id`, que identifica uma pessoa; gravado
# `agents_only`; sem `tipo` no cadastro, logo sem máscara por papel e sem classe
# LGPD. Alguém acrescentou um campo e não o registrou, e nada ficava vermelho.
#
# O QUE ESTE PROBE PODE REPROVAR
# ==============================
#   A  o EXTRATOR voltar a perder tag por distância                  → VERMELHO
#      (regressão do defeito achado em 2026-09-03 — ver abaixo)
#   B  tag escrita por CÓDIGO de plataforma fora da SEMENTE          → VERMELHO
#   B  tag escrita por SKILL fora do mapa VIVO                       → VERMELHO
#   C  o modo `--json` deixar de acompanhar o veredicto              → VERMELHO
#      (um `return` seco ali daria exit 0 — o modo que um CI usaria
#       seria justamente o que não pode reprovar)
#
# ⚠️ **O ramo A é o que dá valor aos outros, e nasceu de um erro meu.**
# O extrator usava uma JANELA de 6 linhas antes do literal. Ao escrever o
# comentário que documenta a remoção do `reviewer_id`, empurrei a tag irmã
# `round_echoed` para fora da janela naquele sítio — e o relatório **não mudou de
# número**, porque a mesma tag é escrita num segundo sítio e o resultado é
# deduplicado por nome. A redundância mascarou a perda. Hoje o extrator usa o
# ESCOPO DA CHAMADA (balanceia brackets, pula strings), e o `--autoteste` prova
# isso contra texto sintético — a única forma de a regressão ficar vermelha sem
# depender de como o repositório está escrito hoje.
#
# INCONCLUSIVO é ramo próprio: sem o config-api a metade TENANT não tem contra o
# que ser julgada, e cair na semente publicaria como não-declaradas tags que o
# portão aceita.
#
# Uso:  bash infra/test/probe_contextstore_cadastro.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CENSO="$RAIZ/infra/test/censo_contextstore_cadastro.py"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }

echo "${BLD}probe_contextstore_cadastro — nenhuma tag escrita fora do cadastro${RST}"
echo

# ── A — o instrumento antes da medição ───────────────────────────────────────
echo "${BLD}A) o extrator não perde tag por distância${RST}"
A_OUT="$(python3 "$CENSO" --autoteste 2>&1)"; A_RC=$?
if [ "$A_RC" -eq 0 ]; then
  ok "$A_OUT"
else
  bad "extrator regrediu — o resto desta medição não vale nada"
  printf '%s\n' "$A_OUT" | sed 's/^/    /'
fi

# ── B — o censo ──────────────────────────────────────────────────────────────
echo
echo "${BLD}B) censo: plataforma × SEMENTE · tenant × mapa VIVO${RST}"
B_OUT="$(python3 "$CENSO" 2>&1)"; B_RC=$?
printf '%s\n' "$B_OUT" | grep -E "^  escritas:|^     x |^VERDE|^REPROVADO|^INCONCLUSIVO|DINAMICO" \
  | sed 's/^/    /'
case "$B_RC" in
  0) ok "as duas metades limpas" ;;
  2) inc "metade TENANT não julgada (config-api fora?) — não é verde" ;;
  *) bad "tag escrita fora do cadastro" ;;
esac

# ── C — o modo --json acompanha ──────────────────────────────────────────────
echo
echo "${BLD}C) --json devolve o MESMO veredicto${RST}"
python3 "$CENSO" --json >/dev/null 2>&1; C_RC=$?
if [ "$C_RC" -eq "$B_RC" ]; then
  ok "--json e modo relatório concordam (rc=$C_RC)"
else
  bad "--json rc=$C_RC ≠ relatório rc=$B_RC — o modo de CI não acompanha o veredicto"
fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s), $INC inconclusivo(s)"; exit 1
fi
if [ "$INC" -gt 0 ]; then
  echo "${YLW}${BLD}INCONCLUSIVO${RST} — $INC ramo(s) sem amostra"; exit 2
fi
echo "${GRN}${BLD}VERDE${RST} — cadastro íntegro nas duas metades, e o instrumento provado"
