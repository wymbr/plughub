"""
test_ret13_background_task_supervision.py — RET-13: a morte tem de aparecer.

O gate `infra/test/probe_background_task_supervision.sh` mede ESTRUTURA — que toda
task de boot passe por um supervisor. Esta suíte mede a outra proposição: que o
supervisor **fale** quando deve e **cale** quando deve. Um `add_done_callback` que
não logasse nada passaria no gate e falharia aqui.

Três ramos, e o terceiro é o que separa alarme útil de alarme ignorado:
  1. a corrotina levanta        → ERROR nomeando a task
  2. a corrotina retorna        → WARNING (não há exceção nenhuma para inspecionar)
  3. a task é cancelada         → SILÊNCIO (shutdown normal)

⚠️ O ramo 3 tinha um defeito de PRODUTO junto: `_run_consumer_safe` e
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

supervisionar = main_mod._supervisionar


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
