"""
Test fixtures for the Dashboard API.

All ClickHouse interactions are mocked — no real CH connection needed.
We patch `plughub_dashboard_api.db.get_client` to return a MagicMock
whose `.query()` returns configurable fake result sets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from plughub_dashboard_api.app import app


class FakeQueryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


@pytest.fixture()
def mock_ch():
    """Return a MagicMock ClickHouse client and patch get_client."""
    client = MagicMock()
    with patch("plughub_dashboard_api.queries.get_client", return_value=client):
        yield client


@pytest.fixture()
def api_client():
    return TestClient(app)


# ── Shared fake rows ───────────────────────────────────────────────────────────

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

POOL_ROW = ("retencao_humano", 3, 25, 7.5, 6.8, 8.2, NOW)

AGENT_SUMMARY_ROW = ("agent-001", "human", "retencao_humano", 10, 7.8)

AGENT_TREND_ROW = ("agent-001", "2024-05-31", 7.5, 3)

AGENT_SECTION_ROW = (
    "agent-001",
    "postura_atendimento",
    "base_score",
    8.0,
    10,
    None,
    None,
)

EVAL_HEADER_ROW = (
    "eval-uuid-001",
    "contact-uuid-001",
    "agent-001",
    "retencao_humano",
    "eval_retencao_humano_v1",
    NOW,
    7.5,
)

EVAL_ITEM_ROW = (
    "eval-uuid-001",
    "postura_atendimento",
    "abertura",
    "saudacao_adequada",
    9,
    3,
    "Saudação correta",
)
