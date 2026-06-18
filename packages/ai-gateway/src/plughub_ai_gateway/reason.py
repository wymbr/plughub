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

# T7b — caminho tool-use nativo (structured output). O modelo é OBRIGADO a chamar a
# tool, cujo input_schema é o JSON Schema montado upstream a partir do form.
_SYSTEM_TOOL = """You are a structured evaluation component.
You MUST call the provided tool exactly once, populating every field per its schema.
Do not answer in free text — only the tool call is read."""

_TOOL_NAME = "submit_result"
_TOOL_USE_MAX_ATTEMPTS = 3  # validação recursiva + retry como rede (§5.4)


class ReasonEngine:
    def __init__(
        self,
        provider: LLMProvider,
        model_profiles: dict[str, Any],
        max_tokens: int = 4096,
    ) -> None:
        self._provider       = provider
        self._model_profiles = model_profiles
        # Teto de tokens da saída estruturada. 1024 (antigo, hardcoded) truncava a
        # rubrica de avaliação (N critérios × observações) → JSON inválido. É teto:
        # respostas curtas do realtime não mudam.
        self._max_tokens     = max_tokens

    async def process(self, req: ReasonRequest) -> ReasonResponse:
        profile = self._model_profiles.get(req.model_profile)
        if profile is None:
            raise ValueError(f"unknown model_profile: {req.model_profile}")

        start = time.monotonic()

        # T7b — quando há JSON Schema (montado upstream do form), usa tool-use nativo.
        if req.json_schema is not None:
            return await self._process_tool_use(req, profile, start)

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
            max_tokens=self._max_tokens,
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


    async def _process_tool_use(self, req: ReasonRequest, profile: Any, start: float) -> ReasonResponse:
        """T7b — structured output via tool-use nativo. O JSON Schema (req.json_schema)
        é o input_schema de uma única tool forçada; o provedor garante o shape por
        construção. Validação recursiva + retry como rede."""
        tool = {
            "name":         _TOOL_NAME,
            "description":  "Return the structured result. Call this tool exactly once "
                            "with every field populated per its schema.",
            "input_schema": req.json_schema,
        }
        base_user = (
            f"Input:\n{json.dumps(req.input, ensure_ascii=False, indent=2)}\n\n"
            f"Call `{_TOOL_NAME}` with the structured result."
        )

        parsed: dict[str, Any] | None = None
        last_errors: list[str] | None = None
        correction = ""
        llm_resp = None
        attempts = 0
        for _ in range(_TOOL_USE_MAX_ATTEMPTS):
            attempts += 1
            llm_resp = await self._provider.call(
                messages=[
                    {"role": "system", "content": _SYSTEM_TOOL},
                    {"role": "user",   "content": base_user + correction},
                ],
                tools=[tool],
                model_id=profile.model_id,
                max_tokens=self._max_tokens,
                force_tool=_TOOL_NAME,
            )
            if llm_resp.tool_calls:
                candidate = llm_resp.tool_calls[0].get("input") or {}
                errors = _validate_json_schema(candidate, req.json_schema)
                if not errors:
                    parsed = candidate
                    break
                last_errors = errors
                correction = (
                    f"\n\nThe previous result was invalid: {errors[:8]}. "
                    f"Fix and call `{_TOOL_NAME}` again."
                )
            else:
                last_errors = ["model did not call the tool"]
                correction = f"\n\nYou MUST call the `{_TOOL_NAME}` tool."

        if parsed is None:
            raise ValueError(
                f"AI Gateway reason (tool-use): failed after {attempts} attempts — {last_errors}"
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        usage = (llm_resp.raw or {}).get("usage", {}) if llm_resp else {}
        return ReasonResponse(
            session_id    = req.session_id,
            result        = parsed,
            model_used    = llm_resp.model_used if llm_resp else profile.model_id,
            input_tokens  = usage.get("input_tokens", 0),
            output_tokens = usage.get("output_tokens", 0),
            latency_ms    = latency_ms,
        )


def _validate_json_schema(data: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validador recursivo lite de JSON Schema (rede de segurança do tool-use). Suporta
    object/array/string/number/integer/boolean, properties/required/items/enum/min/max
    e nullable (via `nullable: true` ou `type: [..., "null"]`). Retorna lista de erros."""
    t = schema.get("type")
    # type como lista (ex.: ["number","null"]) → nullable + tipo base
    nullable = bool(schema.get("nullable"))
    if isinstance(t, list):
        if "null" in t:
            nullable = True
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else None

    if data is None:
        return [] if nullable else [f"{path}: null not allowed"]

    errs: list[str] = []
    if t == "object":
        if not isinstance(data, dict):
            return [f"{path}: expected object"]
        props = schema.get("properties", {}) or {}
        # `required` = chave PRESENTE (pode ser null se o tipo permitir; nullidade é
        # checada na validação do valor abaixo).
        for r in schema.get("required", []) or []:
            if r not in data:
                errs.append(f"{path}.{r}: required")
        for k, sub in props.items():
            if k in data:
                errs += _validate_json_schema(data[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(data, list):
            return [f"{path}: expected array"]
        items = schema.get("items")
        if isinstance(items, dict):
            for i, el in enumerate(data):
                errs += _validate_json_schema(el, items, f"{path}[{i}]")
    elif t in ("number", "integer"):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            return [f"{path}: expected number"]
        if "minimum" in schema and data < schema["minimum"]:
            errs.append(f"{path}: {data} < minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errs.append(f"{path}: {data} > maximum {schema['maximum']}")
    elif t == "string":
        if not isinstance(data, str):
            return [f"{path}: expected string"]
        if schema.get("enum") and data not in schema["enum"]:
            errs.append(f"{path}: '{data}' not in enum {schema['enum']}")
    elif t == "boolean":
        if not isinstance(data, bool):
            return [f"{path}: expected boolean"]
    return errs


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
