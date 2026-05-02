"""
test_copilot_emitter.py
Unit tests for AI Gateway Co-pilot Phase 2 emitter.

Strategy:
  - All async functions are fire-and-forget; tested by inspecting mock calls.
  - Redis is fully mocked (hmget, hset, expire, publish are AsyncMocks).
  - LLM provider is mocked — we control what .call() returns.
  - Error paths: redis raises, provider raises, empty response, bad JSON.
  - _parse_llm_response: raw JSON, markdown fences, empty, truncation, bad JSON.
  - _build_user_prompt: context fields present vs absent.
  - _read_ctx_value: valid entry, missing value key, invalid JSON, None/empty.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..copilot_emitter import (
    _build_user_prompt,
    _parse_llm_response,
    _read_ctx_value,
    _write_copilot_context,
    _publish_copilot_updated,
    _read_context,
    analyze_for_copilot,
)

TENANT  = "tenant_demo"
SESSION = "sess-copilot-001"


# ── helpers ───────────────────────────────────────────────────────────────────

def make_redis(hmget_vals: list | None = None) -> MagicMock:
    r = MagicMock()
    r.hmget  = AsyncMock(return_value=hmget_vals or [None, None, None])
    r.hset   = AsyncMock(return_value=None)
    r.expire = AsyncMock(return_value=None)
    r.publish = AsyncMock(return_value=1)
    return r


def make_provider(response_text: str = "{}") -> MagicMock:
    p = MagicMock()
    resp = MagicMock()
    resp.text = response_text
    p.call = AsyncMock(return_value=resp)
    return p


def ctx_entry(value) -> str:
    return json.dumps({
        "value":      value,
        "confidence": 0.9,
        "source":     "mcp_call:crm",
        "visibility": "agents_only",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })


# ── _read_ctx_value ───────────────────────────────────────────────────────────

class TestReadCtxValue:
    def test_returns_value_from_valid_entry(self):
        raw = ctx_entry("João")
        assert _read_ctx_value(raw) == "João"

    def test_returns_none_for_none_input(self):
        assert _read_ctx_value(None) is None

    def test_returns_none_for_empty_string(self):
        assert _read_ctx_value("") is None

    def test_returns_none_for_invalid_json(self):
        assert _read_ctx_value("not-json") is None

    def test_returns_none_when_value_key_missing(self):
        raw = json.dumps({"confidence": 0.9})
        assert _read_ctx_value(raw) is None


# ── _build_user_prompt ────────────────────────────────────────────────────────

class TestBuildUserPrompt:
    def test_includes_all_context(self):
        prompt = _build_user_prompt("quero cancelar", "Ana", "cancelamento", "frustrated")
        assert "Ana" in prompt
        assert "cancelamento" in prompt
        assert "frustrated" in prompt
        assert "quero cancelar" in prompt

    def test_no_context_shows_placeholder(self):
        prompt = _build_user_prompt("oi", None, None, None)
        assert "No prior context available" in prompt
        assert "oi" in prompt

    def test_truncates_long_message(self):
        long_msg = "x" * 600
        prompt = _build_user_prompt(long_msg, None, None, None)
        # message should be truncated to 500 chars
        assert "x" * 500 in prompt
        assert "x" * 501 not in prompt


# ── _parse_llm_response ───────────────────────────────────────────────────────

class TestParseLlmResponse:
    def test_parses_clean_json(self):
        text = json.dumps({
            "sugestao_resposta": "Posso ajudar com isso.",
            "flags_risco": ["sentimento_negativo"],
            "acoes_recomendadas": ["consultar_historico_crm"],
        })
        s, f, a = _parse_llm_response(text)
        assert s == "Posso ajudar com isso."
        assert f == ["sentimento_negativo"]
        assert a == ["consultar_historico_crm"]

    def test_strips_markdown_fences(self):
        text = "```json\n{\"sugestao_resposta\": \"ok\", \"flags_risco\": [], \"acoes_recomendadas\": []}\n```"
        s, f, a = _parse_llm_response(text)
        assert s == "ok"
        assert f == []
        assert a == []

    def test_returns_defaults_on_invalid_json(self):
        s, f, a = _parse_llm_response("this is not json at all")
        assert s == ""
        assert f == []
        assert a == []

    def test_caps_flags_at_3(self):
        text = json.dumps({
            "sugestao_resposta": "",
            "flags_risco": ["a", "b", "c", "d", "e"],
            "acoes_recomendadas": [],
        })
        _, f, _ = _parse_llm_response(text)
        assert len(f) == 3

    def test_caps_acoes_at_3(self):
        text = json.dumps({
            "sugestao_resposta": "",
            "flags_risco": [],
            "acoes_recomendadas": ["x", "y", "z", "w"],
        })
        _, _, a = _parse_llm_response(text)
        assert len(a) == 3


# ── _write_copilot_context ────────────────────────────────────────────────────

class TestWriteCopilotContext:
    @pytest.mark.asyncio
    async def test_writes_four_fields_to_redis(self):
        redis = make_redis()
        await _write_copilot_context(
            redis, TENANT, SESSION,
            sugestao_resposta  = "Vou verificar.",
            flags_risco        = ["sentimento_negativo"],
            acoes_recomendadas = ["consultar_crm"],
        )
        redis.hset.assert_called_once()
        kwargs = redis.hset.call_args
        mapping = kwargs.kwargs.get("mapping") or kwargs.args[1]
        assert "session.copilot.sugestao_resposta" in mapping
        assert "session.copilot.flags_risco" in mapping
        assert "session.copilot.acoes_recomendadas" in mapping
        assert "session.copilot.ultima_analise" in mapping

    @pytest.mark.asyncio
    async def test_sets_correct_context_key(self):
        redis = make_redis()
        await _write_copilot_context(redis, TENANT, SESSION, "resp", [], [])
        call_args = redis.hset.call_args
        key = call_args.args[0] if call_args.args else call_args.kwargs.get("name")
        assert key == f"{TENANT}:ctx:{SESSION}"

    @pytest.mark.asyncio
    async def test_calls_expire(self):
        redis = make_redis()
        await _write_copilot_context(redis, TENANT, SESSION, "resp", [], [])
        redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_raise_on_redis_error(self):
        redis = make_redis()
        redis.hset = AsyncMock(side_effect=RuntimeError("Redis down"))
        # Must not raise
        await _write_copilot_context(redis, TENANT, SESSION, "resp", [], [])


# ── _publish_copilot_updated ──────────────────────────────────────────────────

class TestPublishCopilotUpdated:
    @pytest.mark.asyncio
    async def test_publishes_to_correct_channel(self):
        redis = make_redis()
        await _publish_copilot_updated(redis, SESSION)
        redis.publish.assert_called_once()
        channel = redis.publish.call_args.args[0]
        assert channel == f"agent:events:{SESSION}"

    @pytest.mark.asyncio
    async def test_payload_contains_type_and_session_id(self):
        redis = make_redis()
        await _publish_copilot_updated(redis, SESSION)
        payload_str = redis.publish.call_args.args[1]
        payload = json.loads(payload_str)
        assert payload["type"] == "copilot.updated"
        assert payload["session_id"] == SESSION

    @pytest.mark.asyncio
    async def test_does_not_raise_on_publish_error(self):
        redis = make_redis()
        redis.publish = AsyncMock(side_effect=RuntimeError("pub/sub down"))
        await _publish_copilot_updated(redis, SESSION)


# ── analyze_for_copilot — happy path + error paths ───────────────────────────

class TestAnalyzeForCopilot:
    @pytest.mark.asyncio
    async def test_calls_provider_with_llm_call(self):
        redis    = make_redis()
        provider = make_provider(json.dumps({
            "sugestao_resposta": "Vou resolver isso agora.",
            "flags_risco": ["sentimento_negativo"],
            "acoes_recomendadas": ["verificar_conta"],
        }))
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "Estou muito frustrado!")
        provider.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_writes_context_and_publishes_on_success(self):
        redis    = make_redis()
        provider = make_provider(json.dumps({
            "sugestao_resposta": "Entendo.",
            "flags_risco": [],
            "acoes_recomendadas": [],
        }))
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "Olá!")
        redis.hset.assert_called_once()
        redis.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_empty_message(self):
        redis    = make_redis()
        provider = make_provider()
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "")
        provider.call.assert_not_called()
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_whitespace_only_message(self):
        redis    = make_redis()
        provider = make_provider()
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "   ")
        provider.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_provider_is_none(self):
        redis = make_redis()
        await analyze_for_copilot(redis, None, SESSION, TENANT, "oi")
        redis.hset.assert_not_called()
        redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_raise_when_provider_raises(self):
        redis    = make_redis()
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        # Must not raise
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "hello")

    @pytest.mark.asyncio
    async def test_does_not_write_on_empty_parse_result(self):
        """If LLM returns all-empty fields, skip writing to avoid overwriting good state."""
        redis    = make_redis()
        provider = make_provider(json.dumps({
            "sugestao_resposta": "",
            "flags_risco": [],
            "acoes_recomendadas": [],
        }))
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "some text")
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_reads_context_store_before_llm_call(self):
        redis    = make_redis([
            ctx_entry("Maria"),
            ctx_entry("cancelamento"),
            ctx_entry("frustrated"),
        ])
        provider = make_provider(json.dumps({
            "sugestao_resposta": "Entendo.",
            "flags_risco": [],
            "acoes_recomendadas": [],
        }))
        await analyze_for_copilot(redis, provider, SESSION, TENANT, "quero cancelar")
        redis.hmget.assert_called_once()
        # Context key format
        key = redis.hmget.call_args.args[0]
        assert key == f"{TENANT}:ctx:{SESSION}"
