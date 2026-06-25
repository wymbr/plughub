"""
ch_client.py
Minimal ClickHouse reader over the HTTP interface (no clickhouse_connect dep).

Runs a SQL query with `FORMAT JSONEachRow` and returns a list of dicts. Read-only:
the exporter only reads the internal history (sessions/segments/messages).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("plughub.quality_export.ch")


class ClickHouseClient:
    def __init__(self, url: str, database: str, user: str, password: str) -> None:
        self._url = url.rstrip("/")
        self._db = database
        self._headers = {
            "X-ClickHouse-User": user,
            "X-ClickHouse-Key": password,
        }

    async def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a read query; return rows as dicts. Parameters use ClickHouse
        server-side binding ({name:Type}) passed as param_<name> query args."""
        q = f"{sql.rstrip().rstrip(';')} FORMAT JSONEachRow"
        query_params: dict[str, str] = {"database": self._db}
        for k, v in (params or {}).items():
            query_params[f"param_{k}"] = str(v)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._url + "/",
                params=query_params,
                content=q.encode("utf-8"),
                headers=self._headers,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"ClickHouse query failed ({resp.status_code}): {resp.text[:300]}")
        rows: list[dict[str, Any]] = []
        for line in resp.text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
