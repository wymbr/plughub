#!/usr/bin/env bash
# probe_i18n_contacts_parity.sh — gate de i18n do namespace `contacts` (F3, gate 2).
#
# A invariante do CLAUDE.md diz "toda string visível nos DOIS locales, sempre por t()".
# O modo de falha dela é silencioso: a chave existe só em `pt-BR`, o i18next cai no
# fallback e a tela aparece em português no meio do inglês (ou o próprio nome da chave).
# Ninguém vê isso em review de diff — só quem troca o idioma.
#
# O que o faria REPROVAR:
#   · qualquer chave-folha presente num locale e ausente no outro → exit 1, nomeada
#   · qualquer chave nova da F3 ausente em um dos dois           → exit 1
#   · leitor quebrado (jq devolvendo pouca coisa)                → exit 2, INCONCLUSIVO
#
# A testemunha das chaves da F3 existe porque igualdade sozinha não julga: dois
# arquivos que o `jq` não conseguisse ler seriam IGUAIS entre si, e o probe passaria
# verde justamente quando não mediu nada.
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo não encontrada"; exit 2; }
L=packages/platform-ui/src/i18n/locales
EN=$L/en/contacts.json
PT=$L/pt-BR/contacts.json

for f in "$EN" "$PT"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: $f ausente"; exit 2; }
done
command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

leaves() { jq -r 'paths(scalars) | join(".")' "$1" | sort; }

n_en=$(leaves "$EN" | wc -l)
n_pt=$(leaves "$PT" | wc -l)
echo "chaves-folha: en=$n_en · pt-BR=$n_pt"

# Piso do leitor. `contacts` tem centenas de chaves; um punhado significa que o `jq`
# leu outra coisa, não que o arquivo encolheu.
if [ "$n_en" -lt 100 ] || [ "$n_pt" -lt 100 ]; then
  echo "INCONCLUSIVO: o leitor devolveu poucas chaves — o probe não mediu nada"
  exit 2
fi

miss=0
for k in \
  lista.columns.direction lista.columns.contact lista.columns.pools \
  lista.columns.outcome   lista.columns.process \
  lista.direction.inbound lista.direction.outbound lista.direction.internal \
  lista.processFootnote   lista.processChipHint \
  filter.entryPool        filter.attendedBy
do
  for f in "$EN" "$PT"; do
    jq -e --arg k "$k" 'getpath($k | split(".")) | strings' "$f" >/dev/null 2>&1 \
      || { echo "AUSENTE: $k em $f"; miss=1; }
  done
done

# As chaves que a F3 REMOVEU (ANI/DNIS). Voltarem é regressão: as duas colunas eram
# permanentemente vazias nos dois canais existentes, e a decisão foi "não voltam".
for k in lista.columns.origin lista.columns.destination filter.ani filter.dnis; do
  for f in "$EN" "$PT"; do
    if jq -e --arg k "$k" 'getpath($k | split(".")) | strings' "$f" >/dev/null 2>&1; then
      echo "RESSUSCITADA: $k em $f (a F3 a removeu — ver telas-design §5)"
      miss=1
    fi
  done
done

diff_out=$(comm -3 <(leaves "$EN") <(leaves "$PT"))
if [ -n "$diff_out" ]; then
  echo "DIVERGÊNCIA entre locales (coluna 1 = só en · coluna 2 = só pt-BR):"
  echo "$diff_out"
  exit 1
fi

[ "$miss" = 0 ] || exit 1
echo "OK — árvores idênticas ($n_en chaves) e as chaves da F3 presentes nos dois locales"
