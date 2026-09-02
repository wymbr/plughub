"""
sentiment_emitter.py
AI Gateway — sentiment.updated Kafka event + sentiment_live Redis aggregate
             + ContextStore write (core.sentiment.*).

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

from plughub_contextstore.writer import write_context_tags
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.ai_gateway.sentiment")

_TOPIC = "sentiment.updated"
_SENTIMENT_LIVE_TTL = 300  # seconds


# ── Pool de agregação ─────────────────────────────────────────────────────────

async def resolve_session_pool_id(redis: Any, session_id: str) -> str:
    """
    Lê o `pool_id` de `session:{id}:meta` — a chave é **String (JSON)**, não hash.

    ⚠️ Este helper existe porque o ai-gateway errava o TIPO da chave, em dois lugares
    independentes (`session.py` e `sentiment_analyzer.py`), ambos com `HGET`. O
    contrato está escrito (`orchestrator-bridge/CLAUDE.md:35` — "String (JSON)") e
    todos os leitores do bridge usam `GET`; só o ai-gateway divergia.

    O modo de falha era caro justamente por ser plausível: `HGET` numa string levanta
    `WRONGTYPE`, o `except` devolvia `"unknown"`, e o painel de sentimento agregava
    TODO contato real sob um balde só — com o dado presente na chave o tempo inteiro.
    Só aparecia em contato de verdade: sessão sintética não tem a chave, `HGET` numa
    chave AUSENTE devolve `None` sem levantar, e o log saía pelo ramo benigno
    ("sem pool_id"). Medido em 2026-08-24.

    Cada saída diz por que saiu, e são quatro motivos DIFERENTES: chave ausente ·
    leitura falhou · JSON ilegível · campo ausente. Um `"unknown"` mudo os
    confundiria, que foi o que escondeu o defeito.

    ⚠️ SEMÂNTICA (dívida conhecida, não corrigida aqui): o `pool_id` do meta é o pool
    de ENTRADA do contato, não o que está atendendo no momento. Um cliente que entrou
    pelo `sac_ia` e está falando com o agente de FILA tem o sentimento agregado sob
    `sac_ia`. É a ambiguidade registrada na fatia C de `session:{id}:meta`
    (`entry_pool_id` × `pool_id`) — ver `docs/guias/session-meta-ownership.md`.
    """
    if redis is None:
        return "unknown"
    key = f"session:{session_id}:meta"
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.warning("sentiment: falha ao LER %s — %s. Agregando sob 'unknown'.", key, exc)
        return "unknown"
    if not raw:
        logger.info("sentiment: %s ausente — agregando sob 'unknown'", key)
        return "unknown"
    try:
        meta = json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception as exc:
        logger.warning("sentiment: %s não é JSON legível — %s", key, exc)
        return "unknown"
    pool_id = meta.get("pool_id") if isinstance(meta, dict) else None
    if not pool_id:
        logger.info("sentiment: %s sem campo pool_id — agregando sob 'unknown'", key)
        return "unknown"
    return str(pool_id)


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
      core.sentiment.current → score numérico (-1.0 a 1.0)

    **`core.sentiment.category` NÃO é escrita aqui (corrigido 2026-08-02).**
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
    now = datetime.now(timezone.utc).isoformat()

    try:
        # ALW-02 — pelo funil, que CARIMBA o `atributo` a partir do cadastro (D9.6).
        # TTL renovado SEM `nx`, como antes: sentimento é medido ao longo da sessão e
        # cada medição estende a vida do hash de propósito.
        await write_context_tags(
            redis, tenant_id, session_id,
            {"core.sentiment.current": round(score, 4)},
            source="ai_inferred:sentiment_emitter", confidence=0.80,
            updated_at=now, ttl_s=_CTX_SESSION_TTL,
        )
    except Exception as exc:
        logger.warning(
            "Failed to write context_store sentiment tenant=%s session=%s: %s",
            tenant_id, session_id, exc,
        )
