"""
usage_emitter.py
Utilitário de emissão de eventos de consumo (usage.events) para o AI Gateway.

Princípio: metering ≠ pricing.
Publica apenas o fato do consumo — sem preço, sem plano, sem quota.
O módulo de pricing (a construir) lê estes dados e decide o que cobrar.

Tópico Kafka: usage.events
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.ai_gateway.usage")

# Prazo do `producer.send`. Mesma razão e mesma ordem de grandeza do emissor de
# sentimento: com broker inalcançável o `send` bloqueia ~40 s em vez de levantar.
_SEND_TIMEOUT_S = 5.0


async def emit_llm_tokens(
    producer: Any,           # aiokafka.AIOKafkaProducer ou duck-type compatível
    tenant_id:    str,
    session_id:   str | None,
    model_id:     str,
    agent_type_id: str | None,
    input_tokens:  int,
    output_tokens: int,
    source:       str,       # T1 — qual caminho gastou. Obrigatório: ver docstring.
    gateway_id:   str = "ai-gateway",
    # ── T2 — chave de atribuição (D1) e identidade da conta (D2) ──────────────
    segment_id:        str | None = None,
    account_config_id: str | None = None,
    account_key_id:    str | None = None,
    model_profile:     str | None = None,
) -> None:
    """
    Publica dois eventos de consumo em usage.events:
      - llm_tokens_input  (qty = input_tokens)
      - llm_tokens_output (qty = output_tokens)

    Input e output são eventos separados porque têm tarifas distintas em todos
    os provedores LLM.

    Fire-and-forget: erros são logados mas nunca bloqueiam o caminho operacional.

    ── T1 (2026-08-28): esta função é o CHOKE POINT dos quatro caminhos vivos ──

    `source` é obrigatório e não tem default. A T0 mediu que **42% das chamadas
    LLM vinham do `sentiment_analyzer`, que não tem rota própria** — um produtor
    ligado ao handler HTTP perderia a fatia inteira, e caladamente. Com o campo
    obrigatório, um caminho novo não consegue emitir sem dizer quem é, e a
    checagem "cada caminho vivo emite" do `probe_llm_call_paths.sh` tem o que ler.

    DUAS GUARDAS que existem por defeito já pago neste repositório:

    · **tenant vazio não emite.** `ReasonRequest.tenant_id` tem default `""`, e
      um evento sem tenant não é atribuível a ninguém — vira custo órfão que
      infla o total e não aparece em nenhuma linha. Recusa NOMEADA, não silêncio.
    · **`producer.send` tem prazo.** Ele NÃO levanta com broker inalcançável: ele
      BLOQUEIA ~40 s no refresh de metadata (medido em 2026-08-24 no emissor de
      sentimento, que por isso deixava o score ilegível por 40 s). Sem o
      `wait_for`, um broker fora do ar viraria latência no caminho de LLM.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return  # resposta em cache ou erro — sem tokens reais

    if producer is None:
        logger.warning(
            "usage: sem produtor Kafka — consumo NÃO publicado source=%s tenant=%s "
            "in=%d out=%d (o gasto ocorreu; o registro é que se perdeu)",
            source, tenant_id, input_tokens, output_tokens,
        )
        return

    if not tenant_id:
        logger.warning(
            "usage: tenant VAZIO — consumo NÃO publicado source=%s session=%s in=%d out=%d. "
            "Evento sem tenant não é atribuível e inflaria o total sem aparecer em linha nenhuma.",
            source, session_id, input_tokens, output_tokens,
        )
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "model_id":     model_id,      # modelo EFETIVO (resp.model_used)
        "gateway_id":   gateway_id,
        "source":       source,
    }
    if agent_type_id:
        metadata["agent_type_id"] = agent_type_id

    # ── T2/D2 — as DUAS identidades de conta, e o par de modelo ──────────────
    #
    # `config_id` (catálogo, sobrevive à rotação de chave) responde *custo por
    # conta*; `key_id` (hash da chave) responde *depuração de rate-limit*. São
    # perguntas diferentes: guardar só o hash faz uma rotação parecer conta nova.
    #
    # `model_profile` é o que o skill PEDIU (`balanced`); `model_id` é o que
    # RESPONDEU. A divergência entre os dois É o diagnóstico de fallback — e some
    # se só um for gravado.
    #
    # Ausência vira chave AUSENTE, nunca string vazia: `""` viraria um valor
    # legítimo do eixo e criaria uma conta chamada "vazio" no relatório.
    if account_config_id:
        metadata["account_config_id"] = account_config_id
    if account_key_id:
        metadata["account_key_id"] = account_key_id
    if model_profile:
        metadata["model_profile"] = model_profile

    events = []
    if input_tokens > 0:
        events.append({
            "event_id":         str(uuid.uuid4()),
            "tenant_id":        tenant_id,
            "session_id":       session_id,
            "segment_id":       segment_id,
            "dimension":        "llm_tokens_input",
            "quantity":         input_tokens,
            "timestamp":        timestamp,
            "source_component": "ai-gateway",
            "metadata":         metadata,
        })
    if output_tokens > 0:
        events.append({
            "event_id":         str(uuid.uuid4()),
            "tenant_id":        tenant_id,
            "session_id":       session_id,
            "segment_id":       segment_id,
            "dimension":        "llm_tokens_output",
            "quantity":         output_tokens,
            "timestamp":        timestamp,
            "source_component": "ai-gateway",
            "metadata":         metadata,
        })

    for event in events:
        try:
            value = json.dumps(event).encode("utf-8")
            # `wait_for` porque `send` BLOQUEIA (não levanta) com broker fora do ar.
            await asyncio.wait_for(
                producer.send("usage.events", value=value),
                timeout=_SEND_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "usage: emissão excedeu %.1fs dimension=%s source=%s tenant=%s — o consumo "
                "OCORREU e não foi registrado; o relatório vai subcontar esta chamada.",
                _SEND_TIMEOUT_S, event["dimension"], source, tenant_id,
            )
        except Exception as exc:
            # Metering nunca bloqueia operação — falha logada, nunca engolida
            logger.warning(
                "usage: falha ao emitir dimension=%s source=%s tenant=%s: %s",
                event["dimension"], source, tenant_id, exc,
            )
