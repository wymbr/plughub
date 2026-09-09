"""
test_ret15_tarefas_efemeras.py — RET-15/16: os adapters usam o helper do pacote.

⚠️ O comportamento de `disparar()` (dono, alarme, silêncio no término normal) é
testado na casa dele, `packages/py-tasks/tests/test_tarefas.py`. O que sobra aqui
é o que só o channel-gateway pode afirmar: que os SEUS adapters chamam aquele
helper, e não um `disparar` local que não guardasse referência nenhuma.

Não é redundante com o gate: ele varre o repositório por AST e reprova o padrão
`create_task` solto; isto prova qual objeto o módulo importou.
"""
from __future__ import annotations

import importlib

import pytest

import plughub_tasks


class TestOsCallSites:
    """Os adapters chamam `disparar`, e não `create_task` — medido no MÓDULO.

    Não é redundante com o gate: ele varre o repositório por AST e reprova o
    padrão; isto prova que ESTE import existe e é o helper certo, o que pega o
    caso de alguém definir um `disparar` local que não guarda referência nenhuma.
    """

    @pytest.mark.parametrize("modulo", [
        "plughub_channel_gateway.adapters.whatsapp",
        "plughub_channel_gateway.adapters.email",
        "plughub_channel_gateway.adapters.sms",
        "plughub_channel_gateway.adapters.webrtc",
        "plughub_channel_gateway.outbound_consumer",
    ])
    def test_o_adapter_usa_o_helper_do_pacote(self, modulo: str):
        import importlib
        m = importlib.import_module(modulo)
        assert getattr(m, "disparar", None) is plughub_tasks.disparar, (
            f"{modulo} nao esta usando o `disparar` do pacote"
        )
