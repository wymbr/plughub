"""
reason.py
Structured output for the Skill Flow reason step.
Spec: PlugHub v24.0 section 4.7 (reason step)

Receives prompt_id + input + output_schema.
Instructs the model to return only valid JSON matching the schema.
Validates the response before returning to the caller.
Uses LLMProvider interface — never calls the Anthropic SDK directly.
"""

from __future__ import annotations
import json
import re
import time
from typing import Any

from .models    import ReasonRequest, ReasonResponse, OutputFieldSchema
from .providers import LLMProvider


_SYSTEM_REASON = """You are a structured decision component.
Return ONLY a valid JSON object matching the provided schema.
Do not include any text before or after the JSON.
Do not use markdown code blocks.
Return the raw JSON object only."""


class ReasonEngine:
    def __init__(self, provider: LLMProvider, model_profiles: dict[str, Any]) -> None:
        self._provider       = provider
        self._model_profiles = model_profiles

    async def process(self, req: ReasonRequest) -> ReasonResponse:
        profile = self._model_profiles.get(req.model_profile)
        if profile is None:
            raise ValueError(f"unknown model_profile: {req.model_profile}")

        start = time.monotonic()

        # Build prompt with schema and input
        schema_desc = _format_schema(req.output_schema)
        user_prompt = (
            f"Expected schema:\n{schema_desc}\n\n"
            f"Input:\n{json.dumps(req.input, ensure_ascii=False, indent=2)}\n\n"
            f"Return the JSON matching the schema."
        )

        # On retry, add correction context
        if req.attempt > 0:
            user_prompt += (
                f"\n\nWarning: previous attempt returned invalid JSON. "
                f"Ensure the JSON exactly matches the schema above."
            )

        llm_resp = await self._provider.call(
            messages=[
                {"role": "system",    "content": _SYSTEM_REASON},
                {"role": "user",      "content": user_prompt},
            ],
            tools=None,
            model_id=profile.model_id,
            max_tokens=1024,
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        raw_text   = llm_resp.content

        # Parse JSON
        try:
            parsed = json.loads(_clean_json(raw_text))
        except json.JSONDecodeError as e:
            raise ValueError(f"AI Gateway reason: invalid JSON — {e}\nResponse: {raw_text[:200]}")

        # Validate against output_schema
        _validate_schema(parsed, req.output_schema)

        usage = llm_resp.raw.get("usage", {})

        return ReasonResponse(
            session_id    = req.session_id,
            result        = parsed,
            model_used    = llm_resp.model_used,
            input_tokens  = usage.get("input_tokens", 0),
            output_tokens = usage.get("output_tokens", 0),
            latency_ms    = latency_ms,
        )


def _format_schema(schema: dict[str, OutputFieldSchema]) -> str:
    lines = ["{"]
    for field_name, field_def in schema.items():
        required = "(required)" if field_def.required else "(optional)"
        type_desc = field_def.type
        if field_def.enum:
            type_desc += f" — values: {field_def.enum}"
        if field_def.minimum is not None:
            type_desc += f" — minimum: {field_def.minimum}"
        if field_def.maximum is not None:
            type_desc += f" — maximum: {field_def.maximum}"
        lines.append(f'  "{field_name}": {type_desc} {required}')
    lines.append("}")
    return "\n".join(lines)


def _clean_json(text: str) -> str:
    """Strips markdown code blocks if present."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _validate_schema(data: dict[str, Any], schema: dict[str, OutputFieldSchema]) -> None:
    """Validates the returned JSON against output_schema. Raises ValueError if invalid."""
    for field_name, field_def in schema.items():
        value = data.get(field_name)

        if value is None:
            if field_def.required:
                raise ValueError(f"required field missing: {field_name}")
            continue

        if field_def.type == "string" and not isinstance(value, str):
            raise ValueError(f"field {field_name}: expected string, got {type(value).__name__}")
        if field_def.type == "number" and not isinstance(value, (int, float)):
            raise ValueError(f"field {field_name}: expected number, got {type(value).__name__}")
        if field_def.type == "boolean" and not isinstance(value, bool):
            raise ValueError(f"field {field_name}: expected boolean, got {type(value).__name__}")

        if field_def.enum and str(value) not in field_def.enum:
            raise ValueError(f"field {field_name}: '{value}' not in enum {field_def.enum}")

        if field_def.type == "number" and isinstance(value, (int, float)):
            if field_def.minimum is not None and value < field_def.minimum:
                raise ValueError(f"field {field_name}: {value} < minimum {field_def.minimum}")
            if field_def.maximum is not None and value > field_def.maximum:
                raise ValueError(f"field {field_name}: {value} > maximum {field_def.maximum}")
