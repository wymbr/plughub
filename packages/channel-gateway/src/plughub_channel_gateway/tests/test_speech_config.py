"""
tests/test_speech_config.py — segmentação da fala por tenant, do config-api (VOZ-21).

O config-api não valida valor: o resolvedor valida, cai no default dizendo qual chave e por quê, e
guarda a PROCEDÊNCIA de cada valor. O serviço é trocado por um `httpx.MockTransport` que conta as
leituras — cache e invalidação se provam pela contagem.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from .. import speech_config
from ..adapters.voice_provider import SpeechSegmentation
from ..speech_config import PARAMS, SpeechSegmentationConfig, resolve

TENANT = "tenant_t"
CHEIO = {"stt_energy_threshold": 900, "stt_end_silence_ms": 1200, "stt_gap_ms": 800,
         "stt_min_speech_ms": 300, "stt_max_speech_ms": 9000, "stt_vad_filter": False}


class TestResolve:
    def test_todas_as_chaves_valem_com_a_procedencia(self):
        seg = resolve(CHEIO, {"stt_end_silence_ms": "tenant"}, TENANT)
        assert (seg.energy_threshold, seg.end_silence_ms, seg.gap_ms, seg.min_speech_ms,
                seg.max_speech_ms, seg.vad_filter) == (900.0, 1200, 800, 300, 9000, False)
        assert seg.provenance["end_silence_ms"] == "tenant"
        assert seg.provenance["gap_ms"] == "config"          # sem escopo conhecido, rotulado

    def test_valor_fora_da_faixa_cai_no_default_e_o_log_nomeia(self, caplog):
        with caplog.at_level(logging.ERROR):
            seg = resolve({**CHEIO, "stt_end_silence_ms": 99999}, {}, TENANT)
        assert seg.end_silence_ms == SpeechSegmentation().end_silence_ms
        assert seg.provenance["end_silence_ms"] == "default: valor invalido"
        assert "stt_end_silence_ms=99999" in caplog.text and "entre 100 e 5000" in caplog.text
        assert seg.energy_threshold == 900.0                 # controle: o resto vale

    @pytest.mark.parametrize("chave,valor", [
        ("stt_vad_filter", "true"), ("stt_vad_filter", 1), ("stt_gap_ms", True),
        ("stt_gap_ms", "700"), ("stt_energy_threshold", float("nan")),
    ])
    def test_tipo_errado_nao_e_aceito(self, chave, valor):
        seg = resolve({**CHEIO, chave: valor}, {}, TENANT)
        campo = next(p.field for p in PARAMS if p.key == chave)
        assert seg.provenance[campo] == "default: valor invalido"

    def test_chave_ausente_cai_no_default_e_o_log_nomeia(self, caplog):
        parcial = {k: v for k, v in CHEIO.items() if k != "stt_min_speech_ms"}
        with caplog.at_level(logging.WARNING):
            seg = resolve(parcial, {}, TENANT)
        assert seg.min_speech_ms == 250 and seg.provenance["min_speech_ms"].startswith("default")
        assert "stt_min_speech_ms nao configurado" in caplog.text

    def test_descricao_leva_valor_e_procedencia_de_cada_campo(self):
        texto = resolve(CHEIO, {"stt_vad_filter": "tenant"}, TENANT).describe()
        assert "vad_filter=False (tenant)" in texto and "max_speech_ms=9000 (config)" in texto


class _Servico:
    def __init__(self, entries=None, status=200, cai=False):
        self.entries = dict(CHEIO if entries is None else entries)
        self.status, self.cai, self.leituras = status, cai, 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.cai:
            raise httpx.ConnectError("recusado")
        if req.url.path.endswith("/_provenance"):
            return httpx.Response(200, json={"keys": {k: {"effective_scope": "global"} for k in self.entries}})
        self.leituras += 1
        assert req.url.params["tenant_id"] in (TENANT, "outro")
        return httpx.Response(self.status, json={"entries": self.entries})


@pytest.fixture
def servico(monkeypatch):
    svc = _Servico()
    real = httpx.AsyncClient
    monkeypatch.setattr(speech_config.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(svc)))
    return svc


class TestCache:
    @pytest.mark.asyncio
    async def test_leitura_boa_fica_ate_invalidar(self, servico):
        cfg = SpeechSegmentationConfig("http://config-api:3600")
        assert (await cfg(TENANT)).end_silence_ms == 1200
        await cfg(TENANT)
        assert servico.leituras == 1
        servico.entries["stt_end_silence_ms"] = 2000
        cfg.invalidate(TENANT)
        seg = await cfg(TENANT)
        assert seg.end_silence_ms == 2000 and servico.leituras == 2
        assert seg.provenance["end_silence_ms"] == "global"

    @pytest.mark.asyncio
    async def test_invalidacao_e_dita_por_quem_invalida(self, servico, caplog):
        cfg = SpeechSegmentationConfig("http://config-api:3600")
        await cfg(TENANT)
        with caplog.at_level(logging.INFO):
            cfg.invalidate(TENANT)
        assert f"segmentacao da fala invalidada (tenant={TENANT}, 1 entrada(s) em cache)" in caplog.text

    @pytest.mark.asyncio
    async def test_mudanca_global_invalida_todos_os_tenants(self, servico):
        cfg = SpeechSegmentationConfig("http://config-api:3600")
        await cfg(TENANT); await cfg("outro")
        cfg.invalidate("__global__")
        await cfg(TENANT); await cfg("outro")
        assert servico.leituras == 4

    @pytest.mark.asyncio
    async def test_config_api_fora_vale_o_default_e_diz_o_que_deixa_de_valer(self, servico, caplog):
        servico.cai = True
        cfg = SpeechSegmentationConfig("http://config-api:3600")
        with caplog.at_level(logging.WARNING):
            seg = await cfg(TENANT)
        assert seg == SpeechSegmentation()
        assert seg.provenance["energy_threshold"] == "default: config-api indisponivel"
        assert "configurada para o tenant NAO vale" in caplog.text

    @pytest.mark.asyncio
    async def test_falha_depois_de_uma_leitura_boa_mantem_o_ultimo_valor(self, servico):
        cfg = SpeechSegmentationConfig("http://config-api:3600")
        await cfg(TENANT)
        servico.cai = True
        cfg.invalidate(TENANT)
        assert (await cfg(TENANT)).end_silence_ms == 1200      # a mudança não chegou; o bom fica
        servico.cai = False
        servico.entries["stt_end_silence_ms"] = 2000
        cfg._retry_s = 0.0
        assert (await cfg(TENANT)).end_silence_ms == 2000      # controle: voltou, relê

    @pytest.mark.asyncio
    async def test_falha_e_retentada_depois_do_intervalo(self, servico):
        servico.status = 500
        cfg = SpeechSegmentationConfig("http://config-api:3600", retry_s=0.0)
        assert (await cfg(TENANT)).end_silence_ms == 700
        servico.status = 200
        assert (await cfg(TENANT)).end_silence_ms == 1200
