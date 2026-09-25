# -*- coding: utf-8 -*-
"""APR-09 — o resume exige `approvals.decide` só de tarefa de APROVAÇÃO, nunca de todo resume.

A ficha dizia que o ingress aplicava o portão *"a QUALQUER resume com JWT"*. Isso foi
verdade até a Camada E2 (2026-07-24), que passou a decidir a capacidade exigida pelo
TIPO da tarefa, resolvido SERVER-SIDE do contexto da workflow suspensa —
`WebhookAdapter.resume_required_abac`. Ela é o coração da garantia, e até aqui só aparecia
MOCKADA (`test_resume_authority.py`): um resolvedor que voltasse a devolver
("approvals","decide") para tudo passaria em todos os testes, e o wrap-up de operador
comum tomaria 403.

Este arquivo roda a função REAL (e o `_read_ctx_tag` real) sobre um Redis falso, e cobre
a precedência inteira: declaração explícita > marcador de aprovação > form-fill (None).
"""
import asyncio
import json
import types

import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter

T = "tenant_x"
TOKEN = "tok-123"
SID = "sess-abc"


class _Redis:
    def __init__(self, ctx: dict[str, str], token_known: bool = True):
        self._h = {f"{T}:resume_tokens": {TOKEN: f"{SID}:x:y"} if token_known else {},
                   f"{T}:ctx:{SID}": {k: json.dumps({"value": v}) for k, v in ctx.items()}}

    async def hget(self, key, field):
        return self._h.get(key, {}).get(field)


def _resolver(ctx: dict[str, str], token_known: bool = True):
    # `_CTX_EMPTY` é atributo de CLASSE que o `_read_ctx_tag` lê; sem ele a exceção cai no
    # `except` fail-soft e TODO caso vira None — um harness que passaria no teste do
    # form-fill por acidente (foi o que a primeira versão deste arquivo fez).
    fake = types.SimpleNamespace(_redis=_Redis(ctx, token_known),
                                 _CTX_EMPTY=WebhookAdapter._CTX_EMPTY)
    fake._read_ctx_tag = types.MethodType(WebhookAdapter._read_ctx_tag, fake)
    return lambda: asyncio.run(WebhookAdapter.resume_required_abac(fake, T, TOKEN))


def test_form_fill_generico_NAO_exige_capacidade():
    """O caso que a ficha temia: wrap-up e form-fill não pedem `approvals.decide`."""
    assert _resolver({"core.workflow.dialog_form_id": "dialog_wrapup"})() is None


def test_aprovacao_pelo_marcador_exige_approvals_decide():
    assert _resolver({"session.decisions": '["aprovar","rejeitar"]'})() == ("approvals", "decide")


def test_declaracao_explicita_do_autor_VENCE_o_marcador():
    ctx = {"session.resume_abac": "deploys.promover", "session.decisions": '["ok"]'}
    assert _resolver(ctx)() == ("deploys", "promover")


@pytest.mark.parametrize("declarado", ["semponto", ".campo", "modulo.", "  .  "])
def test_declaracao_malformada_nao_vira_par_e_cai_na_regra_seguinte(declarado):
    assert _resolver({"session.resume_abac": declarado})() is None
    assert _resolver({"session.resume_abac": declarado, "session.decisions": "x"})() \
        == ("approvals", "decide")


def test_token_desconhecido_nao_exige_nada_aqui():
    """O 404 do token é do handle_resume; aqui a ausência não inventa exigência."""
    assert _resolver({"session.decisions": "x"}, token_known=False)() is None
