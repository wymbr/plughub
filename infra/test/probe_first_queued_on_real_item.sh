#!/usr/bin/env bash
# PROBE (não é gate) — `first_queued` é escrito no caminho REAL de enfileiramento?
#
# POR QUE ESTE PROBE EXISTE
#
# O ADR `adr-work-item-requeue-and-agent-affinity.md` (D2) afirma que
# `first_queued_ms` *"NÃO é escrito hoje no caminho de re-enqueue"* e trata isso
# como trabalho da Fase B. A leitura de código diz o contrário: não é campo do
# JSON do contato — é a chave `{t}:queue:first_queued:{sid}`, escrita com **NX**
# por `add_queued_contact` (registry.py) e lida de lá pelo inbox
# (work-queue.ts → PullInboxPanel).
#
# A tentativa de decidir isso no espécime de 2026-08-04 não serviu: o contato foi
# finalizado na tela, então o ledger e o item já não existiam (`TTL -2`). Ausência
# num item morto não é evidência sobre caminho de escrita.
#
# E o pytest também não decide: o fixture enfileira chamando `add_queued_contact`
# direto — exercita a FUNÇÃO que foi lida, não a ROTA que o produto percorre. Se a
# rota real não passar por lá, o teste fica verde e a premissa segue errada.
#
# Este probe olha os itens que estão DE FATO na fila, sem semear nada.
#
# VEREDICTO DE 3 ESTADOS (ramifica sobre o medido; nada é interpolado em frase):
#   · todos os membros do ZSET têm a chave  → H2: a escrita acontece; a nota do
#     ADR sobre `first_queued` é imprecisa e a sub-tarefa da Fase B não existe
#   · nenhum tem                            → H1: o ADR está certo; a Fase B ganha
#     trabalho real (carimbar no enfileiramento do item de trabalho)
#   · uns sim, outros não                   → MISTO: a diferença está na rota de
#     cada item, e é ela que precisa ser olhada — não a chave
#   · fila VAZIA                            → INCONCLUSIVO. Não é "não escreve".
#     Crie um item real antes (ex.: `bash infra/test/smoke_formfill_renderer.sh`)
#
# Uso (da raiz do repo, demo no ar):
#   bash infra/test/probe_first_queued_on_real_item.sh [pool_id]
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
POOL="${1:-formfill_demo}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "Pool sob observação: $POOL (tenant $TENANT)"
echo

MEMBERS=$(R ZRANGE "${TENANT}:pool:${POOL}:queue" 0 -1)
if [ -z "$MEMBERS" ]; then
  echo "  ⚠️  INCONCLUSIVO — a fila de '$POOL' está VAZIA."
  echo
  echo "     Fila vazia NÃO é evidência de que a chave não é escrita: é ausência"
  echo "     de amostra. Crie um item real e rode de novo:"
  echo "       bash infra/test/smoke_formfill_renderer.sh"
  echo "     (ou aponte para outro pool: $0 <pool_id>)"
  exit 2
fi

WITH=0; WITHOUT=0
while IFS= read -r SID; do
  [ -n "$SID" ] || continue
  FQ=$(R GET "${TENANT}:queue:first_queued:${SID}")
  TTL=$(R TTL "${TENANT}:queue:first_queued:${SID}")
  QAT=$(R --no-raw GET "${TENANT}:queue_contact:${SID}")
  if [ -n "$FQ" ]; then
    WITH=$((WITH+1))
    # Legível como data: confirma que é epoch ms do enqueue, e não outro número
    # que por acaso está lá. Valor sem sentido temporal é tão suspeito quanto
    # chave ausente.
    HUMAN=$(date -u -d "@$((FQ/1000))" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "?")
    echo "  TEM     $SID  first_queued=$FQ ($HUMAN) ttl=${TTL}s"
  else
    WITHOUT=$((WITHOUT+1))
    echo "  NÃO TEM $SID  (ttl=$TTL)"
    # O JSON ajuda a distinguir rota: item de delegate carrega work_item_deadline.
    echo "          queue_contact: ${QAT:0:180}"
  fi
done <<< "$MEMBERS"

TOTAL=$((WITH + WITHOUT))
echo
echo "======================================"
echo "  itens na fila=$TOTAL  com first_queued=$WITH  sem=$WITHOUT"
if   [ "$WITHOUT" = 0 ]; then
  echo "  → H2: a escrita ACONTECE no caminho real."
  echo "        A nota do ADR sobre first_queued é imprecisa; a sub-tarefa da"
  echo "        Fase B não existe. Emendar o ADR (D2) com este número."
elif [ "$WITH" = 0 ]; then
  echo "  → H1: a chave NÃO é escrita no caminho real."
  echo "        O ADR está certo e minha leitura de código estava errada:"
  echo "        a Fase B precisa carimbá-la no enfileiramento do item."
else
  echo "  → MISTO: a diferença está na ROTA de cada item, não na chave."
  echo "        Comparar os queue_contact acima (work_item_deadline presente ="
  echo "        item de delegate) antes de concluir qualquer coisa."
fi
