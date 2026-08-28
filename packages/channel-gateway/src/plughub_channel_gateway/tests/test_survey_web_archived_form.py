"""
test_survey_web_archived_form.py
Fase F3 do ADR `adr-dialog-form-deletion` (2026-08-28).

POR QUE ESTE TESTE EXISTE. O arco do arquivamento decidiu que o dialog-api **serve** form
arquivado por id (D1) — é o que mantém de pé contato em andamento, composição de nota no fim
do diálogo e leitura de história já encerrada. O efeito colateral é que este ponto, que
ANTES falhava sozinho (o 404 levantava no `raise_for_status`), passou a **suceder**: sem
guarda, criar link de survey sobre um form arquivado congelaria esse form num token por dias,
contornando o arquivamento em silêncio.

`survey_link_create` é o ÚNICO lugar do produto que cria vínculo novo a partir de um
`form_id` que não veio de um vínculo existente (D4) — por isso a recusa mora aqui, e só aqui.

As duas asserções são inseparáveis: recusar (a exceção) e **não ter escrito o token** (o
vínculo). Um `create` que levantasse depois do `SET` recusaria e vincularia ao mesmo tempo.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.survey_web import ArchivedFormError, SurveyWebService

_T   = "tenant_test"
_FID = "dialog_nps_buttons"
_ARQ = "2026-08-28T09:00:00+00:00"


def _service(monkeypatch, form: dict) -> tuple[SurveyWebService, MagicMock]:
    """SurveyWebService com o dialog-api mockado devolvendo `form`."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock(return_value=None)
    resp.json = MagicMock(return_value=form)

    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "plughub_channel_gateway.survey_web.httpx.AsyncClient",
        MagicMock(return_value=client),
    )

    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)

    svc = SurveyWebService(
        redis=redis,
        producer=MagicMock(),
        dialog_api_url="http://dialog-api:3760",
        signals_topic="session.signals",
    )
    return svc, redis


def _published(**extra) -> dict:
    return {"form_id": _FID, "version": 2, "status": "published",
            "nodes": [], "default_locale": "pt-BR", **extra}


class TestArchivedFormRefusal:
    async def test_archived_form_is_REFUSED_and_no_token_is_written(self, monkeypatch):
        svc, redis = _service(monkeypatch, _published(deleted_at=_ARQ))
        with pytest.raises(ArchivedFormError) as exc:
            await svc.create(_T, _FID)
        assert exc.value.form_id == _FID
        assert exc.value.deleted_at == _ARQ
        redis.set.assert_not_awaited()          # nenhum vínculo criado

    async def test_live_form_still_creates_the_token(self, monkeypatch):
        """Testemunha de presença. Sem ela, uma guarda que recusasse SEMPRE — ou um
        `deleted_at` lido do campo errado — passaria no teste acima e mataria o survey
        outbound inteiro sem nada ficar vermelho."""
        svc, redis = _service(monkeypatch, _published(deleted_at=None))
        out = await svc.create(_T, _FID)
        assert out["token"]
        assert out["path"].startswith("/survey/")
        redis.set.assert_awaited_once()

    async def test_form_WITHOUT_the_field_is_treated_as_live(self, monkeypatch):
        """Compatibilidade com dialog-api anterior à coluna: ausência do campo é 'vivo',
        nunca 'arquivado'. O caminho seguro aqui é o permissivo — recusar por ausência
        derrubaria todo survey outbound durante um deploy parcial."""
        svc, redis = _service(monkeypatch, _published())
        await svc.create(_T, _FID)
        redis.set.assert_awaited_once()
