#!/usr/bin/env bash
# ==============================================================================
# probe_i18n_duplicate_keys.sh — chave de traducao perdida em silencio
# ==============================================================================
#
# Guarda um defeito MUDO: chave repetida no mesmo objeto de um arquivo de locale.
# O JSON continua valido, o build passa, e a ultima ocorrencia apaga a anterior —
# levando junto as chaves que so ela tinha. Foi o que deixou a Home mostrando
# `catalog.volume-by-channel.label` como titulo de cartao.
#
# O raciocinio de desenho esta no cabecalho do `_i18n_dupes.py`, em uma frase: um
# probe de PARIDADE EN x pt-BR nao pega isto, porque os dois arquivos estavam
# duplicados do mesmo jeito e a paridade estava perfeita.
#
# Nao precisa de stack de pe: le arquivo. Falseabilidade conferida injetando uma
# chave repetida num locale e vendo o probe ficar vermelho.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOCALES="${I18N_LOCALES:-$ROOT/packages/platform-ui/src/i18n/locales}"

printf '\033[1mprobe: chave duplicada em arquivo de locale (perda silenciosa)\033[0m\n'

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  printf '  \033[33mINCONCLUSIVO\033[0m sem python no PATH\n'; exit 2
fi
if [ ! -d "$LOCALES" ]; then
  printf '  \033[33mINCONCLUSIVO\033[0m diretorio de locales nao encontrado: %s\n' "$LOCALES"; exit 2
fi

OUT="$("$PY" "$HERE/_i18n_dupes.py" "$LOCALES" 2>&1)"
RC=$?
printf '%s\n' "$OUT" | sed 's/^/  /'

case "$RC" in
  0) printf '\n\033[32mVERDE\033[0m — nenhuma chave de traducao se apaga em silencio.\n' ;;
  1) printf '\n\033[31mVERMELHO\033[0m — ha chave duplicada: o que a ultima ocorrencia nao\n'
     printf 'repete deixa de existir, e a tela mostra a CHAVE no lugar do texto.\n' ;;
  *) printf '\n\033[33mINCONCLUSIVO\033[0m\n' ;;
esac
exit "$RC"
