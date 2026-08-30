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
import logging
import re
import time
from typing import Any
import asyncio

from .account_selector import AccountSelector
from .models    import ReasonRequest, ReasonResponse, OutputFieldSchema
from .providers import LLMProvider
from .usage_emitter import schedule_llm_tokens

logger = logging.getLogger("plughub.ai_gateway.reason")


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
        providers: dict[str, LLMProvider] | None = None,
        account_selector: AccountSelector | None = None,
        kafka_producer: Any | None = None,
    ) -> None:
        # T1 — produtor de `usage.events`. Opcional: sem Kafka o gateway segue
        # respondendo, e a ausência é LOGADA pelo emissor (o gasto ocorreu; o que
        # se perde é o registro). Nunca silencioso.
        self._kafka_producer = kafka_producer
        # `provider` stays the fallback/default (legacy "anthropic" alias) — used
        # when no AccountSelector is configured, or as last resort.
        self._provider       = provider
        self._model_profiles = model_profiles
        # Teto de tokens da saída estruturada. 1024 (antigo, hardcoded) truncava a
        # rubrica de avaliação (N critérios × observações) → JSON inválido. É teto:
        # respostas curtas do realtime não mudam.
        self._max_tokens     = max_tokens
        # LLM Accounts — full provider registry + selector, mirrors InferenceEngine.
        # None when multi-account rotation isn't configured (single-key dev/test) —
        # every call falls back to `self._provider` unconditionally.
        self._providers        = providers or {}
        self._account_selector = account_selector

    async def _select_provider(
        self,
        profile_provider:      str,
        preferred_config_ids:  list[str],
    ) -> tuple[LLMProvider, str | None]:
        """
        Picks the LLMProvider instance for this call. Mirrors step 1 of
        InferenceEngine._call_with_fallback() (account selection), without the
        throttle-and-retry state machine — reason steps are structured-output
        calls, not conversational turns; a failed call surfaces to the skill
        flow's own catch/retry step instead.

        Returns (provider, provider_key). provider_key is None when the legacy
        single-provider fallback was used (no AccountSelector configured, or
        selection failed) — caller uses it only for `record_usage`.
        """
        if self._account_selector is not None:
            provider_key = await self._account_selector.pick(
                profile_provider,
                preferred_config_ids=preferred_config_ids or [],
            )
            if provider_key is not None:
                provider = self._providers.get(provider_key)
                if provider is not None:
                    return provider, provider_key
                logger.error(
                    "ReasonEngine: AccountSelector picked %s but no matching provider instance",
                    provider_key,
                )
        # No selector, selection failed, or no matching instance — legacy alias.
        return self._provider, None

    def _emit_usage(
        self, req: ReasonRequest, resp: ReasonResponse,
        provider_key: str | None = None,
    ) -> None:
        """
        T1 — publica o consumo deste caminho em `usage.events`.

        UM ponto de emissão para os dois ramos (flat e tool-use), no fim do
        `process`, e não dentro de cada `provider.call`: hoje é uma chamada por
        requisição, mas se um dia houver retry interno o ramo novo passaria a
        emitir sozinho — e subcontar é o modo de falha caro aqui.

        `ensure_future` porque metering NUNCA entra no caminho crítico da resposta
        ao skill flow. O prazo do `send` vive dentro de `emit_llm_tokens`, para
        que nenhum chamador possa esquecê-lo.
        """
        if self._kafka_producer is None:
            return
        # T2/D2 — a conta EFETIVA, resolvida do `provider_key` que atendeu. Nunca
        # de `req.preferred_config_ids`: aquilo é PREFERÊNCIA, e sob throttle ou
        # fallback cross-provider a conta que responde é outra. Derivar da config
        # acertaria em dia normal e erraria no dia do incidente — que é quando o
        # relatório é lido.
        config_id = (self._account_selector.config_id_for(provider_key)
                     if self._account_selector is not None else None)
        key_id = provider_key.split(":", 1)[1] if provider_key and ":" in provider_key else None
        schedule_llm_tokens(
            producer=      self._kafka_producer,
            tenant_id=     req.tenant_id,
            session_id=    req.session_id,
            model_id=      resp.model_used,
            agent_type_id= None,   # `req.agent_id` é auditoria, não tipo de agente
            input_tokens=  resp.input_tokens,
            output_tokens= resp.output_tokens,
            source=        "reason",
            segment_id=        req.segment_id or None,
            account_config_id= config_id,
            account_key_id=    key_id,
            model_profile=     req.model_profile,
        )

    async def process(self, req: ReasonRequest) -> ReasonResponse:
        profile = self._model_profiles.get(req.model_profile)
        if profile is None:
            raise ValueError(f"unknown model_profile: {req.model_profile}")

        start = time.monotonic()

        # T7b — quando há JSON Schema (montado upstream do form), usa tool-use nativo.
        if req.json_schema is not None:
            resp, tool_key = await self._process_tool_use(req, profile, start)
            self._emit_usage(req, resp, tool_key)
            return resp

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

        provider, provider_key = await self._select_provider(
            profile.provider, req.preferred_config_ids,
        )
        llm_resp = await provider.call(
            messages=[
                {"role": "system",    "content": _SYSTEM_REASON},
                {"role": "user",      "content": user_prompt},
            ],
            tools=None,
            model_id=profile.model_id,
            max_tokens=self._max_tokens,
        )
        usage = llm_resp.raw.get("usage", {})
        if provider_key is not None and self._account_selector is not None:
            await self._account_selector.record_usage(
                provider_key,
                tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
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

        resp = ReasonResponse(
            session_id    = req.session_id,
            result        = parsed,
            model_used    = llm_resp.model_used,
            input_tokens  = usage.get("input_tokens", 0),
            output_tokens = usage.get("output_tokens", 0),
            latency_ms    = latency_ms,
        )
        self._emit_usage(req, resp, provider_key)
        return resp


    async def _process_tool_use(self, req: ReasonRequest, profile: Any, start: float) -> tuple[ReasonResponse, str | None]:
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

        provider, provider_key = await self._select_provider(
            profile.provider, req.preferred_config_ids,
        )

        parsed: dict[str, Any] | None = None
        last_errors: list[str] | None = None
        correction = ""
        llm_resp = None
        attempts = 0
        for _ in range(_TOOL_USE_MAX_ATTEMPTS):
            attempts += 1
            llm_resp = await provider.call(
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
        if provider_key is not None and self._account_selector is not None:
            await self._account_selector.record_usage(
                provider_key,
                tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            )
        # Devolve também o `provider_key`: é a CONTA que atendeu, e a emissão de
        # consumo acontece em `process`. Guardá-la em `self` seria corrida entre
        # requisições concorrentes — o engine é compartilhado.
        return ReasonResponse(
            session_id    = req.session_id,
            result        = parsed,
            model_used    = llm_resp.model_used if llm_resp else profile.model_id,
            input_tokens  = usage.get("input_tokens", 0),
            output_tokens = usage.get("output_tokens", 0),
            latency_ms    = latency_ms,
        ), provider_key


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
