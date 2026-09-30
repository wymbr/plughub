"""AUD-08 — entrada de mailing vencida deixa de guardar os contatos (o `expires_at`
era só filtro). A linha, o customer_id e as entregas ficam."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_mailing_api import retention_job as rj


def test_purge_clears_contacts_only_of_expired_entries():
    sql = " ".join(rj.PURGE_SQL.split())
    assert "contacts = '{}'::jsonb" in sql and "metadata = '{}'::jsonb" in sql
    assert "expires_at < now()" in sql and "expires_at IS NOT NULL" in sql
    assert "CASE WHEN status = 'active' THEN 'expired' ELSE status END" in sql, \
        "entrada descadastrada continua descadastrada"
    assert "customer_id" not in sql
    assert "(contacts <> '{}'::jsonb OR metadata <> '{}'::jsonb)" in sql


def test_expire_once_counts():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 7")
    assert asyncio.run(rj.expire_once(pool)) == 7


def test_supervise_says_when_the_task_dies(caplog):
    async def boom():
        raise RuntimeError("x")

    async def run():
        t = rj.supervise(asyncio.create_task(boom()))
        with pytest.raises(RuntimeError):
            await t
        await asyncio.sleep(0)

    asyncio.run(run())
    assert "MORREU" in caplog.text
