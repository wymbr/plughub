"""
gateway.py
Legacy AI Gateway reasoning loop — used by POST /v1/turn.
Spec: PlugHub v24.0 section 2.2a

Uses the LLMProvider interface — never calls the Anthropic SDK directly.
New integrations should use POST /inference via InferenceEngine instead.
"""

from __future__ import annotations
import time
from typing import Any

from .models    import TurnRequest, TurnResponse, ExtractedParams
from .context   import extract_context_from_response
from .providers import LLMProvider


class AIGateway:
    def __init__(self, provider: LLMProvider, model_profiles: dict[str, Any]) -> None:
        self._provider       = provider
        self._model_profiles = model_profiles

    async def process_turn(self, req: TurnRequest) -> TurnResponse:
        """
        Processes one agent turn — full reasoning loop.
        Extracts session parameters from the response.
        """
        profile = self._model_profiles.get(req.model_profile)
        if profile is None:
            raise ValueError(f"unknown model_profile: {req.model_profile}")

        start = time.monotonic()

        # Build messages — system prompt prepended as a system role message
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": req.system_prompt},
        ]
        for m in req.messages:
            messages.append({
                "role":    m.role if m.role != "agent" else "assistant",
                "content": m.content,
            })

        # Build tools list in MCP-neutral format
        tools: list[dict[str, Any]] | None = None
        if req.tools:
            tools = [
                {
                    "name":         t.get("name", ""),
                    "description":  t.get("description", ""),
                    "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
                }
                for t in req.tools
            ]

        llm_resp = await self._provider.call(
            messages=messages,
            tools=tools,
            model_id=profile.model_id,
            max_tokens=req.max_tokens,
        )

        latency_ms = int((time.monotonic() - start) * 1000)

        # Extract intra-turn session parameters from the neutral content string
        user_message = ""
        for m in reversed(req.messages):
            if m.role == "customer":
                user_message = m.content
                break

        ctx = extract_context_from_response(
            user_message=user_message,
            assistant_response=llm_resp.content,
            call_type=req.call_type,
        )

        usage = llm_resp.raw.get("usage", {})

        return TurnResponse(
            session_id    = req.session_id,
            agent_id      = req.agent_id,
            content       = llm_resp.content,
            tool_calls    = llm_resp.tool_calls,
            stop_reason   = llm_resp.stop_reason,
            extracted_params = ExtractedParams(
                intent          = ctx.intent,
                confidence      = ctx.confidence,
                sentiment_score = ctx.sentiment_score,
                flags           = ctx.flags,
            ),
            model_used    = llm_resp.model_used,
            input_tokens  = usage.get("input_tokens", 0),
            output_tokens = usage.get("output_tokens", 0),
            latency_ms    = latency_ms,
        )
