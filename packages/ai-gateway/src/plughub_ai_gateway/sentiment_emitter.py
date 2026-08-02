"""
sentiment_emitter.py
AI Gateway — sentiment.updated Kafka event + sentiment_live Redis aggregate
             + ContextStore write (session.sentimento.*).

Princípio: fire-and-forget em todos os paths. Erros de infraestrutura nunca
bloqueiam o retorno do AI Gateway ao agente chamador.

Tópico Kafka: sentiment.updated
  Publicado após cada turno LLM com extraction bem-sucedida.
  Consumido por: analytics-api (Arc 3) para agregar sentimento por pool
  em real-time no dashboard operacional.

Redis key: {tenant_id}:pool:{pool_id}:sentiment_live
  Hash com avg_score + score_total + count (valores numéricos puros).
  TTL: 300s (renovado a cada atualização).
  Lido por: analytics-api → GET /dashboard/sentiment.

Nota de arquitetura: category classification (satisfied/neutral/frustrated/angry)
  é RESPONSABILIDADE DO CONSUMER (analytics-api), não do AI Gateway.
  As faixas são configuráveis por tenant via Config API — o AI Gateway apenas
  produz o score numérico bruto e não deve interpretar seu significado.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.ai_gateway.sentiment")

_TOPIC = "sentiment.updated"
_SENTIMENT_LIVE_TTL = 300  # seconds


# ── Kafka emission ────────────────────────────────────────────────────────────

async def emit_sentiment_updated(
    producer:   Any,
    tenant_id:  str,
    session_id: str,
    pool_id:    str,
    score:      float,
) -> None:
    """
    Publica sentiment.updated no Kafka.
    Fire-and-forget: nunca levanta exceção.

    Payload: event_id, tenant_id, session_id, pool_id, score, timestamp.
    Sem category — classificação é responsabilidade do analytics-api consumer.
    """
    if producer is None:
        return
    try:
        event = {
            "event_id":   str(uuid.uuid4()),
            "tenant_id":  tenant_id,
            "session_id": session_id,
            "pool_id":    pool_id,
            "score":      round(score, 4),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
        value = json.dumps(event).encode("utf-8")
        await producer.send(_TOPIC, value=value)
    except Exception as exc:
        logger.warning(
            "Failed to emit sentiment.updated tenant=%s session=%s: %s",
            tenant_id, session_id, exc,
        )


# ── Redis live aggregate ──────────────────────────────────────────────────────

async def update_sentiment_live(
    redis:      Any,
    tenant_id:  str,
    pool_id:    str,
    score:      float,
    session_id: str,
) -> None:
    """
    Mantém o hash {tenant_id}:pool:{pool_id}:sentiment_live no Redis.
    Atualiza:
      - avg_score: média móvel simples (running total / count)
      - score_total: soma acumulada dos scores
      - count: total de atualizações
      - last_session_id: última sessão que gerou atualização
      - updated_at: timestamp ISO8601

    Sem contagens por categoria — classificação é responsabilidade do consumer.
    TTL renovado para 300s a cada atualização.
    Fire-and-forget: nunca levanta exceção.
    """
    if redis is None:
        return
    key = f"{tenant_id}:pool:{pool_id}:sentiment_live"
    try:
        raw   = await redis.hgetall(key)
        count = int(raw.get("count", 0)) + 1
        total = round(float(raw.get("score_total", 0.0)) + score, 4)
        avg   = round(total / count, 4)

        mapping = {
            "avg_score":       str(avg),
            "score_total":     str(total),
            "count":           str(count),
            "last_session_id": session_id,
            "updated_at":      datetime.now(timezone.utc).isoformat(),
        }
        await redis.hset(key, mapping=mapping)
        await redis.expire(key, _SENTIMENT_LIVE_TTL)
    except Exception as exc:
        logger.warning(
            "Failed to update sentiment_live key=%s: %s", key, exc,
        )


# ── ContextStore write ────────────────────────────────────────────────────────

_CTX_SESSION_TTL = 14_400  # 4 hours — matches ContextStore default session TTL


async def write_context_store_sentiment(
    redis:      Any,
    tenant_id:  str,
    session_id: str,
    score:      float,
) -> None:
    """
    Escreve o sentimento atual no ContextStore da sessão.
    Chave: {tenant_id}:ctx:{session_id}  (hash Redis)
    Tag:
      session.sentimento.current → score numérico (-1.0 a 1.0)

    **`session.sentimento.categoria` NÃO é escrita aqui (corrigido 2026-08-02).**
    A classificação em satisfied/neutral/frustrated/angry usa faixas CONFIGURÁVEIS POR
    TENANT e é feita na LEITURA, pelo consumidor — regra escrita em três lugares
    independentes (o cabeçalho deste módulo, `platform-events.ts` e o `CLAUDE.md`
    § Sentiment Tracking). O classificador canônico vive em
    `analytics-api/sessions.py::_classify`; os limiares, no `config-api`.

    Histórico, porque o modo de falha vale mais que o conserto: `_classify` foi
    REMOVIDO deste módulo para fazer valer essa regra, e a chamada aqui ficou para trás.
    Toda invocação levantava `NameError` — e a linha caía FORA do `try` abaixo, que
    começava depois dela. O chamador (`session.py`) engolia com
    `logger.warning("Sentiment pipeline failed …")`, mensagem que soa intermitente
    quando o defeito era permanente; e como as duas emissões anteriores (Kafka + Redis
    live) já haviam sucedido, o pipeline parecia funcionar. Resultado: NENHUMA das duas
    tags chegava ao ContextStore, incluindo `current`, que nada tinha a ver com o
    problema. O `copilot_emitter` lia `categoria` e degradava sem log.

    Só foi encontrado porque o `testpaths` deste pacote apontava para um diretório
    inexistente (hífen × underscore) — a suíte inteira nunca rodou.

    Convenções de ContextEntry:
      confidence: 0.80 — inferência do AI Gateway (não é dado declarado pelo cliente)
      source:     "ai_inferred:sentiment_emitter"
      visibility: "agents_only" — não é exposto ao cliente

    Fire-and-forget: nunca levanta exceção — e agora isso é verdade, porque não há
    computação fora do `try`.
    """
    if redis is None:
        return
    key = f"{tenant_id}:ctx:{session_id}"
    now = datetime.now(timezone.utc).isoformat()

    entry_current = json.dumps({
        "value":      round(score, 4),
        "confidence": 0.80,
        "source":     "ai_inferred:sentiment_emitter",
        "visibility": "agents_only",
        "updated_at": now,
    })

    try:
        await redis.hset(
            key,
            mapping={"session.sentimento.current": entry_current},
        )
        # Renew TTL on the session context hash
        await redis.expire(key, _CTX_SESSION_TTL)
    except Exception as exc:
        logger.warning(
            "Failed to write context_store sentiment key=%s: %s", key, exc,
        )
