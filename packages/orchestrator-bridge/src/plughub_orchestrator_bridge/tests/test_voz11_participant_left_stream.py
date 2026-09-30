"""VOZ-11 (fatia a) — a saída da IA nativa entra no stream, com a identidade da entrada.

O gateway tira o atendente do conjunto de mídia no `participant_left` do stream, e a IA nativa só
saía pelo Kafka de analytics: o teto do cliente ficava mais permissivo que o devido."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from plughub_orchestrator_bridge.main import _write_participant_left_to_stream


def _redis(exists=True):
    r = AsyncMock()
    r.exists.return_value = 1 if exists else 0
    return r


@pytest.mark.asyncio
async def test_exit_carries_the_entry_identity_and_stays_with_agents():
    r = _redis()
    assert await _write_participant_left_to_stream(r, "s1", "native-ia-001", "native", "primary", "resolved")
    (key, fields), kw = r.xadd.call_args.args, r.xadd.call_args.kwargs
    assert key == "session:s1:stream" and fields["type"] == "participant_left"
    assert fields["author_id"] == "native-ia-001"
    assert fields["visibility"] == "agents_only", "o chat do cliente mostraria 'agente saiu'"
    payload = json.loads(fields["payload"])
    assert payload["instance_id"] == "native-ia-001" and payload["framework"] == "native"
    assert json.loads(fields["author"])["role"] == "primary" and fields["event_id"]
    assert kw.get("maxlen") == 500


@pytest.mark.asyncio
async def test_absent_stream_is_not_created():
    r = _redis(exists=False)
    assert await _write_participant_left_to_stream(r, "s1", "ia", "native", "primary", "x") is False
    r.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_no_identity_writes_nothing():
    r = _redis()
    assert await _write_participant_left_to_stream(r, "s1", "", "native", "primary", "x") is False
    r.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_failure_is_reported_not_raised(caplog):
    r = _redis()
    r.xadd.side_effect = ConnectionError("redis fora")
    with caplog.at_level("WARNING"):
        assert await _write_participant_left_to_stream(r, "s1", "ia", "native", "primary", "x") is False
    assert "NAO registrado" in caplog.text
