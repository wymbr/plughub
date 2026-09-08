#!/usr/bin/env bash
# ==============================================================================
# probe_config_permissions_census.sh — MOD-01 / fase G0 do ADR de granularidade
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Que existe MECANISMO conferindo a populacao de um campo de capacidade contra a
# declaracao dele. O ADR pede isto ANTES de qualquer mudanca (D7), e a razao nao e
# zelo: o defeito que a costura 1 expoe nao e um grant errado, e **nao haver nada
# que confira uma coisa contra a outra**.
#
# Medido em 2026-09-08, e e por isso que a ficha existe: em oito dias a populacao de
# `config.permissions` mudou nos DOIS sentidos sem nada acusar — um portador sumiu
# (o `pending.md` ainda o citava como detentor) e tres apareceram. Deriva so se
# corrige onde ha probe.
#
# ⚠️ ELE NAO E UM GATE DE POLITICA, E ISSO E DECIDIDO. Deter um campo que o preset
# nao declara e legitimo (a tela concede depois do nascimento); nao deter um que ele
# declara tambem (a tela revoga). Julgar isso hoje seria inventar um alvo que a
# MOD-04 ainda nao decidiu. O que ele julga e se o censo CONSEGUE MEDIR — e quando a
# MOD-04 fixar o alvo, a classe B vira assercao no helper.
#
# O QUE O DEIXA VERMELHO
# ----------------------
#   R1  o campo censado nao existe em declaracao nenhuma -> o censo mede o vazio, e
#       contar zero por medir a coisa errada e pior que nao contar
#   R2  `infra/modules.yaml` e o catalogo DEPLOYADO (`GET /auth/modules`) discordam
#       sobre quem declara o campo -> "existe != esta aplicado", que e o que
#       acontece ao editar o YAML sem reiniciar o auth-api
#
# FALSEABILIDADE (conferida em 2026-09-08): `CAMPO=config.nao_existe` derruba o R1.
#
# ⚠️ NAO usa `jq` de proposito — dois probes desta familia saem INCONCLUSIVO nesta
# maquina por causa dele. A comparacao e por-usuario e mora em Python.
#
# ⚠️ Sem credencial ou sem auth-api: INCONCLUSIVO (2), nunca 0. "Nenhum portador" e
# resposta legitima do censo; "nao consegui perguntar" nao e — e a diferenca entre as
# duas e exatamente o que este arquivo existe para preservar.
#
# Env:
#   CAMPO    default `config.permissions`. Serve qualquer `modulo.campo` do catalogo.
#   AUTH     default http://localhost:3200
#   TENANT   default tenant_demo
#
# SAIDA: 0 = VERDE (mediu) · 1 = VERMELHO · 2 = INCONCLUSIVO (nao mediu)
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

# ⚠️ `AUTH` e do `_auth.sh` e ja vem com o sufixo `/auth` (default
# `http://localhost:3202/auth`). O helper Python monta as rotas a partir da BASE, entao
# reaproveitar a variavel crua produziria `/auth/auth/modules` — 404 lido como "servico
# fora do ar". Derivar em vez de declarar uma segunda variavel evita duas casas para a
# mesma URL.
BASE="${AUTH%/auth}"
TENANT="${TENANT:-tenant_demo}"
YAML="${YAML:-$RAIZ/infra/modules.yaml}"

echo "== probe_config_permissions_census =="

TOK="$(plughub_token || true)"
if [ -z "${TOK:-}" ]; then
  echo "INCONCLUSIVO — sem credencial; o censo nao pode perguntar nada"
  exit 2
fi

PYBIN="$(command -v python3 || true)"
if [ -z "$PYBIN" ]; then
  echo "INCONCLUSIVO — python3 ausente; a comparacao por-usuario mora nele"
  exit 2
fi

PLUGHUB_TOKEN="$TOK" "$PYBIN" "$HERE/_config_permissions_census.py" "$BASE" "$TENANT" "$YAML"
RC=$?

case "$RC" in
  0) echo "======================"; echo "VERDE" ;;
  1) echo "======================"; echo "VERMELHO" ;;
  *) echo "======================"; echo "INCONCLUSIVO" ;;
esac
exit "$RC"
