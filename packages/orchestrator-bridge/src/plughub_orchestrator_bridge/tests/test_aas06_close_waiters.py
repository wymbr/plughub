"""
test_aas06_close_waiters.py — AAS-06 (2026-10-01): o fechamento do lado do cliente chega ao menu
ESTACIONADO.

PROPOSIÇÃO: `count_menu_close_waiters` conta todo `menu` voltado ao cliente que precisa do sinal
`session:closed:{sid}` — o que bloqueia (marca de BLPOP) E o que está estacionado (DUR-01) — e
nenhum outro. Antes, o estacionado contava zero: nada era empurrado, o acordar achava a caixa
vazia e estacionava de novo até o prazo do menu (medido no `CancelTask` do canal `a2a`).
"""
from __future__ import annotations

import json

from plughub_orchestrator_bridge import main as bridge_mod

SID, T = "sess-c", "tenant_demo"
ALL = json.dumps({"visibility": "all"})


class FakeRedis:
    def __init__(self, waiting: dict, parked=(), active=(), tenant=T) -> None:
        self.waiting, self.parked, self.active = waiting, set(parked), set(active)
        self.tenant = tenant

    async def hgetall(self, k):
        assert k == f"menu:waiting:{SID}"
        return dict(self.waiting)

    async def get(self, k):
        return json.dumps({"tenant_id": self.tenant}) if self.tenant and k == f"session:{SID}:meta" else None

    async def smembers(self, k):
        assert k == f"session:{SID}:parked_runs"
        return set(self.parked)

    async def exists(self, k):
        return int(any(k == f"{T}:session:{SID}:active_instance:{a}" for a in self.active))


async def n(**kw) -> int:
    return await bridge_mod.count_menu_close_waiters(FakeRedis(**kw), SID)


async def test_bloqueado_conta_controle():
    assert await n(waiting={"ia-1": ALL}, active={"ia-1"}) == 1


async def test_estacionado_conta_o_defeito():
    assert await n(waiting={"ia-1": ALL}, parked={"ia-1"}) == 1


async def test_saiu_do_blpop_e_nao_estacionou_nao_conta():
    # o sinal seria consumido por um agente de hook que nascesse depois
    assert await n(waiting={"ia-1": ALL}) == 0


async def test_hook_com_lista_de_participante_nunca_recebe_mesmo_estacionado():
    hook = json.dumps({"visibility": ["part_humano"]})
    assert await n(waiting={"wrap-1": hook}, parked={"wrap-1"}, active={"wrap-1"}) == 0


async def test_agente_de_fila_estacionado_fica_para_o_marcador_proprio():
    assert await n(waiting={"_default_": ALL}, parked={"_default_"}) == 0


async def test_um_bloqueado_e_um_estacionado():
    assert await n(waiting={"ia-1": ALL, "ia-2": ALL}, active={"ia-1"}, parked={"ia-2"}) == 2


async def test_sem_tenant_conta_toda_entrada_voltada_ao_cliente():
    assert await n(waiting={"ia-1": ALL, "ia-2": ALL}, tenant=None) == 2
