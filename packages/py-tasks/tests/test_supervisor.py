"""
test_supervisor.py — plughub_tasks.supervisionar: a morte de uma task de BOOT aparece.

Mudou de casa na RET-17 (era `analytics-api/tests/test_ret13_...`): as 4 cópias do
supervisor viraram uma função de biblioteca, e o teste segue o código. O que ficou
no analytics-api é o que só ELE pode afirmar — que as corrotinas dele deixam o
cancelamento chegar até aqui.

Três ramos, e o terceiro é o que separa alarme útil de alarme ignorado:
  1. a corrotina levanta  → ERROR nomeando a task
  2. a corrotina retorna  → WARNING (não há exceção nenhuma para inspecionar)
  3. a task é cancelada   → SILÊNCIO (shutdown normal)
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from plughub_tasks import supervisionar


async def _assentar() -> None:
    """Deixa o callback do loop rodar. Espera por TASK, não por contagem de yields."""
    for _ in range(3):
        await asyncio.sleep(0)


class TestOAlarmeFala:
    async def test_excecao_vira_error_nomeando_a_task(self, caplog):
        async def morre():
            raise RuntimeError("clickhouse fora do ar")

        with caplog.at_level(logging.ERROR):
            t = supervisionar("analytics-consumer", asyncio.create_task(morre()))
            with pytest.raises(RuntimeError):
                await t
            await _assentar()

        registros = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert registros, "task morreu e nada foi logado — é a cegueira que a RET-13 fecha"
        msg = registros[0].getMessage()
        assert "analytics-consumer" in msg, "o alarme precisa NOMEAR quem morreu"
        assert "clickhouse fora do ar" in msg, "e dizer a causa, senão não se trata nada"

    async def test_retorno_espontaneo_vira_warning(self, caplog):
        """Sem exceção nenhuma para inspecionar — o ramo que `t.exception()` não vê."""
        async def termina():
            return None

        with caplog.at_level(logging.WARNING):
            await supervisionar("performance-sync", asyncio.create_task(termina()))
            await _assentar()

        avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert avisos, (
            "consumidor que RETORNA some tão silenciosamente quanto o que levanta; "
            "um alarme que só olha t.exception() fica mudo exatamente aqui"
        )
        assert "performance-sync" in avisos[0].getMessage()


class TestOAlarmeCala:
    async def test_cancelamento_e_silencio(self, caplog):
        """Shutdown normal não é incidente — e alarme que grita à toa é ignorado."""
        async def dorme():
            await asyncio.sleep(3600)

        with caplog.at_level(logging.WARNING):
            t = supervisionar("analytics-consumer", asyncio.create_task(dorme()))
            await asyncio.sleep(0)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await _assentar()

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
            "o supervisor falou no shutdown — é assim que um alarme deixa de ser lido"
        )
