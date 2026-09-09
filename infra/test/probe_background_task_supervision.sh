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
# ⚠️ **São DUAS populações, e elas não se substituem** (RET-15, 2026-09-09):
#   BOOT     vive enquanto o processo vive; morrer é incidente → exige SUPERVISÃO.
#   EFÊMERA  uma por mensagem, dentro de um handler; terminar é normal → exige DONO.
# Uma efêmera criada como statement não é referenciada por ninguém: a exceção fica
# presa na Task e o CPython pode coletá-la no meio da execução. Medido no
# channel-gateway: **10 disparos sem dono**, seis deles com instruções de topo fora
# de qualquer `try` — entre eles `WhatsAppAdapter._process_inbound`, ou seja,
# mensagem de cliente que some sem uma linha de log. Contar as duas juntas daria um
# número sem uso, que é o erro que a D14.1 registra.
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
#   C  task efêmera criada como statement, sem dono (use `disparar()`)
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
# ── C. a outra populacao: efemeras sem dono ─────────────────────────────────
#
# ⚠️ Contra o TETO declarado em `task_ownership.debt`, nunca contra zero: os 22
# disparos soltos fora do channel-gateway foram MEDIDOS e ficaram de fora do
# conserto por escopo (RET-16). Um gate cronicamente vermelho ensina a ser
# ignorado; um teto cobra a REGRESSAO e mantem a divida na tela.
printf '\n\033[1mC. nenhuma tarefa efemera nasce sem dono\033[0m\n'
DEBT=infra/test/task_ownership.debt
[ -f "$DEBT" ] || { echo "INCONCLUSIVO: $DEBT ausente"; exit 2; }
ERRO=$(mktemp)
SOLTAS=$(python3 "$CENSO" . --efemeras 2>"$ERRO") || {
  echo "INCONCLUSIVO: censo de efemeras falhou:"; cat "$ERRO"; rm -f "$ERRO"; exit 2; }
rm -f "$ERRO"

VEREDICTO=$(printf '%s\n' "$SOLTAS" | awk -F'\t' -v debt="$DEBT" '
  BEGIN {
    while ((getline linha < debt) > 0) {
      if (linha ~ /^[[:space:]]*#/ || linha ~ /^[[:space:]]*$/) continue
      split(linha, c, /[[:space:]]+/)
      svc = c[1]
      # `!servico` = ISENTO por decisao (sem gatilho); sem `!` = DIVIDA (com gatilho).
      # Junta-los faria a divida herdar a tranquilidade da decisao.
      if (substr(svc, 1, 1) == "!") { svc = substr(svc, 2); isento[svc] = 1 }
      teto[svc] = c[2]
    }
  }
  NF { achado[$1]++; onde[$1] = onde[$1] sprintf("                   %s:%s  %s()\n", $1, $2, $3) }
  END {
    ruim = 0
    for (s in achado) {
      t = (s in teto) ? teto[s] : 0
      if (achado[s] > t) {
        ruim = 1
        printf "FALHA\t%s\t%d\t%d\n%s", s, achado[s], t, onde[s]
      } else if (achado[s] < t) {
        printf "FOLGA\t%s\t%d\t%d\n", s, achado[s], t
      } else if (s in isento) {
        printf "ISENTO\t%s\t%d\t%d\n", s, achado[s], t
      } else {
        printf "DIVIDA\t%s\t%d\t%d\n", s, achado[s], t
      }
    }
    for (s in teto) if (!(s in achado) && teto[s] > 0) printf "FOLGA\t%s\t0\t%d\n", s, teto[s]
    exit ruim
  }')
RUIM=$?

printf '%s\n' "$VEREDICTO" | while IFS=$'\t' read -r tipo svc n t; do
  case "$tipo" in
    DIVIDA) printf '               %-20s %2d solto(s)  \033[33mdivida declarada\033[0m\n' "$svc" "$n" ;;
    ISENTO) printf '               %-20s %2d solto(s)  isento declarado (fossil, nao deployado)\n' "$svc" "$n" ;;
    FOLGA)  printf '               %-20s %2d solto(s)  teto e %s — \033[33mbaixe o teto\033[0m\n' "$svc" "$n" "$t" ;;
    FALHA)  printf '  \033[31mFALHA\033[0m        %-20s %2d solto(s), teto %s:\n' "$svc" "$n" "$t" ;;
    *)      [ -n "$tipo" ] && printf '%s\n' "$tipo" ;;
  esac
done

if [ "$RUIM" != 0 ]; then
  printf '                 -> a excecao fica presa na Task, e sem referencia forte o\n'
  printf '                    CPython pode coleta-la no meio da execucao. Use\n'
  printf '                    `disparar(coro, nome=...)`, em packages/py-tasks\n'
  printf '\n\033[31mVERMELHO\033[0m - ver secoes acima.\n'
  exit 1
fi
printf '  \033[32mOK\033[0m           nenhum disparo solto acima do teto declarado\n'

printf '\n\033[32mVERDE\033[0m - nenhuma task morre em silencio, de boot ou efemera.\n'
exit 0
