"""
copilot_emitter.py
AI Gateway — Co-pilot Phase 2: background analysis of customer messages
during human agent sessions.

Princípio: fire-and-forget em todos os paths. Erros de infraestrutura nunca
bloqueiam o retorno ao chamador.

Fluxo:
  1. Lê ContextStore ({tenant_id}:ctx:{session_id}) para contexto da sessão
     (caller.nome, caller.motivo_contato, session.sentimento.categoria)
  2. Monta prompt compacto e chama LLM (haiku — isolado de tráfego realtime)
  3. Parseia resposta JSON: { sugestao_resposta, flags_risco, acoes_recomendadas }
  4. Escreve session.copilot.* no ContextStore (fire-and-forget)
  5. Publica copilot.updated em agent:events:{session_id} via Redis pub/sub
     → Agent Assist UI recebe via WebSocket e re-busca /copilot_state

ContextStore tags escritas:
  session.copilot.sugestao_resposta   → string: sugestão de resposta para o agente
  session.copilot.flags_risco         → list[str]: flags de risco detectados
  session.copilot.acoes_recomendadas  → list[str]: ações recomendadas
  session.copilot.ultima_analise      → ISO8601: timestamp da última análise

Confidence: 0.75 — inferência de co-pilot (não verificada pelo agente)
Source: "ai_inferred:copilot_emitter"
Visibility: "agents_only" — nunca exposto ao cliente
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.ai_gateway.copilot")

_CTX_SESSION_TTL = 14_400   # 4 hours — same as ContextStore default
_MAX_MSG_LEN     = 500       # truncate very long customer messages
_MAX_HISTORY     = 5         # max recent messages to include as context

_SYSTEM_PROMPT = """\
You are a real-time co-pilot assistant for a customer service agent.
Analyze the latest customer message and provide concise, actionable guidance.

Respond ONLY with a JSON object (no markdown, no explanation):
{
  "sugestao_resposta": "<one concise sentence the agent can say or adapt>",
  "flags_risco": ["<risk flag if detected, e.g. 'sentimento_negativo', 'intencao_cancelamento', 'sla_em_risco'>"],
  "acoes_recomendadas": ["<specific action, e.g. 'consultar_historico_crm', 'escalar_para_supervisor'>"]
}

Rules:
- sugestao_resposta: 1 sentence max, professional, in the same language as the customer message
- flags_risco: empty list if no risks detected; max 3 items
- acoes_recomendadas: empty list if no action needed; max 3 items
- Never include PII (CPF, credit card numbers) in any field
- Response must be valid JSON only
"""


def _build_user_prompt(
    customer_message: str,
    caller_nome: str | None,
    motivo_contato: str | None,
    sentimento_categoria: str | None,
) -> str:
    """Builds the compact user prompt for co-pilot analysis."""
    ctx_parts = []
    if caller_nome:
        ctx_parts.append(f"Customer name: {caller_nome}")
    if motivo_contato:
        ctx_parts.append(f"Contact reason: {motivo_contato}")
    if sentimento_categoria:
        ctx_parts.append(f"Current sentiment: {sentimento_categoria}")

    ctx_block = "\n".join(ctx_parts) if ctx_parts else "No prior context available."
    msg_truncated = customer_message[:_MAX_MSG_LEN]

    return f"""Session context:
{ctx_block}

Latest customer message:
"{msg_truncated}"

Provide co-pilot guidance for the agent."""


def _read_ctx_value(raw: str | bytes | None) -> Any:
    """Safely reads a ContextEntry value from Redis hash field."""
    if not raw:
        return None
    try:
        entry = json.loads(raw)
        return entry.get("value") if isinstance(entry, dict) else None
    except Exception:
        return None


async def _read_context(
    redis: Any,
    tenant_id: str,
    session_id: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Reads relevant ContextStore fields for co-pilot analysis.
    Returns (caller_nome, motivo_contato, sentimento_categoria).
    Never raises.
    """
    try:
        key = f"{tenant_id}:ctx:{session_id}"
        raw = await redis.hmget(
            key,
            "caller.nome",
            "caller.motivo_contato",
            "session.sentimento.categoria",
        )
        caller_nome          = _read_ctx_value(raw[0]) if raw else None
        motivo_contato       = _read_ctx_value(raw[1]) if raw else None
        sentimento_categoria = _read_ctx_value(raw[2]) if raw else None
        return (
            str(caller_nome)          if caller_nome          else None,
            str(motivo_contato)       if motivo_contato       else None,
            str(sentimento_categoria) if sentimento_categoria else None,
        )
    except Exception as exc:
        logger.debug("Failed to read ContextStore for copilot tenant=%s session=%s: %s", tenant_id, session_id, exc)
        return None, None, None


async def _write_copilot_context(
    redis: Any,
    tenant_id: str,
    session_id: str,
    sugestao_resposta: str,
    flags_risco: list[str],
    acoes_recomendadas: list[str],
) -> None:
    """
    Writes co-pilot suggestions to ContextStore.
    Fire-and-forget: never raises.
    """
    key = f"{tenant_id}:ctx:{session_id}"
    now = datetime.now(timezone.utc).isoformat()

    def _entry(value: Any) -> str:
        return json.dumps({
            "value":      value,
            "confidence": 0.75,
            "source":     "ai_inferred:copilot_emitter",
            "visibility": "agents_only",
            "updated_at": now,
        })

    try:
        await redis.hset(key, mapping={
            "session.copilot.sugestao_resposta":  _entry(sugestao_resposta),
            "session.copilot.flags_risco":        _entry(flags_risco),
            "session.copilot.acoes_recomendadas": _entry(acoes_recomendadas),
            "session.copilot.ultima_analise":     _entry(now),
        })
        await redis.expire(key, _CTX_SESSION_TTL)
    except Exception as exc:
        logger.warning(
            "Failed to write copilot context key=%s: %s", key, exc,
        )


async def _publish_copilot_updated(
    redis: Any,
    session_id: str,
) -> None:
    """
    Publishes copilot.updated event to the agent:events Redis channel
    so the Agent Assist WebSocket forwards it to the UI.
    Fire-and-forget: never raises.
    """
    try:
        event = json.dumps({"type": "copilot.updated", "session_id": session_id})
        await redis.publish(f"agent:events:{session_id}", event)
    except Exception as exc:
        logger.warning("Failed to publish copilot.updated session=%s: %s", session_id, exc)


def _parse_llm_response(text: str) -> tuple[str, list[str], list[str]]:
    """
    Parses LLM JSON response into (sugestao_resposta, flags_risco, acoes_recomendadas).
    Returns safe defaults on any parse failure.
    """
    try:
        # Strip markdown code fences if present
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(clean)
        sugestao    = str(data.get("sugestao_resposta", "")).strip()
        flags       = [str(f) for f in data.get("flags_risco", []) if f][:3]
        acoes       = [str(a) for a in data.get("acoes_recomendadas", []) if a][:3]
        return sugestao, flags, acoes
    except Exception as exc:
        logger.debug("Failed to parse copilot LLM response: %s | text=%r", exc, text[:200])
        return "", [], []


async def analyze_for_copilot(
    redis:            Any,
    provider:         Any,      # AnthropicProvider or compatible (has .call())
    session_id:       str,
    tenant_id:        str,
    customer_message: str,
    model_id:         str = "claude-haiku-4-5-20251001",
    max_tokens:       int = 256,
) -> None:
    """
    Analyzes a customer message in background, writes co-pilot suggestions to ContextStore,
    and publishes copilot.updated to the agent:events Redis channel.

    Fire-and-forget: never raises. All errors are logged at WARNING level or below.

    Args:
        redis:            aioredis client
        provider:         LLM provider instance with .call(messages, tools, model_id, max_tokens)
        session_id:       current contact session ID
        tenant_id:        tenant ID
        customer_message: raw text of the customer's latest message
        model_id:         model to use (defaults to haiku for cost/speed isolation)
        max_tokens:       max tokens for LLM response
    """
    if not customer_message or not customer_message.strip():
        return  # nothing to analyze

    if provider is None:
        logger.debug("copilot analyze skipped — no provider available session=%s", session_id)
        return

    try:
        # 1. Read ContextStore context
        caller_nome, motivo_contato, sentimento_categoria = await _read_context(
            redis, tenant_id, session_id,
        )

        # 2. Build messages for LLM
        user_prompt = _build_user_prompt(
            customer_message     = customer_message,
            caller_nome          = caller_nome,
            motivo_contato       = motivo_contato,
            sentimento_categoria = sentimento_categoria,
        )
        messages = [{"role": "user", "content": user_prompt}]

        # 3. Call LLM (fire; let provider handle retries/throttle internally)
        response = await provider.call(
            messages   = messages,
            tools      = [],
            model_id   = model_id,
            max_tokens = max_tokens,
            system     = _SYSTEM_PROMPT,
        )

        # 4. Parse response text
        text = ""
        if response and hasattr(response, "text"):
            text = response.text or ""
        elif response and isinstance(response, dict):
            text = response.get("text", "")

        sugestao, flags, acoes = _parse_llm_response(text)
        if not sugestao and not flags and not acoes:
            logger.debug("copilot: empty parse result session=%s text=%r", session_id, text[:100])
            return

        # 5. Write to ContextStore
        await _write_copilot_context(
            redis              = redis,
            tenant_id          = tenant_id,
            session_id         = session_id,
            sugestao_resposta  = sugestao,
            flags_risco        = flags,
            acoes_recomendadas = acoes,
        )

        # 6. Notify UI via pub/sub
        await _publish_copilot_updated(redis, session_id)

        logger.debug(
            "copilot: analysis complete session=%s flags=%s acoes=%s",
            session_id, flags, acoes,
        )

    except Exception as exc:
        logger.warning(
            "copilot analyze error session=%s tenant=%s: %s",
            session_id, tenant_id, exc,
        )
