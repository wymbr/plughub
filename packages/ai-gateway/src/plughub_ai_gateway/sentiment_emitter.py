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
  Hash com avg_score + distribuição por categoria.
  TTL: 300s (renovado a cada atualização).
  Lido por: analytics-api → GET /dashboard/sentiment.

Categorias de sentimento (ranges configuráveis por tenant — padrão):
  [ 0.3,  1.0] → satisfied
  [-0.3,  0.3] → neutral
  [-0.6, -0.3] → frustrated
  [-1.0, -0.6] → angry
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


# ── Category classification ───────────────────────────────────────────────────

def _classify(score: float) -> str:
    """Maps a sentiment score to the canonical category label."""
    if score >= 0.3:
        return "satisfied"
    elif score >= -0.3:
        return "neutral"
    elif score >= -0.6:
        return "frustrated"
    else:
        return "angry"


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

    Payload:
      event_id, tenant_id, session_id, pool_id, score, category, timestamp
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
            "category":   _classify(score),
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
      - count: total de atualizações
      - satisfied / neutral / frustrated / angry: contagem por categoria
      - last_session_id: última sessão que gerou atualização
      - updated_at: timestamp ISO8601

    TTL renovado para 300s a cada atualização.
    Fire-and-forget: nunca levanta exceção.
    """
    if redis is None:
        return
    key = f"{tenant_id}:pool:{pool_id}:sentiment_live"
    try:
        # Lê estado atual para calcular nova média
        raw = await redis.hgetall(key)
        count     = int(raw.get("count", 0))
        total     = float(raw.get("score_total", 0.0))

        count  += 1
        total  += score
        avg    = round(total / count, 4)
        cat    = _classify(score)

        # Incrementa categoria
        cat_count = int(raw.get(cat, 0)) + 1

        mapping = {
            "avg_score":       str(avg),
            "score_total":     str(round(total, 4)),
            "count":           str(count),
            cat:               str(cat_count),
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
    Tags:
      session.sentimento.current   → score numérico (-1.0 a 1.0)
      session.sentimento.categoria → categoria textual (satisfied/neutral/frustrated/angry)

    Convenções de ContextEntry:
      confidence: 0.80 — inferência do AI Gateway (não é dado declarado pelo cliente)
      source:     "ai_inferred:sentiment_emitter"
      visibility: "agents_only" — não é exposto ao cliente

    Fire-and-forget: nunca levanta exceção.
    """
    if redis is None:
        return
    key      = f"{tenant_id}:ctx:{session_id}"
    category = _classify(score)
    now      = datetime.now(timezone.utc).isoformat()

    entry_current = json.dumps({
        "value":      round(score, 4),
        "confidence": 0.80,
        "source":     "ai_inferred:sentiment_emitter",
        "visibility": "agents_only",
        "updated_at": now,
    })
    entry_category = json.dumps({
        "value":      category,
        "confidence": 0.80,
        "source":     "ai_inferred:sentiment_emitter",
        "visibility": "agents_only",
        "updated_at": now,
    })

    try:
        await redis.hset(
            key,
            mapping={
                "session.sentimento.current":   entry_current,
                "session.sentimento.categoria": entry_category,
            },
        )
        # Renew TTL on the session context hash
        await redis.expire(key, _CTX_SESSION_TTL)
    except Exception as exc:
        logger.warning(
            "Failed to write context_store sentiment key=%s: %s", key, exc,
        )
