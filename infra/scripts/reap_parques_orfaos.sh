#!/usr/bin/env bash
# ==============================================================================
# reap_parques_orfaos.sh — MUTIRÃO ÚNICO: encerra sessões `suspended` sem endereço
# ==============================================================================
#
# O QUE ELE FAZ
# -------------
# Encontra sessões que ficaram `suspended` para sempre — prazo vencido, endereço de
# retomada perdido — e as encerra com `close_reason = suspend_orphaned`, emitindo
# `contact_closed` no tópico `conversations.events`.
#
# POR QUE ELE EXISTE (RET-14, 2026-09-09)
# ---------------------------------------
# O token que endereça uma sessão suspensa vive no Redis, que neste deploy roda
# `--save ""` com `appendonly no`. Perdido o Redis, perde-se o endereço — e com ele
# o `pipeline_state` do flow. A sessão não tem a que voltar, e **nada a alcança**:
# o scanner varre tokens que não existem mais, e o `force-complete` responde 404
# porque o estado 1 dele exige o item no ledger.
#
# Medido em 2026-09-09: 212 sessões assim, a mais antiga de 10/08.
#
# ⚠️ **Isto é LIMPEZA, não mecanismo.** O mecanismo é a RET-11, que deu registro
# durável ao parque para que a população pare de crescer. Este script existe para a
# dívida que já estava lá quando o registro nasceu — o consumidor da RET-11 é
# `auto_offset_reset=latest` de propósito, então ele não enxerga o passado.
# Rodar isto uma vez e nunca mais é o desfecho esperado.
#
# ⚠️ **DRY-RUN por padrão.** Sem `--apply` ele não escreve nada: conta, agrupa e
# mostra. Encerrar sessão é irreversível pelo caminho normal (o `ReplacingMergeTree`
# guarda a última versão), e um mutirão que escreve por default é o tipo de
# ferramenta que apaga um incidente em vez de denunciá-lo.
#
# ⚠️ **O corte de idade protege quem ainda espera.** Só entra o que abriu há mais de
# `--dias` (default 7). Havia 3 sessões dentro do prazo na medição original; um corte
# ausente as mataria junto — política contra uma população que não foi contada é o
# erro que a D14.1 registra.
#
# ⚠️ **Sessão com token VIVO nunca entra.** Antes de emitir, cada candidata é
# conferida contra `{tenant}:resume_tokens` no Redis: se o endereço existe, a sessão
# é alcançável e não é órfã — é só uma espera longa.
#
# USO
#   ./reap_parques_orfaos.sh                 # dry-run, 7 dias
#   ./reap_parques_orfaos.sh --dias 30       # dry-run, corte mais conservador
#   ./reap_parques_orfaos.sh --apply         # escreve
#
# SAIDA: 0 = ok (com ou sem candidatas) · 1 = erro · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

DC="${DC:-docker compose -f docker-compose.demo.yml}"
CH_DB="${CH_DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
DIAS=7
APLICAR=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dias)  DIAS="$2"; shift 2 ;;
    --apply) APLICAR=1; shift ;;
    *) echo "argumento desconhecido: $1"; exit 1 ;;
  esac
done

printf '\033[1mmutirao: sessoes `suspended` sem endereco de retomada\033[0m\n'
printf '  tenant=%s  corte=%s dia(s)  modo=%s\n\n' "$TENANT" "$DIAS" \
  "$([ "$APLICAR" = 1 ] && echo APLICAR || echo DRY-RUN)"

# ── 1. candidatas ────────────────────────────────────────────────────────────
# ⚠️ `FINAL` nao e enfeite. `sessions` e ReplacingMergeTree: ate a fusao, a
# versao ANTIGA (status=suspended) continua visivel ao lado da nova. Sem ele a
# segunda execucao reproponha as 163 ja fechadas — medido — e o relatorio
# mentiria sobre o que sobrou.
# A linha COMPLETA, porque `sessions` e ReplacingMergeTree de linha inteira: um
# evento parcial chega com row_version mais novo e APAGA pool_id/channel/opened_at.
CANDIDATAS=$($DC exec -T clickhouse clickhouse-client -q "
  SELECT toJSONString(map(
    'session_id', session_id, 'tenant_id', tenant_id, 'channel', channel,
    'pool_id', pool_id, 'customer_id', toString(customer_id),
    'opened_at', toString(opened_at),
    'origin_session_id', toString(origin_session_id),
    'spawn_reason', toString(spawn_reason)))
  FROM ${CH_DB}.sessions FINAL
  WHERE tenant_id='${TENANT}' AND status='suspended' AND closed_at IS NULL
    AND opened_at < now() - INTERVAL ${DIAS} DAY
  ORDER BY opened_at
" 2>/dev/null)

if [ -z "$CANDIDATAS" ]; then
  echo "  nenhuma candidata — nada a fazer."
  exit 0
fi
N=$(printf '%s\n' "$CANDIDATAS" | grep -c .)

echo "── o que seria encerrado ────────────────────────────────────────────"
$DC exec -T clickhouse clickhouse-client -q "
  SELECT pool_id, count() AS n, min(opened_at) AS mais_antiga
  FROM ${CH_DB}.sessions FINAL
  WHERE tenant_id='${TENANT}' AND status='suspended' AND closed_at IS NULL
    AND opened_at < now() - INTERVAL ${DIAS} DAY
  GROUP BY pool_id ORDER BY n DESC" 2>/dev/null | sed 's/^/   /'
printf '   ── total: %s\n\n' "$N"

if [ "$APLICAR" != 1 ]; then
  echo "DRY-RUN — nada foi escrito. Use --apply para encerrar."
  exit 0
fi

# ── 2. emitir ────────────────────────────────────────────────────────────────
# Roda DENTRO do channel-gateway: e o container que ja tem aiokafka, Redis e rede
# para o broker. O host so passa a lista, pelo STDIN.
#
# ⚠️ O programa vai em `python -c`, NAO em heredoc. Um `<<'PY'` ocupa o stdin, e
# entao a lista canalizada nunca chega — foi assim que a primeira versao deste
# script emitiu ZERO eventos e ainda assim imprimiu "OK". Com `-c`, o stdin fica
# livre para os dados, que e o unico arranjo em que as duas coisas cabem.
PROGRAMA=$(cat <<'PY'
import asyncio, json, os, sys
from datetime import datetime, timezone
from aiokafka import AIOKafkaProducer
import redis.asyncio as aioredis

tenant    = sys.argv[1]
linhas    = [l for l in sys.stdin.read().split("\n") if l.strip()]
brokers   = os.getenv("PLUGHUB_KAFKA_BROKERS") or os.getenv("KAFKA_BROKERS") or "kafka:9092"
redis_url = os.getenv("PLUGHUB_REDIS_URL") or os.getenv("REDIS_URL") or "redis://redis:6379"

async def main() -> int:
    if not linhas:
        print("INCONCLUSIVO: nenhuma candidata chegou pelo stdin", file=sys.stderr)
        return 2
    r = aioredis.from_url(redis_url, decode_responses=True)
    # O endereco vivo de qualquer sessao deste tenant. Uma leitura so: o hash e do
    # TENANT, nao da sessao.
    try:
        vivos = await r.hgetall(f"{tenant}:resume_tokens")
    except Exception as exc:
        print(f"INCONCLUSIVO: nao li os tokens vivos ({exc}) — sem essa leitura eu "
              f"poderia encerrar sessao ALCANCAVEL", file=sys.stderr)
        await r.aclose()
        return 2
    sessoes_vivas = {v.split(":", 1)[0] for v in vivos.values()}

    p = AIOKafkaProducer(bootstrap_servers=brokers,
                         value_serializer=lambda v: json.dumps(v).encode())
    await p.start()
    agora, emitidos, pulados = datetime.now(timezone.utc).isoformat(), 0, 0
    try:
        for linha in linhas:
            s = json.loads(linha)
            if s["session_id"] in sessoes_vivas:
                pulados += 1
                print(f"   PULADA (token vivo): {s['session_id']}")
                continue
            await p.send_and_wait("conversations.events", {
                "event_type":   "contact_closed",
                # `source` proprio: `channel_gateway` faz o handler da analytics
                # RETORNAR NONE, e a trilha deve dizer quem fechou.
                "source":       "parking_reaper",
                "session_id":   s["session_id"],
                "tenant_id":    s["tenant_id"],
                "channel":      s["channel"],
                "pool_id":      s["pool_id"],
                "customer_id":  s["customer_id"] or None,
                "origin_session_id": s["origin_session_id"] or None,
                "spawn_reason":      s["spawn_reason"] or None,
                "started_at":   s["opened_at"],
                "ended_at":     agora,
                "close_reason": "suspend_orphaned",
                "timestamp":    agora,
            })
            emitidos += 1
    finally:
        await p.stop()
        await r.aclose()
    print(f"\n   candidatas={len(linhas)}  emitidos={emitidos}  pulados_por_token_vivo={pulados}")
    # ⚠️ Zero emitido com candidatas na mao NAO e sucesso. A primeira versao deste
    # script dizia "OK" nesse estado, que e a degradacao de SINAL TROCADO: pior que
    # inerte, porque afirma ter feito.
    if emitidos == 0 and pulados < len(linhas):
        print("FALHA: havia candidata a emitir e nada foi emitido", file=sys.stderr)
        return 1
    return 0

sys.exit(asyncio.run(main()))
PY
)

printf '%s\n' "$CANDIDATAS" | $DC exec -T channel-gateway python -c "$PROGRAMA" "$TENANT"
CODE=$?
printf '\n'
case "$CODE" in
  0) printf '\033[32mOK\033[0m — eventos emitidos. A analytics consome em segundos; confira com:\n'
     printf "   SELECT close_reason, count() FROM ${CH_DB}.sessions WHERE tenant_id='${TENANT}' AND close_reason='suspend_orphaned' GROUP BY close_reason\n" ;;
  2) printf '\033[33mINCONCLUSIVO\033[0m — nada foi escrito, e o motivo esta acima.\n' ;;
  *) printf '\033[31mFALHOU\033[0m (exit=%s) — ver acima.\n' "$CODE" ;;
esac
exit "$CODE"
