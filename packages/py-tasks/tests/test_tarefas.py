"""
test_tarefas.py — plughub_tasks: a tarefa efemera tem dono e tem alarme.

Mudou de casa na RET-16 (era `channel-gateway/tests/test_ret15_...`): o helper
virou pacote quando o sexto consumidor apareceu, e o teste segue o codigo.

O gate `probe_background_task_supervision.sh` § C mede ESTRUTURA (nenhum
`create_task` solto em handler). Esta suíte mede o comportamento de `disparar()`:

  1. a task ganha DONO enquanto vive          — sem isso o CPython pode coletá-la
  2. e o conjunto ESVAZIA quando ela termina  — senão o dono vira vazamento
  3. exceção vira ERROR nomeando a tarefa     — o dano que a RET-15 fecha
  4. cancelamento é silêncio
  5. ⚠️ TÉRMINO NORMAL é silêncio

⚠️ O item 5 é o que separa este helper do supervisor de boot (RET-13), e copiar
aquele comportamento para cá seria o defeito: uma tarefa efêmera termina a cada
mensagem, e um WARNING por mensagem é ruído que ensina a ignorar o log inteiro.
Os dois helpers existem porque as duas populações têm ciclos de vida OPOSTOS.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import plughub_tasks as tarefas


async def _assentar() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


class TestDono:
    async def test_a_tarefa_tem_dono_enquanto_vive(self):
        comecou = asyncio.Event()
        solte   = asyncio.Event()

        async def trabalha():
            comecou.set()
            await solte.wait()

        antes = tarefas.vivas()
        t = tarefas.disparar(trabalha(), nome="teste-dono")
        await comecou.wait()
        assert tarefas.vivas() == antes + 1, (
            "sem referencia forte o loop e o unico dono, e o CPython documenta que "
            "a task pode ser coletada no meio da execucao"
        )
        solte.set()
        await t
        await _assentar()
        assert tarefas.vivas() == antes, (
            "o conjunto tem de ESVAZIAR: uma entrada por mensagem, para sempre, "
            "seria trocar um vazamento de excecao por um vazamento de memoria"
        )

    async def test_o_conjunto_esvazia_tambem_quando_a_tarefa_MORRE(self):
        async def morre():
            raise RuntimeError("x")

        antes = tarefas.vivas()
        t = tarefas.disparar(morre(), nome="teste-vazamento")
        with pytest.raises(RuntimeError):
            await t
        await _assentar()
        assert tarefas.vivas() == antes


class TestAlarme:
    async def test_excecao_vira_error_nomeando_a_tarefa(self, caplog):
        async def morre():
            raise ValueError("whatsapp payload invalido")

        with caplog.at_level(logging.ERROR):
            t = tarefas.disparar(morre(), nome="whatsapp-inbound")
            with pytest.raises(ValueError):
                await t
            await _assentar()

        erros = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert erros, (
            "sem esta linha, uma mensagem de cliente some sem rastro: a excecao "
            "fica presa numa Task que ninguem aguarda"
        )
        msg = erros[0].getMessage()
        assert "whatsapp-inbound" in msg, "o alarme precisa NOMEAR a tarefa"
        assert "whatsapp payload invalido" in msg, "e dizer a causa"

    async def test_cancelamento_e_silencio(self, caplog):
        async def dorme():
            await asyncio.sleep(3600)

        with caplog.at_level(logging.WARNING):
            t = tarefas.disparar(dorme(), nome="teste-cancel")
            await asyncio.sleep(0)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await _assentar()

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    async def test_termino_normal_e_SILENCIO(self, caplog):
        """⚠️ A diferença para o supervisor de BOOT, e ela é o ponto.

        Lá, uma task que retorna é incidente (consumidor não deveria retornar
        enquanto o serviço vive). Aqui, retornar é o que a tarefa existe para
        fazer — uma por mensagem. Um WARNING por mensagem afogaria o log e
        ensinaria a ignorá-lo, que é como um alarme deixa de ser lido.
        """
        async def entrega():
            return "ok"

        with caplog.at_level(logging.DEBUG):
            await tarefas.disparar(entrega(), nome="outbound-dispatch")
            await _assentar()

        ruido = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not ruido, f"tarefa efemera que terminou bem gerou log: {ruido}"
