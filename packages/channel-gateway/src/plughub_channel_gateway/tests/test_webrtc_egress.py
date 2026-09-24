"""
tests/test_webrtc_egress.py — gravação da chamada por PARTES (VOZ-06).

Substitui os testes da "Phase D", que exercitavam um caminho que nunca rodou (sem egress no
compose, gatilho sem produtor, gravação em vídeo MP4 com `sleep(5)` adivinhando o fim do arquivo).
Cada caso traz o CONTROLE ao lado: o que grava tem de gravar, o que não grava não pode gravar.

O store é o `ProtocolBoundStore` do contrato dos escritores de anexo — ligado à assinatura do
Protocol, então `artifact_class` que o Protocol não aceitasse reprovaria aqui.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.adapters import media_policy, webrtc_recording
from plughub_channel_gateway.adapters.webrtc_provider import (
    EgressResult,
    LiveKitProvider,
    MockWebRTCProvider,
    WebRTCProviderUnavailable,
)
from plughub_channel_gateway.adapters.webrtc_recording import OPT_OUT_TAG, CallRecorder
from plughub_channel_gateway.recording_config import RecordingPolicy
from plughub_channel_gateway.tests.test_attachment_writers_contract import ProtocolBoundStore

TENANT = "tenant_test"
SID = "sid-voz06-rec-0001"
ROOM = "plughub-sip-x"
AVISO = "Esta chamada sera gravada."


class FakeRedis:
    def __init__(self) -> None:
        self.ctx: dict[str, str] = {}
        self.stream: list[dict] = []
        self.kv: dict[str, str] = {}
        self.hget_error: Exception | None = None

    async def hget(self, key, field):
        if self.hget_error is not None:
            raise self.hget_error
        assert key == f"{TENANT}:ctx:{SID}"
        return self.ctx.get(field)

    async def xadd(self, key, fields):
        assert key == f"session:{SID}:stream"
        self.stream.append(dict(fields))

    async def setex(self, key, ttl, value):
        self.kv[key] = value

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)

    def tipos(self) -> list[str]:
        return [e["type"] for e in self.stream]


class Rig:
    """O gravador com tudo que ele toca, e o registro da ORDEM das coisas."""

    def __init__(self, tmp_path: Path, *, texto=True, voz=False, store=True, retention=7) -> None:
        self.redis = FakeRedis()
        self.provider = MockWebRTCProvider()
        self.store = ProtocolBoundStore() if store else None
        self.ordem: list[str] = []
        self.dir = tmp_path / "rec"
        self._texto, self._voz = texto, voz
        orig_start = self.provider.start_egress

        async def start(room_name, filepath):
            self.ordem.append("egress")
            return await orig_start(room_name, filepath)
        self.provider.start_egress = start

        async def policy(_t):
            return RecordingPolicy(AVISO, retention, {"notice": "config", "retention_days": "config"})

        async def send_text(_sid, text):
            if self._texto:
                self.ordem.append(f"texto:{text}")
            return self._texto

        def speak(_sid, text, played=None):
            self.ordem.append(f"voz:{text}")
            if played is not None:
                played.set()

        # VOZ-39: o estado que o widget recebe, na ORDEM das outras coisas
        self.estados: list[str] = []

        async def on_state(_sid, estado):
            self.estados.append(estado)
            self.ordem.append(f"estado:{estado}")

        self.rec = CallRecorder(
            redis=self.redis, tenant_id=TENANT, output_dir=str(self.dir), config_api_url="http://x",
            default_notice="padrao", provider=lambda: self.provider, store=lambda: self.store,
            speak=speak, can_speak=lambda _s: self._voz, send_text=send_text, policy=policy,
            on_state=on_state,
        )

    def reserves(self) -> list[dict]:
        return [kw for m, kw in (self.store.chamadas if self.store else []) if m == "reserve"]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def _optout(valor) -> str:
    return json.dumps({"value": valor, "confidence": 1.0, "source": "skill"})


# ── Grava o que deve, e nada mais ────────────────────────────────────────────

class TestGravaPeloPool:
    async def test_pool_que_grava_vira_arquivo_guardado_como_call_recording(self, rig):
        await rig.rec.update(SID, ROOM, {"pool_grava"})
        assert rig.rec.active(SID)
        filepath = rig.provider.egresses_started[0]["filepath"]
        assert filepath.startswith(str(rig.dir / SID)) and filepath.endswith(".ogg")
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert rig.provider.egresses_stopped == ["EG_mock_0001"]
        [kw] = rig.reserves()
        assert kw["artifact_class"] == "call_recording" and kw["mime_type"] == "audio/ogg"
        # VOZ-36: a parte leva os pools que a atenderam — é o escopo de quem pode ouvir
        assert kw["attrs"]["pools"] == ["pool_grava"] and kw["attrs"]["part"] == 1
        # retenção da CLASSE, não a do anexo de webchat
        assert abs((kw["expires_at"] - datetime.now(timezone.utc)) - timedelta(days=7)) < timedelta(minutes=1)
        [done] = [e for e in rig.redis.stream if e["type"] == "recording.completed"]
        assert done["pools"] == "pool_grava" and done["part"] == "1" and done["artifact_class"] == "call_recording"
        assert done["visibility"] == "agents_only"
        assert not Path(filepath).exists(), "o rascunho tem de ser apagado depois de guardado"

    async def test_controle_sem_pool_que_grava_nao_grava(self, rig):
        await rig.rec.update(SID, ROOM, set())
        await rig.rec.update(SID, ROOM, {""})
        assert rig.provider.egresses_started == [] and rig.redis.stream == []

    async def test_aviso_vem_ANTES_do_egress(self, rig):
        await rig.rec.update(SID, ROOM, {"p"})
        assert rig.ordem == [f"texto:{AVISO}", "egress"]

    async def test_aviso_falado_so_libera_depois_de_tocar(self, tmp_path):
        r = Rig(tmp_path, texto=False, voz=True)
        await r.rec.update(SID, ROOM, {"p"})
        assert r.ordem == [f"voz:{AVISO}", "egress"]

    async def test_sem_aviso_entregue_nao_grava(self, tmp_path):
        r = Rig(tmp_path, texto=False, voz=False)
        await r.rec.update(SID, ROOM, {"p"})
        assert r.provider.egresses_started == []
        assert r.redis.stream[0]["type"] == "recording.skipped"
        assert r.redis.stream[0]["reason"] == "notice_undeliverable"


class TestPartes:
    async def test_troca_de_pools_que_gravam_corta_em_partes_sem_repetir_o_aviso(self, rig):
        await rig.rec.update(SID, ROOM, {"a"})
        await rig.rec.update(SID, ROOM, {"a", "b"})
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert len(rig.provider.egresses_started) == 2
        assert [k for k in rig.ordem if k.startswith("texto")] == [f"texto:{AVISO}"]
        pools = sorted(e["pools"] for e in rig.redis.stream if e["type"] == "recording.completed")
        assert pools == ["a", "a,b"]

    async def test_ultimo_atendente_que_grava_saiu_a_parte_fecha(self, rig):
        await rig.rec.update(SID, ROOM, {"a"})
        await rig.rec.update(SID, ROOM, set())
        await rig.rec.drain()
        assert not rig.rec.active(SID) and len(rig.reserves()) == 1

    async def test_bloco_mascarado_nao_e_gravado(self, rig):
        """NIV-07: a parte fecha no `hold` (antes do prompt) e a próxima só começa no `release`."""
        await rig.rec.update(SID, ROOM, {"p"})
        await rig.rec.hold(SID)
        assert not rig.rec.active(SID) and rig.provider.egresses_stopped == ["EG_mock_0001"]
        await rig.rec.update(SID, ROOM, {"p"})           # atendente "renegociado" durante o bloco
        assert len(rig.provider.egresses_started) == 1   # nada começa durante o bloco
        await rig.rec.release(SID)
        assert rig.rec.active(SID) and len(rig.provider.egresses_started) == 2
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert len(rig.reserves()) == 2
        assert [k for k in rig.ordem if k.startswith("texto")] == [f"texto:{AVISO}"]


class TestRecusa:
    async def test_recusa_antes_nao_grava(self, rig):
        rig.redis.ctx[OPT_OUT_TAG] = _optout(True)
        await rig.rec.update(SID, ROOM, {"p"})
        assert rig.provider.egresses_started == []
        assert [(e["type"], e["reason"]) for e in rig.redis.stream] == [("recording.skipped", "opt_out")]

    async def test_controle_false_grava(self, rig):
        rig.redis.ctx[OPT_OUT_TAG] = _optout("false")
        await rig.rec.update(SID, ROOM, {"p"})
        assert len(rig.provider.egresses_started) == 1

    async def test_valor_estranho_vale_como_recusa(self, rig):
        rig.redis.ctx[OPT_OUT_TAG] = _optout("talvez")
        await rig.rec.update(SID, ROOM, {"p"})
        assert rig.provider.egresses_started == []

    async def test_recusa_ilegivel_nao_grava(self, rig):
        rig.redis.hget_error = ConnectionError("redis fora")
        await rig.rec.update(SID, ROOM, {"p"})
        assert rig.provider.egresses_started == []
        assert rig.redis.stream[0]["reason"] == "opt_out_unreadable"

    async def test_recusa_durante_a_parte_descarta_e_nao_volta(self, rig, monkeypatch):
        monkeypatch.setattr(webrtc_recording, "OPT_OUT_POLL_S", 0.01)
        await rig.rec.update(SID, ROOM, {"p"})
        filepath = rig.provider.egresses_started[0]["filepath"]
        rig.redis.ctx[OPT_OUT_TAG] = _optout(True)
        for _ in range(200):
            if not rig.rec.active(SID):
                break
            await asyncio.sleep(0.01)
        await rig.rec.drain()
        assert not rig.rec.active(SID)
        assert rig.reserves() == [], "parte recusada NAO pode ser guardada"
        assert "recording.discarded" in rig.redis.tipos()
        assert not Path(filepath).exists()
        await rig.rec.update(SID, ROOM, {"p", "q"})      # troca de atendente depois da recusa
        assert len(rig.provider.egresses_started) == 1


class TestNuncaFingeGravar:
    async def test_egress_que_nao_comeca_e_dito(self, rig):
        rig.provider.egress_start_error = RuntimeError("sem servico de egress")
        await rig.rec.update(SID, ROOM, {"p"})
        assert not rig.rec.active(SID)
        [f] = rig.redis.stream
        assert f["type"] == "recording.failed" and "sem servico de egress" in f["reason"]

    async def test_egress_que_termina_falho_e_dito_e_nada_se_guarda(self, rig):
        rig.provider.egress_end_error = "Local upload failed: permission denied"
        await rig.rec.update(SID, ROOM, {"p"})
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert rig.reserves() == []
        assert any(e["type"] == "recording.failed" and "permission denied" in e["reason"]
                   for e in rig.redis.stream)

    async def test_parte_parada_antes_de_gravar_e_vazia_nao_falha(self, rig):
        from plughub_channel_gateway.adapters.webrtc_provider import EgressResult

        async def wait(egress_id, timeout_s):
            return EgressResult(egress_id, "EGRESS_ABORTED", error="Stopped before started")
        rig.provider.wait_egress = wait
        await rig.rec.update(SID, ROOM, {"p"})
        await rig.rec.hold(SID)
        await rig.rec.drain()
        assert [(e["type"], e.get("reason")) for e in rig.redis.stream] == [("recording.discarded", "empty")]
        # controle: ABORTED COM arquivo não é "vazia" — cai no caminho de falha dita
        async def wait2(egress_id, timeout_s):
            return EgressResult(egress_id, "EGRESS_ABORTED", filename=str(rig.dir / "x.ogg"))
        rig.provider.wait_egress = wait2
        await rig.rec.release(SID)
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert rig.redis.stream[-1]["type"] == "recording.failed"

    async def test_sem_store_e_dito_e_o_rascunho_sai(self, tmp_path):
        r = Rig(tmp_path, store=False)
        await r.rec.update(SID, ROOM, {"p"})
        filepath = r.provider.egresses_started[0]["filepath"]
        await r.rec.close(SID)
        await r.rec.drain()
        assert any(e["type"] == "recording.failed" and e["stage"] == "store" for e in r.redis.stream)
        assert not Path(filepath).exists()

    async def test_arquivo_fora_do_rascunho_nao_e_lido(self, rig, tmp_path):
        fora = tmp_path / "fora.ogg"
        fora.write_bytes(b"OggS")

        async def wait(egress_id, timeout_s):
            from plughub_channel_gateway.adapters.webrtc_provider import EgressResult
            return EgressResult(egress_id, "EGRESS_COMPLETE", filename=str(fora), size_bytes=4)
        rig.provider.wait_egress = wait
        await rig.rec.update(SID, ROOM, {"p"})
        await rig.rec.close(SID)
        await rig.rec.drain()
        assert rig.reserves() == [] and fora.exists()
        assert any("FORA do rascunho" in e.get("reason", "") for e in rig.redis.stream)

    async def test_sem_plano_de_midia_e_dito(self, tmp_path):
        r = Rig(tmp_path)
        r.rec._provider = lambda: None
        await r.rec.update(SID, ROOM, {"p"})
        assert r.redis.stream[0]["type"] == "recording.failed"


# ── VOZ-39 / VOZ-44: o estado que o widget mostra fixo ───────────────────────

@pytest.fixture(autouse=True)
def _vigia_rapido(monkeypatch):
    """O vigia do egress pergunta ao SFU em ms, não em s — e o teste espera pelo FATO (o estado
    anunciado), nunca por uma contagem de voltas."""
    monkeypatch.setattr(webrtc_recording, "EGRESS_POLL_FAST_S", 0.005)
    monkeypatch.setattr(webrtc_recording, "EGRESS_POLL_S", 0.005)


async def _ate(cond, teto: float = 2.0) -> None:
    fim = asyncio.get_running_loop().time() + teto
    while not cond():
        assert asyncio.get_running_loop().time() < fim, "condicao nao cumprida no prazo"
        await asyncio.sleep(0.005)


def _estado(eg: str, status: str, **kw) -> EgressResult:
    return EgressResult(eg, status, **kw)


class TestEstadoNoWidget:
    """O aviso é mensagem de chat, que rola; a faixa fixa lê ESTE estado. Anunciado só quando muda,
    e só depois do FATO: `recording` quando o SFU CONFIRMA o egress (VOZ-44), não quando é pedido."""

    async def test_gravando_so_depois_do_aviso_do_egress_e_da_confirmacao(self, rig):
        rig.provider.egress_status_now["EG_mock_0001"] = _estado("EG_mock_0001", "EGRESS_STARTING")
        await rig.rec.update(SID, ROOM, {"p"})
        await asyncio.sleep(0.05)
        assert rig.ordem == [f"texto:{AVISO}", "egress"], "egress PEDIDO nao acende a faixa"
        rig.provider.egress_status_now.pop("EG_mock_0001")          # o SFU confirma
        await _ate(lambda: rig.estados == ["recording"])
        assert rig.ordem == [f"texto:{AVISO}", "egress", "estado:recording"]

    async def test_egress_que_nao_confirma_nao_acende_e_e_dito(self, rig, monkeypatch, caplog):
        """Medido ao vivo: sem ninguém publicando áudio, STARTING a parte inteira."""
        monkeypatch.setattr(webrtc_recording, "EGRESS_STARTING_WARN_S", 0.05)
        rig.provider.egress_status_now["EG_mock_0001"] = _estado("EG_mock_0001", "EGRESS_STARTING")
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: any("NAO confirmou" in r.getMessage() for r in caplog.records))
        assert rig.estados == [] and rig.rec.active(SID)
        rig.provider.egress_status_now.pop("EG_mock_0001")          # confirmou tarde: acende
        await _ate(lambda: rig.estados == ["recording"])

    async def test_confirmada_e_depois_esfria_apaga(self, rig, monkeypatch):
        """Parte nova depois de uma ativa, que nunca confirma: a faixa não fica mentindo."""
        monkeypatch.setattr(webrtc_recording, "EGRESS_STARTING_WARN_S", 0.05)
        await rig.rec.update(SID, ROOM, {"a"})
        await _ate(lambda: rig.estados == ["recording"])
        rig.provider.egress_status_now["EG_mock_0002"] = _estado("EG_mock_0002", "EGRESS_STARTING")
        await rig.rec.update(SID, ROOM, {"a", "b"})
        await _ate(lambda: rig.estados == ["recording", "stopped"])

    async def test_troca_de_atendentes_nao_pisca(self, rig):
        await rig.rec.update(SID, ROOM, {"a"})
        await _ate(lambda: rig.estados == ["recording"])
        await rig.rec.update(SID, ROOM, {"a", "b"})      # corta a parte e começa outra
        await _ate(lambda: rig.rec._recs[SID].part.active)
        assert len(rig.provider.egresses_started) == 2 and rig.estados == ["recording"]
        await rig.rec.close(SID)
        assert rig.estados == ["recording", "stopped"]

    async def test_bloco_mascarado_e_pausa(self, rig):
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        await rig.rec.hold(SID)
        await rig.rec.release(SID)
        assert rig.estados == ["recording", "paused"], "parte nova ainda nao confirmada: segue pausa"
        await _ate(lambda: rig.estados == ["recording", "paused", "recording"])
        await rig.rec.close(SID)
        assert rig.estados == ["recording", "paused", "recording", "stopped"]

    async def test_bloco_antes_de_gravar_nao_e_pausa(self, rig):
        """Sem aviso ainda não houve gravação — `paused` diria ao cliente que algo foi gravado."""
        rig.rec._recs[SID] = webrtc_recording._Rec(room=ROOM, demand=("p",))
        await rig.rec.hold(SID)
        assert rig.estados == []

    async def test_ultimo_atendente_saiu_e_parado(self, rig):
        await rig.rec.update(SID, ROOM, {"a"})
        await _ate(lambda: rig.estados == ["recording"])
        await rig.rec.update(SID, ROOM, set())
        assert rig.estados == ["recording", "stopped"]

    async def test_controle_sem_gravacao_nenhum_estado(self, tmp_path):
        sem_aviso = Rig(tmp_path / "a", texto=False, voz=False)
        await sem_aviso.rec.update(SID, ROOM, {"p"})
        recusa = Rig(tmp_path / "b")
        recusa.redis.ctx[OPT_OUT_TAG] = _optout(True)
        await recusa.rec.update(SID, ROOM, {"p"})
        await asyncio.sleep(0.05)
        assert sem_aviso.estados == [] and recusa.estados == []

    async def test_recusa_durante_a_parte_e_parado_nao_pausa(self, rig, monkeypatch):
        monkeypatch.setattr(webrtc_recording, "OPT_OUT_POLL_S", 0.01)
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        rig.redis.ctx[OPT_OUT_TAG] = _optout(True)
        await _ate(lambda: rig.estados == ["recording", "stopped"])

    async def test_widget_fora_nao_para_a_gravacao(self, rig, caplog):
        async def quebra(_sid, _estado):
            raise ConnectionError("ws caiu")
        rig.rec._on_state = quebra
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: any("NAO chegou ao cliente" in r.getMessage() for r in caplog.records))
        assert rig.rec.active(SID)
        await rig.rec.close(SID)

    async def test_adapter_manda_o_estado_pelo_socket_do_cliente(self):
        from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
        assert hasattr(WebRTCAdapter, "_send_recording_state")
        ad = WebRTCAdapter.__new__(WebRTCAdapter)
        ws = MagicMock()
        ws.send_json = AsyncMock()
        ad._connections = {SID: ws}
        await ad._send_recording_state(SID, "recording")
        await ad._send_recording_state("sem-socket", "recording")    # telefone: não há tela
        [c] = ws.send_json.await_args_list
        assert c.args[0] == {"type": "webrtc.recording", "state": "recording"}


class TestEgressTerminaSozinho:
    """Egress que termina DEPOIS de confirmado (falho, abortado): a parte fecha na hora, a faixa
    apaga e o fim é falha. Controle ao lado: egress vivo não fecha nada, e estado ilegível não é
    "terminou"."""

    async def test_abortado_depois_de_ativo_fecha_apaga_e_e_falha(self, rig):
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        rig.provider.egress_status_now["EG_mock_0001"] = _estado(
            "EG_mock_0001", "EGRESS_ABORTED", error="Start signal not received")
        await _ate(lambda: not rig.rec.active(SID))
        await rig.rec.drain()
        assert rig.estados == ["recording", "stopped"]
        [f] = [e for e in rig.redis.stream if e["type"] == "recording.failed"]
        assert f["stage"] == "egress" and "Start signal not received" in f["reason"]
        assert "recording.discarded" not in rig.redis.tipos(), "aborto no meio NAO e 'parte vazia'"
        assert rig.reserves() == []
        assert rig.provider.egresses_stopped == [], "egress que ja terminou nao se pede para parar"

    async def test_controle_egress_vivo_segue_gravando(self, rig):
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        await asyncio.sleep(0.1)                      # várias consultas
        assert rig.rec.active(SID) and rig.estados == ["recording"]
        assert "recording.failed" not in rig.redis.tipos()

    async def test_estado_ilegivel_nao_e_terminou(self, rig, caplog):
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        rig.provider.egress_status_now["EG_mock_0001"] = ConnectionError("SFU fora")
        await asyncio.sleep(0.1)
        assert rig.rec.active(SID) and rig.estados == ["recording"]
        avisos = [r for r in caplog.records if "ILEGIVEL" in r.getMessage()]
        assert len(avisos) == 1, "o aviso sai UMA vez por queda, nao a cada consulta"

    async def test_completo_sozinho_com_arquivo_e_guardado(self, rig):
        """Ex.: a sala esvaziou e o SFU fechou o arquivo. O que foi gravado é gravação — guarda-se."""
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        caminho = rig.provider.egresses_started[0]["filepath"]
        rig.provider.egress_status_now["EG_mock_0001"] = _estado(
            "EG_mock_0001", "EGRESS_COMPLETE", filename=caminho)
        await _ate(lambda: not rig.rec.active(SID))
        await rig.rec.drain()
        assert rig.estados == ["recording", "stopped"]
        assert len(rig.reserves()) == 1 and "recording.completed" in rig.redis.tipos()

    async def test_proximo_fato_abre_parte_nova_sem_repetir_o_aviso(self, rig):
        await rig.rec.update(SID, ROOM, {"p"})
        await _ate(lambda: rig.estados == ["recording"])
        rig.provider.egress_status_now["EG_mock_0001"] = _estado("EG_mock_0001", "EGRESS_FAILED")
        await _ate(lambda: not rig.rec.active(SID))
        await rig.rec.update(SID, ROOM, {"p"})       # atendente renegociado
        assert rig.rec.active(SID) and len(rig.provider.egresses_started) == 2
        await _ate(lambda: rig.estados == ["recording", "stopped", "recording"])
        assert [k for k in rig.ordem if k.startswith("texto")] == [f"texto:{AVISO}"]
        await rig.rec.close(SID)


# ── O gatilho: a política do pool ────────────────────────────────────────────

class TestGatilhoDoPool:
    def _campo(self, **politica):
        return {"pool_id": "p", "media_policy_source": "registry",
                "media_policy": {"customer_publish": ["audio"], "agent_publish": ["audio"], **politica}}

    def test_recording_true_liga(self):
        rec, aviso = media_policy.attendant_from_pool_field("human", self._campo(recording=True))
        assert rec["recording"] is True and aviso is None

    def test_ausente_nao_liga(self):
        rec, _ = media_policy.attendant_from_pool_field("human", self._campo())
        assert rec["recording"] is False

    def test_valor_que_nao_e_true_nao_liga(self):
        rec, _ = media_policy.attendant_from_pool_field("human", self._campo(recording="true"))
        assert rec["recording"] is False

    def test_politica_nao_lida_nao_grava(self):
        rec, aviso = media_policy.attendant_from_pool_field(
            "human", {"pool_id": "p", "media_policy_source": "registry_unavailable"})
        assert rec["recording"] is False and aviso

    async def test_adapter_entrega_ao_gravador_so_os_pools_que_gravam(self):
        from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
        assert hasattr(WebRTCAdapter, "_recording_follow")
        ad = WebRTCAdapter.__new__(WebRTCAdapter)
        ad._sip = {}
        ad._recorder = MagicMock()
        ad._recorder.update = AsyncMock()
        await ad._recording_follow(SID, {"attendants": {
            "i1": {"pool_id": "grava", "recording": True},
            "i2": {"pool_id": "nao_grava", "recording": False},
            "i3": "estado-antigo",
        }})
        ad._recorder.update.assert_awaited_once()
        assert ad._recorder.update.await_args.args[2] == {"grava"}


# ── A porta pública de anexos não serve gravação ─────────────────────────────

class TestPortaPublica:
    async def _serve(self, monkeypatch, klass):
        from fastapi import HTTPException
        from plughub_channel_gateway import main as _main  # noqa: F401 — main antes: o router o importa
        from plughub_channel_gateway import upload_router
        from plughub_channel_gateway.attachment_store import AttachmentMeta
        store = MagicMock()
        store.resolve = AsyncMock(return_value=AttachmentMeta(
            file_id="f", tenant_id=TENANT, session_id=SID, original_name="x.ogg", mime_type="audio/ogg",
            size_bytes=4, file_path="p", serving_url="u", expires_at=None, deleted_at=None,
            artifact_class=klass))

        async def _gen():
            yield b"OggS"
        store.stream_bytes = AsyncMock(return_value=_gen())
        monkeypatch.setattr(upload_router._main_module, "_attachment_store", store, raising=False)
        try:
            return await upload_router.serve_attachment("f")
        except HTTPException as exc:
            return exc

    async def test_gravacao_nao_sai_pela_porta_publica(self, monkeypatch):
        r = await self._serve(monkeypatch, "call_recording")
        assert getattr(r, "status_code", None) == 404

    async def test_controle_anexo_de_webchat_sai(self, monkeypatch):
        r = await self._serve(monkeypatch, "webchat_attachment")
        assert getattr(r, "status_code", 200) == 200 and r.media_type == "audio/ogg"


class TestProvider:
    def test_sem_credencial_nao_ha_egress_placebo(self):
        with pytest.raises(WebRTCProviderUnavailable):
            LiveKitProvider(url="ws://livekit", api_key="", api_secret="")

    async def test_mock_escreve_o_arquivo_pedido(self, tmp_path):
        p = MockWebRTCProvider()
        eid = await p.start_egress("r", str(tmp_path / "a" / "x.ogg"))
        res = await p.wait_egress(eid, 1)
        assert res.complete and Path(res.filename).read_bytes()[:4] == b"OggS"


class TestMainEntregaOStore:
    """O `main` constrói o adapter WebRTC COM o AttachmentStore. Medido ao vivo (probe_voz06): sem
    ele a gravação terminava em `recording.failed` "sem AttachmentStore". Censo por AST, não grep."""

    def test_webrtc_adapter_recebe_attachment_store(self):
        import ast
        import inspect
        from plughub_channel_gateway import main
        arvore = ast.parse(inspect.getsource(main))
        chamadas = [n for n in ast.walk(arvore) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "WebRTCAdapter"]
        assert chamadas, "o main nao constroi o WebRTCAdapter — o censo nao mediu nada"
        for c in chamadas:
            assert "attachment_store" in {k.arg for k in c.keywords}, "WebRTCAdapter sem attachment_store"

