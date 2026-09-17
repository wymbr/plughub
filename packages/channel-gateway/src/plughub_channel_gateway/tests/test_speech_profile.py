"""
tests/test_speech_profile.py — perfil de fala por ponto de entrada (VOZ-25).

O endpoint WebRTC aponta um perfil (`settings.speech_profile_id`); o perfil, no namespace
`speech_profiles` do config-api, sobrepõe a segmentação do tenant e escolhe modelo, língua e voz dentro
do mesmo serviço de fala. Ordem: menu → perfil → tenant → global → default (modelo/língua/voz: env).

As proposições que importam: o perfil VALE (chega ao pedido do serviço, não só ao log); o que não pode
valer é DITO (perfil ausente, campo inválido, chave desconhecida, config-api fora); pool direto e
endpoint sem perfil ficam exatamente como antes (controle); STT e TTS usam a MESMA resolução.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from .. import endpoint_resolver, speech_config
from ..adapters.speaches_provider import SpeachesSTTProvider, SpeachesTTSProvider
from ..adapters.voice_provider import SpeechSegmentation, STTResult
from ..adapters.webrtc_room_client import MockRoomClient
from ..speech_config import SpeechProfiles, apply_profile, resolve_session
from .test_speaches_provider import SR, Servico, _gera, _quadros, _silencio, _voz
from .test_speech_metrics import _metricas
from .test_webrtc_collect import _ate
from .test_webrtc_stt_tts import SESSION_ID, _abre, _make_adapter

TENANT = "tenant_t"
ENV = {"stt_model": "env/stt", "stt_language": "pt-BR", "tts_model": "env/tts", "tts_voice": "faber"}
SEG_TENANT = SpeechSegmentation(end_silence_ms=900, gap_ms=800,
                                provenance={"end_silence_ms": "tenant", "gap_ms": "global"})


class TestApplyProfile:
    def test_sem_perfil_fica_tudo_como_antes(self):
        v = apply_profile(SEG_TENANT, ENV, None, None, TENANT)
        assert v.segmentation == SEG_TENANT and v.profile_id is None
        assert (v.stt_model, v.stt_language, v.tts_model, v.tts_voice) == ("env/stt", "pt-BR", "env/tts", "faber")
        assert set(v.provenance.values()) == {"env"}

    def test_perfil_sobrepoe_so_o_que_declara_com_a_procedencia(self):
        v = apply_profile(SEG_TENANT, ENV, "sip-g711",
                          {"stt_end_silence_ms": 1500, "stt_language": "en", "tts_voice": "amy"}, TENANT)
        assert v.profile_id == "sip-g711"
        assert v.segmentation.end_silence_ms == 1500 and v.segmentation.provenance["end_silence_ms"] == "profile:sip-g711"
        assert v.segmentation.gap_ms == 800 and v.segmentation.provenance["gap_ms"] == "global"    # controle
        assert (v.stt_language, v.tts_voice) == ("en", "amy")
        assert v.provenance["stt_language"] == "profile:sip-g711" and v.provenance["stt_model"] == "env"
        assert "end_silence_ms=1500 (profile:sip-g711)" in v.segmentation.describe()
        assert "stt_language=en (profile:sip-g711)" in v.describe_voice()

    @pytest.mark.parametrize("chave,valor", [
        ("stt_end_silence_ms", 99999), ("stt_vad_filter", "sim"), ("stt_language", "portugues"),
        ("stt_model", "tem espaco"), ("tts_voice", 7),
    ])
    def test_campo_invalido_nao_vale_e_o_log_nomeia(self, chave, valor, caplog):
        with caplog.at_level(logging.ERROR):
            v = apply_profile(SEG_TENANT, ENV, "p1", {chave: valor, "stt_gap_ms": 1100}, TENANT)
        assert f"perfil 'p1': {chave}={valor!r} invalido" in caplog.text
        assert v.segmentation.gap_ms == 1100                                   # controle: o resto vale
        assert "profile:p1" not in (v.segmentation.provenance.get("end_silence_ms"), v.provenance.get(chave))

    def test_chave_desconhecida_e_dita(self, caplog):
        with caplog.at_level(logging.WARNING):
            apply_profile(SEG_TENANT, ENV, "p1", {"stt_endsilence": 900, "description": "ok"}, TENANT)
        assert "chave(s) desconhecida(s) stt_endsilence" in caplog.text and "description" not in caplog.text

    def test_perfil_ausente_nao_vale_e_e_dito(self, caplog):
        with caplog.at_level(logging.WARNING):
            v = apply_profile(SEG_TENANT, ENV, "fantasma", speech_config._AUSENTE, TENANT)
        assert v.profile_id is None and v.segmentation == SEG_TENANT
        assert "perfil de fala 'fantasma' referenciado pelo endpoint nao existe" in caplog.text

    def test_perfil_que_nao_e_objeto_e_ignorado_com_erro(self, caplog):
        with caplog.at_level(logging.ERROR):
            v = apply_profile(SEG_TENANT, ENV, "p1", "texto", TENANT)
        assert v.profile_id is None and "nao e um objeto" in caplog.text


class _Servico:
    def __init__(self, perfis=None):
        self.perfis, self.cai, self.leituras = dict(perfis or {}), False, 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.cai:
            raise httpx.ConnectError("recusado")
        assert req.url.path == "/config/speech_profiles"
        self.leituras += 1
        return httpx.Response(200, json={"entries": self.perfis})


@pytest.fixture
def servico(monkeypatch):
    svc = _Servico({"p1": {"stt_end_silence_ms": 1500}})
    real = httpx.AsyncClient
    monkeypatch.setattr(speech_config.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(svc)))
    return svc


async def _seg(tenant):
    return SEG_TENANT


class TestResolveSession:
    async def test_perfil_do_config_api_vale(self, servico):
        v = await resolve_session(_seg, SpeechProfiles("http://c:3600"), TENANT, "p1", ENV)
        assert v.profile_id == "p1" and v.segmentation.end_silence_ms == 1500

    async def test_sem_perfil_nao_consulta_o_namespace(self, servico):
        v = await resolve_session(_seg, SpeechProfiles("http://c:3600"), TENANT, None, ENV)
        assert v.profile_id is None and servico.leituras == 0

    async def test_id_invalido_e_ignorado_com_erro(self, servico, caplog):
        with caplog.at_level(logging.ERROR):
            v = await resolve_session(_seg, SpeechProfiles("http://c:3600"), TENANT, "Perfil Com Espaco", ENV)
        assert v.profile_id is None and servico.leituras == 0 and "nao e um id de perfil valido" in caplog.text

    async def test_config_api_fora_sem_leitura_anterior_diz_que_o_perfil_nao_vale(self, servico, caplog):
        servico.cai = True
        with caplog.at_level(logging.WARNING):
            v = await resolve_session(_seg, SpeechProfiles("http://c:3600"), TENANT, "p1", ENV)
        assert v.profile_id is None and v.segmentation == SEG_TENANT
        assert "perfil de fala NAO vale nas chamadas" in caplog.text and "'p1' da chamada NAO vale" in caplog.text


class TestSpeechProfilesCache:
    async def test_leitura_fica_ate_invalidar_e_a_invalidacao_e_dita(self, servico, caplog):
        pf = SpeechProfiles("http://c:3600")
        await pf(TENANT); await pf(TENANT)
        assert servico.leituras == 1
        servico.perfis["p2"] = {}
        with caplog.at_level(logging.INFO):
            pf.invalidate(TENANT)
        assert f"perfis de fala invalidados (tenant={TENANT}, 1 entrada(s) em cache)" in caplog.text
        assert "p2" in await pf(TENANT) and servico.leituras == 2

    async def test_falha_depois_de_leitura_boa_mantem_os_perfis(self, servico):
        pf = SpeechProfiles("http://c:3600")
        await pf(TENANT)
        servico.cai = True
        pf.invalidate(TENANT)
        assert "p1" in await pf(TENANT)


class TestProvedorEscolheModelo:
    async def test_stt_manda_o_modelo_da_chamada_e_o_default_sem_ele(self):
        svc = Servico(textos=["a", "b"])
        stt = SpeachesSTTProvider("http://speaches:8000", "Systran/faster-whisper-small",
                                  http=httpx.AsyncClient(transport=httpx.MockTransport(svc)))
        pcm = _voz(600) + _silencio(900)
        [r async for r in stt.stream(_gera(_quadros(pcm)), sample_rate=SR, language="en", model="Systran/faster-whisper-medium")]
        [r async for r in stt.stream(_gera(_quadros(pcm)), sample_rate=SR, language="pt-BR")]
        assert b"\r\n\r\nSystran/faster-whisper-medium\r\n" in svc.pedidos[0].content
        assert b"\r\n\r\nen\r\n" in svc.pedidos[0].content
        assert b"\r\n\r\nSystran/faster-whisper-small\r\n" in svc.pedidos[1].content      # controle

    async def test_tts_manda_modelo_e_voz_da_chamada(self):
        svc = Servico()
        tts = SpeachesTTSProvider("http://speaches:8000", "piper-pt", "faber",
                                  http=httpx.AsyncClient(transport=httpx.MockTransport(svc)))
        await tts.synthesize("oi", "amy", model="piper-en")
        await tts.synthesize("oi")
        a, b = (json.loads(p.content) for p in svc.pedidos)
        assert (a["model"], a["voice"]) == ("piper-en", "amy") and (b["model"], b["voice"]) == ("piper-pt", "faber")


class TestEndpointAponta:
    async def test_resolver_devolve_o_settings_da_linha(self, monkeypatch):
        def reg(req):
            return httpx.Response(200, json={"endpoints": [{"pool_id": "p", "origin": "external",
                                                            "settings": {"speech_profile_id": "p1"}}]})
        real = httpx.AsyncClient
        monkeypatch.setattr(endpoint_resolver.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reg)))
        endpoint_resolver._cache.clear()
        ep = await endpoint_resolver.resolve_endpoint(channel="webrtc", identifier="sala", tenant_id=TENANT,
                                                      agent_registry_url="http://r:3300")
        assert ep.pool_id == "p" and ep.settings == {"speech_profile_id": "p1"}
        endpoint_resolver._cache.clear()

    @pytest.mark.parametrize("settings,esperado", [
        ({"speech_profile_id": "p1"}, "p1"), ({}, None), ({"speech_profile_id": 5}, None),
    ])
    async def test_adapter_leva_o_perfil_do_endpoint(self, monkeypatch, settings, esperado):
        adapter, _, _ = _make_adapter()
        adapter._settings.agent_registry_url = "http://r:3300"

        async def _ep(**kw):
            return endpoint_resolver.ResolvedEndpoint("pool-x", "external", False, None, "found", settings)
        monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", _ep)
        assert await adapter._resolve_pool("sala", "c") == ("pool-x", esperado)

    async def test_pool_direto_nao_tem_perfil(self, monkeypatch):
        adapter, _, _ = _make_adapter()
        adapter._settings.agent_registry_url = "http://r:3300"

        async def _ep(**kw):
            return endpoint_resolver.ResolvedEndpoint(None, None, False, None, "not_found")
        monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", _ep)
        assert await adapter._resolve_pool("pool-x", "c") == ("pool-x", None)


class _SttGrava:
    supports_tuning = True
    supports_model_choice = True
    measures_confidence = True

    def __init__(self):
        self.chamadas = []

    async def stream(self, chunks, language=None, stats=None, **kw):
        self.chamadas.append({"language": language, **kw})
        async for _ in chunks:
            if stats is not None:
                stats.frame(20, 100.0, False)
        yield STTResult(transcript="x", is_final=True, confidence=0.8)


class _TtsGrava:
    supports_model_choice = True
    output_sample_rate = 24_000

    def __init__(self):
        self.pedidos = []

    async def synthesize(self, text, voice_id=None, model=None):
        self.pedidos.append((voice_id, model))
        return b"\x00\x00" * 10


class _Perfis:
    def __init__(self, perfis):
        self.perfis, self.leituras = perfis, 0

    async def __call__(self, tenant):
        self.leituras += 1
        return self.perfis


def _adapter_com_perfil(perfil_id, perfis):
    room = MockRoomClient()
    room.inject_audio(b"\x00" * 960)
    room.end_audio()
    stt, tts = _SttGrava(), _TtsGrava()
    adapter, _, producer = _make_adapter(stt=stt, room_client=room)
    adapter._tts = tts
    _abre(adapter)
    adapter._sessions[SESSION_ID]["speech_profile_id"] = perfil_id
    adapter.speech_config = _seg
    adapter.speech_profiles = _Perfis(perfis)
    return adapter, room, stt, tts, producer


class TestChamadaUsaOPerfil:
    PERFIS = {"sip": {"stt_model": "m-sip", "stt_language": "en", "stt_end_silence_ms": 1500,
                      "tts_model": "piper-en", "tts_voice": "amy"}}

    async def test_stt_tts_e_telemetria_seguem_o_perfil_com_uma_resolucao(self, caplog):
        adapter, room, stt, tts, producer = _adapter_com_perfil("sip", self.PERFIS)
        with caplog.at_level(logging.INFO):
            await adapter._stt_pipeline(SESSION_ID, room)
            await adapter._synthesize_pcm(SESSION_ID, "ola")
        (c,) = stt.chamadas
        assert (c["model"], c["language"], c["segmentation"].end_silence_ms) == ("m-sip", "en", 1500)
        assert tts.pedidos == [("amy", "piper-en")]
        assert adapter.speech_profiles.leituras == 1                     # STT e TTS: a mesma resolução
        assert "voz da chamada session=%s perfil=sip" % SESSION_ID in caplog.text
        assert await _ate(lambda: _metricas(producer))
        (e,) = _metricas(producer)
        assert (e["speech_profile_id"], e["stt_model"]) == ("sip", "m-sip")
        assert e["segmentation_scope"]["end_silence_ms"] == "profile"

    async def test_controle_sem_perfil_segue_o_tenant_e_o_env(self):
        adapter, room, stt, tts, producer = _adapter_com_perfil(None, self.PERFIS)
        await adapter._stt_pipeline(SESSION_ID, room)
        await adapter._synthesize_pcm(SESSION_ID, "ola")
        (c,) = stt.chamadas
        s = adapter._settings
        assert (c["model"], c["language"], c["segmentation"]) == (s.webrtc_stt_model, s.voice_stt_language, SEG_TENANT)
        assert tts.pedidos == [(s.webrtc_tts_voice, s.webrtc_tts_model)]
        assert adapter.speech_profiles.leituras == 0
        assert await _ate(lambda: _metricas(producer))
        (e,) = _metricas(producer)
        assert e["speech_profile_id"] is None and e["stt_model"] == s.webrtc_stt_model

    async def test_fechar_a_sessao_esquece_a_resolucao(self):
        adapter, room, *_ = _adapter_com_perfil("sip", self.PERFIS)
        await adapter._speech_settings(SESSION_ID)
        assert SESSION_ID in adapter._speech_resolved
        await adapter._close_session(SESSION_ID, "agent_done")
        assert SESSION_ID not in adapter._speech_resolved
