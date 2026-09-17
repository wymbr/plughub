# -*- coding: utf-8 -*-
"""Quem espera num `receive` e desbloqueado quando o cliente desliga? (VOZ-23)

MEDIDO em 2026-09-17, no pool de calibracao da verificacao de fala: o cliente sintetico desligava, o
contato fechava — e o agente seguia parado no BLPOP do `receive` ate o `timeout_s` do step (300 s),
com a INSTANCIA presa. A verificacao seguinte, no mesmo pool, nao era atendida
(`call_not_answered`). O empurrao de `session:closed` contava so o HASH `menu:waiting`; quem espera
num `receive` se registra noutro HASH (`receive:waiting:{sid}`) e nao era contado por ninguem.

Vale para qualquer fluxo com `receive`, nao so para a verificacao — e o modo de falha e mudo: nada
fica vermelho, a instancia so parece ocupada.
"""
import pytest

from plughub_orchestrator_bridge.main import receive_waiters

SID = "sess-1"


class _Redis:
    def __init__(self, valor=0, explode=False):
        self.valor, self.explode, self.chaves = valor, explode, []

    async def hlen(self, key):
        self.chaves.append(key)
        if self.explode:
            raise RuntimeError("redis fora")
        return self.valor


@pytest.mark.asyncio
async def test_conta_os_que_esperam_e_le_a_chave_do_receive():
    r = _Redis(valor=2)
    assert await receive_waiters(r, SID) == 2
    assert r.chaves == [f"receive:waiting:{SID}"]


@pytest.mark.asyncio
async def test_ninguem_esperando_e_zero():
    assert await receive_waiters(_Redis(valor=0), SID) == 0


@pytest.mark.asyncio
async def test_falha_de_leitura_conta_zero_e_diz(caplog):
    """Contar A MAIS empurraria um `session:closed` que outro agente consumiria por engano; contar a
    MENOS mantem o comportamento antigo, que e o conhecido. O log nomeia a degradacao."""
    with caplog.at_level("WARNING"):
        assert await receive_waiters(_Redis(explode=True), SID) == 0
    assert "Could not count receive:waiting" in caplog.text
