"""
test_ret13_background_task_supervision.py — RET-13/17: o cancel chega ao supervisor.

⚠️ Os três ramos do alarme mudaram de casa na RET-17, com o supervisor: agora são
`packages/py-tasks/tests/test_supervisor.py`. O que sobra aqui é o que só o
analytics-api pode afirmar — que as corrotinas DELE deixam o cancelamento chegar
até o supervisor, e que o retorno por sinal não vira morte espontânea.

⚠️ O defeito de PRODUTO que motivou estes casos: `_run_consumer_safe` e
`run_performance_job_loop` faziam `break` no `CancelledError`, então a task
terminava COM SUCESSO ao ser cancelada e cairia no ramo 2 — o alarme gritaria em
todo shutdown normal. Alarme que grita quando nada há ensina a ser ignorado, que é
pior que alarme nenhum.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import plughub_analytics_api.main as main_mod
from plughub_analytics_api.performance_job import run_performance_job_loop



async def _assentar() -> None:
    """Deixa o callback do loop rodar. Espera por TASK, não por contagem de yields."""
    for _ in range(3):
        await asyncio.sleep(0)


class TestOCancelChegaAoSupervisor:
    """⚠️ O produto tem de PROPAGAR o cancel, senão o ramo 3 vira o ramo 2."""

    async def test_consumer_safe_relevanta_o_cancel(self, monkeypatch):
        async def nunca_termina(*_a, **_k):
            await asyncio.sleep(3600)

        monkeypatch.setattr(main_mod, "run_consumer", nunca_termina)
        t = asyncio.create_task(main_mod._run_consumer_safe(object(), None))
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert t.cancelled(), (
            "engolir o CancelledError faz a task terminar COM SUCESSO ao ser "
            "cancelada, e o supervisor a reporta como morte espontânea no shutdown"
        )

    async def test_performance_loop_relevanta_o_cancel(self, monkeypatch):
        async def nunca_termina(*_a, **_k):
            await asyncio.sleep(3600)

        monkeypatch.setattr(
            "plughub_analytics_api.performance_job.run_performance_sync", nunca_termina
        )
        t = asyncio.create_task(run_performance_job_loop(object(), None, interval_s=3600))
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert t.cancelled()

class TestORetornoPorSinalNaoEMorte:
    """⚠️ A proposição que o primeiro teste desta suíte NÃO media — e o ao vivo pegou.

    `run_consumer` instala handlers de SIGTERM/SIGINT e, ao recebê-los, **retorna**:
    não levanta, não é cancelado. Com um mock que dormia até o cancel, o caso do
    `CancelledError` passava e este caminho — o único que acontece num `docker stop`
    real — não era exercido por ninguém. Resultado medido ao vivo: o supervisor
    gritava *"TERMINOU sozinha"* em todo shutdown normal.

    É a lição do CLAUDE.md sobre instrumento honesto que mede a proposição errada:
    *"pergunte de qual PROPOSIÇÃO cada ramo é evidência"*.
    """

    async def test_retorno_de_run_consumer_nao_encerra_a_task(self, monkeypatch):
        chamadas = 0

        async def retorna_como_no_sigterm(*_a, **_k):
            nonlocal chamadas
            chamadas += 1
            return None                     # exatamente o que o real faz no sinal

        monkeypatch.setattr(main_mod, "run_consumer", retorna_como_no_sigterm)
        t = asyncio.create_task(main_mod._run_consumer_safe(object(), None))
        await _assentar()

        assert not t.done(), (
            "a task terminou 'com sucesso' quando o consumer retornou por sinal — "
            "e o supervisor reporta isso como morte espontanea em todo shutdown"
        )
        assert chamadas == 1, "e nao pode virar laco de rechamada, que seria ruido"

        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert t.cancelled(), "o fim ordenado desta task e o cancel do lifespan"
