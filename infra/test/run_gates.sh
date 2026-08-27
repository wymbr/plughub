#!/usr/bin/env bash
# ==============================================================================
# run_gates.sh — executa o conjunto de gates declarado em `gates.manifest`
# ==============================================================================
#
# POR QUE ELE EXISTE (2026-08-27)
# -------------------------------
# Havia 24 gates neste repositorio e NENHUM runner os invocava. Cada arco
# adicionava o seu, via-o verde uma vez, e ele virava lembranca: gate que ninguem
# roda nao e cobertura, e o modo de falha e AUSENCIA — nada fica vermelho, o
# arquivo so para de dizer a verdade em silencio.
#
# O QUE ELE GARANTE, E O QUE NAO
# ------------------------------
# Garante que cada script do manifesto FOI EXECUTADO e que seu `rc` foi
# reportado sem tradução. Nao julga o interior de nenhum: quem decide verde e o
# proprio gate.
#
# TRES desfechos, nao dois — porque "nao consegui medir" nao e "passou":
#   0  VERDE          o gate mediu e aprovou
#   1  VERMELHO       o gate mediu e reprovou
#   2  INCONCLUSIVO   o gate nao conseguiu medir (falta credencial, servico fora,
#                     amostra vazia). CONTA COMO FALHA.
#   *  RC=n           qualquer outro codigo — reportado cru, nunca arredondado
#      MISSING        o manifesto cita um arquivo que nao existe
#      TIMEOUT        estourou o limite (default 300s, `GATE_TIMEOUT`)
#
# ASSISTIDOS (linhas com `!` no manifesto) NAO sao executados: precisam de estado
# vivo ou argumento que so um humano produz. Sao LISTADOS com o requisito.
# Rodar-os assim mesmo os deixaria INCONCLUSIVO para sempre, e um runner
# permanentemente vermelho por nao-defeito ensina a ignorar o vermelho — perdendo
# o unico valor que ele tem. Omiti-los faria a cobertura parecer maior do que e.
# Sao dois erros opostos; a lista evita os dois.
#
# Uso:
#   bash infra/test/run_gates.sh                  # tudo do manifesto
#   bash infra/test/run_gates.sh --only seguranca # so os que casam o padrao
#   bash infra/test/run_gates.sh --list           # lista sem executar
#   GATE_TIMEOUT=600 bash infra/test/run_gates.sh
#
# Os logs de cada execucao ficam num diretorio proprio, impresso no fim — um
# resumo que nao permite chegar ao motivo obriga a re-rodar tudo para depurar um.
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MANIFEST="${GATE_MANIFEST:-$HERE/gates.manifest}"
TIMEOUT="${GATE_TIMEOUT:-300}"
ONLY=""
LIST_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help) sed -n '2,36p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "argumento desconhecido: $1" >&2; exit 64 ;;
  esac
done

[ -f "$MANIFEST" ] || { echo "manifesto ausente: $MANIFEST" >&2; exit 2; }

# Diretorio de logs desta execucao. Sem data via `date` no nome de commit, mas
# aqui e efemero e ajuda a distinguir execucoes na mesma sessao.
RUNDIR="$(mktemp -d "${TMPDIR:-/tmp}/plughub-gates-XXXXXX")"

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
yell()  { printf '\033[33m%s\033[0m' "$1"; }

SCRIPTS=()      # linhas AUTO, com eventual prefixo de env
ASSISTED=()     # linhas `!`, nunca executadas — listadas com o requisito
while IFS= read -r line; do
  line="${line%%#*}"
  # apara so as bordas: o miolo pode ter env prefix e o requisito do assistido
  line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [ -z "$line" ] && continue
  case "$line" in
    '!'*)
      entry="${line#\!}"
      [ -n "$ONLY" ] && case "$entry" in *"$ONLY"*) : ;; *) continue ;; esac
      ASSISTED+=("$entry")
      continue ;;
  esac
  [ -n "$ONLY" ] && case "$line" in *"$ONLY"*) : ;; *) continue ;; esac
  SCRIPTS+=("$line")
done < "$MANIFEST"

if [ "${#SCRIPTS[@]}" -eq 0 ]; then
  echo "INCONCLUSIVO: o manifesto nao produziu nenhum script" >&2
  [ -n "$ONLY" ] && echo "  (o filtro --only '$ONLY' nao casou nada)" >&2
  exit 2
fi

if [ "$LIST_ONLY" -eq 1 ]; then
  [ "${#SCRIPTS[@]}" -gt 0 ] && printf '%s\n' "${SCRIPTS[@]}"
  [ "${#ASSISTED[@]}" -gt 0 ] && printf '!%s\n' "${ASSISTED[@]}"
  exit 0
fi

printf '\033[1mrun_gates\033[0m — %d gates AUTO (+%d assistidos, nao executados)' \
  "${#SCRIPTS[@]}" "${#ASSISTED[@]}"
[ -n "$ONLY" ] && printf " (filtro: %s)" "$ONLY"
printf '\n  timeout por gate: %ss   logs: %s\n\n' "$TIMEOUT" "$RUNDIR"

n_ok=0; n_red=0; n_inc=0; n_other=0; n_missing=0; n_timeout=0
declare -a ROWS=()

for entry in "${SCRIPTS[@]}"; do
  # `VAR=valor outra=coisa script.sh` — o ultimo campo e o script, o resto e env.
  s="${entry##* }"
  envs="${entry% *}"
  [ "$envs" = "$entry" ] && envs=""
  path="$HERE/$s"
  printf '  %-46s ' "$s"
  if [ ! -f "$path" ]; then
    red "MISSING"; printf '\n'
    n_missing=$((n_missing + 1)); ROWS+=("MISSING|$s|-|-")
    continue
  fi
  log="$RUNDIR/${s%.sh}.log"
  start=$SECONDS
  if [ -n "$envs" ]; then
    env $envs timeout --preserve-status -k 10 "$TIMEOUT" bash "$path" >"$log" 2>&1
  else
    timeout --preserve-status -k 10 "$TIMEOUT" bash "$path" >"$log" 2>&1
  fi
  rc=$?
  dur=$((SECONDS - start))
  case "$rc" in
    0)   green "VERDE";        n_ok=$((n_ok + 1));      ROWS+=("VERDE|$s|$rc|$dur") ;;
    1)   red   "VERMELHO";     n_red=$((n_red + 1));    ROWS+=("VERMELHO|$s|$rc|$dur") ;;
    2)   yell  "INCONCLUSIVO"; n_inc=$((n_inc + 1));    ROWS+=("INCONCLUSIVO|$s|$rc|$dur") ;;
    124|137) red "TIMEOUT";    n_timeout=$((n_timeout + 1)); ROWS+=("TIMEOUT|$s|$rc|$dur") ;;
    *)   red   "RC=$rc";       n_other=$((n_other + 1)); ROWS+=("RC=$rc|$s|$rc|$dur") ;;
  esac
  printf '  (%ss)\n' "$dur"
done

printf '\n\033[1mResumo\033[0m\n'
printf '  VERDE .......... %d\n' "$n_ok"
printf '  VERMELHO ....... %d\n' "$n_red"
printf '  INCONCLUSIVO ... %d   (nao mediu — conta como falha)\n' "$n_inc"
printf '  TIMEOUT ........ %d\n' "$n_timeout"
printf '  OUTRO RC ....... %d\n' "$n_other"
printf '  MISSING ........ %d   (manifesto cita arquivo inexistente)\n' "$n_missing"

falhas=$((n_red + n_inc + n_other + n_missing + n_timeout))
if [ "$falhas" -gt 0 ]; then
  printf '\n\033[1mNao-verdes\033[0m (log completo em %s):\n' "$RUNDIR"
  for r in "${ROWS[@]}"; do
    IFS='|' read -r st sc rc dur <<< "$r"
    [ "$st" = "VERDE" ] && continue
    printf '  %-14s %s\n' "$st" "$sc"
    lg="$RUNDIR/${sc%.sh}.log"
    [ -f "$lg" ] && tail -3 "$lg" | sed 's/^/                 | /'
  done
fi

if [ "${#ASSISTED[@]}" -gt 0 ]; then
  printf '\n\033[1mNAO EXECUTADOS\033[0m — %d assistidos (precisam de estado vivo ou argumento):\n' \
    "${#ASSISTED[@]}"
  for a in "${ASSISTED[@]}"; do
    nome="${a%%[[:space:]]*}"
    req="$(printf '%s' "$a" | sed "s|^$nome[[:space:]]*||")"
    printf '  %-46s %s\n' "$nome" "${req:-(requisito nao declarado no manifesto)}"
  done
  printf '  Estes NAO contam como falha — mas tambem NAO contam como cobertura.\n'
fi

printf '\n'
if [ "$falhas" -eq 0 ]; then
  printf '%s — %d/%d gates verdes\n' "$(green 'TUDO VERDE')" "$n_ok" "${#SCRIPTS[@]}"
  exit 0
fi
printf '%s — %d de %d nao-verdes\n' "$(red 'FALHOU')" "$falhas" "${#SCRIPTS[@]}"
exit 1
