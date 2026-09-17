"""
test_webrtc_token_attendant.py — VOZ-15 (2026-09-17): o token de mídia do agente exige ATENDER.

O QUE ESTAVA ERRADO. `GET /webrtc/token/{sid}?role=agent` exigia Bearer e `agent_assist.atender`
recortado ao pool da sessão, e parava aí — perguntava *"você pode atender contatos deste pool?"*,
nunca *"você atende ESTE contato?"*. Medido ao vivo em 2026-09-15 (`probe_webrtc_agent_console.sh`,
linha INFO): com o agente atribuído e na sala, outro usuário com o mesmo grant recebia **200**, isto
é, um token de agente para a chamada de um cliente alheio.

O discriminador é o conjunto de ATENDENTES do estado de mídia (`channel:webrtc:{sid}:media`,
VOZ-10). A instância humana é `human-{sub}`, o mesmo `sub` que vira identidade na sala.

TRÊS COISAS QUE ESTES TESTES SEGURAM, e nenhuma delas é "o 403 aconteceu":
  · o controle POSITIVO — quem atende continua recebendo token. Sem ele, uma rota que recusasse
    todo mundo passaria no caso negativo e pareceria proteção;
  · o SUPERVISOR segue fora da regra: ele assina oculto sem atender, por definição. Se a regra o
    pegasse, a supervisão morreria em silêncio — e o teste é a testemunha disso;
  · a CORRIDA: sem atendente conhecido a resposta é `room_not_ready` (404), que o Console sabe
    repetir, e não 403, que o faria desistir de uma chamada que ia funcionar meio segundo depois.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter

SECRET = "segredo-de-teste-hs256"
POOL   = "atendimento_humano"
SID    = "sessao-de-teste"

ATENDER  = {"agent_assist": {"atender":   {"access": "read_write", "scope": [POOL]}}}
MONITORAR = {"contacts":    {"monitorar": {"access": "read_only",  "scope": [POOL]}}}


def _token(sub: str, module_config: dict, tenant: str = "tenant_a") -> str:
    return pyjwt.encode({"sub": sub, "tenant_id": tenant, "module_config": module_config,
                         "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def _req(sub: str, module_config: dict):
    return SimpleNamespace(headers={"authorization": "Bearer " + _token(sub, module_config)})


@pytest.fixture
def adapter(monkeypatch):
    """Adapter de mentira com o ESTADO que a rota consulta.

    ⚠️ `attendant_ids` é assertado em `WebRTCAdapter` antes de ser mockado: um mock CRIA o
    atributo que se pede a ele, então sem esta linha o teste continuaria verde se alguém
    renomeasse o método — provando a chamada e escondendo a ausência.
    """
    assert hasattr(WebRTCAdapter, "attendant_ids"), "o alvo mockado sumiu do adapter"

    a = MagicMock()
    a.provider_unavailable = None
    a._redis = MagicMock()
    a._redis.get = AsyncMock(return_value='{"tenant_id": "tenant_a", "pool_id": "%s"}' % POOL)
    a.attendant_ids = AsyncMock(return_value=set())
    a.get_token = AsyncMock(return_value={"token": "t", "livekit_url": "wss://x", "room_name": "r"})
    monkeypatch.setattr(cg_main, "_webrtc_adapter", a)
    monkeypatch.setattr(cg_main.get_settings(), "auth_jwt_secret", SECRET, raising=False)
    return a


async def _pede(req, role="agent"):
    return await cg_main.webrtc_token(SID, req, role=role)


class TestAgente:
    async def test_quem_ATENDE_recebe_token(self, adapter):
        """Controle positivo — e é ele que separa 'portão' de 'rota quebrada'."""
        adapter.attendant_ids.return_value = {"human-ana", "sac_ia-001"}
        r = await _pede(_req("ana", ATENDER))
        assert r["token"] == "t"
        adapter.get_token.assert_awaited_once()

    async def test_mesmo_grant_sem_atender_403(self, adapter):
        """O caso que valia 200 até 2026-09-17: capacidade no pool não é atendimento."""
        adapter.attendant_ids.return_value = {"human-ana"}
        with pytest.raises(HTTPException) as e:
            await _pede(_req("bruno", ATENDER))
        assert e.value.status_code == 403
        assert "ATENDER" in e.value.detail
        adapter.get_token.assert_not_awaited()      # recusa ANTES de emitir credencial de sala

    async def test_sem_atendentes_ainda_e_room_not_ready(self, adapter):
        """Corrida normal do `routing.assigned`: o Console repete em `room_not_ready` e desiste
        no 403. Trocar um pelo outro quebraria a chamada legítima que chega meio segundo depois."""
        adapter.attendant_ids.return_value = set()
        with pytest.raises(HTTPException) as e:
            await _pede(_req("ana", ATENDER))
        assert e.value.status_code == 404
        assert e.value.detail["code"] == "room_not_ready"
        adapter.get_token.assert_not_awaited()

    async def test_atendente_que_saiu_perde_o_token(self, adapter):
        """`participant_left` tira do estado de mídia — e o token acompanha, sem TTL próprio."""
        adapter.attendant_ids.return_value = {"human-carla"}
        with pytest.raises(HTTPException) as e:
            await _pede(_req("ana", ATENDER))
        assert e.value.status_code == 403


class TestSupervisor:
    async def test_supervisor_NAO_precisa_atender(self, adapter):
        """A exceção é a função: `contacts.monitorar` assina oculto sem atender. Se este teste
        ficar vermelho, a regra nova matou a supervisão — que é pior do que o buraco que ela fecha."""
        adapter.attendant_ids.return_value = {"human-ana"}
        r = await _pede(_req("bruno", MONITORAR), role="supervisor")
        assert r["token"] == "t"

    async def test_supervisor_nem_consulta_os_atendentes(self, adapter):
        adapter.attendant_ids.return_value = set()
        await _pede(_req("bruno", MONITORAR), role="supervisor")
        adapter.attendant_ids.assert_not_awaited()


class TestOrdem:
    async def test_capacidade_decide_ANTES_do_atendimento(self, adapter):
        """Sem o grant, a recusa é de CAPACIDADE e o estado de mídia nem é lido — recusa não paga
        Redis, e o 403 nomeia o que falta em vez de culpar o atendimento."""
        adapter.attendant_ids.return_value = {"human-bruno"}
        with pytest.raises(HTTPException) as e:
            await _pede(_req("bruno", {"contacts": {"monitorar": {"access": "read_only"}}}))
        assert e.value.status_code == 403
        assert "agent_assist" in e.value.detail
        adapter.attendant_ids.assert_not_awaited()
