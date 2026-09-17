"""
test_speech_check_route.py — VOZ-27: a porta com portão de `POST /v1/speech-checks`.

O executor é interno e sua credencial é um token de SERVIÇO. Esta rota existe para que a TELA possa
pedir uma verificação sem que o browser toque nesse token nem na porta anônima do pool webhook — e
por isso ela só vale se o portão fechar de verdade e se a autoria não vier do corpo.

O que cada caso fixa:
  * sem Bearer → 401, e o executor NÃO é chamado (o controle positivo mede isso, senão um 401 vindo
    de outra causa passaria por proteção);
  * com Bearer e sem `config.channels` em ESCRITA → 403 (inclusive com leitura, que é o caso que
    parece autorizado);
  * `requested_by` é o `sub` do token e o tenant é o do token — corpo não decide autoria;
  * 409 e 422 do serviço chegam ao chamador COM o motivo: "ocupado" e "perfil não existe" pedem
    reações diferentes de quem clicou;
  * env faltando → 503 nomeando a env, nunca um pedido que finge ter saído;
  * executor fora → 502 dizendo, não 500 mudo.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.main import SpeechCheckRunRequest

SECRET = "segredo-de-teste-hs256"
ESCRITA  = {"config": {"channels": {"access": "read_write"}}}
LEITURA  = {"config": {"channels": {"access": "read_only"}}}
OUTRO    = {"agent_assist": {"atender": {"access": "read_write"}}}


def _token(tenant: str = "tenant_a", module_config: dict | None = None, sub: str = "user_x") -> str:
    return pyjwt.encode({"sub": sub, "tenant_id": tenant, "module_config": module_config or {},
                         "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def _req(headers: dict | None = None):
    return SimpleNamespace(headers={k.lower(): v for k, v in (headers or {}).items()})


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("sem corpo JSON")
        return self._payload


class _ClienteFalso:
    """Capta o que a rota mandaria ao executor. `erro` simula executor inalcançável."""

    def __init__(self, resposta: _Resp | None = None, erro: Exception | None = None):
        self.resposta, self.erro = resposta, erro
        self.chamadas: list[dict] = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.chamadas.append({"url": url, "json": json, "headers": headers})
        if self.erro is not None:
            raise self.erro
        return self.resposta


@pytest.fixture
def ambiente(monkeypatch):
    s = cg_main.get_settings()
    monkeypatch.setattr(s, "auth_jwt_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "speech_check_url", "http://speech-check:3870", raising=False)
    monkeypatch.setattr(s, "speech_check_service_token", "token-de-servico", raising=False)

    def instalar(cliente: _ClienteFalso) -> _ClienteFalso:
        monkeypatch.setattr(cg_main.httpx, "AsyncClient", cliente)
        return cliente

    return SimpleNamespace(settings=s, instalar=instalar)


async def _chamar(request, corpo=None):
    return await cg_main.speech_check_run(request, corpo or SpeechCheckRunRequest())


class TestPortao:
    async def test_sem_credencial_401_e_nao_chama_o_executor(self, ambiente):
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "x"})))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req())
        assert e.value.status_code == 401
        # O ponto do caso: recusar ANTES de pedir. Sem esta asserção, um 401 devolvido
        # depois de a verificação já ter começado passaria por portão.
        assert cli.chamadas == []

    async def test_token_sem_capacidade_403(self, ambiente):
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "x"})))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req({"authorization": "Bearer " + _token(module_config=OUTRO)}))
        assert e.value.status_code == 403
        assert cli.chamadas == []

    async def test_leitura_nao_basta(self, ambiente):
        """Pedir medição é ATO, não leitura — quem só olha canais não dispara chamada."""
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "x"})))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req({"authorization": "Bearer " + _token(module_config=LEITURA)}))
        assert e.value.status_code == 403
        assert cli.chamadas == []

    async def test_escrita_passa(self, ambiente):
        """Controle positivo: o portão deixa passar quem deve — senão 'nega tudo' pareceria proteção."""
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "abc", "status": "running"})))
        r = await _chamar(_req({"authorization": "Bearer " + _token(module_config=ESCRITA)}))
        assert r.status_code == 202
        assert len(cli.chamadas) == 1


class TestAutoria:
    async def test_quem_pediu_e_o_tenant_vem_do_TOKEN(self, ambiente):
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "abc"})))
        await _chamar(
            _req({"authorization": "Bearer " + _token(tenant="tenant_z", module_config=ESCRITA, sub="ana")}),
            SpeechCheckRunRequest(speech_profile_id="tronco-sp"),
        )
        enviado = cli.chamadas[0]["json"]
        assert enviado["requested_by"] == "user:ana"
        assert enviado["tenant_id"] == "tenant_z"
        assert enviado["speech_profile_id"] == "tronco-sp"
        assert cli.chamadas[0]["headers"]["x-service-token"] == "token-de-servico"

    async def test_corpo_nao_declara_autoria(self, ambiente):
        """`requested_by` no corpo é ignorado: campo de autoria preenchido pelo chamador não é autoria."""
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "abc"})))
        corpo = SpeechCheckRunRequest.model_validate({"speech_profile_id": None, "requested_by": "user:chefe"})
        await _chamar(_req({"authorization": "Bearer " + _token(module_config=ESCRITA, sub="ana")}), corpo)
        assert cli.chamadas[0]["json"]["requested_by"] == "user:ana"


class TestVeredictoDoServico:
    @pytest.mark.parametrize("status,payload", [
        (409, {"detail": {"reason": "check_running", "check_id": "em-curso"}}),
        (422, {"detail": {"reason": "profile_not_found", "speech_profile_id": "nao-existe"}}),
    ])
    async def test_recusa_chega_com_o_motivo(self, ambiente, status, payload):
        ambiente.instalar(_ClienteFalso(_Resp(status, payload)))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req({"authorization": "Bearer " + _token(module_config=ESCRITA)}))
        assert e.value.status_code == status
        assert e.value.detail == payload["detail"]

    async def test_executor_inalcancavel_502(self, ambiente):
        ambiente.instalar(_ClienteFalso(erro=RuntimeError("connect timeout")))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req({"authorization": "Bearer " + _token(module_config=ESCRITA)}))
        assert e.value.status_code == 502
        assert "connect timeout" in str(e.value.detail)


class TestConfiguracaoAusente:
    @pytest.mark.parametrize("campo", ["speech_check_url", "speech_check_service_token"])
    async def test_env_faltando_recusa_503_nomeando(self, ambiente, monkeypatch, campo):
        monkeypatch.setattr(ambiente.settings, campo, "", raising=False)
        cli = ambiente.instalar(_ClienteFalso(_Resp(202, {"check_id": "x"})))
        with pytest.raises(HTTPException) as e:
            await _chamar(_req({"authorization": "Bearer " + _token(module_config=ESCRITA)}))
        assert e.value.status_code == 503
        assert "PLUGHUB_SPEECH_CHECK_URL" in e.value.detail
        assert cli.chamadas == []
