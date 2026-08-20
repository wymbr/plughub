"""
sentiment_analyzer.py
AI Gateway — MEDIÇÃO de sentimento a partir da fala do cliente.

Distinto de `sentiment_emitter.py`, que só PUBLICA um score já calculado (Kafka +
Redis live + ContextStore). Este módulo é a fonte; aquele é o encanamento.

── Por que existe ───────────────────────────────────────────────────────────────
Até 2026-08-23 a plataforma não media sentimento em lugar nenhum, por dois caminhos
que pareciam medir:

  · `/v1/reason` (`main.py:456`) lia `sentiment_score` do RESULTADO do LLM, isto é,
    do `output_schema` que o skill declarou. **Nenhum skill do repositório o
    declara**, então o valor era sempre `0.0` — neutro, indistinguível de
    não-medido. Sentimento auto-reportado pelo próprio modelo que está atendendo
    também não é medição: é o avaliado dando a própria nota.
  · `/inference` (`inference.py:163-189`) isola estruturalmente a fala do cliente —
    e entrega a `extract_context_from_response` (`context.py:53-64`), que é uma
    **contagem de palavras-chave em português**: 10 negativas, 8 positivas,
    `(pos−neg)/total`. O próprio comentário do arquivo diz *"In production:
    sentiment model fine-tuned per vertical"*. Além disso `/inference` não tem
    chamador algum no repositório.

Decisão: a extração é do GATEWAY (é ele que sabe falar com o modelo), a fala chega
NOMEADA no contrato (`ReasonRequest.customer_utterance`, preenchido a partir de uma
referência declarada no step), e o cálculo é uma chamada dedicada e barata, fora do
turno. Não se pede ao skill que declare `sentiment_score` no `output_schema`: isso
poria invariante de plataforma em YAML de tenant.

── Regras de instrumento ────────────────────────────────────────────────────────
· Fire-and-forget: nunca bloqueia nem derruba o retorno do `/v1/reason`.
· **Falha NÃO vira 0.0.** Sem score medido, nada é escrito — ausência denuncia,
  neutro mente. Esta é a regra inteira do módulo.
· Perfil `fast` (haiku), isolado do tráfego realtime, como o copiloto.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .sentiment_emitter import (
    emit_sentiment_updated,
    update_sentiment_live,
    write_context_store_sentiment,
)

logger = logging.getLogger("plughub.ai_gateway.sentiment_analyzer")

_MAX_UTTERANCE_LEN = 1_000
_MAX_TOKENS        = 64

_SYSTEM_PROMPT = """\
You rate the sentiment of a customer's message in a customer-service conversation.

Respond ONLY with a JSON object, no markdown and no explanation:
{"sentiment_score": <number between -1.0 and 1.0>}

Scale:
 -1.0  furious, abusive, threatening to leave
 -0.5  frustrated, complaining
  0.0  neutral, purely informational
 +0.5  satisfied, cooperative
 +1.0  delighted, thanking effusively

Rules:
- Rate the CUSTOMER's state, not the topic. A calm question about a billing error is
  near 0.0; an angry question about the same error is negative.
- Judge the message in its own language; do not translate first.
- Irony and sarcasm are negative even when the words are positive.
- Output the number only inside the JSON. Never add fields.
"""


def _parse_score(text: str) -> float | None:
    """
    Extrai o score da resposta do modelo. Devolve None quando não dá para ler —
    **nunca 0.0**, que é um valor legítimo da escala e viraria "cliente neutro".

    Aceita JSON cru ou embrulhado em cerca de markdown; como último recurso, procura
    o primeiro número da faixa no texto. O fallback existe porque `max_tokens` baixo
    pode truncar a chave de fechamento, e um score correto truncado não deveria virar
    ausência.
    """
    if not text:
        return None

    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(clean)
        if isinstance(data, dict) and isinstance(data.get("sentiment_score"), (int, float)):
            return max(-1.0, min(1.0, float(data["sentiment_score"])))
    except Exception:
        pass

    # O fallback numérico só vale DEPOIS da chave `sentiment_score`, e só existe para
    # o caso de JSON truncado por `max_tokens` (`{"sentiment_score": -0.3` sem fechar).
    #
    # Sem essa âncora ele pescava o primeiro número de QUALQUER texto: `{"outro": 1}`
    # — JSON válido, sem o campo — virava score 1.0, "cliente encantado" fabricado a
    # partir de dado alheio. Pego pelo teste, não em produção. É a mesma família do
    # `0.0` default que este módulo veio substituir: um número plausível vindo de
    # lugar nenhum.
    key_at = clean.find("sentiment_score")
    if key_at >= 0:
        match = re.search(r"-?\d+(?:\.\d+)?", clean[key_at + len("sentiment_score"):])
        if match:
            try:
                value = float(match.group())
                if -1.0 <= value <= 1.0:
                    logger.debug("sentiment: score lido por fallback numérico — texto=%r", clean[:120])
                    return value
            except ValueError:
                pass

    logger.warning("sentiment: resposta ilegível do modelo — texto=%r", clean[:200])
    return None


async def _resolve_pool_id(redis: Any, session_id: str) -> str:
    """
    Lê `pool_id` de `session:{id}:meta` — mesma fonte que `session.py:140` usa.

    Ausente devolve `"unknown"` porque o agregado por pool ainda tem valor sem ele,
    mas o caso é LOGADO: `"unknown"` que aparece em massa é sintoma de meta não
    escrito, não de fluxo sem pool.
    """
    try:
        pool_id = await redis.hget(f"session:{session_id}:meta", "pool_id")
        if pool_id:
            return pool_id
        logger.info("sentiment: session:%s:meta sem pool_id — agregando sob 'unknown'", session_id)
    except Exception as exc:
        logger.warning("sentiment: falha ao ler pool_id de session:%s:meta — %s", session_id, exc)
    return "unknown"


async def analyze_and_emit_sentiment(
    redis:              Any,
    provider:           Any,      # LLMProvider — .call(messages, tools, model_id, max_tokens)
    producer:           Any,      # AIOKafkaProducer | None
    tenant_id:          str,
    session_id:         str,
    customer_utterance: str,
    model_id:           str,
) -> None:
    """
    Mede o sentimento de UMA fala do cliente e publica pelos três canais existentes.

    Fire-and-forget: nunca levanta. Mas também nunca degrada em silêncio — cada
    caminho de saída diz por que saiu.
    """
    if not customer_utterance or not customer_utterance.strip():
        return  # nada a medir; não é falha

    if provider is None:
        logger.warning(
            "sentiment: sem provider LLM — sentimento NÃO medido para session=%s", session_id,
        )
        return

    if not tenant_id:
        # Escrever sob tenant vazio é pior que não escrever: o dado existe, ninguém o
        # encontra, e a chave órfã passa por "há sentimento no sistema".
        logger.warning(
            "sentiment: tenant_id vazio para session=%s — recusado (chave nasceria "
            "sem prefixo de tenant e o dado ficaria inalcançável)", session_id,
        )
        return

    try:
        messages = [
            # `role: "system"` como MENSAGEM: o provider extrai para o parâmetro
            # nativo. Não existe kwarg `system` em `LLMProvider.call()` — foi
            # exatamente esse engano que manteve o `copilot_emitter` mudo.
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": customer_utterance[:_MAX_UTTERANCE_LEN]},
        ]
        response = await provider.call(
            messages   = messages,
            tools      = None,
            model_id   = model_id,
            max_tokens = _MAX_TOKENS,
        )
        # `.content`, não `.text` — o segundo não existe em `LLMResponse`.
        score = _parse_score(getattr(response, "content", "") or "")
    except Exception as exc:
        logger.warning(
            "sentiment: chamada ao modelo falhou session=%s — %s. Nada escrito "
            "(um 0.0 aqui viraria 'cliente neutro').", session_id, exc,
        )
        return

    if score is None:
        return  # `_parse_score` já registrou o motivo

    pool_id = await _resolve_pool_id(redis, session_id)

    await emit_sentiment_updated(
        producer=producer, tenant_id=tenant_id, session_id=session_id,
        pool_id=pool_id, score=score,
    )
    await update_sentiment_live(
        redis=redis, tenant_id=tenant_id, pool_id=pool_id,
        score=score, session_id=session_id,
    )
    await write_context_store_sentiment(
        redis=redis, tenant_id=tenant_id, session_id=session_id, score=score,
    )
    logger.info(
        "sentiment: medido session=%s pool=%s score=%+.2f", session_id, pool_id, score,
    )
