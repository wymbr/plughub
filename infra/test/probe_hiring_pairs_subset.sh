#!/usr/bin/env bash
# ==============================================================================
# probe_hiring_pairs_subset.sh — MOD-08 / fase G1b do ADR de granularidade
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Que a contratacao declarada em `hiring_pairs:` (em `infra/modules.yaml`) CONTINUA
# possivel sob o guard de RANK da MOD-02 — `rank(concedido) <= rank(detido)`, campo a
# campo. Formalmente: `preset(contratado)` contido em `preset(contratante)`.
#
# POR QUE ELE E PRE-REQUISITO, E NAO ACOMPANHAMENTO
# -------------------------------------------------
# Medido em 2026-09-08: o guard de rank aplicado aos presets de entao produzia A
# DIAGONAL — cada papel so conseguia criar um clone de si mesmo. O `supervisor` nao
# criava um `operator`, bloqueado em `approvals.decide`, `approvals.operacao` e
# `evaluation.contestar`, que o preset do operador traz e o dele nao tinha. Ou seja: o
# guard sozinho quebra a contratacao mais ordinaria do sistema, e o sintoma nao se
# parece com "preset errado" — parece "a tela nao deixa".
#
# Os tres campos foram acrescentados ao preset do supervisor (e ao seed, que
# `_seed_vs_preset.py` confere). Este gate existe para que a PROXIMA edicao de preset
# nao desfaca isso em silencio.
#
# ⚠️ ELE NAO E UMA HIERARQUIA. A cadeia `admin > developer > supervisor > operator` foi
# proposta e REFUTADA pelo catalogo no mesmo dia: nenhum par adjacente estava ordenado,
# e `operator` tem campos que `supervisor` nao tem de proposito (o operador CONTESTA a
# propria avaliacao; o supervisor REVISA). Papeis sao funcoes, nao niveis — os pares
# sao um grafo DECLARADO, e este gate so confere a propriedade de cada aresta.
#
# O QUE O DEIXA VERMELHO
# ----------------------
#   A  uma aresta declarada perde a contencao (o contratante nao alcanca algum campo
#      com que o contratado nasce), ou o papel de um dos lados nao tem preset nenhum
#   B  algum `role_defaults` usa `access` fora do dominio do rank — numa comparacao
#      ingenua viraria rank 0 e o subconjunto passaria por acidente
#
# `hiring_pairs:` ausente => INCONCLUSIVO, nunca verde: um gate sem aresta diria
# "toda contratacao possivel" tendo conferido zero.
#
# FALSEABILIDADE (conferida em 2026-09-08): remover `approvals.decide` do preset do
# supervisor derruba o ramo A nomeando o campo.
#
# ⚠️ ELE NAO OLHA GRANTS VIVOS, de proposito. Compara declaracao com declaracao. Um
# contratante real pode ter recebido MENOS que o preset (a tela revoga depois do
# nascimento) e falhar no runtime com este gate verde — esse eixo e do CENSO
# (`probe_config_permissions_census.sh`, classe C). Juntar os dois faria um gate
# reprovar por uso normal da tela, que e como se ensina a ignorar um vermelho.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$HERE/../.." && pwd)"
YAML="${YAML:-$RAIZ/infra/modules.yaml}"

echo "== probe_hiring_pairs_subset =="

PYBIN="$(command -v python3 || true)"
if [ -z "$PYBIN" ]; then
  echo "INCONCLUSIVO — python3 ausente; a comparacao mora nele"
  exit 2
fi

# Estatico de proposito: le o ARQUIVO, nao o catalogo deployado. A propriedade e da
# DECLARACAO, e exigir a stack de pe para conferi-la faria o gate depender de um
# ambiente que a pergunta nao usa. Quem confere arquivo x deployado e o censo.
"$PYBIN" "$HERE/_hiring_pairs.py" "$YAML"
RC=$?

echo "======================"
case "$RC" in
  0) echo "VERDE" ;;
  1) echo "VERMELHO" ;;
  *) echo "INCONCLUSIVO" ;;
esac
exit "$RC"
