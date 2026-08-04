#!/usr/bin/env bash
# probe_queue_member_resurrect.sh — 2026-08-04  (achado 1c)
#
# PERGUNTA: um membro removido do ZSET da fila VOLTA sozinho?
#
# Em 2026-08-04, durante a montagem de outro experimento, `509d5441…` foi removido
# de `tenant_demo:pool:formfill_demo:queue` (ZREM → 1) e o probe seguinte mostrou o
# semáforo vazio. Minutos depois o item foi reivindicado COM SUCESSO — e o
# `work_task_claim` (router.py §739) exige o `atomic_claim_dequeue` vencedor, ou seja,
# o membro ESTAVA no ZSET no momento do claim. Algo o repôs.
#
# Hipótese a testar (NÃO assumir): o item vive em DUAS chaves —
#   {t}:pool:{p}:queue          → membro do sorted set (o que o ZREM tira)
#   {t}:queue_contact:{sid}     → JSON do contato (que o ZREM NÃO tira)
# e algum caminho re-enfileira a partir do JSON sobrevivente.
#
# Este probe NÃO conserta nada. Ele observa, sem clique nenhum, e RESTAURA o que
# removeu (toda montagem sintética deixa rastro; desfazer é parte da rodada).
#
# USO:
#   bash infra/test/probe_queue_member_resurrect.sh <pool_id> <session_id>
#   bash infra/test/probe_queue_member_resurrect.sh formfill_demo 54911855-....
#
# Sem argumentos, lista os candidatos e sai.

set -u

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POOL="${1:-}"
SID="${2:-}"
WATCH_S="${WATCH_S:-90}"

r() { docker compose -f "$COMPOSE" exec -T redis redis-cli "$@" < /dev/null; }

echo "== probe 1c: membro de fila ressuscita? (tenant=$TENANT) =="

PING="$(r PING)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "  compose: $COMPOSE (rodar da RAIZ do repo)"
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê."
  exit 2
fi
echo "preflight: PING=PONG"
echo

if [ -z "$POOL" ] || [ -z "$SID" ]; then
  echo "Candidatos (filas não vazias):"
  for K in $(r --scan --pattern "${TENANT}:pool:*:queue"); do
    P="$(echo "$K" | sed "s|^${TENANT}:pool:||; s|:queue$||")"
    MEM="$(r ZRANGE "$K" 0 -1)"
    [ -z "$MEM" ] && continue
    echo "  pool=${P}"
    while IFS= read -r M; do
      [ -z "$M" ] && continue
      echo "    ${M}"
    done <<< "$MEM"
  done
  echo
  echo "USO: bash $0 <pool_id> <session_id>"
  exit 0
fi

QKEY="${TENANT}:pool:${POOL}:queue"
CKEY="${TENANT}:queue_contact:${SID}"

# ── Estado ANTES (e é o que permite restaurar) ───────────────────────────────
SCORE0="$(r ZSCORE "$QKEY" "$SID")"
CJSON0="$(r EXISTS "$CKEY")"

echo "ANTES:"
echo "  ZSCORE ${QKEY} ${SID:0:8} = ${SCORE0:-<AUSENTE>}"
echo "  EXISTS ${CKEY:0:40}…      = ${CJSON0}   (1 = JSON do contato presente)"
echo

if [ -z "$SCORE0" ]; then
  echo "VEREDICTO: INCONCLUSIVO — o item não está no ZSET agora; nada a remover."
  echo "           (escolher um item listado por este script sem argumentos)"
  exit 2
fi

echo ">>> removendo o membro (ZREM). O JSON do contato NÃO é tocado."
REMOVED="$(r ZREM "$QKEY" "$SID")"
echo "    ZREM = ${REMOVED}  (1 = removido)"
if [ "$REMOVED" != "1" ]; then
  echo "VEREDICTO: INCONCLUSIVO — ZREM não removeu; estado mudou sob os pés."
  exit 2
fi
echo

# ── Observação ───────────────────────────────────────────────────────────────
# PREVISÃO a escrever ANTES de olhar: se a hipótese do JSON sobrevivente estiver
# certa, o membro reaparece em algum momento da janela. Se nada o repuser, ele
# fica ausente pelos WATCH_S inteiros.
echo "observando por ${WATCH_S}s (amostra a cada 5s)…"
BACK_AT=""
ELAPSED=0
while [ "$ELAPSED" -lt "$WATCH_S" ]; do
  sleep 5
  ELAPSED=$((ELAPSED + 5))
  S="$(r ZSCORE "$QKEY" "$SID")"
  if [ -n "$S" ]; then
    BACK_AT="$ELAPSED"
    echo "  t+${ELAPSED}s: DE VOLTA (score=${S})"
    break
  fi
  echo "  t+${ELAPSED}s: ausente"
done
echo

# ── Veredicto: três ramos ────────────────────────────────────────────────────
echo "== veredicto =="
if [ -n "$BACK_AT" ]; then
  S1="$(r ZSCORE "$QKEY" "$SID")"
  echo "RESSUSCITOU em t+${BACK_AT}s."
  echo "  score ANTES  = ${SCORE0}"
  echo "  score DEPOIS = ${S1}"
  if [ "$S1" = "$SCORE0" ]; then
    echo "  Score IDÊNTICO ⇒ quem repôs reusou o queued_at_ms original (re-enfileiramento"
    echo "  que PRESERVA a espera — comportamento de requeue, não de contato novo)."
  else
    echo "  Score DIFERENTE ⇒ quem repôs carimbou tempo novo (a espera foi RESETADA)."
  fi
  echo
  echo "PRÓXIMO PASSO (não teorizar): achar QUEM repôs, nos logs da janela —"
  echo "  docker compose -f $COMPOSE logs --since ${WATCH_S}s routing-engine | grep -i -e queue -e requeue -e ${SID:0:8}"
  echo "  docker compose -f $COMPOSE logs --since ${WATCH_S}s orchestrator-bridge channel-gateway | grep -i ${SID:0:8}"
  echo
  echo "NADA A RESTAURAR: o item voltou sozinho."
else
  echo "NÃO ressuscitou em ${WATCH_S}s."
  echo "  Isso NÃO absolve o achado 1c — só diz que, em repouso, nada repõe o membro."
  echo "  O caso original tinha um agente logado com a inbox pollando e claims em curso;"
  echo "  se o gatilho for uma dessas ações, ele não aparece num sistema parado."
  echo
  echo ">>> RESTAURANDO o membro com o score original (${SCORE0}) — o ZREM foi meu."
  r ZADD "$QKEY" "$SCORE0" "$SID" > /dev/null
  S2="$(r ZSCORE "$QKEY" "$SID")"
  if [ "$S2" = "$SCORE0" ]; then
    echo "    restaurado: ZSCORE = ${S2} ✓"
  else
    echo "    ATENÇÃO: restauração NÃO conferiu (ZSCORE=${S2:-<AUSENTE>}, esperado ${SCORE0})."
    echo "    Item pode ter ficado fora da fila — conferir à mão antes de seguir."
  fi
fi
