#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_python_suites — as suites Python RODAM, e rodam a partir da IMAGEM
#
# Por que este gate existe, e por que ele não é higiene:
#
#   O `TODO.md` carregava desde 2026-08-27 o item *"quatro testes vermelhos que
#   ninguém estava vendo"*, com o agravante *"nada roda estas suítes"*. Medido em
#   2026-08-30, o agravante era MAIOR que o item: **nenhuma das 14 imagens Python
#   tinha pytest**. Quatro containers tinham — instalado à mão, em algum momento,
#   por alguém — e um `docker compose up -d` apaga isso. Ou seja, as quatro suites
#   que "rodavam" rodavam por ESTADO HERDADO, que é a invariante do `CLAUDE.md`:
#   *um ambiente que só sobe porque já subiu antes não está sendo verificado —
#   está sendo lembrado.*
#
#   O que o gate julga, em três proposições SEPARADAS (e a separação é o ponto):
#
#     A. a DECLARAÇÃO   — os 14 Dockerfiles instalam `.[dev]`
#     B. a IMAGEM       — pytest importa em container NOVO, feito da imagem
#     C. a EXECUÇÃO     — cada suite roda e o vermelho é só o declarado abaixo
#
#   (A) sem (B) é promessa sem mecanismo — a família do DDL de
#   `participation_intervals`. (B) sem (A) fica verde por container herdado, que é
#   justamente o defeito que originou o gate.
#
# ⚠️ ARMADILHA MEDIDA, e ela custou 476 falsos vermelhos: **nunca `cd /app`**.
#   Rodar da raiz do monorepo troca o rootdir do pytest, e com ele o
#   `[tool.pytest.ini_options]` de cada pacote (`asyncio_mode = "auto"`) deixa de
#   ser lido — TODO teste assíncrono falha. Na primeira execução isto deu
#   "476 failed" contra os 15 reais. Um runner escrito da forma óbvia nasceria
#   permanentemente vermelho, e todo mundo aprenderia a ignorá-lo. Usa-se o
#   WORKDIR do próprio container.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO (pré-condição falhou).
# Uso: bash infra/test/probe_python_suites.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${PREFIX:-plughub-demo}"

SERVICOS="evaluation-api auth-api analytics-api config-api channel-gateway \
orchestrator-bridge routing-engine ai-gateway workflow-api scheduler-api \
dialog-api calendar-api pricing-api quality-ingest"

# ── Vermelho DECLARADO ────────────────────────────────────────────────────────
#
# Não é lista de exceção que envelhece calada: cada linha nomeia o teste E a
# dívida de onde ele vem, e o gate IMPRIME as três a cada execução. Falha NOVA,
# ou falha aqui que passe a PASSAR, muda a contagem e o gate acusa.
#
#   routing-engine · test_expire_returns_the_slot_even_after_the_lease_expired
#     `claimed_via` volta 'record' onde o teste espera 'semaphore'. É exatamente a
#     "lacuna 2" que a validação da Camada F declarou aberta (*a lease não foi
#     medida*), e `claimed_via` é resíduo aberto da D14.1.
#
#   ai-gateway · test_reason_emite_com_source_reason
#   ai-gateway · test_sentiment_emite_mesmo_com_resposta_ilegivel
#     `sources()` volta VAZIO — nenhum evento de uso emitido. Toca a atribuição de
#     token (T0–T3), cujo contrato é `source` obrigatório nos 4 caminhos vivos.
#
BASELINE_TOTAL=3
declare -A BASELINE=(
  [routing-engine]=1
  [ai-gateway]=2
)

FAIL=0; INC=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=$((FAIL+1)); }
huh() { echo "  ? $1"; INC=$((INC+1)); }

echo "=== probe_python_suites — as suites rodam a partir da IMAGEM ==="

# ── A. DECLARAÇÃO ─────────────────────────────────────────────────────────────
echo
echo "-- A. DECLARACAO (os Dockerfiles instalam o extra .[dev]) --"
SEM_DEV=""
for s in $SERVICOS; do
  df="$ROOT/packages/$s/Dockerfile"
  [ -f "$df" ] || { huh "A: $s sem Dockerfile"; continue; }
  grep -q '\.\[dev\]' "$df" || SEM_DEV="$SEM_DEV $s"
done
if [ -z "$SEM_DEV" ]; then
  ok "A: os 14 Dockerfiles instalam .[dev] — o pytest vem da imagem, nao da mao"
else
  bad "A: sem .[dev] em:$SEM_DEV — a suite so rodaria por estado herdado"
fi

# ── B. IMAGEM ─────────────────────────────────────────────────────────────────
echo
echo "-- B. IMAGEM (container NOVO, sem estado herdado) --"
SEM_PYTEST=""
for s in $SERVICOS; do
  v=$(docker run --rm --entrypoint sh "$PREFIX-$s" -lc \
        'python -c "import pytest" 2>/dev/null && echo sim' 2>/dev/null)
  [ "$v" = "sim" ] || SEM_PYTEST="$SEM_PYTEST $s"
done
if [ -n "$SEM_PYTEST" ] && [ "$SEM_PYTEST" = " $SERVICOS" ]; then
  huh "B: nenhuma imagem respondeu — docker indisponivel?"
elif [ -z "$SEM_PYTEST" ]; then
  ok "B: pytest importa nas 14 IMAGENS (nao no container que alguem tocou)"
else
  bad "B: imagem sem pytest:$SEM_PYTEST"
fi

# ── C. EXECUÇÃO ───────────────────────────────────────────────────────────────
echo
echo "-- C. EXECUCAO (WORKDIR do pacote; NUNCA /app — ver cabecalho) --"
TOT_PASS=0; TOT_FAIL=0; VERMELHOS=""
for s in $SERVICOS; do
  c="$PREFIX-$s-1"
  out=$(docker exec "$c" sh -lc 'python -m pytest -q 2>&1 | tail -3' 2>/dev/null)
  line=$(printf '%s' "$out" | grep -E 'passed|failed|error|no tests' | tail -1)
  if [ -z "$line" ]; then
    huh "C: $s nao produziu linha de resultado (container fora do ar?)"
    continue
  fi
  # ⚠️ O ancoramento '(^|[^0-9])' e' load-bearing: a 1a versao exigia um nao-digito
  # ANTES do numero, e por isso nao casava linha que COMECA com ele ('237 passed, ...').
  # Doze suites verdes viraram 'ZERO testes' — e quem pegou foi a testemunha de
  # PRESENCA abaixo, nao a contagem: sem ela o gate teria somado so 472 e passado.
  p=$(printf '%s' "$line" | sed -nE 's/.*(^|[^0-9])([0-9]+) passed.*/\2/p'); p=${p:-0}
  f=$(printf '%s' "$line" | sed -nE 's/.*(^|[^0-9])([0-9]+) failed.*/\2/p'); f=${f:-0}
  # Testemunha de PRESENCA: suite que nao coleta nada nao pode passar por verde.
  if [ "$p" -eq 0 ] && [ "$f" -eq 0 ]; then
    huh "C: $s coletou ZERO testes — verde por ausencia de amostra nao e verde"
    continue
  fi
  TOT_PASS=$((TOT_PASS+p)); TOT_FAIL=$((TOT_FAIL+f))
  esperado=${BASELINE[$s]:-0}
  if [ "$f" -ne "$esperado" ]; then
    VERMELHOS="$VERMELHOS $s($f!=$esperado)"
  fi
  printf '     %-20s %s\n' "$s" "$line"
done

echo
if [ -n "$VERMELHOS" ]; then
  bad "C: contagem de falhas fora do declarado:$VERMELHOS"
elif [ "$TOT_FAIL" -eq "$BASELINE_TOTAL" ]; then
  ok "C: $TOT_PASS passando; $TOT_FAIL falhando, e as $TOT_FAIL sao as DECLARADAS"
else
  bad "C: total de falhas $TOT_FAIL != baseline $BASELINE_TOTAL"
fi

echo
echo "-- vermelho DECLARADO (imprime sempre; divida, nao exceção calada) --"
echo "     routing-engine  test_expire_returns_the_slot_even_after_the_lease_expired"
echo "                     claimed_via='record' x 'semaphore' — lacuna 2 da Camada F"
echo "     ai-gateway      test_reason_emite_com_source_reason"
echo "     ai-gateway      test_sentiment_emite_mesmo_com_resposta_ilegivel"
echo "                     sources() VAZIO — atribuicao de token (T0-T3)"

echo
if [ "$FAIL" -gt 0 ]; then
  echo "FALHA ($FAIL)"; exit 1
elif [ "$INC" -gt 0 ]; then
  echo "INCONCLUSIVO ($INC)"; exit 2
else
  echo "OK — as 14 suites rodam a partir da imagem; vermelho so o declarado"; exit 0
fi
