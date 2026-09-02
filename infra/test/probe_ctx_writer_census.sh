#!/usr/bin/env bash
# probe_ctx_writer_census.sh — CNS-06: o número que dimensiona a ALW-02 tem CRITÉRIO.
#
# ── Por que este gate existe ──────────────────────────────────────────────────
#
# Havia TRÊS números para "quantos escritores diretos do ContextStore existem" — 12 na
# §1.7 do ADR, 16 numa contagem estrutural, 18 numa textual — e nenhum critério escrito.
# A ALW-02 (o choke point de escrita) é a maior tarefa do arco ALLOWLIST, e estava sendo
# dimensionada por um número que ninguém sabia reproduzir.
#
# O critério vive no cabeçalho de `_ctx_writer_census.py`. Este gate impede que ele
# volte a ser opinião:
#
#   A. o instrumento roda e produz número
#   B. o número não regride em silêncio (piso declarado)
#   C. a divergência contra o ORÁCULO é a DECLARADA — uma nova reprova
#   D. testemunha negativa: escrita em OUTRO hash não entra na conta
#
# ── O ramo C é o coração, e a divergência é o achado ─────────────────────────
#
# O oráculo (`ESCRITORES` do censo de cadastro) e o instrumento foram construídos por
# caminhos independentes e para fins diferentes. Eles DIVERGEM de propósito em três
# arquivos, e cada um dos três é uma informação:
#
#   + journey.ts   o instrumento acha, o oráculo não: é o `writeContextTag`, o FUNIL
#                  que já existe. Contá-lo é o que revela que a ALW-02 não é "construir
#                  um choke point", é "estender o que já está lá".
#   − server.ts    o oráculo lista, o instrumento não: ambos IMPORTAM e chamam
#   − session.ts   `writeContextTag`. São chamadores do funil, não escritores diretos —
#                  e o critério diz que o helper conta UMA vez, no helper.
#
# Um quarto item em qualquer direção significa que alguém escreveu no ContextStore por
# um caminho novo, ou que o funil ganhou/perdeu um cliente. Nos dois casos, a ALW-02
# mudou de tamanho e alguém precisa saber.
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; FAIL=2; }

#: Piso, não alvo. Baixar exige explicar o que saiu; subir sem explicar é escritor novo
#: entrando sem passar pela decisão da ALW-02.
PISO_ARQUIVOS=8
PISO_SITIOS=22

echo "=== probe_ctx_writer_census — CNS-06 (dimensiona a ALW-02) ==="
echo

OUT="$(python3 infra/test/_ctx_writer_census.py 2>/dev/null)"
if [ -z "$OUT" ]; then
  huh "A: o instrumento nao produziu saida (python3? AST?)"
  echo; echo "INCONCLUSIVO"; exit 2
fi
ok "A: instrumento rodou"

# Le a linha RESUMO, que o instrumento emite SEM acento justamente para isto: casar
# acento no shell depende de locale, e bancada decidindo veredicto e o padrao que
# este arco passou o dia consertando.
RESUMO_L=$(printf "%s" "$OUT" | grep "^RESUMO " | head -1)
ARQ=$(echo "$RESUMO_L" | tr " " "
" | grep "^arquivos=" | cut -d= -f2)
SIT=$(echo "$RESUMO_L" | tr " " "
" | grep "^sitios=" | cut -d= -f2)
[ -z "$ARQ" ] && ARQ=0
[ -z "$SIT" ] && SIT=0
echo "     medido: $ARQ arquivos, $SIT sitios (piso $PISO_ARQUIVOS/$PISO_SITIOS)"

if [ "$ARQ" -eq "$PISO_ARQUIVOS" ] && [ "$SIT" -eq "$PISO_SITIOS" ]; then
  ok "B: numero estavel no piso declarado"
elif [ "$ARQ" -lt "$PISO_ARQUIVOS" ] || [ "$SIT" -lt "$PISO_SITIOS" ]; then
  bad "B: o numero ENCOLHEU ($ARQ/$SIT) — se um escritor virou funil, baixe o piso NESTE arquivo e diga qual"
else
  bad "B: o numero CRESCEU ($ARQ/$SIT) — escritor direto novo entrou sem passar pela ALW-02"
fi

# C — a divergencia contra o oraculo e a DECLARADA
INST_ONLY=$(printf '%s' "$OUT" | sed -n 's/^     + //p' | sort)
ORAC_ONLY=$(printf '%s' "$OUT" | sed -n 's/^     - //p' | sort)
ESPERADO_INST="packages/mcp-server-plughub/src/tools/journey.ts"
ESPERADO_ORAC="packages/mcp-server-plughub/src/server.ts
packages/mcp-server-plughub/src/tools/session.ts"
if [ "$INST_ONLY" = "$ESPERADO_INST" ]; then
  ok "C: so-no-instrumento e o FUNIL esperado (writeContextTag)"
else
  bad "C: so-no-instrumento mudou — esperado o funil, veio: $(echo $INST_ONLY)"
fi
if [ "$ORAC_ONLY" = "$ESPERADO_ORAC" ]; then
  ok "C: so-no-oraculo sao os dois CHAMADORES do funil"
else
  bad "C: so-no-oraculo mudou — esperados server.ts e session.ts, veio: $(echo $ORAC_ONLY)"
fi

# D — testemunha NEGATIVA: hash que nao e ContextStore fica de fora.
# Sem ela, um casador largo demais passaria em A/B/C contando `menu:waiting:` como
# ContextStore — e ja passou: a primeira versao do instrumento trazia tres falsos
# positivos do skill-flow-engine porque casava o `ctx.` do RECEPTOR (`ctx.redis.hset`).
if printf '%s' "$OUT" | grep -q "skill-flow-engine/src/steps/"; then
  bad "D: escrita em OUTRO hash entrou na conta (menu:waiting: nao e ContextStore)"
else
  ok "D: escrita em outro hash NAO entra — o marcador e a chave, nao o receptor"
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "OK — o numero da ALW-02 tem criterio, piso e divergencia explicada"
elif [ "$FAIL" = "2" ]; then
  echo "INCONCLUSIVO"
else
  echo "FALHA"
fi
exit "$FAIL"
