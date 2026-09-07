#!/usr/bin/env bash
# probe_tree_continuation.sh — 2026-09-07  (RET-05 do ADR
# adr-tree-return-continuation.md)
#
# PERGUNTA: o ciclo esta LIGADO, e ligado onde ele pode girar?
#
# O QUE ESTA ENTREGA MUDA, e e a primeira do arco que o cliente VE
#   Ate a RET-04 tudo era mecanismo inerte: 0 de 14 formas declaravam
#   `on_return`, entao o `continuar` do orquestrador caia sempre em `finalizar`.
#   A RET-05 liga: a forma de navegacao ganha a question `pos_atendimento` e as
#   folhas que podem continuar apontam para ela.
#
# TRES RAMOS
#   A  PONTEIROS — `on_return` esta nas folhas cujo destino DEVOLVE, e NAO esta
#      nas que nao devolvem. ⚠️ Este e o ramo que carrega peso: um ponteiro numa
#      folha nao-delegavel (portabilidade, que tem cadeia de delegate; humano,
#      que nao tem deploy) PENDURA o contato ate o `timeout_hours`, com o cliente
#      vendo o especialista atender e nada ficando vermelho. Zero ponteiros e
#      INCONCLUSIVO: o ciclo estaria desligado e o resto nao diria nada.
#   B  CAMINHOS — toda opcao de continuacao ou e POOL (casa no `navigation_pools`
#      por prefixo) ou e COMANDO (literal comparado contra `category_path` no
#      fluxo). Orfao nao existe: o `pool_route_resolve` nao tem default POR
#      DECISAO, entao o cliente escolheria e o roteador recusaria.
#   C  MENUS — o especialista PULA o proprio menu de continuidade quando ha
#      chamador. Sem isto o cliente e perguntado DUAS vezes (o especialista
#      pergunta, devolve, e o orquestrador abre a continuacao) — a mesma tela que
#      o dono leu como defeito no `menu_motivo`, um turno adiante.
#
# ⚠️ POR QUE O MENU DO ESPECIALISTA NAO FOI APAGADO: `sac_ia` tem endpoint de
#    canal proprio (`webchat/sac`, 198 segmentos medidos), entao ha cliente que
#    chega la sem orquestrador nenhum. Apagar consertaria o caminho novo e
#    quebraria o antigo — a mesma decisao que preservou o `menu_motivo`.
#
# ⚠️ COMANDO x DEMANDA, e a divida que isso abre: a arvore roteia demanda para
#    POOL, e "encerrar"/"outro assunto" nao sao pool nenhum. Hoje sao DOIS
#    caminhos declarados no fluxo. Um terceiro obriga o genero a subir para a
#    arvore (um `kind` de folha), senao o fluxo vira catalogo de caminhos — a
#    segunda casa do conteudo que a D2 existe para impedir.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_ret05_continuation_probe.py"
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

echo "probe_tree_continuation — o ciclo esta ligado, e onde pode girar (RET-05)"

rodar "A — so continua quem tem para onde voltar"  ponteiros
rodar "B — pool declarado ou comando do fluxo"     caminhos
rodar "C — o especialista nao pergunta duas vezes" menus
rodar "D · COMPOSICAO — as duas casas compoem o mesmo `category_path`" composicao

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
echo "RESULTADO: OK — o ciclo gira, e so onde ha para onde voltar"
exit 0
