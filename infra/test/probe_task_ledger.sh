#!/usr/bin/env bash
# ==============================================================================
# probe_task_ledger.sh — o ledger de tarefas nao pode mentir nem perder tarefa
# ==============================================================================
#
# Guarda os dois modos de falha do par `pending.md` / `done.md`:
#
#   (1) STATUS VELHO — o defeito que motivou o ledger. Medido em 2026-08-31:
#       NOVE marcadores desatualizados no `TODO.md`, e em todos o corpo estava
#       certo e o TITULO estava velho. Quem lista pendencias le titulo. Por isso
#       o ramo F proibe palavra de status em titulo de grupo: remove a
#       possibilidade em vez de exigir vigilancia.
#
#   (2) TAREFA PERDIDA — modo de falha NOVO, que o desenho de dois arquivos
#       cria e o `TODO.md` nao tinha. Mover e apagar-aqui + escrever-la, nao
#       atomico. Sem os ramos C e D, um id pode acabar nos dois arquivos ou em
#       nenhum, e o segundo caso e silencioso. Trocar status velho por tarefa
#       sumida seria piorar.
#
# Nao precisa de stack de pe: le arquivo e o git. Falseabilidade conferida
# apagando um id, duplicando um id e pondo "concluido" num titulo de grupo.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

PEND="pending.md"
DONE="done.md"
FAIL=0

for f in "$PEND" "$DONE"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: $f nao existe"; exit 2; }
done

echo "== probe_task_ledger =="

# ---------------------------------------------------------------- ramo A: ids
# id = 3 maiusculas + hifen + 2 digitos, na primeira celula da linha de tabela.
ids_of() { grep -oE '^\| [A-Z]{3}-[0-9]{2} \|' "$1" | tr -d '| ' ; }
ALL_IDS="$(cat <(ids_of "$PEND") <(ids_of "$DONE"))"
N_IDS="$(printf '%s\n' "$ALL_IDS" | grep -c . || true)"
DUP="$(printf '%s\n' "$ALL_IDS" | sort | uniq -d)"
if [ -n "$DUP" ]; then
  echo "A. VERMELHO — id repetido atraves dos dois arquivos:"; printf '   %s\n' $DUP; FAIL=1
else
  echo "A. verde — $N_IDS ids, todos unicos"
fi

# ------------------------------------------------- ramo B: grupo tem documento
BADDOC=""
while IFS= read -r g; do
  [ "$g" = "sem-demanda" ] && continue
  [ -f "$g" ] || BADDOC="$BADDOC $g"
done < <(grep -hoE '^## `[^`]+`' "$PEND" "$DONE" | sed 's/^## `//; s/`$//' | sort -u)
if [ -n "$BADDOC" ]; then
  echo "B. VERMELHO — grupo aponta documento inexistente:"; printf '   %s\n' $BADDOC; FAIL=1
else
  echo "B. verde — todo grupo titula documento existente (ou e o balde sem-demanda)"
fi

# --------------------------------------------- ramo C: id nao mora nos dois
BOTH="$(comm -12 <(ids_of "$PEND" | sort -u) <(ids_of "$DONE" | sort -u))"
if [ -n "$BOTH" ]; then
  echo "C. VERMELHO — id presente em pending E done (mudanca pela metade):"; printf '   %s\n' $BOTH; FAIL=1
else
  echo "C. verde — nenhum id nos dois arquivos"
fi

# ------------------------------------------- ramo D: nenhum id sumiu vs HEAD
if git rev-parse --verify -q "HEAD:$PEND" >/dev/null 2>&1; then
  OLD="$(cat <(git show "HEAD:$PEND" 2>/dev/null) <(git show "HEAD:$DONE" 2>/dev/null) \
        | grep -oE '^\| [A-Z]{3}-[0-9]{2} \|' | tr -d '| ' | sort -u)"
  LOST="$(comm -23 <(printf '%s\n' "$OLD") <(printf '%s\n' "$ALL_IDS" | sort -u))"
  if [ -n "$LOST" ]; then
    echo "D. VERMELHO — id existia no HEAD e sumiu dos DOIS arquivos:"; printf '   %s\n' $LOST; FAIL=1
  else
    echo "D. verde — nenhuma tarefa perdida na mudanca"
  fi
else
  echo "D. SEM BASELINE — $PEND ainda nao esta no HEAD; comparacao impossivel nesta execucao"
fi

# --------------------------------- ramo E: linha de done cita ancora de origem
NOANCHOR="$(grep -E '^\| [A-Z]{3}-[0-9]{2} \|' "$DONE" | grep -vcE 'CHANGELOG\.md|TODO\.md' || true)"
if [ "$NOANCHOR" -gt 0 ]; then
  echo "E. VERMELHO — $NOANCHOR linha(s) em $DONE sem ancora de CHANGELOG/TODO"; FAIL=1
else
  echo "E. verde — toda linha fechada cita a ancora onde o porque mora"
fi

# ------------------------------- ramo F: titulo de grupo nao afirma status
STATUS_RE='concluid|completo|entregue|fechad|pendente|em aberto|implementad|resolvido|nao diagnosticad'
BADT="$(grep -hE '^## ' "$PEND" "$DONE" | grep -icE "$STATUS_RE" || true)"
if [ "$BADT" -gt 0 ]; then
  echo "F. VERMELHO — $BADT titulo(s) de grupo afirmando status (o campo e a coluna, nunca o titulo):"
  grep -hE '^## ' "$PEND" "$DONE" | grep -iE "$STATUS_RE" | sed 's/^/   /'
  FAIL=1
else
  echo "F. verde — nenhum titulo de grupo afirma status"
fi

echo "======================"
[ "$FAIL" -eq 0 ] && { echo "VERDE"; exit 0; }
echo "VERMELHO"; exit 1
