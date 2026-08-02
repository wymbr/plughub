#!/usr/bin/env bash
# report_suite_skips.sh — quantos testes cada suíte PULA dentro do próprio container.
#
# POR QUE ISTO EXISTE. `pytest.skip` sai VERDE. Uma suíte que pula inteira é
# indistinguível, no resumo, de uma suíte que passa — e ninguém lê a contagem de
# `skipped`. Em 2026-08-02 descobriu-se que `test_instance_semaphore.py` (24 testes,
# cobrindo `claim_instance`) e `test_human_instance_identity.py` (11) NUNCA haviam
# rodado no container: liam só `REDIS_URL` e caíam no default `localhost:6379`. Era a
# segunda ocorrência da mesma causa — a primeira (9 testes do claim pull, 2026-07-30)
# foi corrigida arquivo a arquivo e não alcançou os vizinhos.
#
# `test_redis_url_resolution_guard.py` fecha aquela causa ESPECÍFICA, e só no
# routing-engine. Este relatório é o de cima: mede o SINTOMA (skip em massa) em todos
# os pacotes, seja qual for o motivo — Redis, ClickHouse, Postgres, marker esquecido.
#
# LEITURA. Skip não é defeito por si: há teste legitimamente condicional. O que este
# relatório entrega é a CONTAGEM, para que uma suíte com 35 pulos deixe de passar por
# "verde" na conversa. A pergunta a fazer de cada linha com skip > 0 é a mesma:
# *o que faria estes testes rodarem, e por que isso não está acontecendo aqui?*
#
# Uso:  bash infra/test/report_suite_skips.sh [pacote ...]
# Pré:  stack demo no ar. Dura: ~1 min por pacote com suíte.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"

# Serviço do compose == diretório em packages/ (padrão do repo). Onde divergir, a
# sonda abaixo reporta INCONCLUSIVO em vez de pular calado.
PKGS=(
  routing-engine orchestrator-bridge analytics-api channel-gateway evaluation-api
  ai-gateway session-replayer workflow-api calendar-api scheduler-api config-api
  pricing-api auth-api dialog-api mailing-api quality-ingest quality-export
  rules-engine usage-aggregator clickhouse-consumer conversation-writer
)
[ "$#" -gt 0 ] && PKGS=("$@")

TOTAL_SKIP=0
INCONCL=0
ROWS=""

printf '%-24s %8s %8s %8s   %s\n' PACOTE PASSOU PULOU FALHOU NOTA
printf '%s\n' "────────────────────────────────────────────────────────────────────────"

for pkg in "${PKGS[@]}"; do
  # Sonda por EXEC: o que importa é conseguir rodar código lá dentro.
  if ! $DC exec -T "$pkg" sh -lc 'true' >/dev/null 2>&1; then
    printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - "serviço não responde a exec"
    INCONCL=$((INCONCL + 1)); continue
  fi
  WD="/app/packages/$pkg"
  if ! $DC exec -T "$pkg" sh -lc "[ -f '$WD/pyproject.toml' ]" >/dev/null 2>&1; then
    printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - "sem pyproject em $WD (TS? caminho outro?)"
    INCONCL=$((INCONCL + 1)); continue
  fi

  OUT="$($DC exec -T "$pkg" sh -lc \
        "pip install -q pytest pytest-asyncio >/dev/null 2>&1;
         cd '$WD' && python -m pytest -p no:cacheprovider -q 2>&1 | tail -5" 2>&1)"

  # A linha-resumo do pytest é a fonte; ausência dela é INCONCLUSIVO, nunca zero.
  P=$(printf '%s' "$OUT" | grep -oE '[0-9]+ passed'  | grep -oE '[0-9]+' | tail -1)
  S=$(printf '%s' "$OUT" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' | tail -1)
  F=$(printf '%s' "$OUT" | grep -oE '[0-9]+ (failed|error)' | grep -oE '[0-9]+' | tail -1)
  if [ -z "$P$S$F" ]; then
    printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - "sem linha-resumo: ${OUT##*$'\n'}"
    INCONCL=$((INCONCL + 1)); continue
  fi

  NOTE=""
  [ "${S:-0}" -gt 0 ] 2>/dev/null && { NOTE="⚠️  ver motivos com -rs"; TOTAL_SKIP=$((TOTAL_SKIP + S)); }
  [ "${F:-0}" -gt 0 ] 2>/dev/null && NOTE="❌ falhas — $NOTE"
  printf '%-24s %8s %8s %8s   %s\n' "$pkg" "${P:-0}" "${S:-0}" "${F:-0}" "$NOTE"
done

echo
echo "── veredicto ───────────────────────────────────────────────────────────────"
if [ "$TOTAL_SKIP" -gt 0 ]; then
  echo "   ⚠️  $TOTAL_SKIP teste(s) PULADO(S) no total."
  echo "   Para cada pacote marcado, os motivos:"
  echo "     $DC exec -T <pacote> sh -lc 'cd /app/packages/<pacote> && python -m pytest -q -rs'"
  echo "   Se o motivo citar um serviço INDISPONÍVEL que está no ar, é a causa de"
  echo "   2026-07-30/08-02: o teste lê uma variável que o serviço não define"
  echo "   (`REDIS_URL` × `PLUGHUB_REDIS_URL`). Correção = dual-read + guarda no pacote."
else
  echo "   nenhum skip nos pacotes medidos."
fi
[ "$INCONCL" -gt 0 ] && {
  echo "   ⚠️  $INCONCL pacote(s) INCONCLUSIVO(s) — não mediram, e isso não é 'zero skip'."
  exit 2
}
exit 0
