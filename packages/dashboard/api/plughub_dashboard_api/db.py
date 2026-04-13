"""
ClickHouse client factory.
Uses clickhouse-connect which supports both HTTP and native protocols.
We use HTTP (port 8123) for simplicity in the pilot.
"""

from __future__ import annotations

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from .config import get_settings

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        s = get_settings()
        _client = clickhouse_connect.get_client(
            host=s.clickhouse_host,
            port=s.clickhouse_port,
            database=s.clickhouse_database,
            username=s.clickhouse_user,
            password=s.clickhouse_password,
        )
    return _client
