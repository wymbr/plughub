"""
insights.py
Query of active ConversationInsights and PendingDeliveries for the customer.
Spec: PlugHub v24.0 sections 3.4, 3.4a

At the start of each contact, the Routing Engine queries:
  - conversation_insights: insight.historico.* and insight.conversa.* items
  - pending_deliveries:    active outbound.* items for the customer

Redis keys (written by mcp-server-plughub / AI Gateway):
  {tenant_id}:insight:{conversation_id}:{item_id}  — active conversation insight
  {tenant_id}:insight:h:{customer_id}:{item_id}    — historical customer insight
  {tenant_id}:pending:{customer_id}:{item_id}      — active pending delivery

Result is stored in the Redis session for use by the AI Gateway and Supervisor:
  {tenant_id}:session:{conversation_id}:context    — JSON with insights + pending_deliveries
"""

from __future__ import annotations
import json
import logging

import redis.asyncio as aioredis

logger = logging.getLogger("plughub.routing.insights")

# Session context TTL (aligned with the maximum session duration)
SESSION_CONTEXT_TTL_S = 3_600  # 1 hora


def _insight_conversa_pattern(tenant_id: str, conversation_id: str) -> str:
    """Scan pattern for active conversation insights."""
    return f"{tenant_id}:insight:{conversation_id}:*"

def _insight_historico_pattern(tenant_id: str, customer_id: str) -> str:
    """Scan pattern for historical customer insights."""
    return f"{tenant_id}:insight:h:{customer_id}:*"

def _pending_pattern(tenant_id: str, customer_id: str) -> str:
    """Scan pattern for active pending deliveries."""
    return f"{tenant_id}:pending:{customer_id}:*"

def _session_context_key(tenant_id: str, conversation_id: str) -> str:
    """Key where the consolidated context is stored for the session."""
    return f"{tenant_id}:session:{conversation_id}:context"


async def fetch_session_context(
    tenant_id:       str,
    customer_id:     str,
    conversation_id: str,
    redis_client:    aioredis.Redis,
) -> dict:
    """
    Queries active ConversationInsights and PendingDeliveries for the customer
    and stores the consolidated context in the Redis session.

    Spec 3.4a: "At the start of each contact, query active ConversationInsights and
    PendingDeliveries for the customer and include in the Redis session."

    Returns:
        dict with:
          - conversation_insights: list of insight.* items
          - pending_deliveries:    list of outbound.* items
    """
    conversation_insights: list[dict] = []
    pending_deliveries:    list[dict] = []

    # ── 1. Active conversation insights (insight.conversa.*) ──
    async for key in redis_client.scan_iter(
        _insight_conversa_pattern(tenant_id, conversation_id)
    ):
        raw = await redis_client.get(key)
        if raw:
            try:
                item = json.loads(raw)
                conversation_insights.append(item)
            except Exception:
                pass

    # ── 2. Historical customer insights (insight.historico.*) ──
    async for key in redis_client.scan_iter(
        _insight_historico_pattern(tenant_id, customer_id)
    ):
        raw = await redis_client.get(key)
        if raw:
            try:
                item = json.loads(raw)
                conversation_insights.append(item)
            except Exception:
                pass

    # ── 3. Active Pending Deliveries (outbound.*) ──
    async for key in redis_client.scan_iter(
        _pending_pattern(tenant_id, customer_id)
    ):
        raw = await redis_client.get(key)
        if raw:
            try:
                item = json.loads(raw)
                pending_deliveries.append(item)
            except Exception:
                pass

    # Sort by priority (highest first)
    conversation_insights.sort(
        key=lambda x: x.get("priority", 50), reverse=True
    )
    pending_deliveries.sort(
        key=lambda x: x.get("priority", 50), reverse=True
    )

    context = {
        "customer_id":            customer_id,
        "conversation_id":        conversation_id,
        "conversation_insights":  conversation_insights,
        "pending_deliveries":     pending_deliveries,
    }

    # Persist to Redis session for downstream use (AI Gateway, Supervisor)
    ctx_key = _session_context_key(tenant_id, conversation_id)
    await redis_client.set(
        ctx_key,
        json.dumps(context),
        ex=SESSION_CONTEXT_TTL_S,
    )

    logger.debug(
        "Session context loaded: conversation=%s insights=%d pending=%d",
        conversation_id,
        len(conversation_insights),
        len(pending_deliveries),
    )

    return context
