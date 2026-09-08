#!/usr/bin/env bash
# ==============================================================================
# probe_rank_grant_guard.sh — MOD-02 / E2: ninguem concede o que nao detem
# ==============================================================================
#
# Mede o guard de RANK na BORDA (HTTP), nao no predicado. Os dois sao fatos
# distintos, e so o segundo protege alguem: durante a propria entrega da MOD-02, com
# `grants.violacoes` ja verde em 21 testes de unidade, a primeira medicao ao vivo
# ainda mostrou um usuario `admin` sendo criado — a fiacao das rotas era o que
# faltava conferir.
#
# Monta um DELEGADO de verdade (`config.users` + o pacote do preset de operator + UM
# pool) e exercita quatro cenarios; detalhe de cada um no cabecalho do
# `_rank_grant_probe.py`. O P1 e POSITIVO de proposito: sem ele, um guard que negasse
# tudo passaria neste arquivo — e ele e a Costura 1 do ADR, que era 403 antes desta
# entrega.
#
# Tudo o que cria, remove — inclusive em falha.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

BASE="${AUTH%/auth}"
TENANT="${TENANT:-tenant_demo}"

echo "== probe_rank_grant_guard =="

TOK="$(plughub_token || true)"
[ -z "${TOK:-}" ] && { echo "INCONCLUSIVO — sem credencial de master"; exit 2; }
PYBIN="$(command -v python3 || true)"
[ -z "$PYBIN" ] && { echo "INCONCLUSIVO — python3 ausente"; exit 2; }

PLUGHUB_TOKEN="$TOK" "$PYBIN" "$HERE/_rank_grant_probe.py" "$BASE" "$TENANT"
RC=$?
echo "======================"
case "$RC" in
  0) echo "VERDE" ;;
  1) echo "VERMELHO" ;;
  *) echo "INCONCLUSIVO" ;;
esac
exit "$RC"
