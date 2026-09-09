#!/usr/bin/env bash
# ==============================================================================
# probe_background_task_supervision.sh — toda task de BOOT tem a morte visível
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Toda task criada no boot de um serviço Python (`lifespan`/`startup`) — os
# consumidores e scanners que devem viver enquanto o processo viver — passa por um
# supervisor que loga quando ela morre ou termina sozinha.
#
# POR QUE ELE EXISTE (RET-13, 2026-09-09)
# ---------------------------------------
# Uma task sob `asyncio.create_task` que ninguém aguarda guarda a exceção DENTRO do
# objeto Task, e ela some. O serviço segue de pé, `/health` verde, com um consumidor
# a menos — e o sintoma aparece longe: no caso que criou o `_supervise` do
# channel-gateway (2026-08-07), *"revogar token não vale"*, três camadas abaixo.
#
# ⚠️ **A ficha apontava UMA task e a medição achou ONZE.** A RET-13 dizia que o
# scanner de prazo era *"a única task de background do channel-gateway sem
# `_supervise`"* — verdade, e irrelevante: o censo mediu **16 tasks de boot em 4
# serviços**, e o `_supervise` só existia num deles. Dez tasks em analytics-api,
# evaluation-api e workflow-api não tinham alarme nenhum. É a lição da GAT-01 outra
# vez: *"rodou tudo o que a lista cita"* e *"a lista cita tudo"* são dois fatos.
#
# ⚠️ **A população é a task de BOOT, e isso é escolha declarada.** Task efêmera de
# trabalho (uma por mensagem, criada dentro de um handler) é outro fenômeno, com
# outro conserto — está medida e registrada como RET-15. Contar as duas juntas daria
# um número sem uso.
#
# ⚠️ **Ele aceita DUAS formas de supervisão**, porque o que não pode divergir é o
# comportamento, não a implementação: envelope (`_supervise(nome, create_task(...))`)
# ou callback (`t = create_task(...)` + `t.add_done_callback(...)`). Exigir um nome
# de função faria o gate cobrar estilo e ficar cego para quem supervisiona de outro
# jeito.
#
# O QUE O DEIXARIA VERMELHO
#   A  task de boot criada sem envelope e sem `add_done_callback`
#   B  censo vazio (nenhuma task achada em serviço nenhum) → INCONCLUSIVO
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
CENSO="infra/test/_background_task_census.py"
[ -f "$CENSO" ] || { echo "INCONCLUSIVO: $CENSO ausente"; exit 2; }
command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

printf '\033[1mprobe: toda task de boot tem a morte visivel\033[0m\n\n'

SAIDA=$(python3 "$CENSO" . 2>&1) || { echo "INCONCLUSIVO: censo falhou:"; echo "$SAIDA"; exit 2; }
TOTAL=$(printf '%s\n' "$SAIDA" | grep -c .)
if [ "$TOTAL" = 0 ]; then
  printf '  \033[33mINCONCLUSIVO\033[0m nenhuma task de boot achada — o censo quebrou?\n'
  exit 2
fi

CRUAS=$(printf '%s\n' "$SAIDA" | grep -c 'CRUA$')
SUP=$((TOTAL - CRUAS))

printf '\033[1mA. censo por servico\033[0m\n'
printf '%s\n' "$SAIDA" | awk -F'\t' '
  { n[$1]++; if ($4 == "CRUA") c[$1]++ }
  END { for (s in n) printf "               %-18s %2d task(s)%s\n", s, n[s],
          (c[s] ? sprintf("  \033[31m%d CRUA(S)\033[0m", c[s]) : "  todas supervisionadas") }' | sort

printf '\n\033[1mB. nenhuma task de boot nasce sem alarme\033[0m\n'
if [ "$CRUAS" != 0 ]; then
  printf '  \033[31mFALHA\033[0m        %d task(s) de boot sem supervisao:\n' "$CRUAS"
  printf '%s\n' "$SAIDA" | grep 'CRUA$' | awk -F'\t' '{printf "                 %s:%s  %s()\n", $1, $2, $3}'
  printf '                 -> a excecao fica presa no objeto Task e SOME; o servico\n'
  printf '                    segue de pe, /health verde, com um consumidor a menos.\n'
  printf '\n\033[31mVERMELHO\033[0m - ver secoes acima.\n'
  exit 1
fi

printf '  \033[32mOK\033[0m           %d de %d task(s) de boot supervisionadas\n' "$SUP" "$TOTAL"
printf '\n\033[32mVERDE\033[0m - nenhuma task de boot morre em silencio.\n'
exit 0
