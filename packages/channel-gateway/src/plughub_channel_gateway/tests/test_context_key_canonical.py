"""
test_context_key_canonical.py
CNS-19 — chave de `delegate.context`/`collect.context` cujo CANÔNICO é do `core.*`
é gravada no canônico, não no alias `session.<k>`.

POR QUE ESTE TESTE EXISTE. A CNS-11 moveu 56 nomes de plataforma para `core.*` e
migrou os LEITORES — o Console (`isFormFillSnapshot` lê
`core.workflow.dialog_form_id`), o `skill_dialog_runner_v1`, o
`skill_survey_runner_v1` e o step de desarme do `skill_limite_processo_v1`. **Este
produtor não foi migrado junto**: o laço prefixava toda chave com `session.`.

O efeito, medido ao vivo em 2026-09-03: item de fila PULL — aprovação **e** wrap-up,
que são os dois `delegate` com `dialog_form_id` no context — abria no chat com
*"Awaiting messages…"* em vez de renderizar o formulário. Ficou invisível porque o
caminho de *collect-engage* escreve a canônica direto, então metade funcionava; e
porque o funil CARIMBA a canônica no `atributo` **sem renomear a chave**, de modo
que o dado parece certo numa inspeção e é ilegível para quem lê pelo nome novo.

Os pares são obrigatórios: renomear × NÃO renomear. Um produtor que mandasse tudo
para a canônica passaria no primeiro caso e faria a fase V4 do
`adr-contextstore-allowlist` — inversão declarada NÃO reversível — chegar de
carona, sem decisão e sem medição.
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway.adapters.webhook import store_key_for_context_entry

_T = "tenant_test"


@pytest.mark.asyncio
async def test_nome_de_plataforma_vai_para_a_canonica_core():
    """`dialog_form_id` é lido por código de plataforma pela canônica."""
    assert await store_key_for_context_entry(_T, "dialog_form_id") == \
        "core.workflow.dialog_form_id"


@pytest.mark.asyncio
async def test_ja_prefixado_tambem_normaliza():
    """O chamador pode escrever `session.dialog_form_id` à mão — o alias é o mesmo."""
    assert await store_key_for_context_entry(_T, "session.dialog_form_id") == \
        "core.workflow.dialog_form_id"


@pytest.mark.asyncio
async def test_vocabulario_do_TENANT_continua_em_session():
    """O par obrigatório. `session.cpf` é alias de `session.cliente.cpf` — canônica de
    SESSÃO, não de plataforma. Renomeá-la é a fase V4 do ADR da ALLOWLIST, que é
    decisão própria e não reversível; ela não pode chegar de carona neste conserto."""
    assert await store_key_for_context_entry(_T, "cpf") == "session.cpf"
    assert await store_key_for_context_entry(_T, "session.cpf") == "session.cpf"


@pytest.mark.asyncio
async def test_chave_desconhecida_fica_como_estava():
    """Chave que o mapa não conhece (campo de tela do pacote) segue em `session.<k>` —
    o conserto não pode mudar o destino de dado arbitrário do chamador."""
    assert await store_key_for_context_entry(_T, "numero_cartao") == "session.numero_cartao"
    assert await store_key_for_context_entry(_T, "campo_que_nao_existe") == \
        "session.campo_que_nao_existe"


@pytest.mark.asyncio
async def test_decisions_nao_e_de_plataforma_e_fica_onde_esta():
    """`session.decisions` é alias de `session.workflow.decisions` — canônica de
    SESSÃO. O Console a lê por esse nome mesmo; movê-la quebraria a leitura que
    hoje funciona, que é o modo de falha inverso ao que o conserto ataca."""
    assert await store_key_for_context_entry(_T, "decisions") == "session.decisions"
