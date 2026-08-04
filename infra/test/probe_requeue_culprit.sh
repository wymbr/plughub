#!/usr/bin/env bash
# probe_requeue_culprit.sh — QUEM devolveu à fila um item em trabalho ativo, e quando.
#
# CONTEXTO. O `probe_reclaim_duplication.sh` provou o ESTADO (item no ZSET + vaga ocupada +
# lease ausente). Falta o AGENTE da ação. Isto importa porque duas causas com o mesmo estado
# pedem correções opostas:
#
#   · se quem re-enfileirou foi o fechamento do WS (F5 ⇒ auto-release), a devolução é
#     INTENCIONAL e o defeito é ela ser PARCIAL — devolveu o item à fila e não devolveu a vaga,
#     deixando o Console com um formulário vivo. Espelho exato da fix 2a, que devolvia a vaga e
#     não tirava o item de lugar nenhum: os dois fatos (FILA × VAGA) são mexidos por caminhos
#     diferentes e divergem.
#   · se foi o rollback de capacidade, ou um timeout scanner, a leitura é outra e o conserto
#     também.
#
# O que este probe NÃO faz: escolher entre elas por plausibilidade. Ele traz a linha do log ou
# declara que não achou. "Provavelmente foi o F5" é a frase que fecharia o caso errado.
#
# Uso:  bash infra/test/probe_requeue_culprit.sh [session_id] [pool] [janela]
#       janela = argumento --since do docker logs (default 3h)

set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
SID="${1:-dbdb1e94-1b86-4e2c-ab84-cd9498e1fa73}"
POOL="${2:-formfill_demo}"
SINCE="${3:-3h}"
SHORT="${SID%%-*}"

rcli() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
ts()   { [ -n "${1:-}" ] && date -d "@$(( ${1} / 1000 ))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "?"; }

echo "══ quem re-enfileirou $SHORT… (pool=$POOL) ══"
echo

# ── 1. Linha do tempo pelo estado ───────────────────────────────────────────────
SCORE=$(rcli ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID")
LEDGER=$(rcli GET "${TENANT}:work_task:${SID}")
QJSON=$(rcli GET "${TENANT}:queue_contact:${SID}")

echo "── linha do tempo (do estado, não do log) ─────────────────────────────────"
echo "   score do ZSET (= último enqueue) .... ${SCORE:-∅}   → $(ts "${SCORE:-}")"
if [ -n "$LEDGER" ]; then
  echo "   ledger created_at .................. $(jq -r '.created_at // "∅"' <<<"$LEDGER" 2>/dev/null)"
  echo "   ledger deadline .................... $(jq -r '.deadline   // "∅"' <<<"$LEDGER" 2>/dev/null)"
fi
if [ -n "$QJSON" ]; then
  echo "   JSON queued_at_ms .................. $(jq -r '.queued_at_ms // "∅"' <<<"$QJSON" 2>/dev/null)"
  echo "   JSON first_queued_ms ............... $(jq -r '.first_queued_ms // "∅ (AUSENTE)"' <<<"$QJSON" 2>/dev/null)"
  echo
  echo "   Campos do JSON (a ausência de um campo diz por qual caminho ele foi escrito):"
  jq -r 'keys | join(", ")' <<<"$QJSON" 2>/dev/null | sed 's/^/     /'
fi
echo
echo "   ► Se o score do ZSET for POSTERIOR ao created_at do ledger, houve re-enqueue."
echo "     Compare com a hora em que você deu o F5."
echo

# ── 2. O log, por serviço ───────────────────────────────────────────────────────
# Cada serviço é consultado separadamente: juntar tudo num grep só esconde QUEM falou,
# que é a pergunta inteira deste probe.
for SVC in mcp-server-plughub routing-engine orchestrator-bridge; do
  echo "── $SVC ───────────────────────────────────────────────────────────────────"
  OUT=$($COMPOSE logs --since "$SINCE" --timestamps "$SVC" 2>/dev/null \
        | grep -iE "$SHORT|work_task|claim|release|requeue|re-enfileir|enqueue|logout|leave" \
        | grep -iE "$SHORT" \
        | tail -40)
  if [ -z "$OUT" ]; then
    echo "   (nenhuma linha citando $SHORT… em $SINCE)"
  else
    sed 's/^/   /' <<<"$OUT"
  fi
  echo
done

# ── 3. O que aconteceu no pool, mesmo sem citar a sessão ────────────────────────
# O caminho de desconexão costuma logar por POOL/instância, não por sessão — procurar só
# pelo session_id devolveria vazio e o vazio passaria por "não aconteceu".
echo "── eventos de desconexão/saída de pool na janela (sem filtro de sessão) ────"
$COMPOSE logs --since "$SINCE" --timestamps mcp-server-plughub 2>/dev/null \
  | grep -iE "logout|leave|remainingPools|allPools|release|ws close|disconnect" \
  | tail -30 | sed 's/^/   /'
echo
echo "══ leitura ════════════════════════════════════════════════════════════════"
echo "   Ache a linha que ZADD-ou de volta. Se NENHUM serviço registrou o re-enqueue,"
echo "   isso é achado próprio: uma devolução à fila sem rastro é a degradação silenciosa"
echo "   que a § Postura proíbe — e explica por que o defeito sobreviveu até hoje."
