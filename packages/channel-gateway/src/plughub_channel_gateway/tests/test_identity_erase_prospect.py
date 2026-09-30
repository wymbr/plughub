"""AUD-06 — eliminação de um PROSPECT (cliente que só existe no Redis).

Achado na prova ao vivo: todo titular recém-provisionado mora só no Redis, e a eliminação
pelo cadastro durável o dava como `not_found`, deixando telefone e e-mail resolvendo para
ele até o TTL. O caminho do cadastro durável (com e sem veto) é provado ao vivo, contra o
Postgres de verdade; aqui fica o do Redis, com um dublê em memória."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.identity.index import IdentityIndex, _encode_index


class _Redis:
    def __init__(self, data: dict[str, str]):
        self.data = dict(data)

    async def get(self, k):
        return self.data.get(k)

    async def exists(self, k):
        return int(k in self.data)

    async def delete(self, *ks):
        n = 0
        for k in ks:
            k = k.decode() if isinstance(k, bytes) else k
            n += int(self.data.pop(k, None) is not None)
        return n

    async def scan_iter(self, match: str, count: int = 0):
        prefix = match.rstrip("*")
        for k in list(self.data):
            if k.startswith(prefix):
                yield k


def _idx(data):
    idx = IdentityIndex(redis=_Redis(data), salt="s", phone_region="BR", db_pool=MagicMock())
    idx.subject_record = AsyncMock(return_value=None)   # sem cadastro durável
    return idx


PROSPECT = {
    "t1:customer:prospect:cus_a": json.dumps({"customer_id": "cus_a", "created_at": "2026-09-30",
                                              "kinds": ["email", "phone"]}),
    "t1:pending_by_customer:cus_a": "{}",
    "t1:identity:phone:h1": _encode_index("cus_a", "claimed"),
    "t1:identity:email:h2": _encode_index("cus_a", "claimed"),
    "t1:identity:phone:h9": _encode_index("cus_outro", "claimed"),   # de OUTRA pessoa
    "t2:identity:phone:h1": _encode_index("cus_a", "claimed"),       # de outro TENANT
}


@pytest.mark.asyncio
async def test_prospect_erase_removes_its_keys_and_only_its_keys():
    idx = _idx(PROSPECT)
    out = await idx.erase_subject("t1", "cus_a")
    assert out["prospect"] is True and out["customer_ids"] == ["cus_a"]
    assert out["anchors_deleted"] == 2
    left = set(idx._redis.data)
    assert left == {"t1:identity:phone:h9", "t2:identity:phone:h1"}, left


@pytest.mark.asyncio
async def test_unknown_customer_is_not_found_and_touches_nothing():
    idx = _idx(PROSPECT)
    out = await idx.erase_subject("t1", "cus_nada")
    assert out["customer_ids"] == []
    assert set(idx._redis.data) == set(PROSPECT)


@pytest.mark.asyncio
async def test_prospect_record_feeds_the_dossier():
    idx = _idx(PROSPECT)
    rec = await idx.prospect_record("t1", "cus_a")
    assert rec["status"] == "prospect"
    assert [a["kind"] for a in rec["anchors"]] == ["email", "phone"]
    assert rec["attributes"] == {}, "prospect não tem atributo — logo não tem veto"
    assert await idx.prospect_record("t1", "cus_nada") is None
