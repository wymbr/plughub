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
# SUÍTE INVISÍVEL (2026-08-03). Três pacotes (auth-api, session-replayer,
# usage-aggregator) mantêm testes em `packages/<pkg>/tests` que o Dockerfile não copia —
# a suíte existe, é mantida, e nunca roda. Este relatório deixou de apenas REPORTAR isso
# e passa a RODAR, montando o diretório num container efêmero da mesma imagem. A decisão
# (montar × copiar na imagem × rodar no host) está comentada no ponto do código.
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

  # DESCOBRIR o diretório, não SUPOR. A 1ª versão assumia `/app/packages/<nome>` — o
  # padrão do routing-engine — e marcou 6 pacotes como "sem pyproject" quando o
  # Dockerfile deles copia o pacote direto em `/app` (ex.: quality-ingest). Supor o
  # caminho transformava "não sei medir" em "não mediu", que é o mesmo defeito que este
  # relatório existe para caçar, cometido pelo próprio relatório.
  WD="$($DC exec -T "$pkg" sh -lc '
        for d in "/app/packages/'"$pkg"'" /app /app/src; do
          [ -f "$d/pyproject.toml" ] && { echo "$d"; exit 0; }
        done
        exit 1' 2>/dev/null | tr -d '\r')"
  if [ -z "$WD" ]; then
    printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - "pyproject não encontrado (TS? outro layout?)"
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
    # Zero testes coletados NA IMAGEM tem DUAS causas, e confundi-las custa caro:
    #   · o repositório não tem teste nenhum (dívida de cobertura), ou
    #   · o teste EXISTE e o Dockerfile não o copia (suíte invisível — o mesmo defeito
    #     do `ai-gateway`, com outro mecanismo).
    # Este script roda da raiz do repo, então pode conferir o disco e responder qual das
    # duas é. Sem essa checagem ele afirmaria "SEM SUÍTE" para pacotes bem cobertos —
    # exatamente o erro de ler ausência-de-visão como ausência-de-fato que ele persegue.
    # Medido 2026-08-02: auth-api, session-replayer e usage-aggregator TÊM testes no
    # repo e nenhum na imagem; dialog-api, scheduler-api e mailing-api não têm nenhum.
    if printf '%s' "$OUT" | grep -qE 'no tests ran|warning[s]? in'; then
      # `wc -l`, NÃO `grep -c . || echo 0`: com zero linhas o `grep -c` imprime "0" E
      # sai com 1, então o `||` acrescentava um SEGUNDO "0" e a variável virava "0\n0"
      # — `[ "0\n0" -gt 0 ]` erra com "integer expression expected". A classificação
      # ainda saía certa por acidente (o `[` falho cai no `else`), que é o pior tipo de
      # bug: ruidoso no terminal e correto no resultado, portanto fácil de ignorar.
      N_REPO=$(find "packages/$pkg" -name 'test_*.py' 2>/dev/null | wc -l | tr -d ' ')
      if [ "${N_REPO:-0}" -gt 0 ]; then
        # SUÍTE INVISÍVEL — o teste existe no repo e não na imagem. Em vez de só
        # reportar, RODA: monta `packages/<pkg>/tests` num container efêmero da mesma
        # imagem (2026-08-03).
        #
        # Por que montar e não copiar `tests/` no Dockerfile: teste não precisa viajar
        # na imagem de produção. E por que não rodar no host: as deps de dev teriam de
        # existir lá, e a suíte deixaria de rodar no MESMO ambiente que o serviço — que
        # é toda a razão de este relatório existir (o caso `REDIS_URL` ×
        # `PLUGHUB_REDIS_URL` só apareceu por rodar dentro do container).
        #
        # `--no-deps` (não sobe a stack), `--rm` (não deixa resíduo), mount `:ro` +
        # PYTHONDONTWRITEBYTECODE (não suja o repo com `__pycache__` de root — em WSL
        # isso vira arquivo que o usuário não consegue apagar).
        MOUNT_OUT="$($DC run --rm --no-deps \
              -e PYTHONDONTWRITEBYTECODE=1 \
              -v "$PWD/packages/$pkg/tests:$WD/tests:ro" \
              "$pkg" sh -lc \
              "pip install -q pytest pytest-asyncio httpx >/dev/null 2>&1;
               cd '$WD' && python -m pytest -p no:cacheprovider -q tests 2>&1 | tail -5" 2>&1)"

        MP=$(printf '%s' "$MOUNT_OUT" | grep -oE '[0-9]+ passed'  | grep -oE '[0-9]+' | tail -1)
        MS=$(printf '%s' "$MOUNT_OUT" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' | tail -1)
        MF=$(printf '%s' "$MOUNT_OUT" | grep -oE '[0-9]+ (failed|error)' | grep -oE '[0-9]+' | tail -1)

        if [ -z "$MP$MS$MF" ]; then
          printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - \
            "❌ INVISÍVEL e o mount TAMBÉM não mediu: ${MOUNT_OUT##*$'\n'}"
          INCONCL=$((INCONCL + 1)); continue
        fi

        [ "${MS:-0}" -gt 0 ] 2>/dev/null && TOTAL_SKIP=$((TOTAL_SKIP + MS))
        MNOTE="↺ rodou por MOUNT (não está na imagem — por desenho)"
        [ "${MF:-0}" -gt 0 ] 2>/dev/null && MNOTE="❌ falhas — $MNOTE"
        printf '%-24s %8s %8s %8s   %s\n' "$pkg" "${MP:-0}" "${MS:-0}" "${MF:-0}" "$MNOTE"
        continue
      else
        printf '%-24s %8s %8s %8s   %s\n' "$pkg" 0 0 0 \
          "⚠️  SEM SUÍTE — nenhum test_*.py no repositório"
      fi
    else
      printf '%-24s %8s %8s %8s   %s\n' "$pkg" - - - "sem linha-resumo: ${OUT##*$'\n'}"
    fi
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
