#!/usr/bin/env bash
# triage_failing_suites.sh — UMA LINHA por teste vermelho, em todos os pacotes de uma vez.
#
# POR QUE ISTO EXISTE, e por que não é o `report_suite_skips.sh`. Aquele responde
# "quantos?" — é um censo, e foi escrito para que uma suíte com 35 pulos deixasse de
# passar por verde na conversa. Este responde "quais, e por quê", que é a pergunta
# seguinte e tem um custo diferente: rodar seis suítes uma a uma, lendo tracebacks
# inteiros, é meia hora de terminal para uma informação que cabe em trinta linhas.
#
# O FORMATO É A DECISÃO. `--tb=line` + `-rf` dá exatamente um `assert 401 == 204` por
# falha. Isso basta para CLASSIFICAR (teste desatualizado × defeito de código × ambiente
# ausente), que é o trabalho caro; o traceback completo só importa depois, para o punhado
# que a classificação não resolver. Traceback inteiro de 48 falhas é volume que esconde
# padrão — e o padrão é o achado: em 2026-08-02 as mesmas quatro causas se repetiram em
# quatro pacotes, e só ficaram visíveis lado a lado.
#
# NÃO CONFUNDIR ZERO COM AUSÊNCIA. Um pacote que não roda sai como INCONCLUSIVO e o
# script termina com código ≠ 0. "Nenhuma falha listada" tem duas leituras opostas —
# suíte verde, ou suíte que não coletou — e a primeira é a que o olho assume.
#
# Uso:  bash infra/test/triage_failing_suites.sh [pacote ...]
# Pré:  stack demo no ar.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"

# Default = os pacotes medidos VERMELHOS em 2026-08-02 (ver TODO.md § Suítes vermelhas).
PKGS=(config-api pricing-api evaluation-api channel-gateway analytics-api)
[ "$#" -gt 0 ] && PKGS=("$@")

INCONCL=0

for pkg in "${PKGS[@]}"; do
  echo
  echo "══════════════════════════════════════════════════════════════════════════"
  echo "  $pkg"
  echo "══════════════════════════════════════════════════════════════════════════"

  if ! $DC exec -T "$pkg" sh -lc 'true' >/dev/null 2>&1; then
    echo "  INCONCLUSIVO — serviço não responde a exec"
    INCONCL=$((INCONCL + 1)); continue
  fi

  # Mesma descoberta de WORKDIR do report_suite_skips.sh: DESCOBRIR, não supor.
  WD="$($DC exec -T "$pkg" sh -lc '
        for d in "/app/packages/'"$pkg"'" /app /app/src; do
          [ -f "$d/pyproject.toml" ] && { echo "$d"; exit 0; }
        done
        exit 1' 2>/dev/null | tr -d '\r')"
  if [ -z "$WD" ]; then
    echo "  INCONCLUSIVO — pyproject não encontrado"
    INCONCL=$((INCONCL + 1)); continue
  fi

  OUT="$($DC exec -T "$pkg" sh -lc \
        "pip install -q pytest pytest-asyncio httpx >/dev/null 2>&1;
         cd '$WD' && python -m pytest -p no:cacheprovider -q --tb=line -rf 2>&1" 2>&1)"

  # A linha-resumo é a prova de que a suíte RODOU. Sem ela, não há veredicto.
  if ! printf '%s' "$OUT" | grep -qE '[0-9]+ (passed|failed|error)'; then
    echo "  INCONCLUSIVO — sem linha-resumo do pytest. Últimas linhas:"
    printf '%s\n' "$OUT" | tail -15 | sed 's/^/    /'
    INCONCL=$((INCONCL + 1)); continue
  fi

  # Bloco de falhas (uma linha cada) + resumo.
  #
  # Cortar em `FAILURES`, NÃO em `short test summary info` (erro cometido na 1ª versão,
  # 2026-08-03): o `--tb=line` imprime o motivo — `…/test_x.py:41: AssertionError` — no
  # bloco FAILURES, que vem ANTES do resumo. Cortando depois dele sobravam só os NOMES
  # dos testes, e nome de teste é a única coisa que a linha `FAILED` já dava sem
  # `--tb` nenhum. O relatório continuava parecendo completo: 22 linhas, formatação
  # certa, veredicto no fim — e sem a informação pela qual ele existe.
  printf '%s\n' "$OUT" | sed -n '/=== FAILURES ===/,$p' | sed 's/^/  /'
  echo "  ───"
  printf '%s\n' "$OUT" | grep -E '^[0-9]+ (passed|failed)|[0-9]+ failed' | tail -1 | sed 's/^/  /'
done

echo
echo "── veredicto ───────────────────────────────────────────────────────────────"
[ "$INCONCL" -gt 0 ] && {
  echo "   ⚠️  $INCONCL pacote(s) INCONCLUSIVO(s) — não mediram. Isso NÃO é 'sem falhas'."
  exit 2
}
echo "   todos os pacotes mediram."
exit 0
