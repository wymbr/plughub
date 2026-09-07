#!/usr/bin/env bash
# probe_orchestrator_delegate_verb.sh — 2026-09-07  (RET-02 do ADR
# adr-tree-return-continuation.md)
#
# PERGUNTA: o orquestrador escolhe DELEGAR ou ESCALAR por destino, e escolhe pelo
# criterio derivado do que esta promovido la?
#
# POR QUE NAO UM `delegate` UNICO
#   O alvo do orquestrador e uma REFERENCIA (`$.pipeline_state.rota.pool`), entao
#   um unico step `delegate` alcancaria TODOS os destinos do mapa. Medido: 2 dos
#   6 nao sao delegaveis -- `portabilidade_ia` tem cadeia de delegate (o token e
#   tag unica da sessao e colide, CTR-06) e `retencao_humano` nao tem deploy.
#   Delegar para eles penduraria o contato ate o `timeout_hours`, com o cliente
#   vendo o especialista atender e NADA ficando vermelho.
#
# O VERBO E DERIVADO, NUNCA DECLARADO
#   Vem do `pool_route_resolve`, que le o snapshot PROMOVIDO no destino. Um campo
#   `delegavel: true` no pool seria segunda fonte de verdade que envelhece calada:
#   bastaria publicar um skill sem o retorno para a declaracao virar mentira.
#
# TRES RAMOS, e o terceiro e o que nao dava para pular
#   A  FLUXO — o snapshot promovido dos orquestradores tem o ramo `decidir_verbo`,
#      UM `delegate`, enderecado por REFERENCIA (nunca literal), e o
#      `nivel_continuacao` que fecha o laco.
#   B  CRITERIO ao vivo — aplica a regra sobre o deploy real de cada destino e
#      exige que ao menos um resolva para `delegate`; zero delegaveis e
#      INCONCLUSIVO, porque o ramo nunca seria exercido e o verde nao diria nada.
#   C  PARIDADE cross-language — o criterio esta escrito DUAS vezes (TypeScript em
#      `navigation.ts`, Python aqui), e paridade presumida entre linguagens e o
#      defeito que o gate do channel-gateway existe para pegar. Os dois leem os
#      MESMOS vetores (`fixtures/verb_vectors.json`); este ramo roda o lado
#      Python, e a suite do mcp-server roda o lado TypeScript sobre o mesmo
#      arquivo. Vetor novo obriga as duas casas a concordarem.
#
# ⚠️ ESTA ENTREGA NAO LIGA CICLO NENHUM. O retorno so continua se a folha
#    declarar `on_return`, e nenhuma forma publicada declara (medido: 0 de 14).
#    Sem ponteiro, o `continuar` cai em `finalizar` — o comportamento de hoje.
#    Quem for ligar precisa do TETO da RET-04 antes, senao o laco nao tem fim.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_ret02_verb_probe.py"
FALHA=0
INCONCL=0

rodar() {
  rotulo="$1"
  modo="$2"
  echo ""
  echo "── $rotulo ─────────────────────────────────────────────────────────"
  rc=0
  python3 "$HELPER" "$modo" || rc=$?
  case "$rc" in
    0) ;;
    3) INCONCL=$((INCONCL + 1)) ;;
    *) FALHA=$((FALHA + 1)) ;;
  esac
}

echo "probe_orchestrator_delegate_verb — delegar ou escalar, por destino (RET-02)"

rodar "A — o fluxo promovido tem o ramo do verbo"  fluxo
rodar "B — o criterio sobre o deploy real"          paridade
rodar "C — paridade cross-language (vetores)"       vetores

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA ramo(s) reprovaram"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo "RESULTADO: INCONCLUSIVO — $INCONCL ramo(s) sem populacao"
  exit 3
fi
echo "RESULTADO: OK — o verbo e derivado por destino, e as duas casas concordam"
exit 0
