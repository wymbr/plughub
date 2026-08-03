"""
test_quota_sync.py
O contrato pricing → routing é UMA STRING, e ninguém o guardava (2026-08-03).

POR QUE ISTO EXISTE. `quota_sync` é o **produtor** de `{t}:quota:capacity:ai_agent`, e o
`AdmissionController` do routing-engine é o **consumidor** — o único teto de sessão de IA
que sobrou depois da fatia 3 (`admission.py:240`, `kind:ai ≤ C_ai`). Os dois serviços
concordam apenas pelo formato do nome da chave. Se um dos lados mudar:

    a chave não existe → `get()` devolve None → "sem gate" → **fail open**

IA sem teto, sem erro, sem log — o modo de falha que este projeto persegue. O lado do
consumidor já está preso por `test_admission_licensing.py` (que escreve a chave literal);
este arquivo prende o do produtor, e com isso o par fecha.

A segunda armadilha é o **DEL**: capacidade 0 apaga a chave, e apagar a chave é
exatamente "sem gate". Isso é intencional (instalação sem pricing configurado não deve
ganhar um teto de 0, que barraria tudo), mas é uma decisão que precisa estar afirmada —
senão vira o conserto errado no dia em que alguém investigar "por que a IA não é
barrada".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..quota_sync import _QUOTA_KEY, _QUOTA_TYPE_KEY, sync_tenant

_T = "tenant_test"


def _redis():
    r = MagicMock()
    r.set    = AsyncMock()
    r.delete = AsyncMock()
    return r


def _capacity(total: int, by_type: list[dict]):
    return {"agent_capacity_total": total, "by_type": by_type}


class TestKeyContract:
    def test_key_formats_are_the_ones_admission_reads(self):
        """Literais fixados. Mudá-los aqui obriga a mudar `admission.py` junto.

        Não é redundância com o teste do routing: lá se afirma o que o CONSUMIDOR lê,
        aqui o que o PRODUTOR escreve. O defeito mora entre os dois, e um teste só de
        cada lado, isolado, continua passando enquanto o par diverge.
        """
        assert _QUOTA_TYPE_KEY == "{tenant_id}:quota:capacity:{resource_type}"
        assert _QUOTA_TYPE_KEY.format(tenant_id=_T, resource_type="ai_agent") == \
            f"{_T}:quota:capacity:ai_agent"
        assert _QUOTA_KEY.format(tenant_id=_T) == f"{_T}:quota:max_concurrent_sessions"


class TestSync:
    @pytest.mark.asyncio
    async def test_writes_total_and_per_type(self):
        r = _redis()
        with patch("plughub_pricing_api.quota_sync.pricing_db.get_capacity",
                   new=AsyncMock(return_value=_capacity(370, [
                       {"resource_type": "ai_agent",    "total": 360},
                       {"resource_type": "human_agent", "total": 10},
                   ]))):
            out = await sync_tenant(r, MagicMock(), _T)

        assert out == 370
        written = {c.args[0]: c.args[1] for c in r.set.call_args_list}
        assert written[f"{_T}:quota:capacity:ai_agent"]    == "360"
        assert written[f"{_T}:quota:capacity:human_agent"] == "10"
        assert written[f"{_T}:quota:max_concurrent_sessions"] == "370"

    @pytest.mark.asyncio
    async def test_zero_capacity_DELETES_which_means_NO_GATE(self):
        """Capacidade 0 apaga a chave — e chave ausente é ausência de teto, não teto 0.

        Decisão deliberada (instalação sem pricing não deve ser barrada), mas com
        consequência forte o suficiente para merecer asserção: um recurso de IA zerado
        por engano no cadastro não *restringe* a IA — **libera**.
        """
        r = _redis()
        with patch("plughub_pricing_api.quota_sync.pricing_db.get_capacity",
                   new=AsyncMock(return_value=_capacity(0, []))):
            await sync_tenant(r, MagicMock(), _T)

        deleted = {c.args[0] for c in r.delete.call_args_list}
        assert f"{_T}:quota:capacity:ai_agent" in deleted
        assert f"{_T}:quota:max_concurrent_sessions" in deleted
        r.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_type_missing_from_capacity_is_deleted_not_left_stale(self):
        """Tipo que sumiu do cadastro tem a chave APAGADA, não deixada com o valor velho.

        Sem isso, remover toda a capacidade de IA deixaria o teto anterior valendo — o
        pior dos dois mundos: um limite que não corresponde a contrato nenhum.
        """
        r = _redis()
        with patch("plughub_pricing_api.quota_sync.pricing_db.get_capacity",
                   new=AsyncMock(return_value=_capacity(10, [
                       {"resource_type": "human_agent", "total": 10},
                   ]))):
            await sync_tenant(r, MagicMock(), _T)

        deleted = {c.args[0] for c in r.delete.call_args_list}
        assert f"{_T}:quota:capacity:ai_agent" in deleted
        written = {c.args[0] for c in r.set.call_args_list}
        assert f"{_T}:quota:capacity:human_agent" in written   # controle positivo

    @pytest.mark.asyncio
    async def test_redis_failure_never_breaks_billing(self):
        """`sync_tenant` devolve None e não propaga — billing não quebra por quota."""
        r = _redis()
        r.set.side_effect = RuntimeError("redis down")
        with patch("plughub_pricing_api.quota_sync.pricing_db.get_capacity",
                   new=AsyncMock(return_value=_capacity(10, []))):
            assert await sync_tenant(r, MagicMock(), _T) is None

    @pytest.mark.asyncio
    async def test_no_redis_is_a_noop(self):
        with patch("plughub_pricing_api.quota_sync.pricing_db.get_capacity",
                   new=AsyncMock()) as cap:
            assert await sync_tenant(None, MagicMock(), _T) is None
        cap.assert_not_called()
